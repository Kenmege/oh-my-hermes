"""Steering, input modes, effort, the queue rows, the key guide and the approval bell.

Two harnesses. Most cases drive an `App` whose gateway client only records:
the test sends keys through `App._on_input`, reads the calls the app made, and
hands back the answer it wants through `App._on_response`, so a refusal, a
wrong scope or a late answer is one line. Every recorded call is checked
against the gateway contract the way `GatewayClient` checks it before a byte
is written. The end-to-end cases run `main` against the replay gateway from
`test_tui_app`, with `session.steer` and `config.set` answered from the
scenario's answer table in Hermes 6c61c3e27d's shapes.
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from _tui_gateway_replay import Scenario, spans
from _tui_lines import line_text
from test_tui_app import (
    LIVE_ID,
    NO_TERMIOS,
    AppTestCase,
    FakePoller,
    ScriptedTerminal,
    Step,
    startup_answers,
    status_row_of,
)
from omh.tui import app as app_module
from omh.tui import gateway_contract as contract
from omh.tui.app import EFFORT_CYCLE, SLOT_ARM_SECONDS, App, queue_lines
from omh.tui.frame import MODE_ACCEPT, MODE_NORMAL, MODE_PLAN
from omh.tui.gateway import Event, Response, ServerRequest
from omh.tui.keys import CHAR, DOWN, ENTER, TAB, UP, Key
from omh.tui.layout import slot_cap
from omh.tui.pager import Pager
from omh.tui.session_picker import SessionPicker, SessionRow
from omh.tui.slash import parse_slash, plan_invocation
from omh.tui.theme import DEFAULT_THEME
from omh.tui.transcript import NOTICE_ERROR, NOTICE_WARN, NoticeBlock, UserBlock
from omh.tui.width import line_width

SID = "live-1"
CTRL_ENTER = Key(ENTER, ctrl=True)
CTRL_ENTER_BYTES = b"\x1b[13;5u"  # kitty CSI u; modifyOtherKeys sends ESC[27;5;13~
SHIFT_TAB = Key(TAB, shift=True)
SHIFT_TAB_BYTES = b"\x1b[Z"
# Hermes 6c61c3e27d's answers to the per-session switch (`_set_yolo`, methods_config_set.py).
YOLO_IS_ON = {"key": "yolo", "value": "1", "scope": "session"}
YOLO_IS_OFF = {"key": "yolo", "value": "0", "scope": "session"}
YOLO_ON_CALL = ("config.set", {"key": "yolo", "session_id": SID, "value": "on"})
YOLO_OFF_CALL = ("config.set", {"key": "yolo", "session_id": SID, "value": "off"})
CTRL_T = Key(CHAR, "t", ctrl=True)
PLAN_PROMPT = "[/plan — plan mode]\n\nFor this turn, you are in PLAN MODE.\n\nTask to plan:\nfix the flaky test\n"


class RecordingClient:
    """What `App` needs from a gateway client, kept in memory; nothing is spawned."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.answers: list[tuple[str, dict]] = []

    def request(self, method: str, params: dict) -> int:
        problem = contract.call_problem(method, params)
        if problem:
            raise ValueError(problem)
        self.calls.append((method, params))
        return len(self.calls)

    def respond(self, request_id: str, frame: dict) -> None:
        self.answers.append((request_id, frame))

    def named(self, method: str) -> list[dict]:
        return [params for name, params in self.calls if name == method]


def make_app(*, busy: bool = False, rows: int = 24, cols: int = 80, **options: object) -> tuple[App, RecordingClient]:
    client = RecordingClient()
    app = App(ScriptedTerminal(rows, cols), client, options.pop("environ", {}), poller=FakePoller(), **options)  # type: ignore[arg-type]
    app.sid = SID
    app.turn_pending = busy
    return app, client


def type_text(app: App, text: str) -> None:
    for char in text:
        app._on_input(Key(CHAR, char))


def answer(app: App, client: RecordingClient, result: dict | None = None, error: dict | None = None) -> None:
    """Answer the newest call."""
    app._on_response(Response(len(client.calls), result, error))


def notices(app: App, level: str | None = None) -> list[str]:
    return [
        block.text
        for block in app.transcript.blocks
        if isinstance(block, NoticeBlock) and (level is None or block.level == level)
    ]


