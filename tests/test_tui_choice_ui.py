"""While the person picks from a list, the OMH TUI draws no composer, as Claude Code does.

An approval and a clarify question with choices are answered by picking: the
composer's two rules, its rows and the caret are gone, and the list stands
where the composer was, right above the mode row. The draft (its text, caret
and paste chips) is not touched while it is hidden, no key types into it, and
it comes back as it was the moment the question closes; a request waiting
behind the first one opens before any frame shows the composer again. A
question answered with text (a sudo password, a secret, a clarify question
without choices) keeps the composer as its answer field. These cases drive an
`App` whose gateway client only records, read the VT screen it painted, and
check the frames a terminal would show at every step.
"""

from __future__ import annotations

import unittest

from test_tui_modes import RecordingClient, make_app, type_text
from omh.tui.app import App
from omh.tui.gateway import ServerRequest
from omh.tui.keys import BACKSPACE, CHAR, DOWN, ENTER, ESCAPE, LEFT, UP, Key, Paste

APPROVAL = ServerRequest("srq-1", "approval", {"command": "rm -rf ./build", "description": "recursive delete"})
CLARIFY = ServerRequest(
    "srq-2", "clarify", {"questions": [{"qid": "q0", "question": "Which color?", "choices": ["red", "blue"]}]}
)
SUDO = ServerRequest("srq-3", "sudo", {"command": "apt install x"})
RULES = frozenset("─━┄╌")
LONG_PASTE = "\n".join(f"pasted line {n}" for n in range(6))


def drawn(app: App) -> list[str]:
    app._render()
    return app.terminal.screen.lines()  # type: ignore[attr-defined]


def status_row(screen: list[str]) -> int:
    (row,) = [index for index, line in enumerate(screen) if line.startswith("⚚ OMH")]
    return row


def rule_rows(screen: list[str]) -> list[int]:
    return [index for index, line in enumerate(screen) if line.strip() and set(line.strip()) <= RULES]


def composer_drawn(app: App, screen: list[str]) -> bool:
    """The composer's frame is on the screen: its two rules, and the caret shown."""
    return len(rule_rows(screen)) == 2 and app.terminal.screen.cursor_visible  # type: ignore[attr-defined]


def armed(app: App) -> None:
    """The open slot has been open past its arm delay."""
    app.slot_opened_at = app.clock() - 10.0


def press(app: App, *keys: Key | Paste) -> None:
    for key in keys:
        app._on_input(key)


def a_draft(app: App) -> tuple[str, int, dict[str, str]]:
    """A draft with a paste chip, the caret moved back into it; what the composer holds."""
    type_text(app, "fix the bug ")
    app._on_input(Paste(LONG_PASTE))
    type_text(app, " please")
    press(app, Key(LEFT), Key(LEFT), Key(LEFT))
    composer = app.composer
    assert composer.pastes, "the paste became a chip"
    return composer.text, composer.caret, dict(composer.pastes)


def held(app: App) -> tuple[str, int, dict[str, str]]:
    return app.composer.text, app.composer.caret, dict(app.composer.pastes)


def answered(client: RecordingClient, request_id: str) -> list[dict]:
    return [frame for answered_id, frame in client.answers if answered_id == request_id]


