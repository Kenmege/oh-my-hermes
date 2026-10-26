"""herdr: inside a herdr pane the OMH TUI tells herdr its state on each change, and lets go of the pane on exit.

herdr lists each pane's agent as idle, working or blocked. The TUI reports
the state its title shows (`waiting on you` is blocked, `running` is working,
`idle` is idle) with `herdr pane report-agent --source omh:tui --agent omh`,
only when it changes, and `herdr pane release-agent` as it exits. Only
`HERDR_ENV=1` with a `HERDR_PANE_ID` turns this on; anywhere else nothing is
spawned. One call runs at a time on a worker thread and only the newest waits
behind it; the release replaces any waiting report and stops the one in
flight. Each `--seq` is above the last, and every failure is dropped.
"""

from __future__ import annotations

import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from test_tui_modes import SID, drawn, make_app
from omh.tui import herdr
from omh.tui.gateway import Event, ServerRequest
from omh.tui.herdr import HerdrReporter, finish, herdr_binary, herdr_pane, spawn_herdr

PANE = "w1:p1"
IN_HERDR = {"HERDR_ENV": "1", "HERDR_PANE_ID": PANE}


class FakeProcess:
    """A herdr CLI call that answers after `delay` seconds, or at once when killed."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.killed = threading.Event()

    def wait(self, timeout: float | None = None) -> int:
        limit = self.delay if timeout is None else min(self.delay, timeout)
        if self.killed.wait(limit):
            return -9
        if timeout is not None and self.delay > timeout:
            raise subprocess.TimeoutExpired("herdr", timeout)
        return 0

    def kill(self) -> None:
        self.killed.set()


class Herdr:
    """The spawn seam: each argv the worker started, with its environment and its process."""

    def __init__(self, delay: float = 0.0, first_delay: float | None = None) -> None:
        self.delay = delay
        self.first_delay = first_delay
        self.argv: list[list[str]] = []
        self.environ: list[dict[str, str]] = []
        self.processes: list[FakeProcess] = []
        self.started = threading.Event()  # the first call is in flight

    def __call__(self, argv, environ) -> FakeProcess:
        first = not self.argv
        self.argv.append(list(argv))
        self.environ.append(dict(environ))
        process = FakeProcess(self.first_delay if first and self.first_delay is not None else self.delay)
        self.processes.append(process)
        self.started.set()
        return process

    def commands(self) -> list[tuple[str, str]]:
        """(command, state) per call started, state "" for a release."""
        return [(argv[2], argv[argv.index("--state") + 1] if "--state" in argv else "") for argv in self.argv]


def seqs(fake: Herdr) -> list[int]:
    return [int(argv[argv.index("--seq") + 1]) for argv in fake.argv]


def settle(reporter: HerdrReporter) -> None:
    """Wait until the worker holds no call, waiting or in flight: the next change is then a call of its own."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with reporter.ready:
            if reporter.pending is None and reporter.running is None:
                return
        time.sleep(0.005)
    raise AssertionError("the worker never went idle")


def report_each(reporter: HerdrReporter, states: tuple[str, ...]) -> None:
    """Report `states` one at a time, each after the last call finished, so none is replaced."""
    for state in states:
        reporter.report(state)
        settle(reporter)


