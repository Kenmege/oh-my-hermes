"""The composer stays put: `build_frame` + `LiveRegion` driven into a VT screen model.

The owner's top complaint about the Hermes-hosted TUI is that the prompt
"goes too far up": rows added around the input push the composer upward.
This file is the regression proof that the OMH TUI's composer does not move.
It runs the frame helper and the live region the way the app loop will (flush
stable lines with `flush_above`, then build a frame from the region's current
height and render it) through event sequences: a streaming tail that grows
past the screen, stable lines flushed with and without pad rows, a panel slot
that grows and shrinks, a server-request prompt slot, a completion menu, a
composer that grows to its maximum and back, a multi-line paste, the end of a
turn. After every step, and after every flush before the next frame, it reads
the VT screen back and asserts:

(a) the composer's first screen row changes only when the composer's own row
    count changes, and then by exactly that count, because the region's
    bottom never moves;
(b) the hardware cursor sits on the composer caret;
(c) the status line is the region's last row and sits on the screen's last
    row, with the region's rows exactly the frame's rows above it;
(d) the shell history and every line flushed so far are above the region,
    complete and in order.

Sampling: heights 10, 11, 12, 13, 16, 20, 24, 31, 40, 57, 80 (the ends of the
10..80 range, the small heights where the budget caps bind, and a spread in
between) x widths 40, 80, 120, with the launch cursor on the screen's last
row. Launch rows 1 and mid-screen run for heights 10, 24, 80 at every width.
A launch from row 0 has its own test: there the composer moves down once by
one row and never up.
"""

from __future__ import annotations

from collections.abc import Sequence
import unittest

from _tui_lines import line_text
from _vt_screen import VTScreen

from omh.tui.composer import PROMPT, SUBMIT, Composer
from omh.tui.frame import build_frame, sticky_line
from omh.tui.keys import BACKSPACE, CHAR, ENTER, ESCAPE, Key, Paste
from omh.tui.layout import composer_cap, slot_cap
from omh.tui.screen import LiveRegion
from omh.tui.style import Line, Span
from omh.tui.theme import DEFAULT_THEME
from omh.tui.width import cell_width, truncate_line

HEIGHTS = (10, 11, 12, 13, 16, 20, 24, 31, 40, 57, 80)
WIDTHS = (40, 80, 120)
LAUNCH_ROW_HEIGHTS = (10, 24, 80)
STATUS_PREFIX = "⚚ OMH v"


def text_of(line: Line) -> str:
    return line_text(line).rstrip(" ")


def plain(text: str) -> Line:
    return (Span(text),)


