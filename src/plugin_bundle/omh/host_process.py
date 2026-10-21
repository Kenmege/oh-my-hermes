"""Is the Hermes process a helper ran in still there? Hermes' own probes, imported when called.

A helper `delegate_task` starts runs inside the Hermes process that started
it, and a restart ends it without a `subagent_stop`: Hermes fires none for a
child a restart killed; its own recovery of such work is
`tools/async_delegation.py:recover_abandoned_delegations`. So the process is
recorded when the helper starts (`this_process`), and a later process asks
whether that one is still running (`process_alive`).

Both use the probes in Hermes' `gateway.status` that its own recovery uses
(`_owner_liveness` beside it): `_pid_exists`, which never signals the
process (`os.kill(pid, 0)` ends it on Windows), and the start-time
fingerprint with its drift tolerance, so a pid the OS gave to another process
is not read as the recorded one. The import is at call time and guarded
(#1623): outside Hermes, or on a Hermes without these probes, nothing is
recorded and every answer is unknown, which leaves each caller's own time
bound in charge.
"""
from __future__ import annotations

import os
from typing import Any


def this_process() -> dict[str, Any] | None:
    """``{"pid", "started"}`` for this process, or None when Hermes' probes are not here.

    ``started`` is Hermes' start-time fingerprint, None when it could not be read.
    """
    try:
        from gateway.status import get_process_start_time
    except ImportError:
        return None
    pid = os.getpid()
    try:
        started = get_process_start_time(pid)
    except Exception:
        # Hermes' reader answers None itself for every failure it knows; any other is the same unread time.
        started = None
    return {"pid": pid, "started": started if _is_int(started) else None}


def recorded_process(value: object) -> dict[str, Any] | None:
    """A process in the shape `this_process` writes, or None."""
    if not isinstance(value, dict) or not _is_int(value.get("pid")) or value["pid"] <= 0:
        return None
    started = value.get("started")
    if started is not None and not _is_int(started):
        return None
    return {"pid": value["pid"], "started": started}


def process_alive(value: object) -> bool | None:
    """True when the recorded process still runs, False when it is gone, None when that cannot be told.

    None: nothing recorded, no Hermes probes, a pid that exists with no start
    time to compare it by, or a probe that raised. Only False says the
    process ended, so only False may hand its helper's work out again.
    """
    recorded = recorded_process(value)
    if recorded is None:
        return None
    try:
        from gateway.status import _pid_exists, get_process_start_time, start_time_fingerprints_match
    except ImportError:
        return None
    try:
        if not _pid_exists(recorded["pid"]):
            return False
        current = get_process_start_time(recorded["pid"])
        if recorded["started"] is None or not _is_int(current):
            return None
        # A pid that exists under another start time is another process: the recorded one ended.
        return bool(start_time_fingerprints_match(recorded["started"], current))
    except Exception:
        # A probe that raised has not said the process ended.
        return None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
