"""No blank screen when the terminal goes away and comes back: the bytes the OMH TUI writes, counted.

Switching away from the terminal and back (another tab, a split laid out
again, a multiplexer pane) reaches the TUI as a resize signal. A shrink and
restore close together arrives as one signal that reads the old size, and by
then the terminal has reflowed or cut its rows, so every settled resize
redraws the whole session from memory. The erase that starts that redraw goes
out in the same synchronized update as the frame drawn after it, so a
terminal that holds the update shows no blank screen between the two; and
after it the screen and the scrollback hold the session exactly as before
the signal, whatever the resize did to the rows. Focus reports and opening
and leaving the pager erase nothing.
"""

from __future__ import annotations

from collections.abc import Callable
import time
import unittest

from _tui_gateway_replay import Scenario, spans
from test_tui_app import BOX_MARK, NO_TERMIOS, AppTestCase, Run, Step, startup_answers
from test_tui_modes import make_app
from omh.tui.app import RESIZE_SETTLE_SECONDS
from omh.tui.pager import ALT_SCREEN_OFF, Pager
from omh.tui.style import Span
from omh.tui.screen import CLEAR_SCREEN_AND_SCROLLBACK, SYNC_BEGIN, SYNC_END

ERASE_SCREEN = "\x1b[2J"
ERASE_SAVED = "\x1b[3J"
FULL_RESET = "\x1bc"
SETTLED = RESIZE_SETTLE_SECONDS + 0.2  # past the settle, and the frame after it