class GatingTests(unittest.TestCase):
    def test_only_herdr_env_1_with_a_pane_id_names_a_pane(self) -> None:
        self.assertEqual(herdr_pane(IN_HERDR), PANE)
        self.assertEqual(herdr_pane({"HERDR_ENV": "1", "HERDR_PANE_ID": f"  {PANE}\n"}), PANE)
        for environ in ({}, {"HERDR_PANE_ID": PANE}, {"HERDR_ENV": "0", "HERDR_PANE_ID": PANE},
                        {"HERDR_ENV": "true", "HERDR_PANE_ID": PANE}, {"HERDR_ENV": "1"},
                        {"HERDR_ENV": "1", "HERDR_PANE_ID": "  "}):
            self.assertIsNone(herdr_pane(environ), environ)

    def test_outside_herdr_nothing_is_spawned_and_no_thread_starts(self) -> None:
        fake = Herdr()
        reporter = HerdrReporter({"HERDR_PANE_ID": PANE}, spawn=fake)
        with patch.object(threading.Thread, "start", side_effect=AssertionError("no worker outside herdr")):
            for state in ("idle", "working", "blocked", "idle"):
                reporter.report(state)
            reporter.release()
        self.assertEqual(fake.argv, [])
        self.assertIsNone(reporter.worker)

    def test_a_release_with_nothing_reported_calls_nothing(self) -> None:
        fake = Herdr()
        reporter = HerdrReporter(IN_HERDR, spawn=fake)
        reporter.release()
        self.assertEqual(fake.argv, [])

    def test_herdr_bin_path_names_the_binary_only_when_absolute(self) -> None:
        self.assertEqual(herdr_binary({"HERDR_BIN_PATH": "/opt/herdr/bin/herdr"}), "/opt/herdr/bin/herdr")
        for value in ("", "herdr-dev", "./bin/herdr", "bin/herdr"):
            self.assertEqual(herdr_binary({"HERDR_BIN_PATH": value}), "herdr", value)
        self.assertEqual(herdr_binary({}), "herdr")
        fake = Herdr()
        reporter = HerdrReporter({**IN_HERDR, "HERDR_BIN_PATH": "/opt/herdr/bin/herdr"}, spawn=fake)
        reporter.report("idle")
        reporter.release()
        self.assertEqual({argv[0] for argv in fake.argv}, {"/opt/herdr/bin/herdr"})


