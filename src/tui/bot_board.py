"""`/bots` and Ctrl+S: this machine's Hermes bots on one read-only board, full screen in the pager.

The board lists the Hermes home this TUI runs under, resolved as the input
history resolves it (`history.history_path`), and the root's every named
profile (`<root>/profiles/<name>/` that carries one of the files Hermes reads
as a profile's identity and has no deletion tombstone). It has three levels,
Enter going down one and ← or Esc up one:

1. the profiles, under a header of what they add up to (gateways up, agents
   running, the active sessions' tokens and cost), each in a rounded box
   (`bot_box`, two to a row from `BOX_MIN` up): its gateway's mark, its name
   (and Hermes' `display_name` from `profile.yaml`) and, while it runs, its
   uptime, agents and resident memory in the top edge; its platforms as chips
   and a requested restart in the bottom edge; inside, a face drawn from the
   name (`avatar`) beside four labelled rows: its configured default model
   (`config.yaml`) and the model its newest session ran when it differs; the
   last 24 hours (tokens by the hour each active session was last active as a
   sparkline, how many were active, how many are stored); those sessions'
   tokens and cost; and what it remembers (Hermes' `memories/MEMORY.md` and
   `USER.md` as gauges against their configured limits, and the record count of
   the OMH store its `config.yaml` names). Box edges, faces, gauges, the
   sparkline and the chips take the delegation view's charset names
   (`unicode`, or `ascii`); a narrow row gives up its least important pieces
   (`Piece` drop ranks) and never wraps;
2. one profile's sessions, under its gateway record in full (pid, record
   age, each platform's state and error) and its exact memory and per-source
   session counts, newest activity first, as a table under one band
   per row: title, model, tokens, cost, source, last activity and message
   count, each column as wide as its widest cell, numbers right-aligned, and
   `SESSION_DROPS` given up in order when the title would run short.
   Delegated children (`source = 'subagent'`) are counted on level 1 and not
   listed;
3. one session's last `MESSAGE_ROWS_READ` messages the model still sees,
   oldest of them first: time, role and the start of the text (one row for a
   tool result, a few for the talk), under its model, tokens and cost.

Tokens and cost are the totals each session's row records, so the active
sessions' sum is what those sessions spent in their lifetime, not in the last
24 hours alone: Hermes keeps no per-call time for them. A cost is summed only
over sessions Hermes priced (`cost_status` actual or estimated, `~` when any
is an estimate), and the sessions it could not price are counted beside it,
never as $0. Every column is optional: a store without one shows less, never a
number it did not read.

"Active in the last 24 hours" is what the board counts instead of open
sessions: a messaging gateway leaves `ended_at` unset on every chat it
served, so a Slack bot idle for weeks still reads as dozens of open sessions.
A session's activity is `last_activity_at`, else its start.

A gateway reads as running only when the process its record names is still
the one that wrote it. The pid is probed with signal 0 and nothing else, its
start-time fingerprint read as Hermes reads it (`/proc/<pid>/stat` field 22
on Linux, the kernel's start time in centiseconds on macOS, which is psutil's
`create_time() * 100`) and compared within Hermes' drift tolerance; a zombie,
or a recorded start time the process's own cannot be compared with, is not
running; and the record must name the home it was read from. A platform's state counts as now
only when the entry was stamped by that same process (`writer_pid` and
`writer_start_time`, Hermes' own ownership rule); otherwise it shows as what
was last recorded. A profile whose own gateway is not running but which the
default home's running gateway lists in `served_profiles` reads as served
by it.

Everything is read off the UI thread (`BoardReader`), every `BOARD_REFRESH_SECONDS`
and at once when the person moves, so a locked or huge `state.db` never holds a
frame. Each `state.db` is opened read-only (`connect_readonly`, a quarter-second
busy wait), only when it exists, and immutable when no connection has it open
(`_no_connection_open`). Only the sessions table is read whole, a row per session;
messages are read by the session's index with a `LIMIT`, their text cut in the
query. `config.yaml`, `profile.yaml` and the memory files are read only when regular files,
at most `CONFIG_BYTES` and `MEMORY_FILE_BYTES` of them (a bigger one is sized,
not read), `config.yaml` through OMH's block-YAML scan
(`runtime_paths.config_scalar_text`); the OMH store's records are counted
from its directory listing, never opened. A live gateway's resident memory
comes from `/proc/<pid>/statm` on Linux and `proc_pidinfo` on macOS, which
answers only for this user's processes. Nothing here writes a file, opens a
socket, calls the gateway, or signals
a process beyond that probe; a read of a store a connection has open takes its
read mark in that store's existing `-shm`, as every reader of it does. Every text
the board shows from those files is a bot's or a user's and is cleaned as
gateway text is (`markdown.clean_text`), whitespace folded to one line.

Keys: ↑↓ (j / k) move, PageUp / PageDown a page, g / G either end, Enter or →
open, ← / Esc / q back (on level 1, close), r read again now. On level 3 the
arrows scroll the messages, which open at the newest.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import sys
import threading
import time
from typing import Any, ClassVar

from ..plugin_bundle.omh import runtime_paths
from ..plugin_bundle.omh.hud_text import compact_count
from ..plugin_bundle.omh.sqlite_readonly import connect_readonly
from .history import history_path
from .launch_card import _pad
from .keys import CHAR, DOWN, END, ENTER, ESCAPE, HOME, LEFT, PAGE_DOWN, PAGE_UP, RIGHT, UP, Key, Paste
from .markdown import clean_text
from .pager import Pager
from .prompts import CHOICE_MARK
from .session_picker import age_text
from .style import Line, Span, Style
from .theme import EDGE_CONTRAST, TEXT_CONTRAST, Theme, contrast_ratio
from .width import cell_width, line_width, truncate, truncate_line, wrap_line

BOARD_TITLE = "Hermes bots"
BOARD_KEYS = "↑↓ move  Enter open  ← back  r refresh  q close"
MESSAGE_KEYS = "↑↓ scroll  g/G ends  ← back  r refresh  q close"
BOARD_REFRESH_SECONDS = 5.0
ACTIVE_WINDOW_SECONDS = 24 * 3600
ACTIVE_HOURS = 24
DEFAULT_PROFILE = "default"
SUBAGENT_SOURCE = "subagent"
PROFILE_CAP = 64
SESSION_ROWS_READ = 200
MESSAGE_ROWS_READ = 40
MESSAGE_CHARS_READ = 2000  # cut in the query: a tool result can be megabytes
TOOL_CALLS_CHARS_READ = 4000
MESSAGE_SHOWN_CHARS = 400
MESSAGE_SHOWN_ROWS = 3
GATEWAY_STATE_FILE = "gateway_state.json"
GATEWAY_STATE_BYTES = 1 << 20
STATE_DB = "state.db"
STATE_DB_BUSY_SECONDS = 0.25
# The files Hermes reads as a profile's identity (`hermes_constants._PROFILE_IDENTITY_MARKERS`).
PROFILE_MARKERS = ("config.yaml", ".env", "SOUL.md", "profile.yaml", "auth.json", "state.db")
DELETED_PROFILES = ".deleted"  # where Hermes keeps a deleted profile's tombstone, beside the profile dirs
# Hermes' `START_TIME_DRIFT_TOLERANCE`: 2 s on either fingerprint scale (Linux ticks, macOS centiseconds).
START_TIME_TOLERANCE = 200
PID_LIMIT = 1 << 31
# Signal 0 is a probe only on POSIX: on Windows `os.kill(pid, 0)` sends CTRL_C_EVENT to the console group.
ON_POSIX = os.name == "posix"
# sysctl names and `kinfo_proc` layout on macOS (sys/sysctl.h, sys/proc.h), measured against psutil on arm64.
CTL_KERN, KERN_PROC, KERN_PROC_PID = 1, 14, 1
KINFO_PROC_BYTES = 1024  # room for the struct (648 bytes on arm64 and x86_64)
KINFO_STAT_OFFSET = 36  # `kp_proc.p_stat`, after the start-time union, two pointers and `p_flag`
SZOMB = 5
# `proc_pidinfo(PROC_PIDTASKINFO)` on macOS (sys/proc_info.h): `pti_resident_size` is the struct's second field.
PROC_PIDTASKINFO = 4
TASK_INFO_BYTES = 96
EPOCH_PLAUSIBLE = 946_684_800  # 2000-01-01: Hermes' floor for an epoch `updated_at` (`normalize_updated_at`)
CONFIG_FILE = "config.yaml"
PROFILE_META_FILE = "profile.yaml"  # Hermes' presentation metadata (`hermes_cli.profiles.read_profile_meta`)
CONFIG_BYTES = 1 << 20
# Hermes' built-in memory (`tools/memory_tool_store.py`): `<home>/memories/`, one file per target, entries joined by
# its `ENTRY_DELIMITER`, each target capped in characters by `memory.<limit key>` in config.yaml.
MEMORY_DIR = "memories"
MEMORY_TARGETS = (
    ("memory", "MEMORY.md", "memory_char_limit", "memory_enabled"),
    ("user", "USER.md", "user_char_limit", "user_profile_enabled"),
)
MEMORY_ENTRY_DELIMITER = "\n§\n"
MEMORY_FILE_BYTES = 1 << 20  # hundreds of times any limit: a bigger file is drift, sized and not read
YAML_FALSE = frozenset({"false", "no", "off", "0"})
OMH_RECORDS_CAP = 10_000
# `cost_status` values that carry a price (Hermes' `CostStatus`: actual, estimated, included, unknown).
PRICED_STATUSES = ("actual", "estimated")
INCLUDED_STATUS = "included"
# Level 2's table: its columns in order, the right-aligned ones, and what a narrow terminal gives up first.
SESSION_COLUMNS = ("model", "input", "output", "cost", "source", "age", "messages")
SESSION_RIGHT = frozenset({"input", "output", "cost", "age", "messages"})
SESSION_DROPS = (("source",), ("cost",), ("input", "output"))
SESSION_GAP = "  "
SESSION_TITLE_MIN = 16
# Level 1's rows: the label column, the gaps a row's pieces keep, and its glyphs in each of the delegation view's
# charsets (`delegation_view.CHARSETS`).
LABEL_WIDTH = 8
FIT_GAP = 3  # the least gap before a row's right-pushed part
SPARK_GAP = "   "
MEMORY_GAP = "  "
GAUGE_CELLS = 10
GAUGE_WARN, GAUGE_ERROR = 70, 90  # ok below 70 %, warn through 90 %, error past it
SPARK_GLYPHS = {"unicode": "▁▂▃▄▅▆▇█", "ascii": "_.,:-=+*#"}
# A bot's box: the least width that takes two side by side, the gap between them, and its edges in each charset
# (the launch card's rounded corners, or `+` `-` `|`); every box holds the same rows, so a row of boxes is even.
BOX_MIN = 72
BOX_GAP = "  "
BOX_PAD = 1
BOX_BODY_ROWS = 4
BOX_GLYPHS = {"unicode": ("╭", "╮", "╰", "╯", "─", "│"), "ascii": ("+", "+", "+", "+", "-", "|")}
# A bot's face: rows and cells, the gap after it, the narrowest box inside that still takes one, and its pixels in
# each charset (none, top, bottom, both).
AVATAR_ROWS, AVATAR_COLS = BOX_BODY_ROWS, 8
AVATAR_GAP = "  "
AVATAR_MIN_INNER = 44
AVATAR_GLYPHS = {"unicode": (" ", "▀", "▄", "█"), "ascii": (" ", "'", ",", "#")}
GAUGE_GLYPHS = {"unicode": ("▰", "▱"), "ascii": ("#", ".")}
CHIP_GLYPHS = {"unicode": ("●", "○"), "ascii": ("*", "o")}
GATEWAY_MARKS = {"unicode": {}, "ascii": {"●": "*", "○": "o", "◌": "?"}}  # a gateway's mark in a box's top edge  # a platform as its gateway runs it now, or as last recorded

GATEWAY_RUNNING = "running"
GATEWAY_STOPPED = "stopped"


@dataclass(frozen=True)
class Platform:
    name: str
    state: str
    error: str = ""
    needs_attention: bool = False
    current: bool = False  # stamped by the gateway process that runs now


@dataclass(frozen=True)
class Gateway:
    state: str  # the record's `gateway_state`; "" with no readable record
    live: bool  # its process runs and is the one that wrote the record
    pid: int | None = None
    active_agents: int = 0
    exit_reason: str = ""
    elsewhere: str = ""  # the other home the record names; its pid means nothing here
    served: tuple[str, ...] = ()
    platforms: tuple[Platform, ...] = ()
    problem: str = ""  # why there is no record to read: missing, or unreadable
    started: float | None = None  # when its process started (epoch seconds), from the record; only while live
    updated: float | None = None  # the record's `updated_at` (epoch seconds)
    restart_requested: bool = False
    rss: int | None = None  # its live process's resident bytes, where the OS tells without a signal


@dataclass(frozen=True)
class Usage:
    """Tokens and cost summed over sessions, as their rows in `state.db` record them."""

    input: int = 0
    output: int = 0
    cache_read: int | None = None  # None when the store records no cache reads
    cost: float | None = None  # over the sessions Hermes priced (`PRICED_STATUSES`); None when none was
    estimated: bool = False  # some of `cost` is Hermes' estimate
    included: int = 0  # sessions Hermes recorded as included in a plan
    unpriced: int = 0  # sessions with tokens and no price


@dataclass(frozen=True)
class MemoryFile:
    """One of Hermes' built-in memory targets, as its file and config.yaml say."""

    target: str  # "memory" or "user"
    entries: int = 0
    chars: int = 0
    limit: int | None = None  # its configured character limit; None when config.yaml does not say
    enabled: bool = True
    problem: str = ""  # why it could not be counted: unreadable, or too large to read (with its size)


