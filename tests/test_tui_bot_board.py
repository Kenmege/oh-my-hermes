"""The bot board (Ctrl+S, `/bots`): this machine's Hermes profiles, read-only, in the pager.

Fixture homes stand in for a real root: `default` with a gateway this very
process "runs" (its pid and start time), a connected Slack it stamped and a
Discord entry an older process left; `miku` whose record still says running
for a pid that is gone; `broken` with a record that is not JSON and no
state.db; `locked` whose state.db another connection holds exclusively; and
three directories that are no profile (hidden, no identity file, a deletion
tombstone). `default` also has a config.yaml (its model, memory limits and an
OMH store with two records) and a MEMORY.md; `SummaryTests` build the token
columns, memory files and configs the summaries read, and the shapes they must
refuse, in the same tree. The cases read them through the board's own readers, draw them at
a narrow and a wide width, drive the board's keys, open it from the app with
Ctrl+S and `/bots` (and not from inside Ctrl+R's search), and prove nothing is
written: every connection is `mode=ro`, the only signal is 0, and the tree's
bytes and times are what they were.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from unittest.mock import patch

from _test_temp_root import make_test_tempdir
from _tui_lines import line_text, lines_text
from test_tui_modes import make_app, type_text
from test_tui_muscle_memory import commands, send
from omh.plugin_bundle.omh import sqlite_readonly
from omh.tui import bot_board
from omh.tui.bot_board import (
    BOARD_TITLE,
    MESSAGE_ROWS_READ,
    BoardSnapshot,
    BoardView,
    BotBoard,
    board_homes,
    message_lines,
    profile_lines,
    read_board,
    read_gateway,
    read_messages,
    read_profile,
    read_sessions,
    session_lines,
    process_facts,
)
from omh.tui.history import HistorySearch
from omh.tui.keys import CHAR, DOWN, ENTER, ESCAPE, LEFT, UP, Key
from omh.tui.theme import DEFAULT_THEME
from omh.tui.width import line_width

NOW = 1_800_000_000.0
DAY = 24 * 3600
CTRL_S = Key(CHAR, "s", ctrl=True)
GONE_PID = 4_194_301  # past macOS' pid range; `pid_exists` is patched where it matters
# The board probes a gateway pid on POSIX only (the OMH TUI needs termios); off it no gateway ever reads as running.
POSIX_ONLY = unittest.skipUnless(os.name == "posix", "the board's liveness probe is POSIX-only")


def write_store(home: Path, sessions: list[tuple], messages: list[tuple] = (), *, wal: bool = False) -> Path:
    """A state.db with the columns the board reads: (id, source, title, started, last activity, count) rows."""
    path = home / "state.db"
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT, display_name TEXT, "
        "started_at REAL NOT NULL, ended_at REAL, last_activity_at REAL, message_count INTEGER DEFAULT 0);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL, "
        "content TEXT, tool_name TEXT, tool_calls TEXT, timestamp REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);"
    )
    connection.executemany(
        "INSERT INTO sessions (id, source, title, started_at, last_activity_at, message_count) VALUES (?, ?, ?, ?, ?, ?)",
        sessions,
    )
    connection.executemany(
        "INSERT INTO messages (session_id, role, content, tool_name, tool_calls, timestamp, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        messages,
    )
    connection.commit()
    connection.close()
    return path


USAGE_COLUMNS = (
    "model TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, "
    "estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT"
)


def write_usage_store(home: Path, sessions: list[tuple], columns: str = USAGE_COLUMNS) -> Path:
    """A state.db as Hermes writes one now: (id, source, title, last activity, count, *the `columns`) rows."""
    path = home / "state.db"
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT, display_name TEXT, "
        f"started_at REAL NOT NULL, ended_at REAL, last_activity_at REAL, message_count INTEGER DEFAULT 0, {columns});"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL, "
        "content TEXT, tool_name TEXT, tool_calls TEXT, timestamp REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);"
    )
    names = [column.split()[0] for column in columns.split(", ")]
    marks = ", ".join("?" * (6 + len(names)))
    connection.executemany(
        f"INSERT INTO sessions (id, source, title, started_at, last_activity_at, message_count, {', '.join(names)}) "
        f"VALUES ({marks})",
        [(row[0], row[1], row[2], row[3] - 60, row[3], row[4], *row[5:]) for row in sessions],
    )
    connection.commit()
    connection.close()
    return path


def write_memory(home: Path, name: str, entries: list[str] | bytes) -> Path:
    path = home / "memories" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(entries if isinstance(entries, bytes) else "\n§\n".join(entries).encode("utf-8"))
    return path


ROOT_CONFIG = """model:
  provider: claude-proxy
  default: claude-opus-5-5
memory:
  memory_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
plugins:
  entries:
    omh:
      settings:
        omh_home: omh-store
