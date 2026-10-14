"""The in-memory pager's frames (`omh.tui.pager`): a frame repaints only the rows that changed since the last one."""

from __future__ import annotations

import random
import unittest

from _vt_screen import VTScreen
from omh.tui.pager import Pager
from omh.tui.style import Line, Span
from omh.tui.theme import DEFAULT_THEME as THEME


def rows(*texts: str) -> list[Line]:
    return [(Span(text),) for text in texts]


def fresh_screen(pager: Pager, cols: int, height: int) -> list[str]:
    """What a pager showing the same page draws on a blank screen."""
    screen = VTScreen(height, cols)
    copy = Pager(pager.title, list(pager.lines), pager.top)
    screen.feed(copy.frame(cols, height, THEME))
    return screen.lines()


class PagerFrameTests(unittest.TestCase):
    def test_a_frame_with_nothing_changed_writes_nothing(self) -> None:
        pager = Pager("log", rows("one", "two"))
        self.assertIn("two", pager.frame(30, 6, THEME))
        self.assertEqual(pager.frame(30, 6, THEME), "")

    def test_only_the_rows_that_changed_are_painted(self) -> None:
        pager = Pager("log", rows("row 0", "row 1", "row 2"))
        pager.frame(30, 6, THEME)
        pager.lines = rows("row 0", "ROW 1", "row 2")
        changed = pager.frame(30, 6, THEME)
        self.assertIn("ROW 1", changed)
        self.assertNotIn("row 0", changed)
        self.assertNotIn("row 2", changed)
        self.assertNotIn("log ·", changed)  # the footer says the same

    def test_a_new_size_or_an_invalidate_paints_every_row_again(self) -> None:
        pager = Pager("log", rows("row 0", "row 1"))
        pager.frame(30, 6, THEME)
        self.assertIn("row 0", pager.frame(31, 6, THEME))
        self.assertIn("row 0", pager.frame(31, 7, THEME))
        pager.invalidate()  # something else cleared the screen
        self.assertIn("row 0", pager.frame(31, 7, THEME))

    def test_the_screen_after_any_run_of_diffed_frames_is_the_one_a_full_frame_draws(self) -> None:
        generator = random.Random(5)
        words = ["alpha", "beta", "gamma", "delta", "한글 줄", "a much longer row that runs past the edge of the screen"]
        for trial in range(30):
            pager = Pager("log", [])
            screen = VTScreen(8, 24)
            for _step in range(12):
                pager.lines = rows(*(generator.choice(words) for _ in range(generator.randrange(0, 14))))
                pager.top = generator.randrange(0, 10)
                screen.feed(pager.frame(24, 8, THEME))
                with self.subTest(trial=trial, step=_step):
                    self.assertEqual(screen.lines(), fresh_screen(pager, 24, 8))


if __name__ == "__main__":
    unittest.main()
