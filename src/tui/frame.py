"""One repaint: from what the app holds to the rows `LiveRegion.render` draws.

The app loop calls `build_frame` once per repaint with the terminal size, the
parts it holds (sticky prompt, streaming tail, slot, composer, info line)
and the input mode, then hands the result to
`LiveRegion.render(frame.lines, frame.cursor, cols, flush=lines)`.

The composer is a rounded box: a top edge `╭─…─╮`, its input rows each drawn
as `│ ` + row + ` │`, and a bottom edge `╰─…─╯`, all in the rule colour
whatever the input mode, so the composer rows are `BOX_INSET` cells narrower
than the terminal on each side. The box takes the rows the layout gives the
composer frame (`layout.allocate`: the top edge where the top rule was, the
bottom edge where the bottom rule was), so a 6-row terminal draws it open at
the bottom, and below `layout.MIN_ROWS` rows or `layout.MIN_COLS` columns the
input rows are drawn full width with no box at all. While the composer holds
a `!` line for Hermes' shell the prompt mark is drawn in the accent instead
of the prompt colour; the box stays as it is. The info line under the bottom
edge is a line the app builds; the frame only places it. The agent view the
app builds (`agents`) is drawn under the info line, the region's last rows.

The composer follows the content until the screen is full, then stays on the
bottom rows. With `min_height` 0 a frame is exactly as tall as what it holds
(`layout.allocate`), and `LiveRegion` draws it right under the output, so the
composer sits under the last row printed, the panel, prompt slot, completion
menu, tail and sticky prompt above it, and the rows under it (the bottom
edge, by the terminal size alone, `layout.under_composer`, the info line and
the agent view) under it. When something above the composer grows,
the composer moves down with it until the region's last row is the screen's
last row, and from then on the screen scrolls instead; when something above
it shrinks, the composer moves up by as many rows. Once the region's last row
is the screen's last row, the app passes the region's height as
`min_height`, so what shrinks there becomes pad rows at the top of the frame
and the composer does not move up: when the agent view under it shrinks, it
moves down by as many rows.

An empty composer shows `placeholder` after the prompt glyph in the hint
colour while the app asks for it (no slot open). It is drawn, never typed:
the caret stays right after the glyph and the composer's text stays empty.

While the person picks from a list (`composer` is None) the frame draws no
composer at all: no box, no input rows, and no caret (`Frame.cursor` is None,
and `LiveRegion` hides the cursor) unless `slot_caret` puts one in the slot (a
picker's filter row or the history search's query row, where typing goes). The slot stands in the
composer's place, right above the info line, with the rows `layout.choice_cap`
gives it. The rows under the composer are the same either way.

The attachment row (the images queued for the next message) is the composer's
first row inside the box while there is one: it counts against the composer's cap.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from .composer import PROMPT, Composer
from .layout import BLANK, Budget, allocate, caret_row, compose, composer_cap, is_tiny
from .style import Line, Span
from .theme import DEFAULT_THEME, Theme
from .width import line_width, truncate_line

# The composer's box: its corners, its edges and its sides.
BOX_EDGE = "─"
BOX_TOP = ("╭", "╮")
BOX_BOTTOM = ("╰", "╯")
BOX_SIDE = "│"
BOX_INSET = 2  # cells the box takes on each side of a composer row: the side and a space
STICKY_PREFIX = "↳ you: "
STICKY_CUT = "…"  # where the reminder's prompt is cut

MODE_NORMAL = "normal"
MODE_ACCEPT = "accept"
MODE_PLAN = "plan"
MODE_ACCEPT_PLAN = "accept+plan"  # approvals off and the plan flag on, both at once
MODE_SHELL = "shell"  # the composer holds a `!` line: it goes to Hermes' shell.exec, not to the model


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
    info: Line,
    sticky: Line | None = None,
    tail: Sequence[Line] = (),
    slot: Sequence[Line] = (),
    theme: Theme = DEFAULT_THEME,
    prompt: str = PROMPT,
    attachments: Line | None = None,
    mode: str = MODE_NORMAL,
    placeholder: str = "",
    slot_caret: tuple[int, int] | None = None,
    text_question: bool = False,
    min_height: int = 0,
    below: Sequence[Line] = (),
    agents: Sequence[Line] = (),
    composer_blank: bool = False,
) -> Frame:
    """The region for one repaint; `min_height` rows at least, pad rows at the top making up the rest.

    `attachments` is drawn as the composer's first row when the composer may
    have two rows; a terminal too short for that shows the input alone.
    `mode` MODE_SHELL draws the prompt mark in the accent; `info` is the
    region's last row. `composer` None draws no
    composer (nor the attachment row): the slot takes its place, and the caret
    is `slot_caret`, a (row, col) within `slot`, or none where that is None or
    its row is cut.
    `text_question` says the slot holds a question answered with text, whose
    label keeps a row on a tiny terminal (`layout.allocate`). `below` is drawn
    under the bottom edge, above the info line: the completion menu where the
    app puts it under the composer. `agents` is the agent view, drawn under
    the info line as the region's last rows, sized by its producer to
    `layout.agent_view_cap`. `composer_blank` draws the composer's rows, its
    box's included, as blank rows of the same count, with no caret: the agent
    list under them has the keys, and nothing moves as it opens or closes.
    """
    cap = composer_cap(rows)
    boxed = not is_tiny(rows, cols)
    inner = cols - 2 * BOX_INSET if boxed else cols
    composer_lines: list[Line] = []
    caret_line = caret_col = 0
    if composer is not None:
        chips = [truncate_line(attachments, inner)] if attachments is not None and cap >= 2 else []
        mark = replace(theme.accent, bold=True) if mode == MODE_SHELL else theme.prompt
        input_lines, (caret_line, caret_col) = composer.render(inner, cap - len(chips), prompt, mark, theme.accent)
        if placeholder and not composer.text and input_lines:
            input_lines[0] = truncate_line((*input_lines[0], Span(placeholder, theme.hint)), inner)
        composer_lines = chips + input_lines
        if boxed:
            composer_lines = [box_row(line, inner, theme) for line in composer_lines]
            caret_col += BOX_INSET
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
        below_rows=len(below),
        agents_rows=len(agents),
    )
    lines = compose(
        budget,
        sticky=sticky,
        tail=tail,
        slot=slot,
        rule=BLANK if composer_blank else box_edge(cols, theme, BOX_TOP),
        composer=[BLANK] * len(composer_lines) if composer_blank else composer_lines,
        bottom_rule=BLANK if composer_blank else box_edge(cols, theme, BOX_BOTTOM),
        info=truncate_line(info, cols),
        below=below,
        agents=agents,
    )
    if composer is not None and not composer_blank:
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


def box_edge(width: int, theme: Theme = DEFAULT_THEME, corners: tuple[str, str] = BOX_TOP) -> Line:
    """The box's top (`BOX_TOP`) or bottom (`BOX_BOTTOM`) edge, full width, in the rule colour."""
    if width < 2:
        return (Span(BOX_EDGE * max(width, 0), theme.rule),)
    return (Span(corners[0] + BOX_EDGE * (width - 2) + corners[1], theme.rule),)


def box_row(line: Line, inner: int, theme: Theme = DEFAULT_THEME) -> Line:
    """One composer row inside the box: the side, a space, `line` padded to `inner` cells, a space, the side."""
    shown = truncate_line(line, inner)
    pad = (Span(" " * (inner - line_width(shown))),) if line_width(shown) < inner else ()
    return (Span(BOX_SIDE + " ", theme.rule), *shown, *pad, Span(" " + BOX_SIDE, theme.rule))


def sticky_line(prompt_text: str, width: int, theme: Theme = DEFAULT_THEME) -> Line:
    """`↳ you: <first line of the prompt>` cut to `width`, shown while a turn runs.

    It has no band, as the prompt's echo in the transcript has none: `↳ you:`
    in the accent, which no transcript row starts with, the prompt in the
    muted text colour, and a dim `…` where it is cut. So it reads apart from
    the transcript rows right above it by its words and colours.
    """
    if width <= 0:
        return ()
    first = prompt_text.strip().split("\n", 1)[0]
    line = (Span(STICKY_PREFIX, theme.accent), Span(first, theme.muted))
    if line_width(line) <= width:
        return line
    return (*truncate_line(line, width - len(STICKY_CUT), ellipsis=""), Span(STICKY_CUT, theme.dim))
