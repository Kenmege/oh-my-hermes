"""While the person picks from a list, the OMH TUI draws no composer, as Claude Code does.

An approval and a clarify question with choices are answered by picking, and
the session, rewind and model pickers are lists too: the composer's two rules
and its rows are gone, and the list stands where the composer was, right above
the mode row. A question shows no caret; a picker shows it at the end of its
filter row, its last, where its typing goes. The draft (its
text, caret and paste chips) is not touched while it is hidden, no key types
into it, and it comes back as it was the moment the list closes; a request
waiting behind the first one opens before any frame shows the composer again.
A question answered with text (a sudo password, a secret, a clarify question
without choices) keeps the composer as its answer field. These cases drive an
`App` whose gateway client only records, read the VT screen it painted, and
check the frames a terminal would show at every step.
"""

from __future__ import annotations

import unittest

from _tui_lines import line_text
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

    def test_the_caret_comes_back_on_the_pickers_filter_row_after_a_view(self) -> None:
        app, _client = make_app()
        app.transcript.add_notice("something to view")
        app._open_session_picker(SESSIONS)
        screen = drawn(app)
        filter_row = status_row(screen) - 2
        press(app, Key(CHAR, "o", ctrl=True))  # the details view, over the picker
        self.assertIsNotNone(app.pager)
        press(app, Key(CHAR, "q"))
        self.assertIsNone(app.pager)
        self.assertTrue(app.terminal.screen.cursor_visible)  # type: ignore[attr-defined]
        drawn(app)
        self.assertEqual(app.terminal.screen.cursor, (filter_row, 3))  # type: ignore[attr-defined]

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

    def test_two_text_questions_back_to_back_give_the_draft_back_unmasked(self) -> None:
        # The draft is back before the waiting question opens, so the second one sets the draft aside, not the first's field.
        second = (ServerRequest("srq-8", "sudo", {"command": "make install"}),
                  ServerRequest("srq-9", "clarify", {"questions": [{"qid": "q", "question": "Why?"}]}),
                  ServerRequest("srq-10", "secret", {"env_var": "TOKEN"}))
        for waiting in second:
            with self.subTest(method=waiting.method):
                app, client = make_app(busy=True)
                draft = a_draft(app)
                app._on_server_request(SUDO)
                app._on_server_request(waiting)
                armed(app)
                type_text(app, "hunter2")
                press(app, Key(ENTER))
                self.assertIs(app.slot.request_id, waiting.id)  # type: ignore[union-attr]
                self.assertEqual(app.composer.text, "")
                armed(app)
                press(app, Key(ESCAPE))
                self.assertIsNone(app.slot)
                self.assertEqual(held(app), draft)
                self.assertFalse(app.composer.masked)

    def test_leaving_a_view_opened_over_the_composer_shows_the_caret_again_at_once(self) -> None:
        app, _client = make_app()
        app.transcript.add_notice("something to view")
        type_text(app, "a draft")
        drawn(app)
        press(app, Key(CHAR, "o", ctrl=True))
        self.assertIsNotNone(app.pager)
        press(app, Key(CHAR, "q"))
        self.assertIsNone(app.pager)
        self.assertTrue(app.terminal.screen.cursor_visible)  # type: ignore[attr-defined]  # before any repaint

    def test_text_that_arrives_while_a_text_question_is_open_goes_to_the_draft_not_the_answer(self) -> None:
        # A drop's answer, an image's remainder or a command's prefill is the person's draft, never an answer to Hermes.
        from omh.tui.slash import parse_slash

        shot = {"attached": True, "path": "/h/images/shot.png", "name": "shot.png", "width": 4, "height": 3,
                "remainder": "what is this?"}
        arrivals = {
            "drop": (lambda app: app._on_drop(app.sid, "/Users/me/notes.txt", {"matched": False}), "/Users/me/notes.txt"),
            "drop refused": (lambda app: app._on_drop_refused("/Users/me/notes.txt"), "/Users/me/notes.txt"),
            "image remainder": (lambda app: app._on_image_attached(app.sid, shot), "what is this?"),
            "prefill": (lambda app: app._on_dispatch(parse_slash("/draft x"), {"type": "prefill", "message": "drafted"}, 0),
                        None),
        }
        for request in (SUDO, ServerRequest("srq-4", "clarify", {"questions": [{"qid": "q", "question": "Why?"}]})):
            for name, (arrive, appended) in arrivals.items():
                with self.subTest(method=request.method, arrival=name):
                    app, client = make_app(busy=True)
                    type_text(app, "my draft")
                    app._on_server_request(request)
                    arrive(app)
                    self.assertEqual(app.composer.text, "")  # the answer field holds nothing it did not type
                    armed(app)
                    press(app, Key(ESCAPE))
                    self.assertEqual(app.composer.text, "drafted" if appended is None else "my draft" + appended)
                    self.assertNotIn("notes.txt", str(client.answers))

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


