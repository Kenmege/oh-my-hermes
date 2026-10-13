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

from collections.abc import Iterable
from dataclasses import dataclass
import json
import os
from pathlib import Path

from ..hashutil import sha256_file
from .manifest import same_directory
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


def read_ledger_writes(
    hermes_home: Path, within: Iterable[Path] | None = None, *, report_unparsed_rows: bool = False
) -> tuple[list[LedgerWrite], list[str]]:
    """Every file write the home's ledgers record, oldest first, and the ledgers that could not be read.

    With `within`, only writes under one of those directories are kept, and a
    line that names none of them is skipped before it is parsed: a ledger
    written before Hermes 0.21.4 recorded every file of a skill on every row
    and could grow to hundreds of megabytes.

    A line that is not a ledger row (not JSON, or not an object) is skipped.
    With `report_unparsed_rows` it is also named in the unread list, for a
    caller that must not act on a write it could not read: such a line may
    record one.
    """
    needles = _path_needles(within) if within is not None else None
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
        for number, line in enumerate(text.splitlines(), start=1):
            if needles is not None and not any(needle in line for needle in needles):
                continue
            row_writes = _row_writes(line, str(ledger))
            if row_writes is None:
                if report_unparsed_rows:
                    unread.append(f"{ledger}: line {number} is not a ledger row")
                continue
            writes.extend(row_writes)
    if within is not None:
        roots = list(within)
        writes = [write for write in writes if any(writes_under([write], root) for root in roots)]
    # ISO-8601 UTC stamps from one writer sort as text. Stable, so rows of one
    # ledger that share a stamp keep the order they were appended in.
    writes.sort(key=lambda write: write.ts)
    return writes, unread


def _path_needles(roots: Iterable[Path]) -> set[str]:
    """Each root as it appears inside a JSON string, both as given and resolved."""
    needles: set[str] = set()
    for root in roots:
        for spelling in {os.path.normpath(str(root)), os.path.realpath(str(root))}:
            needles.add(json.dumps(spelling, ensure_ascii=False)[1:-1])
    return needles


def _row_writes(line: str, ledger: str) -> list[LedgerWrite] | None:
    """The writes one ledger line records, or None when the line is not a ledger row."""
    if not line.strip():
        return []
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict):
        return None
    if str(row.get("action", "")) in _NOT_AN_EDIT:
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


def writes_in_skill_dir(skill_dir: Path, writes: list[LedgerWrite]) -> list[tuple[LedgerWrite, str]]:
    """Each write to a file of `skill_dir`, oldest first, with the file's skill-relative POSIX path.

    A row spells a path the way the directory was spelled on disk when Hermes
    wrote it. On a case-insensitive filesystem a skill directory renamed to
    another letter case is still the same directory, so a path that does not
    match as text is matched by asking the filesystem whether one of its
    ancestors IS `skill_dir`.
    """
    bases = (os.path.normpath(str(skill_dir)), os.path.realpath(str(skill_dir)))
    folded_name = skill_dir.name.casefold()
    same_as_skill_dir: dict[str, bool] = {}
    found: list[tuple[LedgerWrite, str]] = []
    for write in writes:
        normalized = os.path.normpath(write.path)
        base = next((candidate for candidate in bases if _within(normalized, candidate)), None)
        if base is None:
            for ancestor in Path(normalized).parents:
                if ancestor.name.casefold() != folded_name:
                    continue
                key = str(ancestor)
                if key not in same_as_skill_dir:
                    same_as_skill_dir[key] = same_directory(ancestor, skill_dir)
                if same_as_skill_dir[key]:
                    base = key
                    break
        if base is not None and normalized != base:
            found.append((write, Path(os.path.relpath(normalized, base)).as_posix()))
    return found


def newest_write_to(skill_dir: Path, relative: str, writes: list[LedgerWrite]) -> LedgerWrite | None:
    """The newest write the ledgers record for one file of the skill, named by its skill-relative path."""
    matching = [write for write, written in writes_in_skill_dir(skill_dir, writes) if written == relative]
    return matching[-1] if matching else None


def ledger_holds_copy(write: LedgerWrite) -> bool:
    """Whether the content this write left is still stored where Hermes keeps it.

    A row's file content lives at `<hermes_home>/.curator_backups/blobs/<sha256>`
    of the home whose ledger recorded it. Hermes 0.21.1-0.21.3 never delete a
    blob; 0.21.4 deletes one no row references when `hermes curator` compacts
    the ledger, and 0.21.5 also when the ledger outgrows its size limit, after
    a grace period. A removal left no content to keep.
    """
    if not write.sha256:
        return False
    blob = Path(write.ledger).parent.parent / ".curator_backups" / "blobs" / write.sha256
    try:
        return blob.is_file()
    except OSError:
        return False


def hermes_edit_still_on_disk(skill_dir: Path, writes: list[LedgerWrite], rendered: set[str]) -> LedgerWrite | None:
    """The newest write under `skill_dir` whose result is still what the disk holds.

    "Still on disk" is the test, not "the ledger names this skill": after a
    `--force` install or a rollback has put OMH's render back, the old row is
    history, and treating it as a live edit would hold the skill back forever.

    A removal counts only for a file OMH renders for the skill (`rendered`,
    skill-relative POSIX paths). A file that is absent and that OMH never
    renders reads the same whether Hermes removed it or never added it -- a
    `write_file` followed by a `remove_file` leaves exactly OMH's render -- so
    counting that absence would keep the skill forever.
    """
    for write, relative in reversed(writes_in_skill_dir(skill_dir, writes)):
        if not write.sha256 and relative not in rendered:
            continue
        if write_still_on_disk(write):
            return write
    return None