class SteerTests(unittest.TestCase):
    def test_ctrl_enter_during_a_turn_steers_with_the_typed_text(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "use the other file")
        app._on_input(CTRL_ENTER)
        self.assertEqual(client.calls, [("session.steer", {"session_id": SID, "text": "use the other file"})])
        self.assertEqual(app.composer.text, "")
        self.assertEqual(notices(app), [])  # nothing is claimed before Hermes answers
        answer(app, client, {"status": "queued", "text": "use the other file"})
        self.assertEqual(notices(app), ["↪ steered: use the other file"])
        self.assertEqual(client.named("prompt.submit"), [])
        self.assertFalse(app.queued)

    def test_the_steered_notice_names_only_the_first_line(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "first line")
        app._on_input(Key(ENTER, shift=True))
        type_text(app, "second line")
        app._on_input(CTRL_ENTER)
        self.assertEqual(client.named("session.steer"), [{"session_id": SID, "text": "first line\nsecond line"}])
        answer(app, client, {"status": "queued", "text": "first line\nsecond line"})
        self.assertEqual(notices(app), ["↪ steered: first line"])

    def test_ctrl_enter_while_idle_is_enter(self) -> None:
        sent = []
        for key in (Key(ENTER), CTRL_ENTER):
            app, client = make_app()
            type_text(app, "a prompt")
            app._on_input(key)
            sent.append(client.calls)
        self.assertEqual(sent[0], [("prompt.submit", {"session_id": SID, "text": "a prompt"})])
        self.assertEqual(sent[1], sent[0])

    def test_a_rejected_steer_goes_as_a_normal_prompt(self) -> None:
        # The turn ended before the text arrived: idle by the time the answer is read, so it is sent now.
        app, client = make_app(busy=True)
        type_text(app, "too late")
        app._on_input(CTRL_ENTER)
        app.turn_pending = False
        answer(app, client, {"status": "rejected", "text": "too late"})
        self.assertEqual(client.calls[1:], [("prompt.submit", {"session_id": SID, "text": "too late"})])
        self.assertEqual(notices(app), [])

    def test_a_rejected_steer_while_the_tui_still_waits_on_a_turn_joins_the_queue(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "too early")
        app._on_input(CTRL_ENTER)
        answer(app, client, {"status": "rejected", "text": "too early"})
        self.assertEqual([text for text, *_ in app.queued], ["too early"])
        self.assertEqual(client.named("prompt.submit"), [])

    def test_a_steer_hermes_cannot_take_is_not_lost(self) -> None:
        # 4010: the agent is still starting, or takes no steer.
        app, client = make_app(busy=True)
        type_text(app, "keep this")
        app._on_input(CTRL_ENTER)
        answer(app, client, error={"code": 4010, "message": "agent does not support steer"})
        self.assertEqual([text for text, *_ in app.queued], ["keep this"])
        (warning,) = notices(app, NOTICE_WARN)
        self.assertIn("4010", warning)
        self.assertIn("Sent as a prompt instead", warning)

    def test_ctrl_enter_with_nothing_typed_sends_nothing(self) -> None:
        app, client = make_app(busy=True)
        app._on_input(CTRL_ENTER)
        type_text(app, "   ")
        app._on_input(CTRL_ENTER)
        self.assertEqual(client.calls, [])

    def test_ctrl_enter_on_a_slash_command_runs_the_command(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "/queue")
        app._on_input(CTRL_ENTER)
        self.assertEqual(client.calls, [])
        self.assertEqual(notices(app), ["Nothing is queued."])

    def test_ctrl_enter_answers_an_open_question_instead_of_steering(self) -> None:
        now = [100.0]
        app, client = make_app(busy=True, clock=lambda: now[0])
        app._on_server_request(ServerRequest("srq-1", "vault.code", {}))
        now[0] += SLOT_ARM_SECONDS
        type_text(app, "123456")
        app._on_input(CTRL_ENTER)
        self.assertEqual(client.answers, [("srq-1", {"result": {"value": "123456"}})])
        self.assertEqual(client.calls, [])

    def test_the_steer_command_steers_a_running_turn_and_sends_a_prompt_when_idle(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "/steer check the tests too")
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls, [("session.steer", {"session_id": SID, "text": "check the tests too"})])
        answer(app, client, {"status": "queued", "text": "check the tests too"})
        self.assertEqual(notices(app), ["↪ steered: check the tests too"])

        app, client = make_app()
        type_text(app, "/steer check the tests too")
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls, [("prompt.submit", {"session_id": SID, "text": "check the tests too"})])

    def test_the_steer_command_without_text_says_how_and_sends_nothing(self) -> None:
        for busy in (True, False):
            with self.subTest(busy=busy):
                app, client = make_app(busy=busy)
                type_text(app, "/steer")
                app._on_input(Key(ENTER))
                self.assertEqual(client.calls, [])
                self.assertEqual(notices(app, NOTICE_WARN), ["Usage: /steer <text to add to the running turn>"])

    def test_a_queued_prompt_is_sent_as_a_prompt_never_as_a_steer(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "wait for the turn")
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls, [])
        app.turn_pending = False
        app._drain_queue()
        self.assertEqual(
            client.calls, [("prompt.submit", {"session_id": SID, "text": "wait for the turn", "queued": True})]
        )

    def test_only_ctrl_enter_and_the_steer_command_can_steer(self) -> None:
        # Steer text is what the person typed and sent, so `session.steer` has one sender with two callers.
        tree = ast.parse(Path(app_module.__file__).read_text(encoding="utf-8"))
        callers: set[str] = set()
        senders: set[str] = set()
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Constant) and node.value == "session.steer":
                    senders.add(function.name)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_steer":
                    callers.add(function.name)
        self.assertEqual(senders, {"_steer"})
        self.assertEqual(callers, {"_steer_or_submit", "_steer_command"})


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class SteerReplayTests(AppTestCase):
    def steer_run(self, status: str, *segments: str) -> object:
        answers = (*startup_answers(), ("session.steer", {"result": {"status": status, "text": "second prompt"}}))
        return self.prepare(Scenario("session", spans("session", "startup", "create", *segments), answers=answers))

    def test_ctrl_enter_as_a_key_report_steers_and_the_notice_lands(self) -> None:
        run = self.steer_run("queued", "plain_turn")
        terminal = run.terminal
        terminal.script(
            # One read: Ctrl+Enter lands while the first prompt's turn is pending.
            Step(b"first prompt\rsecond prompt" + CTRL_ENTER_BYTES, when=terminal.idle, label="idle"),
            Step(b"\x04", when=lambda: "↪ steered: second prompt" in terminal.history() and terminal.idle(), label="steered"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        (steer,) = run.calls("session.steer")
        self.assertEqual(steer["params"], {"session_id": LIVE_ID, "text": "second prompt"})
        self.assertEqual(len(run.calls("prompt.submit")), 1)

    def test_a_rejected_steer_is_submitted_once_the_turn_is_over(self) -> None:
        run = self.steer_run("rejected", "plain_turn", "smart_approved_turn")
        terminal = run.terminal
        terminal.script(
            Step(b"first prompt\rsecond prompt" + CTRL_ENTER_BYTES, when=terminal.idle, label="idle"),
            Step(b"\x04", when=lambda: "cleanly)." in terminal.history() and terminal.idle(), label="turn 2"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        first, second = run.calls("prompt.submit")
        self.assertEqual(first["params"], {"session_id": LIVE_ID, "text": "first prompt"})
        self.assertEqual(second["params"], {"session_id": LIVE_ID, "text": "second prompt"})
        self.assertNotIn("steered", terminal.history())


def session_info(app: App, **payload: object) -> None:
    app._on_event(Event("session.info", app.sid, dict(payload)))


def in_accept(**options: object) -> tuple[App, RecordingClient]:
    """An app Hermes confirmed accept for, with the calls so far forgotten."""
    app, client = make_app(**options)
    app._on_input(SHIFT_TAB)
    answer(app, client, YOLO_IS_ON)
    client.calls.clear()
    return app, client


class ModeCycleTests(unittest.TestCase):
    def test_shift_tab_cycles_normal_accept_plan_normal(self) -> None:
        app, client = make_app()
        app._on_input(SHIFT_TAB)
        self.assertEqual(client.calls, [YOLO_ON_CALL])
        self.assertEqual(app.mode, MODE_NORMAL)  # accept is shown once Hermes confirms it, not before
        answer(app, client, YOLO_IS_ON)
        self.assertEqual(app.mode, MODE_ACCEPT)

        app._on_input(SHIFT_TAB)
        self.assertEqual(client.calls, [YOLO_ON_CALL, YOLO_OFF_CALL])
        self.assertEqual(app.mode, MODE_ACCEPT)  # plan waits for the off to be confirmed
        answer(app, client, YOLO_IS_OFF)
        self.assertEqual(app.mode, MODE_PLAN)

        app._on_input(SHIFT_TAB)
        self.assertEqual(app.mode, MODE_NORMAL)
        self.assertEqual(client.calls, [YOLO_ON_CALL, YOLO_OFF_CALL])  # plan is the TUI's flag: no call
        self.assertEqual(notices(app), [])

    def test_a_yolo_answer_that_is_not_this_session_only_is_a_failure(self) -> None:
        refusals = {
            "scope global": {"key": "yolo", "value": "1", "scope": "global"},
            "no scope": {"key": "yolo", "value": "1"},
            "value off": {"key": "yolo", "value": "0", "scope": "session"},
            "value missing": {"key": "yolo", "scope": "session"},
            "value true": {"key": "yolo", "value": True, "scope": "session"},
        }
        for name, result in refusals.items():
            with self.subTest(name):
                app, client = make_app()
                app._on_input(SHIFT_TAB)
                answer(app, client, result)
                self.assertEqual(app.mode, MODE_NORMAL)
                self.assertEqual(client.calls, [YOLO_ON_CALL, YOLO_OFF_CALL])  # the explicit off
                (notice,) = notices(app, NOTICE_ERROR)
                self.assertIn("Accept all stays off", notice)
                answer(app, client, YOLO_IS_OFF)
                self.assertEqual(len(notices(app)), 1)
                self.assertEqual(len(client.calls), 2)

    def test_an_error_answer_to_accept_is_a_failure_too(self) -> None:
        # 5001 can follow a switch that was already flipped, so the explicit off still goes out.
        app, client = make_app()
        app._on_input(SHIFT_TAB)
        answer(app, client, error={"code": 5001, "message": "boom"})
        self.assertEqual(app.mode, MODE_NORMAL)
        self.assertEqual(client.calls, [YOLO_ON_CALL, YOLO_OFF_CALL])
        (notice,) = notices(app, NOTICE_ERROR)
        self.assertIn("5001: boom", notice)

    def test_a_switch_back_hermes_does_not_confirm_is_said_and_not_retried(self) -> None:
        app, client = make_app()
        app._on_input(SHIFT_TAB)
        answer(app, client, {"key": "yolo", "value": "1", "scope": "global"})
        answer(app, client, {"key": "yolo", "value": "1", "scope": "session"})
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(notices(app, NOTICE_ERROR)), 2)
        self.assertIn("did not confirm the switch back", notices(app)[-1])

    def test_without_a_session_nothing_is_sent(self) -> None:
        app, client = make_app()
        app.sid = ""
        app._on_input(SHIFT_TAB)
        self.assertEqual(client.calls, [])
        self.assertEqual(app.mode, MODE_NORMAL)
        self.assertEqual(len(notices(app, NOTICE_WARN)), 1)
        self.assertFalse(app.mode_request_open)

    def test_an_off_hermes_does_not_confirm_leaves_accept_shown(self) -> None:
        for name, result, error in (
            ("still on", YOLO_IS_ON, None),
            ("scope global", {"key": "yolo", "value": "0", "scope": "global"}, None),
            ("error", None, {"code": 4001, "message": "session not found"}),
        ):
            with self.subTest(name):
                app, client = in_accept()
                app._on_input(SHIFT_TAB)
                answer(app, client, result, error)
                self.assertEqual(app.mode, MODE_ACCEPT)
                self.assertEqual(client.calls, [YOLO_OFF_CALL])  # no loop of further calls
                (notice,) = notices(app, NOTICE_ERROR)
                self.assertIn("may still be on", notice)

    def test_an_off_while_hermes_still_reports_approvals_off_keeps_accept(self) -> None:
        # approvals.mode off, or --yolo: the session switch is off and Hermes still asks nothing.
        app, client = in_accept()
        app._on_input(SHIFT_TAB)
        session_info(app, yolo=True)
        answer(app, client, YOLO_IS_OFF)
        self.assertEqual(app.mode, MODE_ACCEPT)
        (notice,) = notices(app, NOTICE_WARN)
        self.assertIn("still reports approvals off", notice)

    def test_the_session_info_that_precedes_a_confirmed_off_does_not_cost_plan(self) -> None:
        app, client = in_accept()
        app._on_input(SHIFT_TAB)
        session_info(app, yolo=False)  # Hermes emits it before it answers the call
        self.assertEqual(app.mode, MODE_NORMAL)
        answer(app, client, YOLO_IS_OFF)
        self.assertEqual(app.mode, MODE_PLAN)
        self.assertEqual(notices(app), [])

    def test_a_second_shift_tab_waits_for_the_first_answer(self) -> None:
        app, client = make_app()
        app._on_input(SHIFT_TAB)
        app._on_input(SHIFT_TAB)
        self.assertEqual(client.calls, [YOLO_ON_CALL])
        answer(app, client, YOLO_IS_ON)
        app._on_input(SHIFT_TAB)
        self.assertEqual(client.calls, [YOLO_ON_CALL, YOLO_OFF_CALL])

    def test_shift_tab_does_nothing_while_something_else_has_the_keys(self) -> None:
        def approval(app: App) -> None:
            app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))

        def question(app: App) -> None:
            app._on_server_request(ServerRequest("srq-1", "sudo", {"command": "apt install x"}))

        def menu(app: App) -> None:
            type_text(app, "/he")

        def picker(app: App) -> None:
            app.picker = SessionPicker([SessionRow("stored-1", "a title", "", 0.0, 1)], current="")

        def pager(app: App) -> None:
            app.pager = Pager("history", [])

        for opened in (approval, question, menu, picker, pager):
            with self.subTest(opened.__name__):
                now = [100.0]
                app, client = make_app(clock=lambda: now[0])
                opened(app)
                now[0] += SLOT_ARM_SECONDS
                app._on_input(SHIFT_TAB)
                self.assertEqual(client.calls, [])
                self.assertEqual(client.answers, [])
                self.assertEqual(app.mode, MODE_NORMAL)

    def test_no_call_the_app_builds_carries_a_scope(self) -> None:
        tree = ast.parse(Path(app_module.__file__).read_text(encoding="utf-8"))
        keys = {
            key.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            for key in node.keys
            if isinstance(key, ast.Constant)
        }
        self.assertIn("session_id", keys)  # the walk does see the params the app builds
        self.assertNotIn("scope", keys)


class ModeFollowsHermesTests(unittest.TestCase):
    def test_session_info_decides_whether_accept_is_shown(self) -> None:
        app, _client = make_app()
        session_info(app, yolo=True)
        self.assertEqual(app.mode, MODE_ACCEPT)
        session_info(app, yolo=False)
        self.assertEqual(app.mode, MODE_NORMAL)
        app.mode = MODE_PLAN
        session_info(app, yolo=False)
        self.assertEqual(app.mode, MODE_PLAN)
        session_info(app, yolo=True)  # approvals off outranks the TUI's own plan flag
        self.assertEqual(app.mode, MODE_ACCEPT)

    def test_a_session_info_without_a_yolo_word_changes_nothing(self) -> None:
        for payload in ({}, {"yolo": None}, {"yolo": "1"}, {"yolo": 1}):
            for mode in (MODE_NORMAL, MODE_ACCEPT, MODE_PLAN):
                with self.subTest(payload=payload, mode=mode):
                    app, _client = make_app()
                    app.mode = mode
                    session_info(app, **payload)
                    self.assertEqual(app.mode, mode)

    def test_another_sessions_info_is_not_this_sessions_mode(self) -> None:
        app, _client = make_app()
        app._on_event(Event("session.info", "someone-else", {"yolo": True}))
        self.assertEqual(app.mode, MODE_NORMAL)

    def test_a_new_session_starts_from_what_its_own_info_says(self) -> None:
        for info, expected in (({}, MODE_NORMAL), ({"yolo": False}, MODE_NORMAL), ({"yolo": True}, MODE_ACCEPT)):
            with self.subTest(info=info):
                app, _client = in_accept()
                app._adopt_session({"session_id": "live-2", "info": info})
                self.assertEqual(app.mode, expected)

    def test_plan_stays_on_across_a_new_session(self) -> None:
        app, _client = make_app()
        app.mode = MODE_PLAN
        app._adopt_session({"session_id": "live-2", "info": {}})
        self.assertEqual(app.mode, MODE_PLAN)

    def test_an_answer_for_a_session_already_left_changes_nothing(self) -> None:
        app, client = make_app()
        app._on_input(SHIFT_TAB)
        app._adopt_session({"session_id": "live-2", "info": {}})
        answer(app, client, YOLO_IS_ON)
        self.assertEqual(app.mode, MODE_NORMAL)
        self.assertEqual(notices(app), [])
        app._on_input(SHIFT_TAB)  # and the new session's key is not stuck behind the old call
        self.assertEqual(client.calls[-1], ("config.set", {"key": "yolo", "session_id": "live-2", "value": "on"}))


class PlanModeTests(unittest.TestCase):
    def plan_app(self, busy: bool = False) -> tuple[App, RecordingClient]:
        app, client = make_app(busy=busy)
        app.mode = MODE_PLAN
        return app, client

    def test_a_prompt_goes_as_slash_plan_through_the_slash_command_path(self) -> None:
        app, client = self.plan_app()
        type_text(app, "fix the flaky test")
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls, [("slash.exec", {"command": "plan fix the flaky test", "session_id": SID})])
        answer(app, client, error={"code": contract.NOT_MINE, "message": "pending-input command"})
        self.assertEqual(
            client.calls[1], ("command.dispatch", {"name": "plan", "arg": "fix the flaky test", "session_id": SID})
        )
        answer(app, client, {"type": "send", "message": PLAN_PROMPT})
        self.assertEqual(client.calls[2], ("prompt.submit", {"session_id": SID, "text": PLAN_PROMPT}))
        shown = [block.text for block in app.transcript.blocks if isinstance(block, UserBlock)]
        self.assertEqual(shown, ["/plan fix the flaky test"])

    def test_a_typed_slash_plan_in_normal_mode_keeps_showing_what_hermes_built(self) -> None:
        app, client = make_app()
        type_text(app, "/plan fix the flaky test")
        app._on_input(Key(ENTER))
        answer(app, client, error={"code": contract.NOT_MINE, "message": "pending-input command"})
        answer(app, client, {"type": "send", "message": PLAN_PROMPT})
        shown = [block.text for block in app.transcript.blocks if isinstance(block, UserBlock)]
        self.assertEqual(shown, [PLAN_PROMPT])

    def test_normal_and_accept_send_the_prompt_as_typed(self) -> None:
        for mode in (MODE_NORMAL, MODE_ACCEPT):
            with self.subTest(mode=mode):
                app, client = make_app()
                app.mode = mode
                type_text(app, "fix the flaky test")
                app._on_input(Key(ENTER))
                self.assertEqual(client.calls, [("prompt.submit", {"session_id": SID, "text": "fix the flaky test"})])

    def test_a_slash_command_typed_in_plan_mode_is_not_wrapped(self) -> None:
        app, client = self.plan_app()
        type_text(app, "/queue")
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls, [])
        self.assertEqual(notices(app), ["Nothing is queued."])

    def test_an_answer_to_a_question_is_never_wrapped(self) -> None:
        requests = (
            ("clarify", {"questions": [{"qid": "q0", "question": "Which?", "choices": []}]},
             {"result": {"answers": {"q0": "the blue one"}}}),
            ("sudo", {"command": "apt install x"}, {"result": {"value": "the blue one"}}),
            ("secret", {"env_var": "TOKEN_X"}, {"result": {"value": "the blue one"}}),
            ("vault.code", {}, {"result": {"value": "the blue one"}}),
        )
        for method, params, expected in requests:
            with self.subTest(method=method):
                now = [100.0]
                app, client = make_app(clock=lambda: now[0])
                app.mode = MODE_PLAN
                app._on_server_request(ServerRequest("srq-1", method, params))
                now[0] += SLOT_ARM_SECONDS
                type_text(app, "the blue one")
                app._on_input(Key(ENTER))
                self.assertEqual(client.answers, [("srq-1", expected)])
                self.assertEqual(client.calls, [])

    def test_a_steer_in_plan_mode_is_sent_as_typed_and_its_fallback_is_a_plan_prompt(self) -> None:
        app, client = self.plan_app(busy=True)
        type_text(app, "also the docs")
        app._on_input(CTRL_ENTER)
        self.assertEqual(client.calls, [("session.steer", {"session_id": SID, "text": "also the docs"})])
        app.turn_pending = False
        answer(app, client, {"status": "rejected", "text": "also the docs"})
        self.assertEqual(client.calls[1], ("slash.exec", {"command": "plan also the docs", "session_id": SID}))

    def test_slash_plan_is_a_command_hermes_runs_not_one_the_tui_turns_away(self) -> None:
        for invocation in (parse_slash("/plan fix it"), plan_invocation("fix it")):
            with self.subTest(invocation=invocation):
                self.assertIsNotNone(invocation)
                self.assertIsNone(invocation.command)  # unrouted: it goes to slash.exec
                self.assertEqual(invocation.text, "plan fix it")
        self.assertEqual(plan_invocation("  two\nlines \n").shown, "/plan two\nlines")