class ModeShownTests(unittest.TestCase):
    """While a list hides the composer and its rules, the mode row (or, below 10 rows, the status line) shows the mode."""

    def test_the_mode_row_or_the_status_line_names_the_mode_while_a_list_hides_the_composer(self) -> None:
        from test_tui_modes import show_mode
        from omh.tui.frame import MODE_ACCEPT, MODE_PLAN

        for mode, label, chip in ((MODE_ACCEPT, "⏵⏵ accept all on", "⏵⏵ accept"), (MODE_PLAN, "⏸ plan mode on", "⏸ plan")):
            for opener in (lambda app: app._on_server_request(CLARIFY), lambda app: app._open_session_picker(SESSIONS)):
                with self.subTest(mode=mode):
                    app, _client = make_app(busy=True)
                    show_mode(app, mode)
                    opener(app)
                    screen = drawn(app)
                    self.assertEqual(rule_rows(screen), [])  # no rule carries the mode now
                    self.assertTrue(screen[status_row(screen) - 1].startswith(label), screen)
                    short, _client = make_app(busy=True, rows=9)
                    show_mode(short, mode)
                    opener(short)
                    screen = drawn(short)
                    self.assertIn(chip, screen[status_row(screen)])


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


SESSIONS = {"sessions": [
    {"id": "s1", "title": "Release notes", "message_count": 3, "started_at": 0},
    {"id": "s2", "title": "Refactor the router", "message_count": 9, "started_at": 0},
]}
HISTORY = {"messages": [
    {"role": "user", "row_id": 1, "text": "first prompt"},
    {"role": "assistant", "text": "a reply"},
    {"role": "user", "row_id": 3, "text": "second prompt"},
]}
MODELS = {"model": "m1", "provider": "p", "providers": [{"slug": "p", "name": "Provider", "models": ["m1", "m2"]}]}


class PickerTests(unittest.TestCase):
    """The session, rewind and model pickers filter as you type: the filter row stands where the composer was."""

    def openers(self) -> list[tuple[str, object]]:
        return [
            ("sessions", lambda app: app._open_session_picker(SESSIONS)),
            ("rewind", lambda app: app._open_rewind_picker(app.sid, HISTORY)),
            ("model", lambda app: app._open_model_picker(app.sid, MODELS)),
        ]

    def test_a_picker_hides_the_composer_and_its_filter_row_takes_the_composers_place(self) -> None:
        for name, opener in self.openers():
            with self.subTest(picker=name):
                app, _client = make_app()
                draft = a_draft(app)
                opener(app)  # type: ignore[operator]
                screen = drawn(app)
                self.assertEqual(rule_rows(screen), [], screen)
                status = status_row(screen)
                self.assertEqual(screen[status - 2], " › type to filter")  # right above the mode row
                self.assertEqual(app.terminal.screen.cursor, (status - 2, 3))  # type: ignore[attr-defined]  # the caret on it
                type_text(app, "s")
                self.assertEqual(drawn(app)[status - 2], " › s")
                self.assertEqual(held(app), draft)  # the filter is the picker's, not the draft's
                press(app, Key(ESCAPE))
                self.assertTrue(composer_drawn(app, drawn(app)))
                self.assertEqual(held(app), draft)

    def test_the_mode_row_says_the_pickers_keys_not_the_composers(self) -> None:
        # The composer's hints (`? shortcuts`, `⇧⇥ mode`) do nothing while a picker has the keys.
        keys = {"sessions": "↑↓ move · enter switches · esc closes", "rewind": "↑↓ move · enter edits it · esc closes",
                "model": "↑↓ move · enter switches this session · esc closes"}
        for name, opener in self.openers():
            with self.subTest(picker=name):
                app, _client = make_app()
                opener(app)  # type: ignore[operator]
                screen = drawn(app)
                mode_row = screen[status_row(screen) - 1]
                self.assertIn(keys[name], mode_row)
                self.assertNotIn("shortcuts", mode_row)
                self.assertEqual([row for row in screen if "esc closes" in row], [mode_row])  # not in the header too
                narrow = line_text(app._mode_row_line(40))
                self.assertTrue(narrow.rstrip().endswith("esc closes"), narrow)
                press(app, Key(ESCAPE))
                self.assertIn("shortcuts", drawn(app)[status_row(drawn(app)) - 1])

    def test_the_caret_shows_at_the_end_of_the_filter_row_where_typing_goes(self) -> None:
        # An IME pre-edit (Hangul, kana) is drawn at the cursor: it must be where the filter's text is typed.
        for name, opener in self.openers():
            with self.subTest(picker=name):
                app, _client = make_app()
                type_text(app, "a draft")
                opener(app)  # type: ignore[operator]
                screen = drawn(app)
                row = status_row(screen) - 2
                self.assertEqual(screen[row], " › type to filter")
                self.assertTrue(app.terminal.screen.cursor_visible)  # type: ignore[attr-defined]
                self.assertEqual(app.terminal.screen.cursor, (row, 3))  # type: ignore[attr-defined]
                type_text(app, "s")
                drawn(app)
                self.assertEqual(app.terminal.screen.cursor, (row, 4))  # type: ignore[attr-defined]
                press(app, Key(ESCAPE))
                screen = drawn(app)
                self.assertEqual(app.terminal.screen.cursor[0], next(i for i, line in enumerate(screen) if line.startswith("› a draft")))  # type: ignore[attr-defined]

    def test_a_tiny_terminal_still_shows_the_pickers_highlighted_row(self) -> None:
        for rows in (5, 4, 3):
            with self.subTest(rows=rows):
                app, _client = make_app(rows=rows, cols=40)
                app._open_session_picker(SESSIONS)
                screen = drawn(app)
                self.assertTrue(any(line.startswith(" ● Release notes") or line.startswith("   Release notes")
                                    for line in screen), screen)

    def test_below_ten_rows_a_pickers_keys_stay_in_its_header(self) -> None:
        app, _client = make_app(rows=9)
        app._open_session_picker(SESSIONS)
        screen = drawn(app)
        self.assertTrue(any(row.startswith(" Sessions (2)") and "esc closes" in row for row in screen), screen)
        narrow, _client = make_app(rows=9, cols=40)  # the header's keys take their short form, never a cut-off long one
        narrow._open_session_picker(SESSIONS)
        header = next(row for row in drawn(narrow) if row.startswith(" Sessions (2)"))
        self.assertTrue(header.rstrip().endswith("esc closes"), header)

    def test_the_history_search_and_the_completion_menu_keep_the_composer(self) -> None:
        app, _client = make_app()
        app.composer.history = ["an older prompt"]
        press(app, Key(CHAR, "r", ctrl=True))
        self.assertTrue(composer_drawn(app, drawn(app)))
        press(app, Key(ESCAPE))
        type_text(app, "/")
        self.assertTrue(composer_drawn(app, drawn(app)))