"""


def write_record(home: Path, record: dict | str) -> None:
    text = record if isinstance(record, str) else json.dumps(record)
    (home / "gateway_state.json").write_text(text, encoding="utf-8")


def make_root(root: Path) -> None:
    pid, started = os.getpid(), process_facts(os.getpid())[0]
    write_record(root, {
        "pid": pid, "start_time": started, "hermes_home": str(root), "gateway_state": "running",
        "active_agents": 2, "served_profiles": ["default"],
        "platforms": {
            "slack": {"state": "connected", "writer_pid": pid, "writer_start_time": started},
            "discord": {"state": "connected", "writer_pid": GONE_PID, "writer_start_time": 1},
        },
    })
    write_store(
        root,
        [
            ("s-new", "slack", "Release notes", NOW - 3 * DAY, NOW - 600, 12),
            ("s-old", "slack", None, NOW - 30 * DAY, None, 4),
            ("s-cli", "cli", "Local run", NOW - 2 * DAY, NOW - 2 * DAY, 2),
            ("s-child", "subagent", "child", NOW - 700, NOW - 700, 3),
        ],
        [
            ("s-new", "user", "ship it?", None, None, NOW - 900, 1),
            ("s-new", "assistant", "\x1b[31mred\x1b[0m done \x1b]0;pwned\x07now", None, None, NOW - 800, 1),
            ("s-new", "assistant", "", None, json.dumps([{"function": {"name": "terminal"}}]), NOW - 700, 1),
            ("s-new", "tool", "exit 0", "terminal", None, NOW - 650, 1),
            ("s-new", "user", "compacted away", None, None, NOW - 640, 0),
        ],
    )
    (root / "config.yaml").write_text(ROOT_CONFIG, encoding="utf-8")
    write_memory(root, "MEMORY.md", ["User ships on Fridays", "  Prefers short answers  "])
    records = root / "omh-store" / "memory" / "records"
    records.mkdir(parents=True)
    for name in ("mem_a.json", "mem_b.json", "notes.txt"):
        (records / name).write_text("{}", encoding="utf-8")
    profiles = root / "profiles"
    miku = profiles / "miku"
    miku.mkdir(parents=True)
    write_record(miku, {
        "pid": GONE_PID, "start_time": 1, "hermes_home": str(miku), "gateway_state": "running",
        "platforms": {"slack": {"state": "connected", "writer_pid": GONE_PID, "writer_start_time": 1}},
    })
    write_store(miku, [(f"m-{n}", "slack", f"chat {n}", NOW - 20 * DAY, NOW - 19 * DAY, 5) for n in range(3)], wal=True)
    broken = profiles / "broken"
    broken.mkdir()
    (broken / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    write_record(broken, "{not json")
    locked = profiles / "locked"
    locked.mkdir()
    write_record(locked, {"pid": GONE_PID, "gateway_state": "stopped", "exit_reason": "signal"})
    write_store(locked, [("l-1", "cli", "x", NOW, NOW, 1)], wal=True)
    (profiles / ".hidden").mkdir()
    (profiles / ".hidden" / "config.yaml").write_text("", encoding="utf-8")
    (profiles / "ghost").mkdir()  # no identity file: a cron heartbeat's leftover, never a profile
    (profiles / "gone").mkdir()
    (profiles / "gone" / "config.yaml").write_text("", encoding="utf-8")
    (profiles / ".deleted" / "gone").mkdir(parents=True)


def tree_state(root: Path) -> dict[str, tuple[int, int]]:
    """Every file under `root` with its size and modification time."""
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*")) if path.is_file()
    }


class FixtureRoot(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(make_test_tempdir("omh-bot-board-")))
        make_root(self.root)
        self.environ = {"HERMES_HOME": str(self.root)}


class HomesTests(FixtureRoot):
    def test_the_root_and_each_named_profile_and_nothing_that_is_no_profile(self) -> None:
        current, homes = board_homes(self.environ)
        self.assertEqual(current, self.root)
        self.assertEqual([name for name, _home in homes], ["default", "broken", "locked", "miku"])

    def test_a_profile_home_finds_its_root_and_is_the_one_marked_as_this_tui(self) -> None:
        environ = {"HERMES_HOME": str(self.root / "profiles" / "miku")}
        current, homes = board_homes(environ)
        self.assertEqual(homes[0], ("default", self.root))
        snapshot = read_board(environ, BoardView(), NOW)
        self.assertEqual([profile.name for profile in snapshot.profiles if profile.this_tui], ["miku"])

    def test_without_hermes_home_it_is_hermes_own_default_under_home(self) -> None:
        current, homes = board_homes({"HOME": str(self.root)})
        self.assertEqual(current, self.root / ".hermes")
        self.assertEqual(homes, [("default", self.root / ".hermes")])
        self.assertEqual(board_homes({}), (None, []))


class GatewayTests(FixtureRoot):
    @POSIX_ONLY
    def test_a_running_record_whose_process_runs_reads_as_running_with_its_own_platforms(self) -> None:
        gateway = read_gateway(self.root)
        self.assertTrue(gateway.live)
        self.assertEqual((gateway.state, gateway.pid, gateway.active_agents), ("running", os.getpid(), 2))
        platforms = {platform.name: platform.current for platform in gateway.platforms}
        self.assertEqual(platforms, {"discord": False, "slack": True}, "an entry another process stamped is old news")

    def test_a_running_record_whose_pid_is_gone_does_not_read_as_running(self) -> None:
        gateway = read_gateway(self.root / "profiles" / "miku")
        self.assertEqual(gateway.state, "running")
        self.assertFalse(gateway.live)
        self.assertEqual([platform.current for platform in gateway.platforms], [False])

    @POSIX_ONLY
    def test_a_pid_the_os_gave_to_another_process_does_not_read_as_running(self) -> None:
        record = json.loads((self.root / "gateway_state.json").read_text(encoding="utf-8"))
        for start, live in ((record["start_time"] + 10_000, False), (record["start_time"] + 150, True), (None, True)):
            with self.subTest(start=start):
                write_record(self.root, {**record, "start_time": start})
                self.assertIs(read_gateway(self.root).live, live)

    def test_a_record_naming_another_home_does_not_read_as_running_and_says_why(self) -> None:
        record = json.loads((self.root / "gateway_state.json").read_text(encoding="utf-8"))
        write_record(self.root, {**record, "hermes_home": "/srv/container/hermes"})
        gateway = read_gateway(self.root)
        self.assertEqual((gateway.live, gateway.elsewhere), (False, "/srv/container/hermes"))
        lines, _span = profile_lines(snapshot(self.environ).profiles, 0, NOW, 150, DEFAULT_THEME)
        text = "\n".join(lines_text(lines))
        self.assertIn("gateway not running here · its record is another home's (/srv/container/hermes)", text)
        self.assertNotIn(f"pid {os.getpid()} is gone", text, "its pid may well be alive: that is not the reason")

    def test_off_posix_nothing_is_probed_and_nothing_reads_as_running(self) -> None:
        # The module's flag, not `os.name`: Python 3.11's pathlib reads `os.name` to build every `Path`.
        with patch.object(bot_board, "ON_POSIX", False), patch.object(bot_board, "pid_exists") as probe:
            self.assertFalse(bot_board.process_is_record(os.getpid(), 1))
            self.assertFalse(read_gateway(self.root).live)
        probe.assert_not_called()

    @POSIX_ONLY
    def test_a_zombie_or_an_unreadable_start_time_does_not_read_as_running(self) -> None:
        record = json.loads((self.root / "gateway_state.json").read_text(encoding="utf-8"))
        started = record["start_time"]
        for facts, recorded, live in (
            ((started, True), started, False),  # a zombie: Hermes' `_pid_exists` calls it dead
            ((None, False), started, False),  # another user's process whose start time cannot be read
            ((None, False), None, True),  # a record without a start time is taken on its pid
        ):
            with self.subTest(facts=facts, recorded=recorded):
                write_record(self.root, {**record, "start_time": recorded})
                with patch.object(bot_board, "process_facts", return_value=facts):
                    self.assertIs(read_gateway(self.root).live, live)

    @POSIX_ONLY
    def test_the_start_time_of_another_users_process_is_read(self) -> None:
        # pid 1 runs as root: macOS' `proc_pidinfo` answers nothing for it, `sysctl(KERN_PROC_PID)` does.
        start, zombie = bot_board.process_facts(1)
        if sys.platform in ("darwin", "linux"):
            self.assertIsInstance(start, int)
        self.assertFalse(zombie)

    @POSIX_ONLY
    def test_a_zombie_child_reads_as_one(self) -> None:
        if sys.platform not in ("darwin", "linux"):
            self.skipTest("no zombie reader on this platform")
        child = os.fork()
        if child == 0:
            os._exit(0)
        self.addCleanup(os.waitpid, child, 0)
        deadline = time.monotonic() + 5
        while not bot_board.process_facts(child)[1] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(bot_board.process_facts(child)[1])
        self.assertTrue(bot_board.pid_exists(child), "signal 0 still answers for a zombie")
        self.assertFalse(bot_board.process_is_record(child, None))

    def test_a_missing_or_corrupt_record_says_so(self) -> None:
        self.assertEqual(read_gateway(self.root / "profiles" / "broken").problem, "gateway record unreadable")
        self.assertEqual(read_gateway(self.root / "profiles" / "ghost").problem, "no gateway record")
        write_record(self.root, "[1, 2]")
        self.assertEqual(read_gateway(self.root).problem, "gateway record unreadable")

    def test_odd_values_in_a_record_never_raise(self) -> None:
        write_record(self.root, '{"pid": 100000000000000000000, "start_time": NaN, "gateway_state": "running"}')
        self.assertFalse(read_gateway(self.root).live)
        write_record(self.root, "[" * 200_000)
        self.assertEqual(read_gateway(self.root).problem, "gateway record unreadable")

    def test_no_pid_below_two_is_ever_probed(self) -> None:
        probed: list[int] = []
        with patch.object(bot_board, "pid_exists", side_effect=lambda pid: probed.append(pid) or True):
            for pid in (0, -1, 1, True, "97968"):
                write_record(self.root, {"pid": pid, "gateway_state": "running"})
                self.assertFalse(read_gateway(self.root).live)
        self.assertEqual(probed, [])


class StoreTests(FixtureRoot):
    def test_sessions_are_counted_by_source_with_the_last_day_active_ones_and_the_last_activity(self) -> None:
        profile = read_profile("default", self.root, self.root, NOW)
        self.assertEqual(profile.store, "")
        self.assertEqual(dict(profile.sources), {"slack": 2, "cli": 1, "subagent": 1})
        self.assertEqual(profile.active, 2, "s-new (10 min) and s-child (12 min); s-cli is two days old")
        self.assertEqual(profile.last_activity, NOW - 600)

    def test_open_sessions_days_old_are_not_active(self) -> None:
        profile = read_profile("miku", self.root / "profiles" / "miku", None, NOW)
        self.assertEqual((profile.sources, profile.active), ((("slack", 3),), 0))

    def test_a_locked_store_reads_as_busy_and_does_not_wait(self) -> None:
        holder = sqlite3.connect(self.root / "profiles" / "locked" / "state.db", isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("PRAGMA locking_mode=EXCLUSIVE")
        holder.execute("BEGIN EXCLUSIVE")
        holder.execute("INSERT INTO sessions (id, source, started_at) VALUES ('l-2', 'cli', 0)")
        started = time.monotonic()
        profile = read_profile("locked", self.root / "profiles" / "locked", None, NOW)
        self.assertEqual(profile.store, "state.db busy")
        self.assertLess(time.monotonic() - started, 2.0)

    def test_a_missing_store_says_so_and_is_never_created(self) -> None:
        home = self.root / "profiles" / "broken"
        self.assertEqual(read_profile("broken", home, None, NOW).store, "no state.db")
        self.assertEqual(read_sessions(home)[2], "no state.db")
        self.assertFalse((home / "state.db").exists())

    def test_the_sessions_list_newest_activity_first_without_delegated_children(self) -> None:
        sessions, unlisted, problem = read_sessions(self.root)
        self.assertEqual(([row.id for row in sessions], unlisted, problem), (["s-new", "s-cli", "s-old"], 1, ""))
        self.assertEqual(sessions[2].last_activity, NOW - 30 * DAY, "no last_activity_at: its start")

    def test_messages_are_the_live_tail_oldest_first_with_escapes_and_controls_gone(self) -> None:
        messages, session, problem = read_messages(self.root, "s-new")
        self.assertEqual((problem, session.title, session.message_count), ("", "Release notes", 12))
        self.assertEqual(
            [(row.role, row.text) for row in messages],
            [("user", "ship it?"), ("assistant", "red done now"), ("assistant", "(calls terminal)"),
             ("tool", "terminal: exit 0")],
        )

    def test_text_that_is_not_utf8_is_shown_replaced_not_dropped(self) -> None:
        connection = sqlite3.connect(self.root / "state.db")
        connection.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('s-new', 'user', "
                           "CAST(X'6f6bff21' AS TEXT), ?)", (NOW,))
        connection.commit()
        connection.close()
        messages, _session, problem = read_messages(self.root, "s-new")
        self.assertEqual((problem, messages[-1].text), ("", "ok\ufffd!"))

    def test_the_tail_is_bounded(self) -> None:
        write_store(self.root / "profiles" / "broken", [("b", "cli", "b", NOW, NOW, 99)],
                    [("b", "user", f"line {n} " + "x" * 5000, None, None, NOW + n, 1) for n in range(99)])
        messages, _session, _problem = read_messages(self.root / "profiles" / "broken", "b")
        self.assertEqual(len(messages), MESSAGE_ROWS_READ)
        self.assertTrue(messages[-1].text.startswith("line 98 "))
        self.assertLessEqual(max(len(row.text) for row in messages), bot_board.MESSAGE_CHARS_READ)


class ReadOnlyTests(FixtureRoot):
    def test_the_store_itself_is_never_opened_outside_sqlite(self) -> None:
        # Closing any descriptor on a store drops every SQLite lock this process holds on it (POSIX record locks
        # are per process), the HUD reader's included, so the board decides how to open it from the names beside it.
        opened: list[str] = []
        real_open, real_io_open = os.open, bot_board.Path.open

        def os_open(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        def path_open(self, *args, **kwargs):
            opened.append(str(self))
            return real_io_open(self, *args, **kwargs)

        with patch.object(os, "open", side_effect=os_open), patch.object(bot_board.Path, "open", path_open):
            for view in (BoardView(), BoardView("default"), BoardView("default", "s-new"), BoardView("miku")):
                read_board(self.environ, view, NOW)
        self.assertEqual([path for path in opened if path.endswith("state.db")], [])
        self.assertTrue([path for path in opened if path.endswith("gateway_state.json")], "the probe saw the reads")


    def test_a_wal_store_a_writer_has_open_is_read_with_what_the_writer_committed(self) -> None:
        writer = sqlite3.connect(self.root / "profiles" / "miku" / "state.db")
        self.addCleanup(writer.close)
        writer.execute("INSERT INTO sessions (id, source, started_at) VALUES ('m-new', 'slack', ?)", (NOW,))
        writer.commit()
        self.assertTrue((self.root / "profiles" / "miku" / "state.db-wal").exists())
        profile = read_profile("miku", self.root / "profiles" / "miku", None, NOW)
        self.assertEqual((profile.sources, profile.active), ((("slack", 4),), 1))

    @POSIX_ONLY
    def test_the_board_opens_no_write_connection_sends_only_signal_zero_and_changes_no_file(self) -> None:
        before = tree_state(self.root)
        connects: list[tuple[str, dict]] = []
        signals: list[tuple[int, int]] = []
        real_connect, real_kill = sqlite3.connect, os.kill

        def connect(database, *args, **options):
            connects.append((str(database), options))
            return real_connect(database, *args, **options)

        def kill(pid, signal):
            signals.append((pid, signal))
            return real_kill(pid, signal) if signal == 0 else None

        with patch.object(sqlite_readonly.sqlite3, "connect", side_effect=connect), patch.object(os, "kill", side_effect=kill):
            for view in (BoardView(), BoardView("default"), BoardView("default", "s-new"), BoardView("miku"),
                         BoardView("locked"), BoardView("broken"), BoardView("broken", "nope")):
                read_board(self.environ, view, NOW)
        self.assertTrue(connects)
        for database, options in connects:
            self.assertTrue(database.startswith("file:"), database)
            self.assertIn(database.rsplit("?", 1)[1], ("mode=ro", "mode=ro&immutable=1"))
            self.assertIs(options.get("uri"), True)
        self.assertTrue(signals)
        self.assertEqual({signal for _pid, signal in signals}, {0})
        self.assertEqual(tree_state(self.root), before)


class SummaryTests(FixtureRoot):
    """The model, token, memory and gateway summaries: what each reads, and what it says when it cannot."""

    def profile(self, name: str = "default"):
        home = self.root if name == "default" else self.root / "profiles" / name
        return read_profile(name, home, self.root, NOW)

    def line(self, profile, index: int) -> str:
        """Level 1's text for `profile`: 0 is its gateway's row; -3, -2 and -1 its model, memory and blank rows."""
        lines, _span = profile_lines([profile], 0, NOW, 400, DEFAULT_THEME)
        return lines_text(lines)[2:][index]

    def test_the_configured_model_and_the_one_the_newest_session_ran(self) -> None:
        write_usage_store(self.root, [
            ("new", "slack", "t", NOW - 60, 2, "z-ai/glm-5.3", 10, 5, 0, 0.01, None, "estimated"),
            ("child", "subagent", "t", NOW - 10, 2, "child-model", 10, 5, 0, 0.0, None, "included"),
            ("old", "cli", "t", NOW - 3 * DAY, 2, "claude-opus-5-5", 10, 5, 0, 0.0, None, "included"),
        ])
        profile = self.profile()
        self.assertEqual((profile.model, profile.provider, profile.ran), ("claude-opus-5-5", "claude-proxy", "z-ai/glm-5.3"))
        row = self.line(profile, -3)
        self.assertTrue(row.startswith("    claude-opus-5-5 via claude-proxy · 2 active · "), row)
        self.assertTrue(row.endswith(" · newest ran z-ai/glm-5.3"), "the cell most rows leave empty comes last")
        write_usage_store(self.root, [("same", "cli", "t", NOW - 60, 2, "claude-opus-5-5", 1, 1, 0, None, None, None)])
        self.assertNotIn("newest ran", self.line(self.profile(), -3), "the same model is not said twice")

    def test_a_model_written_as_a_string_or_not_at_all(self) -> None:
        for config, model in (("model: gpt-5.5\n", "gpt-5.5"), ("model:\n  model: kimi-k3\n", "kimi-k3"), ("agent: {}\n", "")):
            with self.subTest(config=config):
                (self.root / "config.yaml").write_text(config, encoding="utf-8")
                self.assertEqual(self.profile().model, model)
        self.assertTrue(self.line(self.profile(), -3).startswith("    no default model configured"))

    def test_a_config_it_cannot_read_says_so_and_names_no_model_or_limit(self) -> None:
        config = self.root / "config.yaml"
        for content, said in (
            (b"model:\n\tdefault: tabbed\n", "config.yaml unreadable"),  # YAML indents with spaces
            (b"model:\n  default: \xff\xfe\n", "config.yaml unreadable"),
            (b"#" * (bot_board.CONFIG_BYTES + 1), "config.yaml too large to read (1 MB)"),
        ):
            with self.subTest(said=said, size=len(content)):
                config.write_bytes(content)
                profile = self.profile()
                self.assertEqual((profile.config, profile.model), (said, ""))
                self.assertTrue(self.line(profile, -3).startswith(f"    {said}"))
        config.unlink()
        profile = self.profile()
        self.assertEqual(profile.config, "no config.yaml")
        self.assertEqual([memory.limit for memory in profile.memory], [None, None])
        self.assertIsNone(profile.omh_records, "no config: no store is named, and none is guessed")

    def test_the_active_sessions_tokens_and_only_the_cost_hermes_priced(self) -> None:
        write_usage_store(self.root, [
            ("a", "slack", "t", NOW - 60, 2, "m", 1_000_000, 200_000, 12_400_000, 1.5, 4.0, "actual"),
            ("b", "slack", "t", NOW - 120, 2, "m", 200_000, 34_500, 0, 0.12, None, "estimated"),
            ("c", "cli", "t", NOW - 180, 2, "m", 50_000, 10_000, 0, 0.0, None, "unknown"),
            ("d", "cli", "t", NOW - 240, 2, "m", 1_000, 0, 0, 0.0, None, "included"),
            ("e", "cli", "t", NOW - 300, 2, "m", 0, 0, 0, None, None, None),  # nothing used yet: not unpriced
            ("old", "cli", "t", NOW - 3 * DAY, 2, "m", 9_000_000, 9_000_000, 0, 99.0, None, "estimated"),
        ])
        profile = self.profile()
        self.assertEqual(profile.active, 5)
        self.assertEqual(profile.usage, bot_board.Usage(1_251_000, 244_500, 12_400_000, 4.12, True, 1, 1))
        self.assertTrue(self.line(profile, -3).endswith(
            " · 5 active · 1.3M in · 244.5k out · 12.4M cache read · ~$4.12 + 1 unpriced · newest ran m"))

    def test_a_cost_hermes_never_priced_is_never_shown_as_zero(self) -> None:
        for statuses, said in (
            (("unknown", "unknown"), "cost unknown"), (("included", "included"), "included"),
            (("actual", "actual"), "$0.00"), ((None, None), "cost unknown"),
        ):
            with self.subTest(statuses=statuses):
                write_usage_store(self.root, [
                    (f"s{n}", "cli", "t", NOW - 60, 2, "m", 10, 10, 0, 0.0, None, status) for n, status in enumerate(statuses)
                ])
                self.assertTrue(self.line(self.profile(), -3).endswith(f"20 in · 20 out · 0 cache read · {said} · newest ran m"))

    def test_older_stores_without_the_columns_show_less_and_no_number_they_lack(self) -> None:
        # The fixture's own store has no model, token or cost column at all.
        profile = self.profile()
        self.assertEqual((profile.usage, profile.ran), (None, ""))
        self.assertEqual(self.line(profile, -3), "    claude-opus-5-5 via claude-proxy")
        sessions = snapshot(self.environ, BoardView("default"))
        self.assertEqual((sessions.sessions[0].model, sessions.sessions[0].usage), ("", None))
        self.assertTrue(squeeze(lines_text(session_lines(sessions, 0, NOW, 150, DEFAULT_THEME)[0])[2]).endswith(
            "Release notes slack 10m ago 12 messages"))
        tail = snapshot(self.environ, BoardView("default", "s-new"))
        self.assertEqual(lines_text(message_lines(tail, tail.sessions[0], NOW, 150, DEFAULT_THEME))[2], "")
        write_usage_store(self.root, [("t", "cli", "t", NOW - 60, 2, 5, 7)], "input_tokens INTEGER, output_tokens INTEGER")
        profile = self.profile()
        self.assertEqual(profile.usage, bot_board.Usage(5, 7, None, None, False, 0, 1))
        self.assertTrue(self.line(profile, -3).endswith("1 active · 5 in · 7 out · cost unknown"), "no cache read column")

    def test_levels_two_and_three_show_each_sessions_model_tokens_and_cost(self) -> None:
        write_usage_store(self.root, [
            ("a", "slack", "Release notes", NOW - 60, 12, "anthropic/claude-opus-5-5", 325_170, 89_019, 12_469_303, 0.4321, None, "estimated"),
            ("b", "cli", "Probe", NOW - 120, 2, "glm-5.3", 192, 67_200, 0, 0.0, None, "unknown"),
        ])
        sessions = snapshot(self.environ, BoardView("default"))
        rows = lines_text(session_lines(sessions, 0, NOW, 150, DEFAULT_THEME)[0])
        self.assertEqual(squeeze(rows[2]), "❯ Release notes claude-opus-5-5 325.2k in 89k out ~$0.43 slack 1m ago 12 messages")
        self.assertEqual(squeeze(rows[3]), "Probe glm-5.3 192 in 67.2k out cli 2m ago 2 messages", "no unpriced cost")
        tail = snapshot(self.environ, BoardView("default", "a"))
        head = lines_text(message_lines(tail, tail.sessions[0], NOW, 150, DEFAULT_THEME))[:3]
        self.assertEqual(head[2], "anthropic/claude-opus-5-5 · 325.2k in · 89k out · 12.5M cache read · ~$0.43")

    def test_memory_is_counted_as_hermes_counts_it_against_the_configured_limits(self) -> None:
        memory, user = self.profile().memory
        joined = len("User ships on Fridays\n§\nPrefers short answers")  # entries stripped, joined by the delimiter
        self.assertEqual(memory, bot_board.MemoryFile("memory", 2, joined, 2200, True))
        self.assertEqual(user, bot_board.MemoryFile("user", 0, 0, 1375, True), "no USER.md yet: empty")
        self.assertEqual(
            self.line(self.profile(), -2),
            f"    memory 2 entries, {joined}/2,200 chars · user profile empty · OMH memory 2 records",
        )
        write_memory(self.root, "USER.md", ["x" * 1400])
        profile = self.profile()
        lines, _span = profile_lines([profile], 0, NOW, 400, DEFAULT_THEME)
        over = next(span for span in lines[-2] if "1,400/1,375" in span.text)
        self.assertIs(over.style, DEFAULT_THEME.warn, "over its limit")

    def test_memory_that_is_off_unreadable_or_huge_says_so_without_counting(self) -> None:
        config = ROOT_CONFIG.replace("memory_enabled: true", "memory_enabled: false")
        (self.root / "config.yaml").write_text(config.replace("  user_char_limit: 1375\n", ""), encoding="utf-8")
        write_memory(self.root, "USER.md", b"caf\xe9")
        self.assertEqual(self.line(self.profile(), -2).split(" · ")[:2], ["    memory off", "user profile unreadable"])
        write_memory(self.root, "USER.md", ["a", "b"])
        self.assertIn("user profile 2 entries, 5 chars ·", self.line(self.profile(), -2), "no limit configured: none shown")
        huge = write_memory(self.root, "MEMORY.md", b"x" * (2 * bot_board.MEMORY_FILE_BYTES))
        (self.root / "config.yaml").write_text(ROOT_CONFIG, encoding="utf-8")
        read: list[int] = []
        real_open = bot_board.Path.open

        def path_open(path, *args, **kwargs):
            handle = real_open(path, *args, **kwargs)
            if path == huge:
                real_read = handle.read
                handle.read = lambda size=-1: read.append(size) or real_read(size)
            return handle

        with patch.object(bot_board.Path, "open", path_open):
            memory = self.profile().memory[0]
        self.assertEqual((memory.problem, memory.entries), ("too large to read (2 MB)", 0))
        self.assertEqual(read, [bot_board.MEMORY_FILE_BYTES + 1], "one bounded read")
        self.assertTrue(self.line(self.profile(), -2).startswith("    memory too large to read (2 MB) · "))

    def test_the_omh_store_is_the_one_config_names_and_only_regular_json_files_count(self) -> None:
        records = self.root / "omh-store" / "memory" / "records"
        with contextlib.suppress(OSError):  # Windows without the symlink privilege: the count is the same
            (records / "linked.json").symlink_to(records / "mem_a.json")
        (records / "dir.json").mkdir()
        self.assertEqual(self.profile().omh_records, 2)
        for setting, records_seen, said in (
            ("$HERMES_HOME/omh-store", 2, ""),
            ("${OMH_SECRET}/store", None, "OMH store not resolvable"),  # read from no environment
            (str(self.root / "nowhere"), None, "OMH store not found"),
            (str(self.root), 0, ""),  # a store with no records yet
        ):
            with self.subTest(setting=setting):
                (self.root / "config.yaml").write_text(ROOT_CONFIG.replace("omh-store", setting), encoding="utf-8")
                profile = self.profile()
                self.assertEqual((profile.omh_records, profile.omh), (records_seen, said))
        (self.root / "config.yaml").write_text(ROOT_CONFIG.split("plugins:")[0], encoding="utf-8")
        self.assertNotIn("OMH", self.line(self.profile(), -2), "no setting: no store named, ~/.omh not guessed")

    @POSIX_ONLY
    def test_a_live_gateway_shows_its_uptime_memory_and_record_age(self) -> None:
        record = json.loads((self.root / "gateway_state.json").read_text(encoding="utf-8"))
        # 75 s before the board's clock (NOW is 2027-01-15 08:00:00 UTC).
        write_record(self.root, {**record, "updated_at": "2027-01-15T07:58:45Z", "restart_requested": True})
        now = time.time()
        gateway = read_gateway(self.root, now)
        self.assertEqual((gateway.live, gateway.updated, gateway.restart_requested), (True, NOW - 75, True))
        header = self.line(self.profile(), 0)
        self.assertIn("· updated 1m ago · restart requested", header)
        if sys.platform not in ("darwin", "linux"):
            self.assertEqual((gateway.started, gateway.rss), (None, None))
            return
        # The record's start time is this process's own: its age is this process's.
        self.assertLess(abs(gateway.started - bot_board.started_at(bot_board.process_facts(os.getpid())[0], now)), 5)
        self.assertLess(now - gateway.started, 7 * DAY, "this test process started recently")
        self.assertGreater(gateway.rss, 1 << 20, "this process holds more than a megabyte")
        self.assertRegex(header, r"gateway running · pid \d+ · 2 agents running · up \S+.* · RSS [\d.]+ [KMG]B · ")

    def test_a_gateway_that_is_not_running_shows_only_how_old_its_record_is(self) -> None:
        miku = self.root / "profiles" / "miku"
        record = json.loads((miku / "gateway_state.json").read_text(encoding="utf-8"))
        write_record(miku, {**record, "updated_at": NOW - 3 * DAY})
        with patch.object(bot_board, "resident_bytes") as rss:
            header = self.line(self.profile("miku"), 0)
        rss.assert_not_called()
        self.assertTrue(header.endswith("its record still says running (pid 4194301 is gone) · updated 3d ago"), header)
        self.assertNotIn(" up ", header)

    def test_record_times_are_read_as_hermes_reads_them(self) -> None:
        at = datetime(2026, 10, 26, 4, 14, 34, tzinfo=timezone.utc).timestamp()
        for value, expected in (
            ("2026-10-26T04:14:34+00:00", at), ("2026-10-26T04:14:34Z", at), ("2026-10-26T04:14:34", at),
            ("2026-10-26T13:14:34+09:00", at), (at, at),
            (12, None), (True, None), ("yesterday", None), (None, None), (float("nan"), None),
        ):
            with self.subTest(value=value):
                self.assertEqual(bot_board._record_time(value), expected)

    def test_compact_numbers(self) -> None:
        self.assertEqual(
            [bot_board._bytes_text(size) for size in (512, 12 * 1024, 1536 * 1024, 412 << 20, int(1.2 * (1 << 30)))],
            ["512 B", "12 KB", "1.5 MB", "412 MB", "1.2 GB"],
        )
        self.assertEqual([bot_board._duration_text(s) for s in (45, 720, 12_000, 190_000)], ["45s", "12m", "3h 20m", "2d 4h"])
        self.assertEqual(bot_board._cost_text(bot_board.Usage(cost=0.001)), "<$0.01")


class TableTests(FixtureRoot):
    """Level 2 is a table under one band, and level 1's summaries line up across profiles."""

    def sessions(self) -> BoardSnapshot:
        write_usage_store(self.root, [
            ("a", "telegram", "Rehearsal schedule for the anniversary", NOW - 60, 140, "moonshot/kimi-k3.1-pro",
             302_000, 41_200, 0, 0.52, None, "estimated"),
            ("b", "x", "Poll", NOW - 2 * 3600, 9, "glm-5.3", 192, 4_100, 0, None, None, "unknown"),
            ("c", "discord", "Merch drop", NOW - 3 * DAY, 12, "deepseek/deepseek-v4.1-flash", 44_600, 9_300, 0, 0.0, None, "included"),
        ])
        return snapshot(self.environ, BoardView("default"))

    def test_level_two_columns_line_up_and_the_band_spans_every_row_alike(self) -> None:
        sessions = self.sessions()
        for cols in (150, 80):
            with self.subTest(cols=cols):
                lines, _span = session_lines(sessions, 1, NOW, cols, DEFAULT_THEME)
                rows = lines[2:]
                self.assertEqual({line_width(row) for row in rows}, {cols}, "every row runs to the edge, never wraps")
                text = lines_text(rows)
                for unit in (" out", " ago", " messages"):
                    if unit in text[0]:
                        self.assertEqual(len({row.index(unit) for row in text}), 1, f"{unit!r} ends one column")
                models = {next(row.index(name) for name in ("kimi", "glm", "deepseek") if name in row) for row in text}
                self.assertEqual(len(models), 1, "the title column is one width, the model column starts alike")
                for index, row in enumerate(rows):
                    band = DEFAULT_THEME.menu_current if index == 1 else DEFAULT_THEME.menu
                    self.assertEqual({span.style.bg for span in row}, {band.bg}, "one band from the mark to the edge")
        rows = lines_text(session_lines(sessions, 0, NOW, 150, DEFAULT_THEME)[0])[2:]
        ends = {row.index(" in") for row in rows}
        self.assertEqual(len(ends), 1, "token counts are right-aligned on their unit")
        self.assertIn("   192 in", rows[1])
        self.assertEqual(len({row.index("ago") for row in rows}), 1)
        self.assertEqual(rows[0].index("~$0.52") + len("~$0.52"), rows[2].index("included") + len("included"), "cost too")
        self.assertIn("Rehearsal schedule for the anniversary", rows[0])
        narrow = lines_text(session_lines(sessions, 0, NOW, 70, DEFAULT_THEME)[0])[2]
        self.assertIn("…", narrow, "the title gives way, cut with an ellipsis")

    def test_a_narrow_board_gives_up_source_then_cost_then_tokens(self) -> None:
        table = bot_board._session_table(self.sessions().sessions, NOW)
        seen: list[list[str]] = []
        for cols in range(200, 20, -1):
            keep = bot_board._session_columns(table, cols)
            if not seen or keep != seen[-1]:
                seen.append(keep)
        everything = ["model", "input", "output", "cost", "source", "age", "messages"]
        self.assertEqual(seen, [
            everything,
            ["model", "input", "output", "cost", "age", "messages"],
            ["model", "input", "output", "age", "messages"],
            ["model", "age", "messages"],
        ])
        rows = session_lines(self.sessions(), 0, NOW, 30, DEFAULT_THEME)[0][2:]
        self.assertEqual({line_width(row) for row in rows}, {30}, "past every drop, the title alone, still one row")

    def test_a_column_no_session_fills_is_left_out(self) -> None:
        sessions = snapshot(self.environ, BoardView("default"))  # a store with no model or token column
        self.assertEqual(bot_board._session_columns(bot_board._session_table(sessions.sessions, NOW), 150),
                         ["source", "age", "messages"])

    def test_level_one_summaries_line_up_across_profiles(self) -> None:
        write_usage_store(self.root, [("a", "cli", "t", NOW - 60, 2, "m", 1_000_000, 200_000, 0, 1.0, None, "actual")])
        miku = self.root / "profiles" / "miku"
        (miku / "config.yaml").write_text("model:\n  default: deepseek-v4.1-flash-ultrafast\n", encoding="utf-8")
        write_usage_store(miku, [("m", "cli", "t", NOW - 60, 2, "m", 7, 3, 0, None, None, "unknown")])
        write_memory(miku, "MEMORY.md", ["one"])
        profiles = [read_profile(name, home, self.root, NOW) for name, home in (("default", self.root), ("miku", miku))]
        lines, _span = profile_lines(profiles, 0, NOW, 300, DEFAULT_THEME)
        text = lines_text(lines)
        models = [row for row in text if " active · " in row]
        memories = [row for row in text if row.startswith("    memory ")]
        self.assertEqual(len(models), 2)
        self.assertEqual(len({row.index(" active · ") for row in models}), 1, models)
        self.assertEqual(len({row.index(" out") for row in models}), 1, "numbers right-aligned on their unit")
        self.assertEqual(len({row.index("user profile") for row in memories}), 1, memories)
        self.assertFalse([row for row in text if row != row.rstrip()], "no row ends in padding")


