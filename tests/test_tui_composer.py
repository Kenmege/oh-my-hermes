"""The composer's editing state machine and its rendering (`omh.tui.composer`).

Every key the design lists has a test, and each editing key is also run over
text a terminal user actually types: Hangul (composed, and decomposed as a
macOS paste delivers it), emoji with a ZWJ, a skin tone or a flag, and a
combining mark. Expectations are literals; the caret position after a wrap is
counted by hand in cells.
"""

from __future__ import annotations

import unicodedata
import unittest

from _tui_lines import lines_text
from omh.tui.composer import (
    CANCEL,
    COMPLETE,
    EXIT,
    INTERRUPT,
    NONE,
    SUBMIT,
    Composer,
    ComposerAction,
)
from omh.tui.keys import (
    BACKSPACE,
    CHAR,
    DELETE,
    DOWN,
    END,
    ENTER,
    ESCAPE,
    HOME,
    INSERT,
    LEFT,
    PAGE_UP,
    RIGHT,
    TAB,
    UNKNOWN,
    UP,
    Key,
    Paste,
)
from omh.tui.style import Style

HANGUL = "한글"
HANGUL_NFD = unicodedata.normalize("NFD", HANGUL)
FAMILY = "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466"
THUMBS_UP_TONED = "\U0001F44D\U0001F3FD"
FLAG_KR = "\U0001F1F0\U0001F1F7"
Q_ACUTE = "q́"


def ctrl(char: str) -> Key:
    return Key(CHAR, char, ctrl=True)


def typed(composer: Composer, text: str) -> None:
    for ch in text:
        composer.handle(Key(CHAR, ch))


def press(composer: Composer, *keys: Key) -> ComposerAction:
    action = ComposerAction(NONE)
    for key in keys:
        action = composer.handle(key)
    return action


def at(text: str, caret: int) -> Composer:
    return Composer(text=text, caret=caret)


class InsertTests(unittest.TestCase):
    def test_printable_keys_insert_at_the_caret(self) -> None:
        composer = Composer()
        typed(composer, "helo")
        press(composer, Key(LEFT))
        typed(composer, "l")
        self.assertEqual((composer.text, composer.caret), ("hello", 4))

    def test_hangul_and_emoji_insert_as_typed(self) -> None:
        composer = Composer()
        typed(composer, HANGUL)
        composer.handle(Key(CHAR, FAMILY))
        self.assertEqual(composer.text, HANGUL + FAMILY)
        self.assertEqual(composer.caret, len(HANGUL + FAMILY))

    def test_alt_chords_and_unbound_ctrl_keys_change_nothing(self) -> None:
        composer = at("abc", 1)
        for key in (Key(CHAR, "x", alt=True), ctrl("q"), Key(INSERT), Key(PAGE_UP), Key(UNKNOWN)):
            with self.subTest(key=key):
                self.assertEqual(composer.handle(key), ComposerAction(NONE))
                self.assertEqual((composer.text, composer.caret), ("abc", 1))

    def test_a_decomposed_paste_is_stored_composed(self) -> None:
        composer = Composer()
        composer.handle(Paste(HANGUL_NFD))
        self.assertEqual(composer.text, HANGUL)
        self.assertEqual(composer.caret, 2)

    def test_construction_normalizes_and_a_caret_inside_a_syllable_lands_after_it(self) -> None:
        composer = Composer(text=HANGUL_NFD, caret=4)  # after the first jamo of the second syllable
        self.assertEqual((composer.text, composer.caret), (HANGUL, 2))


