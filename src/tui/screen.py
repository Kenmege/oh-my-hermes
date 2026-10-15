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
pre-edit appears in place. A frame with no caret (a question answered by
picking: neither the composer nor a picker's filter is drawn) leaves the cursor hidden, parked on the region's last row; the next frame with
a caret shows it again.

Every row is clipped to `width` cells (a wide glyph that would straddle the
edge is dropped whole) and has its control characters and bidi controls
removed, so neither a stray escape in model output nor a long line can move
the cursor, and no embedding or override can make a bidi-aware terminal draw
a row in another order than the one measured. Autowrap is
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

Synchronized output: every frame that paints rows is one synchronized
update (DECSET 2026, `SYNC_BEGIN` ... `SYNC_END`), so a terminal that
supports it shows the frame whole instead of row by row, and a blinking or
animated row never flickers; a terminal that does not ignores the mode. A
frame that only parks the cursor is not wrapped. `render(..., flush=lines)`
writes a frame and the lines flushed after it as one write and one update,
so no screen between the two shows.

Precondition: the region is never taller than the terminal. Cursor-up stops
at the top of the screen, so a taller region would lose its relative
bookkeeping; the layout's height budget guarantees this.

Where the region is on the screen (`last_row`) is worked out, never asked of
the terminal, and only where it is certain. Growing pushes the last row down
one screen row per row added, and so does every flushed line beyond the pad,
until the last row is the screen's last row, where it stays. So the row is
known once those pushes would have reached the screen's last row from any
start, and from the first frame on when the caller says that frame starts on
the screen's top row (`at_top`: it homed the cursor first). A frame that
takes rows off the region's end moves the last row back up. The app never
draws one (its frames are never shorter than the region), so nothing here
follows it: the row is unknown from then on.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .escapes import strip_escapes
from .style import CSI, SGR_RESET, Line, Span, is_fill
from .width import is_unpainted, line_width, truncate_line

CARRIAGE_RETURN = "\r"
NEWLINE = "\r\n"
ERASE_TO_LINE_END = f"{CSI}K"
ERASE_BELOW = f"{CSI}J"
HIDE_CURSOR = f"{CSI}?25l"
SHOW_CURSOR = f"{CSI}?25h"
AUTOWRAP_OFF = f"{CSI}?7l"
AUTOWRAP_ON = f"{CSI}?7h"
# Home, erase the screen, erase the scrollback (xterm `CSI 3 J`): the start of a full redraw.
CURSOR_HOME = f"{CSI}H"
CLEAR_SCREEN_AND_SCROLLBACK = f"{CSI}H{CSI}2J{CSI}3J"
# One synchronized update: the terminal holds the screen until the end mark, then shows the frame at once.
SYNC_BEGIN = f"{CSI}?2026h"
SYNC_END = f"{CSI}?2026l"


def cursor_up(rows: int) -> str:
    return f"{CSI}{rows}A" if rows > 0 else ""


def cursor_down(rows: int) -> str:
    return f"{CSI}{rows}B" if rows > 0 else ""


def cursor_forward(cols: int) -> str:
    return f"{CSI}{cols}C" if cols > 0 else ""


def _without_controls(line: Line) -> Line:
    """The last guard before bytes reach the terminal: no escape sequence, control or bidi control in span text.

    Styles are painted from `Span.style`, never from the text, so any sequence
    found in text came from somewhere else (a gateway string, a tool summary)
    and is removed whole rather than leaving its parameters on screen.
    """
    return tuple(
        Span("".join(ch for ch in strip_escapes(span.text) if not is_unpainted(ch)), span.style) for span in line
    )


def paint_line(line: Line, width: int) -> str:
    """The bytes for one row, written from column 0.

    Styled spans end in an SGR reset, so the row leaves default attributes
    behind. A row shorter than `width` ends with erase-to-end-of-line; a full
    row does not, because on a terminal that keeps the cursor on the last
    column, erasing from there would delete the row's last glyph. A row whose
    last span is empty and has a background (`style.fill`) erases with that
    background, which a terminal paints to the right edge (background colour
    erase), so a band reaches the edge without trailing spaces to copy.
    """
    fill = line[-1].style if line and is_fill(line[-1]) else None
    clipped = truncate_line(_without_controls(line), width, ellipsis="")
    parts: list[str] = []
    for span in clipped:
        sgr = span.style.sgr()
        parts.append(f"{sgr}{span.text}{SGR_RESET}" if sgr else span.text)
    if line_width(clipped) < width:
        parts.append(f"{fill.sgr()}{ERASE_TO_LINE_END}{SGR_RESET}" if fill is not None else ERASE_TO_LINE_END)
    return "".join(parts)


class LiveRegion:
    def __init__(self, write: Callable[[str], None], at_top: bool = False) -> None:
        self._write = write
        self._rows: list[str] = []  # painted bytes of each row the region holds
        self._row = 0  # hardware cursor row, relative to the region's top
        self._parked: tuple[int, int] | None = (0, 0)
        self._width: int | None = None
        self._lines: tuple[Line, ...] = ()
        self._cursor: tuple[int, int] | None = (0, 0)
        self._hidden = False  # the last frame left the hardware cursor hidden (it had no caret)
        self._pad = 0  # how many of the top rows are padding `flush_above` may write into
        # What `last_row` stands on: whether the first frame starts on the screen's top row, that
        # frame's height (0 until it is drawn), and the rows the last row was pushed down since
        # (None once a frame took rows off the region's end).
        self._at_top = at_top
        self._first_height = 0
        self._pushed: int | None = 0

    @property
    def height(self) -> int:
        """Rows the region holds on screen now (0 before the first frame and after `clear`)."""
        return len(self._rows)

    @property
    def pad(self) -> int:
        """How many of the region's top rows are padding."""
        return self._pad

    @property
    def caret_hidden(self) -> bool:
        """Whether the last frame had no caret, so the hardware cursor is hidden."""
        return self._hidden

    def last_row(self, screen_rows: int) -> int | None:
        """The 0-based screen row the region's last row is on, or None where that is not certain.

        `screen_rows` is the terminal's height, the same since the first frame.
        """
        if not self._rows or self._pushed is None:
            return None
        from_top = self._first_height - 1 + self._pushed  # where a first frame on the top row would be now
        if from_top >= screen_rows - 1:
            return screen_rows - 1  # a start further down reached the last row sooner
        return from_top if self._at_top else None

    def _shift_last_row(self, rows: int) -> None:
        """The last row went `rows` rows down the screen (fewer where the screen ended); a negative count loses it."""
        if self._pushed is not None:
            self._pushed = self._pushed + rows if rows >= 0 else None

    def _move_to(self, row: int, ops: list[str]) -> None:
        """Cursor to column 0 of region row `row`, which must already exist on screen."""
        if row < self._row:
            ops.append(cursor_up(self._row - row))
        elif row > self._row:
            ops.append(cursor_down(row - self._row))
        ops.append(CARRIAGE_RETURN)
        self._row = row

    def _compose(self, lines: Sequence[Line], cursor: tuple[int, int] | None, width: int, ops: list[str]) -> tuple[str, bool]:
        """The frame's bytes, after `ops` already queued ("" when nothing changed), and whether they paint rows.

        A row whose line and width are the ones the last frame painted keeps
        that frame's bytes instead of being painted again. `cursor` None hides
        the hardware cursor and parks it at the start of the last row.
        """
        width = max(width, 1)
        repaint_all = width != self._width
        last_rows, last_lines = self._rows, self._lines
        kept = 0 if repaint_all else min(len(last_rows), len(last_lines))
        rows = [
            last_rows[index] if index < kept and line == last_lines[index] else paint_line(line, width)
            for index, line in enumerate(lines)
        ]
        held = max(len(self._rows), 1)  # the row the cursor is on is the region's first row
        if not self._first_height:
            self._first_height = len(rows)
        elif self._rows:  # empty after a flush past the pad, which counted its own push
            self._shift_last_row(len(rows) - len(self._rows))
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
        hidden = cursor is None
        last = max(len(rows), 1) - 1
        target = (last, 0) if cursor is None else (min(max(cursor[0], 0), last), min(max(cursor[1], 0), width - 1))
        painted = bool(ops)
        toggled = hidden != self._hidden  # the caret alone appearing or going is a change to write
        self._rows = rows
        self._width = width
        self._lines = tuple(lines)
        self._cursor = cursor
        self._hidden = hidden
        if not painted and target == self._parked and not toggled:
            return "", False
        park: list[str] = []
        self._move_to(target[0], park)
        park.append(cursor_forward(target[1]))
        self._parked = target
        if not painted:
            return "".join(park) + ((HIDE_CURSOR if hidden else SHOW_CURSOR) if toggled else ""), False
        shown = [] if hidden else [SHOW_CURSOR]
        return "".join([HIDE_CURSOR, AUTOWRAP_OFF, *ops, AUTOWRAP_ON, *park, *shown]), True

    def _send(self, written: str, painted: bool) -> None:
        """Write `written`, as one synchronized update when it paints rows."""
        if written:
            self._write(f"{SYNC_BEGIN}{written}{SYNC_END}" if painted else written)

    def render(
        self, lines: Sequence[Line], cursor: tuple[int, int] | None, width: int, pad: int = 0, flush: Sequence[Line] = ()
    ) -> None:
        """Show `lines` as the region and park the cursor at `cursor` = (row, col) within it; None hides it.

        The first `pad` lines are padding (blank rows) that `flush_above` may write into. `flush` is flushed
        above the region right after, in the same write.
        """
        written, painted = self._compose(lines, cursor, width, [])
        self._pad = min(max(pad, 0), len(self._rows))
        if flush:
            more, also = self._flush(flush, width)
            written, painted = written + more, painted or also
        self._send(written, painted)

    def flush_above(self, lines: Sequence[Line], width: int) -> None:
        """Print `lines` permanently above the region, then repaint the region beneath them.

        The first lines fill the region's pad rows, which then leave the region:
        it gets shorter from the top and its bottom stays put. Lines beyond the
        pad are written over the region's top rows and push it down; once they
        scroll off the screen they are ordinary scrollback.
        """
        self._send(*self._flush(lines, width))

    def _flush(self, lines: Sequence[Line], width: int) -> tuple[str, bool]:
        """`flush_above`'s bytes and whether they paint rows, unwritten."""
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
        if not self._first_height:
            self._at_top = self._at_top and not lines  # the first frame now starts below these lines
        if len(lines) <= self._pad:
            self._rows = self._rows[consumed:]  # the rows below the pad did not move
        else:
            ops.append(ERASE_BELOW)
            self._rows = []
            self._width = None
            if self._first_height:
                # The rows left restart below the flushed lines; with none left, nothing says where.
                self._shift_last_row(len(lines) - consumed if len(self._lines) > consumed else -1)
        self._pad -= consumed
        self._lines = self._lines[consumed:]
        if self._cursor is not None:
            self._cursor = (self._cursor[0] - consumed, self._cursor[1])
        return self._compose(self._lines, self._cursor, width, ops)

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
        self._at_top = False  # the next first frame starts wherever this region's top row was
        self._first_height = 0
        self._pushed = 0
