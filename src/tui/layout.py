"""The live region's height budget and the order its parts stack in.

Top to bottom the region holds:

    pad rows        blank rows that keep the region's bottom fixed when content shrinks
    sticky prompt   "↳ you: …" while a turn runs
    streaming tail  the last rows of the live transcript block
    slot            panel, completion menu or server-request prompt (one at a time); under a
                    panel or menu, or alone, the app's rows for the queued prompts
    top rule        the composer frame's upper edge
    composer        1..composer_cap(rows) input rows, the caret parked here
    bottom rule     the composer frame's lower edge
    mode row        the input mode, or a hint for the keys that matter now; while the agent
                    list is focused, its rows instead
    status line

The budget is a pure function of the terminal size and the content sizes:

- The region is never taller than `rows - 1`. `LiveRegion` moves only
  relative to its own top and cursor-up stops at the top of the screen, so a
  region taller than the screen loses its bookkeeping; the spare row keeps the
  region clear of that edge.
- Composer and status are placed first, so they always fit: the composer gets
  `min(input rows, max(1, rows // 4))`, at least one row whenever the screen
  has two; the status row comes next.
- Then the rows under the composer, each by the terminal's height alone: the
  bottom rule from `BOTTOM_RULE_MIN_ROWS` rows, the mode row from
  `MODE_ROW_MIN_ROWS`. A short terminal loses the mode row first and the
  bottom rule next, so a slot still gets its full cap there.
- Then the top rule, then the slot (at most `rows // 3`), then the sticky
  prompt (only when asked and a row is left), and the tail takes whatever
  remains, keeping its last rows.
- Below `MIN_ROWS` rows or `MIN_COLS` columns only composer and status are
  drawn, and, while a question answered with text is open (`text_question`),
  one slot row above them for its label when a row is left.
- No-shrink: `min_height` is the region's current height. When content needs
  fewer rows, pad rows fill the top so the region keeps its height, and with
  it its bottom row. The rows under the composer depend only on the terminal
  size, so the composer's distance from the bottom depends only on the
  composer's own row count, which is the invariant the composer row stands on.
- The one exception: while the agent list is focused (`agent_list_rows` above
  zero) it takes the mode row's place and as many rows as it has, up to
  `agent_list_room`, so the composer sits that much higher. It never makes the
  region taller than it would be without it: the rows it adds come out of the
  pad first, then out of the tail, the sticky prompt and the slot, so the
  region keeps its height and its last row. The moment focus leaves, the mode
  row is one row again and those rows go back where they came from, so the
  composer is back on the row it left.
- With the composer hidden (`composer_hidden`: the person picks from a list,
  so nothing is typed) the region draws no top rule, no input rows and no
  bottom rule, and the slot stands right above the mode row (above the status
  line where there is none), taking up to `choice_cap` rows: its own cap and
  the rows a one-row composer frame takes at that size. The mode row and the
  status line keep their rows, so once the composer is drawn again it is on
  the row it was on before, the region's height held by pad rows as always.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .style import Line

MIN_ROWS = 6
MIN_COLS = 20
# The rows under the composer, kept only where the slot still gets its full cap beside them.
BOTTOM_RULE_MIN_ROWS = 7
MODE_ROW_MIN_ROWS = 10

BLANK: Line = ()


@dataclass(frozen=True)
class Budget:
    pad: int
    sticky: int
    tail: int
    slot: int
    rule: int
    composer: int
    bottom_rule: int
    mode: int
    status: int

    @property
    def total(self) -> int:
        return (
            self.pad
            + self.sticky
            + self.tail
            + self.slot
            + self.rule
            + self.composer
            + self.bottom_rule
            + self.mode
            + self.status
        )

    @property
    def composer_top(self) -> int:
        """Region row of the composer's first row."""
        return self.pad + self.sticky + self.tail + self.slot + self.rule


def max_height(rows: int) -> int:
    """The tallest region a terminal of `rows` rows may hold."""
    return max(rows - 1, 0)


def is_tiny(rows: int, cols: int) -> bool:
    """True when the terminal is too small for anything but composer and status."""
    return rows < MIN_ROWS or cols < MIN_COLS


def under_composer(rows: int, cols: int) -> tuple[int, int]:
    """(bottom rule, mode row): the rows between the composer and the status line, by terminal size alone."""
    if is_tiny(rows, cols):
        return 0, 0
    return int(rows >= BOTTOM_RULE_MIN_ROWS), int(rows >= MODE_ROW_MIN_ROWS)