class NewlineAndSubmitTests(unittest.TestCase):
    def test_enter_submits_the_text_and_clears(self) -> None:
        composer = Composer()
        typed(composer, "hi")
        self.assertEqual(composer.handle(Key(ENTER)), ComposerAction(SUBMIT, "hi"))
        self.assertEqual((composer.text, composer.caret, composer.history), ("", 0, ["hi"]))

    def test_enter_on_empty_input_submits_nothing_into_history(self) -> None:
        composer = Composer()
        self.assertEqual(composer.handle(Key(ENTER)), ComposerAction(SUBMIT, ""))
        typed(composer, "   ")
        press(composer, Key(ENTER))
        self.assertEqual(composer.history, [])

    def test_a_repeated_submit_is_stored_once(self) -> None:
        composer = Composer()
        for text in ("one", "one", "two", "one"):
            typed(composer, text)
            press(composer, Key(ENTER))
        self.assertEqual(composer.history, ["one", "two", "one"])

    def test_alt_enter_and_ctrl_j_insert_a_newline(self) -> None:
        composer = Composer()
        typed(composer, "a")
        press(composer, Key(ENTER, alt=True))
        typed(composer, "b")
        press(composer, ctrl("j"))
        typed(composer, "c")
        self.assertEqual(composer.text, "a\nb\nc")
        self.assertEqual(composer.handle(Key(ENTER)), ComposerAction(SUBMIT, "a\nb\nc"))

    def test_enter_right_after_a_backslash_turns_it_into_a_newline(self) -> None:
        composer = Composer()
        typed(composer, "one\\")
        self.assertEqual(composer.handle(Key(ENTER)), ComposerAction(NONE))
        typed(composer, "two")
        self.assertEqual((composer.text, composer.caret), ("one\ntwo", 7))
        self.assertEqual(composer.handle(Key(ENTER)), ComposerAction(SUBMIT, "one\ntwo"))

    def test_a_backslash_away_from_the_caret_or_in_masked_input_does_not_hold_enter(self) -> None:
        composer = Composer(text="a\\ b", caret=4)
        self.assertEqual(composer.handle(Key(ENTER)), ComposerAction(SUBMIT, "a\\ b"))
        masked = Composer(masked=True)
        typed(masked, "pw\\")
        self.assertEqual(masked.handle(Key(ENTER)), ComposerAction(SUBMIT, "pw\\"))

    def test_replace_swaps_a_range_and_parks_the_caret_after_it(self) -> None:
        composer = Composer(text="see @sr now", caret=7)
        composer.replace(4, 7, "@file:src/" + HANGUL_NFD + ".md")
        self.assertEqual(composer.text, f"see @file:src/{HANGUL}.md now")
        self.assertEqual(composer.caret, len(f"see @file:src/{HANGUL}.md"))


class DeleteTests(unittest.TestCase):
    def test_backspace_removes_one_cluster(self) -> None:
        for unit in ("x", "한", FAMILY, THUMBS_UP_TONED, FLAG_KR, Q_ACUTE):
            with self.subTest(unit=unit):
                composer = at("a" + unit, len("a" + unit))
                press(composer, Key(BACKSPACE))
                self.assertEqual((composer.text, composer.caret), ("a", 1))

    def test_backspace_after_a_decomposed_paste_removes_a_whole_syllable(self) -> None:
        composer = Composer()
        composer.handle(Paste(HANGUL_NFD))
        press(composer, Key(BACKSPACE))
        self.assertEqual(composer.text, "한")

    def test_delete_removes_the_cluster_after_the_caret(self) -> None:
        for unit in ("x", "한", FAMILY, THUMBS_UP_TONED, FLAG_KR, Q_ACUTE):
            with self.subTest(unit=unit):
                composer = at(unit + "b", 0)
                press(composer, Key(DELETE))
                self.assertEqual((composer.text, composer.caret), ("b", 0))

    def test_deleting_past_the_ends_changes_nothing(self) -> None:
        composer = at("ab", 0)
        press(composer, Key(BACKSPACE))
        self.assertEqual((composer.text, composer.caret), ("ab", 0))
        composer.caret = 2
        press(composer, Key(DELETE))
        self.assertEqual((composer.text, composer.caret), ("ab", 2))

    def test_ctrl_w_deletes_the_word_before_the_caret(self) -> None:
        composer = at("say 안녕 하세요  ", len("say 안녕 하세요  "))
        press(composer, ctrl("w"))
        self.assertEqual(composer.text, "say 안녕 ")
        press(composer, ctrl("w"), ctrl("w"))
        self.assertEqual((composer.text, composer.caret), ("", 0))

    def test_ctrl_u_and_ctrl_k_kill_to_the_line_edges(self) -> None:
        composer = at("one\ntwo three\nfour", len("one\ntwo "))
        press(composer, ctrl("k"))
        self.assertEqual(composer.text, "one\ntwo \nfour")
        press(composer, ctrl("u"))
        self.assertEqual((composer.text, composer.caret), ("one\n\nfour", 4))

    def test_ctrl_u_and_ctrl_k_at_a_line_edge_remove_the_newline(self) -> None:
        composer = at("one\ntwo", 4)
        press(composer, ctrl("u"))
        self.assertEqual((composer.text, composer.caret), ("onetwo", 3))
        press(composer, ctrl("e"), ctrl("k"))
        self.assertEqual(composer.text, "onetwo")
        composer = at("one\ntwo", 3)
        press(composer, ctrl("k"))
        self.assertEqual((composer.text, composer.caret), ("onetwo", 3))


