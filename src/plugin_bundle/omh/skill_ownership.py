"""Refuse a Hermes skill edit that would land on an OMH-managed skill.

Hermes' background self-improvement review may patch, edit, or write files
into any skill it loaded, skills under `skills.external_dirs` included; only
delete and archive stay guarded (`tools/skill_manager_guards.py`). An OMH
skill is rendered by the installer and recorded in `<omh_home>/manifest.json`,
so an edit made in place either holds that skill back from every later OMH
update or stays behind in a retired generation Hermes no longer loads.

`pre_tool_call` therefore blocks `skill_manage` edit, patch, write_file and
remove_file when the named skill resolves to one the manifest records, and
the message says where the lesson goes instead: a separately named companion
skill in the profile's own skills directory, or OMH's workflow-learning lane.
Upstream rejected read-only external directories because a bare refusal
breeds silent local duplicates; that pointer is what keeps this refusal from
doing the same.

The hook reaches the background-review fork too. `hermes_cli/plugins.py`
`_get_pre_tool_call_directive_details` checks the fork's thread whitelist and
then invokes `pre_tool_call` for every tool the whitelist admits, and
`skill_manage` is one of them.

Resolution follows `tools/skill_manager_tool.py::_find_skill`, which serves a
bare name from the profile's own skills directory, then `skills.create_dir`,
then the external directories. A name either of the first two also holds
edits that copy, not OMH's, so it passes through here.

Rule: only the installed directory name is a target, never the canonical
catalog name and never a prefix. The installed name is also every OMH
skill's frontmatter `name`, and it is the only one of OMH's names that
`_find_skill` resolves to the managed directory; `skill_manage` on a
canonical name such as `doctor` edits some other skill or nothing, and a
user skill called `omh-notes` is not OMH's. A categorized `category/name`
only resolves inside the local directory, so it never reaches an OMH skill.

Anything this cannot read -- no manifest, an unreadable one, a `config.yaml`
that exists but cannot be read, or a `skills:` block in a shape this scan
does not parse -- lets the call through: the installer and `omh doctor`
still report the edit, and a refusal aimed at a copy OMH does not own would
be the worse error.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path, PurePosixPath
from typing import Final

SKILL_MANAGE_TOOL: Final = "skill_manage"
# The `skill_manage` actions that change a skill's files in place. `create`
# refuses a name that already resolves, and `delete` is guarded upstream.
CONTENT_WRITE_ACTIONS: Final = frozenset({"edit", "patch", "write_file", "remove_file"})
MANIFEST_NAME: Final = "manifest.json"
WORKFLOW_LEARNING_SKILL: Final = "workflow-learning"
# Directory names Hermes' skill scanners never descend into
# (`agent/skill_utils.py` `EXCLUDED_SKILL_DIRS`), and the support directories
# inside a skill package, which hold files rather than skills.
SKILL_SCAN_EXCLUDED_DIRS: Final = frozenset(
    {
        ".git", ".github", ".hub", ".archive", ".curator_backups", ".locks",
        ".venv", "venv", "node_modules", "site-packages", "__pycache__",
        ".tox", ".nox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    }
)
SKILL_SUPPORT_DIRS: Final = frozenset({"references", "templates", "assets", "scripts"})


def skill_directories(root: Path, errors: list[str] | None = None) -> list[Path]:
    """Every directory under `root` holding a SKILL.md, the way Hermes finds skills.

    Symlinked directories are not followed, so an alias of a directory already
    walked is not counted twice. A directory that cannot be listed is skipped
    and, when `errors` is given, named there: "nothing found" and "nothing
    looked at" are different answers.
    """
    found: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except FileNotFoundError:
            continue
        except OSError as error:
            if errors is not None:
                errors.append(f"{directory}: {error}")
            continue
        holds_skill = directory != root and any(child.name == "SKILL.md" and child.is_file() for child in children)
        if holds_skill:
            found.append(directory)
        for child in reversed(children):
            if child.name in SKILL_SCAN_EXCLUDED_DIRS or child.is_symlink() or not child.is_dir():
                continue
            if holds_skill and child.name in SKILL_SUPPORT_DIRS:
                continue
            pending.append(child)
    return sorted(found)


def configured_create_dir(hermes_home: Path) -> tuple[Path | None, bool]:
    """`skills.create_dir` as Hermes resolves it, and whether the config could be read.

    A text scan in the spirit of the bundle's other `config.yaml` readers: only
    a direct child of the top-level `skills:` block counts, the last one wins
    as in YAML, and a null or empty value is unset. Like
    `agent/skill_utils.py::get_skill_create_dir`, `~` and `${VAR}` expand and a
    relative path is anchored at the Hermes home. A missing `config.yaml` is
    an ordinary home with nothing configured; one that exists but cannot be
    read, or an inline `skills:` value, answers "not readable".
    """
    try:
        text = (hermes_home / "config.yaml").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, True
    except (OSError, UnicodeDecodeError):
        return None, False
    raw = ""
    in_skills = False
    child_indent: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace():
            key, _separator, rest = stripped.partition(":")
            in_skills = key.strip() == "skills"
            child_indent = None
            inline = rest.split("#", 1)[0].strip()
            if in_skills and inline not in {"", "{}", "~", "null", "Null", "NULL"}:
                return None, False
            continue
        if not in_skills:
            continue
        indent = len(line) - len(line.lstrip())
        if child_indent is None:
            child_indent = indent
        key, separator, rest = stripped.partition(":")
        if indent == child_indent and separator and key.strip() == "create_dir":
            raw = _config_scalar(rest)
    if not raw:
        return None, True
    path = Path(os.path.expanduser(os.path.expandvars(raw)))
    return (path if path.is_absolute() else hermes_home / path), True


def _config_scalar(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1].strip()
    stripped = stripped.split("#", 1)[0].strip()
    return "" if stripped in {"~", "null", "Null", "NULL"} else stripped


def managed_skill_files(omh_home: str | Path) -> dict[str, tuple[str, Path]]:
    """Installed directory name -> (canonical name, SKILL.md path) for each skill the manifest records."""
    try:
        payload = json.loads((Path(omh_home) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
        return {}
    recorded_dir = payload.get("skills_dir")
    # A manifest without a recorded directory predates generations and can
    # only describe the one pack directory that existed then.
    skills_dir = Path(recorded_dir) if isinstance(recorded_dir, str) and recorded_dir else Path(omh_home) / "skills"
    files: dict[str, tuple[str, Path]] = {}
    for record in payload["skills"]:
        if not isinstance(record, dict):
            continue
        relative = record.get("path")
        if not isinstance(relative, str) or not relative:
            continue
        label = PurePosixPath(relative).parent.name
        if not label:
            continue
        name = record.get("name")
        files[label] = (name if isinstance(name, str) and name else label, skills_dir / relative)
    return files


def skill_manage_targets(tool_input: object) -> list[tuple[str, str]]:
    """(action, name) for every operation one `skill_manage` call carries.

    The advertised shape is an `operations` array whose ops name their own
    target, falling back to a top-level `name`; the older flat shape (one
    top-level `action` and `name`) is still accepted by the tool, so it is
    read too.
    """
    if not isinstance(tool_input, Mapping):
        return []
    default_name = tool_input.get("name")
    operations = tool_input.get("operations")
    if operations is None:
        operations = [tool_input]
    elif isinstance(operations, Mapping):
        operations = [operations]
    elif not isinstance(operations, list):
        return []
    targets: list[tuple[str, str]] = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        action = operation.get("action")
        name = operation.get("name") or default_name
        if isinstance(action, str) and isinstance(name, str) and name.strip():
            targets.append((action.strip(), name.strip()))
    return targets


def managed_skill_edit_directive(
    *, tool_name: object, tool_input: object, omh_home: str, hermes_home: str
) -> dict[str, str] | None:
    """A `block` naming where the lesson goes, when this call edits an OMH-managed skill; else None."""
    if str(tool_name or "") != SKILL_MANAGE_TOOL:
        return None
    writes = [(action, name) for action, name in skill_manage_targets(tool_input) if action in CONTENT_WRITE_ACTIONS]
    if not writes:
        return None
    managed = managed_skill_files(omh_home)
    higher_tier_names: set[str] | None = None
    for action, name in writes:
        entry = managed.get(name)
        if entry is None or not entry[1].is_file():
            continue
        if higher_tier_names is None:
            # The two tiers `_find_skill` searches before the external ones.
            create_dir, readable = configured_create_dir(Path(hermes_home))
            if not readable:
                return None
            roots = [Path(hermes_home) / "skills", *([create_dir] if create_dir is not None else [])]
            higher_tier_names = {directory.name for root in roots for directory in skill_directories(root)}
        if name in higher_tier_names:
            continue
        workflow_learning = next(
            (label for label, (canonical, _path) in managed.items() if canonical == WORKFLOW_LEARNING_SKILL), ""
        )
        return {"action": "block", "message": managed_skill_edit_message(name, action, workflow_learning)}
    return None


def managed_skill_edit_message(label: str, action: str, workflow_learning: str = "") -> str:
    """What the model reads instead of the edit: why it was refused, and where the lesson goes."""
    lane = "OMH's workflow-learning lane"
    if workflow_learning:
        lane += f" (skill_view name='{workflow_learning}')"
    return (
        f"Refused: '{label}' is an oh-my-hermes skill that OMH re-renders from its catalog on every update, "
        f"so a {action} here would hold it back from later updates or be left behind in a retired install. "
        "Keep the lesson in a skill OMH does not manage: skill_manage action=create with a different name, "
        f"such as '{label}-notes', holding a short companion that names '{label}' and the case it adds rather "
        f"than a copy of it (it lands in this profile's own skills directory), or record it through {lane}. "
        f"Do not retry this {action} on '{label}'."
    )


__all__ = [
    "CONTENT_WRITE_ACTIONS",
    "SKILL_MANAGE_TOOL",
    "configured_create_dir",
    "managed_skill_edit_directive",
    "managed_skill_edit_message",
    "managed_skill_files",
    "skill_directories",
    "skill_manage_targets",
]
