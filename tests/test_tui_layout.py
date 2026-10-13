"""The live region's height budget and stacking order (`omh.tui.layout`).

`allocate` is checked as properties over a grid of terminal sizes and content
sizes, so a rule that holds at 24 rows but breaks at 7 or 80 shows up. The
hard ones: the region is never taller than `rows - 1`, composer and status
fit before anything else, the caps hold, and `min_height` is honoured with
pad rows. `compose` is checked for row order, the tail keeping its last rows,
and the exact row count.
"""

from __future__ import annotations

import itertools
import unittest

from omh.tui.composer import Composer
from omh.tui.frame import build_frame, rule_line, status_line, sticky_line
from omh.tui.layout import (
    BLANK,
    MIN_COLS,
    MIN_ROWS,
    Budget,
    allocate,
    caret_row,
    compose,
    composer_cap,
    slot_cap,
)
from omh.tui.style import Line, Span
from omh.tui.theme import DEFAULT_THEME
from omh.tui.width import line_width

ROWS = range(1, 86)
COLS = (5, MIN_COLS - 1, MIN_COLS, 40, 120)
TAIL_ROWS = (0, 1, 5, 40, 200)
SLOT_ROWS = (0, 2, 9, 60)
COMPOSER_ROWS = (0, 1, 3, 12, 50)


def plain(*texts: str) -> list[Line]:
    return [(Span(text),) for text in texts]


def texts(lines: list[Line]) -> list[str]:
    return ["".join(span.text for span in line) for line in lines]


