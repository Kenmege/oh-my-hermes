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
from test_tui_modes import make_app, type_text
from omh.tui import app as app_module
from omh.tui import prompts
from omh.tui.gateway import Event, ServerRequest
from omh.tui.keys import CHAR, DOWN, ENTER, ESCAPE, Key
from omh.tui.layout import MIN_COLS, allocate
from omh.tui.app import BELL
from omh.tui.pager import ALT_SCREEN_OFF, HistoryPager
from omh.tui.transcript import NOTICE_ERROR, NOTICE_OK, TURN_WAITED, NoticeBlock, ToolGroup, TurnSummaryBlock

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


class WaitingQuestionTests(unittest.TestCase):
    def test_the_open_question_counts_the_questions_waiting_behind_it(self) -> None:
        app, client = make_app(busy=True, rows=24, cols=100)
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x", "description": "recursive delete"}))
        self.assertFalse([row for row in drawn(app) if "waiting" in row and row.startswith("▌")])
        for request, _label in TEXT_QUESTIONS[:2]:
            app._on_server_request(request)
        self.assertIn("▌ ⚠ Hermes asks to run recursive delete · 2 more questions waiting", drawn(app))
        armed(app)
        press(app, Key(ESCAPE))
        self.assertIn("▌ 🔑 Hermes needs your sudo password · 1 more question waiting", drawn(app))
        press(app, CTRL_O)
        self.assertIn("▌ 🔑 Hermes needs your sudo password · 1 more question waiting", paged(app))
        press(app, Key(CHAR, "q"))
        armed(app)
        press(app, Key(ESCAPE))
        self.assertIn("▌ 🔑 Hermes needs API_KEY", drawn(app))  # the last one: nothing behind it
        self.assertEqual([request_id for request_id, _ in client.answers], ["srq-1", "srq-3"])

    def test_a_request_the_tui_cannot_show_is_declined_at_once_even_behind_a_question(self) -> None:
        app, client = make_app(busy=True)
        app._on_server_request(LONG_APPROVAL)
        app._on_server_request(ServerRequest("srq-9", "terminal.read", {}))
        self.assertEqual([request_id for request_id, _ in client.answers], ["srq-9"])
        self.assertEqual(client.answers[0][1]["error"]["code"], -32601)
        self.assertFalse(app.waiting_requests)
        self.assertIs(app.slot.request_id, "srq-1")  # type: ignore[union-attr]
        self.assertFalse([row for row in drawn(app) if "waiting" in row and row.startswith("▌")])


class MultiSelectAppTests(unittest.TestCase):
    def test_space_on_a_multi_select_question_ticks_and_never_reaches_the_draft(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "my draft")
        app._on_server_request(ServerRequest("srq-6", "clarify", {"questions": [
            {"qid": "q0", "question": "Which toppings?", "choices": ["cheese", "ham"], "multi_select": True},
        ]}))
        armed(app)
        press(app, Key(CHAR, " "), Key(DOWN), Key(CHAR, " "))
        self.assertIn("▌ ❯ [x] 2. ham", drawn(app))
        press(app, Key(ENTER))
        self.assertEqual(client.answers, [("srq-6", {"result": {"answers": {"q0": '["cheese","ham"]'}}})])
        self.assertEqual(app.composer.text, "my draft")


COLOUR = ServerRequest("srq-7", "clarify", {"questions": [
    {"qid": "q0", "question": "Which colour?", "choices": ["red (Recommended)", "blue"], "multi_select": False},
]})


def outcome_rows(app) -> list[tuple[str, str]]:
    """The answers the transcript recorded: each row's text and level."""
    return [(block.text, block.level) for block in app.transcript.blocks if isinstance(block, NoticeBlock)]


def opened(request: ServerRequest):
    app, client = make_app(busy=True)
    app._on_server_request(request)
    armed(app)
    return app, client