@dataclass(frozen=True)
class Profile:
    name: str
    home: Path
    gateway: Gateway
    sources: tuple[tuple[str, int], ...] = ()  # session counts by source, most first
    active: int = 0  # sessions with activity in the last 24 hours
    last_activity: float | None = None
    store: str = ""  # why the sessions could not be read; "" when they were
    this_tui: bool = False
    display: str = ""  # Hermes' presentation-only `display_name` (`profile.yaml`); "" when it sets none
    model: str = ""  # the configured default model (`model.default` in config.yaml)
    provider: str = ""
    config: str = ""  # why config.yaml could not be read; "" when it was
    ran: str = ""  # the model its newest session ran
    usage: Usage | None = None  # the active sessions' tokens; None when the store records none
    # Those tokens by the hour each session was last active, oldest of the `ACTIVE_HOURS` first; () without columns.
    hourly: tuple[int, ...] = ()
    memory: tuple[MemoryFile, ...] = ()
    omh_records: int | None = None  # record files in the OMH store config.yaml names; None when it names none
    omh: str = ""  # why that store's records could not be counted


@dataclass(frozen=True)
class SessionRow:
    id: str
    title: str
    source: str
    last_activity: float
    message_count: int
    model: str = ""
    usage: Usage | None = None


@dataclass(frozen=True)
class MessageRow:
    role: str
    text: str
    at: float


@dataclass(frozen=True)
class BoardView:
    """What the board shows beyond the profiles: one profile's sessions, and one of its sessions' messages."""

    profile: str = ""
    session: str = ""


@dataclass(frozen=True)
class BoardSnapshot:
    view: BoardView
    profiles: tuple[Profile, ...]
    sessions: tuple[SessionRow, ...] = ()  # one profile's, or, for a session's messages, that session's row
    unlisted: int = 0  # subagent sessions the profile has, not listed
    messages: tuple[MessageRow, ...] = ()
    problem: str = ""  # why the view's sessions or messages could not be read
    read_at: float = 0.0


# ── where the homes are ───────────────────────────────────────────────────


def board_homes(environ: Mapping[str, str]) -> tuple[Path | None, list[tuple[str, Path]]]:
    """This TUI's Hermes home and every home on the board: the root (`default`), then each named profile by name."""
    history = history_path(environ)
    if history is None:
        return None, []
    current = history.parent
    root = current.parent.parent if current.parent.name == "profiles" else current
    homes = [(DEFAULT_PROFILE, root)]
    try:
        entries = sorted((root / "profiles").iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if len(homes) > PROFILE_CAP:
            break
        try:
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            if (entry.parent / DELETED_PROFILES / entry.name).exists():
                continue
            if not any((entry / marker).is_file() for marker in PROFILE_MARKERS):
                continue
        except OSError:
            continue
        homes.append((entry.name, entry))
    return current, homes


def _same_path(first: Path, second: Path) -> bool:
    try:
        return first.resolve() == second.resolve()
    except (OSError, RuntimeError):
        return first == second


# ── the gateway record ────────────────────────────────────────────────────


def _pid(value: Any) -> int | None:
    # Never below 2 (signal 0 to 0 or -1 probes a process group or every process, and 1 is init), nor past a pid_t.
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 < value < PID_LIMIT else None


def _fingerprint_value(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value)


def pid_exists(pid: int) -> bool:
    """Whether a process has `pid`: signal 0, which delivers nothing. Never called off POSIX."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # another user's process: it exists
    except OSError:
        return False
    return True


def process_facts(pid: int) -> tuple[int | None, bool]:
    """The process's start time on Hermes' fingerprint scale (None where it cannot be read), and whether it is a
    zombie, which Hermes' own `_pid_exists` counts as dead: it can no longer be a running gateway."""
    if sys.platform.startswith("linux"):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            return int(fields[19]), fields[0] == "Z"
        except (OSError, IndexError, ValueError):
            return None, False
    if sys.platform == "darwin":
        return _darwin_process_facts(pid)
    return None, False


def _darwin_process_facts(pid: int) -> tuple[int | None, bool]:
    """`sysctl(KERN_PROC_PID)`'s `kinfo_proc`, as psutil reads it: `p_starttime` in centiseconds (psutil's
    `create_time() * 100`) and `p_stat`.

    Read for any user's process, unlike `proc_pidinfo`, which answers nothing
    for another user's: a gateway pid the OS gave to such a process must still
    be told from the gateway. `p_starttime` is the struct's first field and
    `p_stat` (`SZOMB` for a zombie) is at `KINFO_STAT_OFFSET`.
    """
    import ctypes

    try:
        libc = ctypes.CDLL("/usr/lib/libc.dylib", use_errno=True)
    except OSError:
        return None, False
    mib = (ctypes.c_int * 4)(CTL_KERN, KERN_PROC, KERN_PROC_PID, pid)
    buffer = ctypes.create_string_buffer(KINFO_PROC_BYTES)
    size = ctypes.c_size_t(KINFO_PROC_BYTES)
    if libc.sysctl(mib, 4, buffer, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0 or size.value <= KINFO_STAT_OFFSET:
        return None, False  # no such process: the kernel answers with nothing
    seconds, microseconds = struct.unpack_from("qi", buffer.raw, 0)
    return round((seconds + microseconds / 1_000_000) * 100), buffer.raw[KINFO_STAT_OFFSET] == SZOMB


def started_at(fingerprint: int, now: float) -> float | None:
    """When a process with this start-time fingerprint started, in epoch seconds; None off Linux and macOS.

    macOS' fingerprint is the start time itself in centiseconds; Linux's is
    clock ticks since boot, which `CLOCK_BOOTTIME` turns into an age.
    """
    if sys.platform == "darwin":
        return fingerprint / 100
    if sys.platform.startswith("linux"):
        try:
            return now - (time.clock_gettime(time.CLOCK_BOOTTIME) - fingerprint / os.sysconf("SC_CLK_TCK"))
        except (AttributeError, OSError, ValueError):
            return None
    return None


def resident_bytes(pid: int) -> int | None:
    """The process's resident memory: `/proc/<pid>/statm` on Linux, `proc_pidinfo` on macOS; None where the OS
    does not say (another user's process on macOS, any other platform). Neither sends the process anything."""
    if sys.platform.startswith("linux"):
        try:
            return int(Path(f"/proc/{pid}/statm").read_text(encoding="utf-8").split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, IndexError, ValueError):
            return None
    if sys.platform != "darwin":
        return None
    import ctypes

    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError:
        return None
    libproc.proc_pidinfo.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int)
    buffer = ctypes.create_string_buffer(TASK_INFO_BYTES)
    if libproc.proc_pidinfo(pid, PROC_PIDTASKINFO, 0, buffer, TASK_INFO_BYTES) != TASK_INFO_BYTES:
        return None
    return struct.unpack_from("Q", buffer.raw, 8)[0]


def process_is_record(pid: int | None, recorded_start: Any) -> bool:
    """Whether `pid` runs and is the process that recorded `recorded_start`.

    As Hermes' own liveness check, a zombie is dead and a start time further
    from the recorded one than its tolerance is a pid the OS gave to another
    process. Stricter than it in one case: a recorded start time that cannot
    be compared, because the process's own cannot be read, is not taken as a
    match, so a board never calls a reused pid a running gateway. Only a
    record without a start time is taken on its pid alone.
    """
    if pid is None or not ON_POSIX or not pid_exists(pid):
        return False
    current, zombie = process_facts(pid)
    if zombie:
        return False
    recorded = _fingerprint_value(recorded_start)
    if recorded is None:
        return True
    return current is not None and abs(current - recorded) <= START_TIME_TOLERANCE


def _text(value: Any) -> str:
    """Text from a Hermes file, cleaned as gateway text is and folded to one line."""
    return " ".join(clean_text(value if isinstance(value, str) else "").split())


def _record_time(value: Any) -> float | None:
    """An `updated_at` as Hermes reads one (`normalize_updated_at`): ISO text (naive is UTC), or a plausible epoch."""
    if isinstance(value, str):
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw)
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    number = _fingerprint_value(value)
    return float(value) if number is not None and number >= EPOCH_PLAUSIBLE else None


