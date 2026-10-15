"""The composer stays put: `build_frame` + `LiveRegion` driven into a VT screen model.

The owner's top complaint about the Hermes-hosted TUI is that the prompt
"goes too far up": rows added around the input push the composer upward.
This file is the regression proof that the OMH TUI's composer does not move.
It runs the frame helper and the live region the way the app loop does
(`App._render`: build a frame from the region's current height and render it,
then flush the stable lines with `flush_above` into its pad rows) through
event sequences: a streaming tail that grows past the screen, stable lines
flushed with and without pad rows, a panel slot that grows and shrinks, a
server-request prompt slot, a completion menu, a composer that grows to its
maximum and back, a multi-line paste, the end of a turn, the input mode
switching between normal, accept and plan (each redraws both composer rules
and the mode row), and the agent list taking the keys, growing past its cap
and giving them back. After every frame, and again after its flush, it reads
the VT screen back and asserts:

(a) the composer's first screen row changes only when the composer's own row
    count changes, and then by exactly that count, because the region's
    bottom never moves and the rows under the composer are fixed for the
    terminal size. The one exception is the focused agent list: while it is
    drawn the composer sits higher by the rows the list adds to the mode
    row's one, and the frame after it leaves has the composer back on the
    row it was on before. The list never makes the region taller, so the
    bottom does not move for it, not even from the launch row 0;
(b) the hardware cursor sits on the composer caret;
(c) the status line is the region's last row and sits on the screen's last
    row, with the region's rows exactly the frame's rows above it;
(d) the shell history and every line flushed so far are above the region,
    complete and in order;
(e) between the composer and the status line sit exactly the bottom rule (in
    the mode's glyph) and the mode row, where the height has room for them:
    both from 10 rows, the bottom rule alone from 7, neither below that.
    While the agent list is focused its rows are there instead of the mode
    row, as many as it has up to what the region's height leaves under the
    composer and the cap, and only where the mode row would be;
(f) whenever the live region says which screen row it ends on
    (`LiveRegion.last_row`), that is the row the status line is on, and by
    the end of every scenario it does say;
(g) a flush of more lines than the frame has pad rows (one that scrolls the
    screen) leaves the region with no pad rows, so no blank row stays between
    the lines just printed and the region.

Sampling: heights 6, 7, 9, 10, 11, 12, 13, 16, 20, 24, 31, 40, 57, 80 (the
heights where the mode row and the bottom rule drop out, the small heights
where the budget caps bind, and a spread up to 80) x widths 40, 80, 120, with
the launch cursor on the screen's last row. Launch rows 1 and mid-screen run
for heights 10, 24, 80 at every width. Each mode also holds for a whole
scenario at 10 and 24 rows. A launch from row 0 has its own test: there the
composer moves down once by one row and never up.
"""

from __future__ import annotations

from collections.abc import Sequence
import unittest

from _tui_lines import line_text
from _vt_screen import VTScreen

from omh.tui.app import mode_row_line
from omh.tui.composer import PROMPT, SUBMIT, Composer
from omh.tui.frame import MODE_ACCEPT, MODE_NORMAL, MODE_PLAN, build_frame, sticky_line
from omh.tui.keys import BACKSPACE, CHAR, ENTER, ESCAPE, Key, Paste
from omh.tui.layout import composer_cap, slot_cap
from omh.tui.screen import LiveRegion
from omh.tui.style import Line, Span
from omh.tui.theme import DEFAULT_THEME
from omh.tui.width import cell_width, truncate_line

HEIGHTS = (6, 7, 9, 10, 11, 12, 13, 16, 20, 24, 31, 40, 57, 80)
WIDTHS = (40, 80, 120)
LAUNCH_ROW_HEIGHTS = (10, 24, 80)
MODE_HEIGHTS = (10, 24)
STATUS_PREFIX = "⚚ OMH"
RULE_GLYPHS = {MODE_NORMAL: "─", MODE_ACCEPT: "━", MODE_PLAN: "┄"}


def rows_under_composer(rows: int) -> int:
    """The spec, written out: bottom rule from 7 rows, mode row from 10, on a terminal of 20+ columns."""
    return (rows >= 7) + (rows >= 10)