def squeeze(text: str) -> str:
    """A row's words, each run of padding one space: what it says, apart from where it says it."""
    return " ".join(text.split())


def snapshot(environ: dict, view: BoardView = BoardView()) -> BoardSnapshot:
    return read_board(environ, view, NOW)


class DrawingTests(FixtureRoot):
    @POSIX_ONLY
    def test_level_one_says_what_each_gateway_and_store_is_at_any_width(self) -> None:
        with patch.object(bot_board, "pid_exists", side_effect=lambda pid: pid == os.getpid()):
            profiles = snapshot(self.environ).profiles
        for cols in (40, 150):
            with self.subTest(cols=cols):
                lines, span = profile_lines(profiles, 1, NOW, cols, DEFAULT_THEME)
                self.assertLessEqual(max(line_width(line) for line in lines), cols)
                if cols == 150:
                    text = "\n".join(lines_text(lines))
                    self.assertIn(BOARD_TITLE, text)
                    self.assertIn("● default (this TUI)  gateway running · pid", text)
                    self.assertIn("2 agents running", text)
                    self.assertIn("slack: connected\n", text + "\n")
                    self.assertIn("discord: connected when last recorded", text)
                    self.assertIn("4 sessions · 2 active in the last 24 h · last active 10m ago", text)
                    self.assertIn("◌ miku  gateway not running · its record still says running", text)
                    self.assertIn("○ broken  gateway record unreadable", text)
                    self.assertIn("sessions: no state.db", text)
                    self.assertIn("○ locked  gateway stopped (signal)", text)
                    self.assertTrue(lines_text(lines)[span[0]].startswith("❯ "), "the highlighted profile is marked")

    @POSIX_ONLY
    def test_a_profile_the_default_gateway_serves_reads_as_served(self) -> None:
        record = json.loads((self.root / "gateway_state.json").read_text(encoding="utf-8"))
        write_record(self.root, {**record, "served_profiles": ["default", "miku"]})
        lines, _span = profile_lines(snapshot(self.environ).profiles, 0, NOW, 150, DEFAULT_THEME)
        self.assertIn("● miku  served by the default gateway", "\n".join(lines_text(lines)))

    def test_levels_two_and_three_fit_any_width_and_show_the_tail(self) -> None:
        sessions = snapshot(self.environ, BoardView("default"))
        messages = snapshot(self.environ, BoardView("default", "s-new"))
        for cols in (40, 150):
            with self.subTest(cols=cols):
                listed, span = session_lines(sessions, 0, NOW, cols, DEFAULT_THEME)
                tail = message_lines(messages, sessions.sessions[0], NOW, cols, DEFAULT_THEME)
                self.assertLessEqual(max(line_width(line) for line in listed + tail), cols)
        listed, span = session_lines(sessions, 0, NOW, 150, DEFAULT_THEME)
        text = lines_text(listed)
        self.assertIn("1 subagent session not listed", text[0])
        self.assertTrue(text[span[0]].startswith("❯ Release notes"))
        self.assertTrue(squeeze(text[span[0]]).endswith("slack 10m ago 12 messages"))
        tail = "\n".join(lines_text(message_lines(messages, sessions.sessions[0], NOW, 150, DEFAULT_THEME)))
        self.assertIn("default › Release notes", tail)
        self.assertIn("assistant red done now", tail)
        self.assertNotIn("pwned", tail)
        self.assertNotIn("\x1b", tail)


