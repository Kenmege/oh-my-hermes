"""The live region's height budget and stacking order (`omh.tui.layout`).

`allocate` is checked as properties over a grid of terminal sizes and content
sizes, so a rule that holds at 24 rows but breaks at 7 or 80 shows up. The
hard ones: the region is never taller than `rows - 1`, composer and status
fit before anything else, the rows under the composer follow the terminal's
height alone (the mode row goes before the bottom rule), the caps hold, and
nothing is cut while the region has room, so the region is exactly as tall as
its content. `compose` is checked for row order, the tail keeping its last
rows, and the exact row count. The focused agent list is the one content size
that changes the rows under the composer; it is checked over the same sizes
for the bounds that matter: it adds its extra rows to the region, and where
the region would pass `rows - 1` they come out of what is above the rule and
nothing else. A hidden composer (a question answered by
picking) is checked over the same grid: no rules or input rows, the mode row
and the status line exactly where the composer leaves them, and the slot given
its `choice_cap`, at least one row wherever the composer had one.
"""

from __future__ import annotations

import itertools
import unittest

from _tui_lines import lines_text
from omh.tui.composer import Composer
from omh.tui.frame import build_frame, rule_line, sticky_line
from omh.tui.frame import MODE_ACCEPT, MODE_PLAN
from omh.tui.layout import (
    BLANK,
    BOTTOM_RULE_MIN_ROWS,
    MIN_COLS,
    MIN_ROWS,
    MODE_ROW_MIN_ROWS,
    Budget,
    agent_list_cap,
    allocate,
    caret_row,
    choice_cap,
    compose,
    composer_cap,
    slot_cap,
)
from omh.tui.style import Line, Span
from omh.tui.theme import DEFAULT_THEME

ROWS = range(1, 86)
COLS = (5, MIN_COLS - 1, MIN_COLS, 40, 120)
TAIL_ROWS = (0, 1, 5, 40, 200)
SLOT_ROWS = (0, 2, 9, 60)
COMPOSER_ROWS = (0, 1, 3, 12, 50)


def plain(*texts: str) -> list[Line]:
    return [(Span(text),) for text in texts]


