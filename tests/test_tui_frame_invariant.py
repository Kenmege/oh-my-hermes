"""The region follows the content until the screen is full, then stays on the bottom rows.

`build_frame` + `LiveRegion` driven into a VT screen model. The region (sticky
prompt, streaming tail, slot, composer, mode row, status line) is drawn right
under the output and is as tall as its content, the way Claude Code's input
box follows what was printed: no blank rows between the transcript and the
composer at the start, after a turn or around a question. Once its last row is
the screen's last row (and the region knows it), the frame keeps the region's
height, so what shrinks above the composer becomes blank pad rows at the
region's top and the composer stays on the bottom rows (the owner's complaint
about Hermes' Ink TUI: a closing list must not pull the prompt up). This file
is the regression proof. It runs the frame helper and the live region the way
the app loop does (`App._render`: build a frame, with the region's height as
`min_height` once it is on the screen's last row, then render it with the
stable lines flushed above it in the same write) through
event sequences: a streaming tail that grows past the screen while stable
lines flush, a panel slot that grows and shrinks, a server-request prompt
slot, a completion menu, a composer that grows to its maximum and back, a
multi-line paste, the end of a turn, the input mode switching between normal,
accept and plan (each redraws both composer rules and the mode row). After
every frame it reads the VT screen back and asserts:

(a) the shell history and every line flushed so far are exactly what lies
    above the region, complete and in order, so the only blank rows between
    the last line printed and the composer are the region's own (pad rows on
    the bottom rows, the spacer a block opens with);
(b) the hardware cursor sits on the composer caret;
(c) the status line is the region's last row, the region's rows are exactly
    the frame's rows above it, and every row under the status line is blank;
(d) a frame that scrolled the screen leaves the status line on the screen's
    last row: the screen scrolls only once the region reaches its end, so
    together with (a) and (c) the region's place is fixed by the content alone;
(e) between the composer and the status line sit exactly the bottom rule (in
    the mode's glyph) and the mode row, where the height has room for them:
    both from 10 rows, the bottom rule alone from 7, neither below that;
(f) whenever the live region says which screen row it ends on
    (`LiveRegion.last_row`), that is the row the status line is on, and by
    the end of every scenario it does say;
(g) a frame built with the region on the screen's last row leaves it there,
    and moves the composer's first row only by its own row count.

A question answered by picking, and so a picker or the agent list, hides the
composer (`composer=None`): those frames have no rule and no caret, the slot's
rows stand right above the mode row (or the status line), and the frame after
the question has the composer drawn under the output again, with nothing of
the question left on the screen.

Sampling: heights 6, 7, 9, 10, 11, 12, 13, 16, 20, 24, 31, 40, 57, 80 (the
heights where the mode row and the bottom rule drop out, the small heights
where the budget caps bind, and a spread up to 80) x widths 40, 80, 120, with
the launch cursor on the screen's last row. Launch rows 0, 1 and mid-screen
run for heights 10, 24, 80 at every width. Each mode also holds for a whole
scenario at 10 and 24 rows.
"""

from __future__ import annotations

from collections.abc import Sequence
import re
import unittest

from _tui_lines import line_text
from _vt_screen import VTScreen