class Session:
    """One terminal, one live region, the app loop's repaint order."""

    def __init__(self, case: unittest.TestCase, rows: int, cols: int, history: int) -> None:
        self.case = case
        self.rows = rows
        self.cols = cols
        self.screen = VTScreen(rows, cols)
        self.shell = [f"shell {index}" for index in range(history)]
        for line in self.shell:
            self.screen.feed(line + "\r\n")
        self.region = LiveRegion(self.screen.feed)
        self.composer = Composer()
        self.sticky: Line | None = None
        self.tail: list[Line] = []
        self.slot: list[Line] = []
        self.flushed: list[str] = []
        self.flush_pads: list[tuple[int, int]] = []  # (pad rows, lines flushed) at each flush
        self.lines: tuple[Line, ...] = ()
        self.view: tuple[list[Line], tuple[int, int]] = ([], (0, 0))  # the composer as last drawn
        self.last: tuple[int, int, int] | None = None  # status row, composer top, composer rows
        self.status = truncate_line((Span(f"{STATUS_PREFIX}9.9.9 │ test-model │ ctx 12% │ idle", DEFAULT_THEME.status),), cols)
        self.pinned = True  # the bottom sits on the screen's last row
        self.bottom_moves = 0
        self.label = ""

    # -- the app loop ---------------------------------------------------------

    def paint(self, label: str, flush: Sequence[str] = ()) -> None:
        self.label = label
        if flush:
            pad = self.region.pad
            consumed = min(len(flush), pad)
            self.flush_pads.append((pad, len(flush)))
            self.region.flush_above([plain(text) for text in flush], self.cols)
            self.flushed.extend(flush)
            self.label = f"{label} (after flush, before the frame)"
            self.check(self.lines[consumed:], self.view)
            self.label = label
        frame = build_frame(
            cols=self.cols,
            rows=self.rows,
            composer=self.composer,
            status=self.status,
            previous_height=self.region.height,
            sticky=self.sticky,
            tail=self.tail,
            slot=self.slot,
        )
        self.region.render(frame.lines, frame.cursor, self.cols, pad=frame.pad)
        self.lines = frame.lines
        self.view = self.composer.render(self.cols, composer_cap(self.rows), PROMPT, DEFAULT_THEME.prompt)
        self.check(frame.lines, self.view)
        # the caret claim itself, counted without the composer: one row of text, caret at its end
        text = self.composer.text
        if text and "\n" not in text and self.composer.caret == len(text) and cell_width(PROMPT + text) < self.cols:
            self.case.assertEqual(self.screen.cursor[1], cell_width(PROMPT + text), f"{label}: cursor column")

    def keys(self, label: str, events: Sequence[Key | Paste]) -> None:
        for event in events:
            self.composer.handle(event)
        self.paint(label)

    # -- the invariants -------------------------------------------------------

    def check(self, expected: Sequence[Line], view: tuple[list[Line], tuple[int, int]]) -> None:
        t = self.case
        where = f"{self.rows}x{self.cols} after {self.label!r}"
        screen = self.screen.lines()
        status_rows = [row for row, text in enumerate(screen) if text.startswith(STATUS_PREFIX)]
        t.assertEqual(len(status_rows), 1, f"{where}: status line rows {status_rows}\n" + "\n".join(screen))
        status_row = status_rows[0]
        height = self.region.height
        top = status_row - height + 1
        # (c) the status line is the region's last row; the region holds exactly the frame's rows
        t.assertEqual(height, len(expected), where)
        t.assertGreaterEqual(top, 1 if self.pinned else 0, f"{where}: region taller than rows - 1")
        t.assertEqual(screen[top : status_row + 1], [text_of(line) for line in expected], where)
        t.assertTrue(all(not text for text in screen[status_row + 1 :]), f"{where}: rows below the status line")
        if self.pinned:
            t.assertEqual(status_row, self.rows - 1, f"{where}: status line left the screen's last row")
        # the composer sits directly on the status line
        lines, (caret_row, caret_col) = view
        composer_top = status_row - len(lines)
        t.assertEqual(screen[composer_top:status_row], [text_of(line) for line in lines], where)
        # (a) the composer's first row moves only with its own row count
        if self.last is not None:
            last_status, last_top, last_rows = self.last
            if status_row != last_status:
                self.bottom_moves += 1
                t.assertFalse(self.pinned, f"{where}: the region's bottom moved {last_status} -> {status_row}")
                t.assertEqual(status_row, last_status + 1, f"{where}: the bottom moved by more than one row")
                t.assertEqual(self.bottom_moves, 1, f"{where}: the bottom moved twice")
            else:
                t.assertEqual(composer_top - last_top, last_rows - len(lines), f"{where}: the composer row moved")
        self.last = (status_row, composer_top, len(lines))
        # (b) the hardware cursor is on the caret
        t.assertEqual(self.screen.cursor, (composer_top + caret_row, caret_col), where)
        t.assertTrue(self.screen.cursor_visible, where)
        # (d) everything printed before the region is intact, in order
        t.assertEqual(self.screen.scrollback + screen[:top], self.shell + self.flushed, where)


def typed(text: str) -> list[Key | Paste]:
    return [Key(CHAR, ch) for ch in text]


