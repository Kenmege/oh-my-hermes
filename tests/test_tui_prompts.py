"""The OMH TUI's prompt slot for server-to-client requests."""

from __future__ import annotations

import unittest

from omh.tui import prompts
from omh.tui.keys import CHAR, DOWN, ENTER, ESCAPE, LEFT, Key, Paste
from omh.tui.theme import DEFAULT_THEME
from omh.tui.width import line_width

# Shapes recorded from Hermes 6c61c3e27d (tests/fixtures/tui_gateway).
APPROVAL = {
    "session_id": "f05e2691",
    "command": "rm -rf ./omh-m0-probe-dir",
    "pattern_key": "recursive delete",
    "pattern_keys": ["recursive delete"],
    "description": "recursive delete",
    "allow_permanent": True,
    "allow_session": True,
    "request_id": "421ace443ef640baa09f80731226646e",
    "choices": ["once", "session", "always", "deny"],
}
CLARIFY = {
    "session_id": "b6af062a",
    "questions": [
        {"qid": "q0", "question": "Which color do you prefer?", "choices": ["red (Recommended)", "blue"], "multi_select": False},
    ],
}
CTRL_C = Key(CHAR, "c", ctrl=True)


def slot(method: str, params: dict) -> prompts.PromptSlot:
    opened = prompts.open_request("srq-1", method, params)
    assert isinstance(opened, prompts.PromptSlot)
    return opened


