"""Esc Esc and `/rewind`: clear a draft into history, or go back to an earlier prompt and send it again.

With text in the composer, Esc twice clears it and keeps it in the input
history. On an empty composer between turns, Esc twice (or `/rewind`) lists
the session's prompts from `session.history`; the one picked comes back into
the composer, and sending it sends `prompt.submit` with
`truncate_before_row_id` and `confirm_truncate`, so Hermes drops that prompt
and everything after it, and the screen is redrawn from the cut. Answers are
the recorded Hermes 6c61c3e27d frames in `tests/fixtures/tui_gateway/parity.jsonl`.
"""

from __future__ import annotations

import unittest

from _tui_lines import line_text, lines_text
from test_tui_gateway_contract import recorded_rows
from test_tui_modes import SID, answer, make_app, notices, type_text
from omh.tui.app import DOUBLE_ESCAPE_SECONDS, App
from omh.tui.gateway import Event, Response
from omh.tui.keys import CHAR, DOWN, ENTER, ESCAPE, Key
from omh.tui.rewind_picker import CLOSE, PICK, RewindPicker, rewind_rows
from omh.tui.theme import DEFAULT_THEME
from omh.tui.transcript import NOTICE_WARN, AssistantBlock, UserBlock


def recorded_result(segment: str) -> dict:
    (frame,) = [row["frame"] for row in recorded_rows("parity", segment) if row["dir"] == "in" and "result" in row["frame"]]
    return frame["result"]


HISTORY = recorded_result("history_two")
TODO_PROMPT = HISTORY["messages"][0]["text"]
SECOND_PROMPT = "Reply with exactly the word: two"


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def esc(app: App) -> None:
    app._on_input(Key(ESCAPE))


def mode_row(app: App, cols: int) -> str:
    return line_text(app._mode_row_line(cols))


class PickerTests(unittest.TestCase):
    def test_the_rows_are_the_sessions_prompts_newest_first_with_their_row_ids(self) -> None:
        rows = rewind_rows(HISTORY)
        self.assertEqual([(row.text, row.row_id, row.ordinal) for row in rows], [(SECOND_PROMPT, 7, 2), (TODO_PROMPT, 1, 1)])
        self.assertEqual(rows[0].index, [message.get("row_id") for message in HISTORY["messages"]].index(7))

    def test_a_user_row_without_an_int_row_id_or_text_is_no_prompt_to_rewind_to(self) -> None:
        messages = [
            {"role": "user", "text": "kept", "row_id": 3},
            {"role": "user", "text": "no id"},
            {"role": "user", "text": "a string id", "row_id": "4"},
            {"role": "user", "text": "a bool id", "row_id": True},
            {"role": "user", "text": "   ", "row_id": 5},
            {"role": "assistant", "text": "a reply", "row_id": 6},
        ]
        self.assertEqual([(row.text, row.row_id) for row in rewind_rows({"messages": messages})], [("kept", 3)])

    def test_enter_picks_the_highlighted_prompt_typing_filters_and_esc_closes(self) -> None:
        picker = RewindPicker(rewind_rows(HISTORY))
        picker.handle_key(Key(DOWN))
        action = picker.handle_key(Key(ENTER))
        self.assertEqual((action.kind, action.row.row_id), (PICK, 1))
        picker = RewindPicker(rewind_rows(HISTORY))
        for char in "todo":
            picker.handle_key(Key(CHAR, char))
        self.assertEqual(picker.handle_key(Key(ENTER)).row.row_id, 1)
        self.assertEqual(RewindPicker(rewind_rows(HISTORY)).handle_key(Key(ESCAPE)).kind, CLOSE)

    def test_the_slot_names_what_picking_does(self) -> None:
        rows = lines_text(RewindPicker(rewind_rows(HISTORY)).lines(100, DEFAULT_THEME, 6, 1792006300.0))
        self.assertIn("Rewind", rows[0])
        self.assertIn("enter edits it", rows[0])
        self.assertTrue(rows[1].startswith(" #2 Reply with exactly the word: two"), rows)