def read_gateway(home: Path, now: float | None = None) -> Gateway:
    """The gateway `<home>/gateway_state.json` records, and whether its process still runs."""
    path = home / GATEWAY_STATE_FILE
    try:
        if not path.is_file():
            return Gateway("", False, problem="no gateway record")
        with path.open("rb") as handle:
            record = json.loads(handle.read(GATEWAY_STATE_BYTES))
    except (OSError, ValueError, RecursionError):
        return Gateway("", False, problem="gateway record unreadable")
    if not isinstance(record, dict):
        return Gateway("", False, problem="gateway record unreadable")
    pid = _pid(record.get("pid"))
    named = record.get("hermes_home")
    elsewhere = isinstance(named, str) and bool(named) and not _same_path(Path(named), home)
    live = not elsewhere and process_is_record(pid, record.get("start_time"))
    platforms: list[Platform] = []
    entries = record.get("platforms")
    for name, entry in sorted(entries.items())[:PROFILE_CAP] if isinstance(entries, dict) else ():
        if not isinstance(entry, dict):
            continue
        stamped = entry.get("writer_pid") == pid and entry.get("writer_start_time") == record.get("start_time")
        platforms.append(Platform(
            _text(name), _text(entry.get("state")) or "unknown", _text(entry.get("error_message")),
            entry.get("needs_attention") is True, live and stamped,
        ))
    agents = record.get("active_agents")
    served = record.get("served_profiles")
    fingerprint = _fingerprint_value(record.get("start_time"))
    started = started_at(fingerprint, time.time() if now is None else now) if live and fingerprint is not None else None
    return Gateway(
        _text(record.get("gateway_state")) or "unknown", live, pid,
        agents if isinstance(agents, int) and not isinstance(agents, bool) and agents > 0 else 0,
        _text(record.get("exit_reason")),
        _text(named) if elsewhere else "",
        tuple(_text(name) for name in served if isinstance(name, str)) if isinstance(served, list) else (),
        tuple(platforms),
        started=started,
        updated=_record_time(record.get("updated_at")),
        restart_requested=record.get("restart_requested") is True,
        rss=resident_bytes(pid) if live and pid is not None else None,
    )


# ── the session store ─────────────────────────────────────────────────────


def _store_problem(error: sqlite3.Error) -> str:
    code = getattr(error, "sqlite_errorcode", None)
    return "state.db busy" if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else "state.db unreadable"


def _no_connection_open(path: Path) -> bool:
    """Whether no connection has the store at `path` open, read from the files beside it alone.

    A WAL store (every Hermes `state.db`) keeps a `-wal` and a `-shm` while any
    connection has it open, and the last one to close removes them; a
    rollback-journal store has a `-journal` only while it writes. A `mode=ro`
    open of a WAL store without them would create both and leave them there,
    so a store with none of them is opened immutable instead. A writer that
    opens it during the read can make that read fail, which costs one
    refresh; it cannot change the store. The store itself is never opened
    here: closing any descriptor on it would drop every SQLite lock this
    process holds on it (POSIX record locks are per process), the HUD reader's
    included.
    """
    try:
        return not any(path.with_name(path.name + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))
    except OSError:
        return False


def _open_store(home: Path) -> sqlite3.Connection | str:
    """A read-only connection to `<home>/state.db`, or why there is none; no file is created beside it, or for it."""
    path = home / STATE_DB
    try:
        if not path.is_file():
            return "no state.db"
        connection = connect_readonly(path, timeout=STATE_DB_BUSY_SECONDS, immutable=_no_connection_open(path))
    except OSError:
        return "state.db unreadable"
    except sqlite3.Error as error:
        return _store_problem(error)
    # A row whose text is not UTF-8 is shown with replacement characters rather than failing the whole read.
    connection.text_factory = lambda raw: raw.decode("utf-8", "replace")
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _activity(columns: set[str]) -> str:
    # One of two fixed literals, never caller text.
    return "COALESCE(last_activity_at, started_at)" if "last_activity_at" in columns else "started_at"


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) if math.isfinite(value) else 0.0


def _usage_select(columns: set[str], *, row: bool = False) -> str | None:
    """`cost_status`, a count, how many have tokens, input, output, cache reads and cost, for this store's columns,
    summed for a `GROUP BY` or, with `row`, one session's own, as the `Usage` fold reads them; None when the store
    records no tokens. Fixed literals, never caller text."""
    if not {"input_tokens", "output_tokens"} <= columns:
        return None
    status = "cost_status" if "cost_status" in columns else "NULL"
    cache = "cache_read_tokens" if "cache_read_tokens" in columns else "NULL"
    costs = [name for name in ("actual_cost_usd", "estimated_cost_usd") if name in columns]
    cost = f"COALESCE({', '.join(costs)})" if len(costs) == 2 else costs[0] if costs else "NULL"
    parts = ("COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) > 0", "input_tokens", "output_tokens", cache, cost)
    summed = ", ".join(part if row else f"SUM({part})" for part in parts)
    return f"{status}, {'1' if row else 'COUNT(*)'}, {summed}"


def _usage(rows: Sequence[Sequence[Any]]) -> Usage:
    """Fold `_usage_select`'s rows (one per `cost_status`) into one `Usage`: a cost only where Hermes priced one."""
    input_tokens = output_tokens = included = unpriced = 0
    cache: int | None = None
    cost: float | None = None
    estimated = False
    for status, count, with_tokens, read_in, read_out, read_cache, spent in rows:
        input_tokens += int(_number(read_in))
        output_tokens += int(_number(read_out))
        if read_cache is not None:
            cache = (cache or 0) + int(_number(read_cache))
        status = _text(status)
        if status in PRICED_STATUSES and isinstance(spent, (int, float)) and math.isfinite(spent):
            cost = (cost or 0.0) + float(spent)
            estimated = estimated or status != "actual"
        elif status == INCLUDED_STATUS:
            included += int(_number(count))
        else:
            unpriced += int(_number(with_tokens))
    return Usage(input_tokens, output_tokens, cache, cost, estimated, included, unpriced)


def _bounded_text(path: Path, limit: int) -> tuple[str | None, str]:
    """A regular file's text when it is at most `limit` bytes, else None and why: "missing", "unreadable", or
    too large (with its size). Never reads past `limit` + 1 bytes."""
    try:
        if not path.is_file():
            return None, "missing" if not path.exists() else "unreadable"
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            return None, f"too large to read ({_bytes_text(path.stat().st_size)})"
        return data.decode("utf-8-sig"), ""
    except (OSError, UnicodeDecodeError):
        return None, "unreadable"


def _config_text(config: str, key_path: tuple[str, ...]) -> str | None:
    try:
        return runtime_paths.config_scalar_text(config, key_path)
    except runtime_paths.RuntimeBindingError:
        return None


def _configured_model(config: str) -> tuple[str, str]:
    """The default model and provider config.yaml names, as Hermes reads `model` (a string, or `default`, else
    `model`, under it). Raises `RuntimeBindingError` on a shape the scan cannot follow."""
    def scalar(*key_path: str) -> str:
        value = _text(runtime_paths.config_scalar_text(config, key_path))
        if value[:1] in ("{", "["):  # a flow collection: empty is no value, anything else a shape not followed
            if value.replace(" ", "") in ("{}", "[]"):
                return ""
            raise runtime_paths.RuntimeBindingError("config.yaml uses an inline shape the board cannot follow")
        return value

    return scalar("model") or scalar("model", "default") or scalar("model", "model"), scalar("model", "provider")


def read_memory(home: Path, config: str | None) -> tuple[MemoryFile, ...]:
    """Hermes' built-in memory targets as `MemoryStore` counts them: entries split on its delimiter, characters
    of the entries joined again; each target's limit and switch from config.yaml when it says."""
    files = []
    for target, file_name, limit_key, switch_key in MEMORY_TARGETS:
        limit_text = _config_text(config, ("memory", limit_key)) if config is not None else None
        limit = int(limit_text) if limit_text is not None and limit_text.isascii() and limit_text.isdigit() else None
        switch = _config_text(config, ("memory", switch_key)) if config is not None else None
        enabled = switch is None or switch.strip().lower() not in YAML_FALSE
        text, problem = _bounded_text(home / MEMORY_DIR / file_name, MEMORY_FILE_BYTES)
        if problem and problem != "missing":
            files.append(MemoryFile(target, limit=limit, enabled=enabled, problem=problem))
            continue
        entries = [entry for entry in (part.strip() for part in (text or "").split(MEMORY_ENTRY_DELIMITER)) if entry]
        files.append(MemoryFile(target, len(entries), len(MEMORY_ENTRY_DELIMITER.join(entries)), limit, enabled))
    return tuple(files)