class FakeReader:
    """A `BoardReader` that reads at once on this thread, and records what the board asked."""

    instances: list[FakeReader] = []

    def __init__(self, environ, *, clock=time.time) -> None:
        self.environ = environ
        self.views: list[BoardView] = []
        self.refreshes = 0
        self.started = self.stopped = self.closed = False
        self.view = BoardView()
        self.pending: BoardSnapshot | None = None
        FakeReader.instances.append(self)

    def start(self, wake=None) -> None:
        self.started, self.stopped = True, False
        self.view = BoardView()
        self.pending = read_board(self.environ, self.view, NOW)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    def show(self, view: BoardView) -> None:
        self.views.append(view)
        self.view = view
        self.pending = read_board(self.environ, view, NOW)

    def refresh(self) -> None:
        self.refreshes += 1

    def take(self) -> BoardSnapshot | None:
        pending, self.pending = self.pending, None
        return pending


class ReaderThreadTests(FixtureRoot):
    def test_the_thread_reads_at_start_and_for_each_view_and_stops(self) -> None:
        woken: list[int] = []
        reader = bot_board.BoardReader(self.environ, clock=lambda: NOW)
        reader.start(lambda: woken.append(1))
        self.addCleanup(reader.stop)
        first = self.wait_for(reader)
        self.assertEqual((first.view, len(first.profiles)), (BoardView(), 4))
        reader.show(BoardView("default"))
        second = self.wait_for(reader, lambda snapshot: snapshot.view == BoardView("default"))
        self.assertEqual([row.id for row in second.sessions], ["s-new", "s-cli", "s-old"])
        self.assertTrue(woken)
        reader.stop()

    def wait_for(self, reader, accept=lambda snapshot: True) -> BoardSnapshot:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            snapshot = reader.take()
            if snapshot is not None and accept(snapshot):
                return snapshot
            time.sleep(0.01)
        self.fail("no snapshot arrived")

    def test_a_read_that_raises_is_drawn_as_a_named_failure(self) -> None:
        def broken(environ, view, now):
            raise RuntimeError("boom")

        reader = bot_board.BoardReader(self.environ, read=broken, clock=lambda: NOW)
        reader._open = True
        reader.poll_once()
        snapshot = reader.take()
        self.assertEqual((snapshot.problem, snapshot.read_at), ("the board could not be read: RuntimeError", NOW))
        board = BotBoard("bots", clock=lambda: NOW + 120)
        board.take(snapshot)
        board.frame(80, 10, DEFAULT_THEME)
        self.assertEqual(lines_text(board.lines), ["the board could not be read: RuntimeError"])
        self.assertIn("read 2m ago", line_text(board.footer("1-1/1", DEFAULT_THEME)[1]))

    def test_a_closed_board_reads_nothing_and_opening_it_again_reuses_the_thread(self) -> None:
        reads: list[BoardView] = []

        def read(environ, view, now):
            reads.append(view)
            return BoardSnapshot(view, (), read_at=now)

        reader = bot_board.BoardReader(self.environ, read=read)
        reader.start()
        self.addCleanup(reader.close)
        thread = reader._thread
        self.wait_for(reader)
        reader.stop()
        settled = len(reads)
        time.sleep(0.2)
        self.assertEqual(len(reads), settled, "nothing is read while the board is closed")
        reader.show(BoardView("miku"))
        reader.start()
        self.assertIs(reader._thread, thread)
        self.assertEqual(self.wait_for(reader).view, BoardView(), "it opens again at the profiles")

    def test_a_read_finished_after_the_board_closed_is_dropped(self) -> None:
        reader = bot_board.BoardReader(self.environ)
        reader._open = True

        def slow(environ, view, now):
            reader.stop()  # the person closed the board while this read ran
            return BoardSnapshot(view, ())

        reader._read = slow
        reader.poll_once()
        self.assertIsNone(reader.take())

    def test_a_read_finished_after_the_view_changed_is_dropped(self) -> None:
        reader = bot_board.BoardReader(self.environ)

        def slow(environ, view, now):
            reader.show(BoardView("miku"))  # the person moved while this read ran
            return BoardSnapshot(view, ())

        reader._open = True
        reader._read = slow
        reader.poll_once()
        self.assertIsNone(reader.take())


