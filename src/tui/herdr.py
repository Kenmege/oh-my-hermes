"""The OMH TUI's state, told to herdr when the TUI runs in a herdr pane.

herdr (a terminal workspace manager for coding agents) lists each pane's agent
as idle, working or blocked. It reads Hermes' state from Hermes' own title and
screen, which the OMH TUI does not draw, so inside a herdr pane the TUI says
it: `herdr pane report-agent` each time the state its title shows changes, and
`herdr pane release-agent` as it exits. That happens only where herdr's pane
environment is present, `HERDR_ENV=1` and a `HERDR_PANE_ID`, the contract
herdr's own Hermes plugin reads; anywhere else nothing here spawns anything.
The binary is `HERDR_BIN_PATH` or `herdr` on PATH, as for that plugin, and the
report reaches herdr over its local socket through its CLI: no network call.

The calls run on one worker thread, in the order they were made, so the loop
never waits on herdr, and every failure (no `herdr` on PATH, a dead socket, a
call past `CALL_SECONDS`) is dropped. Each carries a `--seq` from the wall
clock in nanoseconds, as herdr's plugins send, and never lower than the last:
herdr ignores a report whose seq is not above the last one it took from the
source, and the source is the same for every TUI launched in that pane.

The source is `omh:tui` and the agent `hermes`. herdr drops a state report
from `herdr:hermes` (its plugin's source carries session identity only), and
it keeps a session id only from its own `herdr:*` sources, so none is sent.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import queue
import subprocess
import threading
import time

SOURCE = "omh:tui"
AGENT = "hermes"
CALL_SECONDS = 1.0  # as long as herdr's own plugin waits on one call
RELEASE_WAIT_SECONDS = 2.0  # the most an exit waits for the calls still queued and the release


def herdr_pane(environ: Mapping[str, str]) -> str | None:
    """The herdr pane the TUI runs in, or None outside herdr."""
    if environ.get("HERDR_ENV") != "1":
        return None
    return environ.get("HERDR_PANE_ID", "").strip() or None


def run_herdr(argv: Sequence[str], environ: Mapping[str, str]) -> None:
    """One herdr CLI call, waited on for at most `CALL_SECONDS`; whatever goes wrong is dropped."""
    try:
        subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(environ),
            timeout=CALL_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


class HerdrReporter:
    """`report` on every frame and `release` on the way out; only a change, inside herdr, calls herdr."""

    def __init__(
        self,
        environ: Mapping[str, str],
        *,
        run: Callable[[Sequence[str], Mapping[str, str]], None] = run_herdr,
        clock: Callable[[], int] = time.time_ns,
    ) -> None:
        self.environ = environ
        self.pane = herdr_pane(environ)
        self.binary = environ.get("HERDR_BIN_PATH") or "herdr"
        self.run = run
        self.clock = clock
        self.state: str | None = None  # the last state reported; None: herdr holds nothing from this TUI
        self.seq = 0
        self.calls: queue.SimpleQueue[list[str] | None] = queue.SimpleQueue()
        self.worker: threading.Thread | None = None

    def report(self, state: str) -> None:
        """Tell herdr the pane's agent is now `state` (idle, working or blocked), if that changed."""
        if self.pane is None or state == self.state:
            return
        self.state = state
        self._send("report-agent", "--state", state)

    def release(self) -> None:
        """Let go of the pane in herdr's agent list, waiting at most `RELEASE_WAIT_SECONDS` for the queued calls."""
        if self.state is None or self.worker is None:
            return
        self.state = None
        self._send("release-agent")
        self.calls.put(None)
        self.worker.join(RELEASE_WAIT_SECONDS)
        self.worker = None

    def _send(self, command: str, *options: str) -> None:
        assert self.pane is not None
        self.seq = max(self.seq + 1, self.clock())
        argv = [self.binary, "pane", command, self.pane, "--source", SOURCE, "--agent", AGENT, *options]
        self.calls.put([*argv, "--seq", str(self.seq)])
        if self.worker is None:
            self.worker = threading.Thread(target=self._work, name="omh-tui-herdr", daemon=True)
            self.worker.start()

    def _work(self) -> None:
        while (argv := self.calls.get()) is not None:
            self.run(argv, self.environ)