class ReportTests(unittest.TestCase):
    def test_only_a_change_is_reported_and_the_exit_releases(self) -> None:
        fake = Herdr()
        reporter = HerdrReporter(IN_HERDR, spawn=fake)
        report_each(reporter, ("idle", "idle", "working", "working", "working", "blocked", "blocked", "idle"))
        reporter.release()
        self.assertEqual(fake.commands(), [
            ("report-agent", "idle"), ("report-agent", "working"), ("report-agent", "blocked"),
            ("report-agent", "idle"), ("release-agent", ""),
        ])
        self.assertIsNone(reporter.worker)

    def test_the_argv_is_herdrs_cli_with_source_omh_tui_and_agent_omh(self) -> None:
        fake = Herdr()
        reporter = HerdrReporter(IN_HERDR, spawn=fake, clock=lambda: 1000)
        reporter.report("working")
        settle(reporter)
        reporter.release()
        self.assertEqual(fake.argv, [
            ["herdr", "pane", "report-agent", PANE, "--source", "omh:tui", "--agent", "omh",
             "--state", "working", "--seq", "1000"],
            ["herdr", "pane", "release-agent", PANE, "--source", "omh:tui", "--agent", "omh", "--seq", "1001"],
        ])
        self.assertEqual(fake.environ, [IN_HERDR, IN_HERDR])  # herdr's CLI finds its socket in this environment

    def test_every_seq_is_above_the_last_whatever_the_clock_does(self) -> None:
        # herdr ignores a report whose seq is not above the last from the source; the wall clock can repeat or step back.
        readings = iter([5_000, 5_000, 4_000, 9_000, 9_000])
        fake = Herdr()
        reporter = HerdrReporter(IN_HERDR, spawn=fake, clock=lambda: next(readings))
        report_each(reporter, ("idle", "working", "idle", "blocked"))
        reporter.release()
        self.assertEqual(seqs(fake), [5_000, 5_001, 5_002, 9_000, 9_001])

    def test_a_slow_herdr_gets_only_the_newest_state_and_the_release_still_runs(self) -> None:
        # A herdr taking most of a second per call: queued changes used to push the release past the exit's wait.
        fake = Herdr(delay=0.05, first_delay=0.9)
        reporter = HerdrReporter(IN_HERDR, spawn=fake)
        reporter.report("idle")
        self.assertTrue(fake.started.wait(5))  # the first call is in flight
        for state in ("working", "blocked", "working", "idle", "working"):
            reporter.report(state)  # returns at once: none of these waits on herdr
        began = time.monotonic()
        reporter.release()
        self.assertLess(time.monotonic() - began, 0.5)
        self.assertEqual(fake.commands(), [("report-agent", "idle"), ("release-agent", "")])
        self.assertTrue(fake.processes[0].killed.is_set())  # the stale report in flight was stopped
        self.assertFalse(fake.processes[1].killed.is_set())  # the release ran to its end
        self.assertEqual(seqs(fake), sorted(seqs(fake)))

    def test_a_change_while_a_call_runs_replaces_the_one_waiting(self) -> None:
        fake = Herdr(first_delay=0.3)
        reporter = HerdrReporter(IN_HERDR, spawn=fake)
        reporter.report("idle")
        self.assertTrue(fake.started.wait(5))
        for state in ("working", "blocked", "working"):
            reporter.report(state)
        settle(reporter)
        reporter.release()
        self.assertEqual([state for _command, state in fake.commands()], ["idle", "working", ""])

    def test_calls_run_off_the_callers_thread_on_one_worker(self) -> None:
        threads: list[threading.Thread] = []
        fake = Herdr()

        def spawn(argv, environ):
            threads.append(threading.current_thread())
            return fake(argv, environ)

        reporter = HerdrReporter(IN_HERDR, spawn=spawn)
        report_each(reporter, ("idle", "working", "blocked"))
        reporter.release()
        self.assertNotIn(threading.main_thread(), threads)
        self.assertEqual(len(set(threads)), 1)

    def test_an_exit_waits_a_bounded_time_and_stops_a_release_herdr_never_answers(self) -> None:
        fake = Herdr(delay=30)
        reporter = HerdrReporter(IN_HERDR, spawn=fake)
        reporter.report("working")
        self.assertTrue(fake.started.wait(5))
        with patch.object(herdr, "RELEASE_WAIT_SECONDS", 0.1):
            began = time.monotonic()
            reporter.release()
            elapsed = time.monotonic() - began
        self.assertLess(elapsed, 0.5)
        self.assertEqual(fake.commands(), [("report-agent", "working"), ("release-agent", "")])
        self.assertTrue(all(process.killed.is_set() for process in fake.processes))  # no herdr call outlives the TUI

    def test_a_report_after_the_release_sends_nothing_and_starts_no_worker(self) -> None:
        fake = Herdr()
        reporter = HerdrReporter(IN_HERDR, spawn=fake)
        reporter.report("idle")
        reporter.release()
        with patch.object(threading.Thread, "start", side_effect=AssertionError("no second worker")):
            reporter.report("working")
            reporter.release()
        self.assertEqual(fake.commands(), [("report-agent", "idle"), ("release-agent", "")])
        self.assertIsNone(reporter.worker)


class SilentFailureTests(unittest.TestCase):
    def test_a_missing_binary_starts_nothing(self) -> None:
        self.assertIsNone(spawn_herdr(["/nonexistent/herdr-binary", "pane", "report-agent", PANE], IN_HERDR))

    def test_a_spawn_error_is_dropped(self) -> None:
        for error in (PermissionError(13, "denied"), subprocess.SubprocessError("x")):
            with patch.object(herdr.subprocess, "Popen", side_effect=error):
                self.assertIsNone(spawn_herdr(["herdr", "pane", "report-agent", PANE], IN_HERDR))

    def test_the_call_is_quiet_and_gets_the_given_environment(self) -> None:
        with patch.object(herdr.subprocess, "Popen") as popen:
            spawn_herdr(("herdr", "pane", "release-agent", PANE), IN_HERDR)
        (argv,), options = popen.call_args
        self.assertEqual(argv, ["herdr", "pane", "release-agent", PANE])
        self.assertEqual(options["env"], IN_HERDR)
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(options[stream], subprocess.DEVNULL)

    def test_a_call_past_its_time_is_stopped(self) -> None:
        process = FakeProcess(delay=30)
        with patch.object(herdr, "CALL_SECONDS", 0.05):
            began = time.monotonic()
            finish(process)
        self.assertLess(time.monotonic() - began, 0.5)
        self.assertTrue(process.killed.is_set())

    def test_a_call_that_could_not_start_does_not_stop_the_next(self) -> None:
        started: list[list[str]] = []

        def spawn(argv, environ):
            started.append(list(argv))
            return None if len(started) == 1 else FakeProcess(0)

        reporter = HerdrReporter(IN_HERDR, spawn=spawn)
        reporter.report("idle")
        deadline = time.monotonic() + 5
        while not started and time.monotonic() < deadline:
            time.sleep(0.005)
        reporter.report("working")
        reporter.release()
        self.assertEqual(started[-1][2], "release-agent")
        self.assertGreaterEqual(len(started), 2)