def effort_call(level: str, sid: str = SID) -> tuple[str, dict]:
    return ("config.set", {"key": "reasoning", "session_id": sid, "value": level})


def effort_set(level: str) -> dict:
    """Hermes 6c61c3e27d's answer to a session effort (`_set_reasoning`): the word it took."""
    return {"key": "reasoning", "value": level}


class EffortTests(unittest.TestCase):
    def test_ctrl_t_steps_the_effort_and_shows_it_once_hermes_confirms(self) -> None:
        app, client = make_app()
        seen = []
        for _ in range(len(EFFORT_CYCLE) + 1):
            app._on_input(CTRL_T)
            asked = client.calls[-1][1]["value"]
            self.assertNotEqual(app.effort, asked)  # shown only once Hermes answers
            answer(app, client, effort_set(asked))
            self.assertEqual(app.effort, asked)
            seen.append(asked)
        self.assertEqual(seen, ["low", "medium", "high", "xhigh", "max", "low"])
        self.assertEqual(client.calls, [effort_call(level) for level in seen])
        self.assertEqual(notices(app), [])

    def test_a_level_outside_the_cycle_steps_to_low(self) -> None:
        for level in ("", "none", "minimal", "ultra", "something-new"):
            with self.subTest(level=level):
                app, client = make_app()
                app.effort = level
                app._on_input(CTRL_T)
                self.assertEqual(client.calls, [effort_call("low")])

    def test_presses_before_the_answer_step_on_from_what_was_asked(self) -> None:
        app, client = make_app()
        app.effort = "low"
        app._on_input(CTRL_T)
        app._on_input(CTRL_T)
        self.assertEqual(client.calls, [effort_call("medium"), effort_call("high")])
        app._on_response(Response(1, effort_set("medium"), None))
        self.assertEqual(app.effort, "medium")
        app._on_response(Response(2, effort_set("high"), None))
        self.assertEqual(app.effort, "high")
        app._on_input(CTRL_T)
        self.assertEqual(client.calls[-1], effort_call("xhigh"))

    def test_a_refused_effort_says_so_and_leaves_the_display(self) -> None:
        refusals = (
            ("an error", None, {"code": 4002, "message": "unknown reasoning value: low"}),
            ("another level", effort_set("medium"), None),
            ("no value", {"key": "reasoning"}, None),
        )
        for name, result, error in refusals:
            with self.subTest(name):
                app, client = make_app()
                app.effort = "high"
                app._on_input(CTRL_T)
                answer(app, client, result, error)
                self.assertEqual(app.effort, "high")
                (notice,) = notices(app, NOTICE_WARN)
                self.assertIn("did not set the reasoning effort to xhigh", notice)
                app._on_input(CTRL_T)  # and the next press asks for the same step again
                self.assertEqual(client.calls[-1], effort_call("xhigh"))

    def test_ctrl_t_does_nothing_while_a_question_is_open_or_without_a_session(self) -> None:
        for method, params in (("approval", {"command": "rm -rf ./x"}), ("sudo", {"command": "apt install x"})):
            with self.subTest(method=method):
                now = [100.0]
                app, client = make_app(clock=lambda: now[0])
                app._on_server_request(ServerRequest("srq-1", method, params))
                now[0] += SLOT_ARM_SECONDS
                app._on_input(CTRL_T)
                self.assertEqual((client.calls, client.answers), ([], []))
                self.assertEqual(app.composer.text, "")
        app, client = make_app()
        app.sid = ""
        app._on_input(CTRL_T)
        self.assertEqual(client.calls, [])
        self.assertEqual(len(notices(app, NOTICE_WARN)), 1)

    def test_session_info_is_where_the_shown_effort_comes_from(self) -> None:
        app, _client = make_app()
        session_info(app, reasoning_effort="high")
        self.assertEqual(app.effort, "high")
        session_info(app, reasoning_effort="none", yolo=False)
        self.assertEqual(app.effort, "none")
        for payload in ({}, {"reasoning_effort": None}, {"reasoning_effort": 3}):
            session_info(app, **payload)
            self.assertEqual(app.effort, "none")
        session_info(app, reasoning_effort="")  # Hermes' word for the provider's default
        self.assertEqual(app.effort, "")

    def test_a_new_session_shows_its_own_effort(self) -> None:
        app, client = make_app()
        app.effort = "high"
        app._on_input(CTRL_T)
        app._adopt_session({"session_id": "live-2", "info": {}})
        self.assertEqual(app.effort, "")
        answer(app, client, effort_set("xhigh"))  # the session left answers late
        self.assertEqual(app.effort, "")
        app._adopt_session({"session_id": "live-3", "info": {"reasoning_effort": "max"}})
        self.assertEqual(app.effort, "max")
        app._on_input(CTRL_T)
        self.assertEqual(client.calls[-1], effort_call("low", "live-3"))

    def test_the_effort_command_takes_any_hermes_level(self) -> None:
        for level in contract.REASONING_LEVELS:
            with self.subTest(level=level):
                app, client = make_app()
                type_text(app, f"/effort {level.upper()}")
                app._on_input(Key(ENTER))
                self.assertEqual(client.calls, [effort_call(level)])
                answer(app, client, effort_set(level))
                self.assertEqual(app.effort, level)

    def test_the_effort_command_sends_nothing_that_is_not_a_level(self) -> None:
        # show, hide, on, off, full and clamp are display settings under the same Hermes key.
        for word in ("show", "hide", "on", "off", "full", "clamp", "hgih", "high --global"):
            with self.subTest(word=word):
                app, client = make_app()
                type_text(app, f"/effort {word}")
                app._on_input(Key(ENTER))
                self.assertEqual(client.calls, [])
                (notice,) = notices(app, NOTICE_WARN)
                self.assertIn("none, minimal, low, medium, high, xhigh, max, ultra", notice)

    def test_the_effort_command_alone_names_the_level_shown(self) -> None:
        app, client = make_app()
        app.effort = "high"
        type_text(app, "/effort")
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls, [])
        (notice,) = notices(app)
        self.assertIn("Reasoning effort high.", notice)


