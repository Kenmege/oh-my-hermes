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
from omh.tui.gateway import Response, ServerRequest
from omh.tui.keys import CHAR, ENTER, Key
from omh.tui.transcript import NOTICE_WARN, NoticeBlock

SID = "live-1"
CTRL_ENTER = Key(ENTER, ctrl=True)
CTRL_ENTER_BYTES = b"\x1b[13;5u"  # kitty CSI u; modifyOtherKeys sends ESC[27;5;13~


class RecordingClient:
    """What `App` needs from a gateway client, kept in memory; nothing is spawned."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.answers: list[tuple[str, dict]] = []

    def request(self, method: str, params: dict) -> int:
        undeclared = contract.undeclared_params(method, params)
        if undeclared:
            raise ValueError(f"{method} does not declare {undeclared}")
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


if __name__ == "__main__":
    unittest.main()
