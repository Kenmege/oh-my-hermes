"""The session's git branch and how many files differ, read off the render path for the row under the info line.

`GitWatcher` runs `git --no-optional-locks status --porcelain=v2 --branch`
in the session's working directory on a thread of its own: once at start,
again on `refresh()` (a turn ended, a tool finished) and every
`INTERVAL_SECONDS` besides. A frame only ever reads the last answer
(`state`), so a slow or missing git never holds a frame up. Each run has
`TIMEOUT_SECONDS`; one that does not finish, a git that is not installed, a
directory that is not a repository, or output this module cannot read all
leave `state` None, and the row shows no branch. Nothing is written: the
optional-locks flag keeps `status` from refreshing the index, and no
repository-configured fsmonitor hook runs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import subprocess
import threading

TIMEOUT_SECONDS = 2.0
INTERVAL_SECONDS = 15.0
SHORT_SHA = 7  # a detached HEAD is named by this much of its commit
# `core.fsmonitor=false`: a repository's configured fsmonitor hook never runs for a read the person did not ask for.
ARGV = ("git", "-c", "core.fsmonitor=false", "--no-optional-locks", "status", "--porcelain=v2", "--branch")


@dataclass(frozen=True)
class GitState:
    branch: str  # the branch, or a detached HEAD's short sha
    changed: int  # tracked files modified, staged, renamed or unmerged
    untracked: int


def parse_status(text: str) -> GitState | None:
    """`git status --porcelain=v2 --branch` read as a `GitState`; None when it names no head."""
    head = oid = ""
    changed = untracked = 0
    for line in text.splitlines():
        if line.startswith("# branch.head "):
            head = line.removeprefix("# branch.head ").strip()
        elif line.startswith("# branch.oid "):
            oid = line.removeprefix("# branch.oid ").strip()
        elif line.startswith(("1 ", "2 ", "u ")):
            changed += 1
        elif line.startswith("? "):
            untracked += 1
    if head == "(detached)":
        head = oid[:SHORT_SHA] if oid and oid != "(initial)" else ""
    return GitState(head, changed, untracked) if head else None


def read_status(cwd: str, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> GitState | None:
    """One read in `cwd`; None for anything but a clean answer from git (see the module docstring)."""
    try:
        done = run(list(ARGV), cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
                   stdin=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return parse_status(done.stdout) if done.returncode == 0 else None


class GitWatcher:
    """Keeps the newest `read_status` of `cwd` in `state`; `start`, `refresh` and `stop` never block on git."""

    def __init__(self, cwd: str, read: Callable[[str], GitState | None] = read_status) -> None:
        self.cwd = cwd
        self.state: GitState | None = None
        self._read = read
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self.on_change: Callable[[], None] | None = None

    def start(self, wake: Callable[[], None] | None = None) -> None:
        """Start the reading thread; `wake` is called, on that thread, when a read changes `state`."""
        self.on_change = wake
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="omh-git-status", daemon=True)
            self._thread.start()

    def refresh(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 1.0) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stopping.is_set():
            state = self._read(self.cwd)
            if state != self.state:
                self.state = state
                if self.on_change is not None:
                    self.on_change()
            self._wake.wait(INTERVAL_SECONDS)
            self._wake.clear()
