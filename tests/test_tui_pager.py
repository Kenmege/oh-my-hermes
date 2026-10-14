"""The in-memory pager's frames (`omh.tui.pager`): a frame repaints only the rows that changed since the last one."""

from __future__ import annotations

import random
import unittest

from _vt_screen import VTScreen
from omh.tui.keys import CHAR, Key
from omh.tui.pager import HISTORY_BLOCKS_KEPT, HistoryPager, Pager
from omh.tui.style import Line, Span
from omh.tui.theme import DEFAULT_THEME as THEME, theme_named


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


def key(char: str) -> Key:
    return Key(CHAR, char=char)


def items_of(heights: list[int]) -> list[list[Line]]:
    return [rows(*(f"block {index} row {row}" for row in range(height))) for index, height in enumerate(heights)]


def flattened(items: list[list[Line]]) -> list[Line]:
    """The blocks as `/history` used to build them all at once: a blank row between blocks."""
    lines: list[Line] = []
    for item in items:
        if item:
            lines.extend([()] if lines else [])
            lines.extend(item)
    return lines


class HistoryPagerTests(unittest.TestCase):
    """`/history` renders a block only when a page reaches it, and pages exactly as a pager over every row would."""

    def history(self, items: list[list[Line]], calls: list[tuple[int, int]] | None = None) -> HistoryPager:
        def render(index: int, width: int, theme: object) -> list[Line]:
            if calls is not None:
                calls.append((index, width))
            return items[index]

        return HistoryPager("history", count=len(items), render=render)

    def test_every_key_shows_the_page_a_pager_over_all_the_rows_shows(self) -> None:
        generator = random.Random(11)
        for trial in range(40):
            items = items_of([generator.randrange(0, 7) for _ in range(generator.randrange(1, 30))])  # 0: a block with no rows
            lazy, eager = self.history(items), Pager("history", flattened(items))
            lazy_screen, eager_screen = VTScreen(8, 30), VTScreen(8, 30)
            lazy_screen.feed(lazy.frame(30, 8, THEME))
            eager_screen.feed(eager.frame(30, 8, THEME))
            for step in range(25):
                chars = "".join(generator.choice("jjjkk bgG") for _ in range(generator.randrange(1, 4)))
                for pager, screen in ((lazy, lazy_screen), (eager, eager_screen)):
                    for char in chars:  # several keys between two frames: each is a move of its own
                        pager.handle_key(key(char), 8)
                    screen.feed(pager.frame(30, 8, THEME))
                with self.subTest(trial=trial, step=step, keys=chars):
                    self.assertEqual(lazy_screen.lines()[:-1], eager_screen.lines()[:-1])

    def test_blocks_with_no_rows_get_no_blank_row_and_none_opens_the_history(self) -> None:
        items = items_of([0, 0, 2, 0, 1])
        lazy, eager = self.history(items), Pager("history", flattened(items))
        lazy_screen, eager_screen = VTScreen(8, 30), VTScreen(8, 30)
        lazy_screen.feed(lazy.frame(30, 8, THEME))
        eager_screen.feed(eager.frame(30, 8, THEME))
        self.assertEqual([row.rstrip() for row in lazy_screen.lines()[:4]], ["block 2 row 0", "block 2 row 1", "", "block 4 row 0"])
        self.assertEqual(lazy_screen.lines()[:-1], eager_screen.lines()[:-1])

    def test_opening_and_jumping_to_either_end_render_only_the_blocks_those_pages_reach(self) -> None:
        calls: list[tuple[int, int]] = []
        pager = self.history(items_of([3] * 2000), calls)
        first = VTScreen(24, 80)
        first.feed(pager.frame(80, 24, THEME))
        self.assertEqual(first.lines()[0].rstrip(), "block 0 row 0")
        self.assertIn("history · 1-6/2000 messages", first.lines()[-1])
        self.assertLess(len(calls), 20)  # the first page and the last one, 6 blocks each
        pager.handle_key(key("G"), 24)
        last = VTScreen(24, 80)
        last.feed(pager.frame(80, 24, THEME))
        self.assertEqual(last.lines()[-2].rstrip(), "block 1999 row 2")
        pager.handle_key(key("g"), 24)
        pager.frame(80, 24, THEME)
        self.assertLess(len(calls), 20)  # both ends were kept

    def test_paging_through_every_block_keeps_only_the_most_recent_ones(self) -> None:
        calls: list[tuple[int, int]] = []
        pager = self.history(items_of([3] * 2000), calls)
        pager.frame(80, 24, THEME)
        for _ in range(2000 * 4 // 23 + 1):
            pager.handle_key(key(" "), 24)
            pager.frame(80, 24, THEME)
        self.assertEqual(pager.item, 1994)  # the last page's top: 23 rows end with block 1999's last
        self.assertLessEqual(len(pager._kept), HISTORY_BLOCKS_KEPT)
        self.assertEqual(len(calls), 2000)  # each block rendered once on the way down

    def test_a_new_width_renders_the_blocks_again_at_it(self) -> None:
        calls: list[tuple[int, int]] = []
        pager = self.history(items_of([2] * 50), calls)
        pager.frame(80, 10, THEME)
        pager.frame(60, 10, THEME)
        self.assertEqual({width for index, width in calls if index == 0}, {80, 60})

    def test_a_new_theme_renders_the_blocks_again_in_it(self) -> None:
        items = items_of([2] * 50)
        themes: list[object] = []

        def render(index: int, width: int, theme: object) -> list[Line]:
            if index == 0:
                themes.append(theme)
            return items[index]

        pager = HistoryPager("history", count=len(items), render=render)
        crimson = theme_named("crimson")
        pager.frame(80, 10, THEME)
        pager.frame(80, 10, crimson)
        self.assertEqual(len(themes), 2)
        self.assertIs(themes[1], crimson)

    def test_a_move_asked_for_before_the_first_frame_is_made_at_it(self) -> None:
        pager = self.history(items_of([3] * 100))
        pager.handle_key(key("G"), 10)
        pager.handle_key(key("k"), 10)
        screen = VTScreen(10, 40)
        screen.feed(pager.frame(40, 10, THEME))
        self.assertEqual(screen.lines()[-2].rstrip(), "block 99 row 1")


if __name__ == "__main__":
    unittest.main()
