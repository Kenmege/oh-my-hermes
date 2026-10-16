"""The questions Hermes asks, through the app: reading one whole before answering it.

Ctrl+O while a question is open pages it whole (a long command every row of
it) and answers nothing; q or Esc goes back to the question as it was, and a
question that closes while paged takes its pager with it. A terminal too small
for anything but the composer and the status line still shows a question
answered with text its label. These cases drive an `App` whose gateway client
only records, and read the VT screen it painted.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from test_tui_choice_ui import armed, drawn, press
from test_tui_modes import make_app
from omh.tui import prompts
from omh.tui.gateway import ServerRequest
from omh.tui.keys import CHAR, DOWN, ESCAPE, Key
from omh.tui.layout import MIN_COLS, allocate
from omh.tui.pager import ALT_SCREEN_OFF, HistoryPager

LONG_COMMAND = "\n".join(f"echo line {n}" for n in range(1, 21))
LONG_APPROVAL = ServerRequest(
    "srq-1", "approval",
    {"command": LONG_COMMAND, "description": "script execution", "choices": ["once", "session", "deny"]},
)
CTRL_O = Key(CHAR, "o", ctrl=True)
TEXT_QUESTIONS = (
    (ServerRequest("srq-3", "sudo", {"command": "apt install x"}), "Hermes needs your sudo password"),
    (ServerRequest("srq-4", "secret", {"env_var": "API_KEY"}), "Hermes needs API_KEY"),
    (ServerRequest("srq-5", "clarify", {"questions": [{"qid": "q", "question": "Which number?"}]}), "Which number?"),
)


def paged(app) -> list[str]:
    return ["".join(span.text for span in line) for line in app.pager.lines]


class QuestionViewTests(unittest.TestCase):
    def test_ctrl_o_pages_the_open_approval_whole_and_answers_nothing(self) -> None:
        app, client = make_app(busy=True, rows=12, cols=60)
        app._on_server_request(LONG_APPROVAL)
        armed(app)
        press(app, Key(DOWN))
        self.assertIn("▌   echo line 1", drawn(app))  # the slot shows the command's head and the marker
        self.assertTrue([row for row in drawn(app) if "ctrl+o shows all" in row])
        press(app, CTRL_O)
        self.assertIsNotNone(app.pager)
        self.assertIs(app.pager, app.question_view)
        text = paged(app)
        self.assertEqual([row for row in text if "echo line" in row], [f"▌   echo line {n}" for n in range(1, 21)])
        self.assertIn("▌ ❯ allow once", text)
        self.assertEqual(client.answers, [])
        press(app, Key(CHAR, "q"))
        self.assertIsNone(app.pager)
        self.assertIs(app.slot.request_id, "srq-1")  # type: ignore[union-attr]
        self.assertEqual(app.slot.selected, 1)  # type: ignore[union-attr]
        self.assertIn("▌ ❯ allow once", drawn(app))
        self.assertEqual(client.answers, [])

    def test_esc_in_the_pager_leaves_it_and_denies_nothing(self) -> None:
        app, client = make_app(busy=True, rows=12, cols=60)
        app._on_server_request(LONG_APPROVAL)
        armed(app)
        press(app, CTRL_O, Key(ESCAPE))
        self.assertIsNone(app.pager)
        self.assertIsNotNone(app.slot)
        self.assertEqual(client.answers, [])
        press(app, Key(ESCAPE))  # with the pager gone, Esc is the question's again
        self.assertEqual(client.answers, [("srq-1", {"result": {"choice": "deny"}})])

    def test_a_question_withdrawn_while_paged_takes_its_pager_with_it(self) -> None:
        app, client = make_app(busy=True, rows=12, cols=60)
        app._on_server_request(LONG_APPROVAL)
        press(app, CTRL_O)
        app._on_withdrawn({"id": "srq-1", "reason": "timeout"})
        self.assertIsNone(app.slot)
        self.assertIsNone(app.pager)
        self.assertIsNone(app.question_view)
        self.assertIn(ALT_SCREEN_OFF, "".join(app.terminal.writes))  # type: ignore[attr-defined]
        self.assertEqual(client.answers, [])

    def test_without_a_question_ctrl_o_is_still_the_transcript(self) -> None:
        app, _client = make_app()
        app.transcript.add_notice("something to view")
        press(app, CTRL_O)
        self.assertIsInstance(app.pager, HistoryPager)
        self.assertIsNone(app.question_view)


class DocumentedQuestionViewTests(unittest.TestCase):
    def test_installation_shows_the_markers_the_slot_draws(self) -> None:
        installation = " ".join((Path(__file__).resolve().parents[1] / "docs" / "INSTALLATION.md").read_text(
            encoding="utf-8").split())
        self.assertIn(f"`{prompts.MORE_ANY_ROWS[0].strip()}`", installation)
        self.assertIn(f"`{prompts.MORE_ROWS[0].strip().format(count=14, s='s')}`", installation)
        self.assertIn("Ctrl+O opens the whole question in the pager: it answers nothing", installation)


class TinyTerminalQuestionTests(unittest.TestCase):
    def test_a_question_answered_with_text_keeps_its_label_on_a_tiny_terminal(self) -> None:
        for rows, cols in ((4, 80), (5, 80), (24, MIN_COLS - 1)):
            for request, label in TEXT_QUESTIONS:
                with self.subTest(rows=rows, cols=cols, method=request.method):
                    app, _client = make_app(busy=True, rows=rows, cols=cols)
                    app._on_server_request(request)
                    screen = drawn(app)
                    (row,) = [line for line in screen if line.startswith("▌ ")]
                    shown = row[2:].removeprefix("🔑 ").removeprefix("? ").split(" …")[0].rstrip("…")
                    self.assertTrue(shown and label.startswith(shown), row)
                    self.assertTrue([line for line in screen if line.startswith("›")], screen)  # the composer under it

    def test_the_label_row_is_the_text_questions_alone(self) -> None:
        # A tiny terminal still gives a panel, a menu or a picked list nothing beyond what it gave before.
        self.assertEqual(allocate(5, 80, sticky=False, tail_rows=0, slot_rows=4, composer_rows=1, min_height=0).slot, 0)
        self.assertEqual(
            allocate(5, 80, sticky=False, tail_rows=0, slot_rows=4, composer_rows=1, min_height=0, text_question=True).slot, 1
        )
        self.assertEqual(
            allocate(3, 80, sticky=False, tail_rows=0, slot_rows=4, composer_rows=1, min_height=0, text_question=True).slot, 0
        )
        roomy = dict(sticky=False, tail_rows=0, slot_rows=9, composer_rows=1, min_height=0)
        self.assertEqual(allocate(24, 80, **roomy, text_question=True), allocate(24, 80, **roomy))


if __name__ == "__main__":
    unittest.main()
