"""The OMH TUI's state, told to herdr when the TUI runs in a herdr pane.

herdr (a terminal workspace manager for coding agents) lists each pane's agent
as idle, working or blocked. It reads Hermes' state from Hermes' own title and
screen, which the OMH TUI does not draw, so inside a herdr pane the TUI says
it: `herdr pane report-agent` each time the state its title shows changes, and
`herdr pane release-agent` as it exits. That happens only where herdr's pane
environment is present, `HERDR_ENV=1` and a `HERDR_PANE_ID`, the same gate
herdr's own Hermes plugin reads (that plugin reports a session id only, never
a state); anywhere else nothing here spawns anything. The binary is an
absolute `HERDR_BIN_PATH` or `herdr` on PATH, and the report reaches herdr over
its local socket through its CLI: no network call.

One call runs at a time, on a worker thread, so the loop never waits on herdr
(not even on starting one). Only the newest call waits behind it: a change
made while a call runs replaces the one still waiting, as herdr's integration
guide asks (send only the latest state, drop the older ones). The release also
replaces any report waiting and stops the report in flight, and an exit that
gives up waiting on the release stops that too, so no herdr call outlives the
TUI; every stop is made on the worker, which also reaps. Every failure (no
herdr, a dead socket, a call past `CALL_SECONDS`) is dropped. Each call
carries a `--seq` from the wall clock in nanoseconds, as herdr's plugins send,
never lower than the last: herdr ignores a report whose seq is not above the
last one it took from the source, which is the same for every TUI launched in
the pane. A dropped call only skips a number.

The source is `omh:tui` and the agent `omh`, a label herdr does not know, as
its integration guide asks: a known label (`hermes`) ties the report to
herdr's own Hermes process and screen detection, which drop or override it.
herdr lists an unknown label as reported, and clears it once the pane is back
at its shell. No session id is sent: herdr keeps one only from its own
`herdr:*` sources.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import os
import subprocess
import threading
import time
from typing import Protocol

SOURCE = "omh:tui"
AGENT = "omh"
CALL_SECONDS = 1.0  # as long as herdr's own plugin waits on one call
RELEASE_WAIT_SECONDS = 2.0  # the most an exit waits for the release to finish
STOP_WAIT_SECONDS = 0.2  # past that, how long it waits for the worker to stop the release
SLICE_SECONDS = 0.05  # how often a call in flight checks whether it should stop
RELEASE = "release-agent"


class Process(Protocol):
    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


def herdr_pane(environ: Mapping[str, str]) -> str | None:
    """The herdr pane the TUI runs in, or None outside herdr."""
    if environ.get("HERDR_ENV") != "1":
        return None
    return environ.get("HERDR_PANE_ID", "").strip() or None


def herdr_binary(environ: Mapping[str, str]) -> str:
    """`HERDR_BIN_PATH` when it is an absolute path, `herdr` on PATH otherwise (never one relative to the cwd)."""
    path = environ.get("HERDR_BIN_PATH", "")
    return path if os.path.isabs(path) else "herdr"


def spawn_herdr(argv: Sequence[str], environ: Mapping[str, str]) -> Process | None:
    """Start one herdr CLI call, or None when it cannot start."""
    try:
        return subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(environ),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def finish(process: Process, stop: Callable[[], bool] = lambda: False) -> None:
    """Wait for a call for at most `CALL_SECONDS`, or until `stop()` says so, then stop it; reaped either way."""
    deadline = time.monotonic() + CALL_SECONDS
    while True:
        try:
            process.wait(timeout=max(min(SLICE_SECONDS, deadline - time.monotonic()), 0.0))
            return
        except subprocess.TimeoutExpired:
            if stop() or time.monotonic() >= deadline:
                process.kill()
                process.wait()
                return


class HerdrReporter:
    """`report` on every frame and `release` on the way out; only a change, inside herdr, calls herdr."""

    def __init__(
        self,
        environ: Mapping[str, str],
        *,
        spawn: Callable[[Sequence[str], Mapping[str, str]], Process | None] = spawn_herdr,
        clock: Callable[[], int] = time.time_ns,
    ) -> None:
        self.environ = environ
        self.pane = herdr_pane(environ)
        self.binary = herdr_binary(environ)
        self.spawn = spawn
        self.clock = clock
        self.state: str | None = None  # the last state reported; None: herdr holds nothing from this TUI
        self.released = False  # the exit has run: nothing more is sent
        self.seq = 0
        self.ready = threading.Condition()  # guards the four below
        self.pending: list[str] | None = None  # the newest call not started yet
        self.running: list[str] | None = None  # the call in flight
        self.closing = False  # the release is in: a report in flight stops, and the worker ends after the release
        self.abandoned = False  # the exit stopped waiting: nothing more starts, and the call in flight stops
        self.worker: threading.Thread | None = None

    def report(self, state: str) -> None:
        """Tell herdr the pane's agent is now `state` (idle, working or blocked), if that changed."""
        if self.pane is None or self.released or state == self.state:
            return
        self.state = state
        self._send(self._argv("report-agent", "--state", state))

    def release(self) -> None:
        """Let go of the pane in herdr's agent list, in place of any report not sent yet; once."""
        reported, self.released = self.state is not None, True
        if not reported or self.worker is None:
            return
        self.state = None
        argv = self._argv(RELEASE)
        with self.ready:
            self.pending, self.closing = argv, True
            self.ready.notify()
        self.worker.join(RELEASE_WAIT_SECONDS)
        if self.worker.is_alive():  # a herdr that holds the release: the worker stops it, so it does not outlive the TUI
            with self.ready:
                self.abandoned = True
                self.ready.notify()
            self.worker.join(STOP_WAIT_SECONDS)
        self.worker = None

    def _argv(self, command: str, *options: str) -> list[str]:
        self.seq = max(self.seq + 1, self.clock())
        assert self.pane is not None
        return [self.binary, "pane", command, self.pane, "--source", SOURCE, "--agent", AGENT, *options,
                "--seq", str(self.seq)]

    def _send(self, argv: list[str]) -> None:
        with self.ready:
            self.pending = argv
            self.ready.notify()
        if self.worker is None:
            self.worker = threading.Thread(target=self._work, name="omh-tui-herdr", daemon=True)
            self.worker.start()

    def _stop(self, argv: list[str]) -> bool:
        """Whether the call `argv` in flight should stop: the exit gave up, or it is a report the release replaced."""
        with self.ready:
            return self.abandoned or (self.closing and argv[2] != RELEASE)

    def _work(self) -> None:
        while True:
            with self.ready:
                while self.pending is None and not self.closing:
                    self.ready.wait()
                argv, self.pending = self.pending, None
                if argv is None or self.abandoned:
                    return
                self.running = argv
            try:
                process = self.spawn(argv, self.environ)  # outside the lock: `report` never waits on a fork
                if process is not None:
                    finish(process, lambda: self._stop(argv))
            finally:
                with self.ready:
                    self.running = None