class MoveTests(unittest.TestCase):
    def test_left_and_right_step_one_cluster(self) -> None:
        text = "a" + FAMILY + "한" + FLAG_KR
        composer = at(text, 0)
        stops = []
        for _ in range(5):
            press(composer, Key(RIGHT))
            stops.append(composer.caret)
        self.assertEqual(stops, [1, 1 + len(FAMILY), 2 + len(FAMILY), len(text), len(text)])
        for _ in range(5):
            press(composer, Key(LEFT))
        self.assertEqual(composer.caret, 0)

    def test_ctrl_and_alt_arrows_step_one_word(self) -> None:
        text = "hello  한글 world"
        for modifier in ("ctrl", "alt"):
            with self.subTest(modifier=modifier):
                composer = at(text, 0)
                stops = []
                for _ in range(4):
                    press(composer, Key(RIGHT, **{modifier: True}))
                    stops.append(composer.caret)
                self.assertEqual(stops, [5, 9, 15, 15])
                stops = []
                for _ in range(4):
                    press(composer, Key(LEFT, **{modifier: True}))
                    stops.append(composer.caret)
                self.assertEqual(stops, [10, 7, 0, 0])

    def test_home_end_ctrl_a_ctrl_e_use_the_logical_line(self) -> None:
        composer = at("one\ntwo three\nfour", len("one\ntwo t"))
        press(composer, Key(HOME))
        self.assertEqual(composer.caret, 4)
        press(composer, Key(END))
        self.assertEqual(composer.caret, 13)
        press(composer, ctrl("a"))
        self.assertEqual(composer.caret, 4)
        press(composer, ctrl("e"))
        self.assertEqual(composer.caret, 13)

    def test_up_and_down_move_between_lines_keeping_the_cell_column(self) -> None:
        composer = at("abcdef\n한글\nxy", 5)  # after "abcde", column 5
        press(composer, Key(DOWN))
        self.assertEqual(composer.caret, 9)  # column 4: after "한글"
        press(composer, Key(DOWN))
        self.assertEqual(composer.caret, 12)  # line "xy" ends at column 2
        press(composer, Key(UP))
        self.assertEqual(composer.caret, 8)  # column 2: after "한"
        press(composer, Key(UP))
        self.assertEqual(composer.caret, 2)

    def test_up_and_down_follow_wrapped_rows_after_a_render(self) -> None:
        composer = at("abcdefghij", 10)
        composer.render(6, 5)  # "› " + 4 cells per row: abcd / efgh / ij
        press(composer, Key(UP))
        self.assertEqual(composer.caret, 6)
        press(composer, Key(UP))
        self.assertEqual(composer.caret, 2)
        press(composer, Key(DOWN), Key(DOWN))
        self.assertEqual(composer.caret, 10)

    def test_a_wide_glyph_straddling_the_column_lands_before_it(self) -> None:
        composer = at("abc\n한글", 3)
        press(composer, Key(DOWN))
        self.assertEqual(composer.caret, 5)  # column 3 falls inside "글": stop after "한"


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.composer = Composer(history=["first", "second\nline"])

    def test_up_at_the_top_recalls_older_entries_and_stops_at_the_oldest(self) -> None:
        typed(self.composer, "draft")
        press(self.composer, Key(UP))
        self.assertEqual((self.composer.text, self.composer.history_index), ("second\nline", 1))
        press(self.composer, Key(UP))  # caret was at the end: first up moves within the entry
        self.assertEqual(self.composer.text, "second\nline")
        press(self.composer, Key(UP))
        self.assertEqual((self.composer.text, self.composer.history_index), ("first", 0))
        press(self.composer, Key(UP))
        self.assertEqual((self.composer.text, self.composer.history_index), ("first", 0))

    def test_down_at_the_bottom_returns_to_the_draft(self) -> None:
        typed(self.composer, "draft")
        press(self.composer, Key(UP), Key(UP), Key(UP))
        press(self.composer, Key(DOWN))
        self.assertEqual(self.composer.text, "second\nline")
        press(self.composer, Key(DOWN))
        self.assertEqual((self.composer.text, self.composer.history_index), ("draft", None))
        self.assertEqual(self.composer.caret, 5)
        press(self.composer, Key(DOWN))
        self.assertEqual(self.composer.text, "draft")

    def test_submitting_a_recalled_entry_appends_and_resets(self) -> None:
        press(self.composer, Key(UP), Key(UP), Key(UP))
        self.assertEqual(self.composer.handle(Key(ENTER)), ComposerAction(SUBMIT, "first"))
        self.assertEqual(self.composer.history, ["first", "second\nline", "first"])
        self.assertIsNone(self.composer.history_index)

    def test_empty_history_leaves_the_text_alone(self) -> None:
        composer = at("abc", 1)
        press(composer, Key(UP), Key(DOWN))
        self.assertEqual((composer.text, composer.history_index), ("abc", None))


