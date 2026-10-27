"""Which files of the `omh` package an `omh update` changed, git `--stat` style.

A staged update installs a fresh generation next to the previous one, and the
previous one stays on disk as the rollback target until garbage collection.
The re-entered update (the new generation's own `omh update`) therefore has
both package trees at hand: it reads the activation marker in
`self-update.json`, which names the previous generation and this one, and
compares the two `site-packages/omh` trees file by file.

Only OMH's own installed package is read, never the user's files, and the
record carries paths and line counts, never content.
"""

from __future__ import annotations

from collections.abc import Mapping
import difflib
import os
from pathlib import Path
from typing import Any

from ..core.errors import OmhError
from ..install.self_update_state import state_path
from ..install.self_update_state_validation import parse_marker, same_existing_path
from ..system.local_store import read_json_object_result
from .language import tr

SCHEMA_VERSION = "update_file_changes/v1"
REENTERED_ENV = "OMH_UPDATE_COMMAND_PACKAGE_REENTERED"
GENERATION_ENV = "OMH_SELF_UPDATE_GENERATION"
ROLLBACK_RESTORE_ENV = "OMH_UPDATE_ROLLBACK_RESTORE"

# The printed stat shows this many files; the payload carries all of them.
ROW_LIMIT = 40
NAME_WIDTH_LIMIT = 50
GRAPH_WIDTH = 30


def _unavailable(reason: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "status": "unavailable", "reason": reason}


def generation_package_dir(generation: Path) -> Path | None:
    """The installed `omh` package inside one generation's venv, if exactly one."""
    venv = generation / "venv"
    found = [*venv.glob("lib/python*/site-packages/omh"), *venv.glob("Lib/site-packages/omh")]
    found = [path for path in found if path.is_dir()]
    return found[0] if len(found) == 1 else None


def _package_files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts and path.suffix != ".pyc"
    }


def _text_lines(data: bytes) -> list[str] | None:
    """Lines split on LF only, as git counts them; None for a binary file."""
    if b"\0" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # Keep each line's LF so a missing final newline differs, as in git.
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _line_counts(before: list[str], after: list[str]) -> tuple[int, int]:
    insertions = deletions = 0
    for tag, old_start, old_end, new_start, new_end in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal":
            deletions += old_end - old_start
            insertions += new_end - new_start
    return insertions, deletions


def package_file_changes(before: Path, after: Path) -> list[dict[str, Any]]:
    """One row per file that differs between two package trees, in path order."""
    old, new = _package_files(before), _package_files(after)
    rows: list[dict[str, Any]] = []
    for relative in sorted(old.keys() | new.keys()):
        old_bytes = old[relative].read_bytes() if relative in old else b""
        new_bytes = new[relative].read_bytes() if relative in new else b""
        if old_bytes == new_bytes:
            continue
        old_lines, new_lines = _text_lines(old_bytes), _text_lines(new_bytes)
        if old_lines is None or new_lines is None:
            rows.append({"path": relative, "binary": True, "insertions": 0, "deletions": 0})
            continue
        insertions, deletions = _line_counts(old_lines, new_lines)
        rows.append({"path": relative, "binary": False, "insertions": insertions, "deletions": deletions})
    return rows