def erases(writes: list[str]) -> int:
    """Full-screen erases (ED 2, ED 3, RIS) in `writes`."""
    return sum(text.count(ERASE_SCREEN) + text.count(ERASE_SAVED) + text.count(FULL_RESET) for text in writes)


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class FlickerTests(AppTestCase):
    def session(self, *turns: str) -> Run:
        return self.prepare(
            Scenario("session", spans("session", "startup", "create", *turns), answers=startup_answers()),
            rows=24, cols=80,
        )

    def mark(self, run: Run, marks: dict[str, int], name: str, ready: Callable[[], bool]) -> Callable[[], bool]:
        """`ready`, noting the first time it holds how many writes there were and the whole screen with its scrollback."""
        def check() -> bool:
            if not ready():
                return False
            if name not in marks:
                marks[name] = len(run.terminal.writes)
                self.screens[name] = run.terminal.screen.scrollback + run.terminal.screen.lines()
            return True

        return check

    def ready(self, run: Run) -> Callable[[], bool]:
        """Idle, with the effort Hermes reports on the info line: nothing on the screen still waits on the gateway."""
        return lambda: run.terminal.idle() and "· effort default" in run.terminal.info_row()

    def after(self, run: Run, label: str, seconds: float = SETTLED) -> Callable[[], bool]:
        return lambda: label in run.terminal.fired and time.monotonic() - run.terminal.fired[label] > seconds

    def setUp(self) -> None:
        self.screens: dict[str, list[str]] = {}

    def assert_one_whole_redraw(self, run: Run, writes: list[str]) -> None:
        """One erase of screen and scrollback, written at the start of the synchronized update that paints the frame."""
        self.assertEqual(sum(text.count(ERASE_SCREEN) for text in writes), 1, writes)
        (redraw,) = [text for text in writes if ERASE_SCREEN in text]
        self.assertTrue(redraw.startswith(SYNC_BEGIN + CLEAR_SCREEN_AND_SCROLLBACK), redraw[:40])
        self.assertTrue(redraw.endswith(SYNC_END) and BOX_MARK in redraw, redraw[-80:])

    def assert_session_as_before(self, before: str, after: str) -> None:
        """The whole screen and its scrollback are what they were before the signal: nothing cut, lost or doubled."""
        before_rows, after_rows = self.screens[before], self.screens[after]
        self.assertEqual(len(after_rows), len(before_rows))
        self.assertEqual([(i, b, a) for i, (b, a) in enumerate(zip(before_rows, after_rows)) if b != a], [])

    def resize_back(self, *sizes: tuple[int, int]) -> None:
        """Signals that end at 24x80, before the resize settles; the screen and scrollback come back as they were."""
        run = self.session()
        marks: dict[str, int] = {}
        steps = [Step(resize=sizes[0], when=self.mark(run, marks, "before", self.ready(run)), label="0")]
        steps += [Step(resize=size, label=str(index)) for index, size in enumerate(sizes[1:], 1)]
        last = str(len(sizes) - 1)
        steps.append(Step(b"\x04", when=self.mark(run, marks, "after", self.after(run, last)), label="settled"))
        run.terminal.script(*steps)
        self.run_main(run)
        self.assert_clean_run(run)
        self.assert_one_whole_redraw(run, run.terminal.writes[marks["before"] : marks["after"]])
        self.assert_session_as_before("before", "after")

    def test_a_same_size_resize_signal_redraws_once_in_one_synchronized_frame(self) -> None:
        self.resize_back((24, 80))

    def test_a_shrink_and_restore_inside_the_settle_leaves_nothing_cut_or_doubled(self) -> None:
        for sizes in (((24, 79), (24, 80)), ((24, 40), (24, 80)), ((20, 80), (24, 80)), ((6, 80), (24, 80)),
                      ((24, 1), (24, 80))):
            with self.subTest(sizes=sizes):
                self.resize_back(*sizes)

    def test_a_real_resize_redraws_once_in_one_synchronized_frame(self) -> None:
        run = self.session()
        marks: dict[str, int] = {}
        run.terminal.script(
            Step(resize=(24, 70), when=self.mark(run, marks, "resize", run.terminal.idle), label="narrower"),
            Step(b"\x04", when=self.mark(run, marks, "end", self.after(run, "narrower")), capture="final",
                 label="settled"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assert_one_whole_redraw(run, run.terminal.writes[marks["resize"] : marks["end"]])
        screen = run.terminal.captures["final"]
        self.assertEqual(sum(line.startswith(BOX_MARK) for line in screen), 2, screen)
        self.assertTrue(all(len(line) <= 70 for line in screen), screen)

    def test_focus_reports_erase_nothing(self) -> None:
        run = self.session()
        marks: dict[str, int] = {}
        run.terminal.script(
            Step(b"\x1b[O", when=self.mark(run, marks, "before", self.ready(run)), label="focus out"),
            Step(b"\x1b[I", label="focus in"),
            Step(b"\x04", when=self.mark(run, marks, "after", self.after(run, "focus in")), label="settled"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assertEqual(erases(run.terminal.writes[marks["before"] : marks["after"]]), 0)
        self.assert_session_as_before("before", "after")

    def test_a_tab_hidden_and_shown_again_redraws_once_in_one_synchronized_frame(self) -> None:
        # A terminal tab switched away and back: a focus report out, the pane at a transient size (one column, as a
        # split pane laid out again can be), its own size again before the resize settles, a focus report in.
        run = self.session()
        marks: dict[str, int] = {}
        run.terminal.script(
            Step(b"\x1b[O", when=self.mark(run, marks, "before", self.ready(run)), label="focus out"),
            Step(resize=(24, 1), when=self.after(run, "focus out", 0.3), label="transient"),
            Step(resize=(24, 80), label="shown"),
            Step(b"\x1b[I", label="focus in"),
            Step(b"\x04", when=self.mark(run, marks, "after", self.after(run, "shown")), label="settled"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assert_one_whole_redraw(run, run.terminal.writes[marks["before"] : marks["after"]])
        self.assert_session_as_before("before", "after")

    def test_a_resize_there_and_back_each_settled_redraws_twice_and_ends_as_before(self) -> None:
        run = self.session()
        marks: dict[str, int] = {}
        run.terminal.script(
            Step(resize=(24, 70), when=self.mark(run, marks, "before", self.ready(run)), label="narrower"),
            Step(resize=(24, 80), when=self.mark(run, marks, "narrow", self.after(run, "narrower")), label="back"),
            Step(b"\x04", when=self.mark(run, marks, "after", self.after(run, "back")), label="settled"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assert_one_whole_redraw(run, run.terminal.writes[marks["before"] : marks["narrow"]])
        self.assert_one_whole_redraw(run, run.terminal.writes[marks["narrow"] : marks["after"]])
        self.assert_session_as_before("before", "after")

    def pager_resize(self, size: tuple[int, int]) -> tuple[Run, dict[str, int]]:
        """The details pager opened, the terminal resized to `size` while it shows, the pager left."""
        run = self.session("plain_turn")
        marks: dict[str, int] = {}
        terminal = run.terminal

        def replied() -> bool:
            return "later." in terminal.history() and terminal.idle()

        terminal.script(
            self.submit(run, "Reply with exactly the word: ok"),
            Step(b"\x0f", when=self.mark(run, marks, "before", replied), label="details"),
            Step(resize=size, when=lambda: terminal.screen.alternate, label="resized while paging"),
            Step(b"q", when=self.after(run, "resized while paging"), label="leave"),
            Step(b"\x04", when=self.mark(run, marks, "after", lambda: not terminal.screen.alternate and terminal.idle()),
                 capture="final", label="back"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assert_one_whole_redraw(run, terminal.writes[marks["before"] : marks["after"]])
        return run, marks

    def test_leaving_the_pager_after_a_resize_back_to_its_size_redraws_once_and_ends_as_before(self) -> None:
        self.pager_resize((24, 80))
        self.assert_session_as_before("before", "after")

    def test_leaving_the_pager_after_a_real_resize_redraws_once_at_the_new_width(self) -> None:
        run, _marks = self.pager_resize((24, 70))
        screen = run.terminal.captures["final"]
        self.assertEqual(sum(line.startswith(BOX_MARK) for line in screen), 2, screen)
        self.assertTrue(all(len(line) <= 70 for line in screen), screen)

    def test_the_pager_opens_and_closes_without_erasing_the_main_screen(self) -> None:
        run = self.session("plain_turn")
        marks: dict[str, int] = {}
        terminal = run.terminal

        def replied() -> bool:
            return "later." in terminal.history() and terminal.idle()

        run.terminal.script(
            self.submit(run, "Reply with exactly the word: ok"),
            Step(b"\x0f", when=self.mark(run, marks, "before", replied), label="details"),
            Step(b"q", when=lambda: terminal.screen.alternate, label="leave"),
            Step(b"\x04", when=self.mark(run, marks, "after", lambda: not terminal.screen.alternate and terminal.idle()),
                 label="back"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assertEqual(erases(terminal.writes[marks["before"] : marks["after"]]), 0)
        self.assert_session_as_before("before", "after")


class PagerRedrawTests(unittest.TestCase):
    """A full redraw while a pager is open (a gateway answer retaking the session: a catalog, a rewind, a resume)."""

    def paging(self):
        app, _client = make_app()
        app._show_pager(Pager("details", [(Span(f"row {n}"),) for n in range(5)]))
        app._render()
        app.terminal.writes.clear()
        return app

    def test_the_erase_goes_out_in_the_pager_s_next_frame(self) -> None:
        # #2346 review, LOW: the erase went out alone, so the alternate screen could show one blank frame.
        app = self.paging()
        app._full_redraw()
        app._render()
        (frame,) = app.terminal.writes
        self.assertTrue(frame.startswith(SYNC_BEGIN + CLEAR_SCREEN_AND_SCROLLBACK) and frame.endswith(SYNC_END),
                        frame[:40])
        self.assertIn("row 4", frame)  # every row painted again after the erase
        self.assertIn(" details · 1-5/5", app.terminal.screen.lines()[-1])
        app._render()
        self.assertEqual(sum(text.count(ERASE_SCREEN) for text in app.terminal.writes), 1, "the erase is owed once")

    def test_left_before_that_frame_the_erase_goes_to_the_alternate_screen_it_was_for(self) -> None:
        app = self.paging()
        app._full_redraw()
        app._leave_pager()
        (left,) = app.terminal.writes
        self.assertTrue(left.startswith(CLEAR_SCREEN_AND_SCROLLBACK + ALT_SCREEN_OFF), left[:40])
        self.assertEqual(app.pager_lead, "")


if __name__ == "__main__":
    unittest.main()
