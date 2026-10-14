"""The composer: the multi-line input a prompt is typed into.

`Composer` is a state machine with no IO: `handle` takes one decoded key or
paste and returns what the app should do about it, `render` draws the text at
a width and reports where the caret is in cells, so the app parks the
hardware cursor there and an IME pre-edit (Korean, Japanese) shows in place.

Text is kept NFC and the caret is a str index on a cluster boundary
(`width.clusters`): a Hangul syllable, an emoji with a skin tone, a ZWJ family
or a flag is one step for the arrows and one deletion for Backspace/Delete.
A decomposed (NFD) paste is composed when it is inserted. Control characters
other than newline and tab are dropped on insert; a tab is kept and drawn as
`TAB_CELLS` spaces.

Wrapping is by cells, cluster by cluster (no word wrap), so every caret
position maps to exactly one (row, col). A row filled to the last cell is
followed by an empty row, so the caret after it never sits past the right
edge. Continuation rows are indented by the prompt's width. When the input
needs more than `max_rows` rows, the visible window scrolls just enough to
keep the caret row inside it.

Keys: printable text inserts; Enter submits, except right after a backslash,
which it turns into a newline; Alt+Enter, Shift+Enter and Ctrl+J insert a
newline (Ctrl+Enter is Enter here; the app decides what its submit means);
Backspace/Delete remove one cluster; Left/Right move one cluster,
with Ctrl or Alt one word; Up/Down move one visual row and browse history at
the first and last row; Home/End and Ctrl+A/Ctrl+E go to the start/end of the
logical line; Ctrl+U/Ctrl+K kill to the start/end of the line (at the edge,
the newline itself); Ctrl+W deletes the word before the caret; a paste
inserts as is, newlines kept; Tab completes; Esc cancels and keeps the text;
Ctrl+C clears the text and cancels, or reports an interrupt when there is no
text; Ctrl+D exits on empty input and deletes forward otherwise.

History lives in memory only, oldest first; a submit appends unless the text
is blank or repeats the newest entry, or `records_history` is off (the app
turns it off while the composer answers a question Hermes asked). Masked input
(secrets, sudo) draws one bullet per cluster, refuses newlines, never browses
history, never enters it, and is shown as bullets by `repr` too.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
import unicodedata

from .keys import (
    BACKSPACE,
    CHAR,
    DELETE,
    DOWN,
    END,
    ENTER,
    ESCAPE,
    HOME,
    LEFT,
    RIGHT,
    TAB,
    UP,
    Key,
    Paste,
)
from .style import DEFAULT_STYLE, Line, Span, Style
from .width import TAB_CELLS, cell_width, cluster_width, clusters, is_control

PROMPT = "› "
MASK = "•"
NEWLINE = "\n"
TAB_CHAR = "\t"

NONE = "none"
SUBMIT = "submit"
CANCEL = "cancel"
INTERRUPT = "interrupt"
EXIT = "exit"
COMPLETE = "complete"


@dataclass(frozen=True)
class ComposerAction:
    kind: str
    text: str = ""  # the submitted text, for SUBMIT only


NOTHING = ComposerAction(NONE)


@dataclass(frozen=True)
class _Row:
    start: int  # cluster index of the row's first cluster
    end: int  # cluster index after the row's last cluster (a newline is never inside a row)
    hard: bool  # the row ends its logical line, so the position at `end` belongs to it


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _boundaries(text: str) -> list[int]:
    """Str index of every cluster boundary in NFC `text`, from 0 to len(text)."""
    bounds = [0]
    for cluster in clusters(text):
        bounds.append(bounds[-1] + len(cluster))
    return bounds


def _cluster_index(bounds: Sequence[int], caret: int) -> int:
    """How many clusters lie before `caret`; a caret inside a cluster counts as its start."""
    return max(bisect_right(bounds, caret) - 1, 0)


def _clean(text: str, masked: bool) -> str:
    kept: list[str] = []
    for ch in text.replace("\r\n", NEWLINE).replace("\r", NEWLINE):
        if ch == NEWLINE:
            if not masked:
                kept.append(ch)
        elif ch == TAB_CHAR or not is_control(ch):
            kept.append(ch)
    return "".join(kept)


def _layout(widths: Sequence[int | None], avail: int) -> list[_Row]:
    """Visual rows for clusters of these widths (`None` is a newline) at `avail` cells."""
    rows: list[_Row] = []
    start = used = 0
    for index, width in enumerate(widths):
        if width is None:
            rows.extend(_close_line(start, index, used, avail))
            start, used = index + 1, 0
            continue
        if used and used + width > avail:
            rows.append(_Row(start, index, hard=False))
            start, used = index, 0
        used += width
    rows.extend(_close_line(start, len(widths), used, avail))
    return rows


def _close_line(start: int, end: int, used: int, avail: int) -> list[_Row]:
    if used >= avail:  # full to the edge: the position after it gets a row of its own
        return [_Row(start, end, hard=False), _Row(end, end, hard=True)]
    return [_Row(start, end, hard=True)]


def _locate(rows: Sequence[_Row], widths: Sequence[int | None], index: int) -> tuple[int, int]:
    """(visual row, cells from the row start) of the position before cluster `index`."""
    for number, row in enumerate(rows):
        if row.start <= index < row.end or (index == row.end and row.hard):
            return number, sum(width or 0 for width in widths[row.start : index])
    return len(rows) - 1, 0


def _position_at(row: _Row, widths: Sequence[int | None], col: int) -> int:
    """The rightmost position in `row` whose column is at most `col`."""
    last = row.end if row.hard else row.end - 1
    best = row.start
    used = 0
    for index in range(row.start, last):
        used += widths[index] or 0
        if used > col:
            break
        best = index + 1
    return best


def _word_start(units: Sequence[str], index: int) -> int:
    while index > 0 and units[index - 1].isspace():
        index -= 1
    while index > 0 and not units[index - 1].isspace():
        index -= 1
    return index


def _word_end(units: Sequence[str], index: int) -> int:
    while index < len(units) and units[index].isspace():
        index += 1
    while index < len(units) and not units[index].isspace():
        index += 1
    return index


@dataclass
class Composer:
    text: str = ""
    caret: int = 0
    history: list[str] = field(default_factory=list)
    history_index: int | None = None
    masked: bool = False
    records_history: bool = True
    _draft: str = field(default="", init=False, repr=False, compare=False)
    _avail: int | None = field(default=None, init=False, repr=False, compare=False)
    _scroll: int = field(default=0, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._set(self.text, min(max(self.caret, 0), len(self.text)))

    def __repr__(self) -> str:
        text = MASK * len(clusters(self.text)) if self.masked else self.text
        return f"Composer(text={text!r}, caret={self.caret}, masked={self.masked})"

    # -- editing primitives ---------------------------------------------------

    def _set(self, text: str, caret: int) -> None:
        """Store `text` as NFC with the caret moved to the matching cluster boundary."""
        full = _nfc(text)
        bounds = _boundaries(full)
        self.text = full
        self.caret = bounds[_cluster_index(bounds, min(len(_nfc(text[:caret])), len(full)))]

    def _replace(self, start: int, end: int, insert: str = "") -> None:
        self._set(self.text[:start] + insert + self.text[end:], start + len(insert))

    def _insert(self, text: str) -> None:
        cleaned = _clean(text, self.masked)
        if cleaned:
            self._replace(self.caret, self.caret, cleaned)

    def replace(self, start: int, end: int, text: str) -> None:
        """Put `text` (cleaned as a paste is) in place of `self.text[start:end]`; the caret lands after it."""
        self._replace(start, end, _clean(text, self.masked))

    def _units(self) -> tuple[list[str], list[int], int]:
        """Clusters, their boundaries, and the caret's cluster index."""
        units = clusters(self.text)
        bounds = _boundaries(self.text)
        return units, bounds, _cluster_index(bounds, self.caret)

    def _load(self, text: str) -> None:
        self._set(text, len(text))
        self._scroll = 0

    def _clear(self) -> None:
        self._load("")
        self.history_index = None
        self._draft = ""

    # -- key handling ---------------------------------------------------------

    def handle(self, event: Key | Paste) -> ComposerAction:
        if isinstance(event, Paste):
            self._insert(event.text)
            return NOTHING
        if event.name == ENTER:
            adds_line = event.alt or event.shift
            if not adds_line and not self.masked and self.text[self.caret - 1 : self.caret] == "\\":
                self._replace(self.caret - 1, self.caret, NEWLINE)
                return NOTHING
            if adds_line:
                self._insert(NEWLINE)
                return NOTHING
            return self._submit()
        if event.name == CHAR:
            if event.ctrl:
                return self._control(event.char)
            if not event.alt:
                self._insert(event.char)
            return NOTHING
        if event.name == TAB:
            return NOTHING if event.shift else ComposerAction(COMPLETE)
        if event.name == ESCAPE:
            return ComposerAction(CANCEL)
        if event.name in (UP, DOWN):
            self._vertical(-1 if event.name == UP else 1)
            return NOTHING
        self._move_or_delete(event)
        return NOTHING

    def _move_or_delete(self, key: Key) -> None:
        units, bounds, index = self._units()
        by_word = key.ctrl or key.alt
        if key.name == BACKSPACE and index > 0:
            self._replace(bounds[index - 1], self.caret)
        elif key.name == DELETE and index < len(units):
            self._replace(self.caret, bounds[index + 1])
        elif key.name == LEFT:
            self.caret = bounds[_word_start(units, index) if by_word else max(index - 1, 0)]
        elif key.name == RIGHT:
            self.caret = bounds[_word_end(units, index) if by_word else min(index + 1, len(units))]
        elif key.name == HOME:
            self.caret = self._line_start()
        elif key.name == END:
            self.caret = self._line_end()

    def _control(self, char: str) -> ComposerAction:
        if char == "c":
            if not self.text:
                return ComposerAction(INTERRUPT)
            self._clear()
            return ComposerAction(CANCEL)
        if char == "d":
            if not self.text:
                return ComposerAction(EXIT)
            self._move_or_delete(Key(DELETE))
        elif char == "j":
            self._insert(NEWLINE)
        elif char == "a":
            self.caret = self._line_start()
        elif char == "e":
            self.caret = self._line_end()
        elif char == "u":
            start = self._line_start()
            self._replace(start - 1 if start == self.caret and start > 0 else start, self.caret)
        elif char == "k":
            end = self._line_end()
            self._replace(self.caret, end + 1 if end == self.caret and end < len(self.text) else end)
        elif char == "w":
            units, bounds, index = self._units()
            self._replace(bounds[_word_start(units, index)], self.caret)
        return NOTHING

    def _line_start(self) -> int:
        return self.text.rfind(NEWLINE, 0, self.caret) + 1

    def _line_end(self) -> int:
        end = self.text.find(NEWLINE, self.caret)
        return len(self.text) if end < 0 else end

    def _submit(self) -> ComposerAction:
        text = self.text
        if self.records_history and not self.masked and text.strip() and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self._clear()
        return ComposerAction(SUBMIT, text)

    # -- rows and history -----------------------------------------------------

    def _cells(self) -> tuple[list[str], list[int | None]]:
        """What each cluster draws as, and its width (`None` for a newline)."""
        shown: list[str] = []
        widths: list[int | None] = []
        for unit in clusters(self.text):
            if unit == NEWLINE:
                shown.append("")
                widths.append(None)
            elif self.masked:
                shown.append(MASK)
                widths.append(1)
            elif unit == TAB_CHAR:
                shown.append(" " * TAB_CELLS)
                widths.append(TAB_CELLS)
            else:
                shown.append(unit)
                widths.append(cluster_width(unit))
        return shown, widths

    def _vertical(self, step: int) -> None:
        _, widths = self._cells()
        unwrapped = sum(width or 0 for width in widths) + 1  # never rendered: logical lines only
        rows = _layout(widths, self._avail if self._avail is not None else unwrapped)
        bounds = _boundaries(self.text)
        row, col = _locate(rows, widths, _cluster_index(bounds, self.caret))
        target = row + step
        if 0 <= target < len(rows):
            self.caret = bounds[_position_at(rows[target], widths, col)]
        elif step < 0:
            self._history_back()
        else:
            self._history_forward()

    def _history_back(self) -> None:
        if self.masked or not self.history or self.history_index == 0:
            return
        if self.history_index is None:
            self._draft = self.text
            self.history_index = len(self.history) - 1
        else:
            self.history_index -= 1
        self._load(self.history[self.history_index])

    def _history_forward(self) -> None:
        if self.masked or self.history_index is None:
            return
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self._load(self.history[self.history_index])
            return
        self.history_index = None
        self._load(self._draft)
        self._draft = ""

    # -- drawing --------------------------------------------------------------

    def render(
        self,
        width: int,
        max_rows: int,
        prompt: str = PROMPT,
        prompt_style: Style = DEFAULT_STYLE,
    ) -> tuple[list[Line], tuple[int, int]]:
        """The visible rows at `width` cells, and the caret as (row, col) within them."""
        prompt_cells = cell_width(prompt)
        avail = max(width - prompt_cells, 1)
        self._avail = avail
        shown, widths = self._cells()
        rows = _layout(widths, avail)
        row, col = _locate(rows, widths, _cluster_index(_boundaries(self.text), self.caret))
        visible = max(max_rows, 1)
        scroll = min(max(self._scroll, row - visible + 1), row)
        scroll = max(min(scroll, len(rows) - visible), 0)
        self._scroll = scroll
        indent = Span(" " * prompt_cells)
        lines: list[Line] = []
        for number in range(scroll, min(len(rows), scroll + visible)):
            lead = Span(prompt, prompt_style) if number == 0 else indent
            body = "".join(shown[rows[number].start : rows[number].end])
            lines.append((lead, Span(body)) if body else (lead,))
        return lines, (row - scroll, prompt_cells + col)