class ApprovalTests(unittest.TestCase):
    def test_always_is_never_offered(self) -> None:
        self.assertEqual(slot("approval", APPROVAL).approval_choices(), ("once", "session", "deny"))

    def test_enter_on_an_untouched_approval_denies(self) -> None:
        answer = slot("approval", APPROVAL).handle_key(Key(ENTER))
        self.assertEqual(answer.frame, {"result": {"choice": "deny"}})

    def test_moving_then_enter_allows_once(self) -> None:
        approval = slot("approval", APPROVAL)
        approval.handle_key(Key(LEFT))
        approval.handle_key(Key(LEFT))
        self.assertEqual(approval.handle_key(Key(ENTER)).frame, {"result": {"choice": "once"}})

    def test_a_letter_can_only_deny(self) -> None:
        # A key typed for the composer must never allow a command: o, s, y and the rest do nothing.
        for char in ("o", "s", "y", "a", "O", "S", "Y"):
            with self.subTest(char=char):
                self.assertIsNone(slot("approval", APPROVAL).handle_key(Key(CHAR, char)))
        for char in ("d", "n", "D", "N"):
            with self.subTest(char=char):
                self.assertEqual(slot("approval", APPROVAL).handle_key(Key(CHAR, char)).frame, {"result": {"choice": "deny"}})

    def test_the_choice_row_names_arrows_and_enter_and_no_letter_shortcut(self) -> None:
        row = "".join(span.text for span in slot("approval", APPROVAL).lines(80, DEFAULT_THEME, max_rows=4)[-1])
        self.assertIn("allow once", row)
        self.assertIn("←→ then Enter · d or Esc denies", row)
        self.assertNotIn("o:", row)
        self.assertNotIn("s:", row)

    def test_escape_and_ctrl_c_send_an_explicit_deny(self) -> None:
        for key in (Key(ESCAPE), CTRL_C):
            with self.subTest(key=key):
                self.assertEqual(slot("approval", APPROVAL).handle_key(key).frame, {"result": {"choice": "deny"}})

    def test_a_session_choice_hermes_did_not_offer_is_not_shown(self) -> None:
        approval = slot("approval", {**APPROVAL, "choices": ["once", "deny"]})
        self.assertEqual(approval.approval_choices(), ("once", "deny"))

    def test_submitted_text_does_not_answer_an_approval(self) -> None:
        approval = slot("approval", APPROVAL)
        self.assertFalse(approval.wants_text)
        self.assertIsNone(approval.submit_text("yes"))

    def test_lines_show_command_and_choices_within_budget(self) -> None:
        rows = slot("approval", APPROVAL).lines(60, DEFAULT_THEME, max_rows=6)
        text = ["".join(span.text for span in row) for row in rows]
        self.assertIn("rm -rf ./omh-m0-probe-dir", "\n".join(text))
        self.assertIn("deny", text[-1])
        self.assertTrue(all(line_width(row) <= 60 for row in rows))

    def test_a_tight_budget_keeps_header_and_choices(self) -> None:
        rows = slot("approval", {**APPROVAL, "command": "x " * 200}).lines(40, DEFAULT_THEME, max_rows=2)
        text = ["".join(span.text for span in row) for row in rows]
        self.assertEqual(len(rows), 2)
        self.assertIn("Hermes asks", text[0])
        self.assertIn("deny", text[1])

    def test_a_long_command_shows_its_head_then_how_much_was_cut(self) -> None:
        # The shape a live run cut wrong: a multi-line `python3 -c` script, tail shown, head lost.
        command = 'python3 -c "\ndef is_prime(n):\n    if n < 2:\n        return False\n' + "    pass\n" * 8 + '"'
        approval = slot("approval", {**APPROVAL, "command": command, "description": "script execution via -e/-c flag"})
        rows = approval.lines(60, DEFAULT_THEME, max_rows=6)
        text = ["".join(span.text for span in row) for row in rows]
        total = command.count("\n") + 1
        self.assertEqual(len(rows), 6)
        self.assertIn("script execution", text[0])
        self.assertEqual(text[1:4], ['  python3 -c "', "def is_prime(n):", "    if n < 2:"])
        self.assertEqual(text[4], f"  … +{total - 3} more lines")
        self.assertEqual(rows[4][0].style, DEFAULT_THEME.dim)
        self.assertIn("deny", text[5])

    def test_one_spare_row_shows_the_commands_first_row(self) -> None:
        approval = slot("approval", {**APPROVAL, "command": "first line\nsecond line\nthird line"})
        text = ["".join(span.text for span in row) for row in approval.lines(60, DEFAULT_THEME, max_rows=3)]
        self.assertEqual(text[1], "  first line")
        self.assertIn("deny", text[2])

    def test_a_command_that_fits_is_shown_whole_with_no_marker(self) -> None:
        approval = slot("approval", {**APPROVAL, "command": "one\ntwo"})
        text = ["".join(span.text for span in row) for row in approval.lines(60, DEFAULT_THEME, max_rows=6)]
        self.assertEqual(text[1:3], ["  one", "two"])
        self.assertEqual(len(text), 4)
        self.assertFalse(any("more line" in row for row in text))

    def test_deny_stays_visible_at_every_width(self) -> None:
        approval = slot("approval", APPROVAL)
        for width in range(18, 120):
            with self.subTest(width=width):
                row = approval.lines(width, DEFAULT_THEME, max_rows=4)[-1]
                self.assertIn("deny", "".join(span.text for span in row))
                self.assertLessEqual(line_width(row), width)

