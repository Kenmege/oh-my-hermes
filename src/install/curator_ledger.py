"""Hermes' skill audit ledger, read and never written.

Hermes appends one JSONL row to `<hermes_home>/skills/.curator_ledger.jsonl`
for every skill mutation, whoever made it (`tools/skill_ledger.py`): `ts`,
`actor` (`curator` for the background self-improvement review, `agent` for a
model in a session, `user` for the CLI), `action`, the `skill` name the call
used, and `before` / `after` lists of `{path, sha256}`. From `v2026.9.21`
(Hermes 0.21.4) those lists keep only the files the mutation changed
(`_delta`); a row written by 0.21.1-0.21.3 lists every file of the skill,
unchanged ones with the same hash on both sides, so this reader drops those
itself. The content of each listed file is kept content-addressed under
`<hermes_home>/.curator_backups/blobs/<sha256>`. Each bot profile is its own
Hermes home with its own ledger.

It is the record that tells a Hermes edit of an OMH-managed skill apart from a
person's hand edit: the installer keeps the first and still refuses the
second, and `omh doctor` reports every write under an OMH-managed path. The
reader claims little and accepts what it can: a missing ledger is no writes,
a line that does not parse is skipped, and a ledger that cannot be read is
named as unread rather than read as empty.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

from ..hashutil import sha256_file
from .skill_registration import hermes_profile_dirs

CURATOR_LEDGER_RELATIVE_PATH = Path("skills") / ".curator_ledger.jsonl"
# Rows that record state rather than an edit: `pre-rollback` repeats the
# current files on both sides, and `rollback` puts back what an earlier row
# replaced, which for an OMH skill is OMH's own render.
_NOT_AN_EDIT = frozenset({"pre-rollback", "rollback"})


@dataclass(frozen=True)
class LedgerWrite:
    """One file a ledger row says a skill mutation changed, and what it left there."""

    path: str
    # Content hash the row recorded after the mutation; "" when it removed the file.
    sha256: str
    ts: str
    actor: str
    action: str
    skill: str
    ledger: str


def curator_ledger_paths(hermes_home: Path) -> list[Path]:
    """The ledger of the Hermes home and of every bot profile under it."""
    paths = [hermes_home / CURATOR_LEDGER_RELATIVE_PATH]
    paths.extend(profile / CURATOR_LEDGER_RELATIVE_PATH for _name, profile in hermes_profile_dirs(hermes_home))
    return paths


def read_ledger_writes(hermes_home: Path) -> tuple[list[LedgerWrite], list[str]]:
    """Every file write the home's ledgers record, oldest first, and the ledgers that could not be read."""
    writes: list[LedgerWrite] = []
    unread: list[str] = []
    for ledger in curator_ledger_paths(hermes_home):
        try:
            text = ledger.read_bytes().decode("utf-8-sig")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError) as error:
            unread.append(f"{ledger}: {error}")
            continue
        for line in text.splitlines():
            writes.extend(_row_writes(line, str(ledger)))
    # ISO-8601 UTC stamps from one writer sort as text. Stable, so rows of one
    # ledger that share a stamp keep the order they were appended in.
    writes.sort(key=lambda write: write.ts)
    return writes, unread


def _row_writes(line: str, ledger: str) -> list[LedgerWrite]:
    if not line.strip():
        return []
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(row, dict) or str(row.get("action", "")) in _NOT_AN_EDIT:
        return []
    before = _file_states(row.get("before"))
    # Upstream `_delta`, for rows written before Hermes applied it: a file with
    # the same hash on both sides was listed, not changed.
    after = {path: sha for path, sha in _file_states(row.get("after")).items() if before.get(path) != sha}
    removed = [path for path in before if path not in _file_states(row.get("after"))]
    fields = {
        "ts": str(row.get("ts", "") or ""),
        "actor": str(row.get("actor", "") or ""),
        "action": str(row.get("action", "") or ""),
        "skill": str(row.get("skill", "") or ""),
        "ledger": ledger,
    }
    return [LedgerWrite(path=path, sha256=sha, **fields) for path, sha in after.items()] + [
        LedgerWrite(path=path, sha256="", **fields) for path in removed
    ]


def _file_states(items: object) -> dict[str, str]:
    if not isinstance(items, list):
        return {}
    states: dict[str, str] = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"]:
            states[item["path"]] = str(item.get("sha256", "") or "")
    return states


def writes_under(writes: list[LedgerWrite], root: Path) -> list[LedgerWrite]:
    """The writes whose file sits under `root`.

    Hermes records an external skills directory by its resolved path, so the
    root is compared both as given and resolved, and a recorded path is never
    resolved again: one `realpath` per row would cost a full ledger of
    syscalls on every install.
    """
    bases = {os.path.normpath(str(root)), os.path.realpath(str(root))}
    return [write for write in writes if any(_within(os.path.normpath(write.path), base) for base in bases)]


def _within(path: str, base: str) -> bool:
    try:
        return os.path.commonpath([path, base]) == base
    except ValueError:
        # Different drives, or one path absolute and the other not.
        return False


def write_still_on_disk(write: LedgerWrite) -> bool:
    """Whether the file holds what the write left: its content, or its absence."""
    path = Path(write.path)
    if write.sha256:
        return path.is_file() and sha256_file(path) == write.sha256
    return not path.exists()


def newest_write_to(path: Path, writes: list[LedgerWrite]) -> LedgerWrite | None:
    """The newest write the ledgers record for exactly this file, compared as `writes_under` compares."""
    spellings = {os.path.normpath(str(path)), os.path.realpath(str(path))}
    matching = [write for write in writes if os.path.normpath(write.path) in spellings]
    return matching[-1] if matching else None


def ledger_holds_copy(write: LedgerWrite) -> bool:
    """Whether the content this write left is still stored where Hermes keeps it.

    A row's file content lives at `<hermes_home>/.curator_backups/blobs/<sha256>`
    of the home whose ledger recorded it, and Hermes deletes a blob once no row
    references it. A removal left no content to keep.
    """
    if not write.sha256:
        return False
    blob = Path(write.ledger).parent.parent / ".curator_backups" / "blobs" / write.sha256
    try:
        return blob.is_file()
    except OSError:
        return False


def hermes_edit_still_on_disk(skill_dir: Path, writes: list[LedgerWrite]) -> LedgerWrite | None:
    """The newest write under `skill_dir` whose result is still what the disk holds.

    "Still on disk" is the test, not "the ledger names this skill": after a
    `--force` install or a rollback has put OMH's render back, the old row is
    history, and treating it as a live edit would hold the skill back forever.
    """
    for write in reversed(writes_under(writes, skill_dir)):
        if write_still_on_disk(write):
            return write
    return None