class BoardKeysTests(FixtureRoot):
    def board(self) -> tuple[BotBoard, FakeReader]:
        reader = FakeReader(self.environ)
        board = BotBoard("bots", show=reader.show, refresh=reader.refresh, clock=lambda: NOW)
        reader.start()
        board.take(reader.take())
        return board, reader

    def press(self, board: BotBoard, reader: FakeReader, *keys: Key) -> bool:
        stays = True
        for key in keys:
            stays = board.handle_key(key, 24)
            arrived = reader.take()
            if arrived is not None:
                board.take(arrived)
            board.frame(100, 24, DEFAULT_THEME)
        return stays

    def test_down_enter_left_walk_the_levels_and_q_closes_from_the_top(self) -> None:
        board, reader = self.board()
        board.frame(100, 24, DEFAULT_THEME)
        self.press(board, reader, Key(DOWN), Key(DOWN), Key(DOWN), Key(UP))
        self.assertEqual(board.profile_index, 2)
        self.press(board, reader, Key(ENTER))
        self.assertEqual(reader.views[-1], BoardView("locked"))
        self.press(board, reader, Key(LEFT))
        self.assertEqual(reader.views[-1], BoardView())
        self.press(board, reader, Key(UP), Key(UP), Key(ENTER), Key(ENTER))
        self.assertEqual(reader.views[-1], BoardView("default", "s-new"))
        page = "\n".join(lines_text(board.lines))
        self.assertIn("default › Release notes", page)
        self.assertTrue(self.press(board, reader, Key(ESCAPE)))
        self.assertEqual(reader.views[-1], BoardView("default"))
        self.assertTrue(self.press(board, reader, Key(CHAR, "q")))
        self.assertFalse(self.press(board, reader, Key(CHAR, "q")), "q on the profiles closes the board")

    def test_r_reads_again_and_the_footer_says_how_old_the_reading_is(self) -> None:
        board, reader = self.board()
        self.press(board, reader, Key(CHAR, "r"))
        self.assertEqual(reader.refreshes, 1)
        footer = [line_text(line) for line in board.footer("1-9/9", DEFAULT_THEME)]
        self.assertIn("read just now", footer[1])
        self.assertIn("read-only", footer[1])

    def test_the_highlight_stays_on_screen_on_a_short_terminal(self) -> None:
        board, reader = self.board()
        for _ in range(3):
            board.handle_key(Key(DOWN), 8)
        screen = board.frame(80, 8, DEFAULT_THEME)
        self.assertIn("❯", screen)

    def test_the_highlight_stays_on_its_session_when_the_list_reorders(self) -> None:
        board, reader = self.board()
        self.press(board, reader, Key(ENTER))  # default's sessions: s-new, s-cli, s-old
        self.press(board, reader, Key(DOWN))
        self.assertEqual(board.highlighted_session, "s-cli")
        connection = sqlite3.connect(self.root / "state.db")
        connection.execute("UPDATE sessions SET last_activity_at = ? WHERE id = 's-old'", (NOW,))
        connection.commit()
        connection.close()
        board.take(read_board(self.environ, BoardView("default"), NOW))  # the next refresh: s-old, s-new, s-cli
        board.frame(100, 24, DEFAULT_THEME)
        self.assertEqual([row.id for row in board.snapshot.sessions], ["s-old", "s-new", "s-cli"])
        self.assertTrue(lines_text(board.lines)[board._span[0]].startswith("❯ Local run"))
        self.press(board, reader, Key(ENTER))
        self.assertEqual(reader.views[-1], BoardView("default", "s-cli"), "Enter opens the row the highlight is on")

    def test_back_to_a_long_list_brings_the_highlight_on_screen_once_the_list_arrives(self) -> None:
        write_store(self.root / "profiles" / "broken",
                    [(f"b-{n:02d}", "cli", f"chat {n:02d}", NOW - n, NOW - n, 1) for n in range(40)])
        held: list[BoardSnapshot] = []
        board = BotBoard("bots", show=lambda view: held.append(read_board(self.environ, view, NOW)), clock=lambda: NOW)
        board.take(read_board(self.environ, BoardView(), NOW))
        board.highlighted_profile = "broken"
        board.frame(100, 12, DEFAULT_THEME)
        board.handle_key(Key(ENTER), 12)
        board.take(held.pop())
        for _ in range(30):
            board.handle_key(Key(DOWN), 12)
        board.frame(100, 12, DEFAULT_THEME)
        board.handle_key(Key(ENTER), 12)  # level 3 of chat 30
        board.take(held.pop())
        board.frame(100, 12, DEFAULT_THEME)
        board.handle_key(Key(LEFT), 12)  # back: the reader has not answered yet
        board.frame(100, 12, DEFAULT_THEME)
        self.assertEqual(lines_text(board.lines), ["broken · reading…"])
        board.take(held.pop())  # the list arrives a frame later
        board.frame(100, 12, DEFAULT_THEME)
        shown = lines_text(board.lines)[board.top : board.top + board.body_rows(12)]
        self.assertTrue([row for row in shown if row.startswith("❯ chat 30")], shown)

    def test_level_three_heads_with_the_sessions_row_as_read_now(self) -> None:
        board, reader = self.board()
        self.press(board, reader, Key(ENTER), Key(ENTER))
        connection = sqlite3.connect(self.root / "state.db")
        connection.execute("UPDATE sessions SET message_count = 99 WHERE id = 's-new'")
        connection.commit()
        connection.close()
        board.take(read_board(self.environ, BoardView("default", "s-new"), NOW))
        board.frame(100, 24, DEFAULT_THEME)
        self.assertIn("slack · 99 messages", "\n".join(lines_text(board.lines)))

    def test_a_snapshot_for_another_view_is_not_drawn_as_this_one(self) -> None:
        board, reader = self.board()
        board.view = BoardView("miku")
        board.frame(100, 24, DEFAULT_THEME)
        self.assertEqual(lines_text(board.lines), ["miku · reading…"])