class ActionKeyTests(unittest.TestCase):
    def test_tab_completes_and_shift_tab_does_not(self) -> None:
        composer = at("/he", 3)
        self.assertEqual(composer.handle(Key(TAB)), ComposerAction(COMPLETE))
        self.assertEqual(composer.handle(Key(TAB, shift=True)), ComposerAction(NONE))
        self.assertEqual(composer.text, "/he")

    def test_escape_cancels_and_keeps_the_text(self) -> None:
        composer = at("draft", 5)
        self.assertEqual(composer.handle(Key(ESCAPE)), ComposerAction(CANCEL))
        self.assertEqual(composer.text, "draft")

    def test_ctrl_c_clears_text_then_interrupts(self) -> None:
        composer = at("draft", 5)
        self.assertEqual(composer.handle(ctrl("c")), ComposerAction(CANCEL))
        self.assertEqual((composer.text, composer.caret), ("", 0))
        self.assertEqual(composer.handle(ctrl("c")), ComposerAction(INTERRUPT))

    def test_ctrl_d_exits_on_empty_and_deletes_forward_otherwise(self) -> None:
        composer = at("ab", 0)
        self.assertEqual(composer.handle(ctrl("d")), ComposerAction(NONE))
        self.assertEqual(composer.text, "b")
        press(composer, ctrl("d"))
        self.assertEqual(composer.handle(ctrl("d")), ComposerAction(EXIT))


class PasteTests(unittest.TestCase):
    def test_a_multi_line_paste_keeps_its_lines_and_lands_at_the_caret(self) -> None:
        composer = at("[]", 1)
        composer.handle(Paste("one\r\ntwo\rthree\n"))
        self.assertEqual(composer.text, "[one\ntwo\nthree\n]")
        self.assertEqual(composer.caret, len("[one\ntwo\nthree\n"))

    def test_control_characters_are_dropped_and_tabs_kept(self) -> None:
        composer = Composer()
        composer.handle(Paste("a\x1b[31mb\x07\tc"))
        self.assertEqual(composer.text, "a[31mb\tc")

    def test_a_tab_draws_as_four_cells(self) -> None:
        composer = at("\tx", 2)
        lines, caret = composer.render(40, 3)
        self.assertEqual(lines_text(lines), ["›     x"])
        self.assertEqual(caret, (0, 7))


class MaskedTests(unittest.TestCase):
    def test_masked_input_draws_one_bullet_per_cluster(self) -> None:
        composer = Composer(masked=True)
        composer.handle(Paste("p" + HANGUL_NFD + FAMILY))
        lines, caret = composer.render(40, 3)
        self.assertEqual(lines_text(lines), ["› ••••"])
        self.assertEqual(caret, (0, 6))
        press(composer, Key(LEFT), Key(LEFT))
        self.assertEqual(composer.render(40, 3)[1], (0, 4))

    def test_masked_input_wraps_by_bullets(self) -> None:
        composer = Composer(masked=True)
        typed(composer, "한글한글한글")  # 12 cells of Hangul, 6 bullets
        lines, caret = composer.render(6, 5)
        self.assertEqual(lines_text(lines), ["› ••••", "  ••"])
        self.assertEqual(caret, (1, 4))

    def test_masked_input_never_enters_or_browses_history(self) -> None:
        composer = Composer(history=["old"], masked=True)
        typed(composer, "secret")
        press(composer, Key(UP))
        self.assertEqual(composer.text, "secret")
        self.assertEqual(composer.handle(Key(ENTER)), ComposerAction(SUBMIT, "secret"))
        self.assertEqual(composer.history, ["old"])

    def test_repr_shows_masked_text_as_bullets(self) -> None:
        composer = Composer(masked=True)
        typed(composer, "hunter2")
        self.assertNotIn("hunter2", repr(composer))
        self.assertIn("•••••••", repr(composer))
        composer.masked = False
        self.assertIn("hunter2", repr(composer))

    def test_a_submit_with_history_recording_off_stays_out_of_history(self) -> None:
        composer = Composer(history=["old"], records_history=False)
        typed(composer, "blue")
        self.assertEqual(composer.handle(Key(ENTER)), ComposerAction(SUBMIT, "blue"))
        self.assertEqual(composer.history, ["old"])

    def test_masked_input_refuses_newlines(self) -> None:
        composer = Composer(masked=True)
        typed(composer, "a")
        press(composer, Key(ENTER, alt=True), ctrl("j"))
        composer.handle(Paste("b\nc\n"))
        self.assertEqual(composer.text, "abc")