class QuestionOutcomeTests(unittest.TestCase):
    def test_a_picked_choice_is_recorded_with_its_label(self) -> None:
        app, _client = opened(COLOUR)
        press(app, Key(ENTER))
        self.assertEqual(outcome_rows(app), [("red (Recommended) · Which colour?", NOTICE_OK)])
        self.assertIn("✓ red (Recommended) · Which colour?", drawn(app))

    def test_a_typed_answer_is_recorded_as_typed(self) -> None:
        app, _client = opened(TEXT_QUESTIONS[2][0])
        type_text(app, "seven")
        press(app, Key(ENTER))
        self.assertEqual(outcome_rows(app), [("seven · Which number?", NOTICE_OK)])

    def test_ticked_choices_are_recorded_joined(self) -> None:
        app, _client = opened(ServerRequest("srq-6", "clarify", {"questions": [
            {"qid": "q0", "question": "Which toppings?", "choices": ["cheese", "ham"], "multi_select": True},
        ]}))
        press(app, Key(CHAR, "2"), Key(CHAR, "1"), Key(ENTER))
        self.assertEqual(outcome_rows(app), [("ham, cheese · Which toppings?", NOTICE_OK)])

    def test_a_cancelled_question_is_a_red_cross_with_a_word(self) -> None:
        for key in (Key(ESCAPE), Key(CHAR, "c", ctrl=True)):
            with self.subTest(key=key):
                app, client = opened(COLOUR)
                press(app, key)
                self.assertEqual(client.answers, [("srq-7", {"result": {}})])
                self.assertEqual(outcome_rows(app), [("cancelled · Which colour?", NOTICE_ERROR)])
                self.assertIn("✗ cancelled · Which colour?", drawn(app))

    def test_each_question_of_a_batch_gets_its_row_and_an_empty_answer_is_skipped(self) -> None:
        app, _client = opened(ServerRequest("srq-8", "clarify", {"questions": [
            {"qid": "a", "question": "Why?"}, {"qid": "b", "question": "Which?", "choices": ["x", "y"]},
        ]}))
        press(app, Key(ENTER))  # nothing typed for the first
        armed(app)
        press(app, Key(DOWN), Key(ENTER))
        self.assertEqual(outcome_rows(app), [("skipped · Why?", NOTICE_ERROR), ("y · Which?", NOTICE_OK)])

    def test_a_value_prompt_says_answered_or_declined_and_never_the_value(self) -> None:
        for request, subject in ((TEXT_QUESTIONS[0][0], "sudo password"), (TEXT_QUESTIONS[1][0], "API_KEY")):
            with self.subTest(method=request.method):
                app, client = opened(request)
                type_text(app, "hunter2")
                press(app, Key(ENTER))
                self.assertEqual(client.answers, [(request.id, {"result": {"value": "hunter2"}})])
                self.assertEqual(outcome_rows(app), [(f"answered · {subject}", NOTICE_OK)])
                self.assertNotIn("hunter2", repr(app.transcript.blocks))
                self.assertFalse([row for row in drawn(app) if "hunter2" in row])
                app, _client = opened(request)
                press(app, Key(ESCAPE))
                self.assertEqual(outcome_rows(app), [(f"declined · {subject}", NOTICE_ERROR)])

    def test_a_withdrawn_question_says_why_with_a_red_cross(self) -> None:
        cases = (
            (COLOUR, "timeout", "timed out · Which colour?"),
            (COLOUR, "interrupted", "interrupted · Which colour?"),
            (TEXT_QUESTIONS[0][0], "", "withdrawn (no reason given) · sudo password"),
            (ServerRequest("srq-1", "approval", {"command": "rm -rf ./x\nmore"}), "session_closed",
             "withdrawn (session_closed) · rm -rf ./x"),
        )
        for request, reason, row in cases:
            with self.subTest(reason=reason, method=request.method):
                app, client = opened(request)
                app._on_withdrawn({"id": request.id, "reason": reason})
                self.assertIsNone(app.slot)
                self.assertEqual(client.answers, [])
                self.assertEqual(outcome_rows(app), [(row, NOTICE_ERROR)])

    def test_an_approvals_answer_is_still_recorded_once(self) -> None:
        app, _client = opened(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        press(app, Key(ESCAPE))
        self.assertEqual(outcome_rows(app), [("denied · rm -rf ./x", NOTICE_ERROR)])


SID = "live-1"  # make_app's session


class WaitingTimeTests(unittest.TestCase):
    def clocked(self):
        now = [100.0]
        app, client = make_app(busy=True, clock=lambda: now[0])
        app.busy_since = 90.0
        return app, client, now

    def tool(self, app, kind: str, **payload) -> None:
        app._on_event(Event(f"tool.{kind}", SID, {"tool_id": "t1", "name": "terminal", **payload}))

    def test_the_working_row_says_it_waits_on_you_with_one_clock(self) -> None:
        app, _client, now = self.clocked()
        self.assertTrue([row for row in drawn(app) if "working… (10s" in row])
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        now[0] = 111.0
        screen = drawn(app)
        self.assertIn("? waiting on you (11s)", screen)  # the wait's clock, not the turn's 21s
        self.assertFalse([row for row in screen if "working" in row or "21s" in row], screen)
        armed(app)
        press(app, Key(ESCAPE))
        now[0] = 112.0
        self.assertTrue([row for row in drawn(app) if "working… (22s" in row])  # the turn's own clock again

    def test_back_to_back_questions_are_one_wait(self) -> None:
        app, _client, now = self.clocked()
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        app._on_server_request(TEXT_QUESTIONS[0][0])
        now[0] = 104.0
        armed(app)
        press(app, Key(ESCAPE))  # the sudo prompt opens as the approval closes
        now[0] = 107.0
        self.assertIn("? waiting on you (7s)", drawn(app))
        armed(app)
        press(app, Key(ESCAPE))
        self.assertEqual(app.turn_waited, 7.0)
        self.assertIsNone(app.waiting_since)

    def test_a_tools_duration_leaves_out_the_wait_on_you(self) -> None:
        for duration in ({"duration_s": 22.0}, {}):  # Hermes' own figure, or the TUI's from the start
            with self.subTest(duration=duration):
                app, _client, now = self.clocked()
                self.tool(app, "start", context="rm -rf ./x")
                now[0] = 101.0
                app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
                now[0] = 121.0
                armed(app)
                press(app, Key(DOWN), Key(ENTER))
                now[0] = 122.0
                self.tool(app, "complete", result="ok", **duration)
                (row,) = [tool for block in app.transcript.blocks if isinstance(block, ToolGroup) for tool in block.tools]
                self.assertAlmostEqual(row.duration_s, 2.0)

    def test_a_tool_that_ran_before_the_wait_keeps_its_whole_duration(self) -> None:
        app, _client, now = self.clocked()
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        now[0] = 130.0
        armed(app)
        press(app, Key(ESCAPE))
        self.tool(app, "start", context="ls")
        now[0] = 133.0
        self.tool(app, "complete", result="ok", duration_s=3.0)
        (row,) = [tool for block in app.transcript.blocks if isinstance(block, ToolGroup) for tool in block.tools]
        self.assertAlmostEqual(row.duration_s, 3.0)

    def test_the_done_line_keeps_the_wall_clock_and_says_the_wait_beside_it(self) -> None:
        app, _client, now = self.clocked()
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        now[0] = 127.0
        armed(app)
        press(app, Key(ESCAPE))
        now[0] = 120.0 + 10.0
        app._on_event(Event("message.complete", SID, {"text": "done"}))
        (summary,) = [block for block in app.transcript.blocks if isinstance(block, TurnSummaryBlock)]
        self.assertEqual((summary.seconds, summary.waited), (40.0, 27.0))
        self.assertTrue([row for row in drawn(app) if row.startswith("✻ 40s (27s waiting on you)")])

    def test_the_next_turn_starts_with_no_wait_whoever_started_it(self) -> None:
        for start in ("prompt", "message.start"):
            with self.subTest(start=start):
                app, _client, now = self.clocked()
                app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
                now[0] = 120.0
                armed(app)
                press(app, Key(ESCAPE))
                app._on_event(Event("message.complete", SID, {"text": "done"}))
                if start == "prompt":
                    type_text(app, "next")
                    press(app, Key(ENTER))
                else:  # a turn the TUI did not start
                    app._on_event(Event("message.start", SID, {}))
                now[0] = 125.0
                app._on_event(Event("message.complete", SID, {"text": "done"}))
                summaries = [block for block in app.transcript.blocks if isinstance(block, TurnSummaryBlock)]
                self.assertEqual([summary.waited for summary in summaries], [20.0, 0.0])

    def test_a_turn_without_a_question_says_no_wait(self) -> None:
        app, _client, now = self.clocked()
        now[0] = 130.0
        app._on_event(Event("message.complete", SID, {"text": "done"}))
        self.assertTrue([row for row in drawn(app) if row.startswith("✻ 40s ") and "waiting" not in row])


class QuestionAttentionTests(unittest.TestCase):
    QUESTIONS = (
        (ServerRequest("srq-1", "approval", {"command": "rm -rf ./x", "description": "recursive delete"}),
         "Hermes asks to run recursive delete"),
        (COLOUR, "Hermes asks: Which colour?"),
        (TEXT_QUESTIONS[0][0], "Hermes needs your sudo password"),
        (TEXT_QUESTIONS[1][0], "Hermes needs API_KEY"),
    )

    def written(self, environ: dict, request: ServerRequest) -> str:
        app, _client = make_app(busy=True, environ=environ)
        app._on_server_request(request)
        return "".join(app.terminal.writes)  # type: ignore[attr-defined]

    def test_every_question_fires_the_turn_ends_channel_with_the_bell_it_always_had(self) -> None:
        for request, said in self.QUESTIONS:
            with self.subTest(method=request.method):
                iterm = self.written({"TERM_PROGRAM": "iTerm.app"}, request)
                self.assertEqual(iterm.count(BELL), 1)
                self.assertIn(f"\x1b]9;{said}\x1b\\", iterm)
                foot = self.written({"TERM": "foot"}, request)
                self.assertIn(f"\x1b]777;notify;OMH;{said}\x1b\\", foot)
                elsewhere = self.written({"TERM_PROGRAM": "Apple_Terminal"}, request)
                self.assertEqual(elsewhere.count(BELL), 1)
                self.assertNotIn("\x1b]9;", elsewhere)
                self.assertNotIn("\x1b]777;", elsewhere)
                quiet = self.written({"TERM_PROGRAM": "iTerm.app", "OMH_TUI_BELL": "0"}, request)
                self.assertNotIn(BELL, quiet)
                self.assertNotIn("\x1b]9;", quiet)

    def test_the_notification_never_says_a_value_and_stays_short(self) -> None:
        long = ServerRequest("srq-2", "clarify", {"questions": [{"qid": "q", "question": "Why " * 60}]})
        written = self.written({"TERM_PROGRAM": "iTerm.app"}, long)
        said = written[written.index("\x1b]9;") + 4 : written.index("\x1b\\", written.index("\x1b]9;"))]
        self.assertLessEqual(len(said), app_module.QUESTION_ALERT_CHARS)
        self.assertTrue(said.startswith("Hermes asks: Why Why"))


class DocumentedQuestionViewTests(unittest.TestCase):
    def test_installation_shows_the_markers_the_slot_draws(self) -> None:
        installation = " ".join((Path(__file__).resolve().parents[1] / "docs" / "INSTALLATION.md").read_text(
            encoding="utf-8").split())
        self.assertIn(f"`{prompts.MORE_ANY_ROWS[0].strip()}`", installation)
        self.assertIn(f"`{prompts.MORE_ROWS[0].strip().format(count=14, s='s')}`", installation)
        self.assertIn("Ctrl+O opens the whole question in the pager: it answers nothing", installation)
        self.assertIn(f"(`{prompts.WAITING.strip().format(count=1, s='')}`)", installation)
        for word in (prompts.OUTCOME_CANCELLED, prompts.OUTCOME_SKIPPED, *prompts.WITHDRAWN_WORDS.values()):
            self.assertIn(f"`✗ {word}", installation)
        self.assertIn("`✓ answered · sudo password`", installation)
        self.assertIn(f"`{app_module.WAITING_GLYPH}{app_module.STATE_WAITING} (11s)`", installation)
        self.assertIn("`✻ 30s" + TURN_WAITED.format(elapsed="27s") + " · 1 tool`", installation)
        self.assertIn("`✗ declined · sudo password`", installation)
        self.assertIn("`✗ " + prompts.WITHDRAWN.format(reason="<Hermes' reason>") + "`", installation)


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
