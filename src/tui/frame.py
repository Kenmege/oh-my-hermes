"""One repaint: from what the app holds to the rows `LiveRegion.render` draws.

The app loop calls `build_frame` once per repaint with the terminal size, the
parts it holds (sticky prompt, streaming tail, slot, composer, mode row,
status line), the input mode and the region's current height, then hands the
result to `LiveRegion.render(frame.lines, frame.cursor, cols, pad=frame.pad)`.

The composer is framed: a rule above it and a rule below it, both drawn in
the input mode's glyph and colour (normal `─` in the rule colour, accept `━`
in `mode_accept`, plan `┄` in `mode_plan`, a `!` shell line `╌` in the
accent), so the mode reads from the frame
even where hue alone would not tell it apart. Accept with plan on beside it
(Hermes has approvals off on its own, and the plan flag is on) draws accept's
rules: approvals off is what the frame must never hide, and the mode row
names both. The mode row under the bottom rule is a line the app builds; the
frame only places it.

The composer stays put. The first frame at a terminal size
(previous height 0: nothing drawn yet, or the region was just cleared for a
resize) is padded to the full budget, `rows - 1`, so the region's bottom
lands on the screen's last row. Every later frame passes the region's
current height, so content that shrinks becomes pad rows instead of a
shorter region, content that grows scrolls the screen up from that same
bottom, and `flush_above` turns pad rows into transcript without moving it.
The rows under the composer (bottom rule, mode row, status line) are a
function of the terminal size alone (`layout.under_composer`), so the
composer's first row is the bottom row minus those rows minus its own row
count: `composer_rows + 3` rows up from the region's end on any terminal of
`MODE_ROW_MIN_ROWS` rows or more, whatever the panel, prompt slot, completion
menu, tail or mode do. A shorter terminal drops the mode row, then the bottom
rule, by its height alone, so the offset is still fixed for each size.

One exception, and only one: while the agent list is focused (`agent_list`
is given) it is drawn where the mode row was and is as tall as it needs to
be within `layout.agent_list_room`, so the composer rises by the rows the
list adds. The list never makes the region taller: the rows it adds come out
of the pad first, then out of the tail, the sticky prompt and the slot, and a
list that has fewer rows than agents scrolls. So the region's last row stays
where it is, the boundary below included, and the frame after the focus has
one mode row again and the same height: the composer is back on the row it
left, and no frame without the list ever moves it.

An empty composer shows `placeholder` after the prompt glyph in the hint
colour while the app asks for it (no slot open). It is drawn, never typed:
the caret stays right after the glyph and the composer's text stays empty.

While the person picks from a list (`composer` is None) the frame draws no
composer at all: no rules, no input rows, and no caret (`Frame.cursor` is None,
and `LiveRegion` hides the cursor) unless `slot_caret` puts one in the slot (a
picker's filter row, where its typing goes). The slot stands in the
composer's place, right above the mode row, with the rows `layout.choice_cap`
gives it. The rows under the composer are the same either way, so the
composer comes back on its row.

The attachment row (the images queued for the next message) is the composer's
first row while there is one: it counts against the composer's cap, so the
input rows keep their place and only the block's top moves.

Boundary: when the region starts on the screen's top row (the launch cursor
on row 0), the first frame's bottom is one row above the screen's last row.
The first time the region grows after that it reaches the last row, so the
composer moves down once, by one row, and never moves up. The agent list is
never that growth.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .composer import PROMPT, Composer
from .layout import Budget, agent_list_room, allocate, caret_row, compose, composer_cap, max_height
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
    pad: int
    budget: Budget


def build_frame(
    *,
    cols: int,
    rows: int,
    composer: Composer | None,
    status: Line,
    previous_height: int,
    sticky: Line | None = None,
    tail: Sequence[Line] = (),
    slot: Sequence[Line] = (),
    theme: Theme = DEFAULT_THEME,
    prompt: str = PROMPT,
    attachments: Line | None = None,
    mode: str = MODE_NORMAL,
    mode_row: Line = (),
    placeholder: str = "",
    agent_list: Callable[[int], Sequence[Line]] | None = None,
    slot_caret: tuple[int, int] | None = None,
    text_question: bool = False,
) -> Frame:
    """The region for one repaint; `previous_height` is `LiveRegion.height` right now.

    `attachments` is drawn as the composer's first row when the composer may
    have two rows; a terminal too short for that shows the input alone.
    `mode` picks both rules' glyph and colour; `mode_row` is drawn under the
    bottom rule where the terminal has room for it. `agent_list`, while the
    agent list is focused, is called with the rows the list may take
    (`layout.agent_list_room`, its hint row included) and returns the list's
    rows, which take the mode row's place; it is not called where there is no
    mode row. `composer` None draws no composer (nor the attachment row or the
    agent list): the slot takes its place, and the caret is `slot_caret`, a
    (row, col) within `slot`, or none where that is None or its row is cut.
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
    else:
        agent_list = None
    min_height = previous_height if previous_height > 0 else max_height(rows)
    room = 0
    if agent_list is not None:
        room = agent_list_room(
            rows,
            cols,
            sticky=sticky is not None,
            tail_rows=len(tail),
            slot_rows=len(slot),
            composer_rows=len(composer_lines),
            min_height=min_height,
        )
    listed = [truncate_line(row, cols) for row in agent_list(room)] if agent_list is not None and room else []
    budget = allocate(
        rows,
        cols,
        sticky=sticky is not None,
        tail_rows=len(tail),
        slot_rows=len(slot),
        composer_rows=len(composer_lines),
        min_height=min_height,
        agent_list_rows=len(listed),
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
        agent_list=listed,
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
        pad=budget.pad,
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