class HiddenComposerTests(unittest.TestCase):
    def test_an_approval_hides_the_composer_and_its_caret_and_gives_the_draft_back_as_it_was(self) -> None:
        app, client = make_app(busy=True)
        draft = a_draft(app)
        composer = app.composer
        before = drawn(app)
        self.assertTrue(composer_drawn(app, before))
        app._on_server_request(APPROVAL)
        during = drawn(app)
        self.assertEqual(rule_rows(during), [], during)
        self.assertFalse(app.terminal.screen.cursor_visible)  # type: ignore[attr-defined]
        self.assertFalse([row for row in during if row.startswith("› ")], during)
        mode_row = status_row(during) - 1
        self.assertEqual(during[mode_row - 3 : mode_row], ["▌ ❯ deny", "▌   allow once", "▌   allow this session"])
        self.assertIn("↑↓ pick · enter confirms · d or esc denies", during[mode_row])
        armed(app)
        press(app, Key(DOWN), Key(ENTER))
        self.assertEqual(answered(client, "srq-1"), [{"result": {"choice": "once"}}])
        after = drawn(app)
        self.assertIs(app.composer, composer)
        self.assertEqual(held(app), draft)
        self.assertTrue(composer_drawn(app, after))
        # The rows from the composer's top rule down are what they were; above them, the answer the person gave.
        top = rule_rows(before)[0]
        self.assertEqual(after[top:], before[top:])
        self.assertIn("✓ allowed once · rm -rf ./build", after[:top])
        self.assertFalse([row for row in after if "allow this session" in row or "Hermes asks" in row], after)

    def test_no_key_types_into_the_hidden_composer_and_a_paste_is_not_kept_for_it(self) -> None:
        app, client = make_app()
        draft = a_draft(app)
        app._on_server_request(APPROVAL)
        armed(app)
        press(app, Key(CHAR, "x"), Key(CHAR, "y"), Key(BACKSPACE), Paste("rm -rf /"), Paste("one\ntwo"), Key(LEFT))
        self.assertEqual(held(app), draft)
        self.assertEqual(client.answers, [])
        press(app, Key(ESCAPE))
        self.assertEqual(answered(client, "srq-1"), [{"result": {"choice": "deny"}}])
        self.assertEqual(held(app), draft)
        self.assertNotIn("rm -rf /", app.composer.text)

    def test_up_then_enter_typed_ahead_on_an_approval_denies(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "an earlier prompt")
        app._on_server_request(APPROVAL)
        armed(app)
        press(app, Key(UP), Key(UP), Key(ENTER))
        self.assertEqual(answered(client, "srq-1"), [{"result": {"choice": "deny"}}])

    def test_a_digit_on_an_approval_answers_nothing_and_moves_nothing(self) -> None:
        app, client = make_app(busy=True)
        app._on_server_request(APPROVAL)
        armed(app)
        for digit in "0123456789":
            press(app, Key(CHAR, digit))
        self.assertEqual(client.answers, [])
        self.assertEqual(app.slot.selected, 0)  # type: ignore[union-attr]
        self.assertIn("▌ ❯ deny", drawn(app))

    def test_a_waiting_request_opens_with_the_composer_still_hidden_and_it_returns_after_the_last(self) -> None:
        app, client = make_app(busy=True)
        draft = a_draft(app)
        composer = app.composer
        app._on_server_request(APPROVAL)
        app._on_server_request(CLARIFY)  # it waits behind the approval
        self.assertFalse(composer_drawn(app, drawn(app)))
        armed(app)
        written = len(app.terminal.writes)  # type: ignore[attr-defined]
        press(app, Key(CHAR, "d"))
        # Answering the approval opened the clarify before any frame was drawn: no frame shows the composer.
        self.assertEqual(app.terminal.writes[written:], [])  # type: ignore[attr-defined]
        self.assertEqual(app.slot.method, "clarify")  # type: ignore[union-attr]
        screen = drawn(app)
        self.assertFalse(composer_drawn(app, screen), screen)
        self.assertIn("▌ ❯ 1. red", screen)
        armed(app)
        press(app, Key(CHAR, "2"))
        self.assertEqual(answered(client, "srq-2"), [{"result": {"answers": {"q0": "blue"}}}])
        self.assertTrue(composer_drawn(app, drawn(app)))
        self.assertIs(app.composer, composer)
        self.assertEqual(held(app), draft)

    def test_a_text_question_waiting_behind_an_approval_gets_a_composer_of_its_own(self) -> None:
        app, client = make_app(busy=True)
        draft = a_draft(app)
        app._on_server_request(APPROVAL)
        app._on_server_request(SUDO)
        armed(app)
        press(app, Key(ESCAPE))
        screen = drawn(app)
        self.assertTrue(composer_drawn(app, screen), screen)  # the sudo's answer field, empty and masked
        self.assertEqual((app.composer.text, app.composer.masked), ("", True))
        armed(app)
        type_text(app, "hunter2")
        press(app, Key(ENTER))
        self.assertEqual(answered(client, "srq-3"), [{"result": {"value": "hunter2"}}])
        self.assertEqual(held(app), draft)
        self.assertNotIn("hunter2", app.composer.text)

    def test_a_steer_given_back_while_the_composer_is_hidden_lands_after_the_draft_and_keeps_its_chips(self) -> None:
        for request in (APPROVAL, CLARIFY, SUDO):
            with self.subTest(method=request.method):
                app, _client = make_app(busy=True)
                text, caret, pastes = a_draft(app)
                app._on_server_request(request)
                app._give_back("use the other file")
                if request.method == "sudo":
                    self.assertEqual(app.composer.text, "")  # never into the answer to Hermes
                app._close_slot()
                self.assertEqual(held(app), (f"{text}\nuse the other file", caret, pastes))
                sent = app.composer.expanded()
                self.assertIn(LONG_PASTE, sent)  # the chip still expands to its text

    def test_a_question_arriving_over_a_view_closes_it_and_hides_the_caret(self) -> None:
        app, _client = make_app(busy=True)
        app.transcript.add_notice("something to view")
        drawn(app)
        press(app, Key(CHAR, "o", ctrl=True))
        self.assertIsNotNone(app.pager)
        app._on_server_request(APPROVAL)
        self.assertIsNone(app.pager)
        screen = drawn(app)
        self.assertFalse(app.terminal.screen.cursor_visible)  # type: ignore[attr-defined]
        self.assertIn("▌ ❯ deny", screen)

    def test_a_text_only_question_keeps_the_composer_as_its_answer_field(self) -> None:
        for request in (SUDO, ServerRequest("srq-4", "clarify", {"questions": [{"qid": "q", "question": "Why?"}]})):
            with self.subTest(method=request.method):
                app, _client = make_app()
                draft = a_draft(app)
                app._on_server_request(request)
                screen = drawn(app)
                self.assertTrue(composer_drawn(app, screen), screen)
                self.assertEqual(app.composer.text, "")
                press(app, Key(ESCAPE))  # inside the arm delay: nothing
                armed(app)
                press(app, Key(ESCAPE))
                self.assertIsNone(app.slot)
                self.assertEqual(held(app), draft)


