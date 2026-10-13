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
from test_tui_app import LIVE_ID, NO_TERMIOS, AppTestCase, FakePoller, ScriptedTerminal, Step, startup_answers
from omh.tui import app as app_module
from omh.tui import gateway_contract as contract
from omh.tui.app import SLOT_ARM_SECONDS, App
from omh.tui.frame import MODE_ACCEPT, MODE_NORMAL, MODE_PLAN
from omh.tui.gateway import Event, Response, ServerRequest
from omh.tui.keys import CHAR, ENTER, TAB, Key
from omh.tui.pager import Pager
from omh.tui.session_picker import SessionPicker, SessionRow
from omh.tui.slash import parse_slash, plan_invocation
from omh.tui.transcript import NOTICE_ERROR, NOTICE_WARN, NoticeBlock, UserBlock

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


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class ModeReplayTests(AppTestCase):
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