class ClarifyTests(unittest.TestCase):
    def test_a_number_picks_that_choice(self) -> None:
        answer = slot("clarify", CLARIFY).handle_key(Key(CHAR, "2"))
        self.assertEqual(answer.frame, {"result": {"answers": {"q0": "blue"}}})

    def test_empty_submit_picks_the_highlighted_choice(self) -> None:
        clarify = slot("clarify", CLARIFY)
        clarify.handle_key(Key(DOWN))
        self.assertEqual(clarify.submit_text("").frame, {"result": {"answers": {"q0": "blue"}}})

    def test_typed_text_answers_freely(self) -> None:
        self.assertEqual(slot("clarify", CLARIFY).submit_text(" green ").frame, {"result": {"answers": {"q0": "green"}}})

    def test_multiple_questions_answer_once_at_the_end(self) -> None:
        params = {"questions": [{"qid": "a", "question": "A?"}, {"qid": "b", "question": "B?", "choices": ["x", "y"]}]}
        clarify = slot("clarify", params)
        self.assertIsNone(clarify.submit_text("first"))
        self.assertEqual(clarify.handle_key(Key(CHAR, "1")).frame, {"result": {"answers": {"a": "first", "b": "x"}}})

    def test_escape_cancels_with_no_answers(self) -> None:
        self.assertEqual(slot("clarify", CLARIFY).handle_key(Key(ESCAPE)).frame, {"result": {}})

    def test_the_hint_says_esc_cancels_because_it_cancels_every_question(self) -> None:
        text = "\n".join("".join(span.text for span in row) for row in slot("clarify", CLARIFY).lines(80, DEFAULT_THEME, 6))
        self.assertIn("Esc cancels", text)
        self.assertNotIn("skips", text)

    def test_a_clarify_with_no_question_is_declined_at_once_with_a_notice(self) -> None:
        for params in ({}, {"questions": []}, {"questions": [{"question": "no qid"}, "not a dict"]}):
            with self.subTest(params=params):
                opened = prompts.open_request("srq-2", "clarify", params)
                self.assertIsInstance(opened, prompts.ImmediateAnswer)
                self.assertEqual(opened.answer.frame, {"result": {}})
                self.assertIn("clarify", opened.notice)

    def test_only_the_digits_one_to_nine_pick_a_choice(self) -> None:
        for char in ("²", "٣", "0", "3", "١"):
            with self.subTest(char=char):
                self.assertIsNone(slot("clarify", CLARIFY).handle_key(Key(CHAR, char)))
        self.assertEqual(slot("clarify", CLARIFY).handle_key(Key(CHAR, "1")).frame, {"result": {"answers": {"q0": "red (Recommended)"}}})

    def test_paste_does_nothing_on_its_own(self) -> None:
        self.assertIsNone(slot("clarify", CLARIFY).handle_key(Paste("blue")))


class ValuePromptTests(unittest.TestCase):
    def test_sudo_and_secret_are_masked_and_answer_with_the_submitted_value(self) -> None:
        for method in ("sudo", "secret"):
            with self.subTest(method=method):
                value = slot(method, {"command": "apt install x", "env_var": "TOKEN_X"})
                self.assertTrue(value.masked)
                self.assertEqual(value.submit_text("hunter2").frame, {"result": {"value": "hunter2"}})

    def test_the_slot_keeps_no_copy_of_a_submitted_value(self) -> None:
        value = slot("sudo", {"command": "apt install x"})
        value.submit_text("hunter2")
        self.assertNotIn("hunter2", repr(value))

    def test_declining_a_value_prompt_sends_an_empty_value(self) -> None:
        self.assertEqual(slot("vault.code", {}).handle_key(Key(ESCAPE)).frame, {"result": {"value": ""}})


class ImmediateAnswerTests(unittest.TestCase):
    def test_desktop_only_requests_are_answered_unsupported_with_a_notice(self) -> None:
        for method in ("terminal.read", "preview.read", "preview.act", "window.read", "tour", "display.install.sudo"):
            with self.subTest(method=method):
                opened = prompts.open_request("srq-9", method, {})
                self.assertIsInstance(opened, prompts.ImmediateAnswer)
                self.assertEqual(opened.answer.request_id, "srq-9")
                self.assertEqual(opened.answer.frame["error"]["code"], -32601)
                self.assertIn(method, opened.notice)

    def test_vault_unlock_is_declined_with_an_empty_value_and_names_hermes(self) -> None:
        opened = prompts.open_request("srq-9", "vault.unlock_prompt", {"backend": "1password"})
        self.assertEqual(opened.answer.frame, {"result": {"value": ""}})
        self.assertIn("hermes --tui", opened.notice)

    def test_an_unknown_future_request_is_still_answered(self) -> None:
        opened = prompts.open_request("srq-9", "future.request", {})
        self.assertIsInstance(opened, prompts.ImmediateAnswer)
        self.assertEqual(opened.answer.frame["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