class ClarifyFieldTests(unittest.TestCase):
    def open(self) -> tuple[App, RecordingClient, tuple[str, int, dict[str, str]]]:
        app, client = make_app(busy=True)
        draft = a_draft(app)
        app._on_server_request(CLARIFY)
        armed(app)
        return app, client, draft

    def test_the_choices_stand_where_the_composer_was_with_a_row_to_type_an_answer(self) -> None:
        app, _client, _draft = self.open()
        screen = drawn(app)
        self.assertFalse(composer_drawn(app, screen))
        mode_row = status_row(screen) - 1
        self.assertEqual(
            screen[mode_row - 4 : mode_row],
            ["▌ ? Which color?", "▌ ❯ 1. red", "▌   2. blue", "▌   3. Other (type your answer)"],
        )

    def test_typing_a_character_opens_the_field_with_it_and_enter_sends_the_answer(self) -> None:
        app, client, draft = self.open()
        type_text(app, "green")
        screen = drawn(app)
        self.assertTrue(composer_drawn(app, screen), screen)
        self.assertIn("› green", screen)
        self.assertIn("▌ ❯ 3. Other (type your answer)", screen)
        press(app, Key(ENTER))
        self.assertEqual(answered(client, "srq-2"), [{"result": {"answers": {"q0": "green"}}}])
        self.assertEqual(held(app), draft)
        self.assertNotIn("green", app.composer.history)  # an answer to Hermes is no prompt to recall

    def test_up_goes_back_to_the_list_keeping_the_text_and_enter_on_the_last_row_brings_it_back(self) -> None:
        app, client, _draft = self.open()
        type_text(app, "gre")
        press(app, Key(UP))
        screen = drawn(app)
        self.assertFalse(composer_drawn(app, screen), screen)
        self.assertIn("▌ ❯ 2. blue", screen)
        press(app, Key(DOWN), Key(ENTER))
        screen = drawn(app)
        self.assertIn("› gre", screen)
        type_text(app, "en")
        press(app, Key(ENTER))
        self.assertEqual(answered(client, "srq-2"), [{"result": {"answers": {"q0": "green"}}}])

    def test_a_paste_opens_the_field_with_the_pasted_text(self) -> None:
        app, client, _draft = self.open()
        press(app, Paste("teal"), Key(ENTER))
        self.assertEqual(answered(client, "srq-2"), [{"result": {"answers": {"q0": "teal"}}}])

    def test_esc_declines_from_the_list_and_from_the_field(self) -> None:
        for typed in ("", "gr"):
            with self.subTest(typed=typed):
                app, client, draft = self.open()
                type_text(app, typed)
                press(app, Key(ESCAPE))
                self.assertEqual(answered(client, "srq-2"), [{"result": {}}])
                self.assertEqual(held(app), draft)

    def test_keys_inside_the_arm_delay_neither_pick_nor_open_the_field(self) -> None:
        app, client = make_app(busy=True)
        app._on_server_request(CLARIFY)
        type_text(app, "1g")
        press(app, Key(ENTER))
        self.assertEqual(client.answers, [])
        self.assertTrue(app.slot.picking)  # type: ignore[union-attr]
        self.assertEqual(app.composer.text, "")