def read_omh_records(home: Path, config: str | None) -> tuple[int | None, str]:
    """How many record files the OMH store this profile's config.yaml names holds (`<store>/memory/records/*.json`,
    regular files only, as OMH's own index lists them; past `OMH_RECORDS_CAP` the count stops); (None, "") when
    config.yaml names no store.

    The store is the one the profile's own plugin binds, read as OMH's
    standalone lane reads it (`runtime_paths.standalone_omh_home_setting`);
    without that setting the board names none rather than guess `~/.omh`.
    """
    try:
        found, value = runtime_paths.standalone_omh_home_setting(config) if config is not None else (False, None)
        if not found or value is None:
            return None, ""
        store = runtime_paths.expand_path(value, hermes_home=home, relative_to=home)
    except runtime_paths.RuntimeBindingError:
        return None, "OMH store not resolvable"
    count = 0
    try:
        if not store.is_dir():
            return None, "OMH store not found"
        with os.scandir(store / "memory" / "records") as entries:
            for entry in entries:
                if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False):
                    count += 1
                    if count > OMH_RECORDS_CAP:
                        break
    except FileNotFoundError:
        return 0, ""
    except OSError:
        return None, "OMH memory unreadable"
    return count, ""


def read_profile(name: str, home: Path, current: Path | None, now: float) -> Profile:
    """One profile's row: its gateway, model, memory, and its sessions counted by source from `state.db`."""
    gateway = read_gateway(home, now)
    this_tui = current is not None and _same_path(current, home)
    config, problem = _bounded_text(home / CONFIG_FILE, CONFIG_BYTES)
    model = provider = ""
    try:
        model, provider = _configured_model(config) if config is not None else ("", "")
    except runtime_paths.RuntimeBindingError:
        problem = "unreadable"
    records, omh = read_omh_records(home, config)
    meta, _meta_problem = _bounded_text(home / PROFILE_META_FILE, CONFIG_BYTES)
    about = {
        "display": _text(_config_text(meta, ("display_name",))) if meta is not None else "",
        "model": model, "provider": provider, "memory": read_memory(home, config), "omh_records": records, "omh": omh,
        "config": {"": "", "missing": f"no {CONFIG_FILE}"}.get(problem, f"{CONFIG_FILE} {problem}"),
    }
    opened = _open_store(home)
    if isinstance(opened, str):
        return Profile(name, home, gateway, store=opened, this_tui=this_tui, **about)
    try:
        columns = _columns(opened, "sessions")
        activity = _activity(columns)
        since = now - ACTIVE_WINDOW_SECONDS
        rows = opened.execute(
            f"SELECT source, COUNT(*), SUM(CASE WHEN {activity} >= ? THEN 1 ELSE 0 END), MAX({activity}) "
            "FROM sessions GROUP BY source",
            (since,),
        ).fetchall()
        usage_select = _usage_select(columns)
        usage = _usage(opened.execute(
            f"SELECT {usage_select} FROM sessions WHERE {activity} >= ? GROUP BY 1", (since,),
        ).fetchall()) if usage_select is not None else None
        hours = opened.execute(
            f"SELECT CAST((? - {activity}) / 3600 AS INTEGER), "
            "SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) "
            f"FROM sessions WHERE {activity} >= ? GROUP BY 1",
            (now, since),
        ).fetchall() if usage_select is not None else None
        ran = opened.execute(
            f"SELECT model FROM sessions WHERE source != ? ORDER BY {activity} DESC LIMIT 1", (SUBAGENT_SOURCE,),
        ).fetchone() if "model" in columns else None
    except sqlite3.Error as error:
        return Profile(name, home, gateway, store=_store_problem(error), this_tui=this_tui, **about)
    finally:
        opened.close()
    sources = sorted(((_text(row[0]) or "unknown", int(_number(row[1]))) for row in rows), key=lambda item: (-item[1], item[0]))
    last = max((_number(row[3]) for row in rows), default=0.0)
    return Profile(
        name, home, gateway, tuple(sources), sum(int(_number(row[2])) for row in rows), last or None, this_tui=this_tui,
        ran=_text(ran[0]) if ran is not None else "", usage=usage, hourly=_hourly(hours), **about,
    )


def _hourly(rows: Sequence[Sequence[Any]] | None) -> tuple[int, ...]:
    """`ACTIVE_HOURS` token sums, oldest hour first, from (hours ago, tokens) rows; () when there were no rows to
    read. A session stamped in the future (a clock that moved back) counts in the newest hour."""
    if rows is None:
        return ()
    buckets = [0] * ACTIVE_HOURS
    for ago, tokens in rows:
        hour = min(max(int(_number(ago)), 0), ACTIVE_HOURS - 1)
        buckets[ACTIVE_HOURS - 1 - hour] += int(_number(tokens))
    return tuple(buckets)


def read_sessions(home: Path) -> tuple[tuple[SessionRow, ...], int, str]:
    """A profile's sessions but delegated children, newest activity first; how many children; why none, if so."""
    opened = _open_store(home)
    if isinstance(opened, str):
        return (), 0, opened
    try:
        select, activity = _session_select(opened)
        rows = opened.execute(
            f"{select} WHERE source != ? ORDER BY {activity} DESC LIMIT ?", (SUBAGENT_SOURCE, SESSION_ROWS_READ),
        ).fetchall()
        unlisted = opened.execute("SELECT COUNT(*) FROM sessions WHERE source = ?", (SUBAGENT_SOURCE,)).fetchone()[0]
    except sqlite3.Error as error:
        return (), 0, _store_problem(error)
    finally:
        opened.close()
    return tuple(_session_row(row) for row in rows), int(_number(unlisted)), ""


def _session_select(connection: sqlite3.Connection) -> tuple[str, str]:
    """The `SELECT ... FROM sessions` a `_session_row` reads, and the activity expression, for this store's columns."""
    columns = _columns(connection, "sessions")
    activity = _activity(columns)
    # Fixed literals, never caller text.
    title = "title" if "title" in columns else "NULL"
    display = "display_name" if "display_name" in columns else "NULL"
    model = "model" if "model" in columns else "NULL"
    # A session's own `Usage` is the same fold over one row; `NULL` with no token columns makes it None.
    usage = _usage_select(columns, row=True) or "NULL"
    return f"SELECT id, source, {title}, {display}, message_count, {activity}, {model}, {usage} FROM sessions", activity


def _session_row(row: Sequence[Any]) -> SessionRow:
    usage = _usage((row[7:],)) if len(row) > 8 else None
    return SessionRow(
        str(row[0]), _text(row[2]) or _text(row[3]), _text(row[1]), _number(row[5]), int(_number(row[4])),
        _text(row[6]), usage,
    )


def _tool_names(tool_calls: Any) -> str:
    try:
        calls = json.loads(tool_calls) if isinstance(tool_calls, str) else None
    except (ValueError, RecursionError):
        return ""
    names = []
    for call in calls if isinstance(calls, list) else ():
        function = call.get("function") if isinstance(call, dict) else None
        name = function.get("name") if isinstance(function, dict) else call.get("name") if isinstance(call, dict) else None
        if isinstance(name, str):
            names.append(_text(name))
    return ", ".join(names)


def _message_text(role: str, content: Any, tool_name: Any, tool_calls: Any) -> str:
    text = _text(content)
    if role == "tool" and _text(tool_name):
        return f"{_text(tool_name)}: {text}" if text else _text(tool_name)
    if not text and tool_calls:
        names = _tool_names(tool_calls)
        return f"(calls {names})" if names else "(tool call)"
    return text


def read_messages(home: Path, session_id: str) -> tuple[tuple[MessageRow, ...], SessionRow | None, str]:
    """The session's last `MESSAGE_ROWS_READ` messages the model still sees, oldest of them first, and its row now."""
    opened = _open_store(home)
    if isinstance(opened, str):
        return (), None, opened
    try:
        select, _activity_expression = _session_select(opened)
        found = opened.execute(f"{select} WHERE id = ?", (session_id,)).fetchone()
        live = " AND active = 1" if "active" in _columns(opened, "messages") else ""
        rows = opened.execute(
            "SELECT role, substr(content, 1, ?), tool_name, substr(tool_calls, 1, ?), timestamp FROM messages "
            f"WHERE session_id = ?{live} ORDER BY id DESC LIMIT ?",
            (MESSAGE_CHARS_READ, TOOL_CALLS_CHARS_READ, session_id, MESSAGE_ROWS_READ),
        ).fetchall()
    except sqlite3.Error as error:
        return (), None, _store_problem(error)
    finally:
        opened.close()
    messages = []
    for row in reversed(rows):
        role = _text(row[0]) or "unknown"
        messages.append(MessageRow(role, _message_text(role, row[1], row[2], row[3]), _number(row[4])))
    return tuple(messages), _session_row(found) if found is not None else None, ""


def read_board(environ: Mapping[str, str], view: BoardView, now: float) -> BoardSnapshot:
    """Every profile's row, and what `view` opens: one profile's sessions, or one session's messages."""
    current, homes = board_homes(environ)
    profiles = tuple(read_profile(name, home, current, now) for name, home in homes)
    home = next((home for name, home in homes if name == view.profile), None)
    if not view.profile:
        return BoardSnapshot(view, profiles, read_at=now)
    if home is None:
        return BoardSnapshot(view, profiles, problem="that profile is gone", read_at=now)
    if view.session:
        messages, session, problem = read_messages(home, view.session)
        listed = (session,) if session is not None else ()
        return BoardSnapshot(view, profiles, listed, messages=messages, problem=problem, read_at=now)
    sessions, unlisted, problem = read_sessions(home)
    return BoardSnapshot(view, profiles, sessions, unlisted, problem=problem, read_at=now)