def drawn(app: App) -> list[str]:
    """The screen after one repaint."""
    app._render()
    return app.terminal.screen.lines()  # type: ignore[attr-defined]


def composer_index(screen: list[str]) -> int:
    """The composer's first row: the one under the upper of the frame's two rules (terminals of 7 rows or more)."""
    rules = [index for index, line in enumerate(screen) if line.strip() and set(line.strip()) <= set("─━┄")]
    return rules[-2] + 1


def queue(app: App, *prompts: str) -> None:
    for prompt in prompts:
        app.queued.append((prompt, "", ""))


class QueueLinesTests(unittest.TestCase):
    def rows(self, prompts: list[str], width: int = 40, max_rows: int = 8) -> list[str]:
        return [line_text(line) for line in queue_lines(prompts, width, max_rows, DEFAULT_THEME)]

    def test_nothing_queued_draws_nothing(self) -> None:
        self.assertEqual(self.rows([]), [])
        self.assertEqual(self.rows(["a prompt"], max_rows=0), [])

    def test_each_prompt_is_one_row_and_the_last_ends_with_the_edit_key(self) -> None:
        self.assertEqual(self.rows(["fix the failing test"]), ["⧗ fix the failing test" + " " * 12 + "↑ edit"])
        rows = self.rows(["fix the failing test", "then update the changelog"])
        self.assertEqual(rows, ["⧗ fix the failing test", "⧗ then update the changelog" + " " * 7 + "↑ edit"])

    def test_the_glyph_is_muted_the_prompt_is_text_and_the_key_is_a_hint(self) -> None:
        (line,) = queue_lines(["fix it"], 40, 8, DEFAULT_THEME)
        self.assertEqual(
            [(span.text.strip(), span.style) for span in line if span.text.strip()],
            [("⧗", DEFAULT_THEME.muted), ("fix it", DEFAULT_THEME.text), ("↑ edit", DEFAULT_THEME.hint)],
        )

    def test_more_than_three_are_counted_on_a_last_row(self) -> None:
        rows = self.rows(["one", "two", "three", "four", "five"])
        self.assertEqual(rows[:3], ["⧗ one", "⧗ two", "⧗ three"])
        self.assertEqual(rows[3], "+2 more" + " " * 27 + "↑ edit")
        self.assertEqual(len(self.rows(["one", "two", "three"])), 3)  # exactly three need no count

    def test_only_the_first_line_of_a_prompt_is_named(self) -> None:
        self.assertEqual(self.rows(["\nfirst line\nsecond line"], width=30), ["⧗ first line" + " " * 12 + "↑ edit"])

    def test_a_short_slot_names_fewer_and_counts_the_rest(self) -> None:
        prompts = ["one", "two", "three", "four"]
        self.assertEqual([row.split("  ")[0] for row in self.rows(prompts, max_rows=3)], ["⧗ one", "⧗ two", "+2 more"])
        self.assertEqual([row.split("  ")[0] for row in self.rows(prompts[:3], max_rows=2)], ["⧗ one", "+2 more"])
        self.assertEqual([row.split("  ")[0] for row in self.rows(prompts[:2], max_rows=2)], ["⧗ one", "⧗ two"])
        self.assertEqual([row.split("  ")[0] for row in self.rows(prompts, max_rows=1)], ["+4 more"])

    def test_no_row_is_wider_than_the_terminal_and_the_key_survives_a_long_prompt(self) -> None:
        prompts = ["a prompt that goes on for a good while longer than any terminal here is wide. " * 2] * 5
        for width in range(20, 121):
            lines = queue_lines(prompts, width, 8, DEFAULT_THEME)
            with self.subTest(width=width):
                self.assertTrue(all(line_width(line) <= width for line in lines))
                self.assertEqual(line_width(lines[-1]), width)
                self.assertTrue(line_text(lines[-1]).endswith("↑ edit"))
                self.assertTrue(line_text(lines[0]).endswith("…"))

    def test_text_hermes_built_cannot_carry_an_escape_onto_the_row(self) -> None:
        (row,) = self.rows(["\x1b[31mred\x1b[0m \x07alert\ttab"], width=40)
        self.assertTrue(row.startswith("⧗ red alert"))
        self.assertNotIn("\x1b", row)


