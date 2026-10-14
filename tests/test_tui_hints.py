"""Hints that say one thing at a time, and layouts that keep them on narrow terminals.

While Hermes asks something, the mode row names that question's keys and
the working row stops offering Esc for an interrupt, so the screen gives Esc
one meaning. The strip's last mark says what Down opens (`↓ agents`). An
approval keeps a short key hint at 60 and 40 columns, and a long command
wraps under its own indent. The banner is one row (brand, version, resumed
session, working directory; the model is on the status line and the keys on
the mode row), and a fresh session is told where the last one is.
"""

from __future__ import annotations

import unittest

from _tui_lines import line_text, lines_text
from test_tui_agent_strip import agent_app
from test_tui_modes import answer, make_app
from omh.tui.app import App, banner_lines
from omh.tui.gateway import Response, ServerRequest
from omh.tui.prompts import open_request
from omh.tui.subagents import OPEN_MARK
from omh.tui.theme import DEFAULT_THEME
from omh.tui.transcript import NoticeBlock

APPROVAL = ServerRequest("srq-1", "approval", {"command": "rm -rf ./build", "description": "recursive delete"})
CLARIFY = ServerRequest("srq-2", "clarify", {"questions": [{"qid": "q1", "question": "Which?", "choices": ["a", "b"]}]})
SECRET = ServerRequest("srq-3", "secret", {"env_var": "TOKEN"})


def mode_row(app: App) -> str:
    return line_text(app._mode_row_line(100))


class QuestionKeyTests(unittest.TestCase):
    def test_an_open_question_puts_its_keys_on_the_mode_row_and_esc_means_one_thing(self) -> None:
        cases = [
            (APPROVAL, "←→ pick · enter confirms · d or esc denies"),
            (CLARIFY, "1-9 or ↑↓ enter picks · or type an answer · esc cancels"),
            (SECRET, "type it · enter sends · esc declines"),
        ]
        for request, keys in cases:
            with self.subTest(method=request.method):
                app, _client = make_app(busy=True)
                app._on_server_request(request)
                self.assertIn(keys, mode_row(app))
                self.assertNotIn("esc interrupt", mode_row(app))
                self.assertNotIn("esc to interrupt", line_text(app._tail(100)[-1]))  # the working row too

    def test_without_a_question_the_working_row_still_offers_the_interrupt(self) -> None:
        app, _client = make_app(busy=True)
        self.assertIn("esc to interrupt", line_text(app._tail(100)[-1]))
        self.assertIn("esc interrupt", mode_row(app))


class StripMarkTests(unittest.TestCase):
    def test_the_strips_last_mark_says_what_down_opens(self) -> None:
        self.assertEqual(OPEN_MARK, "↓ agents")
        app, _client = agent_app()
        self.assertTrue(line_text(app._agent_strip(120)[0]).endswith("↓ agents"))


class NarrowApprovalTests(unittest.TestCase):
    COMMAND = "find . -name '*.pyc' -delete && echo done with a long tail of words"

    def rows(self, width: int) -> list[str]:
        slot = open_request("srq", "approval", {"command": self.COMMAND, "description": "delete in root path"})
        return lines_text(slot.lines(width, DEFAULT_THEME, 6))

    def test_the_choice_row_keeps_a_way_to_deny_at_sixty_and_forty_columns(self) -> None:
        for width in (60, 40):
            with self.subTest(width=width):
                choices = self.rows(width)[-1]
                self.assertIn("deny", choices)
                self.assertIn("d denies", choices)

    def test_a_wrapped_command_keeps_its_indent(self) -> None:
        rows = self.rows(50)
        command_rows = rows[1:-1]
        self.assertGreater(len(command_rows), 1)
        for row in command_rows:
            self.assertTrue(row.startswith("▌   "), rows)


class BannerTests(unittest.TestCase):
    def test_the_banner_is_one_row_without_the_model_or_the_key_hint(self) -> None:
        rows = banner_lines(cwd="~/project", resumed="", theme=DEFAULT_THEME, width=100)
        self.assertEqual(len(rows), 2)
        self.assertEqual(line_text(rows[0]).split(" · ", 1)[1], "~/project")
        self.assertEqual(rows[1], ())
        resumed = line_text(banner_lines(cwd="~/project", resumed="abc", theme=DEFAULT_THEME, width=100)[0])
        self.assertIn("resumed abc · ~/project", resumed)

    def last_session(self, app: App, client: object, sessions: list[dict]) -> None:
        (call,) = [index for index, (method, _params) in enumerate(client.calls) if method == "session.list"]  # type: ignore[attr-defined]
        app._on_response(Response(call + 1, {"sessions": sessions}, None))

    def test_a_fresh_session_names_the_last_one_and_how_to_resume_it(self) -> None:
        app, client = make_app()
        app.stored_id = "now"
        app._ask_last_session()
        self.assertIn(("session.list", {"limit": 3}), client.calls)
        self.last_session(app, client, [
            {"id": "now", "title": "", "message_count": 0, "started_at": 0},
            {"id": "opened-and-left", "title": "", "message_count": 0, "started_at": 0},  # nothing to go back to
            {"id": "earlier", "title": "fix the flaky test", "message_count": 12, "started_at": 0},
        ])
        notice = [block.text for block in app.transcript.blocks if isinstance(block, NoticeBlock)]
        self.assertEqual(notice, ["↺ last session: fix the flaky test · /resume earlier reopens it"])

    def test_a_hermes_that_does_not_know_session_list_costs_the_notice_not_the_session(self) -> None:
        app, client = make_app()
        app._ask_last_session()
        answer(app, client, error={"code": -32601, "message": "Method not found"})
        self.assertIsNone(app.exit_code)
        self.assertEqual(app.transcript.blocks, [])

    def test_nothing_is_said_once_the_person_has_started_or_without_an_earlier_session(self) -> None:
        app, client = make_app()
        app.stored_id = "now"
        app._ask_last_session()
        app._send_prompt("already typing")
        self.last_session(app, client, [{"id": "earlier", "title": "x", "message_count": 3, "started_at": 0}])
        self.assertFalse([block for block in app.transcript.blocks if isinstance(block, NoticeBlock)])
        app, client = make_app()
        app.stored_id = "now"
        app._ask_last_session()
        self.last_session(app, client, [{"id": "now", "title": "x", "message_count": 3, "started_at": 0}])
        self.assertEqual(app.transcript.blocks, [])


if __name__ == "__main__":
    unittest.main()