class DoubleEscapeTests(unittest.TestCase):
    def test_esc_twice_with_text_clears_it_into_the_history_and_once_keeps_it(self) -> None:
        clock = Clock()
        app, client = make_app(clock=clock)
        type_text(app, "a draft")
        esc(app)
        self.assertEqual(app.composer.text, "a draft")
        clock.now += DOUBLE_ESCAPE_SECONDS / 2
        esc(app)
        self.assertEqual(app.composer.text, "")
        self.assertEqual(app.composer.history, ["a draft"])
        self.assertEqual(client.calls, [])

    def test_two_escs_further_apart_than_the_window_are_two_first_escs(self) -> None:
        clock = Clock()
        app, _client = make_app(clock=clock)
        type_text(app, "a draft")
        esc(app)
        clock.now += DOUBLE_ESCAPE_SECONDS + 0.1
        esc(app)
        self.assertEqual(app.composer.text, "a draft")

    def test_the_mode_row_says_what_the_second_esc_will_do(self) -> None:
        clock = Clock()
        app, _client = make_app(clock=clock)
        type_text(app, "a draft")
        esc(app)
        self.assertIn("esc again to clear", mode_row(app, 80))
        clock.now += DOUBLE_ESCAPE_SECONDS + 0.1
        self.assertNotIn("esc again", mode_row(app, 80))
        app._set_composer("")
        esc(app)
        self.assertIn("esc again to rewind", mode_row(app, 80))

    def test_the_cut_counts_the_same_messages_the_picker_was_made_from(self) -> None:
        app, client = make_app()
        app._rewind("")
        messages = ["not a message", {"role": "user", "text": "first", "row_id": 1}, {"role": "assistant", "text": "one"},
                    {"role": "user", "text": "second", "row_id": 2}, {"role": "assistant", "text": "two"}]
        answer(app, client, {"messages": messages})
        app._on_input(Key(ENTER))  # the newest prompt, "second"
        self.assertEqual(app.rewind.before, tuple(messages[1:3]))  # type: ignore[union-attr]  # not "second" itself

    def test_esc_twice_on_a_paste_chip_keeps_its_text_in_the_history(self) -> None:
        from omh.tui.keys import Paste

        clock = Clock()
        app, _client = make_app(clock=clock)
        pasted = "line one\nline two\nline three\nline four"
        app._on_input(Paste(pasted))
        esc(app)
        esc(app)
        self.assertEqual(app.composer.text, "")
        self.assertEqual(app.composer.history[-1], pasted)  # recalled and sent, it is the text, not the label

    def test_typing_after_one_esc_takes_back_what_the_second_would_do(self) -> None:
        clock = Clock()
        app, _client = make_app(clock=clock)
        esc(app)
        self.assertIn("esc again to rewind", mode_row(app, 100))
        app._on_input(Key(CHAR, "a"))
        self.assertNotIn("esc again", mode_row(app, 100))  # at once, not 0.8 s later
        self.assertIsNone(app.escape_armed)

    def test_esc_twice_on_an_empty_idle_composer_lists_the_prompts_to_rewind_to(self) -> None:
        clock = Clock()
        app, client = make_app(clock=clock)
        esc(app)
        self.assertEqual(client.calls, [])
        esc(app)
        self.assertEqual(client.calls, [("session.history", {"session_id": SID})])
        answer(app, client, HISTORY)
        self.assertIsNotNone(app.rewind_picker)

    def test_esc_on_an_empty_composer_during_a_turn_still_interrupts_at_once(self) -> None:
        app, client = make_app(busy=True)
        esc(app)
        self.assertEqual(client.calls, [("session.interrupt", {"session_id": SID})])


