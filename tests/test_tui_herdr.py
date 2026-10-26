"""herdr: inside a herdr pane the OMH TUI tells herdr its state on each change, and lets go of the pane on exit.

herdr lists each pane's agent as idle, working or blocked. The TUI reports
the state its title shows (`waiting on you` is blocked, `running` is working,
`idle` is idle) with `herdr pane report-agent`, only when it changes, and
`herdr pane release-agent` as it exits. Only `HERDR_ENV=1` with a
`HERDR_PANE_ID` turns this on; anywhere else nothing is spawned. The calls run
on one worker thread in order, each `--seq` above the last, and every failure
is dropped.
"""

from __future__ import annotations

import subprocess
import threading
import unittest
from unittest.mock import patch

from test_tui_modes import SID, drawn, make_app
from omh.tui import herdr
from omh.tui.gateway import Event, ServerRequest
from omh.tui.herdr import HerdrReporter, herdr_pane, run_herdr

PANE = "w1:p1"
IN_HERDR = {"HERDR_ENV": "1", "HERDR_PANE_ID": PANE}


class Recorder:
    """The spawn seam: each argv the worker would have run, with the environment it would have run with."""

    def __init__(self) -> None:
        self.argv: list[list[str]] = []
        self.environ: list[dict[str, str]] = []

    def __call__(self, argv, environ) -> None:
        self.argv.append(list(argv))
        self.environ.append(dict(environ))

    def commands(self) -> list[tuple[str, ...]]:
        """(command, state) per call, state "" for a release."""
        found = []
        for argv in self.argv:
            state = argv[argv.index("--state") + 1] if "--state" in argv else ""
            found.append((argv[2], state))
        return found


def seqs(recorder: Recorder) -> list[int]:
    return [int(argv[argv.index("--seq") + 1]) for argv in recorder.argv]


class GatingTests(unittest.TestCase):
    def test_only_herdr_env_1_with_a_pane_id_names_a_pane(self) -> None:
        self.assertEqual(herdr_pane(IN_HERDR), PANE)
        self.assertEqual(herdr_pane({"HERDR_ENV": "1", "HERDR_PANE_ID": f"  {PANE}\n"}), PANE)
        for environ in ({}, {"HERDR_PANE_ID": PANE}, {"HERDR_ENV": "0", "HERDR_PANE_ID": PANE},
                        {"HERDR_ENV": "true", "HERDR_PANE_ID": PANE}, {"HERDR_ENV": "1"},
                        {"HERDR_ENV": "1", "HERDR_PANE_ID": "  "}):
            self.assertIsNone(herdr_pane(environ), environ)

    def test_outside_herdr_nothing_is_spawned_and_no_thread_starts(self) -> None:
        recorder = Recorder()
        reporter = HerdrReporter({"HERDR_PANE_ID": PANE}, run=recorder)
        with patch.object(threading.Thread, "start", side_effect=AssertionError("no worker outside herdr")):
            for state in ("idle", "working", "blocked", "idle"):
                reporter.report(state)
            reporter.release()
        self.assertEqual(recorder.argv, [])
        self.assertIsNone(reporter.worker)

    def test_a_release_with_nothing_reported_calls_nothing(self) -> None:
        recorder = Recorder()
        reporter = HerdrReporter(IN_HERDR, run=recorder)
        reporter.release()
        self.assertEqual(recorder.argv, [])


class ReportTests(unittest.TestCase):
    def test_only_a_change_is_reported_and_the_exit_releases(self) -> None:
        recorder = Recorder()
        reporter = HerdrReporter(IN_HERDR, run=recorder)
        for state in ("idle", "idle", "working", "working", "working", "blocked", "blocked", "idle"):
            reporter.report(state)
        reporter.release()
        self.assertEqual(recorder.commands(), [
            ("report-agent", "idle"), ("report-agent", "working"), ("report-agent", "blocked"),
            ("report-agent", "idle"), ("release-agent", ""),
        ])
        self.assertIsNone(reporter.worker)

    def test_the_argv_is_herdrs_cli_with_source_omh_tui_and_agent_hermes(self) -> None:
        recorder = Recorder()
        reporter = HerdrReporter(IN_HERDR, run=recorder, clock=lambda: 1000)
        reporter.report("working")
        reporter.release()
        self.assertEqual(recorder.argv, [
            ["herdr", "pane", "report-agent", PANE, "--source", "omh:tui", "--agent", "hermes",
             "--state", "working", "--seq", "1000"],
            ["herdr", "pane", "release-agent", PANE, "--source", "omh:tui", "--agent", "hermes", "--seq", "1001"],
        ])
        self.assertEqual(recorder.environ, [IN_HERDR, IN_HERDR])  # herdr's CLI finds its socket in this environment

    def test_herdr_bin_path_names_the_binary(self) -> None:
        recorder = Recorder()
        reporter = HerdrReporter({**IN_HERDR, "HERDR_BIN_PATH": "/opt/herdr/bin/herdr"}, run=recorder)
        reporter.report("idle")
        reporter.release()
        self.assertEqual({argv[0] for argv in recorder.argv}, {"/opt/herdr/bin/herdr"})

    def test_every_seq_is_above_the_last_whatever_the_clock_does(self) -> None:
        # herdr ignores a report whose seq is not above the last from the source; the wall clock can repeat or step back.
        readings = iter([5_000, 5_000, 4_000, 9_000, 9_000])
        recorder = Recorder()
        reporter = HerdrReporter(IN_HERDR, run=recorder, clock=lambda: next(readings))
        for state in ("idle", "working", "idle", "blocked"):
            reporter.report(state)
        reporter.release()
        self.assertEqual(seqs(recorder), [5_000, 5_001, 5_002, 9_000, 9_001])

    def test_reports_run_in_the_order_made_off_the_callers_thread(self) -> None:
        gate = threading.Event()
        callers: list[threading.Thread] = []
        recorder = Recorder()

        def slow(argv, environ) -> None:
            gate.wait(5)  # a herdr that takes its time answering
            callers.append(threading.current_thread())
            recorder(argv, environ)

        reporter = HerdrReporter(IN_HERDR, run=slow)
        for state in ("idle", "working", "blocked", "idle"):
            reporter.report(state)  # returns at once: none of these waits on herdr
        self.assertEqual(recorder.argv, [])
        gate.set()
        reporter.release()
        self.assertEqual([state for _command, state in recorder.commands()], ["idle", "working", "blocked", "idle", ""])
        self.assertEqual(seqs(recorder), sorted(seqs(recorder)))
        self.assertNotIn(threading.main_thread(), callers)
        self.assertEqual(len(set(callers)), 1)  # one worker for the launch

    def test_an_exit_waits_a_bounded_time_on_a_herdr_that_never_answers(self) -> None:
        stuck = threading.Event()
        reporter = HerdrReporter(IN_HERDR, run=lambda argv, environ: stuck.wait(10))
        reporter.report("working")
        with patch.object(herdr, "RELEASE_WAIT_SECONDS", 0.05):
            reporter.release()  # returns; the worker is a daemon and ends with the process
        stuck.set()


