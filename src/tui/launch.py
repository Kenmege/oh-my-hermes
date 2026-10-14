"""How bare `omh` opens the OMH TUI through Hermes' own launcher.

`omh` runs the person's `hermes --tui` with `HERMES_TUI_DIR` pointing at
`frontend/` beside this file. Hermes' launcher then resolves the profile, the
resume target, the working directory and session ownership exactly as it does
for its own terminal, and runs `frontend/dist/entry.js` in place of its Ink
app. That file is a small Node shim that hands the terminal to
`omh.tui.app.main` on the interpreter named in `OMH_TUI_PYTHON`, started with
`-E -P` so neither the launcher's `PYTHONPATH` nor the working directory is
on its import path. `-E` also ignores `PYTHONUSERBASE`: an OMH installed under
a custom user base cannot import itself there, and the shim exits 75 so `omh`
falls back.

When the TUI cannot start it creates the file `omh` named in
`FALLBACK_SENTINEL_ENV` and exits with `FALLBACK_EXIT`, and `omh` opens
Hermes' own terminal instead (`hermes --tui` where Node is found, plain
`hermes` where it is not, after one line on stderr saying why), so a failure
costs the person the OMH surface, never the session. `omh_tui_blocker` is the
same choice made before anything starts. Exit 75 without that file (Hermes
uses 75 on paths of its own), like every other exit code, is the session
ending and is returned as it is.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import signal
import tempfile
from typing import TextIO

from .terminal import terminal_blocker, terminal_supported

# EX_TEMPFAIL. Not 0/1/2/130 (ordinary exits) and not 42, which Hermes'
# launcher reads as "relaunch as `hermes update`".
FALLBACK_EXIT = 75
# The file that makes a `FALLBACK_EXIT` a fallback request: Hermes exits 75
# (EX_TEMPFAIL) on paths of its own, so `omh` reopens Hermes' terminal only
# when the TUI also created this file. `omh` names a path in a private
# directory it creates, and removes the directory when the launch ends
# (`fallback_sentinel`); `gateway_spawn` drops every `OMH_TUI_` name, so the
# gateway and its tools never see it.
FALLBACK_SENTINEL_ENV = "OMH_TUI_FALLBACK_SENTINEL"
# Signals that end `omh` while the TUI runs: closing the terminal window sends SIGHUP.
EXIT_SIGNALS = ("SIGTERM", "SIGHUP")

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
FRONTEND_ENTRY = FRONTEND_DIR / "dist" / "entry.js"


def node_available(environ: Mapping[str, str], hermes_home: Path) -> bool:
    """Whether Hermes' launcher will find Node: `HERMES_NODE`, `node` on PATH, or Hermes' own tools.

    Best effort. Hermes resolves Node through its package manager, which can
    also fetch it; a miss here only means `omh` opens Hermes' classic terminal
    instead of a TUI (Hermes' own TUI needs Node as well), never that a launch
    fails.
    """
    explicit = environ.get("HERMES_NODE", "")
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return True
    if shutil.which("node", path=environ.get("PATH")):
        return True
    return any(
        candidate.is_file() and os.access(candidate, os.X_OK)
        for candidate in sorted((hermes_home / "tools").glob("node-*/bin/node"))
    )


def omh_tui_available(
    stdin: TextIO | None, stdout: TextIO | None, environ: Mapping[str, str], hermes_home: Path
) -> bool:
    """Whether bare `omh` should try the OMH TUI before Hermes' own terminal."""
    return not omh_tui_blocker(stdin, stdout, environ, hermes_home)


def omh_tui_blocker(
    stdin: TextIO | None, stdout: TextIO | None, environ: Mapping[str, str], hermes_home: Path
) -> str:
    """Why bare `omh` cannot try the OMH TUI here, in one line, or "" when it can."""
    if not terminal_supported(stdin, stdout, environ):
        return terminal_blocker(stdin, stdout, environ) or "this terminal cannot host the OMH TUI"
    if not FRONTEND_ENTRY.is_file():
        return f"the OMH TUI frontend is missing from this install ({FRONTEND_ENTRY})"
    if not node_available(environ, hermes_home):
        return "no Node found for Hermes' launcher (HERMES_NODE, PATH, or Hermes' tools/node-*)"
    return ""


def omh_tui_environment(
    environ: Mapping[str, str], *, python: str, omh_home: Path, fallback_sentinel: Path
) -> dict[str, str]:
    """The environment `hermes --tui` runs with when it should start the OMH TUI.

    `OMH_HOME` carries the home this `omh` resolved (including `--omh-home` and
    `--scope project`) across the three processes in between, which do not see
    `omh`'s own arguments.
    """
    env = dict(environ)
    env["HERMES_TUI_DIR"] = str(FRONTEND_DIR)
    env["OMH_TUI_PYTHON"] = python
    env["OMH_HOME"] = str(omh_home)
    env[FALLBACK_SENTINEL_ENV] = str(fallback_sentinel)
    return env


@contextmanager
def fallback_sentinel() -> Iterator[Path]:
    """A path where no file is yet, in a new private directory (mode 0700, under the temp directory).

    The directory and whatever is in it are removed when the block exits,
    by return or by exception. A signal ends the process without that
    unless it is turned into an exception first: SIGHUP and SIGTERM are,
    while `exit_on_signals` is in force around this block (bare `omh` keeps
    it in force while the TUI runs). SIGKILL leaves the directory behind.
    """
    directory = Path(tempfile.mkdtemp(prefix="omh-tui-"))
    try:
        yield directory / "fallback"
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _exit_on_signal(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)


@contextmanager
def exit_on_signals() -> Iterator[None]:
    """While the block runs, `EXIT_SIGNALS` raise `SystemExit(128 + signum)`; the handlers they replace come back after.

    Left to their default action they end `omh` at once and no `finally`
    runs. A signal the platform does not have is skipped, and off the main
    thread nothing is installed, as `omh.tui.app` does for the same pair.
    """
    previous: dict[int, object] = {}
    for name in EXIT_SIGNALS:
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous[signum] = signal.signal(signum, _exit_on_signal)
        except ValueError:
            break  # handlers can only be installed from the main thread
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler if handler is not None else signal.SIG_DFL)


def fallback_requested(returncode: int, sentinel: Path) -> bool:
    """Whether the TUI asked `omh` to open Hermes' own terminal: `FALLBACK_EXIT` and the file it was named."""
    return returncode == FALLBACK_EXIT and os.path.lexists(sentinel)


def request_fallback(environ: Mapping[str, str]) -> int:
    """Create the file named in `FALLBACK_SENTINEL_ENV`, if any, and return `FALLBACK_EXIT`.

    The TUI returns `FALLBACK_EXIT` only through this; the frontend shim and
    its import bootstrap create the file the same way before they exit 75.
    When the file cannot be created, `omh` passes the 75 on as an exit code
    instead of opening Hermes' terminal.
    """
    path = environ.get(FALLBACK_SENTINEL_ENV, "")
    if path:
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600))
        except OSError:
            pass
    return FALLBACK_EXIT
