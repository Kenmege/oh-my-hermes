"""The inline live region: the only rows of the terminal the OMH TUI repaints.

Everything finished goes to the terminal's own scrollback as plain lines
(`flush_above`), so scrolling, selecting and copying stay the terminal's
native behaviour. Below that sits one live region pinned to the bottom of
the output: sticky prompt, streaming tail, panels, composer, status line.

The region never learns its absolute row. Every move is relative to the
region's own top (CR, cursor up/down, erase line, erase below, "\\r\\n"), so it
works wherever the shell left the cursor and inside any scrollback state.
Growing emits newlines from the region's last row, which scrolls the screen
up from the bottom exactly as printed output would; shrinking erases the
freed rows. Only rows whose bytes changed since the last frame are repainted,
which keeps a selection on a static row alive. After every frame the hardware
cursor is parked where the caller asked (the composer caret), so an IME
pre-edit appears in place.

Every row is clipped to `width` cells (a wide glyph that would straddle the
edge is dropped whole) and has its control characters removed, so neither a
stray escape in model output nor a long line can move the cursor. Autowrap is
switched off while a frame paints: if the terminal measures a glyph wider
than the width table does, the row is cut at the edge instead of wrapping
into the next row and shifting the whole region by one.

Pad rows: a frame may declare its first `pad` rows padding, blank rows the
layout adds so the region keeps its height (and so its bottom row) when the
content shrinks. `flush_above` writes into pad rows first: the flushed lines
land in those rows, which stop being part of the region, so the region gets
shorter by that many rows from the top and nothing below them moves or is
repainted. Only the lines beyond the pad push the region down the old way,
scrolling the screen by exactly as many rows as they add. Either way a region
whose bottom sits on the screen's last row keeps it there.

Precondition: the region is never taller than the terminal. Cursor-up stops
at the top of the screen, so a taller region would lose its relative
bookkeeping; the layout's height budget guarantees this.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .escapes import strip_escapes
from .style import CSI, SGR_RESET, Line, Span
from .width import is_control, line_width, truncate_line

CARRIAGE_RETURN = "\r"
NEWLINE = "\r\n"
ERASE_TO_LINE_END = f"{CSI}K"
ERASE_BELOW = f"{CSI}J"
HIDE_CURSOR = f"{CSI}?25l"
SHOW_CURSOR = f"{CSI}?25h"
AUTOWRAP_OFF = f"{CSI}?7l"
AUTOWRAP_ON = f"{CSI}?7h"
# Home, erase the screen, erase the scrollback (xterm `CSI 3 J`): the start of a full redraw.
CLEAR_SCREEN_AND_SCROLLBACK = f"{CSI}H{CSI}2J{CSI}3J"


def cursor_up(rows: int) -> str:
    return f"{CSI}{rows}A" if rows > 0 else ""


def cursor_down(rows: int) -> str:
    return f"{CSI}{rows}B" if rows > 0 else ""


def cursor_forward(cols: int) -> str:
    return f"{CSI}{cols}C" if cols > 0 else ""


def _without_controls(line: Line) -> Line:
    """The last guard before bytes reach the terminal: no escape sequence or control in span text.

    Styles are painted from `Span.style`, never from the text, so any sequence
    found in text came from somewhere else (a gateway string, a tool summary)
    and is removed whole rather than leaving its parameters on screen.
    """
    return tuple(
        Span("".join(ch for ch in strip_escapes(span.text) if not is_control(ch)), span.style) for span in line
    )


def paint_line(line: Line, width: int) -> str:
    """The bytes for one row, written from column 0.

    Styled spans end in an SGR reset, so the row leaves default attributes
    behind. A row shorter than `width` ends with erase-to-end-of-line; a full
    row does not, because on a terminal that keeps the cursor on the last
    column, erasing from there would delete the row's last glyph.
    """
    clipped = truncate_line(_without_controls(line), width, ellipsis="")
    parts: list[str] = []
    for span in clipped:
        sgr = span.style.sgr()
        parts.append(f"{sgr}{span.text}{SGR_RESET}" if sgr else span.text)
    if line_width(clipped) < width:
        parts.append(ERASE_TO_LINE_END)
    return "".join(parts)


class LiveRegion:
    def __init__(self, write: Callable[[str], None]) -> None:
        self._write = write
        self._rows: list[str] = []  # painted bytes of each row the region holds
        self._row = 0  # hardware cursor row, relative to the region's top
        self._parked: tuple[int, int] | None = (0, 0)
        self._width: int | None = None
        self._lines: tuple[Line, ...] = ()
        self._cursor = (0, 0)
        self._pad = 0  # how many of the top rows are padding `flush_above` may write into

    @property
    def height(self) -> int:
        """Rows the region holds on screen now (0 before the first frame and after `clear`)."""
        return len(self._rows)

    @property
    def pad(self) -> int:
        """How many of the region's top rows are padding."""
        return self._pad

    def _move_to(self, row: int, ops: list[str]) -> None:
        """Cursor to column 0 of region row `row`, which must already exist on screen."""
        if row < self._row:
            ops.append(cursor_up(self._row - row))
        elif row > self._row:
            ops.append(cursor_down(row - self._row))
        ops.append(CARRIAGE_RETURN)
        self._row = row

    def _compose(self, lines: Sequence[Line], cursor: tuple[int, int], width: int, ops: list[str]) -> str:
        """The frame's bytes, appended to `ops` already queued; "" when nothing changed."""
        width = max(width, 1)
        rows = [paint_line(line, width) for line in lines]
        repaint_all = width != self._width
        held = max(len(self._rows), 1)  # the row the cursor is on is the region's first row
        if len(rows) > held:
            self._move_to(held - 1, ops)
            ops.append(NEWLINE * (len(rows) - held))
            self._row = len(rows) - 1
        for index, row in enumerate(rows):
            if repaint_all or index >= len(self._rows) or self._rows[index] != row:
                self._move_to(index, ops)
                ops.append(row)
        if any(self._rows[len(rows) :]):
            self._move_to(len(rows), ops)
            ops.append(ERASE_BELOW)
        target = (
            min(max(cursor[0], 0), max(len(rows), 1) - 1),
            min(max(cursor[1], 0), width - 1),
        )
        painted = bool(ops)
        self._rows = rows
        self._width = width
        self._lines = tuple(lines)
        self._cursor = cursor
        if not painted and target == self._parked:
            return ""
        park: list[str] = []
        self._move_to(target[0], park)
        park.append(cursor_forward(target[1]))
        self._parked = target
        if not painted:
            return "".join(park)
        return "".join([HIDE_CURSOR, AUTOWRAP_OFF, *ops, AUTOWRAP_ON, *park, SHOW_CURSOR])

    def render(self, lines: Sequence[Line], cursor: tuple[int, int], width: int, pad: int = 0) -> None:
        """Show `lines` as the region and park the cursor at `cursor` = (row, col) within it.

        The first `pad` lines are padding (blank rows) that `flush_above` may write into.
        """
        frame = self._compose(lines, cursor, width, [])
        self._pad = min(max(pad, 0), len(self._rows))
        if frame:
            self._write(frame)

    def flush_above(self, lines: Sequence[Line], width: int) -> None:
        """Print `lines` permanently above the region, then repaint the region beneath them.

        The first lines fill the region's pad rows, which then leave the region:
        it gets shorter from the top and its bottom stays put. Lines beyond the
        pad are written over the region's top rows and push it down; once they
        scroll off the screen they are ordinary scrollback.
        """
        width = max(width, 1)
        consumed = min(len(lines), self._pad)
        ops: list[str] = []
        self._move_to(0, ops)
        for line in lines:
            ops.append(paint_line(line, width))
            ops.append(NEWLINE)
        # The cursor now sits on the row below the last flushed line: the new top.
        self._row = 0
        self._parked = None
        if len(lines) <= self._pad:
            self._rows = self._rows[consumed:]  # the rows below the pad did not move
        else:
            ops.append(ERASE_BELOW)
            self._rows = []
            self._width = None
        self._pad -= consumed
        self._lines = self._lines[consumed:]
        self._cursor = (self._cursor[0] - consumed, self._cursor[1])
        self._write(self._compose(self._lines, self._cursor, width, ops))

    def clear(self) -> None:
        """Erase the region and leave the cursor at column 0 of its top row (resize, exit)."""
        if not self._rows and self._parked == (0, 0):
            return
        ops: list[str] = []
        self._move_to(0, ops)
        ops.append(ERASE_BELOW)
        self._write("".join(ops))
        self._rows = []
        self._parked = (0, 0)
        self._width = None
        self._lines = ()
        self._cursor = (0, 0)
        self._pad = 0
