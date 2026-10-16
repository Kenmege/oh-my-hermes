"""Gateway crash recovery (approved boundary change, owner decision 10): respawn, resume, at most 3 in 60 s.

When the `tui_gateway` child exits on its own with a failure, the TUI spawns
it again from the environment the launcher handed it and resumes the session
that was open, as Hermes' own TUI does (ui-tui `app/gatewayRecovery.ts`). The
question the old gateway asked closes unanswered, a running turn is reported
lost and never sent again, and the queue is held as after an interrupt. A
normal exit, the person's quit, or a gateway another runtime owns is never
respawned; past the cap the TUI leaves as it did before, naming
`hermes --tui`. Every case drives an `App` over recording clients; the
clock is the test's.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from test_tui_app import FakePoller, ScriptedTerminal, Step
from test_tui_modes import SID, RecordingClient, make_app, notices, type_text
from omh.tui import gateway_contract as contract
from omh.tui.app import (
    RESPAWN_LIMIT,
    RESPAWN_NOTICE,
    RESPAWN_WINDOW_SECONDS,
    RESPAWNED_NOTICE,
    RESPAWNED_WHY,
    TURN_LOST_NOTICE,
    App,
    QueuedLine,
    _run,
)
from omh.tui.gateway import (
    Event,
    GatewayCallError,
    GatewayClient,
    GatewayExited,
    GatewayUnavailable,
    ServerRequest,
    gateway_spawn,
)
from omh.tui.keys import ENTER, Key

STORED = "20261016_101010_abcdef"
ENVIRON = {
    "HERMES_PYTHON": "/opt/hermes/venv/bin/python",
    "HERMES_PYTHON_SRC_ROOT": "/opt/hermes",
    "OMH_TUI_GATEWAY_PYTHONPATH": "/opt/hermes/site",
    "HERMES_CWD": "/work/project",
}
RESUMED = {"session_id": "live-2", "stored_session_id": STORED, "info": {"model": "m"}, "messages": []}


class RespawnClient(RecordingClient):
    """A recording client that also starts, answers the blocking `session.resume`, and is closed."""

    def __init__(self, spawn: object = None, first_id: int = 1, *, start_error: Exception | None = None,
                 resume: dict | Exception = RESUMED) -> None:
        super().__init__()
        self.spawn, self.first_id = spawn, first_id
        self.start_error, self.resume = start_error, resume
        self.blocking: list[tuple[str, dict]] = []
        self.closed = 0

    def start(self, ready_timeout: float = 60.0) -> Event:
        if self.start_error is not None:
            raise self.start_error
        return Event("gateway.ready", None, {})

    def call(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        self.blocking.append((method, params))
        if isinstance(self.resume, Exception):
            raise self.resume
        return self.resume

    def request(self, method: str, params: dict) -> int:
        problem = contract.call_problem(method, params)
        if problem:
            raise ValueError(problem)
        self.calls.append((method, params))
        return self.first_id + len(self.calls) - 1

    def close(self, grace: float = 3.0, terminate_grace: float | None = None) -> int | None:
        self.closed += 1
        return 1

    def stderr_tail(self) -> list[str]:
        return []


class Spawner:
    """`make_client` for the app: hands out the next scripted client and keeps every one it made."""

    def __init__(self, *clients: RespawnClient) -> None:
        self.queued = list(clients)
        self.made: list[RespawnClient] = []

    def __call__(self, spawn: object, first_id: int = 1) -> RespawnClient:
        client = self.queued.pop(0) if self.queued else RespawnClient()
        client.spawn, client.first_id = spawn, first_id
        self.made.append(client)
        return client


def crashing(**options: object) -> tuple[App, RespawnClient, Spawner, list[float]]:
    now = [100.0]
    spawner = options.pop("spawner", None) or Spawner()
    app, _recording = make_app(environ=dict(options.pop("environ", ENVIRON)), clock=lambda: now[0], make_client=spawner,
                               **options)
    old = RespawnClient()
    app.client = old  # type: ignore[assignment]
    app.stored_id = STORED
    return app, old, spawner, now


def crash(app: App, client: RespawnClient, code: int | None = 1) -> None:
    client.inbox.append(GatewayExited(code))
    app._drain_gateway()
    if app.respawn_reason:
        app._respawn_gateway()


class RespawnTests(unittest.TestCase):
    def test_a_crash_spawns_the_gateway_again_as_the_launcher_said_and_resumes_the_session(self) -> None:
        app, old, spawner, _now = crashing()
        crash(app, old)
        self.assertIsNone(app.exit_code)
        (new,) = spawner.made
        self.assertIs(app.client, new)
        self.assertEqual(new.spawn, gateway_spawn(ENVIRON))  # same interpreter, import path and environment
        self.assertEqual(new.blocking, [("session.resume", {"session_id": STORED, "cols": 80})])
        self.assertEqual((app.sid, app.stored_id), ("live-2", STORED))
        self.assertEqual(old.closed, 1)
        said = notices(app)
        self.assertIn(RESPAWN_NOTICE.format(reason="The Hermes gateway exited (code 1)", session=STORED, attempt=1,
                                            limit=RESPAWN_LIMIT, window=RESPAWN_WINDOW_SECONDS), said)
        self.assertIn(f"The Hermes gateway exited (code 1): starting it again and resuming session {STORED} (1 of 3 in 60 s).",
                      said)
        self.assertEqual(said[-1], RESPAWNED_NOTICE.format(session=STORED))
        # It reads the session's settings and command list again, as a session that opens does.
        self.assertEqual([method for method, _params in new.calls], ["config.get", "commands.catalog"])

    def test_the_open_question_and_the_waiting_ones_close_unanswered(self) -> None:
        app, old, _spawner, now = crashing()
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        app._on_server_request(ServerRequest("srq-2", "clarify", {"questions": [{"qid": "q", "question": "?"}]}))
        crash(app, old)
        self.assertIsNone(app.slot)
        self.assertEqual(list(app.waiting_requests), [])
        self.assertEqual(old.answers, [])
        self.assertEqual(app.client.answers, [])  # type: ignore[attr-defined]
        self.assertIn("Hermes' approval request closed unanswered: the gateway that asked has exited.", notices(app))

    def test_a_running_turn_is_reported_lost_and_never_sent_again(self) -> None:
        app, old, spawner, _now = crashing(busy=True)
        crash(app, old)
        self.assertIn(TURN_LOST_NOTICE, notices(app))
        self.assertFalse(app._busy())
        self.assertEqual(spawner.made[0].named("prompt.submit"), [])

    def test_the_queue_is_held_as_after_an_interrupt(self) -> None:
        app, old, spawner, _now = crashing(busy=True)
        app.queued.extend([QueuedLine("next one"), QueuedLine("and this")])
        crash(app, old)
        self.assertEqual(app.composer.text, "next one\nand this")
        self.assertIn(f"{RESPAWNED_WHY}: what you queued is back in the composer, not sent.", notices(app))
        self.assertEqual(spawner.made[0].named("prompt.submit"), [])

    def test_calls_still_waiting_keep_their_ids_and_the_new_client_starts_past_them(self) -> None:
        app, old, spawner, _now = crashing()
        type_text(app, "/usage")
        app._on_input(Key(ENTER))
        waiting = max(app.pending)
        crash(app, old)
        self.assertEqual(spawner.made[0].first_id, waiting + 1)
        self.assertIn(waiting, app.pending)  # it expires as an unanswered call does

    def test_a_write_the_dead_gateway_refuses_respawns_too(self) -> None:
        app, old, spawner, _now = crashing()

        def refuse(method: str, params: dict) -> int:
            raise GatewayUnavailable("the gateway stopped reading its input")

        old.request = refuse  # type: ignore[method-assign]
        type_text(app, "hello")
        app._on_input(Key(ENTER))
        self.assertTrue(app.respawn_reason)
        app._respawn_gateway()
        self.assertIs(app.client, spawner.made[0])
        self.assertIn(TURN_LOST_NOTICE, notices(app))  # the prompt never went out: it is lost, not sent again


class NoRespawnTests(unittest.TestCase):
    def test_a_normal_exit_is_never_respawned(self) -> None:
        app, old, spawner, _now = crashing()
        crash(app, old, code=0)
        self.assertEqual((app.exit_code, spawner.made), (1, []))
        self.assertIn(f"hermes --tui --resume {STORED}", app.farewell)

    def test_the_persons_quit_is_never_respawned(self) -> None:
        app, old, spawner, _now = crashing()
        type_text(app, "/quit")
        app._on_input(Key(ENTER))
        crash(app, old)
        self.assertEqual((app.exit_code, spawner.made), (0, []))

    def test_a_gateway_another_runtime_owns_is_never_respawned(self) -> None:
        app, old, spawner, _now = crashing(environ={**ENVIRON, contract.ATTACH_ENV: "ws://127.0.0.1:9/x"})
        crash(app, old)
        self.assertEqual((app.exit_code, spawner.made), (1, []))

    def test_without_a_way_to_spawn_the_tui_leaves_as_before(self) -> None:
        app, _client = make_app(environ=ENVIRON)
        app.stored_id = STORED  # an app made without make_client
        app.client.inbox.append(GatewayExited(1))  # type: ignore[attr-defined]
        app._drain_gateway()
        self.assertEqual(app.exit_code, 1)


class CapTests(unittest.TestCase):
    def test_three_respawns_in_a_minute_then_the_tui_leaves_naming_hermes_tui(self) -> None:
        app, client, spawner, now = crashing()
        for attempt in range(RESPAWN_LIMIT):
            crash(app, client)
            self.assertIsNone(app.exit_code, attempt)
            client = app.client  # type: ignore[assignment]
            now[0] += 10.0
        crash(app, client)
        self.assertEqual(app.exit_code, 1)
        self.assertEqual(len(spawner.made), RESPAWN_LIMIT)
        self.assertIn(f"hermes --tui --resume {STORED}", app.farewell)

    def test_the_window_slides(self) -> None:
        app, client, spawner, now = crashing()
        for _ in range(RESPAWN_LIMIT):
            crash(app, client)
            client = app.client  # type: ignore[assignment]
        now[0] += RESPAWN_WINDOW_SECONDS + 1
        crash(app, client)
        self.assertIsNone(app.exit_code)
        self.assertEqual(len(spawner.made), RESPAWN_LIMIT + 1)

    def test_a_gateway_that_does_not_start_again_uses_an_attempt_and_the_next_is_tried(self) -> None:
        failing = RespawnClient(start_error=GatewayUnavailable("the gateway exited with code 1 before gateway.ready"))
        spawner = Spawner(failing, RespawnClient())
        app, old, _spawner, _now = crashing(spawner=spawner)
        crash(app, old)
        self.assertIsNone(app.exit_code)
        self.assertEqual(len(spawner.made), 2)
        self.assertIs(app.client, spawner.made[1])
        self.assertEqual(sum("starting it again" in text for text in notices(app)), 2)

    def test_none_starting_ends_it_after_the_cap(self) -> None:
        spawner = Spawner(*[RespawnClient(start_error=GatewayCallError("client.capabilities", 4000, "no")) for _ in range(5)])
        app, old, _spawner, _now = crashing(spawner=spawner)
        crash(app, old)
        self.assertEqual(app.exit_code, 1)
        self.assertEqual(len(spawner.made), RESPAWN_LIMIT)
        self.assertIn("the new Hermes gateway did not start", app.farewell)

    def test_a_resume_the_new_gateway_refuses_ends_it(self) -> None:
        refused = RespawnClient(resume=GatewayCallError("session.resume", 4007, "session is owned by another process"))
        app, old, _spawner, _now = crashing(spawner=Spawner(refused))
        crash(app, old)
        self.assertEqual(app.exit_code, 1)
        self.assertEqual(refused.closed, 1)
        self.assertIn(f"did not resume session {STORED}", app.farewell)

    def test_with_no_stored_session_there_is_nothing_to_resume(self) -> None:
        app, old, spawner, _now = crashing()
        app.stored_id = ""
        crash(app, old)
        self.assertEqual((app.exit_code, spawner.made), (1, []))


class LoopTests(unittest.TestCase):
    def test_the_loop_respawns_and_the_respawned_gateway_is_reaped_on_exit(self) -> None:
        first = RespawnClient()
        first.inbox.append(GatewayExited(1))
        spawner = Spawner()
        terminal = ScriptedTerminal(24, 80)
        terminal.script(Step(b"\x04", when=lambda: bool(spawner.made), label="respawned"))

        def poller(omh_home: object, hermes_home: object) -> FakePoller:
            return FakePoller()

        first.resume = {"session_id": SID, "stored_session_id": STORED, "info": {}}
        environ = {**ENVIRON, "HERMES_TUI_RESUME": STORED}
        code = _run(first, environ, _Sink(), lambda: terminal, poller, spawner)  # type: ignore[arg-type]
        self.assertEqual(code, 0)
        (new,) = spawner.made
        self.assertEqual(new.closed, 1)  # reaped by `_run`; the first one is the caller's to close
        self.assertEqual(new.blocking, [("session.resume", {"session_id": STORED, "cols": 80})])


class FirstIdTests(unittest.TestCase):
    def test_a_client_made_to_replace_another_starts_its_ids_where_it_is_told(self) -> None:
        client = GatewayClient(gateway_spawn(ENVIRON), first_id=7)
        with patch.object(client, "_write"):  # nothing spawned: the request is only recorded as waited for
            self.assertEqual([client.request("session.list", {}), client.request("session.list", {})], [7, 8])
        self.assertEqual(GatewayClient(gateway_spawn(ENVIRON))._next_id, 1)  # a first client starts at 1


class _Sink:
    def write(self, text: str) -> None:
        pass

    def flush(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