def agent_list_cap(rows: int) -> int:
    """The spec, written out: the focused agent list gets what a region of `rows - 1` rows has left
    after the top rule, a composer at its cap (a quarter of the height), the bottom rule and the
    status line; nothing below 10 rows, where there is no mode row for it to replace."""
    return rows - 1 - rows // 4 - 3 if rows >= 10 else 0


def text_of(line: Line) -> str:
    return line_text(line).rstrip(" ")


def plain(text: str) -> Line:
    return (Span(text),)


class Session:
    """One terminal, one live region, the app loop's repaint order."""

    def __init__(
        self,
        case: unittest.TestCase,
        rows: int,
        cols: int,
        history: int,
        mode: str = MODE_NORMAL,
        switching: bool = True,
        at_top: bool = False,
    ) -> None:
        self.case = case
        self.rows = rows
        self.cols = cols
        self.screen = VTScreen(rows, cols)
        self.shell = [f"shell {index}" for index in range(history)]
        for line in self.shell:
            self.screen.feed(line + "\r\n")
        self.region = LiveRegion(self.screen.feed, at_top=at_top)  # at_top: told its first frame starts on row 0
        self.known_rows: list[int | None] = []  # what the region said its last row was, at every check
        self.composer = Composer()
        self.sticky: Line | None = None
        self.tail: list[Line] = []
        self.slot: list[Line] = []
        self.agent_list: list[Line] = []  # the focused agent list's rows; empty while it has no focus
        self.flushed: list[str] = []
        self.flush_pads: list[tuple[int, int]] = []  # (pad rows, lines flushed) at each flush
        self.lines: tuple[Line, ...] = ()
        self.view: tuple[list[Line], tuple[int, int]] = ([], (0, 0))  # the composer as last drawn
        self.last: tuple[int, int, int] | None = None  # status row, composer top, composer rows
        self.last_under = rows_under_composer(rows)  # rows between the composer and the status line, as last drawn
        self.drawn_list: list[Line] = []  # the agent list rows of the frame on screen
        self.status = truncate_line((Span(f"{STATUS_PREFIX} │ test-model │ ctx 12% │ idle", DEFAULT_THEME.status),), cols)
        self.mode = mode
        self.switching = switching  # whether the scenario's mode switches apply
        self.modes_seen: set[str] = set()
        self.pinned = True  # the bottom sits on the screen's last row
        self.bottom_moves = 0
        self.label = ""

    # -- the app loop ---------------------------------------------------------

    def paint(self, label: str, flush: Sequence[str] = ()) -> None:
        self.label = label
        self.modes_seen.add(self.mode)
        mode_row = mode_row_line(
            mode=self.mode,
            busy=self.sticky is not None,
            queued=0,
            keyboard_protocol=False,
            effort="high",
            width=self.cols,
            theme=DEFAULT_THEME,
        )
        frame = build_frame(
            cols=self.cols,
            rows=self.rows,
            composer=self.composer,
            status=self.status,
            previous_height=self.region.height,
            sticky=self.sticky,
            tail=self.tail,
            slot=self.slot,
            mode=self.mode,
            mode_row=mode_row,
            agent_list=(lambda room: self.agent_list) if self.agent_list else None,
        )
        self.region.render(frame.lines, frame.cursor, self.cols, pad=frame.pad)
        self.lines = frame.lines
        self.view = self.composer.render(self.cols, composer_cap(self.rows), PROMPT, DEFAULT_THEME.prompt)
        self.drawn_list = self.agent_list[: self.list_rows(len(self.view[0]))]
        self.check(frame.lines, self.view, self.drawn_list)
        # the caret claim itself, counted without the composer: one row of text, caret at its end
        text = self.composer.text
        if text and "\n" not in text and self.composer.caret == len(text) and cell_width(PROMPT + text) < self.cols:
            self.case.assertEqual(self.screen.cursor[1], cell_width(PROMPT + text), f"{label}: cursor column")
        if flush:
            pad = self.region.pad
            consumed = min(len(flush), pad)
            self.flush_pads.append((pad, len(flush)))
            self.region.flush_above([plain(line) for line in flush], self.cols)
            self.flushed.extend(flush)
            self.lines = frame.lines[consumed:]
            self.label = f"{label} (after its flush)"
            self.check(self.lines, self.view, self.drawn_list)
            if len(flush) > pad:  # (g) the flush scrolled the screen: no pad row is left under its lines
                self.case.assertEqual(self.region.pad, 0, f"{self.rows}x{self.cols} after {self.label!r}: pad rows")
            self.label = label

    def list_rows(self, composer_rows: int) -> int:
        """The spec, written out: the rows of the list a region this tall shows under its composer.

        Everything the region holds but its top rule, composer, bottom rule
        and status line, up to the cap; the region is the height it would be
        without the list, which the list phases assert apart.
        """
        if not self.agent_list:
            return 0
        return min(len(self.agent_list), agent_list_cap(self.rows), self.region.height - composer_rows - 3)

    def keys(self, label: str, events: Sequence[Key | Paste]) -> None:
        for event in events:
            self.composer.handle(event)
        self.paint(label)

    def switch(self, mode: str) -> None:
        """Change the input mode; nothing else changes, so only the rules and the mode row repaint."""
        if self.switching:
            self.mode = mode
            self.paint(f"mode switches to {mode}")

    # -- the invariants -------------------------------------------------------

    def check(
        self, expected: Sequence[Line], view: tuple[list[Line], tuple[int, int]], agent_list: Sequence[Line]
    ) -> None:
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
        # (f) where the region says its last row is, the status line is
        known = self.region.last_row(self.rows)
        self.known_rows.append(known)
        if known is not None:
            t.assertEqual(known, status_row, f"{where}: the region's last row")
        # (e) the composer sits on the bottom rule and the mode row, or as many of them as the height keeps;
        # the focused agent list stands where the mode row would
        lines, (caret_row, caret_col) = view
        under = rows_under_composer(self.rows)
        listed = len(agent_list)
        if listed:
            under += listed - 1
            t.assertEqual(
                screen[status_row - listed : status_row], [text_of(line) for line in agent_list[:listed]], where
            )
        composer_top = status_row - under - len(lines)
        t.assertEqual(screen[composer_top : status_row - under], [text_of(line) for line in lines], where)
        rule = RULE_GLYPHS[self.mode] * self.cols
        t.assertEqual(screen[composer_top - 1], rule, f"{where}: the top rule")
        if under:
            t.assertEqual(screen[status_row - under], rule, f"{where}: the bottom rule")
        if under == 2 and not listed:
            t.assertEqual(screen[status_row - 1], text_of(expected[-2]), f"{where}: the mode row")
            t.assertTrue(screen[status_row - 1], f"{where}: the mode row is empty")
        # (a) the composer's first row moves only with its own row count
        if self.last is not None:
            last_status, last_top, last_rows = self.last
            if status_row != last_status:
                self.bottom_moves += 1
                t.assertFalse(self.pinned, f"{where}: the region's bottom moved {last_status} -> {status_row}")
                t.assertEqual(status_row, last_status + 1, f"{where}: the bottom moved by more than one row")
                t.assertEqual(self.bottom_moves, 1, f"{where}: the bottom moved twice")
            else:
                # the composer's own rows, and the rows the agent list adds or gives back, and nothing else
                moved = (last_rows - len(lines)) + (self.last_under - under)
                t.assertEqual(composer_top - last_top, moved, f"{where}: the composer row moved")
        if not listed:
            # with no list focused the composer's last row is a fixed distance above the status line
            t.assertEqual(composer_top + len(lines), status_row - rows_under_composer(self.rows), where)
        self.last = (status_row, composer_top, len(lines))
        self.last_under = under
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

    # a panel grows past its cap, shrinks, closes; the mode switches while it is open
    for size in range(1, slot_cap(rows, session.cols) + 5, 2):
        session.slot = [plain(f"panel row {n}") for n in range(size)]
        session.paint("panel grows")
    session.switch(MODE_ACCEPT)
    session.slot = session.slot[:2]
    session.paint("panel shrinks")
    session.slot = []
    session.paint("panel closes")

    # a server-request prompt takes the slot, then closes
    session.slot = [plain("approve: rm -rf build?"), plain("  1 once"), plain("  2 session"), plain("  3 deny")]
    session.paint("prompt slot opens")
    session.switch(MODE_PLAN)
    session.slot = []
    session.paint("prompt slot closes")

    # the agent list takes the keys, gains agents past its cap, loses some, and gives the keys back:
    # the composer rises by the rows the list adds and is back on its row the moment the list is gone
    resting, resting_height = session.last, session.region.height
    for size in (3, 2, 8, rows + 4, 5):
        session.agent_list = [plain(f"agent row {n}") for n in range(size - 1)] + [plain("↑↓ select")]
        session.paint("the agent list is focused")
        assert session.last is not None and resting is not None
        session.case.assertEqual(session.region.height, resting_height, f"{rows} rows: the list grew the region")
        lifted = len(session.drawn_list)
        session.case.assertEqual(session.last[1], resting[1] - max(lifted - 1, 0), f"{rows} rows, list of {size}")
    session.agent_list = []
    session.paint("the agent list gives the keys back")
    session.case.assertEqual(session.last, resting, f"{rows} rows: the composer did not come back to its row")
    session.case.assertEqual(session.region.height, resting_height, f"{rows} rows: rows left behind by the list")

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
    session.switch(MODE_ACCEPT)
    while session.composer.text:
        session.keys("composer shrinks", [Key(BACKSPACE)] * 3)
    session.keys("multi-line paste", [Paste("first\nsecond\nthird\nfourth\nfifth")])
    session.keys("Ctrl+C clears", [Key(CHAR, "c", ctrl=True)])

    # the turn ends: the tail flushes, the sticky prompt goes
    stable = [text_of(line) for line in session.tail]
    session.tail = []
    session.sticky = None
    session.paint("turn complete flushes the tail", flush=stable)
    session.switch(MODE_NORMAL)
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
    # (f) by now growth and flushes have carried the region to the screen's last row, and it knows
    session.case.assertEqual(session.region.last_row(rows), rows - 1, f"{rows} rows: the region's last row")