class RenderTests(unittest.TestCase):
    def test_empty_input_draws_the_prompt_with_the_caret_after_it(self) -> None:
        lines, caret = Composer().render(40, 3)
        self.assertEqual(lines_text(lines), ["› "])
        self.assertEqual(caret, (0, 2))

    def test_the_prompt_is_styled_and_continuation_rows_are_indented(self) -> None:
        style = Style(fg="#ff0000", bold=True)
        lines, _ = at("ab\ncd", 0).render(40, 3, prompt="> ", prompt_style=style)
        self.assertEqual(lines_text(lines), ["> ab", "  cd"])
        self.assertEqual(lines[0][0].style, style)
        self.assertEqual(lines[1][0].style, Style())

    def test_wrapping_is_by_cells_and_never_splits_a_wide_glyph(self) -> None:
        composer = at("ab한글cd", 6)
        lines, caret = composer.render(7, 5)  # 5 cells per row
        self.assertEqual(lines_text(lines), ["› ab한", "  글cd"])
        self.assertEqual(caret, (1, 6))

    def test_the_caret_after_a_full_row_starts_a_new_row(self) -> None:
        composer = at("abcd", 4)
        lines, caret = composer.render(6, 5)  # exactly 4 cells per row
        self.assertEqual(lines_text(lines), ["› abcd", "  "])
        self.assertEqual(caret, (1, 2))
        composer.caret = 3
        self.assertEqual(composer.render(6, 5)[1], (0, 5))

    def test_the_caret_at_a_soft_wrap_sits_at_the_next_row_start(self) -> None:
        composer = at("abcdef", 4)
        lines, caret = composer.render(6, 5)
        self.assertEqual(lines_text(lines), ["› abcd", "  ef"])
        self.assertEqual(caret, (1, 2))

    def test_the_caret_col_counts_wide_glyphs_and_emoji(self) -> None:
        composer = at("한" + FAMILY + "x", len("한" + FAMILY))
        self.assertEqual(composer.render(40, 3)[1], (0, 6))

    def test_the_window_scrolls_to_keep_the_caret_visible(self) -> None:
        composer = Composer()
        composer.handle(Paste("1\n2\n3\n4\n5\n6"))
        lines, caret = composer.render(40, 3)
        self.assertEqual(lines_text(lines), ["  4", "  5", "  6"])
        self.assertEqual(caret, (2, 3))
        press(composer, Key(UP), Key(UP))  # still inside the window: no scroll
        self.assertEqual(lines_text(composer.render(40, 3)[0]), ["  4", "  5", "  6"])
        press(composer, Key(UP))
        lines, caret = composer.render(40, 3)
        self.assertEqual(lines_text(lines), ["  3", "  4", "  5"])
        self.assertEqual(caret, (0, 3))
        press(composer, Key(UP), Key(UP))
        lines, caret = composer.render(40, 3)
        self.assertEqual(lines_text(lines), ["› 1", "  2", "  3"])
        self.assertEqual(caret, (0, 3))

    def test_the_window_refills_when_rows_go_away(self) -> None:
        composer = Composer()
        composer.handle(Paste("1\n2\n3\n4\n5\n6"))
        composer.render(40, 3)
        press(composer, Key(BACKSPACE), Key(BACKSPACE))  # drop "\n6"
        lines, caret = composer.render(40, 3)
        self.assertEqual(lines_text(lines), ["  3", "  4", "  5"])
        self.assertEqual(caret, (2, 3))


if __name__ == "__main__":
    unittest.main()
