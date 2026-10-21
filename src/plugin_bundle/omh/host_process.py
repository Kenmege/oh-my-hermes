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
bound in charge. The guard catches any failure, as Hermes' own call sites
do, so a Hermes module that fails to load reads the same as no Hermes.

A pid means something only on the machine, and in the pid namespace, that
read it: a Hermes gateway in a container and a CLI on its host can share one
OMH home, and the host has no such pid. So the record also names where the
pid was read (`machine`), and a record from anywhere else is unknown, never
gone. Beside it is the boot the pid was read in (`boot`): on the same
machine another boot means the machine restarted, which ended every process
of the old one.
"""
from __future__ import annotations

import os
from pathlib import Path
import platform
import sys
from typing import Any, Final

# Linux: the machine's own id (systemd's, kept across boots), its id for this boot, and this process's pid namespace
# (a container has its own).
_MACHINE_ID = Path("/etc/machine-id")
_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
_PID_NAMESPACE = "/proc/self/ns/pid"
_PLATFORM: Final = sys.platform
_MACHINE_CHARS = 300


def this_process() -> dict[str, Any] | None:
    """``{"pid", "started", "machine", "boot"}`` for this process, or None when Hermes' probes are not here.

    ``started`` is Hermes' start-time fingerprint, None when it could not be
    read; ``machine`` is where the pid was read (`machine`), and ``boot``
    the boot it was read in (`boot`), None where none is read.
    """
    try:
        from gateway.status import get_process_start_time
    except Exception:
        # Not Hermes (ImportError), or a Hermes whose module fails to load: nothing is recorded either way,
        # and the `subagent_start` observers calling this never raise.
        return None
    pid = os.getpid()
    try:
        started = get_process_start_time(pid)
    except Exception:
        # Hermes' reader answers None itself for every failure it knows; any other is the same unread time.
        started = None
    return {"pid": pid, "started": started if _is_int(started) else None, "machine": machine(), "boot": boot()}


def machine() -> str:
    """Where a pid of this process means what it says.

    On Linux, the machine id (the host name where there is none) and the pid
    namespace: another host has another id, and a container has its own
    namespace. Elsewhere only the platform: a host name there follows the
    network (macOS renames itself on a DHCP or reverse-DNS change, or on a
    Bonjour conflict), so it would turn every record unknown. Two non-Linux
    machines sharing one OMH home are not told apart.
    """
    if not _PLATFORM.startswith("linux"):
        return f"host:{_PLATFORM}"[:_MACHINE_CHARS]
    try:
        identity = _MACHINE_ID.read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        identity = ""
    try:
        namespace = os.readlink(_PID_NAMESPACE)
    except OSError:
        namespace = ""
    return f"linux:{identity or platform.node()}:{namespace}"[:_MACHINE_CHARS]


def boot() -> str | None:
    """This boot of the machine: Linux's boot id, None where none is read (a reboot then cannot be told)."""
    try:
        return _BOOT_ID.read_text(encoding="ascii").strip()[:_MACHINE_CHARS] or None
    except (OSError, ValueError):
        return None


def recorded_process(value: object) -> dict[str, Any] | None:
    """A process in the shape `this_process` writes, or None."""
    if not isinstance(value, dict) or not _is_int(value.get("pid")) or value["pid"] <= 0:
        return None
    started, where, booted = value.get("started"), value.get("machine"), value.get("boot")
    if started is not None and not _is_int(started):
        return None
    if not isinstance(where, str) or not where or len(where) > _MACHINE_CHARS:
        return None
    if booted is not None and (not isinstance(booted, str) or not booted or len(booted) > _MACHINE_CHARS):
        return None
    return {"pid": value["pid"], "started": started, "machine": where, "boot": booted}


def process_alive(value: object) -> bool | None:
    """True when the recorded process still runs, False when it is gone, None when that cannot be told.

    None: nothing recorded, a pid read on another machine or in another pid
    namespace, no Hermes probes, a pid that exists with no start time to
    compare it by, or a probe that raised. Only False says the process
    ended, so only False may hand its helper's work out again: a probe's own
    answer, or another boot of this same machine.
    """
    recorded = recorded_process(value)
    if recorded is None or recorded["machine"] != machine():
        return None
    here = boot()
    if recorded["boot"] is not None and here is not None and recorded["boot"] != here:
        # The machine restarted since the pid was read, which ended every process it ran.
        return False
    try:
        from gateway.status import _pid_exists, get_process_start_time, start_time_fingerprints_match
    except Exception:
        # Hermes' own call sites guard this import the same way (`_owner_liveness`, `_persist_dispatch`).
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
