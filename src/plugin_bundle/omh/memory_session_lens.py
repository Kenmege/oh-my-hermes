"""The memory provider's own confinement decision, published per session for `omh_memory`.

The provider decides what a session may recall when Hermes initializes it,
as one surface class (`memory_surface_class`): an operator console reads and
writes, a shared surface reads only principal-bound memory and does not
capture, a background run writes nothing. The principal context narrows
principal-bound records. A tool call carries none of that -- Hermes
hands plugin tools ``task_id``, ``session_id`` and ``user_task`` only -- and
the session context Hermes binds per task disagrees with the provider's
``platform`` for cron, the TUI and subagents. So the tool does not re-derive
the decision; the provider writes it here and the tool reads it back by the
``session_id`` both of them receive.

A file under ``<omh_home>/memory/sessions/`` rather than a module global:
Hermes loads the provider package under its memory-provider module name and
the tool package under its plugin module name, and may run a user provider in
a separate plugin host process, so a global in one copy is invisible to the
other. A missing, unreadable or foreign lens file reads as ``None`` and the
tool fails closed on it (shared surface, no writes).

The same file carries the record ids this session was served or recalled and
the ids it confirmed: ``retire`` of a live record and the one-use-per-session
``confirm`` rule read them. Stdlib only; no import of the ``omh`` control plane.

One session id can have more than one provider. Hermes builds short-lived
agents under a live gateway session's id -- the session-hygiene agent
(`gateway/run_turn.py`) and the `/compress` agent
(`gateway/slash_commands_session.py`) -- and, only with
`compression.checkpoint_required` on, loads the provider into them as
`platform="cli"`. So every lens names the provider ``instance`` that wrote it.

What that guarantees: a provider publishing over a live lens another instance
wrote may only tighten it (a looser one adopts the lens it found and writes
nothing), a provider withdraws only a lens its own instance wrote, and a lens
whose owner stopped refreshing it for `SESSION_LENS_STALE_HOURS` may be
replaced by a looser successor at that successor's start. What it cannot
guarantee: when the looser provider publishes first, the tool reads its lens
until the tighter one publishes. That ordering is Hermes' and cannot be closed
here; the provider re-reads the lens before every render and pre-compress
hand-off, so it narrows to the turn in which the tighter provider arrives.
A lens an earlier OMH release wrote names no instance: it counts as another
instance's for widening, and only the same session's console provider may
withdraw it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .memory_state_files import state_file_lock, write_text_atomic

SESSION_LENS_SCHEMA_VERSION = "omh_memory_session_lens/v1"
SESSION_LENS_LISTS = ("seen", "confirmed")
# A session sees at most a handful of records per turn; the cap only stops a
# long-lived gateway session from growing the file without bound.
SESSION_LENS_LIST_LIMIT = 256
# Lens files of sessions that died without `shutdown` are pruned at the next
# session start once this old.
SESSION_LENS_MAX_AGE_DAYS = 30
# A live owner refreshes its lens at every turn start. One unrefreshed for this
# long is presumed left by a process that died: a looser successor may replace
# it at its own start instead of adopting it. A process probe would not do --
# on Windows `os.kill(pid, 0)` sends CTRL_C -- and an owner still alive takes
# the lens back at its next turn start, because tightening is always allowed.
SESSION_LENS_STALE_HOURS = 12
_SAFE_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")

# Surface classes (owner decision 2026-10-13). The acting-principal boundary
# (2ab7da2ef) exists because nobody authenticates who wrote each turn of a
# multi-party chat, so identity-unbound memory stays out of one. An operator
# console is not that: Hermes' `cli` REPL, its `tui` and its `desktop` chat
# panel reach the agent through an OS login, a dashboard login or a Desktop
# token, each of which already grants full control of the agent, so reading
# the operator's memory there discloses nothing that access did not. That
# includes a remote Desktop or dashboard session. Every other platform --
# each gateway, `api_server`, `webhook`, `relay`, `acp`, an empty value and
# anything unknown -- is shared, which fails closed. A non-primary
# `agent_context` (Hermes passes `cron` for the scheduler and `subagent` for
# `delegate_task`, `agent/agent_init.py`) is background: it has no user to
# remember for.
SURFACE_OPERATOR_CONSOLE = "operator_console"
SURFACE_SHARED = "shared"
SURFACE_BACKGROUND = "background"
SURFACE_CLASSES = (SURFACE_OPERATOR_CONSOLE, SURFACE_SHARED, SURFACE_BACKGROUND)
OPERATOR_CONSOLE_PLATFORMS = frozenset({"cli", "tui", "desktop"})
PRIMARY_AGENT_CONTEXTS = frozenset({"", "primary"})
# What each class means for the two flags every consumer already reads:
# (shared_surface, writes_enabled). A background run reads like a shared
# surface, as cron and subagent sessions always have.
_SURFACE_FLAGS = {
    SURFACE_OPERATOR_CONSOLE: (False, True),
    SURFACE_SHARED: (True, True),
    SURFACE_BACKGROUND: (True, False),
}


def memory_surface_class(*, platform: object, agent_context: object, shared_surface: object = False) -> str:
    """The class of the surface Hermes initialized a provider for.

    ``shared_surface`` is a caller's request to treat the session as shared:
    it can turn a console into a shared surface, never the reverse, and it
    never lifts a background run's write ban.
    """
    if str(agent_context or "") not in PRIMARY_AGENT_CONTEXTS:
        return SURFACE_BACKGROUND
    if shared_surface or str(platform or "") not in OPERATOR_CONSOLE_PLATFORMS:
        return SURFACE_SHARED
    return SURFACE_OPERATOR_CONSOLE


# How much each class withholds; a lens may move only up this order while
# another instance's lens is live.
_TIGHTNESS = {SURFACE_OPERATOR_CONSOLE: 0, SURFACE_SHARED: 1, SURFACE_BACKGROUND: 2}


def tighter_surface_class(first: str, second: str) -> str:
    """The class that withholds more; an unknown class counts as background."""
    rank = {name: _TIGHTNESS.get(name, _TIGHTNESS[SURFACE_BACKGROUND]) for name in (first, second)}
    return first if rank[first] >= rank[second] else second


def surface_flags(surface_class: str) -> tuple[bool, bool]:
    """``(shared_surface, writes_enabled)`` for a class; an unknown class reads as background."""
    return _SURFACE_FLAGS.get(surface_class, _SURFACE_FLAGS[SURFACE_BACKGROUND])


def lens_surface_class(lens: dict[str, Any]) -> str:
    """The class a read lens publishes; one an earlier provider wrote carries only its two flags."""
    published = lens.get("surface_class")
    if published in SURFACE_CLASSES:
        return str(published)
    if not lens.get("writes_enabled"):
        return SURFACE_BACKGROUND
    return SURFACE_SHARED if lens.get("shared_surface") else SURFACE_OPERATOR_CONSOLE


def session_lens_path(omh_home: str | Path, session_id: str) -> Path:
    digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:32]
    return Path(omh_home) / "memory" / "sessions" / f"{digest}.json"


def publish_session_lens(
    omh_home: str | Path,
    session_id: str,
    lens: dict[str, Any],
    *,
    reset: bool,
    instance: str = "",
    stale_before: datetime | None = None,
) -> dict[str, Any]:
    """Write the provider's decision and return the lens now on disk.

    ``reset`` starts a new session's id lists. Over a live lens written by
    another ``instance``, a lens that would widen it is not written: the lens
    already there is returned, for the caller to adopt. One that is as tight or
    tighter is written and carries the id lists, because a shared session id is
    the same conversation. With ``stale_before``, a tighter foreign lens last
    published before it counts as abandoned and is replaced.
    """
    path = session_lens_path(omh_home, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if reset:
        _prune_stale(path.parent, keep=path)
    with state_file_lock(path):
        current = _read(path, session_id)
        foreign = current is not None and current.get("instance") != instance
        if foreign and tighter_surface_class(lens_surface_class(current), lens_surface_class(lens)) != lens_surface_class(lens):
            if stale_before is None or not _published_before(current, stale_before):
                return current
        keep = current is not None and (foreign or not reset)
        lists = {key: list(current.get(key, [])) if keep else [] for key in SESSION_LENS_LISTS}
        payload = {
            **lens,
            "schema_version": SESSION_LENS_SCHEMA_VERSION,
            "session_id": str(session_id),
            "instance": str(instance),
            **lists,
        }
        write_text_atomic(path, json.dumps(payload, sort_keys=True))
        return payload


def read_session_lens(omh_home: str | Path, session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    return _read(session_lens_path(omh_home, session_id), session_id)


def note_session_records(omh_home: str | Path, session_id: str, key: str, record_ids: list[str] | tuple[str, ...]) -> list[str]:
    """Add ids to one of the session's lists; return the ids that were new. No lens, no write."""
    if key not in SESSION_LENS_LISTS or not session_id:
        return []
    path = session_lens_path(omh_home, session_id)
    if not path.is_file():
        return []
    with state_file_lock(path):
        current = _read(path, session_id)
        if current is None:
            return []
        known = [str(item) for item in current.get(key, [])]
        added = [record_id for record_id in dict.fromkeys(record_ids) if _SAFE_REF.fullmatch(str(record_id)) and record_id not in known]
        if added:
            current[key] = (known + added)[-SESSION_LENS_LIST_LIMIT:]
            write_text_atomic(path, json.dumps(current, sort_keys=True))
    return added


