"""No flicker when the terminal goes away and comes back: the bytes the OMH TUI writes, counted.

Switching away from the terminal and back (another tab, window, multiplexer
pane) can reach the TUI as a resize signal whose size did not change, or as
a size that changes and changes back faster than the resize settles (a
multiplexer's forced redraw shrinks a pane by one and restores it). Neither
reflowed anything the TUI drew, so neither may erase the screen: the region
is repainted in place inside one synchronized update. A real size change
still redraws the whole session, and the erase that starts that redraw goes
out in the same synchronized update as the frame drawn after it, so no blank
screen shows between the two. Focus reports and opening and leaving the pager
erase nothing either.
"""

from __future__ import annotations

from collections.abc import Callable
import time
import unittest

from _tui_gateway_replay import Scenario, spans
from test_tui_app import BOX_MARK, NO_TERMIOS, AppTestCase, Run, Step, startup_answers
from omh.tui.app import RESIZE_SETTLE_SECONDS
from omh.tui.screen import SYNC_BEGIN, SYNC_END

ERASE_SCREEN = "\x1b[2J"
ERASE_SAVED = "\x1b[3J"
FULL_RESET = "\x1bc"
SETTLED = RESIZE_SETTLE_SECONDS + 0.2  # past the settle, and the frame after it


def erases(writes: list[str]) -> int:
    """Full-screen erases (ED 2, ED 3, RIS) in `writes`."""
    return sum(text.count(ERASE_SCREEN) + text.count(ERASE_SAVED) + text.count(FULL_RESET) for text in writes)