def composer_cap(rows: int) -> int:
    """The most rows the composer may show; render it with this as `max_rows`."""
    room = max_height(rows)
    if room >= 2:
        room -= 1  # the status row
    room -= int(rows >= BOTTOM_RULE_MIN_ROWS) + int(rows >= MODE_ROW_MIN_ROWS)
    return min(max(1, rows // 4), room)


def slot_cap(rows: int, cols: int) -> int:
    """The most rows a slot may take; slot producers size their content to it."""
    return 0 if is_tiny(rows, cols) else rows // 3


def choice_cap(rows: int, cols: int) -> int:
    """The most rows a slot may take while the composer is hidden: `slot_cap` and a one-row composer frame's rows.

    The frame is its top rule, its one input row and its bottom rule where the
    terminal draws one; below `MIN_ROWS` rows or `MIN_COLS` columns it is the
    input row alone.
    """
    frame = min(1, composer_cap(rows))
    if not is_tiny(rows, cols):
        frame += 1 + under_composer(rows, cols)[0]
    return slot_cap(rows, cols) + frame


def agent_list_cap(rows: int, cols: int) -> int:
    """The most rows the focused agent list may take, its hint row included; 0 where there is no mode row.

    What is left of the region once the top rule, a composer at its cap, the
    bottom rule and the status line have their rows.
    """
    if not under_composer(rows, cols)[1]:
        return 0
    return max_height(rows) - composer_cap(rows) - 3


def _list_room(without: Budget, rows: int, cols: int) -> int:
    """The agent list's rows in a region of `without`'s height: all but the top rule, the composer, the bottom rule and the status line."""
    return min(agent_list_cap(rows, cols), without.total - without.rule - without.composer - without.bottom_rule - without.status)


def agent_list_room(
    rows: int, cols: int, *, sticky: bool, tail_rows: int, slot_rows: int, composer_rows: int, min_height: int
) -> int:
    """The most rows the focused agent list may take in this frame, its hint row included; 0 where there is no mode row.

    Never more than the region the same frame would be without the list
    leaves under the composer, so the list never makes the region taller:
    at least the mode row it replaces, at most `agent_list_cap`.
    """
    if not under_composer(rows, cols)[1]:
        return 0
    without = allocate(
        rows, cols, sticky=sticky, tail_rows=tail_rows, slot_rows=slot_rows, composer_rows=composer_rows, min_height=min_height
    )
    return _list_room(without, rows, cols)


def allocate(
    rows: int,
    cols: int,
    *,
    sticky: bool,
    tail_rows: int,
    slot_rows: int,
    composer_rows: int,
    min_height: int,
    agent_list_rows: int = 0,
    composer_hidden: bool = False,
    text_question: bool = False,
) -> Budget:
    """Rows for each part of the region (see the module docstring for the rules)."""
    room = max_height(rows)
    composer = min(max(composer_rows, 1), composer_cap(rows))
    status = min(1, room - composer)
    bottom_rule, mode = under_composer(rows, cols)
    if composer_hidden:
        composer = bottom_rule = 0  # their rows, and the top rule's, go to the slot (`choice_cap`)
    elif agent_list_rows > 0 and mode:
        # The list never grows the region: it is held to the height the frame has without it, so the
        # rows it adds come out of the pad, then out of the tail, the sticky prompt and the slot below.
        without = allocate(
            rows, cols, sticky=sticky, tail_rows=tail_rows, slot_rows=slot_rows, composer_rows=composer_rows, min_height=min_height
        )
        room = without.total
        mode = min(agent_list_rows, _list_room(without, rows, cols))
    left = room - composer - status - bottom_rule - mode
    rule = slot = sticky_rows = tail = 0
    if composer_hidden:
        slot = min(max(slot_rows, 0), choice_cap(rows, cols), left)
        left -= slot
    elif text_question and is_tiny(rows, cols):
        slot = min(max(slot_rows, 0), 1, left)
        left -= slot
    if not is_tiny(rows, cols):
        if not composer_hidden:
            rule = min(1, left)
            left -= rule
            slot = min(max(slot_rows, 0), slot_cap(rows, cols), left)
            left -= slot
        sticky_rows = 1 if sticky and left > 0 else 0
        left -= sticky_rows
        tail = min(max(tail_rows, 0), left)
    content = sticky_rows + tail + slot + rule + composer + bottom_rule + mode + status
    pad = min(max(min_height - content, 0), room - content)
    return Budget(
        pad=pad,
        sticky=sticky_rows,
        tail=tail,
        slot=slot,
        rule=rule,
        composer=composer,
        bottom_rule=bottom_rule,
        mode=mode,
        status=status,
    )


def _last(lines: Sequence[Line], count: int) -> list[Line]:
    """The last `count` lines, blank rows on top when there are fewer."""
    kept = list(lines)[max(len(lines) - count, 0) :] if count > 0 else []
    return [BLANK] * (count - len(kept)) + kept


def _first(lines: Sequence[Line], count: int) -> list[Line]:
    """The first `count` lines, blank rows on top when there are fewer."""
    kept = list(lines)[:count] if count > 0 else []
    return [BLANK] * (count - len(kept)) + kept


def compose(
    budget: Budget,
    *,
    sticky: Line | None,
    tail: Sequence[Line],
    slot: Sequence[Line],
    rule: Line,
    composer: Sequence[Line],
    bottom_rule: Line,
    mode: Line,
    status: Line,
    agent_list: Sequence[Line] = (),
) -> list[Line]:
    """The region's rows, exactly `budget.total` of them, top to bottom.

    The tail keeps its last rows; the slot and the composer keep their first
    rows (their producers size them to `slot_cap` and `composer_cap`), and so
    does `agent_list`, which takes the mode row's rows when it has any.
    """
    region: list[Line] = [BLANK] * budget.pad
    if budget.sticky:
        region.append(sticky if sticky is not None else BLANK)
    region.extend(_last(tail, budget.tail))
    region.extend(_first(slot, budget.slot))
    if budget.rule:
        region.append(rule)
    composer_rows = list(composer)[: budget.composer]
    region.extend(composer_rows + [BLANK] * (budget.composer - len(composer_rows)))
    if budget.bottom_rule:
        region.append(bottom_rule)
    region.extend(_first(agent_list or [mode], budget.mode))
    if budget.status:
        region.append(status)
    return region


def caret_row(budget: Budget, composer_caret_row: int) -> int:
    """The region row of the composer's caret row."""
    last = max(budget.composer - 1, 0)
    return budget.composer_top + min(max(composer_caret_row, 0), last)
