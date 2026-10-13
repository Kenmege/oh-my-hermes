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

When the TUI cannot start it exits with `FALLBACK_EXIT`, and `omh` opens
Hermes' own terminal instead, so a failure costs the person the OMH surface,
never the session. Every other exit code is the person's session ending and is
returned as it is.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import shutil
from typing import TextIO

from .terminal import terminal_supported

# EX_TEMPFAIL. Not 0/1/2/130 (ordinary exits) and not 42, which Hermes'
# launcher reads as "relaunch as `hermes update`".
FALLBACK_EXIT = 75

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
FRONTEND_ENTRY = FRONTEND_DIR / "dist" / "entry.js"


def node_available(environ: Mapping[str, str], hermes_home: Path) -> bool:
    """Whether Hermes' launcher will find Node: `HERMES_NODE`, `node` on PATH, or Hermes' own tools.

    Best effort. Hermes resolves Node through its package manager, which can
    also fetch it; a miss here only means `omh` opens Hermes' terminal instead
    of the OMH TUI, never that a launch fails.
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
    return (
        terminal_supported(stdin, stdout, environ)
        and FRONTEND_ENTRY.is_file()
        and node_available(environ, hermes_home)
    )


def omh_tui_environment(environ: Mapping[str, str], *, python: str, omh_home: Path) -> dict[str, str]:
    """The environment `hermes --tui` runs with when it should start the OMH TUI.

    `OMH_HOME` carries the home this `omh` resolved (including `--omh-home` and
    `--scope project`) across the three processes in between, which do not see
    `omh`'s own arguments.
    """
    env = dict(environ)
    env["HERMES_TUI_DIR"] = str(FRONTEND_DIR)
    env["OMH_TUI_PYTHON"] = python
    env["OMH_HOME"] = str(omh_home)
    return env