from omh.tui.app import mode_row_line
from omh.tui.composer import PROMPT, SUBMIT, Composer
from omh.tui.frame import MODE_ACCEPT, MODE_NORMAL, MODE_PLAN, build_frame, sticky_line
from omh.tui.keys import BACKSPACE, CHAR, ENTER, ESCAPE, Key, Paste
from omh.tui.layout import choice_cap, composer_cap, slot_cap
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
        self.region = LiveRegion(self.screen.feed, at_top=at_top)  # at_top: told it starts on row 0
        self.known_rows: list[int | None] = []  # what the region said its last row was, at every check
        self.composer = Composer()
        self.sticky: Line | None = None
        self.tail: list[Line] = []
        self.slot: list[Line] = []
        self.flushed: list[str] = []
        self.lines: tuple[Line, ...] = ()
        self.view: tuple[list[Line], tuple[int, int]] = ([], (0, 0))  # the composer as last drawn
        self.last: tuple[int, int, int] | None = None  # status row, composer top, composer rows
        self.status = truncate_line((Span(f"{STATUS_PREFIX} │ test-model │ ctx 12% │ idle", DEFAULT_THEME.status),), cols)
        self.mode = mode
        self.switching = switching  # whether the scenario's mode switches apply
        self.modes_seen: set[str] = set()
        self.scrolled = 0  # rows the screen has scrolled since the region was made
        self.heights: list[int] = []  # the region's height at every check
        self.pinned = False  # the frame being checked was built with the region's last row on the screen's last row
        self.pinned_frames = 0  # how many frames so far were
        self.label = ""

    # -- the app loop ---------------------------------------------------------

    def min_height(self, flush: Sequence[str]) -> int:
        """What the app passes: the region's height, less the lines flushed over it, once its last row is the screen's."""
        self.pinned = self.region.last_row(self.rows) == self.rows - 1
        self.pinned_frames += self.pinned
        return max(self.region.height - len(flush), 0) if self.pinned else 0

    def render(self, frame_lines: Sequence[Line], cursor: tuple[int, int] | None, flush: Sequence[str]) -> int:
        """Render as the app does, the flush first in the same write; the rows the screen scrolled for it."""
        before = len(self.screen.scrollback)
        self.region.render(frame_lines, cursor, self.cols, flush=[plain(line) for line in flush], screen_rows=self.rows)
        self.flushed.extend(flush)
        scrolled = len(self.screen.scrollback) - before
        self.scrolled += scrolled
        return scrolled

    def paint_hidden(self, label: str, flush: Sequence[str] = ()) -> None:
        """A frame with the composer hidden: the slot stands in its place, and no caret is shown."""
        self.label = label
        frame = build_frame(
            cols=self.cols,
            rows=self.rows,
            composer=None,
            status=self.status,
            sticky=self.sticky,
            tail=self.tail,
            slot=self.slot,
            mode=self.mode,
            mode_row=self.mode_row(),
            min_height=self.min_height(flush),
        )
        self.case.assertIsNone(frame.cursor)
        scrolled = self.render(frame.lines, frame.cursor, flush)
        self.check_hidden(frame.lines, frame.budget.slot, scrolled)

    def check_hidden(self, expected: Sequence[Line], slot_rows: int, scrolled: int) -> None:
        t = self.case
        where = f"{self.rows}x{self.cols} after {self.label!r}"
        status_row, top = self.check_region(expected, scrolled, where)
        screen = self.screen.lines()
        rules = {glyph * self.cols for glyph in RULE_GLYPHS.values()}
        t.assertFalse([row for row in screen[top:status_row] if row in rules], f"{where}: a composer rule is drawn")
        mode_rows = int(self.rows >= 10)
        drawn = [text_of(line) for line in self.slot[:slot_rows]]
        t.assertEqual(screen[status_row - mode_rows - slot_rows : status_row - mode_rows], drawn, f"{where}: the slot rows")
        t.assertFalse(self.screen.cursor_visible, f"{where}: the caret is shown")

    def mode_row(self) -> Line:
        return mode_row_line(
            mode=self.mode,
            busy=self.sticky is not None,
            queued=0,
            keyboard_protocol=False,
            effort="high",
            width=self.cols,
            theme=DEFAULT_THEME,
        )

    def paint(self, label: str, flush: Sequence[str] = ()) -> None:
        self.label = label
        self.modes_seen.add(self.mode)
        mode_row = self.mode_row()
        frame = build_frame(
            cols=self.cols,
            rows=self.rows,
            composer=self.composer,
            status=self.status,
            sticky=self.sticky,
            tail=self.tail,
            slot=self.slot,
            mode=self.mode,
            mode_row=mode_row,
            min_height=self.min_height(flush),
        )
        scrolled = self.render(frame.lines, frame.cursor, flush)
        self.lines = frame.lines
        self.view = self.composer.render(self.cols, composer_cap(self.rows), PROMPT, DEFAULT_THEME.prompt)
        self.check(frame.lines, self.view, scrolled)
        # the caret claim itself, counted without the composer: one row of text, caret at its end
        text = self.composer.text
        if text and "\n" not in text and self.composer.caret == len(text) and cell_width(PROMPT + text) < self.cols:
            self.case.assertEqual(self.screen.cursor[1], cell_width(PROMPT + text), f"{label}: cursor column")

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

    def check_region(self, expected: Sequence[Line], scrolled: int, where: str) -> tuple[int, int]:
        """(a), (c), (d) and (f), for a frame with the composer or without; (status row, region top)."""
        t = self.case
        screen = self.screen.lines()
        status_rows = [row for row, text in enumerate(screen) if text.startswith(STATUS_PREFIX)]
        t.assertEqual(len(status_rows), 1, f"{where}: status line rows {status_rows}\n" + "\n".join(screen))
        status_row = status_rows[0]
        height = self.region.height
        top = status_row - height + 1
        # (c) the status line is the region's last row; the region holds exactly the frame's rows; blank under it
        self.heights.append(height)
        t.assertEqual(height, len(expected), where)
        t.assertLessEqual(height, self.rows - 1, f"{where}: region taller than rows - 1")
        t.assertEqual(screen[top : status_row + 1], [text_of(line) for line in expected], where)
        t.assertTrue(all(not text for text in screen[status_row + 1 :]), f"{where}: rows below the status line")
        # (a) everything printed before the region is intact, in order, and right above it
        t.assertEqual(self.screen.scrollback + screen[:top], self.shell + self.flushed, where)
        # (d) the screen scrolled only because the region reached its end
        if scrolled:
            t.assertEqual(status_row, self.rows - 1, f"{where}: scrolled {scrolled} rows short of the screen's end")
        # (g) once the region sits on the screen's last row, it stays there whatever shrinks
        if self.pinned:
            t.assertEqual(status_row, self.rows - 1, f"{where}: the region left the screen's last row")
        # (f) where the region says its last row is, the status line is
        known = self.region.last_row(self.rows)
        self.known_rows.append(known)
        if known is not None:
            t.assertEqual(known, status_row, f"{where}: the region's last row")
        return status_row, top

    def check(self, expected: Sequence[Line], view: tuple[list[Line], tuple[int, int]], scrolled: int) -> None:
        t = self.case
        where = f"{self.rows}x{self.cols} after {self.label!r}"
        status_row, _top = self.check_region(expected, scrolled, where)
        screen = self.screen.lines()
        # (e) the composer sits on the bottom rule and the mode row, or as many of them as the height keeps
        lines, (caret_row, caret_col) = view
        under = rows_under_composer(self.rows)
        composer_top = status_row - under - len(lines)
        t.assertEqual(screen[composer_top : status_row - under], [text_of(line) for line in lines], where)
        rule = RULE_GLYPHS[self.mode] * self.cols
        t.assertEqual(screen[composer_top - 1], rule, f"{where}: the top rule")
        if under:
            t.assertEqual(screen[status_row - under], rule, f"{where}: the bottom rule")
        if under == 2:
            t.assertEqual(screen[status_row - 1], text_of(expected[-2]), f"{where}: the mode row")
            t.assertTrue(screen[status_row - 1], f"{where}: the mode row is empty")
        # (g) pinned, the composer's first row moves only with its own row count
        if self.pinned and self.last is not None:
            moved = self.last[2] - len(lines)
            t.assertEqual(composer_top - self.last[1], moved, f"{where}: the composer row moved on the bottom rows")
        self.last = (status_row, composer_top, len(lines))
        # (b) the hardware cursor is on the caret
        t.assertEqual(self.screen.cursor, (composer_top + caret_row, caret_col), where)
        t.assertTrue(self.screen.cursor_visible, where)


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
    session.paint("a small flush", flush=["done 1", "done 2"])
    session.paint("idle repaint")
    # (f) by now the flushes have carried the region as far down as the screen allows, and it knows where it ends
    session.case.assertIsNotNone(session.region.last_row(rows), f"{rows} rows: the region's last row")


