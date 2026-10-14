"""The in-memory pager: `/history`, `/logs` and long slash output, on the alternate screen.

The pager never touches the live region or the terminal's scrollback. It
switches to the alternate screen, paints a page of already-rendered lines and
a footer, and switches back when the person leaves, which restores the main
screen (live region included) exactly as it was. Nothing it shows is written
anywhere else. After its first frame a pager paints only the rows that changed
since its last one, until the size changes or `invalidate` says something else
cleared the screen.

Keys: j / Down one line down, k / Up one line up, Space / PageDown a page
down, b / PageUp a page up, g / Home the top, G / End the bottom, q / Esc
leave.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .keys import CHAR, DOWN, END, ESCAPE, HOME, PAGE_DOWN, PAGE_UP, UP, Key, Paste
from .screen import HIDE_CURSOR, paint_line
from .style import CSI, Line, Span
from .theme import Theme

ALT_SCREEN_ON = f"{CSI}?1049h"
ALT_SCREEN_OFF = f"{CSI}?1049l"
FOOTER_KEYS = "j/k line · space/b page · g/G ends · q leaves"


def _move_to(row: int) -> str:
    """Column 1 of 0-based screen row `row`."""
    return f"{CSI}{row + 1};1H"


@dataclass
class Pager:
    title: str
    lines: Sequence[Line]
    top: int = 0
    # What the last frame left on each screen row (the line and its bytes), and the size it was drawn at.
    _painted: dict[int, tuple[Line, str]] = field(default_factory=dict, init=False, repr=False, compare=False)
    _size: tuple[int, int] | None = field(default=None, init=False, repr=False, compare=False)

    def invalidate(self) -> None:
        """Forget what is on the screen: the next frame paints every row."""
        self._painted = {}
        self._size = None

    def body_rows(self, rows: int) -> int:
        return max(rows - 1, 1)

    def _last_top(self, rows: int) -> int:
        return max(len(self.lines) - self.body_rows(rows), 0)

    def handle_key(self, event: Key | Paste, rows: int) -> bool:
        """Move by one key; False when the person left the pager."""
        if not isinstance(event, Key):
            return True
        page = self.body_rows(rows)
        char = event.char if event.name == CHAR and not event.ctrl and not event.alt else ""
        if event.name == ESCAPE or char == "q":
            return False
        if event.name == DOWN or char == "j":
            step = 1
        elif event.name == UP or char == "k":
            step = -1
        elif event.name == PAGE_DOWN or char == " ":
            step = page
        elif event.name == PAGE_UP or char == "b":
            step = -page
        elif event.name == HOME or char == "g":
            step = -len(self.lines)
        elif event.name == END or char == "G":
            step = len(self.lines)
        else:
            return True
        self.top = min(max(self.top + step, 0), self._last_top(rows))
        return True

    def frame(self, cols: int, rows: int, theme: Theme) -> str:
        """The alternate screen's rows for the current page that differ from the last frame, footer on the last row.

        "" when no row changed.
        """
        if self._size != (cols, rows):
            self.invalidate()
            self._size = (cols, rows)
        body = self.body_rows(rows)
        self.top = min(self.top, self._last_top(rows))
        shown = list(self.lines[self.top : self.top + body])
        parts: list[str] = []
        for row in range(body):
            self._paint(row, shown[row] if row < len(shown) else (), cols, parts)
        last = min(self.top + body, len(self.lines))
        position = f"{self.top + 1 if self.lines else 0}-{last}/{len(self.lines)}"
        footer: Line = (Span(f" {self.title} · {position} · {FOOTER_KEYS} ", theme.status),)
        self._paint(rows - 1, footer, cols, parts)
        return HIDE_CURSOR + "".join(parts) if parts else ""

    def _paint(self, row: int, line: Line, cols: int, parts: list[str]) -> None:
        """Add screen row `row`'s bytes to `parts`, unless the last frame left this very line there."""
        held = self._painted.get(row)
        if held is not None and held[0] == line:
            return
        painted = paint_line(line, cols)
        self._painted[row] = (line, painted)
        parts.append(_move_to(row) + painted)