class BoardReader:
    """Reads the board off the UI thread and keeps the newest snapshot for `take()`.

    One reader serves every opening of the board in a TUI: `start` opens it
    (at the profiles, reading at once), `stop` pauses it when the board
    closes, and `close` ends the thread when the TUI does. While open it reads
    whenever `show` names another view or `refresh` asks, else every
    `BOARD_REFRESH_SECONDS`; a read finished after the view changed, or after
    the board closed, is dropped. Nothing waits for a read under way, so a
    read stuck on a stalled file system holds this one thread and never more:
    the board opened again shows "Reading…" until it returns.
    """

    def __init__(
        self,
        environ: Mapping[str, str],
        *,
        clock: Callable[[], float] = time.time,
        read: Callable[[Mapping[str, str], BoardView, float], BoardSnapshot] = read_board,
    ) -> None:
        self._environ = environ
        self._clock = clock
        self._read = read
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._thread: threading.Thread | None = None
        self._open = False
        self._view = BoardView()
        self._generation = 0
        self._pending: BoardSnapshot | None = None
        self.on_snapshot: Callable[[], None] | None = None

    def start(self, wake: Callable[[], None] | None = None) -> None:
        """Open the board at the profiles and read at once; `wake` is called, on the thread, after each kept read."""
        self.on_snapshot = wake
        with self._lock:
            self._open = True
            self._view = BoardView()
            self._generation += 1
            self._pending = None
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="omh-bot-board", daemon=True)
            self._thread.start()
        self._wake.set()

    def stop(self) -> None:
        """The board closed: read nothing until it opens again."""
        with self._lock:
            self._open = False
            self._generation += 1
            self._pending = None

    def close(self) -> None:
        """The TUI is leaving: the thread ends after the read under way, if any, without being waited for."""
        self.stop()
        self._closing.set()
        self._wake.set()

    def show(self, view: BoardView) -> None:
        with self._lock:
            if view == self._view:
                return
            self._view = view
            self._generation += 1
        self._wake.set()

    def refresh(self) -> None:
        self._wake.set()

    def take(self) -> BoardSnapshot | None:
        """The newest snapshot not yet taken, or None. Never blocks on a read."""
        with self._lock:
            snapshot, self._pending = self._pending, None
        return snapshot

    def poll_once(self) -> None:
        with self._lock:
            view, generation = self._view, self._generation
        try:
            snapshot = self._read(self._environ, view, self._clock())
        except Exception as error:  # noqa: BLE001 - classified in tests/test_broad_exception_policy.py
            problem = f"the board could not be read: {type(error).__name__}"
            snapshot = BoardSnapshot(view, (), problem=problem, read_at=self._clock())
        with self._lock:
            if generation != self._generation or not self._open:
                return
            self._pending = snapshot
        if self.on_snapshot is not None:
            self.on_snapshot()

    def _run(self) -> None:
        while not self._closing.is_set():
            self._wake.clear()
            with self._lock:
                reading = self._open
            if reading:
                self.poll_once()
                self._wake.wait(BOARD_REFRESH_SECONDS)
            else:
                self._wake.wait()  # closed: no read until `start` or `close` wakes it


# ── drawing ───────────────────────────────────────────────────────────────


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _entries(number: int) -> str:
    return f"{number} {'entry' if number == 1 else 'entries'}"


def _bytes_text(size: int) -> str:
    """`512 B`, `12 KB`, `1.5 MB`, `412 MB`, `1.2 GB`."""
    for unit, suffix in ((1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "KB")):
        if size >= unit:
            amount = size / unit
            return f"{amount:.0f} {suffix}" if amount >= 100 else f"{amount:.1f}".removesuffix(".0") + f" {suffix}"
    return f"{size} B"


def _duration_text(seconds: float) -> str:
    """`45s`, `12m`, `3h 20m`, `2d 4h`."""
    whole = max(0, int(seconds))
    if whole < 3600:
        return f"{whole}s" if whole < 60 else f"{whole // 60}m"
    if whole < 86400:
        return f"{whole // 3600}h {whole % 3600 // 60}m"
    return f"{whole // 86400}d {whole % 86400 // 3600}h"


def _cost_text(usage: Usage) -> str:
    """`$0.43`, `~$0.43` when any of it is Hermes' estimate, then `+ N unpriced` for sessions it priced none of;
    `cost unknown` when it priced none; `included` when every session was in a plan; "" with no session."""
    if usage.cost is not None:
        mark = "~" if usage.estimated else ""
        cost = f"{mark}<$0.01" if 0 < usage.cost < 0.005 else f"{mark}${usage.cost:,.2f}"
        return cost + (f" + {usage.unpriced} unpriced" if usage.unpriced else "")
    if usage.unpriced:
        return "cost unknown"
    return INCLUDED_STATUS if usage.included else ""


def _usage_text(usage: Usage) -> str:
    """`1.2M in · 34.5k out · 12.4M cache read · ~$4.12`, each part only where the store recorded it."""
    parts = [f"{compact_count(usage.input)} in", f"{compact_count(usage.output)} out"]
    if usage.cache_read is not None:
        parts.append(f"{compact_count(usage.cache_read)} cache read")
    cost = _cost_text(usage)
    return " · ".join([*parts, cost] if cost else parts)


def _short_model(model: str) -> str:
    """A model without its vendor prefix, as Hermes' own insights show one (`anthropic/claude-x` is `claude-x`)."""
    return model.rsplit("/", 1)[-1]


def _gateway_words(profile: Profile, served_by: str, theme: Theme) -> tuple[str, str, Style]:
    """The gateway's glyph, what it says, and the style both take."""
    gateway = profile.gateway
    if gateway.problem:
        if served_by:
            return "●", f"served by the {served_by} gateway", theme.ok
        return "○", gateway.problem, theme.warn if "unreadable" in gateway.problem else theme.muted
    if gateway.live:
        words = [f"gateway {gateway.state}"]
        if gateway.pid is not None:
            words.append(f"pid {gateway.pid}")
        if gateway.active_agents:
            words.append(f"{_count(gateway.active_agents, 'agent')} running")
        return "●", " · ".join(words), theme.ok if gateway.state == GATEWAY_RUNNING else theme.warn
    if served_by:
        return "●", f"served by the {served_by} gateway", theme.ok
    if gateway.elsewhere:
        return "◌", f"gateway not running here · its record is another home's ({gateway.elsewhere})", theme.warn
    if gateway.state == GATEWAY_STOPPED:
        reason = f" ({gateway.exit_reason})" if gateway.exit_reason else ""
        return "○", f"gateway stopped{reason}", theme.muted
    pid = f"pid {gateway.pid} is gone" if gateway.pid is not None else "no pid recorded"
    return "◌", f"gateway not running · its record still says {gateway.state} ({pid})", theme.warn


# A row's piece: its drop rank (the highest goes first on a narrow row; 0 never goes), the gap before it, its spans.
Piece = tuple[int, str, tuple[Span, ...]]


def _served_by(profiles: Sequence[Profile], profile: Profile) -> str:
    """The default profile's name when its running gateway lists `profile` in `served_profiles`; else ""."""
    default = next((item for item in profiles if item.name == DEFAULT_PROFILE), None)
    if default is None or profile is default or not default.gateway.live:
        return ""
    return default.name if profile.name in default.gateway.served else ""


def _figure(number: str, unit: str, theme: Theme) -> tuple[Span, ...]:
    """A number to find at a glance, its unit quieter beside it."""
    return (Span(number, theme.user), Span(f" {unit}", theme.muted)) if unit else (Span(number, theme.user),)


def _usage_pieces(usage: Usage, theme: Theme, *, cached: bool = True) -> list[Piece]:
    """Tokens and cost as row pieces: cache reads go first on a narrow row, then cost, then output, then input."""
    pieces: list[Piece] = [
        (2, " · ", _figure(compact_count(usage.input), "in", theme)),
        (3, " · ", _figure(compact_count(usage.output), "out", theme)),
    ]
    if cached and usage.cache_read is not None:
        pieces.append((6, " · ", _figure(compact_count(usage.cache_read), "cached", theme)))
    cost = _cost_text(usage)
    if cost:
        price, _plus, unpriced = cost.partition(" + ")
        figure = (Span(price, theme.user),) if usage.cost is not None else (Span(cost, theme.muted),)
        tail = (Span(f" + {unpriced}", theme.muted),) if usage.cost is not None and unpriced else ()
        pieces.append((4, " · ", (*figure, *tail)))
    return pieces


def _fit(
    lead: Sequence[Span], pieces: Sequence[Piece], cols: int, right: Sequence[Piece] = (), first_gap: str = "",
) -> Line:
    """`lead`, then `pieces` each after its gap (the first one kept after `first_gap`), then `right` pushed to the
    row's end; while the row is wider than `cols`, the piece with the highest drop rank goes (rank 0 never does),
    on either side. Never more than one row."""
    left_kept, right_kept = [piece for piece in pieces if piece[2]], [piece for piece in right if piece[2]]

    def joined(kept: Sequence[Piece], first: str) -> list[Span]:
        spans: list[Span] = []
        for _rank, gap, piece in kept:
            spans += [Span(gap if spans else first), *piece]
        return [span for span in spans if span.text]

    while True:
        left, ends = [*lead, *joined(left_kept, first_gap)], joined(right_kept, "")
        room = cols - line_width(tuple(left)) - line_width(tuple(ends))
        droppable = [piece for piece in (*left_kept, *right_kept) if piece[0]]
        if room >= (FIT_GAP if ends else 0) or not droppable:
            break
        worst = max(droppable, key=lambda piece: piece[0])
        (left_kept if worst in left_kept else right_kept).remove(worst)
    pad = (Span(" " * room),) if ends and room > 0 else ()
    return truncate_line((*left, *pad, *ends), cols)


def _label(name: str, theme: Theme) -> tuple[Span, ...]:
    return (Span(name.ljust(LABEL_WIDTH), theme.muted),)