def withdraw_session_lens(
    omh_home: str | Path, session_id: str, *, instance: str | None = None, unowned: bool = False
) -> None:
    """Remove the lens under its lock, so a concurrent note cannot write it back.

    With ``instance``, only a lens that instance wrote is removed: a provider
    that adopted another's lens, or was overtaken by a tighter one, leaves it.
    ``unowned`` also removes a lens that names no instance (an earlier release
    wrote it); the provider passes it only for the session's console provider.
    """
    if not session_id:
        return
    path = session_lens_path(omh_home, session_id)
    if not path.parent.is_dir():
        return
    with state_file_lock(path):
        owner = _instance_of(path)
        if instance is not None and owner != instance and not (unowned and owner == ""):
            return
        path.unlink(missing_ok=True)


def _instance_of(path: Path) -> str | None:
    """The ``instance`` a lens file names, read raw; "" when it names none; None when unreadable."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("instance", "")
    return value if isinstance(value, str) else None


def _published_before(lens: dict[str, Any], cutoff: datetime) -> bool:
    """True when the lens names a publish time earlier than ``cutoff``; an unreadable time is never stale."""
    stamp = lens.get("published_at")
    if not isinstance(stamp, str):
        return False
    try:
        published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if published.tzinfo is None:
        return False
    return published < cutoff


def _read(path: Path, session_id: str) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SESSION_LENS_SCHEMA_VERSION:
        return None
    if data.get("session_id") != str(session_id):
        return None
    if not isinstance(data.get("shared_surface"), bool) or not isinstance(data.get("writes_enabled"), bool):
        return None
    if "surface_class" in data and (
        data["surface_class"] not in SURFACE_CLASSES
        or _SURFACE_FLAGS[data["surface_class"]] != (data["shared_surface"], data["writes_enabled"])
    ):
        # A class its own flags contradict was not written by the provider.
        return None
    homes = data.get("homes")
    if not isinstance(homes, list) or not homes or not all(isinstance(home, str) and Path(home).is_absolute() for home in homes):
        return None
    for key in SESSION_LENS_LISTS:
        if not isinstance(data.get(key), list):
            return None
    return data


def _prune_stale(directory: Path, *, keep: Path) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SESSION_LENS_MAX_AGE_DAYS)).timestamp()
    keep_lock = keep.with_name(f".{keep.name}.lock")
    for path in (*directory.glob("*.json"), *directory.glob(".*.json.lock")):
        try:
            if path not in (keep, keep_lock) and not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


__all__ = [
    "OPERATOR_CONSOLE_PLATFORMS",
    "PRIMARY_AGENT_CONTEXTS",
    "SESSION_LENS_SCHEMA_VERSION",
    "SESSION_LENS_STALE_HOURS",
    "SURFACE_BACKGROUND",
    "SURFACE_CLASSES",
    "SURFACE_OPERATOR_CONSOLE",
    "SURFACE_SHARED",
    "lens_surface_class",
    "memory_surface_class",
    "note_session_records",
    "publish_session_lens",
    "read_session_lens",
    "session_lens_path",
    "surface_flags",
    "tighter_surface_class",
    "withdraw_session_lens",
]