def update_file_changes(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Compare the previous generation's package with this one's.

    Runs only inside the re-entered update of a staged self-update; anything
    else (the candidate smoke run, a rollback restore, a source checkout) is
    `unavailable` with the reason, and prints nothing.
    """
    env = os.environ if environ is None else environ
    if env.get(REENTERED_ENV) != "1" or not env.get(GENERATION_ENV):
        return _unavailable("not_a_staged_update")
    if env.get(ROLLBACK_RESTORE_ENV):
        return _unavailable("rollback_restore")
    current = Path(env[GENERATION_ENV]).expanduser()
    root = current.parent.parent
    state, _error = read_json_object_result(state_path(root))
    if state is None:
        return _unavailable("self_update_state_unreadable")
    try:
        marker = parse_marker(root, state.get("activation_in_progress"))
    except OmhError:
        return _unavailable("activation_marker_invalid")
    if marker is None:
        return _unavailable("activation_marker_missing")
    candidate, previous = marker
    if not same_existing_path(candidate, current):
        return _unavailable("activation_marker_names_another_generation")
    known_good = state.get("previous_known_good")
    if isinstance(known_good, dict) and known_good.get("path") == str(candidate):
        # `omh update --recover-known-good` re-enters the older generation
        # through the same marker; that is a downgrade, not an update.
        return _unavailable("known_good_recovery")
    before, after = generation_package_dir(previous), generation_package_dir(current)
    if before is None or after is None:
        return _unavailable("package_dir_not_found")
    try:
        rows = package_file_changes(before, after)
    except OSError:
        return _unavailable("package_unreadable")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "observed",
        "from_generation": previous.name,
        "to_generation": current.name,
        "files_changed": len(rows),
        "insertions": sum(row["insertions"] for row in rows),
        "deletions": sum(row["deletions"] for row in rows),
        "files": rows,
    }


def _name(path: str) -> str:
    return path if len(path) <= NAME_WIDTH_LIMIT else "..." + path[-(NAME_WIDTH_LIMIT - 3):]


def _scale(count: int, largest: int) -> int:
    return 1 + count * (GRAPH_WIDTH - 1) // largest if count else 0


def _bar(insertions: int, deletions: int, largest: int) -> tuple[int, int]:
    """Graph widths as git's `show_stats`: scale the total, then split it."""
    if largest <= GRAPH_WIDTH:
        return insertions, deletions
    total = _scale(insertions + deletions, largest)
    if total < 2 and insertions and deletions:
        total = 2
    if insertions < deletions:
        plus = _scale(insertions, largest)
        return plus, total - plus
    minus = _scale(deletions, largest)
    return total - minus, minus


def update_file_changes_lines(payload: Mapping[str, Any], *, language: str, color: bool = False) -> list[str]:
    """Title plus a git `--stat` body; empty when nothing was observed."""
    if payload.get("status") != "observed":
        return []
    rows = list(payload.get("files") or [])
    title = tr(language, "file_changes_title")
    if not rows:
        return [title, f"  {tr(language, 'file_changes_none')}"]
    shown = rows[:ROW_LIMIT]
    names = [_name(str(row["path"])) for row in shown]
    name_width = max(len(name) for name in names)
    largest = max(int(row["insertions"]) + int(row["deletions"]) for row in shown)
    count_width = len(str(largest))
    green, red, reset = ("\033[32m", "\033[31m", "\033[0m") if color else ("", "", "")
    lines = [title]
    for name, row in zip(names, shown):
        if row.get("binary"):
            lines.append(f" {name.ljust(name_width)} | {'Bin'.rjust(count_width)}")
            continue
        insertions, deletions = int(row["insertions"]), int(row["deletions"])
        plus, minus = _bar(insertions, deletions, largest)
        graph = (f"{green}{'+' * plus}{reset}" if plus else "") + (f"{red}{'-' * minus}{reset}" if minus else "")
        lines.append(f" {name.ljust(name_width)} | {str(insertions + deletions).rjust(count_width)} {graph}".rstrip())
    if len(rows) > len(shown):
        lines.append(f" {tr(language, 'file_changes_more', count=len(rows) - len(shown))}")
    lines.append(" " + _summary(payload, len(rows), language))
    return lines


def _summary(payload: Mapping[str, Any], files: int, language: str) -> str:
    """git's closing line: singular for one, and a side with no lines is left out."""
    files = int(payload.get("files_changed", files))
    parts = [tr(language, "file_changes_file" if files == 1 else "file_changes_files", count=files)]
    for side in ("insertion", "deletion"):
        count = int(payload.get(f"{side}s", 0))
        if count:
            parts.append(tr(language, f"file_changes_{side}" if count == 1 else f"file_changes_{side}s", count=count))
    return tr(language, "file_changes_separator").join(parts)