class SilentFailureTests(unittest.TestCase):
    def test_a_missing_binary_is_dropped(self) -> None:
        run_herdr(["/nonexistent/herdr-binary", "pane", "report-agent", PANE], IN_HERDR)

    def test_a_timeout_and_a_spawn_error_are_dropped(self) -> None:
        for error in (subprocess.TimeoutExpired(["herdr"], herdr.CALL_SECONDS), PermissionError(13, "denied"),
                      subprocess.SubprocessError("x")):
            with patch.object(herdr.subprocess, "run", side_effect=error):
                run_herdr(["herdr", "pane", "report-agent", PANE], IN_HERDR)

    def test_the_call_is_bounded_quiet_and_gets_the_given_environment(self) -> None:
        with patch.object(herdr.subprocess, "run") as run:
            run_herdr(("herdr", "pane", "release-agent", PANE), IN_HERDR)
        (argv,), options = run.call_args
        self.assertEqual(argv, ["herdr", "pane", "release-agent", PANE])
        self.assertEqual(options["timeout"], herdr.CALL_SECONDS)
        self.assertEqual(options["env"], IN_HERDR)
        self.assertFalse(options["check"])
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(options[stream], subprocess.DEVNULL)

    def test_a_failing_call_does_not_stop_the_next(self) -> None:
        reporter = HerdrReporter(IN_HERDR)
        with patch.object(herdr.subprocess, "run", side_effect=[FileNotFoundError(2, "herdr"), None, None]) as run:
            reporter.report("idle")
            reporter.report("working")
            reporter.release()
        self.assertEqual(run.call_count, 3)


class AppTests(unittest.TestCase):
    """The TUI reports what its title says, on the frames that draw it, and releases on the way out."""

    def app(self, environ: dict[str, str]):
        recorder = Recorder()
        app, _client = make_app(environ=environ)
        app.herdr = HerdrReporter(environ, run=recorder)
        return app, recorder

    def test_the_title_state_maps_to_herdrs_three_states(self) -> None:
        app, recorder = self.app(IN_HERDR)
        drawn(app)
        drawn(app)  # a frame that changes nothing says nothing
        app.turn_pending = True
        drawn(app)
        app._on_event(Event("message.start", SID, {}))
        drawn(app)
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x", "description": "delete"}))
        drawn(app)
        self.assertIn("waiting on you", "".join(app.terminal.writes))  # type: ignore[attr-defined]
        app._close_slot()  # the request answered or withdrawn: the turn runs on
        drawn(app)
        app.turn_pending = False
        app._on_event(Event("message.complete", SID, {"text": "done", "status": "complete"}))
        drawn(app)
        app._shutdown()
        self.assertEqual(recorder.commands(), [
            ("report-agent", "idle"), ("report-agent", "working"), ("report-agent", "blocked"),
            ("report-agent", "working"), ("report-agent", "idle"), ("release-agent", ""),
        ])

    def test_outside_herdr_the_tui_spawns_nothing(self) -> None:
        app, recorder = self.app({})
        drawn(app)
        app.turn_pending = True
        drawn(app)
        app._shutdown()
        self.assertEqual(recorder.argv, [])

    def test_the_app_builds_its_reporter_from_its_own_environment(self) -> None:
        app, _client = make_app(environ=IN_HERDR)
        self.assertEqual(app.herdr.pane, PANE)
        app, _client = make_app()
        self.assertIsNone(app.herdr.pane)


if __name__ == "__main__":
    unittest.main()