class AllocateGridTests(unittest.TestCase):
    def test_budget_rules_hold_over_the_grid(self) -> None:
        checked = 0
        for rows, cols, tail_rows, slot_rows, composer_rows, sticky in itertools.product(
            ROWS, COLS, TAIL_ROWS, SLOT_ROWS, COMPOSER_ROWS, (False, True)
        ):
            for min_height in (0, rows // 2, rows - 1, rows + 3):
                budget = allocate(
                    rows,
                    cols,
                    sticky=sticky,
                    tail_rows=tail_rows,
                    slot_rows=slot_rows,
                    composer_rows=composer_rows,
                    min_height=min_height,
                )
                where = (rows, cols, tail_rows, slot_rows, composer_rows, sticky, min_height, budget)
                self.check_budget(budget, rows, cols, tail_rows, slot_rows, composer_rows, sticky, min_height, where)
                checked += 1
        self.assertGreater(checked, 100_000)

    def check_budget(
        self,
        budget: Budget,
        rows: int,
        cols: int,
        tail_rows: int,
        slot_rows: int,
        composer_rows: int,
        sticky: bool,
        min_height: int,
        where: tuple[object, ...],
    ) -> None:
        room = max(rows - 1, 0)
        content = budget.total - budget.pad
        parts = (budget.pad, budget.sticky, budget.tail, budget.slot, budget.rule, budget.composer, budget.status)
        # never taller than the terminal minus one row, nothing negative
        self.assertLessEqual(budget.total, room, where)
        self.assertTrue(all(part >= 0 for part in parts), where)
        # composer and status first: the composer gets its rows up to its cap, the status row next
        cap = max(1, rows // 4)
        self.assertEqual(budget.composer, min(max(composer_rows, 1), cap, room - 1 if room >= 2 else room), where)
        self.assertEqual(budget.status, 1 if rows >= 3 else 0, where)
        # caps
        self.assertLessEqual(budget.slot, rows // 3, where)
        self.assertLessEqual(budget.slot, slot_rows, where)
        self.assertLessEqual(budget.tail, tail_rows, where)
        self.assertLessEqual(budget.sticky, int(sticky), where)
        self.assertLessEqual(budget.rule, 1, where)
        # tiny terminals: composer and status only
        if rows < MIN_ROWS or cols < MIN_COLS:
            self.assertEqual((budget.sticky, budget.tail, budget.slot, budget.rule), (0, 0, 0, 0), where)
        else:
            self.assertEqual(budget.rule, 1, where)
            # nothing is cut while there is room for it
            if content < room:
                self.assertEqual(budget.tail, tail_rows, where)
                self.assertEqual(budget.slot, min(slot_rows, rows // 3), where)
                self.assertEqual(budget.sticky, int(sticky), where)
            # the sticky row is never taken while the slot is short of its cap
            if budget.sticky:
                self.assertEqual(budget.slot, min(slot_rows, rows // 3), where)
        # no-shrink: pad rows bring the region up to min_height, never past the room, never needlessly
        self.assertEqual(budget.total, max(content, min(min_height, room)), where)
        if min_height <= content:
            self.assertEqual(budget.pad, 0, where)


class AllocateExampleTests(unittest.TestCase):
    def test_an_everyday_terminal(self) -> None:
        budget = allocate(24, 80, sticky=True, tail_rows=3, slot_rows=4, composer_rows=1, min_height=0)
        self.assertEqual(budget, Budget(pad=0, sticky=1, tail=3, slot=4, rule=1, composer=1, status=1))
        self.assertEqual(budget.total, 11)
        self.assertEqual(budget.composer_top, 9)

    def test_the_tail_takes_the_rest_and_the_slot_is_capped(self) -> None:
        budget = allocate(24, 80, sticky=True, tail_rows=100, slot_rows=100, composer_rows=100, min_height=0)
        self.assertEqual(budget, Budget(pad=0, sticky=1, tail=6, slot=8, rule=1, composer=6, status=1))
        self.assertEqual(budget.total, 23)

    def test_pad_rows_hold_the_previous_height(self) -> None:
        budget = allocate(24, 80, sticky=False, tail_rows=0, slot_rows=0, composer_rows=1, min_height=15)
        self.assertEqual(budget, Budget(pad=12, sticky=0, tail=0, slot=0, rule=1, composer=1, status=1))

    def test_pad_never_pushes_past_the_room(self) -> None:
        budget = allocate(10, 80, sticky=False, tail_rows=0, slot_rows=0, composer_rows=1, min_height=50)
        self.assertEqual(budget.total, 9)
        self.assertEqual(budget.pad, 6)

    def test_a_tiny_terminal_draws_composer_and_status_only(self) -> None:
        for rows, cols in ((5, 80), (24, MIN_COLS - 1)):
            with self.subTest(rows=rows, cols=cols):
                budget = allocate(rows, cols, sticky=True, tail_rows=9, slot_rows=9, composer_rows=1, min_height=0)
                self.assertEqual(budget, Budget(pad=0, sticky=0, tail=0, slot=0, rule=0, composer=1, status=1))

    def test_the_smallest_terminals(self) -> None:
        two = allocate(2, 80, sticky=True, tail_rows=9, slot_rows=9, composer_rows=3, min_height=9)
        self.assertEqual(two, Budget(pad=0, sticky=0, tail=0, slot=0, rule=0, composer=1, status=0))
        one = allocate(1, 80, sticky=True, tail_rows=9, slot_rows=9, composer_rows=3, min_height=9)
        self.assertEqual(one.total, 0)

    def test_the_caps(self) -> None:
        self.assertEqual([composer_cap(rows) for rows in (1, 2, 3, 7, 8, 24, 80)], [0, 1, 1, 1, 2, 6, 20])
        self.assertEqual([slot_cap(rows, 80) for rows in (5, 6, 24, 80)], [0, 2, 8, 26])
        self.assertEqual(slot_cap(24, MIN_COLS - 1), 0)


class ComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = (Span("──"),)
        self.status = (Span("status"),)

    def test_rows_stack_in_order_and_the_tail_keeps_its_last_rows(self) -> None:
        budget = Budget(pad=2, sticky=1, tail=2, slot=2, rule=1, composer=2, status=1)
        region = compose(
            budget,
            sticky=(Span("↳ you: hi"),),
            tail=plain("t1", "t2", "t3", "t4"),
            slot=plain("s1", "s2", "s3"),
            rule=self.rule,
            composer=plain("› a", "  b"),
            status=self.status,
        )
        self.assertEqual(
            texts(region), ["", "", "↳ you: hi", "t3", "t4", "s1", "s2", "──", "› a", "  b", "status"]
        )
        self.assertEqual(len(region), budget.total)

    def test_short_parts_are_filled_with_blank_rows_to_the_exact_count(self) -> None:
        budget = Budget(pad=0, sticky=1, tail=3, slot=2, rule=1, composer=2, status=1)
        region = compose(
            budget,
            sticky=None,
            tail=plain("t1"),
            slot=plain("s1"),
            rule=self.rule,
            composer=plain("› a"),
            status=self.status,
        )
        self.assertEqual(texts(region), ["", "", "", "t1", "", "s1", "──", "› a", "", "status"])
        self.assertEqual(region[0], BLANK)

    def test_nothing_budgeted_draws_nothing(self) -> None:
        budget = Budget(pad=0, sticky=0, tail=0, slot=0, rule=0, composer=1, status=1)
        region = compose(
            budget,
            sticky=(Span("sticky"),),
            tail=plain("t1"),
            slot=plain("s1"),
            rule=self.rule,
            composer=plain("› a"),
            status=self.status,
        )
        self.assertEqual(texts(region), ["› a", "status"])

    def test_caret_row_counts_everything_above_the_composer(self) -> None:
        budget = Budget(pad=2, sticky=1, tail=2, slot=2, rule=1, composer=3, status=1)
        self.assertEqual([caret_row(budget, row) for row in (-1, 0, 1, 2, 7)], [8, 8, 9, 10, 10])


class FrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.status = (Span("status"),)

    def test_the_first_frame_fills_the_screen_but_one_row(self) -> None:
        composer = Composer(text="hi", caret=2)
        frame = build_frame(cols=40, rows=10, composer=composer, status=self.status, previous_height=0)
        self.assertEqual((frame.height, frame.pad), (9, 6))
        self.assertEqual(texts(list(frame.lines))[-3:], ["─" * 40, "› hi", "status"])
        self.assertEqual(frame.cursor, (7, 4))

    def test_later_frames_keep_the_previous_height_and_grow_past_it(self) -> None:
        composer = Composer()
        kept = build_frame(cols=40, rows=24, composer=composer, status=self.status, previous_height=5)
        self.assertEqual((kept.height, kept.pad), (5, 2))
        grown = build_frame(
            cols=40, rows=24, composer=composer, status=self.status, previous_height=5, tail=plain("a", "b", "c")
        )
        self.assertEqual((grown.height, grown.pad), (6, 0))
        self.assertEqual(texts(list(grown.lines))[:3], ["a", "b", "c"])

    def test_the_composer_is_rendered_within_its_cap(self) -> None:
        composer = Composer(text="1\n2\n3\n4\n5", caret=9)
        frame = build_frame(cols=40, rows=12, composer=composer, status=self.status, previous_height=11)
        self.assertEqual(frame.budget.composer, 3)
        self.assertEqual(texts(list(frame.lines))[-4:], ["  3", "  4", "  5", "status"])
        self.assertEqual(frame.cursor, (9, 3))

    def test_the_attachment_row_tops_the_composer_and_the_input_row_stays_put(self) -> None:
        chips = (Span("📎 shot.png 1280×720"),)
        for text in ("", "1\n2\n3\n4\n5"):
            with self.subTest(text=text):
                composer = Composer(text=text, caret=len(text))
                plain_frame = build_frame(cols=40, rows=12, composer=composer, status=self.status, previous_height=11)
                with_chips = build_frame(
                    cols=40, rows=12, composer=composer, status=self.status, previous_height=11, attachments=chips
                )
                self.assertEqual(with_chips.height, plain_frame.height)
                self.assertEqual(with_chips.cursor, plain_frame.cursor)
                above_status = with_chips.height - 2
                self.assertEqual(texts([with_chips.lines[above_status]]), texts([plain_frame.lines[above_status]]))
                self.assertLessEqual(with_chips.budget.composer, composer_cap(12))
                top = with_chips.budget.composer_top
                self.assertEqual(texts([with_chips.lines[top]]), ["📎 shot.png 1280×720"])
                self.assertEqual(texts([with_chips.lines[top - 1]]), ["─" * 40])

    def test_a_terminal_too_short_for_two_composer_rows_drops_the_attachment_row(self) -> None:
        frame = build_frame(
            cols=40, rows=7, composer=Composer(), status=self.status, previous_height=6, attachments=(Span("📎 x"),)
        )
        self.assertNotIn("📎 x", texts(list(frame.lines)))

    def test_a_tiny_terminal_frame_is_composer_and_status(self) -> None:
        frame = build_frame(
            cols=12, rows=4, composer=Composer(), status=self.status, previous_height=2, slot=plain("menu")
        )
        self.assertEqual(texts(list(frame.lines)), ["› ", "status"])
        self.assertEqual(frame.cursor, (0, 2))

    def test_the_rule_spans_the_width_in_the_rule_colour(self) -> None:
        self.assertEqual(rule_line(5, DEFAULT_THEME), (Span("─────", DEFAULT_THEME.rule),))

    def test_the_sticky_line_shows_the_first_line_of_the_prompt(self) -> None:
        line = sticky_line("  fix the 한글 bug\nand more", 40, DEFAULT_THEME)
        self.assertEqual(texts([line]), ["↳ you: fix the 한글 bug"])
        self.assertEqual(texts([sticky_line("abcdefghij", 12, DEFAULT_THEME)]), ["↳ you: abcd…"])

    def test_the_status_line_fills_the_width(self) -> None:
        theme = DEFAULT_THEME
        line = status_line(version="1.2.3", model="m", context="ctx 4%", state="idle", width=40, theme=theme)
        self.assertEqual(texts([line]), ["⚚ OMH v1.2.3 │ m │ ctx 4% │ idle" + " " * 8])
        self.assertEqual(line_width(line), 40)
        self.assertEqual([span.style for span in line[:3]], [theme.status, theme.status_dim, theme.status])

    def test_the_status_line_skips_empty_fields_and_cuts_to_the_width(self) -> None:
        line = status_line(version="1.2.3", model="", context="", state="running", width=24)
        self.assertEqual(texts([line]), ["⚚ OMH v1.2.3 │ running  "])
        cut = status_line(version="1.2.3", model="a-very-long-model-name", context="", state="idle", width=20)
        self.assertEqual(texts([cut]), ["⚚ OMH v1.2.3 │ a-ve…"])


if __name__ == "__main__":
    unittest.main()