class AppTests(FixtureRoot):
    def app(self):
        FakeReader.instances = []
        app, client = make_app(environ=self.environ)
        app.make_board_reader = FakeReader
        return app, client

    def test_ctrl_s_from_the_composer_opens_the_board_and_q_closes_it_and_stops_its_reader(self) -> None:
        app, client = self.app()
        type_text(app, "draft")
        app._on_input(CTRL_S)
        self.assertIsInstance(app.pager, BotBoard)
        app._take_board()
        app._render()
        screen = "\n".join(app.terminal.screen.lines())
        self.assertIn(BOARD_TITLE, screen)
        self.assertIn("default (this TUI)", screen)
        app._on_input(Key(CHAR, "q"))
        self.assertIsNone(app.pager)
        self.assertTrue(FakeReader.instances[0].stopped)
        self.assertEqual(app.composer.text, "draft")
        app._on_input(CTRL_S)
        self.assertEqual(len(FakeReader.instances), 1, "the board opened again reuses its one reader")
        self.assertFalse(FakeReader.instances[0].stopped)
        self.assertEqual(client.calls, [], "the board sends nothing to the gateway")

    def test_bots_opens_the_board_and_leaves_no_echo(self) -> None:
        app, client = self.app()
        send(app, "/bots")
        self.assertIsInstance(app.pager, BotBoard)
        self.assertEqual(commands(app), [], "it only opens a pager: nothing to attribute")
        self.assertEqual(client.calls, [])

    def test_ctrl_s_inside_the_history_search_does_not_open_the_board(self) -> None:
        app, _client = self.app()
        app.composer.history = ["ls -la"]
        app._on_input(Key(CHAR, "r", ctrl=True))
        app._on_input(CTRL_S)
        self.assertIsNone(app.pager)
        self.assertIsInstance(app.search, HistorySearch)
        self.assertEqual(FakeReader.instances, [])

    def test_ctrl_s_inside_another_pager_does_nothing(self) -> None:
        app, _client = self.app()
        app._keys("")
        keys = app.pager
        app._on_input(CTRL_S)
        self.assertIs(app.pager, keys)

    def test_shutdown_closes_the_boards_reader(self) -> None:
        app, _client = self.app()
        app._on_input(CTRL_S)
        app._shutdown()
        self.assertTrue(FakeReader.instances[0].stopped)
        self.assertTrue(FakeReader.instances[0].closed)


if __name__ == "__main__":
    unittest.main()