class QueueTests(unittest.TestCase):
    def test_queued_prompts_never_move_the_composer(self) -> None:
        for rows, cols in ((7, 40), (9, 40), (10, 80), (12, 80), (24, 80), (40, 120)):
            for menu in (False, True):
                with self.subTest(rows=rows, cols=cols, menu=menu):
                    app, _client = make_app(busy=True, rows=rows, cols=cols)
                    empty = composer_index(drawn(app))
                    seen = []
                    for count in (1, 2, 3, 6):
                        app.queued.clear()
                        queue(app, *(f"prompt {number}" for number in range(count)))
                        if menu:
                            app.composer.replace(0, len(app.composer.text), "/")
                        seen.append(composer_index(drawn(app)))
                    self.assertEqual(seen, [empty] * 4)

    def test_the_rows_sit_right_above_the_composers_top_rule(self) -> None:
        app, _client = make_app(busy=True)
        queue(app, "fix the failing test", "then update the changelog")
        screen = drawn(app)
        top = composer_index(screen)
        self.assertEqual(set(screen[top - 1]), {"─"})
        self.assertEqual(screen[top - 3].rstrip(), "⧗ fix the failing test")
        self.assertTrue(screen[top - 2].startswith("⧗ then update the changelog"))
        self.assertTrue(screen[top - 2].rstrip().endswith("↑ edit"))
        self.assertIn("esc to interrupt", screen[top - 4])  # the turn's activity row stays above them

    def test_a_completion_menu_keeps_the_rows_left_and_stays_above_the_queue(self) -> None:
        app, _client = make_app(busy=True, rows=24)
        queue(app, "one", "two", "three", "four")
        type_text(app, "/")
        lines = [line_text(line) for line in app._slot_lines(80, 24)]
        self.assertEqual(len(lines), slot_cap(24, 80))
        self.assertTrue(all(line.lstrip().startswith("/") for line in lines[:4]), lines)
        self.assertEqual([line.split("  ")[0] for line in lines[4:]], ["⧗ one", "⧗ two", "⧗ three", "+1 more"])

    def test_a_question_or_the_session_picker_hides_the_rows(self) -> None:
        app, _client = make_app(busy=True)
        queue(app, "fix the failing test")
        self.assertIn("⧗ fix the failing test", "\n".join(drawn(app)))
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        self.assertNotIn("⧗", "\n".join(drawn(app)))
        app._answer(app.slot.decline())  # type: ignore[union-attr]
        app.picker = SessionPicker([SessionRow("stored-1", "a title", "", 0.0, 1)], current="")
        self.assertNotIn("⧗", "\n".join(drawn(app)))

    def test_up_on_an_empty_composer_takes_every_queued_prompt_back(self) -> None:
        app, client = make_app(busy=True)
        queue(app, "fix the failing test", "then update\nthe changelog", "and push")
        app._on_input(Key(UP))
        self.assertEqual(app.composer.text, "fix the failing test\nthen update\nthe changelog\nand push")
        self.assertFalse(app.queued)
        self.assertEqual(client.calls, [])
        self.assertNotIn("⧗", "\n".join(drawn(app)))

    def test_up_with_nothing_queued_still_walks_the_input_history(self) -> None:
        app, _client = make_app()
        type_text(app, "an earlier prompt")
        app._on_input(Key(ENTER))
        self.assertEqual(app.composer.text, "")
        app._on_input(Key(UP))
        self.assertEqual(app.composer.text, "an earlier prompt")

    def test_up_with_text_in_the_composer_leaves_the_queue_alone(self) -> None:
        app, _client = make_app(busy=True)
        queue(app, "queued")
        type_text(app, "a draft")
        app._on_input(Key(UP))
        self.assertEqual([text for text, *_ in app.queued], ["queued"])
        self.assertNotIn("queued", app.composer.text)

    def test_down_and_other_keys_do_not_take_the_queue_back(self) -> None:
        app, _client = make_app(busy=True)
        queue(app, "queued")
        for key in (Key(DOWN), Key(TAB), Key(CHAR, "k", ctrl=True)):
            app._on_input(key)
        self.assertEqual((len(app.queued), app.composer.text), (1, ""))

    def test_up_does_nothing_to_the_queue_while_something_else_has_the_keys(self) -> None:
        def approval(app: App) -> None:
            app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))

        def question(app: App) -> None:
            choices = {"questions": [{"qid": "q0", "question": "Which?", "choices": ["red", "blue"]}]}
            app._on_server_request(ServerRequest("srq-1", "clarify", choices))

        def picker(app: App) -> None:
            app.picker = SessionPicker([SessionRow("stored-1", "a title", "", 0.0, 1)], current="")

        def pager(app: App) -> None:
            app.pager = Pager("history", [])

        for opened in (approval, question, picker, pager):
            with self.subTest(opened.__name__):
                now = [100.0]
                app, client = make_app(busy=True, clock=lambda: now[0])
                queue(app, "queued")
                opened(app)
                now[0] += SLOT_ARM_SECONDS
                app._on_input(Key(UP))
                self.assertEqual([text for text, *_ in app.queued], ["queued"])
                self.assertEqual(app.composer.text, "")
                self.assertEqual((client.calls, client.answers), ([], []))

    def test_up_then_enter_on_an_approval_with_prompts_queued_still_denies(self) -> None:
        now = [100.0]
        app, client = make_app(busy=True, clock=lambda: now[0])
        queue(app, "queued")
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        now[0] += SLOT_ARM_SECONDS
        app._on_input(Key(UP))
        app._on_input(Key(ENTER))
        self.assertEqual(client.answers, [("srq-1", {"result": {"choice": "deny"}})])
        self.assertEqual(len(app.queued), 1)

    def test_up_then_enter_puts_the_prompts_back_in_the_queue_as_one(self) -> None:
        app, client = make_app(busy=True)
        queue(app, "first", "second")
        app._on_input(Key(UP))
        app._on_input(Key(ENTER))
        self.assertEqual([text for text, *_ in app.queued], ["first\nsecond"])
        self.assertEqual(client.calls, [])

    def test_a_prompt_a_command_built_comes_back_as_what_was_typed(self) -> None:
        # Plan mode wrote /plan around the prompt, so the prompt comes back; a typed command comes back as typed.
        app, client = make_app(busy=True)
        app.mode = MODE_PLAN
        type_text(app, "fix the flaky test")
        app._on_input(Key(ENTER))
        answer(app, client, error={"code": contract.NOT_MINE, "message": "pending-input command"})
        answer(app, client, {"type": "send", "message": PLAN_PROMPT})
        app.mode = MODE_NORMAL
        type_text(app, "/learn the build")
        app._on_input(Key(ENTER))
        answer(app, client, error={"code": contract.NOT_MINE, "message": "pending-input command"})
        answer(app, client, {"type": "skill", "message": "a long skill prompt", "display": "learn: the build"})
        self.assertEqual(
            list(app.queued),
            [
                (PLAN_PROMPT, "/plan fix the flaky test", "fix the flaky test"),
                ("a long skill prompt", "learn: the build", "/learn the build"),
            ],
        )
        rows = [line_text(line) for line in app._slot_lines(80, 24)]
        self.assertEqual(rows[0], "⧗ /plan fix the flaky test")
        self.assertTrue(rows[1].startswith("⧗ learn: the build"))
        app._on_input(Key(UP))
        self.assertEqual(app.composer.text, "fix the flaky test\n/learn the build")


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class ModeReplayTests(AppTestCase):
    def test_queued_prompts_show_above_the_rule_and_up_takes_them_back_unsent(self) -> None:
        run = self.prepare(
            Scenario("session", spans("session", "startup", "create", "interrupted_turn"), answers=startup_answers())
        )
        terminal = run.terminal

        def running() -> bool:
            return terminal.status_row().rstrip().endswith("running")

        terminal.script(
            Step(b"Write the numbers from 1 to 300, one per line, nothing else.\r", when=terminal.idle,
                 capture="idle", label="idle"),
            # The replay holds this turn open until it sees session.interrupt.
            Step(b"second prompt\rthird prompt\r", when=running, label="running"),
            Step(b"\x1b[A", when=terminal.shows("⧗ third prompt"), capture="queued", label="queued"),
            Step(b"\x03", when=terminal.shows("› second prompt"), capture="recalled", label="recalled"),
            Step(b"\x03", when=lambda: not terminal.shows("third prompt")(), label="cleared"),
            Step(b"\x04", when=lambda: "Interrupted." in terminal.history() and terminal.idle(), label="end"),
        )
        self.run_main(run)
        self.assert_clean_run(run)  # a prompt.submit for either queued prompt would be a replay mismatch
        self.assertEqual(len(run.calls("prompt.submit")), 1)
        idle, queued, recalled = (terminal.captures[name] for name in ("idle", "queued", "recalled"))
        top = composer_index(queued)
        self.assertEqual(top, composer_index(idle))
        self.assertEqual(queued[top - 3].rstrip(), "⧗ second prompt")
        self.assertTrue(queued[top - 2].startswith("⧗ third prompt"))
        self.assertTrue(queued[top - 2].rstrip().endswith("↑ edit"))
        self.assertIn("queued 2", status_row_of(queued))
        self.assertNotIn("⧗", "\n".join(recalled))
        self.assertEqual(recalled[composer_index(recalled) + 1].strip(), "third prompt")
        self.assertNotIn("queued", status_row_of(recalled))

    def test_ctrl_t_shows_the_effort_hermes_confirmed_on_the_mode_row(self) -> None:
        answers = (*startup_answers(), ("config.set", {"result": effort_set("low")}))
        run = self.prepare(Scenario("session", spans("session", "startup", "create"), answers=answers))
        terminal = run.terminal
        terminal.script(
            Step(b"\x14", when=terminal.idle, capture="before", label="idle"),
            Step(b"\x04", when=terminal.shows("effort low"), label="the effort"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        (call,) = run.calls("config.set")
        self.assertEqual(call["params"], {"key": "reasoning", "session_id": LIVE_ID, "value": "low"})
        # "ctrl+t effort" is the hint; the level itself is absent until Hermes names one.
        self.assertNotIn("effort low", "\n".join(terminal.captures["before"]))

    def test_shift_tab_walks_the_modes_and_the_frame_shows_each(self) -> None:
        answers = (*startup_answers(), ("config.set", {"result": YOLO_IS_ON}), ("config.set", {"result": YOLO_IS_OFF}))
        run = self.prepare(Scenario("session", spans("session", "startup", "create"), answers=answers))
        terminal = run.terminal
        terminal.script(
            Step(SHIFT_TAB_BYTES, when=terminal.idle, capture="normal", label="idle"),
            Step(SHIFT_TAB_BYTES, when=terminal.shows("⏵⏵ accept all on"), capture="accept", label="accept"),
            Step(SHIFT_TAB_BYTES, when=terminal.shows("⏸ plan mode on"), capture="plan", label="plan"),
            Step(b"\x04", when=terminal.shows("? shortcuts"), label="normal again"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assertEqual(
            [frame["params"] for frame in run.calls("config.set")],
            [
                {"key": "yolo", "session_id": LIVE_ID, "value": "on"},
                {"key": "yolo", "session_id": LIVE_ID, "value": "off"},
            ],
        )
        for name, glyph in (("normal", "─"), ("accept", "━"), ("plan", "┄")):
            rules = [line for line in terminal.captures[name] if line and set(line) == {glyph}]
            self.assertEqual(len(rules), 2, terminal.captures[name])

    def test_a_plan_mode_prompt_reaches_hermes_as_the_prompt_slash_plan_builds(self) -> None:
        answers = (
            *startup_answers(),
            ("config.set", {"result": YOLO_IS_ON}),
            ("config.set", {"result": YOLO_IS_OFF}),
            ("slash.exec", {"error": {"code": 4018, "message": "pending-input command"}}),
            ("command.dispatch", {"result": {"type": "send", "message": PLAN_PROMPT}}),
        )
        run = self.prepare(Scenario("session", spans("session", "startup", "create", "plain_turn"), answers=answers))
        terminal = run.terminal
        terminal.script(
            Step(SHIFT_TAB_BYTES, when=terminal.idle, label="idle"),
            Step(SHIFT_TAB_BYTES, when=terminal.shows("⏵⏵ accept all on"), label="accept"),
            Step(b"fix the flaky test\r", when=terminal.shows("⏸ plan mode on"), label="plan"),
            Step(b"\x04", when=lambda: "later." in terminal.history() and terminal.idle(), label="the reply"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        (dispatch,) = run.calls("command.dispatch")
        self.assertEqual(dispatch["params"], {"name": "plan", "arg": "fix the flaky test", "session_id": LIVE_ID})
        (submitted,) = run.calls("prompt.submit")
        self.assertEqual(submitted["params"], {"session_id": LIVE_ID, "text": PLAN_PROMPT})
        self.assertIn("› /plan fix the flaky test", terminal.history())
        self.assertNotIn("PLAN MODE", terminal.history())


if __name__ == "__main__":
    unittest.main()