def unsynchronized(writes: list[str]) -> list[str]:
    """The writes that erase the screen outside one synchronized update that also paints the frame after it."""
    return [
        text for text in writes
        if ERASE_SCREEN in text and not (
            text.startswith(SYNC_BEGIN) and text.endswith(SYNC_END) and BOX_MARK in text
        )
    ]


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class FlickerTests(AppTestCase):
    def session(self) -> Run:
        return self.prepare(
            Scenario("session", spans("session", "startup", "create"), answers=startup_answers()), rows=24, cols=80
        )

    def mark(self, run: Run, marks: dict[str, int], name: str, ready: Callable[[], bool]) -> Callable[[], bool]:
        """`ready`, noting how many writes there were the first time it holds (where scenario `name` starts)."""
        def check() -> bool:
            if not ready():
                return False
            marks.setdefault(name, len(run.terminal.writes))
            return True

        return check

    def after(self, run: Run, label: str, seconds: float = SETTLED) -> Callable[[], bool]:
        return lambda: label in run.terminal.fired and time.monotonic() - run.terminal.fired[label] > seconds

    def test_a_same_size_resize_signal_erases_nothing_and_repaints_the_region_in_place(self) -> None:
        run = self.session()
        marks: dict[str, int] = {}
        run.terminal.script(
            Step(resize=(24, 80), when=self.mark(run, marks, "winch", run.terminal.idle), label="same size"),
            Step(b"\x04", when=self.mark(run, marks, "end", self.after(run, "same size")), capture="final",
                 label="settled"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        writes = run.terminal.writes[marks["winch"] : marks["end"]]
        self.assertEqual(erases(writes), 0, writes)
        painted = [text for text in writes if BOX_MARK in text]
        self.assertTrue(painted, writes)  # the region was painted again, the composer's rules with it
        self.assertTrue(all(text.startswith(SYNC_BEGIN) and text.endswith(SYNC_END) for text in painted), painted)
        self.assertEqual(sum(line.startswith(BOX_MARK) for line in run.terminal.captures["final"]), 2)

    def test_a_size_that_changes_and_changes_back_before_it_settles_erases_nothing(self) -> None:
        # A multiplexer's forced redraw: one column narrower, then the old size again, within the settle.
        run = self.session()
        marks: dict[str, int] = {}
        run.terminal.script(
            Step(resize=(24, 79), when=self.mark(run, marks, "shrink", run.terminal.idle), label="narrower"),
            Step(resize=(24, 80), label="restored"),
            Step(b"\x04", when=self.mark(run, marks, "end", self.after(run, "restored")), capture="final",
                 label="settled"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        writes = run.terminal.writes[marks["shrink"] : marks["end"]]
        self.assertEqual(erases(writes), 0, writes)
        screen = run.terminal.captures["final"]
        self.assertEqual(sum(line.startswith(BOX_MARK) for line in screen), 2, screen)
        everything = run.terminal.screen.scrollback + screen
        self.assertEqual(sum(line.startswith("⚚ OH-MY-HERMES") for line in everything), 1, everything)

    def test_a_real_resize_redraws_once_with_the_erase_inside_the_frames_synchronized_update(self) -> None:
        run = self.session()
        marks: dict[str, int] = {}
        run.terminal.script(
            Step(resize=(24, 70), when=self.mark(run, marks, "resize", run.terminal.idle), label="narrower"),
            Step(b"\x04", when=self.mark(run, marks, "end", self.after(run, "narrower")), capture="final",
                 label="settled"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        writes = run.terminal.writes[marks["resize"] : marks["end"]]
        self.assertEqual(sum(text.count(ERASE_SCREEN) for text in writes), 1, writes)
        self.assertEqual(unsynchronized(writes), [])  # no write leaves the screen blank until the next one
        screen = run.terminal.captures["final"]
        self.assertEqual(sum(line.startswith(BOX_MARK) for line in screen), 2, screen)
        self.assertTrue(all(len(line) <= 70 for line in screen), screen)

    def test_focus_reports_erase_nothing(self) -> None:
        run = self.session()
        marks: dict[str, int] = {}
        run.terminal.script(
            Step(b"\x1b[O", when=self.mark(run, marks, "out", run.terminal.idle), label="focus out"),
            Step(b"\x1b[I", label="focus in"),
            Step(b"\x04", when=self.mark(run, marks, "end", self.after(run, "focus in")), label="settled"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assertEqual(erases(run.terminal.writes[marks["out"] : marks["end"]]), 0)

    def test_a_tab_hidden_and_shown_again_erases_nothing(self) -> None:
        # A terminal tab switched away and back: a focus report out, the pane at a transient size (one column, as a
        # split pane laid out again can be), its own size again before the resize settles, a focus report in.
        run = self.session()
        marks: dict[str, int] = {}
        terminal = run.terminal
        run.terminal.script(
            Step(b"\x1b[O", when=self.mark(run, marks, "hide", terminal.idle), label="focus out"),
            Step(resize=(24, 1), when=self.after(run, "focus out", 0.3), label="transient"),
            Step(resize=(24, 80), label="shown"),
            Step(b"\x1b[I", label="focus in"),
            Step(b"\x04", when=self.mark(run, marks, "end", self.after(run, "shown")), capture="final", label="settled"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        writes = terminal.writes[marks["hide"] : marks["end"]]
        self.assertEqual(erases(writes), 0, writes)
        painted = [text for text in writes if BOX_MARK in text]
        self.assertTrue(painted, writes)  # repainted in place once the size settled back
        self.assertTrue(all(text.startswith(SYNC_BEGIN) and text.endswith(SYNC_END) for text in painted), painted)
        screen = terminal.captures["final"]
        self.assertEqual(sum(line.startswith(BOX_MARK) for line in screen), 2, screen)

    def test_the_pager_opens_and_closes_without_erasing_the_main_screen(self) -> None:
        run = self.prepare(
            Scenario("session", spans("session", "startup", "create", "plain_turn"), answers=startup_answers())
        )
        marks: dict[str, int] = {}
        terminal = run.terminal

        def replied() -> bool:
            return "later." in terminal.history() and terminal.idle()

        run.terminal.script(
            self.submit(run, "Reply with exactly the word: ok"),
            Step(b"\x0f", when=self.mark(run, marks, "open", replied), label="details"),
            Step(resize=(24, 80), when=lambda: terminal.screen.alternate, label="same size while paging"),
            Step(b"q", when=self.after(run, "same size while paging"), label="leave"),
            Step(b"\x04", when=self.mark(run, marks, "end", lambda: not terminal.screen.alternate and terminal.idle()),
                 label="back"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assertEqual(erases(terminal.writes[marks["open"] : marks["end"]]), 0)


if __name__ == "__main__":
    unittest.main()
