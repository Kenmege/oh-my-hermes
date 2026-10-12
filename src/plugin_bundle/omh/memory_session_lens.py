"""The memory provider's own confinement decision, published per session for `omh_memory`.

The provider decides what a session may recall when Hermes initializes it:
``platform`` other than ``cli`` is a shared surface, ``agent_context`` outside
the primary agent disables writes (cron, subagents), and the principal context
narrows principal-bound records. A tool call carries none of that -- Hermes
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
_SAFE_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def session_lens_path(omh_home: str | Path, session_id: str) -> Path:
    digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:32]
    return Path(omh_home) / "memory" / "sessions" / f"{digest}.json"


def publish_session_lens(omh_home: str | Path, session_id: str, lens: dict[str, Any], *, reset: bool) -> None:
    """Write the provider's decision; ``reset`` starts a new session's id lists."""
    path = session_lens_path(omh_home, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if reset:
        _prune_stale(path.parent, keep=path)
    with state_file_lock(path):
        current = None if reset else _read(path, session_id)
        lists = {key: list(current.get(key, [])) if current else [] for key in SESSION_LENS_LISTS}
        payload = {
            **lens,
            "schema_version": SESSION_LENS_SCHEMA_VERSION,
            "session_id": str(session_id),
            **lists,
        }
        write_text_atomic(path, json.dumps(payload, sort_keys=True))


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


def withdraw_session_lens(omh_home: str | Path, session_id: str) -> None:
    """Remove the lens under its lock, so a concurrent note cannot write it back."""
    if not session_id:
        return
    path = session_lens_path(omh_home, session_id)
    if not path.parent.is_dir():
        return
    with state_file_lock(path):
        path.unlink(missing_ok=True)


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
    "SESSION_LENS_SCHEMA_VERSION",
    "note_session_records",
    "publish_session_lens",
    "read_session_lens",
    "session_lens_path",
    "withdraw_session_lens",
]