class RewindTests(unittest.TestCase):
    def picked(self, row: int = 0) -> tuple[App, object]:
        app, client = make_app()
        app._rewind("")
        answer(app, client, HISTORY)
        for _ in range(row):
            app._on_input(Key(DOWN))
        app._on_input(Key(ENTER))
        return app, client

    def test_the_picked_prompt_comes_back_into_the_composer_to_edit(self) -> None:
        app, _client = self.picked()
        self.assertIsNone(app.rewind_picker)
        self.assertEqual(app.composer.text, SECOND_PROMPT)
        self.assertEqual(app.rewind.row_id, 7)  # type: ignore[union-attr]
        self.assertIn("rewind", mode_row(app, 100))

    def test_sending_it_truncates_before_that_prompt_and_redraws_from_the_cut(self) -> None:
        app, client = self.picked()
        app._set_composer("Reply with exactly the word: rewound")
        app._on_input(Key(ENTER))
        self.assertEqual(
            client.calls[-1],
            ("prompt.submit", {"session_id": SID, "text": "Reply with exactly the word: rewound",
                               "truncate_before_row_id": 7, "confirm_truncate": True}),
        )
        users = [block.text for block in app.transcript.blocks if isinstance(block, UserBlock)]
        self.assertEqual(users, [TODO_PROMPT, "Reply with exactly the word: rewound"])
        self.assertTrue([block for block in app.transcript.blocks if isinstance(block, AssistantBlock)])
        self.assertIsNone(app.rewind)
        # the full redraw cleared the screen: the next frame starts on its top row
        self.assertEqual((app.region.height, app.region.top_row(app.size[1])), (0, 0))

    def test_rewinding_to_the_first_prompt_leaves_nothing_above_the_new_one(self) -> None:
        app, client = self.picked(row=1)
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls[-1][1]["truncate_before_row_id"], 1)
        self.assertEqual([block.text for block in app.transcript.blocks if isinstance(block, UserBlock)], [TODO_PROMPT])

    def test_a_refused_rewind_says_so_and_redraws_the_session_hermes_still_has(self) -> None:
        app, client = self.picked()
        app._set_composer("an edited prompt")
        app._on_input(Key(ENTER))
        answer(app, client, error={"code": 4018, "message": "rewind target not found"})
        self.assertEqual(client.calls[-1], ("session.history", {"session_id": SID}))
        answer(app, client, HISTORY)
        self.assertIn("Hermes did not rewind: rewind target not found.", notices(app, NOTICE_WARN)[-1])
        users = [block.text for block in app.transcript.blocks if isinstance(block, UserBlock)]
        self.assertEqual(users, [TODO_PROMPT, SECOND_PROMPT])
        self.assertFalse(app.turn_pending)

    def sent_and_refused(self) -> tuple[App, object]:
        """A rewind sent over a screen that shows the session, which Hermes refuses."""
        app, client = self.picked()
        for message in HISTORY["messages"]:  # what the screen showed before the rewind
            if message.get("role") == "user":
                app.transcript.add_user(message["text"])
            elif message.get("role") == "assistant":
                app.transcript.add_assistant(message.get("text") or "")
        app._set_composer("an edited prompt")
        app._on_input(Key(ENTER))
        answer(app, client, error={"code": 4018, "message": "rewind target not found"})
        return app, client

    def users(self, app: App) -> list[str]:
        return [block.text for block in app.transcript.blocks if isinstance(block, UserBlock)]

    def test_a_refused_rewind_puts_the_screen_back_before_hermes_sends_its_history(self) -> None:
        app, client = self.sent_and_refused()
        self.assertEqual(client.calls[-1], ("session.history", {"session_id": SID}))
        self.assertEqual(self.users(app), [TODO_PROMPT, SECOND_PROMPT])  # not the cut, not the prompt Hermes refused
        self.assertIn("Hermes did not rewind: rewind target not found.", notices(app, NOTICE_WARN)[-1])
        self.assertNotIn("as Hermes still has it", " ".join(notices(app)))  # nothing re-read yet

    def test_a_refused_rewind_whose_history_read_fails_keeps_the_screen_from_before(self) -> None:
        app, client = self.sent_and_refused()
        answer(app, client, error={"code": 5000, "message": "state.db locked"})
        self.assertEqual(self.users(app), [TODO_PROMPT, SECOND_PROMPT])
        said = " ".join(notices(app))
        self.assertIn("state.db locked", said)
        self.assertNotIn("as Hermes still has it", said)

    def queued_behind_a_refused_rewind(self) -> tuple[App, object]:
        """A rewind sent, a prompt queued behind it while it was out, and Hermes' refusal of the rewind."""
        app, client = self.picked()
        app._set_composer("an edited prompt")
        app._on_input(Key(ENTER))
        submit = len(client.calls)
        app.busy_mode = "queue"
        type_text(app, "a queued prompt")
        app._on_input(Key(ENTER))
        app._on_response(Response(submit, None, {"code": 4018, "message": "rewind target not found"}))
        return app, client

    def test_a_prompt_queued_behind_a_refused_rewind_goes_after_the_redraw_and_stays_on_the_screen(self) -> None:
        app, client = self.queued_behind_a_refused_rewind()
        self.assertEqual(client.calls[-1], ("session.history", {"session_id": SID}))  # nothing sent before the redraw
        answer(app, client, HISTORY)
        self.assertEqual(client.calls[-1], ("prompt.submit", {"session_id": SID, "text": "a queued prompt", "queued": True}))
        self.assertEqual(self.users(app), [TODO_PROMPT, SECOND_PROMPT, "a queued prompt"])  # its echo on the redrawn screen

    def test_a_turn_started_before_the_history_answer_keeps_the_queued_prompt_for_its_end(self) -> None:
        app, client = self.queued_behind_a_refused_rewind()
        type_text(app, "sent right away")  # no turn runs after the refusal, so this one goes at once
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls[-1], ("prompt.submit", {"session_id": SID, "text": "sent right away"}))
        history = next(index for index, (method, _params) in enumerate(client.calls, start=1) if method == "session.history"
                       and index > 1)
        app._on_response(Response(history, HISTORY, None))
        self.assertEqual(client.calls[-1], ("prompt.submit", {"session_id": SID, "text": "sent right away"}))
        self.assertEqual([line.text for line in app.queued], ["a queued prompt"])  # waits for that turn's end
        app._on_event(Event("message.complete", SID, {"text": "done", "status": "complete"}))
        self.assertEqual(client.calls[-1], ("prompt.submit", {"session_id": SID, "text": "a queued prompt", "queued": True}))

    def test_a_prompt_queued_behind_a_refused_rewind_goes_once_the_history_read_fails_or_goes_unanswered(self) -> None:
        for outcome in ("refused", "unanswered"):
            with self.subTest(outcome=outcome):
                clock = Clock()
                app, client = make_app(clock=clock)
                app._rewind("")
                answer(app, client, HISTORY)
                app._on_input(Key(ENTER))
                app._set_composer("an edited prompt")
                app._on_input(Key(ENTER))
                submit = len(client.calls)
                app.busy_mode = "queue"
                type_text(app, "a queued prompt")
                app._on_input(Key(ENTER))
                app._on_response(Response(submit, None, {"code": 4018, "message": "rewind target not found"}))
                if outcome == "refused":
                    answer(app, client, error={"code": 5000, "message": "state.db locked"})
                    said = "state.db locked"
                else:
                    clock.now += 3600
                    app._expire_calls()
                    said = "no answer in 120 s"
                self.assertIn(f"Hermes did not send the session's history ({said})", " ".join(notices(app)))
                self.assertEqual(client.calls[-1],
                                 ("prompt.submit", {"session_id": SID, "text": "a queued prompt", "queued": True}))
                self.assertEqual(self.users(app)[-1], "a queued prompt")

    def test_a_rewind_refused_after_the_session_was_left_leaves_the_new_session_on_the_screen(self) -> None:
        for leave in ("new", "switch"):
            with self.subTest(leave=leave):
                app, client = self.picked()
                app._set_composer("an edited prompt")
                app._on_input(Key(ENTER))
                submit = len(client.calls)
                if leave == "new":
                    app._on_new_session({"session_id": "live-2", "stored_session_id": "stored-2"})
                else:
                    app._on_session_resumed("stored-2", {"session_id": "live-2", "stored_session_id": "stored-2"})
                screen = app.transcript
                app._on_response(Response(submit, None, {"code": 4018, "message": "rewind target not found"}))
                self.assertIs(app.transcript, screen)  # not the old session's screen from before the send
                self.assertFalse([text for text in notices(app) if "did not rewind" in text])
                self.assertNotIn(("session.history", {"session_id": SID}), client.calls[submit:])

    def test_a_rewind_sent_once_a_turn_started_gives_the_text_back_and_cuts_nothing(self) -> None:
        app, client = self.picked()
        app.turn_pending = True  # a turn started between the pick and the send
        app._set_composer("an edited prompt")
        app._on_input(Key(ENTER))
        self.assertNotIn("prompt.submit", [method for method, _params in client.calls])
        self.assertEqual(app.composer.text, "an edited prompt")
        self.assertIn("between turns", notices(app, NOTICE_WARN)[-1])
        self.assertIsNone(app.rewind)

    def test_esc_cancels_a_rewind_and_empties_the_composer(self) -> None:
        app, client = self.picked()
        esc(app)
        self.assertIsNone(app.rewind)
        self.assertEqual(app.composer.text, "")
        calls = len(client.calls)
        type_text(app, "a plain prompt")
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls[calls], ("prompt.submit", {"session_id": SID, "text": "a plain prompt"}))

    def test_rewind_waits_for_the_turn_to_end(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "/rewind")
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls, [])
        self.assertIn("between turns", notices(app, NOTICE_WARN)[-1])

    def test_a_session_with_no_prompt_yet_has_nothing_to_rewind_to(self) -> None:
        app, client = make_app()
        app._rewind("")
        answer(app, client, {"count": 0, "messages": []})
        self.assertIsNone(app.rewind_picker)
        self.assertIn("No prompt to rewind to", notices(app)[-1])


class DocumentedRewindTests(unittest.TestCase):
    def test_installation_names_the_window_and_the_calls_the_code_uses(self) -> None:
        from pathlib import Path

        prose = " ".join((Path(__file__).resolve().parents[1] / "docs/INSTALLATION.md").read_text(encoding="utf-8").split())
        self.assertIn(f"Esc twice (within {DOUBLE_ESCAPE_SECONDS:g} s) on an empty composer between turns", prose)
        self.assertIn("`truncate_before_row_id` and `confirm_truncate`", prose)


if __name__ == "__main__":
    unittest.main()
