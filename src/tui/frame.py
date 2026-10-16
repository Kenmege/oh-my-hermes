"""One repaint: from what the app holds to the rows `LiveRegion.render` draws.

The app loop calls `build_frame` once per repaint with the terminal size, the
parts it holds (sticky prompt, streaming tail, slot, composer, mode row,
status line) and the input mode, then hands the result to
`LiveRegion.render(frame.lines, frame.cursor, cols, flush=lines)`.

The composer is framed: a rule above it and a rule below it, both drawn in
the input mode's glyph and colour (normal `─` in the rule colour, accept `━`
in `mode_accept`, plan `┄` in `mode_plan`, a `!` shell line `╌` in the
accent), so the mode reads from the frame
even where hue alone would not tell it apart. Accept with plan on beside it
(Hermes has approvals off on its own, and the plan flag is on) draws accept's
rules: approvals off is what the frame must never hide, and the mode row
names both. The mode row under the bottom rule is a line the app builds; the
frame only places it.

The composer follows the content until the screen is full, then stays on the
bottom rows. With `min_height` 0 a frame is exactly as tall as what it holds
(`layout.allocate`), and `LiveRegion` draws it right under the output, so the
composer sits under the last row printed, the panel, prompt slot, completion
menu, tail and sticky prompt above it, and the rows under it (bottom rule,
mode row, status line, a function of the terminal size alone,
`layout.under_composer`) under it. When something above the composer grows,
the composer moves down with it until the region's last row is the screen's
last row, and from then on the screen scrolls instead; when something above
it shrinks, the composer moves up by as many rows. Once the region's last row
is the screen's last row, the app passes the region's height as
`min_height`, so what shrinks there becomes pad rows at the top of the frame
and the composer does not move up.

An empty composer shows `placeholder` after the prompt glyph in the hint
colour while the app asks for it (no slot open). It is drawn, never typed:
the caret stays right after the glyph and the composer's text stays empty.

While the person picks from a list (`composer` is None) the frame draws no
composer at all: no rules, no input rows, and no caret (`Frame.cursor` is None,
and `LiveRegion` hides the cursor) unless `slot_caret` puts one in the slot (a
picker's filter row or the history search's query row, where typing goes). The slot stands in the
composer's place, right above the mode row, with the rows `layout.choice_cap`
gives it. The rows under the composer are the same either way.

The attachment row (the images queued for the next message) is the composer's
first row while there is one: it counts against the composer's cap.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .composer import PROMPT, Composer
from .layout import Budget, allocate, caret_row, compose, composer_cap
from .style import Line, Span
from .theme import DEFAULT_THEME, Theme
from .width import truncate_line

RULE_GLYPH = "─"
STICKY_PREFIX = "↳ you: "
STATUS_SEPARATOR = " │ "

MODE_NORMAL = "normal"
MODE_ACCEPT = "accept"
MODE_PLAN = "plan"
MODE_ACCEPT_PLAN = "accept+plan"  # approvals off and the plan flag on, both at once
MODE_SHELL = "shell"  # the composer holds a `!` line: it goes to Hermes' shell.exec, not to the model
MODE_RULE_GLYPHS = {MODE_NORMAL: RULE_GLYPH, MODE_ACCEPT: "━", MODE_PLAN: "┄", MODE_ACCEPT_PLAN: "━", MODE_SHELL: "╌"}


@dataclass(frozen=True)
class Frame:
    lines: tuple[Line, ...]
    cursor: tuple[int, int] | None  # (row, col) of the composer caret within the region; None with no composer
    height: int
    budget: Budget


def build_frame(
    *,
    cols: int,
    rows: int,
    composer: Composer | None,
    status: Line,
    sticky: Line | None = None,
    tail: Sequence[Line] = (),
    slot: Sequence[Line] = (),
    theme: Theme = DEFAULT_THEME,
    prompt: str = PROMPT,
    attachments: Line | None = None,
    mode: str = MODE_NORMAL,
    mode_row: Line = (),
    placeholder: str = "",
    slot_caret: tuple[int, int] | None = None,
    text_question: bool = False,
    min_height: int = 0,
) -> Frame:
    """The region for one repaint; `min_height` rows at least, pad rows at the top making up the rest.

    `attachments` is drawn as the composer's first row when the composer may
    have two rows; a terminal too short for that shows the input alone.
    `mode` picks both rules' glyph and colour; `mode_row` is drawn under the
    bottom rule where the terminal has room for it. `composer` None draws no
    composer (nor the attachment row): the slot takes its place, and the caret
    is `slot_caret`, a (row, col) within `slot`, or none where that is None or
    its row is cut.
    `text_question` says the slot holds a question answered with text, whose
    label keeps a row on a tiny terminal (`layout.allocate`).
    """
    cap = composer_cap(rows)
    composer_lines: list[Line] = []
    caret_line = caret_col = 0
    if composer is not None:
        chips = [attachments] if attachments is not None and cap >= 2 else []
        input_lines, (caret_line, caret_col) = composer.render(cols, cap - len(chips), prompt, theme.prompt, theme.accent)
        if placeholder and not composer.text and input_lines:
            input_lines[0] = truncate_line((*input_lines[0], Span(placeholder, theme.hint)), cols)
        composer_lines = chips + input_lines
        caret_line += len(chips)
    budget = allocate(
        rows,
        cols,
        sticky=sticky is not None,
        tail_rows=len(tail),
        slot_rows=len(slot),
        composer_rows=len(composer_lines),
        min_height=min_height,
        composer_hidden=composer is None,
        text_question=text_question,
    )
    rule = rule_line(cols, theme, mode)
    lines = compose(
        budget,
        sticky=sticky,
        tail=tail,
        slot=slot,
        rule=rule,
        composer=composer_lines,
        bottom_rule=rule,
        mode=truncate_line(mode_row, cols),
        status=status,
    )
    if composer is not None:
        cursor: tuple[int, int] | None = (caret_row(budget, caret_line), caret_col)
    elif slot_caret is not None and 0 <= slot_caret[0] < budget.slot:
        cursor = (budget.pad + budget.sticky + budget.tail + slot_caret[0], slot_caret[1])
    else:
        cursor = None
    return Frame(
        lines=tuple(lines),
        cursor=cursor,
        height=budget.total,
        budget=budget,
    )


def rule_line(width: int, theme: Theme = DEFAULT_THEME, mode: str = MODE_NORMAL) -> Line:
    """One edge of the composer frame, full width, in the mode's glyph and colour (normal for an unknown mode)."""
    style = {
        MODE_ACCEPT: theme.mode_accept, MODE_ACCEPT_PLAN: theme.mode_accept, MODE_PLAN: theme.mode_plan,
        MODE_SHELL: theme.accent,
    }.get(mode, theme.rule)
    return (Span(MODE_RULE_GLYPHS.get(mode, RULE_GLYPH) * max(width, 0), style),)


def sticky_line(prompt_text: str, width: int, theme: Theme = DEFAULT_THEME) -> Line:
    """`↳ you: <first line of the prompt>` cut to `width`, shown while a turn runs."""
    first = prompt_text.strip().split("\n", 1)[0]
    line = (Span(STICKY_PREFIX, theme.muted), Span(first, theme.user))
    return truncate_line(line, width)