class AppTests(unittest.TestCase):
    """The TUI reports what its title says, on the frames that draw it, and releases on the way out."""

    def app(self, environ: dict[str, str]):
        fake = Herdr()
        app, client = make_app(environ=environ)
        app.herdr = HerdrReporter(environ, spawn=fake)
        return app, client, fake

    def draw(self, app) -> None:
        drawn(app)
        settle(app.herdr)

    def test_the_title_state_maps_to_herdrs_three_states(self) -> None:
        app, _client, fake = self.app(IN_HERDR)
        self.draw(app)
        self.draw(app)  # a frame that changes nothing says nothing
        app.turn_pending = True
        self.draw(app)
        app._on_event(Event("message.start", SID, {}))
        self.draw(app)
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x", "description": "delete"}))
        self.draw(app)
        self.assertIn("waiting on you", "".join(app.terminal.writes))  # type: ignore[attr-defined]
        app._close_slot()  # the request answered or withdrawn: the turn runs on
        self.draw(app)
        app.turn_pending = False
        app._on_event(Event("message.complete", SID, {"text": "done", "status": "complete"}))
        self.draw(app)
        app._shutdown()
        self.assertEqual(fake.commands(), [
            ("report-agent", "idle"), ("report-agent", "working"), ("report-agent", "blocked"),
            ("report-agent", "working"), ("report-agent", "idle"), ("release-agent", ""),
        ])

    def test_an_interrupted_or_refused_turn_goes_back_to_idle(self) -> None:
        for end in (Event("message.complete", SID, {"text": "", "status": "interrupted"}),
                    Event("error", SID, {"message": "Turn cancelled before the agent was ready"})):
            app, _client, fake = self.app(IN_HERDR)
            app._send_prompt("go")
            app._on_event(Event("message.start", SID, {}))
            self.draw(app)
            app._on_event(end)
            self.draw(app)
            self.assertEqual(fake.commands(), [("report-agent", "working"), ("report-agent", "idle")], end.type)

    def test_the_release_runs_when_the_loop_raises(self) -> None:
        app, _client, fake = self.app(IN_HERDR)
        self.draw(app)
        app.client.wake_fd = lambda: None  # type: ignore[attr-defined]
        with patch.object(app, "_step", side_effect=RuntimeError("boom")), \
                patch.object(app, "_render_when_due"):
            with self.assertRaises(RuntimeError):
                app.run()
        self.assertEqual(fake.commands(), [("report-agent", "idle"), ("release-agent", "")])

    def test_outside_herdr_the_tui_spawns_nothing(self) -> None:
        app, _client, fake = self.app({})
        drawn(app)
        app.turn_pending = True
        drawn(app)
        app._shutdown()
        self.assertEqual(fake.argv, [])

    def test_the_app_builds_its_reporter_from_its_own_environment(self) -> None:
        app, _client = make_app(environ=IN_HERDR)
        self.assertEqual(app.herdr.pane, PANE)
        app, _client = make_app()
        self.assertIsNone(app.herdr.pane)


if __name__ == "__main__":
    unittest.main()