BATCH = ServerRequest("srq-5", "clarify", {"questions": [
    {"qid": "a", "question": "A?", "choices": ["x", "y"]},
    {"qid": "b", "question": "B?"},
    {"qid": "c", "question": "C?", "choices": ["p", "q"]},
]})


class ClarifyBatchTests(unittest.TestCase):
    """In a batch, what was typed for one question never answers the next, and each question arms as a slot does."""

    def test_text_typed_for_one_question_is_not_the_next_ones_answer(self) -> None:
        app, client = make_app(busy=True)
        draft = a_draft(app)
        app._on_server_request(BATCH)
        armed(app)
        type_text(app, "abc")  # Q1's field
        press(app, Key(UP), Key(UP), Key(ENTER))  # back to the list, and x picked
        self.assertEqual(app.slot.question_index, 1)  # type: ignore[union-attr]
        self.assertEqual(app.composer.text, "")  # Q2's field starts empty
        self.assertNotIn("abc", "\n".join(drawn(app)))
        armed(app)
        type_text(app, "hello")
        press(app, Key(ENTER))
        self.assertEqual(app.composer.text, "")
        armed(app)
        press(app, Key(CHAR, "2"))
        self.assertEqual(answered(client, "srq-5"), [{"result": {"answers": {"a": "x", "b": "hello", "c": "q"}}}])
        self.assertEqual(held(app), draft)

    def test_a_next_question_with_choices_opens_its_field_with_only_the_new_key(self) -> None:
        params = {"questions": [{"qid": "a", "question": "A?", "choices": ["x", "y"]},
                                {"qid": "b", "question": "B?", "choices": ["r", "s"]}]}
        app, client = make_app(busy=True)
        app._on_server_request(ServerRequest("srq-6", "clarify", params))
        armed(app)
        type_text(app, "abc")
        press(app, Key(UP), Key(UP), Key(ENTER))
        self.assertTrue(app.slot.picking)  # type: ignore[union-attr]
        armed(app)
        press(app, Key(CHAR, "z"))
        self.assertEqual(app.composer.text, "z")
        press(app, Key(ENTER))
        self.assertEqual(answered(client, "srq-6"), [{"result": {"answers": {"a": "x", "b": "z"}}}])

    def test_keys_in_the_first_moment_after_the_next_question_appears_answer_nothing(self) -> None:
        # A double-tapped Enter: the first picks for Q1, the second must not answer Q2 unseen.
        for second in (Key(ENTER), Key(CHAR, "1"), Key(ESCAPE)):
            with self.subTest(second=second):
                now = [100.0]
                app, client = make_app(busy=True, clock=lambda: now[0])
                app._on_server_request(ServerRequest("srq-7", "clarify", {"questions": [
                    {"qid": "a", "question": "A?", "choices": ["x", "y"]},
                    {"qid": "c", "question": "C?", "choices": ["p", "q"]}]}))
                now[0] += 10.0
                press(app, Key(ENTER), second)
                self.assertEqual((app.slot.question_index, client.answers), (1, []))  # type: ignore[union-attr]
                now[0] += 10.0
                press(app, Key(ENTER))
                self.assertEqual(answered(client, "srq-7"), [{"result": {"answers": {"a": "x", "c": "p"}}}])


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