class RegionFollowsContentTests(unittest.TestCase):
    def run_one(self, rows: int, cols: int, history: int, at_top: bool = False) -> Session:
        session = Session(self, rows, cols, history, at_top=at_top)
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

    def test_the_region_follows_the_content_at_every_sampled_size(self) -> None:
        for rows in HEIGHTS:
            for cols in WIDTHS:
                with self.subTest(rows=rows, cols=cols):
                    self.run_one(rows, cols, history=rows + 3)

    def test_the_region_follows_the_content_from_other_launch_rows(self) -> None:
        for rows in LAUNCH_ROW_HEIGHTS:
            for cols in WIDTHS:
                for history in (0, 1, rows // 2):
                    with self.subTest(rows=rows, cols=cols, launch_row=history):
                        self.run_one(rows, cols, history)

    def test_a_first_frame_from_mid_screen_sits_right_under_the_shell_and_the_rows_under_it_stay_blank(self) -> None:
        session = Session(self, 24, 80, history=8)
        session.paint("first frame")
        screen = session.screen.lines()
        self.assertEqual(screen[:8], session.shell)
        self.assertEqual(screen[8], RULE_GLYPHS[MODE_NORMAL] * 80)  # the composer's top rule, right under the shell
        self.assertEqual(session.last, (12, 9, 1))  # status line, composer row, composer rows
        self.assertEqual(screen[13:], [""] * 11)
        self.assertEqual(session.screen.scrollback, [])
        self.assertIsNone(session.known_rows[0])  # not told where it started, the region cannot say where it is

    def test_a_region_told_it_starts_on_the_top_row_knows_its_last_row_from_the_first_frame(self) -> None:
        # What the app's own full redraw gives it: the screen cleared and the cursor homed.
        for rows in (10, 24):
            with self.subTest(rows=rows):
                session = self.run_one(rows, 80, history=0, at_top=True)
                self.assertNotIn(None, session.known_rows)
                self.assertEqual(session.known_rows[0], session.heights[0] - 1)

    def test_every_invariant_holds_where_rows_added_inside_the_region_are_inserted(self) -> None:
        # A region whose place is certain inserts the rows a growing tail or slot adds (IL) while it has room
        # under it; the scenario checks (a) to (g) over those frames too.
        for rows in (10, 24, 40):
            for cols in (40, 80):
                with self.subTest(rows=rows, cols=cols):
                    session = Session(self, rows, cols, history=0, at_top=True)
                    feed, inserts = session.region._write, []

                    def spy(text: str, feed=feed, inserts=inserts) -> None:
                        inserts.extend(re.findall(r"\x1b\[\d+L", text))
                        feed(text)

                    session.region._write = spy
                    run_scenario(session)
                    self.assertTrue(inserts)

    def test_a_region_launched_lower_down_learns_its_last_row_once_the_output_has_pushed_it_to_the_end(self) -> None:
        session = Session(self, 24, 80, history=5)
        session.paint("first frame")
        session.paint("a few lines", flush=[f"line {n}" for n in range(10)])
        self.assertEqual(session.known_rows, [None, None])
        session.paint("the output reaches the end", flush=[f"line {n}" for n in range(10, 20)])
        self.assertEqual(session.known_rows[-1], 23)

    def test_a_panel_that_shrinks_takes_the_composer_up_with_it_and_leaves_no_row_behind(self) -> None:
        session = Session(self, 24, 80, history=3)
        session.paint("first frame")
        session.slot = [plain(f"panel row {n}") for n in range(6)]
        session.paint("a panel opens")
        assert session.last is not None
        opened = session.last
        session.slot = session.slot[:2]
        session.paint("the panel shrinks")
        self.assertEqual(session.last[1], opened[1] - 4)  # the composer rose by the four rows the panel gave back
        session.slot = []
        session.paint("the panel closes")
        self.assertEqual(session.last[1], opened[1] - 6)
        self.assertFalse([row for row in session.screen.lines() if row.startswith("panel row")])

    def at_the_bottom(self, rows: int = 24, cols: int = 80) -> Session:
        """A session whose region has reached the screen's last row and knows it: what a redraw and output give."""
        session = Session(self, rows, cols, history=0, at_top=True)
        session.paint("first frame")
        session.paint("output fills the screen", flush=[f"line {n}" for n in range(rows + 4)])
        self.assertEqual(session.region.last_row(rows), rows - 1)
        return session

    def blank_rows_above_the_composer(self, session: Session) -> int:
        """Blank rows between the last line printed and the region's first drawn row (the rule, or the slot)."""
        screen = session.screen.lines()
        top = session.rows - session.region.height
        return next(index for index, row in enumerate(screen[top:]) if row)

    def test_on_the_bottom_rows_whatever_closes_above_the_composer_leaves_it_there_with_the_rows_above_it(self) -> None:
        # The owner's choice for G1: below the screen's last row the composer follows the output; once it is
        # there it stays, and a list or question closing does not pull it up (the old "goes too far up").
        closings = {
            "a panel": lambda session: setattr(session, "slot", [plain(f"panel row {n}") for n in range(6)]),
            "a completion menu": lambda session: setattr(session, "slot", [plain(f"/command-{n}") for n in range(6)]),
        }
        for name, opens in closings.items():
            with self.subTest(closing=name):
                session = self.at_the_bottom()
                resting = session.last
                opens(session)
                session.paint(f"{name} opens")
                session.slot = []
                session.paint(f"{name} closes")
                self.assertEqual(session.last, resting)  # the composer and the status line did not move up
                self.assertEqual(self.blank_rows_above_the_composer(session), 6)  # the freed rows, above it
                session.paint("two lines printed", flush=["a notice", "another"])
                self.assertEqual(session.last, resting)
                self.assertEqual(self.blank_rows_above_the_composer(session), 4)  # the lines filled two of them
                self.assertEqual(session.screen.lines()[23 - 4 - 4 - 2 : 23 - 4 - 4], ["a notice", "another"])

    def test_on_the_bottom_rows_a_question_closing_leaves_the_composer_there(self) -> None:
        session = self.at_the_bottom()
        resting = session.last
        session.slot = [plain(f"option {n}") for n in range(choice_cap(24, 80))]
        session.paint_hidden("a question opens")
        session.slot = []
        session.paint("the question closes")
        self.assertEqual(session.last, resting)
        self.assertGreater(self.blank_rows_above_the_composer(session), 0)

    def test_before_its_place_is_certain_a_full_screen_region_still_follows_the_content(self) -> None:
        # Launched from the screen's last row, the region cannot yet tell that row from one higher up (it never
        # asks the terminal), so until output pushes its place to certainty it follows the content: a panel
        # closing takes the composer up and leaves its rows under the status line.
        session = Session(self, 24, 80, history=27)
        session.paint("first frame")
        self.assertIsNone(session.region.last_row(24))
        assert session.last is not None
        resting, scrolled = session.last, session.scrolled
        session.slot = [plain(f"panel row {n}") for n in range(6)]
        session.paint("a panel opens")
        self.assertEqual(session.scrolled - scrolled, 6)
        session.slot = []
        session.paint("the panel closes")
        self.assertEqual(session.last, (resting[0] - 6, resting[1] - 6, 1))
        self.assertEqual(session.screen.lines()[-6:], [""] * 6)
        session.paint("output makes its place certain", flush=[f"line {n}" for n in range(24)])
        self.assertEqual(session.region.last_row(24), 23)  # pinned from here on

    def test_a_question_answered_by_picking_hides_the_composer_and_then_draws_it_under_the_output(self) -> None:
        for rows in HEIGHTS:
            for cols in WIDTHS:
                with self.subTest(rows=rows, cols=cols):
                    session = Session(self, rows, cols, history=rows // 2)
                    session.paint("first frame")
                    session.keys("a draft", typed("a draft"))
                    draft = session.composer.text
                    cap = choice_cap(rows, cols)
                    for size in (1, 4, cap, cap + 5):
                        session.slot = [plain(f"option {n}") for n in range(size)]
                        session.paint_hidden(f"a question of {size} rows")
                    session.paint_hidden("the answer is written above it", flush=["✓ allowed once · rm -rf build"])
                    session.slot = []
                    session.paint("the question closes")
                    self.assertEqual(session.composer.text, draft)
                    self.assertTrue(session.screen.cursor_visible)
                    self.assertFalse([row for row in session.screen.lines() if row.startswith("option ")])

class AppBottomRowsTests(unittest.TestCase):
    """The app on the bottom rows: what closes above the composer there leaves it in place, a redraw starts over."""

    def at_the_bottom(self, app_maker: object = None) -> tuple[object, list[str]]:
        from test_tui_modes import drawn, make_app

        app = app_maker() if callable(app_maker) else make_app(rows=24, cols=80)[0]
        app._on_input(Key(CHAR, "l", ctrl=True))  # a full redraw homes the cursor: the region knows its place
        drawn(app)
        app.transcript.add_notice("\n".join(f"line {n}" for n in range(40)))
        screen = drawn(app)
        self.assertEqual(app.region.last_row(24), 23)
        return app, screen

    def gap(self, screen: list[str]) -> int:
        """Blank rows right above the composer's top rule (the region's pad rows, and the one spacer row)."""
        rule = next(index for index, row in enumerate(screen) if row.startswith("─"))
        return next(count for count in range(rule + 1) if screen[rule - count - 1])

    def where(self, screen: list[str]) -> tuple[int, int]:
        """(the composer's top rule, the status line): screen rows."""
        rule = next(index for index, row in enumerate(screen) if row.startswith("─"))
        return rule, next(index for index, row in enumerate(screen) if row.startswith(STATUS_PREFIX))

    def test_a_picker_a_menu_a_question_and_the_agent_list_closing_leave_the_composer_on_the_bottom_rows(self) -> None:
        from test_tui_agent_strip import agent_app
        from test_tui_choice_ui import SESSIONS
        from test_tui_modes import drawn, type_text

        from omh.tui.gateway import Event, ServerRequest
        from omh.tui.keys import DOWN

        def picker(app: object) -> None:
            app._open_session_picker(SESSIONS)
            drawn(app)
            app._on_input(Key(ESCAPE))

        def menu(app: object) -> None:
            type_text(app, "/")
            self.assertTrue([row for row in drawn(app) if row.startswith(" /") or row.startswith("/")])
            app._on_input(Key(BACKSPACE))

        def question(app: object) -> None:
            app._on_server_request(ServerRequest("srq-9", "approval", {"command": "rm -rf ./x", "description": "delete"}))
            drawn(app)
            app._on_event(Event("request.cancel", app.sid, {"id": "srq-9"}))  # Hermes withdraws it

        def agent_list(app: object) -> None:
            app._on_input(Key(DOWN))
            drawn(app)
            app._on_input(Key(ESCAPE))

        cases = {"a picker": (picker, None), "a completion menu": (menu, None), "an approval": (question, None),
                 "the agent list": (agent_list, lambda: agent_app()[0])}
        for name, (opens_and_closes, maker) in cases.items():
            with self.subTest(closing=name):
                app, before = self.at_the_bottom(maker)
                opens_and_closes(app)
                after = drawn(app)
                self.assertEqual(self.where(after), self.where(before))  # the composer and the status line stayed
                self.assertEqual(self.where(after)[1], 23)
                # The freed rows stay above the composer; the approval's withdrawal notice is printed into them, and
                # the agent list's rows came out of the turn's tail, so neither leaves more blank rows than before.
                self.assertGreaterEqual(self.gap(after), self.gap(before))
                if name in ("a picker", "a completion menu"):
                    self.assertGreater(self.gap(after), self.gap(before))

    def test_the_next_output_fills_the_freed_rows_and_a_redraw_starts_under_the_output_again(self) -> None:
        from test_tui_modes import drawn, type_text

        app, before = self.at_the_bottom()
        type_text(app, "/")  # the completion menu: a slot as tall as its cap
        drawn(app)
        app._on_input(Key(BACKSPACE))
        freed = self.gap(drawn(app))
        self.assertGreater(freed, self.gap(before) + 2)
        app.transcript.add_notice("a notice")  # its spacer and its row
        filled = drawn(app)
        self.assertEqual(self.gap(filled), freed - 2)
        self.assertEqual(self.where(filled), self.where(before))
        app._full_redraw()  # a resize, Ctrl+L, a switch: the region starts again right under the output
        redrawn = drawn(app)
        self.assertEqual(self.gap(redrawn), self.gap(before))
        self.assertEqual(self.where(redrawn)[1], 23)

    def test_launched_on_the_last_row_it_follows_the_content_until_its_place_is_certain(self) -> None:
        # The owner's rule for an unknown place: count it as short of the bottom. The TUI does not ask the terminal
        # where its cursor is, so after a launch from a terminal already holding output, the first screen of
        # output is followed, a closing menu taking the composer up, and the bottom rows hold from then on.
        from test_tui_modes import drawn, make_app, type_text

        app = make_app(rows=24, cols=80)[0]
        app.terminal.screen.feed("".join(f"$ shell {n}\r\n" for n in range(30)))  # the cursor on the last row
        app.transcript.add_notice("a short first output")
        before = drawn(app)
        self.assertEqual((self.where(before)[1], app.region.last_row(24)), (23, None))
        type_text(app, "/")
        drawn(app)
        app._on_input(Key(BACKSPACE))
        after = drawn(app)
        self.assertLess(self.where(after)[1], 23)  # the menu's rows went with it: blank under the status line
        app.transcript.add_notice("\n".join(f"line {n}" for n in range(30)))
        drawn(app)
        self.assertEqual(app.region.last_row(24), 23)  # its own output filled the screen: certain, and pinned

    def test_a_cursor_row_the_terminal_reported_makes_the_place_certain_from_the_first_frame(self) -> None:
        # The startup query's cursor position report (CSI 6n): launched on the last row of a terminal already
        # holding output, the region knows it is on the bottom rows at once, so a closing menu leaves it there.
        from test_tui_modes import drawn, make_app, type_text

        app = make_app(rows=24, cols=80)[0]
        app.terminal.screen.feed("".join(f"$ shell {n}\r\n" for n in range(30)))  # the cursor on the last row
        app.adopt_cursor_row(24)  # 1-based, as the report says it
        before = drawn(app)
        self.assertEqual((self.where(before)[1], app.region.last_row(24)), (23, 23))
        type_text(app, "/")
        drawn(app)
        app._on_input(Key(BACKSPACE))
        after = drawn(app)
        self.assertEqual(self.where(after), self.where(before))  # 0 rows: not the uncertain launch's 11
        self.assertGreater(self.gap(after), self.gap(before))

    def test_short_of_the_bottom_a_completion_menu_opens_under_the_composer_and_nothing_above_moves(self) -> None:
        # Live review B: on a screen not yet full, `/` moved the composer and the caret down 11 rows and Esc moved
        # them back. Claude Code draws its menu under the input; so does the OMH TUI wherever it fits there.
        from test_tui_modes import drawn, make_app, type_text

        from omh.tui.app import path_items

        for start in ("/", "@"):
            with self.subTest(menu=start):
                app = make_app(rows=24, cols=80)[0]
                app.terminal.screen.feed("".join(f"$ shell {n}\r\n" for n in range(4)))
                app.adopt_cursor_row(5)
                before = drawn(app)
                caret = app.terminal.screen.cursor
                type_text(app, start)
                if start == "@":  # as Hermes' `complete.path` answer sets them
                    app.path_items = path_items({"items": [{"text": "@file:alpha.py"}, {"text": "@file:beta.py"}]})
                opened = drawn(app)
                rule = self.where(before)[0]
                self.assertEqual(self.where(opened)[0], rule)  # the composer's row did not move
                self.assertEqual(app.terminal.screen.cursor, (caret[0], caret[1] + 1))
                bottom = opened.index(opened[rule], rule + 1)  # the bottom rule, right under the input row
                self.assertTrue(opened[bottom + 1].strip(), opened)  # the menu's first row under it
                self.assertTrue(app.menu_below)
                app._on_input(Key(ESCAPE))
                closed = drawn(app)
                self.assertEqual(self.where(closed), self.where(before))
                self.assertIsNone(app.menu_below)

    def test_a_menu_that_opened_under_the_composer_stays_there_while_it_is_open(self) -> None:
        from test_tui_modes import drawn, make_app, type_text

        app = make_app(rows=24, cols=80)[0]
        app.adopt_cursor_row(1)
        drawn(app)
        rule = self.where(drawn(app))[0]
        for keys in ("/", "m", "o"):
            type_text(app, keys)
            self.assertEqual(self.where(drawn(app))[0], rule, keys)
        app._on_input(Key(BACKSPACE))
        app._on_input(Key(BACKSPACE))
        self.assertEqual(self.where(drawn(app))[0], rule)
        self.assertTrue(app.menu_below)

    def test_where_it_would_not_fit_under_the_composer_the_menu_opens_above_as_before(self) -> None:
        from test_tui_modes import drawn, make_app, type_text

        for row, reason in ((24, "on the bottom rows"), (17, "too close to them")):
            with self.subTest(reason=reason):
                app = make_app(rows=24, cols=80)[0]
                app.terminal.screen.feed("".join(f"$ shell {n}\r\n" for n in range(30)))
                app.adopt_cursor_row(row)
                before = drawn(app)
                type_text(app, "/")
                opened = drawn(app)
                self.assertFalse(app.menu_below)
                rule, status = self.where(opened)
                self.assertEqual(opened[rule + 2], opened[rule])  # rule, input row, rule: nothing under the composer
                self.assertEqual(status, 23)
                if row == 24:
                    self.assertEqual(self.where(opened), self.where(before))

    def test_the_fit_is_judged_with_the_menu_at_its_tallest(self) -> None:
        # Two `@` rows would fit under the composer here, eight (the cap at 24 rows) would not; the answer can grow.
        from test_tui_modes import drawn, make_app, type_text

        from omh.tui.app import path_items

        app = make_app(rows=24, cols=80)[0]
        app.terminal.screen.feed("".join(f"$ shell {n}\r\n" for n in range(14)))
        app.adopt_cursor_row(15)
        drawn(app)
        type_text(app, "@")
        app.path_items = path_items({"items": [{"text": "@file:alpha.py"}, {"text": "@file:beta.py"}]})
        drawn(app)
        self.assertFalse(app.menu_below)

    def test_output_that_brings_the_region_to_the_last_row_takes_the_menu_above_and_closing_moves_nothing(self) -> None:
        # The re-review's repro: `/` typed mid-screen while a reply streams; the reply pins the region with the menu
        # under the composer. The menu goes above in that same frame, so Esc or Backspace then moves nothing.
        from test_tui_modes import drawn, make_app, type_text

        for close in (Key(ESCAPE), Key(BACKSPACE)):
            with self.subTest(close=close.name):
                app = make_app(rows=24, cols=80)[0]
                app.adopt_cursor_row(3)
                drawn(app)
                type_text(app, "/")
                drawn(app)
                self.assertTrue(app.menu_below)
                app.transcript.add_notice("\n".join(f"line {n}" for n in range(30)))  # the region reaches the last row
                pushed = drawn(app)
                self.assertFalse(app.menu_below)
                rule, status = self.where(pushed)
                self.assertEqual(status, 23)
                self.assertEqual(pushed[rule + 2], pushed[rule])  # nothing under the composer now
                self.assertTrue(pushed[rule - 1].strip())  # the menu above it
                caret = app.terminal.screen.cursor
                app._on_input(close)
                closed = drawn(app)
                self.assertEqual(self.where(closed), (rule, status))
                self.assertEqual(app.terminal.screen.cursor[0], caret[0])

    def test_output_that_moves_the_region_short_of_the_last_row_leaves_the_menu_under_the_composer(self) -> None:
        # Filtered to two rows, the menu would no longer fit at its tallest after the push, but it is not re-decided:
        # only reaching the last row takes it above.
        from test_tui_modes import drawn, make_app, type_text

        app = make_app(rows=24, cols=80)[0]
        app.adopt_cursor_row(10)
        drawn(app)
        type_text(app, "/")
        drawn(app)
        self.assertTrue(app.menu_below)
        type_text(app, "mo")
        before = self.where(drawn(app))[0]
        app.transcript.add_notice("one\ntwo\nthree")  # its spacer and three rows
        after = drawn(app)
        self.assertTrue(app.menu_below)
        self.assertEqual(self.where(after)[0], before + 4)  # moved with the output only
        self.assertLess(self.where(after)[1], 23)

    def test_with_its_place_unknown_the_menu_opens_above_as_before(self) -> None:
        from test_tui_modes import drawn, make_app, type_text

        app = make_app(rows=24, cols=80)[0]
        drawn(app)
        type_text(app, "/")
        drawn(app)
        self.assertFalse(app.menu_below)

    def test_a_reported_row_mid_screen_is_where_the_first_frame_starts(self) -> None:
        from test_tui_modes import drawn, make_app

        app = make_app(rows=24, cols=80)[0]
        app.terminal.screen.feed("".join(f"$ shell {n}\r\n" for n in range(8)))
        app.adopt_cursor_row(9)
        screen = drawn(app)
        self.assertEqual(app.region.last_row(24), self.where(screen)[1])
        self.assertEqual(screen[7], "$ shell 7")

    def test_no_report_or_one_outside_the_screen_leaves_the_place_unknown(self) -> None:
        from test_tui_modes import drawn, make_app

        for row in (None, 0, 25, 999):
            with self.subTest(row=row):
                app = make_app(rows=24, cols=80)[0]
                app.terminal.screen.feed("".join(f"$ shell {n}\r\n" for n in range(30)))
                app.adopt_cursor_row(row)
                drawn(app)
                self.assertIsNone(app.region.last_row(24))  # today's uncertain path, never a guess

    def test_short_of_the_bottom_rows_a_closing_picker_takes_the_composer_up(self) -> None:
        from test_tui_choice_ui import SESSIONS
        from test_tui_modes import drawn, make_app

        app = make_app(rows=24, cols=80)[0]
        app._on_input(Key(CHAR, "l", ctrl=True))
        before = drawn(app)
        app._open_session_picker(SESSIONS)
        drawn(app)
        app._on_input(Key(ESCAPE))
        after = drawn(app)
        self.assertEqual(self.where(after), self.where(before))  # back right under the output, no rows held
        self.assertLess(self.where(after)[1], 23)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
