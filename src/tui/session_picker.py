"""The `/sessions` picker: Hermes' stored sessions in the slot, filtered as you type.

`session.list` answers with the person's stored sessions, most recent first.
The picker shows each as one row (its title, or the first prompt when it has
no title, its age and its message count), marks the session this TUI is in,
and returns the id the person picked. Its last row is the filter
(`filter_line`), drawn where the composer is, which the app hides while a
picker is open. It holds no IO: the app sends the list call, feeds keys in,
and runs the switch with the id it gets back.

Keys: Up/Down move one row, PageUp/PageDown one page; printable text filters
(by title, first prompt or id, ignoring case) and Backspace takes a filter
character back; Enter picks the highlighted row; Esc or Ctrl+C closes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .keys import BACKSPACE, CHAR, DOWN, ENTER, ESCAPE, PAGE_DOWN, PAGE_UP, UP, Key, Paste
from .style import Line, Span
from .theme import Theme
from .width import cell_width, truncate, truncate_line

PICK = "pick"
CLOSE = "close"
CURRENT_MARK = "●"
# Its keys, longest first: on the mode row while it is open, in its header where there is no mode row.
KEYS = ("↑↓ move · enter switches · esc closes", "↑↓ enter switches · esc closes", "esc closes")
FILTER_PROMPT = " › "
FILTER_PLACEHOLDER = "type to filter"


@dataclass(frozen=True)
class SessionRow:
    id: str
    title: str
    preview: str
    started_at: float
    message_count: int

    @property
    def label(self) -> str:
        """What the row is called: the title, else the first prompt, else the id."""
        return (self.title or self.preview or self.id).strip().split("\n", 1)[0]


@dataclass(frozen=True)
class PickerAction:
    kind: str  # PICK or CLOSE
    session_id: str = ""


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def session_rows(result: Mapping[str, object]) -> list[SessionRow]:
    """The rows of a `session.list` answer, in its order; rows without an id are skipped."""
    sessions = result.get("sessions")
    if not isinstance(sessions, list):
        return []
    rows: list[SessionRow] = []
    for entry in sessions:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            continue
        title = entry.get("title")
        preview = entry.get("preview")
        rows.append(
            SessionRow(
                id=entry["id"],
                title=title if isinstance(title, str) else "",
                preview=preview if isinstance(preview, str) else "",
                started_at=_number(entry.get("started_at")),
                message_count=int(_number(entry.get("message_count"))),
            )
        )
    return rows


def age_text(started_at: float, now: float) -> str:
    """`just now`, `5m ago`, `3h ago`, `2d ago`; "" when the start time is unknown."""
    if started_at <= 0:
        return ""
    seconds = max(0, int(now - started_at))
    for size, suffix in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{seconds // size}{suffix} ago"
    return "just now"


def filter_line(query: str, width: int, theme: Theme) -> Line:
    """A picker's last row: what typing filters it by, or how to start, after the composer's prompt glyph."""
    typed = Span(query, theme.text) if query else Span(FILTER_PLACEHOLDER, theme.hint)
    return truncate_line((Span(FILTER_PROMPT, theme.prompt), typed), width)


def _messages(count: int) -> str:
    return f"{count} message{'' if count == 1 else 's'}"


@dataclass
class SessionPicker:
    rows: list[SessionRow]
    current: str  # the stored id of the session this TUI is in
    query: str = ""
    index: int = 0
    page: int = 1  # rows the last render showed; PageUp/PageDown move by this many

    def visible(self) -> list[SessionRow]:
        """The rows the filter keeps, in list order."""
        needle = self.query.casefold()
        if not needle:
            return list(self.rows)
        return [row for row in self.rows if needle in f"{row.title}\n{row.preview}\n{row.id}".casefold()]

    def handle_key(self, event: Key | Paste) -> PickerAction | None:
        """Apply one key; a `PickerAction` when the person picked a row or closed the picker."""
        if isinstance(event, Paste):
            self._filter(self.query + event.text.split("\n", 1)[0])
            return None
        if event.name == ESCAPE or (event.name == CHAR and event.ctrl and event.char == "c"):
            return PickerAction(CLOSE)
        shown = self.visible()
        if event.name == ENTER:
            return PickerAction(PICK, shown[min(self.index, len(shown) - 1)].id) if shown else None
        steps = {UP: -1, DOWN: 1, PAGE_UP: -self.page, PAGE_DOWN: self.page}
        if event.name in steps and shown:
            self.index = min(max(self.index + steps[event.name], 0), len(shown) - 1)
        elif event.name == BACKSPACE:
            self._filter(self.query[:-1])
        elif event.name == CHAR and not event.ctrl and not event.alt:
            self._filter(self.query + event.char)
        return None

    def _filter(self, query: str) -> None:
        self.query = query
        self.index = 0

    def lines(self, width: int, theme: Theme, max_rows: int, now: float, keys: bool = True) -> list[Line]:
        """A header row (its keys after the title when `keys`), the filtered rows scrolled so the highlighted one
        shows, then the filter row."""
        if max_rows <= 0:
            return []
        shown = self.visible()
        title = f" Sessions ({len(shown)}/{len(self.rows)})" if self.query else f" Sessions ({len(self.rows)})"
        header = (Span(title, theme.heading), Span(f"  {KEYS[0]}" if keys else "", theme.hint))
        lines: list[Line] = [truncate_line(header, width)]
        if max_rows == 1:
            return lines
        body = max_rows - 2
        self.page = max(body, 1)
        if not shown:
            lines.append((Span(" no session matches the filter", theme.muted),))
        else:
            self.index = min(self.index, len(shown) - 1)
            start = min(max(self.index - body + 1, 0), max(len(shown) - body, 0))
            for number in range(start, min(start + body, len(shown))):
                lines.append(self._row(shown[number], number == self.index, width, theme, now))
        return [*lines[: max_rows - 1], filter_line(self.query, width, theme)]

    def _row(self, row: SessionRow, highlighted: bool, width: int, theme: Theme, now: float) -> Line:
        style = theme.menu_current if highlighted else theme.menu
        mark = CURRENT_MARK if row.id == self.current else " "
        meta = " · ".join(part for part in (age_text(row.started_at, now), _messages(row.message_count)) if part)
        meta = f" {meta} "
        lead = f" {mark} "
        room = width - cell_width(lead) - cell_width(meta)
        if room < 8:
            return truncate_line((Span(lead + row.label, style),), width)
        label = truncate(row.label, room)
        gap = " " * (room - cell_width(label))
        return (Span(lead + label + gap + meta, style),)