def run_scenario(session: Session) -> None:
    rows = session.rows
    counter = iter(range(1_000_000))
    session.paint("first frame")

    session.keys("typing", typed("hello "))
    session.keys("typing Hangul", typed("한글 입력"))
    action = session.composer.handle(Key(ENTER))
    assert action.kind == SUBMIT
    session.sticky = sticky_line(action.text, session.cols)
    session.paint("submit flushes the user line", flush=[f"you: {action.text}"])

    # the streaming tail grows past the screen while stable lines flush
    for index in range(rows + 6):
        session.tail.append(plain(f"stream {next(counter)}"))
        if index % 3 == 2:
            session.paint("streaming tail grows")
        if index % 7 == 6:
            stable = [text_of(line) for line in session.tail[:5]]
            session.tail = session.tail[5:]
            session.paint("stable lines flush mid-stream", flush=stable)

    # a panel grows past its cap, shrinks, closes
    for size in range(1, slot_cap(rows, session.cols) + 5, 2):
        session.slot = [plain(f"panel row {n}") for n in range(size)]
        session.paint("panel grows")
    session.slot = session.slot[:2]
    session.paint("panel shrinks")
    session.slot = []
    session.paint("panel closes")

    # a server-request prompt takes the slot, then closes
    session.slot = [plain("approve: rm -rf build?"), plain("  1 once"), plain("  2 session"), plain("  3 deny")]
    session.paint("prompt slot opens")
    session.slot = []
    session.paint("prompt slot closes")

    # a completion menu under a slash
    session.keys("slash typed", typed("/"))
    session.slot = [plain(f"/command-{n}") for n in range(6)]
    session.paint("completion menu appears")
    session.composer.handle(Key(ESCAPE))
    session.slot = []
    session.keys("completion menu closes", [Key(BACKSPACE)])

    # the composer grows to its cap and beyond (its window scrolls), then shrinks back
    for line_number in range(composer_cap(rows) + 3):
        session.keys("composer grows", [Key(ENTER, alt=True), *typed(f"line {line_number}")])
    while session.composer.text:
        session.keys("composer shrinks", [Key(BACKSPACE)] * 3)
    session.keys("multi-line paste", [Paste("first\nsecond\nthird\nfourth\nfifth")])
    session.keys("Ctrl+C clears", [Key(CHAR, "c", ctrl=True)])

    # the turn ends: the tail flushes, the sticky prompt goes
    stable = [text_of(line) for line in session.tail]
    session.tail = []
    session.sticky = None
    session.paint("turn complete flushes the tail", flush=stable)
    session.paint("a big flush", flush=[f"history line {next(counter)}" for _ in range(rows + 5)])

    # a second turn with a panel, a shrink, and a small flush into the pad rows
    session.sticky = sticky_line("second prompt", session.cols)
    session.tail = [plain(f"stream {next(counter)}") for _ in range(4)]
    session.slot = [plain(f"agent {n}") for n in range(slot_cap(rows, session.cols))]
    session.paint("second turn with a panel")
    session.slot = []
    session.tail = []
    session.paint("panel and tail gone")
    session.paint("small flush into the pad", flush=["done 1", "done 2"])
    session.paint("idle repaint")


class ComposerStaysPutTests(unittest.TestCase):
    def run_one(self, rows: int, cols: int, history: int) -> Session:
        session = Session(self, rows, cols, history)
        run_scenario(session)
        return session

    def test_composer_row_is_fixed_at_every_sampled_size(self) -> None:
        paths = set()
        for rows in HEIGHTS:
            for cols in WIDTHS:
                with self.subTest(rows=rows, cols=cols):
                    session = self.run_one(rows, cols, history=rows + 3)
                    into_pad = [pad >= count for pad, count in session.flush_pads]
                    self.assertIn(True, into_pad, "no flush landed entirely in pad rows")
                    self.assertIn(False, into_pad, "every flush landed in pad rows")
                    for pad, count in session.flush_pads:
                        paths.add("pad only" if pad >= count else "no pad" if pad == 0 else "pad then push")
        self.assertEqual(paths, {"pad only", "no pad", "pad then push"})

    def test_composer_row_is_fixed_from_other_launch_rows(self) -> None:
        for rows in LAUNCH_ROW_HEIGHTS:
            for cols in WIDTHS:
                for history in (1, rows // 2):
                    with self.subTest(rows=rows, cols=cols, launch_row=history):
                        self.run_one(rows, cols, history)

    def test_a_launch_from_the_top_row_moves_the_composer_down_once_and_never_up(self) -> None:
        for rows in (10, 24):
            with self.subTest(rows=rows):
                session = Session(self, rows, 80, history=0)
                session.pinned = False
                run_scenario(session)
                self.assertEqual(session.bottom_moves, 1)
                self.assertEqual(session.last[0], rows - 1)


if __name__ == "__main__":
    unittest.main()