def _spark(hourly: Sequence[int], charset: str) -> str:
    """One cell per hour, its height the hour's share of the busiest one; an hour with none is the lowest cell."""
    steps = SPARK_GLYPHS[charset]
    peak = max(hourly, default=0)
    return "".join(steps[0] if not peak or not tokens else steps[max(1, round(tokens / peak * (len(steps) - 1)))]
                   for tokens in hourly)


def _gauge_style(percent: int, theme: Theme) -> Style:
    return theme.ok if percent < GAUGE_WARN else theme.warn if percent <= GAUGE_ERROR else theme.error


def _gauge(memory: MemoryFile, theme: Theme, charset: str) -> tuple[Span, ...]:
    """A target's use of its limit as `GAUGE_CELLS` cells and a percentage, coloured by how full it is; without a
    configured limit, its entry count; off, unreadable or too large as words."""
    if not memory.enabled:
        return (Span("off", theme.muted),)
    if memory.problem:
        return (Span(memory.problem, theme.warn),)
    if memory.limit is None or memory.limit <= 0:
        return _figure(str(memory.entries), "entry" if memory.entries == 1 else "entries", theme)
    percent = memory.chars * 100 // memory.limit
    filled = min(GAUGE_CELLS, max(1 if memory.chars else 0, percent // 10))
    full, empty = GAUGE_GLYPHS[charset]
    style = _gauge_style(percent, theme)
    return (Span(full * filled, style), Span(empty * (GAUGE_CELLS - filled), theme.dim), Span(f" {percent}%", style))


def avatar(name: str, charset: str = "unicode") -> list[str]:
    """`AVATAR_ROWS` rows of `AVATAR_COLS` cells drawn from `name` alone, as an identicon: a sha256 of the name lays
    out a mirrored grid of pixels, two to a cell (`▀`, `▄`, `█`). The same name draws the same face on every run."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    half = AVATAR_COLS // 2
    bits = int.from_bytes(digest[:8], "big")
    pixels = [[bits >> (row * half + column) & 1 for column in range(half)] for row in range(2 * AVATAR_ROWS)]
    glyphs = AVATAR_GLYPHS[charset]
    rows = []
    for row in range(AVATAR_ROWS):
        left = "".join(glyphs[pixels[2 * row][column] + 2 * pixels[2 * row + 1][column]] for column in range(half))
        rows.append(left + left[::-1])
    return rows


def _avatar_style(name: str, theme: Theme) -> Style:
    """The face's colour: one of the theme's accent steps (its tool hues and the delegation view's word steps, each
    lifted to 4.5:1 on the background), picked by the name's hash, so it reads on a light page and a dark one."""
    ramp = [style for style in (*theme.kinds, *theme.dag_words) if style.fg]
    if not ramp:
        return theme.text
    return replace(ramp[hashlib.sha256(name.encode("utf-8")).digest()[8] % len(ramp)], bold=False)


def _border(corners: tuple[str, str], left: Line, right: Line, width: int, edge: Style, line: str) -> Line:
    """One edge of a box `width` wide: `left` set into it after the corner, `right` before the other corner, the
    rest of the edge between; `right` goes when both do not fit, and `left` is cut to what is left."""
    first, last = corners
    head = (Span(f"{first}{line} ", edge),) if left else (Span(f"{first}{line}", edge),)
    tail = (Span(" ", edge), *right, Span(f" {line}{last}", edge)) if right else (Span(last, edge),)
    if right and line_width(head) + line_width(left) + line_width(tail) + 2 > width:
        tail = (Span(last, edge),)
    left = truncate_line(left, max(width - line_width(head) - line_width(tail) - 1, 0)) if left else ()
    fill = width - line_width(head) - line_width(left) - line_width(tail)
    return (*head, *left, Span((" " if left else "") + line * max(fill - (1 if left else 0), 0), edge), *tail)


def _box(top: tuple[Line, Line], bottom: Line, body: Sequence[Line], width: int, edge: Style, charset: str) -> list[Line]:
    """`body` in a rounded box `width` wide, in the launch card's style (`launch_card.boxed`): the top edge carries
    `top`'s two lines, left and right, the bottom edge `bottom`. The delegation view's charset picks the corners."""
    glyphs = BOX_GLYPHS[charset]
    inner = width - 2 - 2 * BOX_PAD
    rows = [(Span(glyphs[5] + " " * BOX_PAD, edge), *_pad(line, inner), Span(" " * BOX_PAD + glyphs[5], edge))
            for line in body]
    return [
        _border((glyphs[0], glyphs[1]), *top, width, edge, glyphs[4]), *rows,
        _border((glyphs[2], glyphs[3]), bottom, (), width, edge, glyphs[4]),
    ]


def _bot_title(profile: Profile, served_by: str, highlighted: bool, theme: Theme, charset: str) -> Line:
    glyph, _words, style = _gateway_words(profile, served_by, theme)
    glyph = GATEWAY_MARKS[charset].get(glyph, glyph)
    name = _text(profile.name)
    shown = f"{profile.display} ({name})" if profile.display and profile.display != name else name
    tag = (Span(" (this TUI)", theme.muted),) if profile.this_tui else ()
    return (Span(f"{glyph} ", style), Span(shown, replace(theme.user, underline=highlighted)), *tag)


def _bot_facts(profile: Profile, now: float, theme: Theme) -> Line:
    """The running gateway's uptime, agents and resident memory, for the box's top edge."""
    gateway = profile.gateway
    if not gateway.live:
        return ()
    facts: list[Line] = []
    if gateway.started is not None:
        facts.append((Span("up ", theme.muted), Span(_duration_text(now - gateway.started), theme.user)))
    if gateway.active_agents:
        facts.append(_figure(str(gateway.active_agents), "agent" if gateway.active_agents == 1 else "agents", theme))
    if gateway.rss is not None:
        amount, _space, unit = _bytes_text(gateway.rss).partition(" ")
        facts.append(_figure(amount, unit, theme))
    spans: list[Span] = []
    for fact in facts:
        spans += [*((Span(" · ", theme.muted),) if spans else ()), *fact]
    return tuple(spans)


def _bot_platforms(profile: Profile, served_by: str, width: int, theme: Theme, charset: str) -> Line:
    """For the box's bottom edge: what the gateway says when it is not plainly running, each platform as a chip
    coloured by its state, and a restart Hermes was asked for; the last chips go first when they do not fit."""
    gateway = profile.gateway
    _glyph, words, style = _gateway_words(profile, served_by, theme)
    pieces: list[Piece] = []
    if not gateway.live or gateway.state != GATEWAY_RUNNING:
        pieces.append((0, "", (Span(words, style),)))
    live, stale = CHIP_GLYPHS[charset]
    for number, platform in enumerate(gateway.platforms):
        if not platform.current:
            chip: tuple[Span, ...] = (Span(f"{platform.name} {stale}", theme.muted),)
        elif platform.error:
            chip = (Span(f"{platform.name} ", theme.text), Span(f"{live}!", theme.error))
        elif platform.needs_attention:
            chip = (Span(f"{platform.name} ", theme.text), Span(f"{live}!", theme.warn))
        else:
            chip = (Span(f"{platform.name} ", theme.text), Span(live, theme.ok if platform.state == "connected" else theme.warn))
        pieces.append((2 + number, "  ", chip))  # the last chip goes first
    if gateway.restart_requested:
        pieces.append((1, "  ", (Span("restart requested", theme.warn),)))
    return _fit((), pieces, width) if pieces else ()


def _model_row(profile: Profile, cols: int, theme: Theme) -> Line:
    if profile.config:
        model: tuple[Span, ...] = (Span(profile.config, theme.muted if profile.config.startswith("no ") else theme.warn),)
    elif profile.model:
        model = (Span(profile.model, theme.user),)
    else:
        model = (Span("no default model configured", theme.muted),)
    pieces: list[Piece] = [(0, "", model)]
    if profile.provider and not profile.config:
        pieces.append((2, "  ", (Span(profile.provider, theme.muted),)))
    if profile.ran and profile.ran != profile.model:
        pieces.append((1, " · ", (Span("newest ", theme.muted), Span(profile.ran, theme.text))))
    return _fit(_label("model", theme), pieces, cols)


def _day_rows(profile: Profile, now: float, cols: int, theme: Theme, charset: str) -> list[Line]:
    """The last 24 hours over two rows: tokens by hour beside the sessions active in them (of every one stored),
    then those sessions' tokens and cost."""
    lead = _label("24 h", theme)
    if profile.store:
        style = theme.muted if "no state.db" in profile.store else theme.warn
        return [_fit(lead, [(0, "", (Span(f"sessions: {profile.store}", style),))], cols), ()]
    total = sum(count for _source, count in profile.sources)
    stored = (1, " · ", _figure(f"{total:,}", "stored", theme))
    if not profile.active:
        last = f" · last active {age_text(profile.last_activity, now)}" if profile.last_activity else ""
        return [_fit(lead, [(0, "", (Span(f"no activity{last}", theme.muted),)), stored], cols), ()]
    pieces: list[Piece] = []
    if profile.hourly and any(profile.hourly):
        pieces.append((2, "", (Span(_spark(profile.hourly, charset), theme.running),)))
    pieces += [(0, SPARK_GAP if pieces else "", _figure(str(profile.active), "active", theme)), stored]
    spent = _fit(_label("tokens", theme), _usage_pieces(profile.usage, theme), cols) if profile.usage is not None else ()
    return [_fit(lead, pieces, cols), spent]


def _memory_row(profile: Profile, cols: int, theme: Theme, charset: str) -> Line:
    """Hermes' memory and user profile as gauges, then the OMH store's records."""
    pieces: list[Piece] = []
    for memory in profile.memory:
        named = () if memory.target == "memory" else (Span("user ", theme.muted),)
        pieces.append((0 if memory.target == "memory" else 2, MEMORY_GAP if pieces else "", (*named, *_gauge(memory, theme, charset))))
    if profile.omh_records is not None:
        shown = f"{OMH_RECORDS_CAP:,}+" if profile.omh_records > OMH_RECORDS_CAP else f"{profile.omh_records:,}"
        pieces.append((3, MEMORY_GAP, (Span("OMH ", theme.muted), *_figure(shown, "record" if profile.omh_records == 1 else "records", theme))))
    elif profile.omh:
        pieces.append((3, MEMORY_GAP, (Span(profile.omh, theme.muted),)))
    return _fit(_label("memory", theme), pieces, cols)


def bot_box(profile: Profile, served_by: str, highlighted: bool, now: float, width: int, theme: Theme,
            charset: str = "unicode") -> list[Line]:
    """One bot's box: its name and gateway mark in the top edge, the running gateway's facts on its right; its face
    and the model, last-24-hours, tokens and memory rows inside; its platforms in the bottom edge. The highlighted
    bot's edge takes the theme's accent (`brand`), the others the rule's colour. Every box has `BOX_BODY_ROWS` rows, so boxes side by side are as tall."""
    inner = width - 2 - 2 * BOX_PAD
    face = avatar(_text(profile.name), charset) if inner >= AVATAR_MIN_INNER else []
    room = inner - (AVATAR_COLS + len(AVATAR_GAP) if face else 0)
    rows = [_model_row(profile, room, theme), *_day_rows(profile, now, room, theme, charset),
            _memory_row(profile, room, theme, charset)]
    style = _avatar_style(_text(profile.name), theme)
    body = [((Span(face[number], style), Span(AVATAR_GAP)) if face else ()) + row for number, row in enumerate(rows)]
    edge = theme.brand if highlighted else theme.rule
    top = (_bot_title(profile, served_by, highlighted, theme, charset), _bot_facts(profile, now, theme))
    return _box(top, _bot_platforms(profile, served_by, max(width - 6, 1), theme, charset), body, width, edge, charset)


def _board_head(profiles: Sequence[Profile], cols: int, theme: Theme) -> Line:
    """The title, then what every profile adds up to: gateways up, agents running, the active sessions' tokens."""
    up = [profile.gateway for profile in profiles if profile.gateway.live]
    pieces: list[Piece] = [(0, "", (Span(BOARD_TITLE, theme.heading), Span(f" · {_count(len(profiles), 'profile')}", theme.muted)))]
    if up:
        pieces.append((1, " · ", _figure(str(len(up)), "gateway up" if len(up) == 1 else "gateways up", theme)))
        agents = sum(gateway.active_agents for gateway in up)
        if agents:
            pieces.append((2, " · ", _figure(str(agents), "agent running" if agents == 1 else "agents running", theme)))
    usages = [profile.usage for profile in profiles if profile.usage is not None and profile.active]
    if usages:
        costs = [usage.cost for usage in usages if usage.cost is not None]
        total = Usage(
            sum(usage.input for usage in usages), sum(usage.output for usage in usages), None,
            sum(costs) if costs else None, any(usage.estimated for usage in usages),
            sum(usage.included for usage in usages), sum(usage.unpriced for usage in usages),
        )
        day: list[Span] = [Span("24 h: ", theme.muted)]
        for number, (_rank, gap, piece) in enumerate(_usage_pieces(total, theme, cached=False)):
            day += [*((Span(gap),) if number else ()), *piece]
        pieces.append((3, " · ", tuple(day)))
    return _fit((), pieces, cols)


def profile_lines(
    profiles: Sequence[Profile], selected: int, now: float, cols: int, theme: Theme, charset: str = "unicode",
) -> tuple[list[Line], tuple[int, int]]:
    """Level 1's rows at `cols`, and the first and last row of the highlighted profile's box. Two boxes to a row when
    each can be `BOX_MIN` wide, else one; `charset` is the delegation view's (`unicode` or `ascii`), for the box
    edges, the faces, the gauges, the sparkline and the chips."""
    lines: list[Line] = [_board_head(profiles, cols, theme), ()]
    span = (0, 0)
    across = 2 if cols >= 2 * BOX_MIN + len(BOX_GAP) else 1
    width = max((cols - (across - 1) * len(BOX_GAP)) // across, 1)
    for start in range(0, len(profiles), across):
        boxes = [
            bot_box(profile, _served_by(profiles, profile), start + offset == selected, now, width, theme, charset)
            for offset, profile in enumerate(profiles[start : start + across])
        ]
        first = len(lines)
        for parts in zip(*boxes):
            row: list[Span] = []
            for part in parts:
                row += [*((Span(BOX_GAP),) if row else ()), *part]
            lines.append(tuple(row))
        if start <= selected < start + across:
            span = (first, len(lines) - 1)
        lines.append(())
    if not profiles:
        lines.append((Span("No Hermes home was found.", theme.muted),))
    return [truncate_line(line, cols) for line in lines], span


def _detail_lines(profile: Profile, served_by: str, now: float, theme: Theme) -> list[Line]:
    """Level 2's account of the profile under its title: the gateway record in full (pid, how old, each platform
    and its error), and the memory and session counts level 1 shows as gauges and totals."""
    glyph, words, style = _gateway_words(profile, served_by, theme)
    gateway = profile.gateway
    record = [Span(f"{glyph} {words}", style)]
    if not (gateway.problem or served_by or gateway.elsewhere):
        tail = []
        if gateway.updated is not None:
            tail.append(f"updated {age_text(gateway.updated, now)}")
        tail += [f"{platform.name} {platform.state}" + ("" if platform.current else " when last recorded")
                 + (f" ({platform.error})" if platform.error else " (needs attention)" if platform.needs_attention else "")
                 for platform in gateway.platforms]
        if tail:
            record.append(Span(f" · {' · '.join(tail)}", theme.muted))
        if gateway.restart_requested:
            record.append(Span(" · restart requested", theme.warn))
    counts = []
    for memory in profile.memory:
        label = "memory" if memory.target == "memory" else "user profile"
        if memory.enabled and not memory.problem:
            limit = f"/{memory.limit:,}" if memory.limit is not None else ""
            counts.append(f"{label} {_entries(memory.entries)}, {memory.chars:,}{limit} chars")
    if profile.sources:
        counts.append("sessions by source: " + ", ".join(f"{source} {count}" for source, count in profile.sources))
    return [tuple(record), *([(Span(" · ".join(counts), theme.muted),)] if counts else [])]


def session_lines(
    snapshot: BoardSnapshot, selected: int, now: float, cols: int, theme: Theme,
) -> tuple[list[Line], tuple[int, int]]:
    """Level 2's rows at `cols`, and the highlighted session's row (twice)."""
    view = snapshot.view
    unlisted = f"; {_count(snapshot.unlisted, 'subagent session')} not listed" if snapshot.unlisted else ""
    head = f"{_text(view.profile)} · {_count(len(snapshot.sessions), 'session')}, newest activity first{unlisted}"
    lines: list[Line] = [(Span(head, theme.heading),)]
    profile = next((item for item in snapshot.profiles if item.name == view.profile), None)
    if profile is not None:
        lines += _detail_lines(profile, _served_by(snapshot.profiles, profile), now, theme)
    lines.append(())
    if snapshot.problem:
        lines.append((Span(snapshot.problem, theme.warn),))
    elif not snapshot.sessions:
        lines.append((Span("No sessions.", theme.muted),))
    span = (0, 0)
    table = _session_table(snapshot.sessions, now)
    keep = _session_columns(table, cols - cell_width(CHOICE_MARK))
    widths = {name: max(cell_width(cells[name]) for cells in table) for name in keep}
    room = cols - cell_width(CHOICE_MARK) - sum(widths[name] + len(SESSION_GAP) for name in keep)
    for index, (row, cells) in enumerate(zip(snapshot.sessions, table)):
        highlighted = index == selected
        band = theme.menu_current if highlighted else theme.menu
        mark = CHOICE_MARK if highlighted else " " * len(CHOICE_MARK)
        if highlighted:
            span = (len(lines), len(lines))
        label = row.title or _text(row.id)
        if room < SESSION_TITLE_MIN:
            # No room for a column: the title alone, still banded to the row's end.
            label = truncate(label, max(cols - cell_width(mark), 0))
            lines.append((Span(mark, _on(band, theme.prompt)), Span(label + " " * (cols - cell_width(mark) - cell_width(label)), band)))
            continue
        label = truncate(label, room)
        spans = [Span(mark, _on(band, theme.prompt)), Span(label + " " * (room - cell_width(label)), band)]
        for name in keep:
            pad = " " * (widths[name] - cell_width(cells[name]))
            text = pad + cells[name] if name in SESSION_RIGHT else cells[name] + pad
            spans.append(Span(SESSION_GAP + text, _on(band, theme.muted)))
        lines.append(tuple(spans))
    return [truncate_line(line, cols) for line in lines], span


def _on(band: Style, style: Style, text: str = "") -> Style:
    """`style`'s colour on the row's band, so the band runs unbroken from the mark to the row's end; the band's own
    ink where `style`'s colour would not read on it (a muted word on the highlighted band): 4.5:1 for words, 3:1
    for a lone mark such as a platform's dot, so its state's colour survives wherever it can."""
    floor = TEXT_CONTRAST if any(character.isalnum() for character in text) or not text else EDGE_CONTRAST
    readable = style.fg is not None and band.bg is not None and contrast_ratio(style.fg, band.bg) >= floor
    return replace(style, fg=style.fg if readable or band.bg is None else band.fg, bg=band.bg, reverse=band.reverse)


def _session_table(sessions: Sequence[SessionRow], now: float) -> list[dict[str, str]]:
    """Each session's level-2 cells by column; "" where the store recorded nothing for it."""
    table = []
    for row in sessions:
        usage = row.usage if row.usage is not None and (row.usage.input or row.usage.output) else None
        cost = _cost_text(usage) if usage is not None and (usage.cost is not None or usage.included) else ""
        table.append({
            "model": _short_model(row.model),
            "input": f"{compact_count(usage.input)} in" if usage is not None else "",
            "output": f"{compact_count(usage.output)} out" if usage is not None else "",
            "cost": cost,
            "source": row.source,
            "age": age_text(row.last_activity, now),
            "messages": _count(row.message_count, "message"),
        })
    return table


def _session_columns(table: Sequence[Mapping[str, str]], cols: int) -> list[str]:
    """The level-2 columns that fit beside a title of at least `SESSION_TITLE_MIN` cells in `cols`: a column no
    row fills is left out, then `SESSION_DROPS` are given up in order until the title has its room."""
    keep = [name for name in SESSION_COLUMNS if any(cells[name] for cells in table)]
    for dropped in ((), *SESSION_DROPS):
        keep = [name for name in keep if name not in dropped]
        used = sum(max(cell_width(cells[name]) for cells in table) + len(SESSION_GAP) for name in keep)
        if cols - used >= SESSION_TITLE_MIN:
            break
    return keep


def _clock_text(at: float) -> str:
    try:
        return datetime.fromtimestamp(at).strftime("%m-%d %H:%M") if at > 0 else "--"
    except (OverflowError, OSError, ValueError):
        return "--"


def message_lines(snapshot: BoardSnapshot, session: SessionRow | None, now: float, cols: int, theme: Theme) -> list[Line]:
    """Level 3's rows at `cols`: who and where, then each message's time, role and its text's first rows."""
    view = snapshot.view
    title = (session.title or session.id) if session is not None else view.session
    lines: list[Line] = [(Span(f"{_text(view.profile)} › {_text(title)}", theme.heading),)]
    if session is not None:
        about = f"{session.source} · {_count(session.message_count, 'message')} · last active {age_text(session.last_activity, now)}"
        lines.append((Span(f"{about} · the last {MESSAGE_ROWS_READ} at most, oldest first", theme.muted),))
        ran = " · ".join(part for part in (session.model, _usage_text(session.usage) if session.usage is not None else "") if part)
        if ran:
            lines.append((Span(ran, theme.muted),))
    lines.append(())
    if snapshot.problem:
        lines.append((Span(snapshot.problem, theme.warn),))
    elif not snapshot.messages:
        lines.append((Span("No messages.", theme.muted),))
    roles = {"user": theme.user, "assistant": theme.label, "tool": theme.tool}
    for message in snapshot.messages:
        stamp, role = f"{_clock_text(message.at)} ", f"{message.role:<9} "
        lead = cell_width(stamp) + cell_width(role)
        text = message.text[:MESSAGE_SHOWN_CHARS] + ("…" if len(message.text) > MESSAGE_SHOWN_CHARS else "")
        room = max(cols - lead, 8)
        rows = wrap_line((Span(text or "(empty)", theme.text if text else theme.muted),), room)
        kept = 1 if message.role == "tool" else MESSAGE_SHOWN_ROWS  # a tool result is the call's, not the talk's
        if len(rows) > kept:
            # The last row kept ends in an ellipsis, so a cut message never reads as whole.
            rows = [*rows[: kept - 1], truncate_line((*rows[kept - 1], Span(" …")), room)]
        for number, row in enumerate(rows):
            prefix = (Span(stamp, theme.muted), Span(role, roles.get(message.role, theme.muted))) if number == 0 else (Span(" " * lead),)
            lines.append((*prefix, *row))
    return [truncate_line(line, cols) for line in lines]


def _find(keys: Sequence[str], key: str, index: int) -> int:
    """Where `key` is in `keys` now; where it was when it is gone (or none was chosen), kept in range."""
    if key in keys:
        return keys.index(key)
    return min(index, max(len(keys) - 1, 0))


def _show_nothing(view: BoardView) -> None:
    return None


def _refresh_nothing() -> None:
    return None


@dataclass
class BotBoard(Pager):
    """The board in the pager: the levels, the highlight on each, and the newest snapshot the reader delivered."""

    FOOTER_ROWS: ClassVar[int] = 2

    lines: list[Line] = field(default_factory=list)
    snapshot: BoardSnapshot | None = None
    show: Callable[[BoardView], None] = _show_nothing  # tells the reader what to read next
    refresh: Callable[[], None] = _refresh_nothing
    clock: Callable[[], float] = time.time
    view: BoardView = field(default_factory=BoardView)
    profile_index: int = 0
    session_index: int = 0
    # What the highlight is on, by name and id: the lists re-sort by activity at every read, so an index alone
    # would slide to another row under the person's Enter.
    highlighted_profile: str = ""
    highlighted_session: str = ""
    opened: SessionRow | None = None  # the level-2 row level 3 shows, for its header until its own row is read
    _span: tuple[int, int] = field(default=(0, 0), init=False, repr=False, compare=False)
    _head: int = field(default=2, init=False, repr=False, compare=False)  # the first item's row: the header is above
    _follow: bool = field(default=True, init=False, repr=False, compare=False)
    _to_bottom: bool = field(default=False, init=False, repr=False, compare=False)

    def take(self, snapshot: BoardSnapshot) -> None:
        self.snapshot = snapshot

    def _current(self) -> BoardSnapshot | None:
        """The newest snapshot when it was read for the view shown now."""
        snapshot = self.snapshot
        return snapshot if snapshot is not None and snapshot.view == self.view else None

    def _count(self) -> int:
        snapshot = self.snapshot
        if snapshot is None:
            return 0
        if self.view.profile:
            current = self._current()
            return len(current.sessions) if current is not None else 0
        return len(snapshot.profiles)

    def handle_key(self, event: Key | Paste, rows: int) -> bool:
        """One key; False when the person closed the board."""
        if not isinstance(event, Key):
            return True
        char = event.char if event.name == CHAR and not event.ctrl and not event.alt else ""
        if event.name in (ESCAPE, LEFT) or char == "q":
            return self._back()
        if char == "r":
            self.refresh()
        elif event.name in (ENTER, RIGHT):
            self._open()
        elif self.view.session:
            return super().handle_key(event, rows)
        elif event.name == DOWN or char == "j":
            self._move(1)
        elif event.name == UP or char == "k":
            self._move(-1)
        elif event.name == PAGE_DOWN:
            self._move(self.body_rows(rows))
        elif event.name == PAGE_UP:
            self._move(-self.body_rows(rows))
        elif event.name == HOME or char == "g":
            self._move(-self._count())
        elif event.name == END or char == "G":
            self._move(self._count())
        return True

    def _move(self, step: int) -> None:
        last = max(self._count() - 1, 0)
        if self.view.profile:
            current = self._current()
            self.session_index = min(max(self.session_index + step, 0), last)
            if current is not None and current.sessions:
                self.highlighted_session = current.sessions[self.session_index].id
        else:
            profiles = self.snapshot.profiles if self.snapshot is not None else ()
            self.profile_index = min(max(self.profile_index + step, 0), last)
            if profiles:
                self.highlighted_profile = profiles[self.profile_index].name
        self._follow = True

    def _open(self) -> None:
        snapshot = self.snapshot
        if snapshot is None or self.view.session:
            return
        if not self.view.profile:
            if self.profile_index < len(snapshot.profiles):
                self.highlighted_profile = snapshot.profiles[self.profile_index].name
                self._go(BoardView(self.highlighted_profile))
                self.session_index, self.highlighted_session = 0, ""
            return
        current = self._current()
        if current is not None and self.session_index < len(current.sessions):
            self.opened = current.sessions[self.session_index]
            self.highlighted_session = self.opened.id
            self._go(BoardView(self.view.profile, self.opened.id))
            self._to_bottom = True

    def _back(self) -> bool:
        if self.view.session:
            self._go(BoardView(self.view.profile))
        elif self.view.profile:
            self._go(BoardView())
        else:
            return False
        return True

    def _go(self, view: BoardView) -> None:
        self.view = view
        self.top = 0
        self._follow = True
        self.show(view)

    def _page(self, cols: int, rows: int, theme: Theme) -> tuple[list[Line], str]:
        self._draw(cols, theme)
        body = self.body_rows(rows)
        # Level 1 draws from any snapshot (every one carries the profiles); level 2 waits for its own read.
        drawn = self.snapshot is not None if not self.view.profile else self._current() is not None
        if self._to_bottom and self._current() is not None:
            self._to_bottom = False
            self.top = max(len(self.lines) - body, 0)
        elif self._follow and not self.view.session and drawn:
            self._follow = False
            first, last = self._span
            if first < self.top:
                self.top = 0 if first <= self._head else first  # the first item brings the header above it back
            elif last >= self.top + body:
                self.top = min(first, last - body + 1)
        return super()._page(cols, rows, theme)

    def _draw(self, cols: int, theme: Theme) -> None:
        snapshot = self.snapshot
        now = self.clock()
        if snapshot is None:
            self.lines, self._span = [(Span("Reading…", theme.muted),)], (0, 0)
            return
        self.profile_index = _find(
            [profile.name for profile in snapshot.profiles], self.highlighted_profile, self.profile_index,
        )
        if not snapshot.profiles and snapshot.problem:
            self.lines, self._span = [(Span(snapshot.problem, theme.warn),)], (0, 0)
            return
        if not self.view.profile:
            self.lines, self._span = profile_lines(snapshot.profiles, self.profile_index, now, cols, theme)
            self._head = 2
            return
        current = self._current()
        if current is None:
            self.lines, self._span = [(Span(f"{_text(self.view.profile)} · reading…", theme.muted),)], (0, 0)
            return
        if not self.view.session:
            self.session_index = _find([row.id for row in current.sessions], self.highlighted_session, self.session_index)
            self.lines, self._span = session_lines(current, self.session_index, now, cols, theme)
            self._head = len(self.lines) - len(current.sessions)  # the table's rows close the page
            return
        session = current.sessions[0] if current.sessions else self.opened  # the row as read now, else as opened
        self.lines, self._span = message_lines(current, session, now, cols, theme), (0, 0)

    def footer(self, position: str, theme: Theme) -> list[Line]:
        """The keys, then the title, where the page is, and how old the reading is."""
        keys = MESSAGE_KEYS if self.view.session else BOARD_KEYS
        snapshot = self.snapshot
        age = f"read {age_text(snapshot.read_at, self.clock())}" if snapshot is not None else "reading…"
        refresh = f"every {BOARD_REFRESH_SECONDS:g} s"
        return [
            (Span(f" {keys} ", theme.status),),
            (Span(f" {self.title} · rows {position} · {age} · {refresh} · read-only ", theme.status),),
        ]
