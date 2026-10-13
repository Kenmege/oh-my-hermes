"""One repaint: from what the app holds to the rows `LiveRegion.render` draws.

The app loop calls `build_frame` once per repaint with the terminal size, the
parts it holds (sticky prompt, streaming tail, slot, composer, status line)
and the region's current height, then hands the result to
`LiveRegion.render(frame.lines, frame.cursor, cols, pad=frame.pad)`.

The composer stays put. The first frame at a terminal size
(previous height 0: nothing drawn yet, or the region was just cleared for a
resize) is padded to the full budget, `rows - 1`, so the region's bottom
lands on the screen's last row. Every later frame passes the region's
current height, so content that shrinks becomes pad rows instead of a
shorter region, content that grows scrolls the screen up from that same
bottom, and `flush_above` turns pad rows into transcript without moving it.
The composer's first row is then the bottom row minus the status row minus
its own row count, whatever the panel, prompt slot, completion menu or tail do.

Boundary: when the region starts on the screen's top row (the launch cursor
on row 0), the first frame's bottom is one row above the screen's last row.
The first time the region grows after that it reaches the last row, so the
composer moves down once, by one row, and never moves up.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .composer import PROMPT, Composer
from .layout import Budget, allocate, caret_row, compose, composer_cap, max_height
from .style import Line, Span
from .theme import DEFAULT_THEME, Theme
from .width import line_width, truncate_line

RULE_GLYPH = "─"
STICKY_PREFIX = "↳ you: "
STATUS_GLYPH = "⚚"
STATUS_SEPARATOR = " │ "


@dataclass(frozen=True)
class Frame:
    lines: tuple[Line, ...]
    cursor: tuple[int, int]  # (row, col) of the composer caret within the region
    height: int
    pad: int
    budget: Budget


def build_frame(
    *,
    cols: int,
    rows: int,
    composer: Composer,
    status: Line,
    previous_height: int,
    sticky: Line | None = None,
    tail: Sequence[Line] = (),
    slot: Sequence[Line] = (),
    theme: Theme = DEFAULT_THEME,
    prompt: str = PROMPT,
) -> Frame:
    """The region for one repaint; `previous_height` is `LiveRegion.height` right now."""
    composer_lines, (caret_line, caret_col) = composer.render(cols, composer_cap(rows), prompt, theme.prompt)
    min_height = previous_height if previous_height > 0 else max_height(rows)
    budget = allocate(
        rows,
        cols,
        sticky=sticky is not None,
        tail_rows=len(tail),
        slot_rows=len(slot),
        composer_rows=len(composer_lines),
        min_height=min_height,
    )
    lines = compose(
        budget,
        sticky=sticky,
        tail=tail,
        slot=slot,
        rule=rule_line(cols, theme),
        composer=composer_lines,
        status=status,
    )
    return Frame(
        lines=tuple(lines),
        cursor=(caret_row(budget, caret_line), caret_col),
        height=budget.total,
        pad=budget.pad,
        budget=budget,
    )


def rule_line(width: int, theme: Theme = DEFAULT_THEME) -> Line:
    """The rule above the composer, full width, in the theme's input-rule colour."""
    return (Span(RULE_GLYPH * max(width, 0), theme.rule),)


def sticky_line(prompt_text: str, width: int, theme: Theme = DEFAULT_THEME) -> Line:
    """`↳ you: <first line of the prompt>` cut to `width`, shown while a turn runs."""
    first = prompt_text.strip().split("\n", 1)[0]
    line = (Span(STICKY_PREFIX, theme.dim), Span(first, theme.user))
    return truncate_line(line, width)


def status_line(
    *,
    version: str,
    model: str,
    context: str,
    state: str,
    width: int,
    theme: Theme = DEFAULT_THEME,
) -> Line:
    """`⚚ OMH vX │ model │ ctx │ state` cut to `width` and filled to it with the bar colour.

    Empty fields are left out, separators included.
    """
    spans = [Span(f"{STATUS_GLYPH} OMH v{version}", theme.status)]
    for field in (model, context, state):
        if field:
            spans.append(Span(STATUS_SEPARATOR, theme.status_dim))
            spans.append(Span(field, theme.status))
    line = truncate_line(tuple(spans), width)
    filler = max(width - line_width(line), 0)
    return line + ((Span(" " * filler, theme.status),) if filler else ())