class ComposerStaysPutTests(unittest.TestCase):
    def run_one(self, rows: int, cols: int, history: int) -> Session:
        session = Session(self, rows, cols, history)
        run_scenario(session)
        self.assertEqual(session.modes_seen, {MODE_NORMAL, MODE_ACCEPT, MODE_PLAN})
        return session

    def test_each_mode_holds_for_a_whole_scenario(self) -> None:
        for mode in (MODE_NORMAL, MODE_ACCEPT, MODE_PLAN):
            for rows in MODE_HEIGHTS:
                with self.subTest(mode=mode, rows=rows):
                    session = Session(self, rows, 80, history=rows + 3, mode=mode, switching=False)
                    run_scenario(session)
                    self.assertEqual(session.modes_seen, {mode})

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
                # Not told where it started, the region cannot say where it ends until it has been pushed.
                self.assertIsNone(session.known_rows[0])

    def test_a_region_told_it_starts_on_the_top_row_knows_its_last_row_from_the_first_frame(self) -> None:
        # What the app's own full redraw gives it: the screen cleared and the cursor homed.
        for rows in (10, 24):
            with self.subTest(rows=rows):
                session = Session(self, rows, 80, history=0, at_top=True)
                session.pinned = False
                run_scenario(session)
                self.assertNotIn(None, session.known_rows)
                self.assertEqual(session.known_rows[0], rows - 2)  # one row above the screen's last
                self.assertEqual(sorted(set(session.known_rows)), [rows - 2, rows - 1])

    def test_a_region_launched_lower_down_learns_its_last_row_with_its_first_push(self) -> None:
        session = Session(self, 24, 80, history=5)
        run_scenario(session)
        self.assertIsNone(session.known_rows[0])
        self.assertEqual(set(session.known_rows), {None, 23})

    def test_below_ten_rows_the_agent_list_is_not_drawn_and_moves_nothing(self) -> None:
        for rows in (6, 7, 9):
            with self.subTest(rows=rows):
                session = Session(self, rows, 80, history=rows + 3)
                session.paint("first frame")
                resting = session.last
                session.agent_list = [plain("agent row 0"), plain("agent row 1"), plain("↑↓ select")]
                session.paint("the agent list asks for rows")
                self.assertEqual(session.last, resting)
                self.assertNotIn("agent row 0", session.screen.lines())

    def test_the_agent_list_lifts_the_composer_by_its_extra_rows_and_puts_it_back(self) -> None:
        for rows, sizes in ((10, (2, 4, 9)), (24, (2, 8, 14, 30))):
            session = Session(self, rows, 80, history=rows + 3)
            session.paint("first frame")
            resting = session.last
            assert resting is not None
            for size in sizes:
                with self.subTest(rows=rows, size=size):
                    session.agent_list = [plain(f"agent row {n}") for n in range(size)]
                    session.paint("the agent list is focused")
                    assert session.last is not None
                    drawn = min(size, agent_list_cap(rows))
                    self.assertEqual(session.last[1], resting[1] - (drawn - 1))
                    self.assertEqual(session.last[0], resting[0])  # the status line never moved
                    session.agent_list = []
                    session.paint("the agent list gives the keys back")
                    self.assertEqual(session.last, resting)
        self.assertEqual([agent_list_cap(rows) for rows in (9, 10, 11, 13, 16, 24)], [0, 4, 5, 6, 8, 14])

    def focus_and_leave(self, session: Session, size: int) -> tuple[list[str], list[str], int]:
        """Focus a list of `size` rows, then leave it; the screen while it was shown and after, and its rows drawn."""
        resting, height, before = session.last, session.region.height, session.screen.lines()
        session.agent_list = [plain(f"agent row {n}") for n in range(size - 1)] + [plain("↑↓ select")]
        session.paint("the agent list is focused")
        during, drawn = session.screen.lines(), len(session.drawn_list)
        self.assertEqual(session.region.height, height)  # the list never grew the region
        assert session.last is not None and resting is not None
        self.assertEqual(session.last[0], resting[0])  # so the status line, the region's last row, stayed
        session.agent_list = []
        session.paint("the agent list gives the keys back")
        self.assertEqual(session.last, resting)
        self.assertEqual(session.screen.lines(), before)  # nothing scrolled, and no blank row was left behind
        return during, before, drawn

    def test_with_the_pad_used_up_the_list_takes_the_tails_rows_and_scrolls_nothing(self) -> None:
        session = Session(self, 24, 80, history=27)
        session.paint("first frame")
        session.paint("the pad used up", flush=[f"history line {n}" for n in range(30)])
        self.assertEqual(session.region.pad, 0)
        session.sticky = sticky_line("a prompt", 80)
        session.tail = [plain(f"stream {n}") for n in range(6)]
        session.paint("a turn streaming")
        during, before, drawn = self.focus_and_leave(session, 5)
        top = 24 - session.region.height
        self.assertEqual(during[:top], before[:top])  # the transcript above the region did not move
        self.assertEqual(drawn, 5)
        self.assertNotIn("stream 3", during)  # the tail's oldest rows gave the list its four; the newest stay
        self.assertEqual(during[top : top + 3], ["↳ you: a prompt", "stream 4", "stream 5"])
        _during, _before, drawn = self.focus_and_leave(session, 12)
        self.assertEqual(drawn, 8)  # the sticky prompt and the whole tail give their rows, and no more are taken

    def test_with_no_pad_and_nothing_above_the_rule_the_list_scrolls_in_the_mode_rows_place(self) -> None:
        session = Session(self, 24, 80, history=27)
        session.paint("first frame")
        session.paint("the pad used up", flush=[f"history line {n}" for n in range(30)])
        self.assertEqual(session.region.pad, 0)
        during, _before, drawn = self.focus_and_leave(session, 8)
        self.assertEqual(drawn, 1)  # the region holds one row under the composer
        self.assertIn("agent row 0", during)

    def test_from_the_launch_row_0_the_list_does_not_move_the_regions_last_row(self) -> None:
        # What Ctrl+L leaves: the region ends one row above the screen's last, and the list must not push it down.
        session = Session(self, 24, 80, history=0, at_top=True)
        session.pinned = False
        session.paint("first frame")
        session.paint("a few notices", flush=[f"notice {n}" for n in range(16)])
        self.assertEqual((session.region.pad, session.region.last_row(24)), (2, 22))
        _during, _before, drawn = self.focus_and_leave(session, 4)
        self.assertEqual(session.region.last_row(24), 22)
        self.assertEqual(session.bottom_moves, 0)
        self.assertEqual(drawn, 3)  # the two pad rows and the mode row's


if __name__ == "__main__":
    unittest.main()