class AllocateGridTests(unittest.TestCase):
    def test_budget_rules_hold_over_the_grid(self) -> None:
        checked = 0
        for rows, cols, tail_rows, slot_rows, composer_rows, sticky in itertools.product(
            ROWS, COLS, TAIL_ROWS, SLOT_ROWS, COMPOSER_ROWS, (False, True)
        ):
            budget = allocate(
                rows,
                cols,
                sticky=sticky,
                tail_rows=tail_rows,
                slot_rows=slot_rows,
                composer_rows=composer_rows,
            )
            where = (rows, cols, tail_rows, slot_rows, composer_rows, sticky, budget)
            self.check_budget(budget, rows, cols, tail_rows, slot_rows, composer_rows, sticky, where)
            checked += 1
        self.assertGreater(checked, 25_000)

    def check_budget(
        self,
        budget: Budget,
        rows: int,
        cols: int,
        tail_rows: int,
        slot_rows: int,
        composer_rows: int,
        sticky: bool,
        where: tuple[object, ...],
    ) -> None:
        room = max(rows - 1, 0)
        content = budget.total
        parts = (
            budget.sticky,
            budget.tail,
            budget.slot,
            budget.rule,
            budget.composer,
            budget.bottom_rule,
            budget.mode,
            budget.status,
        )
        # never taller than the terminal minus one row, nothing negative
        self.assertLessEqual(budget.total, room, where)
        self.assertTrue(all(part >= 0 for part in parts), where)
        # composer and status first: the composer gets its rows up to its cap, the status row next,
        # and the cap leaves room for the rows the terminal's height puts under the composer
        cap = max(1, rows // 4)
        under = int(rows >= BOTTOM_RULE_MIN_ROWS) + int(rows >= MODE_ROW_MIN_ROWS)
        self.assertEqual(
            budget.composer, min(max(composer_rows, 1), cap, (room - 1 if room >= 2 else room) - under), where
        )
        self.assertEqual(budget.status, 1 if rows >= 3 else 0, where)
        # caps
        self.assertLessEqual(budget.slot, rows // 3, where)
        self.assertLessEqual(budget.slot, slot_rows, where)
        self.assertLessEqual(budget.tail, tail_rows, where)
        self.assertLessEqual(budget.sticky, int(sticky), where)
        self.assertLessEqual(budget.rule, 1, where)
        # tiny terminals: composer and status only
        if rows < MIN_ROWS or cols < MIN_COLS:
            self.assertEqual(
                (budget.sticky, budget.tail, budget.slot, budget.rule, budget.bottom_rule, budget.mode),
                (0, 0, 0, 0, 0, 0),
                where,
            )
        else:
            self.assertEqual(budget.rule, 1, where)
            # the rows under the composer depend on the height alone, whatever the content
            self.assertEqual(budget.bottom_rule, int(rows >= BOTTOM_RULE_MIN_ROWS), where)
            self.assertEqual(budget.mode, int(rows >= MODE_ROW_MIN_ROWS), where)
            # nothing is cut while there is room for it
            if content < room:
                self.assertEqual(budget.tail, tail_rows, where)
                self.assertEqual(budget.slot, min(slot_rows, rows // 3), where)
                self.assertEqual(budget.sticky, int(sticky), where)
            # the sticky row is never taken while the slot is short of its cap
            if budget.sticky:
                self.assertEqual(budget.slot, min(slot_rows, rows // 3), where)


def budget(**rows: int) -> Budget:
    """A `Budget` with every part not named at 0."""
    parts = ("sticky", "tail", "slot", "rule", "composer", "bottom_rule", "mode", "status")
    return Budget(**{part: rows.get(part, 0) for part in parts})


class AllocateExampleTests(unittest.TestCase):
    def test_an_everyday_terminal(self) -> None:
        got = allocate(24, 80, sticky=True, tail_rows=3, slot_rows=4, composer_rows=1)
        self.assertEqual(got, budget(sticky=1, tail=3, slot=4, rule=1, composer=1, bottom_rule=1, mode=1, status=1))
        self.assertEqual(got.total, 13)
        self.assertEqual(got.composer_top, 9)
        # the composer's first row: its own rows plus the bottom rule, the mode row and the status line from the end
        self.assertEqual(got.total - got.composer_top, got.composer + 3)

    def test_the_tail_takes_the_rest_and_the_slot_is_capped(self) -> None:
        got = allocate(24, 80, sticky=True, tail_rows=100, slot_rows=100, composer_rows=100)
        self.assertEqual(got, budget(sticky=1, tail=4, slot=8, rule=1, composer=6, bottom_rule=1, mode=1, status=1))
        self.assertEqual(got.total, 23)

    def test_an_idle_region_is_its_composer_frame_and_status_line_alone(self) -> None:
        got = allocate(24, 80, sticky=False, tail_rows=0, slot_rows=0, composer_rows=1)
        self.assertEqual(got, budget(rule=1, composer=1, bottom_rule=1, mode=1, status=1))
        self.assertEqual(got.total, 5)

    def test_a_tiny_terminal_draws_composer_and_status_only(self) -> None:
        for rows, cols in ((5, 80), (24, MIN_COLS - 1)):
            with self.subTest(rows=rows, cols=cols):
                got = allocate(rows, cols, sticky=True, tail_rows=9, slot_rows=9, composer_rows=1)
                self.assertEqual(got, budget(composer=1, status=1))

    def test_a_short_terminal_drops_the_mode_row_then_the_bottom_rule(self) -> None:
        shapes = {
            rows: (got.bottom_rule, got.mode)
            for rows in range(MIN_ROWS, 13)
            for got in [allocate(rows, 80, sticky=False, tail_rows=0, slot_rows=0, composer_rows=1)]
        }
        self.assertEqual(
            shapes, {6: (0, 0), 7: (1, 0), 8: (1, 0), 9: (1, 0), 10: (1, 1), 11: (1, 1), 12: (1, 1)}
        )

    def test_a_full_slot_keeps_its_cap_beside_the_rows_under_the_composer(self) -> None:
        # The heights the mode row and the bottom rule start at are chosen so an approval
        # still gets every row it may take: neither ever costs the slot a row.
        for rows in range(MIN_ROWS, 86):
            with self.subTest(rows=rows):
                got = allocate(rows, 80, sticky=False, tail_rows=0, slot_rows=999, composer_rows=999)
                self.assertEqual(got.slot, slot_cap(rows, 80))

    def test_the_smallest_terminals(self) -> None:
        two = allocate(2, 80, sticky=True, tail_rows=9, slot_rows=9, composer_rows=3)
        self.assertEqual(two, budget(composer=1))
        one = allocate(1, 80, sticky=True, tail_rows=9, slot_rows=9, composer_rows=3)
        self.assertEqual(one.total, 0)

    def test_the_caps(self) -> None:
        self.assertEqual([composer_cap(rows) for rows in (1, 2, 3, 7, 8, 24, 80)], [0, 1, 1, 1, 2, 6, 20])
        self.assertEqual([slot_cap(rows, 80) for rows in (5, 6, 24, 80)], [0, 2, 8, 26])
        self.assertEqual(slot_cap(24, MIN_COLS - 1), 0)


class HiddenComposerBudgetTests(unittest.TestCase):
    """While the person picks from a list the composer frame is not drawn and the slot takes its rows."""

    def test_the_slot_takes_the_composer_frames_rows_and_the_rows_under_it_stay(self) -> None:
        checked = 0
        for rows, cols, tail_rows, slot_rows, composer_rows, sticky in itertools.product(
            ROWS, COLS, TAIL_ROWS, SLOT_ROWS, COMPOSER_ROWS, (False, True)
        ):
            parts = dict(sticky=sticky, tail_rows=tail_rows, slot_rows=slot_rows)
            shown = allocate(rows, cols, composer_rows=composer_rows, **parts)  # type: ignore[arg-type]
            got = allocate(rows, cols, composer_rows=composer_rows, composer_hidden=True, **parts)  # type: ignore[arg-type]
            where = (rows, cols, tail_rows, slot_rows, composer_rows, sticky, got)
            self.assertEqual((got.rule, got.composer, got.bottom_rule), (0, 0, 0), where)
            self.assertEqual((got.mode, got.status), (shown.mode, shown.status), where)
            self.assertLessEqual(got.total, max(rows - 1, 0), where)
            self.assertTrue(all(part >= 0 for part in (got.sticky, got.tail, got.slot)), where)
            self.assertEqual(got.slot, min(slot_rows, choice_cap(rows, cols)), where)
            if slot_rows and shown.composer:
                self.assertGreaterEqual(got.slot, 1, where)  # the highlighted row, at least
            if rows < MIN_ROWS or cols < MIN_COLS:
                self.assertEqual((got.sticky, got.tail), (0, 0), where)
            checked += 1
        self.assertGreater(checked, 25_000)

    def test_the_cap_is_the_slots_and_a_one_row_composer_frames(self) -> None:
        sizes = (1, 2, 5, 6, 7, 9, 10, 24, 80)
        self.assertEqual([choice_cap(rows, 80) for rows in sizes], [0, 1, 1, 4, 5, 6, 6, 11, 29])
        self.assertEqual(choice_cap(24, MIN_COLS - 1), 1)  # too narrow for a slot: the composer's own row
        for rows in sizes:
            with self.subTest(rows=rows):
                frame = allocate(rows, 80, sticky=False, tail_rows=0, slot_rows=0, composer_rows=1)
                self.assertEqual(choice_cap(rows, 80), slot_cap(rows, 80) + frame.rule + frame.composer + frame.bottom_rule)

    def test_an_everyday_terminal(self) -> None:
        got = allocate(24, 80, sticky=True, tail_rows=3, slot_rows=5, composer_rows=2, composer_hidden=True)
        self.assertEqual(got, budget(sticky=1, tail=3, slot=5, mode=1, status=1))
        got = allocate(24, 80, sticky=True, tail_rows=100, slot_rows=100, composer_rows=1, composer_hidden=True)
        self.assertEqual(got, budget(sticky=1, tail=9, slot=11, mode=1, status=1))


class SlotCaretTests(unittest.TestCase):
    """With no composer, `slot_caret` (a row and a column within the slot) is the frame's caret: a picker's filter."""

    def test_the_caret_lands_on_its_slot_row_and_none_when_that_row_is_cut(self) -> None:
        slot = plain("header", "row 1", "row 2", " › re")
        frame = build_frame(cols=80, rows=24, composer=None, status=plain("status")[0], slot=slot,
                            slot_caret=(3, 5))
        row, col = frame.cursor  # type: ignore[misc]
        self.assertEqual(lines_text([frame.lines[row]]), [" › re"])
        self.assertEqual(col, 5)
        cut = build_frame(cols=80, rows=24, composer=None, status=plain("status")[0],
                          slot=slot + plain(*[f"more {n}" for n in range(20)]), slot_caret=(23, 5))
        self.assertIsNone(cut.cursor)  # its row is past the slot's cap
        self.assertIsNone(build_frame(cols=80, rows=24, composer=None, status=plain("status")[0],
                                      slot=slot).cursor)


class AgentListBudgetTests(unittest.TestCase):
    def test_the_list_takes_the_mode_rows_place_and_its_own_height(self) -> None:
        plain_row = allocate(24, 80, sticky=False, tail_rows=0, slot_rows=0, composer_rows=1)
        listed = allocate(24, 80, sticky=False, tail_rows=0, slot_rows=0, composer_rows=1, agent_list_rows=5)
        self.assertEqual((plain_row.mode, listed.mode), (1, 5))
        self.assertEqual((plain_row.total, listed.total), (5, 9))  # the region grew by the list's four extra rows
        self.assertEqual(plain_row.composer_top, listed.composer_top)  # under the composer: it keeps its row

    def test_the_cap_is_what_the_frame_and_a_full_composer_leave(self) -> None:
        self.assertEqual([agent_list_cap(rows, 80) for rows in (6, 9, 10, 11, 12, 13, 16, 24, 80)], [0, 0, 4, 5, 5, 6, 8, 14, 56])
        self.assertEqual(agent_list_cap(24, MIN_COLS - 1), 0)
        for rows in range(MODE_ROW_MIN_ROWS, 86):
            with self.subTest(rows=rows):
                self.assertGreaterEqual(agent_list_cap(rows, 80), 4)  # three agents and the hint, at least

    def test_no_list_of_any_length_breaks_the_budget_at_any_size(self) -> None:
        for rows, cols, composer_rows, asked in itertools.product(ROWS, COLS, COMPOSER_ROWS, (1, 2, 4, 8, 30, 500)):
            got = allocate(
                rows, cols, sticky=True, tail_rows=200, slot_rows=9, composer_rows=composer_rows, agent_list_rows=asked
            )
            without = allocate(rows, cols, sticky=True, tail_rows=200, slot_rows=9, composer_rows=composer_rows)
            where = f"{rows}x{cols} composer {composer_rows} list {asked}"
            self.assertLessEqual(got.total, max(rows - 1, 0), where)
            self.assertEqual(got.total, without.total, where)  # a full tail: already as tall as the screen allows
            self.assertEqual(got.mode, min(asked, agent_list_cap(rows, cols)), where)
            # the composer, its two rules and the status line are what they were without the list
            self.assertEqual(
                (got.rule, got.composer, got.bottom_rule, got.status),
                (without.rule, without.composer, without.bottom_rule, without.status),
                where,
            )
            if not without.mode:
                self.assertEqual(got, without, where)  # no mode row to replace: the list is not drawn

    def test_the_list_adds_its_extra_rows_and_takes_from_above_the_rule_only_what_the_screen_lacks(self) -> None:
        sizes = itertools.product(ROWS, COLS, COMPOSER_ROWS, (0, 1, 3, 9), (0, 2, 40), (1, 2, 4, 8, 30))
        for rows, cols, composer_rows, slot_rows, tail_rows, asked in sizes:
            parts = dict(sticky=True, tail_rows=tail_rows, slot_rows=slot_rows, composer_rows=composer_rows)
            without = allocate(rows, cols, **parts)  # type: ignore[arg-type]
            got = allocate(rows, cols, **parts, agent_list_rows=asked)  # type: ignore[arg-type]
            where = f"{rows}x{cols} composer {composer_rows} slot {slot_rows} tail {tail_rows} list {asked}"
            if not without.mode:
                self.assertEqual(got, without, where)
                continue
            self.assertEqual(got.mode, min(asked, agent_list_cap(rows, cols)), where)
            # The region grows by the list's extra rows up to `rows - 1`; past that the slot, sticky and tail give them.
            extra = got.mode - 1
            self.assertEqual(got.total, min(without.total + extra, rows - 1), where)
            taken_above = (without.slot + without.sticky + without.tail) - (got.slot + got.sticky + got.tail)
            self.assertEqual(taken_above, without.total + extra - got.total, where)
            self.assertEqual((got.rule, got.composer, got.bottom_rule, got.status), (1, without.composer, 1, 1), where)

    def test_compose_draws_the_list_where_the_mode_row_was_and_keeps_its_first_rows(self) -> None:
        parts = budget(rule=1, composer=1, bottom_rule=1, mode=3, status=1)
        region = compose(
            parts,
            sticky=None,
            tail=[],
            slot=[],
            rule=(Span("──"),),
            composer=plain("› a"),
            bottom_rule=(Span("__"),),
            mode=(Span("mode"),),
            status=(Span("status"),),
            agent_list=plain("main", "agent 1", "agent 2", "hint"),
        )
        self.assertEqual(lines_text(region), ["──", "› a", "__", "main", "agent 1", "agent 2", "status"])
        self.assertEqual(len(region), parts.total)

    def test_a_frame_with_the_list_puts_it_over_the_status_line_and_the_caret_on_the_composer(self) -> None:
        composer = Composer(text="hi", caret=2)
        status = (Span("status"),)
        resting = build_frame(cols=40, rows=24, composer=composer, status=status, mode_row=(Span("mode"),))
        offered: list[int] = []

        def agent_list(room: int) -> list[Line]:
            offered.append(room)
            return plain("main", "agent 1", "x" * 60)

        listed = build_frame(
            cols=40, rows=24, composer=composer, status=status, mode_row=(Span("mode"),), agent_list=agent_list
        )
        self.assertEqual(offered, [14])
        self.assertEqual(lines_text(list(resting.lines))[-4:], ["› hi", "─" * 40, "mode", "status"])
        self.assertEqual(
            lines_text(list(listed.lines))[-6:], ["› hi", "─" * 40, "main", "agent 1", "x" * 39 + "…", "status"]
        )
        self.assertEqual((listed.height, resting.height), (7, 5))
        self.assertEqual(listed.cursor, resting.cursor)


class ComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = (Span("──"),)
        self.bottom = (Span("__"),)
        self.mode = (Span("mode"),)
        self.status = (Span("status"),)

    def test_rows_stack_in_order_and_the_tail_keeps_its_last_rows(self) -> None:
        parts = budget(sticky=1, tail=2, slot=2, rule=1, composer=2, bottom_rule=1, mode=1, status=1)
        region = compose(
            parts,
            sticky=(Span("↳ you: hi"),),
            tail=plain("t1", "t2", "t3", "t4"),
            slot=plain("s1", "s2", "s3"),
            rule=self.rule,
            composer=plain("› a", "  b"),
            bottom_rule=self.bottom,
            mode=self.mode,
            status=self.status,
        )
        self.assertEqual(
            lines_text(region),
            ["↳ you: hi", "t3", "t4", "s1", "s2", "──", "› a", "  b", "__", "mode", "status"],
        )
        self.assertEqual(len(region), parts.total)

    def test_short_parts_are_filled_with_blank_rows_to_the_exact_count(self) -> None:
        parts = budget(sticky=1, tail=3, slot=2, rule=1, composer=2, bottom_rule=1, mode=1, status=1)
        region = compose(
            parts,
            sticky=None,
            tail=plain("t1"),
            slot=plain("s1"),
            rule=self.rule,
            composer=plain("› a"),
            bottom_rule=self.bottom,
            mode=(),
            status=self.status,
        )
        self.assertEqual(lines_text(region), ["", "", "", "t1", "", "s1", "──", "› a", "", "__", "", "status"])
        self.assertEqual(region[0], BLANK)

    def test_nothing_budgeted_draws_nothing(self) -> None:
        region = compose(
            budget(composer=1, status=1),
            sticky=(Span("sticky"),),
            tail=plain("t1"),
            slot=plain("s1"),
            rule=self.rule,
            composer=plain("› a"),
            bottom_rule=self.bottom,
            mode=self.mode,
            status=self.status,
        )
        self.assertEqual(lines_text(region), ["› a", "status"])

    def test_caret_row_counts_everything_above_the_composer(self) -> None:
        parts = budget(sticky=1, tail=2, slot=2, rule=1, composer=3, bottom_rule=1, mode=1, status=1)
        self.assertEqual([caret_row(parts, row) for row in (-1, 0, 1, 2, 7)], [6, 6, 7, 8, 8])


class FrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.status = (Span("status"),)

    def test_a_frame_is_exactly_as_tall_as_what_it_holds(self) -> None:
        composer = Composer(text="hi", caret=2)
        frame = build_frame(cols=40, rows=10, composer=composer, status=self.status, mode_row=(Span("mode"),))
        self.assertEqual(frame.height, 5)
        self.assertEqual(lines_text(list(frame.lines)), ["─" * 40, "› hi", "─" * 40, "mode", "status"])
        self.assertEqual(frame.cursor, (1, 4))

    def test_a_frame_grows_and_shrinks_with_its_content(self) -> None:
        composer = Composer()
        grown = build_frame(cols=40, rows=24, composer=composer, status=self.status, tail=plain("a", "b", "c"))
        self.assertEqual(grown.height, 8)
        self.assertEqual(lines_text(list(grown.lines))[:3], ["a", "b", "c"])
        shrunk = build_frame(cols=40, rows=24, composer=composer, status=self.status, tail=plain("c"))
        self.assertEqual((shrunk.height, lines_text(list(shrunk.lines))[0]), (6, "c"))

    def test_the_composer_is_rendered_within_its_cap(self) -> None:
        composer = Composer(text="1\n2\n3\n4\n5", caret=9)
        frame = build_frame(cols=40, rows=12, composer=composer, status=self.status)
        self.assertEqual(frame.budget.composer, 3)
        self.assertEqual(lines_text(list(frame.lines))[-6:], ["  3", "  4", "  5", "─" * 40, "", "status"])
        self.assertEqual(frame.cursor, (3, 3))

    def test_the_attachment_row_tops_the_composer_within_its_cap(self) -> None:
        chips = (Span("📎 shot.png 1280×720"),)
        for text, grows in (("", 1), ("1\n2\n3\n4\n5", 0)):
            with self.subTest(text=text):
                composer = Composer(text=text, caret=len(text))
                plain_frame = build_frame(cols=40, rows=12, composer=composer, status=self.status)
                with_chips = build_frame(
                    cols=40, rows=12, composer=composer, status=self.status, attachments=chips
                )
                self.assertEqual(with_chips.height, plain_frame.height + grows)  # a composer at its cap stays there
                self.assertEqual(with_chips.cursor, (plain_frame.cursor[0] + grows, plain_frame.cursor[1]))
                last_input = with_chips.budget.composer_top + with_chips.budget.composer - 1
                plain_last = plain_frame.budget.composer_top + plain_frame.budget.composer - 1
                self.assertEqual(lines_text([with_chips.lines[last_input]]), lines_text([plain_frame.lines[plain_last]]))
                self.assertLessEqual(with_chips.budget.composer, composer_cap(12))
                top = with_chips.budget.composer_top
                self.assertEqual(lines_text([with_chips.lines[top]]), ["📎 shot.png 1280×720"])
                self.assertEqual(lines_text([with_chips.lines[top - 1]]), ["─" * 40])

    def test_a_terminal_too_short_for_two_composer_rows_drops_the_attachment_row(self) -> None:
        frame = build_frame(
            cols=40, rows=7, composer=Composer(), status=self.status, attachments=(Span("📎 x"),)
        )
        self.assertNotIn("📎 x", lines_text(list(frame.lines)))

    def test_a_tiny_terminal_frame_is_composer_and_status(self) -> None:
        frame = build_frame(
            cols=12, rows=4, composer=Composer(), status=self.status, slot=plain("menu")
        )
        self.assertEqual(lines_text(list(frame.lines)), ["› ", "status"])
        self.assertEqual(frame.cursor, (0, 2))

    def test_the_rule_spans_the_width_in_the_rule_colour(self) -> None:
        self.assertEqual(rule_line(5, DEFAULT_THEME), (Span("─────", DEFAULT_THEME.rule),))

    def test_each_mode_draws_both_rules_in_its_own_glyph_and_colour(self) -> None:
        # Glyph and colour together: hue alone does not tell the modes apart on `mono`.
        expected = {
            "normal": ("─", DEFAULT_THEME.rule),
            MODE_ACCEPT: ("━", DEFAULT_THEME.mode_accept),
            MODE_PLAN: ("┄", DEFAULT_THEME.mode_plan),
            "not-a-mode": ("─", DEFAULT_THEME.rule),
        }
        for mode, (glyph, style) in expected.items():
            with self.subTest(mode=mode):
                frame = build_frame(
                    cols=30, rows=24, composer=Composer(), status=self.status, mode=mode
                )
                top, bottom = frame.budget.composer_top - 1, frame.budget.composer_top + frame.budget.composer
                for row in (top, bottom):
                    self.assertEqual(frame.lines[row], (Span(glyph * 30, style),))

    def test_the_placeholder_is_drawn_after_the_prompt_and_never_typed(self) -> None:
        composer = Composer()
        frame = build_frame(
            cols=40, rows=24, composer=composer, status=self.status, placeholder="Ask Hermes anything"
        )
        row = frame.lines[frame.budget.composer_top]
        self.assertEqual(lines_text([row]), ["› Ask Hermes anything"])
        self.assertEqual(row[-1], Span("Ask Hermes anything", DEFAULT_THEME.hint))
        self.assertEqual(frame.cursor, (frame.budget.composer_top, 2))
        self.assertEqual(composer.text, "")
        typed = build_frame(
            cols=40,
            rows=24,
            composer=Composer(text="x", caret=1),
            status=self.status,
            placeholder="Ask Hermes anything",
        )
        self.assertEqual(lines_text([typed.lines[typed.budget.composer_top]]), ["› x"])

    def test_the_sticky_line_shows_the_first_line_of_the_prompt(self) -> None:
        line = sticky_line("  fix the 한글 bug\nand more", 40, DEFAULT_THEME)
        self.assertEqual(lines_text([line]), ["↳ you: fix the 한글 bug"])
        self.assertEqual(lines_text([sticky_line("abcdefghij", 12, DEFAULT_THEME)]), ["↳ you: abcd…"])


if __name__ == "__main__":
    unittest.main()
