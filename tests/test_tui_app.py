"""The OMH TUI's loop and entry point, driven end to end.

Each test runs `omh.tui.app.main` (or the `App` it builds) with a scripted
terminal whose output lands in the VT screen model, a fake HUD poller, and the
real `GatewayClient` talking to the replay gateway, which plays recorded Hermes
6c61c3e27d frames and checks every frame the TUI sends against the recording.
The TUI's startup differs from the recording client's (it asks `config.get`
and `commands.catalog` before `session.create`), so those two are answered
from the scenario's answer table: the catalog with its recorded answer, the
config with an empty one.

A script step waits for a condition on what the screen shows, then delivers
its bytes (or a resize); one silent read follows every step, so a lone Esc
settles as the Escape key. A step that never becomes ready ends the run with
EOF after a deadline and is reported by its label.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import io
import json
import os
from pathlib import Path
import stat
import time
import unittest

from _test_temp_root import make_test_tempdir
from _tui_gateway_replay import (
    EXIT,
    Scenario,
    client_frames,
    load_fixture,
    materialize,
    recorded_answer,
    replay_env,
    spans,
)
from _vt_screen import VTScreen
from omh.tui import terminal as terminal_module
from omh.tui.app import EXIT_HINT, App, main
from omh.tui.gateway import GatewayClient, gateway_spawn
from omh.tui.launch import FALLBACK_EXIT

FAST_GRACE = 0.3
STORED_ID = "20261013_115656_e709ea"
LIVE_ID = "b6af062a"
APPROVAL_STORED_ID = "20261013_115811_9850e7"
ROTATED_ID = "20261013_130000_rotated"
STATUS_MARK = "⚚ OMH"
RULE_MARK = "────"
STARTUP_METHODS = ["client.capabilities", "config.get", "commands.catalog", "session.create"]
NO_TERMIOS = terminal_module.termios is None


def startup_answers() -> tuple[tuple[str, dict], ...]:
    return (
        ("config.get", {"result": {"config": {}}}),
        ("commands.catalog", recorded_answer("session", "commands_catalog")),
    )


class TtyStream(io.StringIO):
    def __init__(self, tty: bool = True) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@dataclass
class Step:
    data: bytes = b""
    when: Callable[[], bool] | None = None
    resize: tuple[int, int] | None = None  # (rows, cols)
    capture: str = ""  # save the screen under this name when the step fires
    label: str = ""


class ScriptedTerminal:
    """What `App` needs from a terminal: scripted input, output fed to a VT screen, a scriptable size."""

    def __init__(self, rows: int = 24, cols: int = 80, timeout: float = 20.0) -> None:
        self.screen = VTScreen(rows, cols)
        self.steps: deque[Step] = deque()
        self.timeout = timeout
        self.deadline = 0.0
        self.stalled = ""
        self.captures: dict[str, list[str]] = {}
        self.writes: list[str] = []
        self.entered = 0
        self.exited = 0
        self._resized = False
        self._quiet = False

    def script(self, *steps: Step) -> None:
        self.steps.extend(steps)

    def __enter__(self) -> ScriptedTerminal:
        self.entered += 1
        if not self.deadline:
            self.deadline = time.monotonic() + self.timeout
        return self

    def __exit__(self, *exc: object) -> None:
        self.exited += 1

    def size(self) -> tuple[int, int]:
        return (self.screen.cols, self.screen.rows)

    def resized(self) -> bool:
        resized, self._resized = self._resized, False
        return resized

    def write(self, text: str) -> None:
        self.writes.append(text)
        self.screen.feed(text)

    def _wait(self, what: str) -> bytes:
        if time.monotonic() > self.deadline:
            self.stalled = what
            raise EOFError(what)
        time.sleep(0.005)
        return b""

    def read(self, timeout: float) -> bytes:
        if self._quiet:
            self._quiet = False
            return self._wait("the silence after a step")
        if not self.steps:
            return self._wait("the script ran out before the TUI exited")
        step = self.steps[0]
        if step.when is not None and not step.when():
            return self._wait(step.label or f"step {step!r}")
        self.steps.popleft()
        self._quiet = True
        if step.capture:
            self.captures[step.capture] = self.screen.lines()
        if step.resize is not None:
            self.screen.resize(*step.resize)
            self._resized = True
        return step.data

    # -- reading the screen --

    def status_row(self) -> str:
        return next((line for line in self.screen.lines() if STATUS_MARK in line), "")

    def history(self) -> str:
        return "\n".join(self.screen.history())

    def idle(self) -> bool:
        return self.status_row().rstrip().endswith("idle")

    def shows(self, needle: str) -> Callable[[], bool]:
        return lambda: needle in "\n".join(self.screen.lines())


class FakePoller:
    def __init__(self) -> None:
        self.sessions: list[str] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self, timeout: float = 1.0) -> None:
        self.stopped = True

    def set_session(self, stored_id: str) -> None:
        self.sessions.append(stored_id)

    def take(self) -> None:
        return None


@dataclass
class Run:
    root: Path
    environ: dict[str, str]
    terminal: ScriptedTerminal
    poller: FakePoller = field(default_factory=FakePoller)
    stderr: io.StringIO = field(default_factory=io.StringIO)
    clients: list[GatewayClient] = field(default_factory=list)
    code: int | None = None

    @property
    def frames(self) -> list[dict]:
        return client_frames(self.root)

    def calls(self, method: str) -> list[dict]:
        return [frame for frame in self.frames if frame.get("method") == method]

    @property
    def active_session_file(self) -> Path:
        return Path(self.environ["HERMES_TUI_ACTIVE_SESSION_FILE"])


class AppTestCase(unittest.TestCase):
    def prepare(self, scenario: Scenario, *, rows: int = 24, cols: int = 80, env: dict | None = None) -> Run:
        root = Path(self.enterContext(make_test_tempdir("omh-tui-app-")))
        materialize(root, scenario)
        environ = {
            **replay_env(root),
            "HERMES_TUI_ACTIVE_SESSION_FILE": str(root / "active-session.json"),
            "HERMES_TUI_CWD": "/fixture/project",
            "TERM": "xterm-256color",
            **(env or {}),
        }
        environ.pop("OMH_NO_TUI", None)
        return Run(root, environ, ScriptedTerminal(rows, cols))

    def make_client(self, run: Run) -> Callable[..., GatewayClient]:
        def build(spawn: object) -> GatewayClient:
            client = GatewayClient(spawn)  # type: ignore[arg-type]
            run.clients.append(client)
            self.addCleanup(client.close, FAST_GRACE)
            return client

        return build

    def run_main(self, run: Run) -> Run:
        run.code = main(
            run.environ,
            stdin=TtyStream(),
            stdout=TtyStream(),
            stderr=run.stderr,
            open_terminal=lambda: run.terminal,
            make_client=self.make_client(run),
            make_poller=lambda omh_home, hermes_home: run.poller,
        )
        return run

    def assert_clean_run(self, run: Run, code: int = 0) -> None:
        self.assertEqual(run.terminal.stalled, "", f"the script stalled; screen:\n{run.terminal.history()}")
        self.assertEqual(run.code, code, run.stderr.getvalue())
        tails = [line for client in run.clients for line in client.stderr_tail()]
        self.assertFalse([line for line in tails if line.startswith("replay:")], tails)

    def submit(self, run: Run, text: str) -> Step:
        return Step(text.encode("utf-8") + b"\r", when=run.terminal.idle, label="the idle status line")


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class StartupRefusalTests(AppTestCase):
    def test_an_unsupported_terminal_falls_back_before_anything_starts(self) -> None:
        run = self.prepare(Scenario("session", spans("session", "startup")))
        run.code = main(
            run.environ,
            stdin=TtyStream(tty=False),
            stdout=TtyStream(),
            stderr=run.stderr,
            open_terminal=lambda: run.terminal,
            make_client=self.make_client(run),
            make_poller=lambda omh_home, hermes_home: run.poller,
        )
        self.assertEqual(run.code, FALLBACK_EXIT)
        self.assertIn("cannot run the OMH TUI", run.stderr.getvalue())
        self.assertEqual(run.clients, [])
        self.assertEqual(run.terminal.writes, [])

    def test_a_missing_interpreter_falls_back_naming_it(self) -> None:
        run = self.prepare(Scenario("session", spans("session", "startup")))
        del run.environ["HERMES_PYTHON"]
        self.run_main(run)
        self.assertEqual(run.code, FALLBACK_EXIT)
        self.assertIn("HERMES_PYTHON", run.stderr.getvalue())
        self.assertEqual(run.terminal.entered, 0)

    def test_an_attach_hand_off_exits_1_naming_hermes_resume(self) -> None:
        run = self.prepare(
            Scenario("session", spans("session", "startup")),
            env={"HERMES_TUI_GATEWAY_URL": "ws://127.0.0.1:9/attach", "HERMES_TUI_RESUME": "20261013_000000_abc"},
        )
        self.run_main(run)
        self.assertEqual(run.code, 1)
        self.assertIn("hermes --tui --resume 20261013_000000_abc", run.stderr.getvalue())
        self.assertEqual(run.clients, [])
        self.assertEqual(run.terminal.entered, 0)

    def test_drift_on_a_startup_call_falls_back_and_reaps_the_gateway(self) -> None:
        # Hermes 6c61c3e27d's real -32601 answer, served for config.get.
        drift = recorded_answer("session", "drift_reply")
        run = self.prepare(Scenario("session", spans("session", "startup"), answers=(("config.get", drift),)))
        self.run_main(run)
        self.assertEqual(run.code, FALLBACK_EXIT)
        self.assertIn("-32601", run.stderr.getvalue())
        self.assertEqual(run.terminal.entered, 0)
        self.assertEqual(run.clients[0].close(grace=FAST_GRACE), 0)


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class SessionTests(AppTestCase):
    def test_startup_draws_status_and_composer_and_writes_the_active_session_file(self) -> None:
        run = self.prepare(Scenario("session", spans("session", "startup", "create"), answers=startup_answers()))
        run.terminal.script(Step(b"\x04", when=run.terminal.idle, capture="ready", label="the first frame"))
        self.run_main(run)
        self.assert_clean_run(run)
        screen = run.terminal.captures["ready"]
        status = next(index for index, line in enumerate(screen) if STATUS_MARK in line)
        self.assertIn("deepseek/deepseek-v4.1-flash-ultrafast", screen[status])
        self.assertTrue(screen[status - 1].startswith("›"), screen)
        self.assertIn(RULE_MARK, screen[status - 2])
        self.assertEqual([frame["method"] for frame in run.frames], STARTUP_METHODS)
        self.assertEqual(run.calls("config.get")[0]["params"], {"key": "full"})
        self.assertEqual(json.loads(run.active_session_file.read_text(encoding="utf-8")), {"session_id": STORED_ID})
        self.assertEqual(stat.S_IMODE(os.stat(run.active_session_file).st_mode), 0o600)
        self.assertEqual(run.poller.sessions, [STORED_ID])
        self.assertTrue(run.poller.started and run.poller.stopped)
        self.assertEqual((run.terminal.entered, run.terminal.exited), (1, 1))
        # The region is gone after exit; nothing of it is left on the screen.
        self.assertNotIn(STATUS_MARK, run.terminal.history())

    def test_a_typed_prompt_is_submitted_and_the_reply_lands_in_scrollback(self) -> None:
        prompt = "Reply with exactly the word: ok"
        run = self.prepare(
            Scenario("session", spans("session", "startup", "create", "plain_turn"), answers=startup_answers())
        )
        run.terminal.script(
            self.submit(run, prompt),
            Step(b"\x04", when=lambda: "later." in run.terminal.history() and run.terminal.idle(), label="reply"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        (submitted,) = run.calls("prompt.submit")
        self.assertEqual(submitted["params"], {"session_id": LIVE_ID, "text": prompt})
        history = run.terminal.history()
        self.assertIn(f"› {prompt}", history)
        self.assertIn("I'm Hermes, your agent", history)
        self.assertLess(history.index(f"› {prompt}"), history.index("I'm Hermes"))

    def test_input_during_a_turn_is_queued_and_sent_when_the_turn_ends(self) -> None:
        run = self.prepare(
            Scenario(
                "session",
                spans("session", "startup", "create", "plain_turn", "smart_approved_turn"),
                answers=startup_answers(),
            )
        )
        run.terminal.script(
            # One read: the second Enter lands while the first prompt's turn is pending.
            Step(b"first prompt\rsecond prompt\r", when=run.terminal.idle, label="idle"),
            Step(b"\x04", when=lambda: "cleanly)." in run.terminal.history() and run.terminal.idle(), label="turn 2"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        first, second = run.calls("prompt.submit")
        self.assertEqual(first["params"], {"session_id": LIVE_ID, "text": "first prompt"})
        self.assertEqual(second["params"], {"session_id": LIVE_ID, "text": "second prompt", "queued": True})
        history = run.terminal.history()
        self.assertLess(history.index("› first prompt"), history.index("later."))
        self.assertLess(history.index("later."), history.index("› second prompt"))

    def test_ctrl_c_during_a_turn_sends_session_interrupt(self) -> None:
        run = self.prepare(
            Scenario("session", spans("session", "startup", "create", "interrupted_turn"), answers=startup_answers())
        )
        run.terminal.script(
            self.submit(run, "Write the numbers from 1 to 300, one per line, nothing else."),
            Step(b"\x03", when=lambda: run.terminal.status_row().rstrip().endswith("running"), label="running"),
            Step(b"\x04", when=lambda: "Interrupted." in run.terminal.history() and run.terminal.idle(), label="end"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        (interrupt,) = run.calls("session.interrupt")
        self.assertEqual(interrupt["params"], {"session_id": LIVE_ID})

    def test_ctrl_c_twice_while_idle_exits_0(self) -> None:
        run = self.prepare(Scenario("session", spans("session", "startup", "create"), answers=startup_answers()))
        run.terminal.script(
            Step(b"\x03", when=run.terminal.idle, label="idle"),
            Step(b"\x03", when=run.terminal.shows(EXIT_HINT), label="the exit hint"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assertEqual([frame["method"] for frame in run.frames], STARTUP_METHODS)

    def test_a_new_stored_id_rewrites_the_session_file_and_repoints_the_poller(self) -> None:
        rotated = {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "session.info",
                "session_id": LIVE_ID,
                "payload": {"model": "deepseek/deepseek-v4.1-flash-ultrafast", "stored_session_id": ROTATED_ID},
            },
        }
        foreign = {**rotated, "params": {**rotated["params"], "session_id": "someone-else"}}
        foreign["params"]["payload"] = {"stored_session_id": "20261013_999999_foreign"}
        played = spans("session", "startup", "create")
        position = sum(stop - start for start, stop in played)
        run = self.prepare(
            Scenario(
                "session",
                played,
                inject=((position, json.dumps(foreign)), (position, json.dumps(rotated))),
                answers=startup_answers(),
            )
        )
        run.terminal.script(Step(b"\x04", when=lambda: run.poller.sessions[-1:] == [ROTATED_ID], label="rotation"))
        self.run_main(run)
        self.assert_clean_run(run)
        # The event for another session id was dropped; only the live session's rotation counted.
        self.assertEqual(run.poller.sessions, [STORED_ID, ROTATED_ID])
        self.assertEqual(json.loads(run.active_session_file.read_text(encoding="utf-8")), {"session_id": ROTATED_ID})
        self.assertEqual(stat.S_IMODE(os.stat(run.active_session_file).st_mode), 0o600)

    def test_a_gateway_exit_mid_session_returns_1_with_the_resume_line(self) -> None:
        run = self.prepare(
            Scenario("session", spans("session", "startup", "create"), answers=startup_answers(), after=EXIT)
        )
        self.run_main(run)
        self.assertEqual(run.terminal.stalled, "")
        self.assertEqual(run.code, 1)
        self.assertIn(f"hermes --tui --resume {STORED_ID}", run.stderr.getvalue())
        self.assertIn("exited", run.terminal.history())
        self.assertEqual((run.terminal.entered, run.terminal.exited), (1, 1))

    def test_a_resize_repaints_without_ghost_rows(self) -> None:
        run = self.prepare(Scenario("session", spans("session", "startup", "create"), answers=startup_answers()))

        def settled(rows: int, cols: int) -> Callable[[], bool]:
            def ready() -> bool:
                screen = run.terminal.screen
                return (screen.rows, screen.cols) == (rows, cols) and screen.lines()[rows - 2].startswith(STATUS_MARK)

            return ready

        run.terminal.script(
            Step(resize=(16, 60), when=run.terminal.idle, label="idle"),
            Step(resize=(30, 100), when=settled(16, 60), capture="narrow", label="the 16x60 frame"),
            Step(b"\x04", when=settled(30, 100), capture="wide", label="the 30x100 frame"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        for name, rows, cols in (("narrow", 16, 60), ("wide", 30, 100)):
            with self.subTest(size=name):
                screen = run.terminal.captures[name]
                self.assertEqual(len(screen), rows)
                drawn = [line for line in screen if line]
                self.assertEqual(sum(STATUS_MARK in line for line in screen), 1, screen)
                self.assertEqual(sum(RULE_MARK in line for line in screen), 1, screen)
                # Only the region is on screen: rule, composer prompt, status, nothing else.
                self.assertEqual(len(drawn), 3, screen)
                self.assertTrue(all(len(line) <= cols for line in screen))


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class PromptSlotTests(AppTestCase):
    def approval_run(self) -> Run:
        return self.prepare(
            Scenario("approval", spans("approval", "startup", "create", "approval_turn"), answers=startup_answers())
        )

    def assert_denied(self, run: Run) -> None:
        self.assert_clean_run(run)
        answers = [frame for frame in run.frames if frame.get("id") == "srq-52e9720342b6"]
        self.assertEqual(answers, [{"jsonrpc": "2.0", "id": "srq-52e9720342b6", "result": {"choice": "deny"}}])
        self.assertIn("It was blocked", run.terminal.history())

    def test_an_approval_opens_a_slot_and_d_sends_the_explicit_deny(self) -> None:
        run = self.approval_run()
        run.terminal.script(
            self.submit(run, "Run rm -rf ./omh-m0-probe-dir"),
            Step(b"d", when=run.terminal.shows("Hermes asks to run"), capture="slot", label="the approval slot"),
            Step(b"\x04", when=lambda: "proceed." in run.terminal.history() and run.terminal.idle(), label="end"),
        )
        self.run_main(run)
        self.assert_denied(run)
        slot = "\n".join(run.terminal.captures["slot"])
        self.assertIn("rm -rf ./omh-m0-probe-dir", slot)
        self.assertIn("waiting on you", slot)
        self.assertEqual(json.loads(run.active_session_file.read_text(encoding="utf-8")), {"session_id": APPROVAL_STORED_ID})

    def test_esc_on_an_approval_sends_the_explicit_deny(self) -> None:
        run = self.approval_run()
        run.terminal.script(
            self.submit(run, "Run rm -rf ./omh-m0-probe-dir"),
            Step(b"\x1b", when=run.terminal.shows("Hermes asks to run"), label="the approval slot"),
            Step(b"\x04", when=lambda: "proceed." in run.terminal.history() and run.terminal.idle(), label="end"),
        )
        self.run_main(run)
        self.assert_denied(run)

    def test_a_resumed_sessions_open_request_is_redelivered_as_a_slot(self) -> None:
        # The recorded approval request, handed back by session.resume as an open request; the
        # replay then expects the recorded deny for it and plays the rest of the recorded turn.
        request = load_fixture("approval")[1][20]["frame"]
        resumed = {
            "result": {
                "session_id": "f05e2691",
                "stored_session_id": APPROVAL_STORED_ID,
                "message_count": 1,
                "messages": [],
                "info": {"model": "deepseek/deepseek-v4.1-flash-ultrafast"},
                "running": True,
                "open_requests": [{"id": request["id"], "method": request["method"], "params": request["params"]}],
            }
        }
        startup = spans("approval", "startup")
        run = self.prepare(
            Scenario("approval", (*startup, (21, 47)), answers=(*startup_answers(), ("session.resume", resumed))),
            env={"HERMES_TUI_RESUME": APPROVAL_STORED_ID},
        )
        run.terminal.script(
            Step(b"d", when=run.terminal.shows("Hermes asks to run"), label="the re-delivered approval"),
            Step(b"\x04", when=lambda: "proceed." in run.terminal.history() and run.terminal.idle(), label="end"),
        )
        self.run_main(run)
        self.assert_denied(run)
        (resume,) = run.calls("session.resume")
        self.assertEqual(resume["params"], {"session_id": APPROVAL_STORED_ID, "cols": 80})
        self.assertEqual(run.calls("session.create"), [])
        self.assertIn(f"Resumed session {APPROVAL_STORED_ID}", run.terminal.history())
        self.assertEqual(json.loads(run.active_session_file.read_text(encoding="utf-8")), {"session_id": APPROVAL_STORED_ID})

    def assert_withdrawal_closes_the_slot(self, kind: str, payload: dict) -> None:
        played = (*spans("approval", "startup", "create"), (5, 21))  # the turn up to the approval request
        withdrawal = {"jsonrpc": "2.0", "method": "event", "params": {"type": kind, "session_id": "f05e2691", "payload": payload}}
        position = sum(stop - start for start, stop in played)
        run = self.prepare(
            Scenario("approval", played, inject=((position, json.dumps(withdrawal)),), answers=startup_answers())
        )
        run.terminal.script(
            self.submit(run, "Run rm -rf ./omh-m0-probe-dir"),
            Step(b"\x04", when=run.terminal.shows("withdrew its approval request"), label="the withdrawal notice"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assertEqual([frame for frame in run.frames if frame.get("id") == "srq-52e9720342b6"], [])
        self.assertNotIn("Hermes asks to run", "\n".join(run.terminal.screen.lines()))

    def test_request_cancel_closes_the_slot_without_answering(self) -> None:
        self.assert_withdrawal_closes_the_slot(
            "request.cancel", {"id": "srq-52e9720342b6", "method": "approval", "reason": "timeout"}
        )

    def test_approval_cancelled_closes_the_slot_by_its_approval_id(self) -> None:
        self.assert_withdrawal_closes_the_slot(
            "approval.cancelled",
            {
                "session_id": "f05e2691",
                "stored_session_id": APPROVAL_STORED_ID,
                "reason": "interrupt",
                "cancelled_count": 1,
                "request_ids": ["421ace443ef640baa09f80731226646e"],
            },
        )

    def test_a_clarify_choice_is_picked_by_its_number(self) -> None:
        run = self.prepare(
            Scenario("session", spans("session", "startup", "create", "clarify_turn"), answers=startup_answers())
        )
        run.terminal.script(
            self.submit(run, "Ask me red or blue."),
            Step(b"2", when=run.terminal.shows("Which color do you prefer?"), label="the clarify slot"),
            Step(b"\x04", when=run.terminal.idle, label="the end of the turn"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        answers = [frame for frame in run.frames if frame.get("id") == "srq-c7e7e10e23ab"]
        self.assertEqual(answers, [{"jsonrpc": "2.0", "id": "srq-c7e7e10e23ab", "result": {"answers": {"q0": "blue"}}}])


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class SlashCommandTests(AppTestCase):
    def test_a_hermes_only_command_shows_its_door_and_sends_nothing(self) -> None:
        run = self.prepare(Scenario("session", spans("session", "startup", "create"), answers=startup_answers()))
        run.terminal.script(
            self.submit(run, "/voice"),
            Step(b"\x04", when=run.terminal.shows("hermes --tui"), label="the hermes notice"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assertIn("/voice runs in Hermes' own terminal", run.terminal.history())
        self.assertEqual([frame["method"] for frame in run.frames], STARTUP_METHODS)

    def test_an_unknown_command_goes_to_slash_exec_then_command_dispatch_on_4018(self) -> None:
        answers = (
            *startup_answers(),
            ("slash.exec", {"error": {"code": 4018, "message": "not a slash.exec command"}}),
            ("command.dispatch", {"result": {"type": "exec", "output": "xyz ran through command.dispatch"}}),
        )
        run = self.prepare(Scenario("session", spans("session", "startup", "create"), answers=answers))
        run.terminal.script(
            self.submit(run, "/xyz"),
            Step(b"\x04", when=run.terminal.shows("xyz ran through"), label="the dispatch output"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        (slash,) = run.calls("slash.exec")
        self.assertEqual(slash["params"], {"command": "xyz", "session_id": LIVE_ID})
        (dispatch,) = run.calls("command.dispatch")
        self.assertEqual(dispatch["params"], {"name": "xyz", "arg": "", "session_id": LIVE_ID})

    def test_tab_completes_a_local_command_and_history_pages_on_the_alternate_screen(self) -> None:
        run = self.prepare(
            Scenario("session", spans("session", "startup", "create", "plain_turn"), answers=startup_answers())
        )
        terminal = run.terminal
        run.terminal.script(
            self.submit(run, "Reply with exactly the word: ok"),
            Step(b"/hist", when=lambda: "later." in terminal.history() and terminal.idle(), label="reply"),
            Step(b"\t", when=terminal.shows("/history"), capture="menu", label="the completion menu"),
            Step(b"\r", when=terminal.shows("› /history"), label="the completed command"),
            Step(b"q", when=lambda: terminal.screen.alternate, capture="pager", label="the pager"),
            Step(b"\x04", when=lambda: not terminal.screen.alternate and terminal.idle(), capture="back", label="back"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assertTrue(any("Page through this session's transcript" in line for line in terminal.captures["menu"]))
        pager = "\n".join(terminal.captures["pager"])
        self.assertIn("I'm Hermes, your agent", pager)
        self.assertIn("history · ", pager)
        back = "\n".join(terminal.captures["back"])
        self.assertIn(STATUS_MARK, back)
        self.assertNotIn("history · ", back)
        self.assertEqual(run.calls("slash.exec"), [])

    def test_omh_model_suspends_the_terminal_around_the_picker(self) -> None:
        run = self.prepare(Scenario("session", spans("session", "startup", "create"), answers=startup_answers()))
        terminal = run.terminal
        seen: list[tuple[int, int]] = []

        def picker(environ: object, theme: object) -> str:
            seen.append((terminal.entered, terminal.exited))
            return "No model-chain changes."

        client = self.make_client(run)(gateway_spawn(run.environ))
        client.start(ready_timeout=10.0)
        app = App(terminal, client, run.environ, poller=run.poller, pick_model_chains=picker)
        app.startup()
        terminal.script(
            self.submit(run, "/omh-model"),
            Step(b"\x04", when=terminal.shows("No model-chain changes."), label="the picker result"),
        )
        with terminal:
            run.code = app.run()
        self.assert_clean_run(run)
        # Inside the picker the TUI had left the terminal once and not yet re-entered it.
        self.assertEqual(seen, [(1, 1)])
        self.assertEqual((terminal.entered, terminal.exited), (2, 2))


if __name__ == "__main__":
    unittest.main()