class ResizeAndShortTerminalTests(unittest.TestCase):
    def test_a_resize_while_a_question_is_open_redraws_it_without_the_composer(self) -> None:
        app, client = make_app(busy=True)
        draft = a_draft(app)
        drawn(app)
        app._on_server_request(APPROVAL)
        drawn(app)
        for rows, cols in ((12, 40), (30, 100)):
            with self.subTest(rows=rows, cols=cols):
                app.terminal.screen.resize(rows, cols)  # type: ignore[attr-defined]
                app._on_resize()
                app._full_redraw()
                app.redraw_at = None
                screen = drawn(app)
                self.assertEqual(rule_rows(screen), [], screen)
                self.assertFalse(app.terminal.screen.cursor_visible)  # type: ignore[attr-defined]
                self.assertIn("▌ ❯ deny", screen)
                self.assertTrue(all(len(row) <= cols for row in screen))
        armed(app)
        press(app, Key(ESCAPE))
        self.assertEqual(answered(client, "srq-1"), [{"result": {"choice": "deny"}}])
        self.assertTrue(composer_drawn(app, drawn(app)))
        self.assertEqual(held(app), draft)

    def test_a_short_or_narrow_terminal_keeps_the_highlighted_choice_and_a_way_to_deny(self) -> None:
        for rows, cols in ((24, 40), (24, 60), (9, 60), (7, 40), (6, 40), (5, 40), (3, 40)):
            with self.subTest(rows=rows, cols=cols):
                app, _client = make_app(busy=True, rows=rows, cols=cols)
                app.effort = "high"
                app._on_server_request(APPROVAL)
                screen = drawn(app)
                self.assertTrue(any(row.startswith("▌ ❯ deny") for row in screen), screen)
                self.assertTrue(all(len(row) <= cols for row in screen))
                self.assertEqual(rule_rows(screen), [], screen)
                if rows >= 9:  # below that a spare row shows the command's head rather than the keys
                    self.assertTrue(any("esc denies" in row for row in screen), screen)  # the mode row, or a row of its own

    def test_below_ten_rows_the_keys_take_a_slot_row_and_not_a_composer_row(self) -> None:
        app, _client = make_app(busy=True, rows=9, cols=80)
        app._on_server_request(APPROVAL)
        screen = drawn(app)
        status = status_row(screen)
        self.assertEqual(screen[status - 1], "▌   ↑↓ pick · enter confirms · d or esc denies")
        self.assertEqual(screen[status - 4 : status - 1], ["▌ ❯ deny", "▌   allow once", "▌   allow this session"])


if __name__ == "__main__":
    unittest.main()
