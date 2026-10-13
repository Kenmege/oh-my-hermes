"""The OMH TUI's HUD panels: the poller's session and cadence rules, and the rows under a budget.

Plan payloads come from the real `read_omh_hud` over a temp OMH home seeded
through the todo store, the way `test_tui_widget_pack.py` seeds the widget's.
Agent rows and the subagent graph are the reader's documented row shapes laid
onto that payload, since seeding a Hermes state.db is the reader's own tests'
job. Where node is available, one case renders the same payloads through the
shipped widget and requires the same row text, so the port cannot drift from
the look the widget tests pin.
"""

from __future__ import annotations

from collections.abc import Callable
import copy
from datetime import datetime, timezone
import json
from importlib import resources
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import threading
import time
import unicodedata
import unittest

from omh.plugin_bundle.omh.runtime_reader import read_omh_hud
from omh.plugin_bundle.omh.todo_store import build_todo_record, write_todo
from omh.tui.panels import (
    HUD_POLL_MAX_SECONDS,
    HUD_POLL_MIN_SECONDS,
    HudPoller,
    HudSnapshot,
    panel_lines,
    status_segments,
)
from omh.tui.theme import DEFAULT_THEME
from omh.tui.width import line_width
from omh.tui_widget_pack import INTERPRETER_MARKER
from omh.version import __version__

NODE = shutil.which("node")
THEME = DEFAULT_THEME


def text(line) -> str:
    return "".join(span.text for span in line)


def texts(lines) -> list[str]:
    return [text(line) for line in lines]


def hud_payload(items: list[dict] | None = None, *, title: str = "Plan") -> dict:
    """A real `read_omh_hud` payload over a temp OMH home holding `items` as its plan."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        omh_home = root / "omh"
        hermes_home = root / "hermes"
        omh_home.mkdir()
        hermes_home.mkdir()
        if items:
            write_todo(omh_home, build_todo_record(title, items, source="test"))
        return read_omh_hud(omh_home, hermes_home, package_version="3.0.2")


def phased_plan(count: int, *, phases: int, active: int) -> list[dict]:
    return [
        {
            "text": f"Task number {index}",
            "state": "active" if index == active else "done" if index < active else "pending",
            "phase": f"Phase {min(index, phases - 1)}",
        }
        for index in range(count)
    ]


def agent_row(index: int, **fields) -> dict:
    row = {
        "state": "running",
        "task_id": f"worker{index}",
        "action": f"Worker {index} action",
        "model": "anthropic/claude-opus-5-5",
        "effort": "high",
        "category": "architect",
        "elapsed_seconds": 30 + index,
        "tokens": 1200 * (index + 1),
    }
    row.update(fields)
    return row


def with_agents(payload: dict, rows: list[dict], *, hidden: int = 0) -> dict:
    payload = copy.deepcopy(payload)
    payload["active"] = True
    payload["subagents"].update(
        rows=rows,
        hidden_rows=hidden,
        active=len(rows) + hidden,
        running=sum(1 for row in rows if row.get("state", "running") == "running"),
    )
    return payload


def with_graph(payload: dict, node_count: int) -> dict:
    payload = copy.deepcopy(payload)
    ids = [f"unit{index}" for index in range(node_count)]
    payload["graph"] = {
        "status": "active",
        "scope": "global",
        "frontier": ids[1:2],
        "edges": [[ids[0], other] for other in ids[1:]],
        "edge_count": max(0, node_count - 1),
        "hidden_nodes": 0,
        "nodes": [
            {
                "node_id": unit,
                "state": "completed" if index == 0 else "prepared_not_observed",
                "blocked_by": [],
                "in_frontier": index == 1,
            }
            for index, unit in enumerate(ids)
        ],
    }
    return payload


def item_rows(rows: list[str]) -> list[str]:
    return [row for row in rows if any(mark in row for mark in ("[•]", "[✓]", "[ ]"))]


def folded(rows: list[str]) -> tuple[int, int]:
    earlier = later = 0
    for row in rows:
        if row.startswith("... ("):
            count = int(row.split("(")[1].split(" ")[0])
            if "earlier" in row:
                earlier = count
            elif "later" in row:
                later = count
    return earlier, later


class PanelEmptyAndErrorTests(unittest.TestCase):
    def test_a_payload_with_nothing_to_show_draws_no_rows(self) -> None:
        payload = hud_payload()
        self.assertEqual(payload["todo"]["status"], "absent")
        for budget in (0, 1, 5, 40):
            with self.subTest(budget=budget):
                self.assertEqual(panel_lines(payload, 80, budget, THEME, 0.0), [])
        self.assertEqual(panel_lines(None, 80, 10, THEME, 0.0), [])
        # Anything the reader did not vouch for as metadata-only is not drawn.
        foreign = dict(payload, privacy="raw")
        self.assertEqual(panel_lines(with_agents(foreign, [agent_row(0)]), 80, 10, THEME, 0.0), [])

    def test_a_reader_failure_is_one_dim_notice(self) -> None:
        lines = panel_lines({"error": "OperationalError"}, 80, 5, THEME, 0.0)
        self.assertEqual(texts(lines), ["⚚ OMH HUD unavailable: OperationalError"])
        self.assertEqual(lines[0][0].style, THEME.dim)
        self.assertEqual(panel_lines({"error": "OperationalError"}, 80, 0, THEME, 0.0), [])


class PlanBlockTests(unittest.TestCase):
    def test_a_phased_plan_keeps_the_active_item_and_accounts_for_every_item(self) -> None:
        payload = hud_payload(phased_plan(12, phases=8, active=5), title="Release")
        roomy = texts(panel_lines(payload, 120, 40, THEME, 0.0))
        self.assertTrue(roomy[0].startswith("[Plan] Release │ 5/12 · 8 phases"), roomy[0])
        # The widget's cap: eight items, anchored just before the first open one.
        self.assertEqual(len(item_rows(roomy)), 8)
        self.assertEqual(folded(roomy), (4, 0))
        for budget in range(3, 16):
            with self.subTest(budget=budget):
                rows = texts(panel_lines(payload, 120, budget, THEME, 0.0))
                self.assertLessEqual(len(rows), budget)
                self.assertTrue(rows[0].startswith("[Plan] Release"))
                if len(rows) > 1:
                    self.assertIn("[•] Task number 5", "\n".join(rows))
                    earlier, later = folded(rows)
                    self.assertEqual(earlier + len(item_rows(rows)) + later, 12, rows)
                    # An item never loses the phase header it belongs under.
                    index = next(i for i, row in enumerate(rows) if "[•]" in row)
                    self.assertEqual(rows[index - 1], "Phase 5")

    def test_too_small_for_one_item_leaves_the_header_alone(self) -> None:
        payload = hud_payload(phased_plan(12, phases=8, active=5), title="Release")
        # One item needs its phase header and both folds: four rows under the header.
        for budget in (1, 2, 3, 4):
            with self.subTest(budget=budget):
                rows = texts(panel_lines(payload, 120, budget, THEME, 0.0))
                self.assertEqual(len(rows), 1, rows)
                self.assertTrue(rows[0].startswith("[Plan] Release │ 5/12"))
        self.assertEqual(len(panel_lines(payload, 120, 5, THEME, 0.0)), 5)

    def test_a_finished_plan_is_one_receipt_line_naming_its_skips(self) -> None:
        items = [
            {"text": "Research", "state": "done"},
            {"text": "Build", "state": "done"},
            {"text": "Polish", "state": "done", "blocked_reason": "not needed"},
        ]
        payload = hud_payload(items, title="Done plan")
        self.assertEqual(payload["todo"]["status"], "all_done")
        rows = texts(panel_lines(payload, 80, 10, THEME, 0.0))
        self.assertEqual(rows, ["[Plan] Done plan │ ✓ 2/3 (1 skipped)"])

    def test_the_parallel_shot_badge_rides_the_plan_header(self) -> None:
        payload = hud_payload(phased_plan(3, phases=1, active=1))
        live = copy.deepcopy(payload)
        live["parallel_shot"] = {"status": "observed", "open_count": 3, "peak_open_count": 5}
        header = texts(panel_lines(live, 120, 10, THEME, 0.0))[0]
        self.assertIn(" · parallel shot ×3", header)
        # Once every member closed: the measured peak, dimmed, with its age.
        history = copy.deepcopy(payload)
        history["parallel_shot"] = {
            "status": "observed",
            "open_count": 0,
            "peak_open_count": 2,
            "observed_at": "2026-10-13T00:00:00Z",
        }
        now = datetime(2026, 10, 13, 0, 0, 5, tzinfo=timezone.utc).timestamp()
        lines = panel_lines(history, 120, 10, THEME, now)
        self.assertIn(" · parallel shot ×2 (5s ago)", text(lines[0]))
        badge = next(span for span in lines[0] if "parallel shot" in span.text)
        self.assertEqual(badge.style, THEME.dim)

    def test_an_active_item_moves_only_while_work_is_live(self) -> None:
        payload = hud_payload(phased_plan(3, phases=1, active=1))
        # This install never saw post_tool_call: liveness is unanswerable, so
        # the item keeps the always-live shape -- green, with the colour wave.
        live_row = next(line for line in panel_lines(payload, 120, 10, THEME, 0.0) if "[•]" in text(line))
        self.assertGreater(len(live_row), 2, "the wave splits the item text into spans")
        self.assertIn(THEME.ok.fg, {span.style.fg for span in live_row})
        stalled = copy.deepcopy(payload)
        stalled["activity"].update(post_tool_call_observed=True, live=False)
        stalled["todo"]["stall"]["status"] = "unchanged"
        stalled["todo"]["updated_age_seconds"] = 1200
        lines = panel_lines(stalled, 120, 10, THEME, 0.0)
        row = next(line for line in lines if "[•]" in text(line))
        self.assertEqual(row[0].style.fg, THEME.warn.fg)
        self.assertTrue(text(row).endswith("(unchanged 20m 0s)"), text(row))
        self.assertNotIn(" .", text(lines[0]), "no walking ellipsis on a plan that is not moving")

    def test_hangul_titles_and_items_cut_on_whole_syllables(self) -> None:
        title = "패널 계획을 한국어로 길게 적은 제목입니다"
        items = [
            {
                "text": "첫 번째 작업은 화면 아래쪽 패널을 그리는 일입니다",
                "state": "active",
                "phase": "구현 단계",
            },
            {"text": "두 번째 작업", "state": "pending", "phase": "구현 단계"},
        ]
        # macOS hands Hangul over decomposed; the panel must still cut by syllable.
        payload = hud_payload(
            [dict(item, text=unicodedata.normalize("NFD", item["text"])) for item in items],
            title=unicodedata.normalize("NFD", title),
        )
        for width in (21, 30, 31, 45):
            with self.subTest(width=width):
                lines = panel_lines(payload, width, 10, THEME, 0.0)
                self.assertTrue(lines)
                for line in lines:
                    self.assertLessEqual(line_width(line), width, text(line))
                    for ch in text(line):
                        self.assertFalse(0x1100 <= ord(ch) <= 0x11FF, f"loose jamo in {text(line)!r}")
                self.assertTrue(text(lines[0]).endswith("…"), text(lines[0]))
                self.assertIn("패널", text(lines[0]))


class AgentsBlockTests(unittest.TestCase):
    def test_rows_carry_the_route_identity_and_a_spinner_from_now(self) -> None:
        payload = with_agents(
            hud_payload(),
            [
                agent_row(0),
                agent_row(1, state="done", category="critic", model="gpt-6.1-sol", effort="xhigh", route_origin="fallback"),
                agent_row(
                    2, state="queued", lane_backend="kanban", assignee="miku", native_status="ready", model="kimi-k3", effort=""
                ),
                agent_row(3, dispatch_lane="maestro", executor_profile="claude_code", model="claude-fable-5-1"),
                agent_row(4, category="inherit", model="glm-5.3", effort=""),
            ],
        )
        rows = texts(panel_lines(payload, 160, 20, THEME, 0.0))
        # The reader marks OMH-side rows global, so the header carries the one marker.
        self.assertTrue(rows[0].startswith("[Agents] │ [global] 5 agents · 3 running"), rows[0])
        body = "\n".join(rows)
        self.assertIn("category:architect(claude-opus-5-5:high)", body)
        self.assertIn("category:critic(gpt-6.1-sol:xhigh fallback)", body)
        self.assertIn("kanban/miku(kimi-k3)", body)
        self.assertIn("(claude/maestro claude-fable-5-1)", body)
        self.assertIn("inherit(glm-5.3)", body)
        self.assertNotIn("anthropic/", body, "the route names the model, not who serves it")
        # Tokens sit in one column down the list.
        token_columns = {row.index("tokens") for row in rows[1:] if "tokens" in row}
        self.assertEqual(len(token_columns), 1, rows)
        # A running row's marker turns with the clock; a settled row's does not.
        later = texts(panel_lines(payload, 160, 20, THEME, 0.09))
        self.assertNotEqual(rows[1][0], later[1][0])
        self.assertEqual(rows[2][0], "✓")
        self.assertEqual(later[2][0], "✓")

    def test_a_running_rows_elapsed_ticks_between_polls(self) -> None:
        payload = with_agents(hud_payload(), [agent_row(0, elapsed_seconds=42)])
        fresh = texts(panel_lines(payload, 120, 5, THEME, 100.0, received_at=100.0))[1]
        later = texts(panel_lines(payload, 120, 5, THEME, 110.0, received_at=100.0))[1]
        self.assertIn("42s", fresh)
        self.assertIn("52s", later)

    def test_agents_over_budget_disclose_the_rest_on_a_more_row(self) -> None:
        rows = [agent_row(index) for index in range(8)]
        payload = with_agents(hud_payload(), rows, hidden=2)
        roomy = texts(panel_lines(payload, 120, 40, THEME, 0.0))
        # Five by default, but never fewer than are running: all eight run.
        self.assertEqual(len(roomy), 1 + 8 + 1)
        self.assertEqual(roomy[-1], "  +2 more")
        for budget in range(3, 10):
            with self.subTest(budget=budget):
                lines = texts(panel_lines(payload, 120, budget, THEME, 0.0))
                self.assertEqual(len(lines), budget)
                shown = len(lines) - 2
                self.assertEqual(lines[-1], f"  +{8 - shown + 2} more")
        # Too small for a row and its disclosure: the header's counts say it.
        self.assertEqual(texts(panel_lines(payload, 120, 2, THEME, 0.0)), [roomy[0]])

    def test_the_graph_goes_first_then_agent_rows_then_plan_items(self) -> None:
        plan = hud_payload(phased_plan(3, phases=1, active=1))
        payload = with_graph(with_agents(plan, [agent_row(index) for index in range(3)]), 3)
        full = texts(panel_lines(payload, 120, 40, THEME, 0.0))
        self.assertEqual(len(full), 4 + 4 + 5)  # graph header+3 nodes, header+3 rows, plan header+phase+3 items
        for budget in range(len(full), -1, -1):
            with self.subTest(budget=budget):
                rows = texts(panel_lines(payload, 120, budget, THEME, 0.0))
                self.assertLessEqual(len(rows), budget)
                graph = [row for row in rows if "DAG ·" in row or " unit" in row]
                agents = [row for row in rows if "[sub]" in row]
                items = item_rows(rows)
                if graph:
                    self.assertEqual(len(agents), 3, rows)
                if agents:
                    self.assertEqual(len(items), 3, rows)
                if budget >= 1:
                    self.assertTrue(any(row.startswith("[Plan]") for row in rows), "the plan header is the last to go")

    def test_graph_rows_name_a_stuck_unit_with_its_own_marker(self) -> None:
        payload = with_graph(with_agents(hud_payload(), [agent_row(0)]), 2)
        payload["graph"]["nodes"][1].update(
            state="running", unit_state="stuck", state_reason="same error", stalled_for_seconds=400
        )
        payload["graph"]["hidden_nodes"] = 3
        lines = panel_lines(payload, 120, 20, THEME, 0.0)
        rows = texts(lines)
        self.assertEqual(rows[1], "  [global] DAG · 1 ready · 1 edges · +3 more")
        self.assertEqual(rows[3], "  [~] unit1 · stuck (same error, 6m 40s since new output)")
        self.assertEqual(lines[3][0].style, THEME.warn)


class BudgetInvariantTests(unittest.TestCase):
    def test_no_budget_and_no_width_is_ever_exceeded(self) -> None:
        plan = hud_payload(phased_plan(12, phases=8, active=5), title="한국어 제목과 English title together")
        busy = with_graph(with_agents(plan, [agent_row(index) for index in range(7)], hidden=3), 5)
        busy["parallel_shot"] = {"status": "observed", "open_count": 4}
        payloads = {
            "plan": plan,
            "agents": with_agents(hud_payload(), [agent_row(index) for index in range(7)], hidden=1),
            "busy": busy,
            "error": {"error": "OperationalError"},
        }
        for name, payload in payloads.items():
            for width in (30, 80, 140):
                for budget in range(13):
                    with self.subTest(payload=name, width=width, budget=budget):
                        lines = panel_lines(payload, width, budget, THEME, 1234.5, received_at=1230.0)
                        self.assertLessEqual(len(lines), budget)
                        if budget:
                            self.assertTrue(lines)
                        for line in lines:
                            self.assertLessEqual(line_width(line), width, text(line))
                            self.assertTrue(all(span.text for span in line))


class StatusSegmentsTests(unittest.TestCase):
    def test_version_and_session_wide_chips(self) -> None:
        payload = hud_payload()
        self.assertEqual(text(status_segments(payload, THEME)), "⚚ OMH v3.0.2")
        payload["repeat"] = {"status": "observed", "consecutive": 4, "period": 2, "intercepted": 1, "stage": "blocking"}
        payload["activity"].update(
            live=True, post_tool_call_observed=True, open_call_count=2, oldest_open_elapsed_seconds=12
        )
        payload["yolo"] = {"status": "observed", "enabled": True}
        line = status_segments(payload, THEME)
        self.assertEqual(text(line), "⚚ OMH v3.0.2 · repeat x4 cycle-of-2 blocked • 2 tools · 12s • yolo mode: on")
        chip = next(span for span in line if span.text.startswith("repeat"))
        self.assertEqual(chip.style.fg, THEME.warn.fg)

    def test_liveness_is_not_claimed_where_it_cannot_be_read(self) -> None:
        payload = hud_payload()
        payload["activity"].update(live=True, post_tool_call_observed=False, open_call_count=2)
        self.assertNotIn("tools", text(status_segments(payload, THEME)))

    def test_without_a_payload_the_line_still_names_omh(self) -> None:
        self.assertEqual(text(status_segments(None, THEME)), f"⚚ OMH v{__version__}")
        self.assertEqual(text(status_segments({"error": "OSError"}, THEME)), f"⚚ OMH v{__version__}")


class FakeReader:
    """Stands in for `read_omh_hud`: records each call and returns what `respond` says."""

    def __init__(self, respond: Callable[[str], dict] | None = None) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self.respond = respond or (lambda session: {"privacy": "metadata_only", "session": session})
        self.called = threading.Event()

    def __call__(self, *args, **kwargs) -> dict:
        self.calls.append((args, kwargs))
        self.called.set()
        return self.respond(kwargs["session_ref"])


class HudPollerTests(unittest.TestCase):
    def poller(self, reader: FakeReader) -> HudPoller:
        return HudPoller("/omh", "/hermes", package_version="9.9.9", reader=reader, clock=lambda: 50.0)

    def test_nothing_is_read_or_delivered_before_a_session(self) -> None:
        reader = FakeReader()
        poller = self.poller(reader)
        self.assertEqual(poller.poll_once(), HUD_POLL_MAX_SECONDS)
        self.assertEqual(reader.calls, [])
        self.assertIsNone(poller.take())

    def test_a_session_switch_empties_the_panel_and_reads_the_new_session(self) -> None:
        reader = FakeReader()
        poller = self.poller(reader)
        poller.set_session("stored-1")
        self.assertEqual(poller.take(), HudSnapshot("stored-1", None, 50.0))
        poller.poll_once()
        args, kwargs = reader.calls[-1]
        self.assertEqual(args, ("/omh", "/hermes"))
        self.assertEqual(
            kwargs,
            {"session_ref": "stored-1", "session_scoped": True, "tui_identity_expected": True, "package_version": "9.9.9"},
        )
        self.assertEqual(poller.take().payload["session"], "stored-1")
        self.assertIsNone(poller.take(), "a slot is taken once")
        # Compression rotates the stored id: the old plan must not outlive it.
        poller.set_session("stored-2")
        self.assertEqual(poller.take(), HudSnapshot("stored-2", None, 50.0))
        poller.poll_once()
        self.assertEqual(reader.calls[-1][1]["session_ref"], "stored-2")
        self.assertEqual(poller.take().session_id, "stored-2")
        poller.set_session("stored-2")
        self.assertIsNone(poller.take(), "naming the same session again changes nothing")

    def test_a_read_that_finishes_after_a_switch_is_discarded(self) -> None:
        holder: dict[str, HudPoller] = {}

        def respond(session: str) -> dict:
            if session == "stored-1":
                holder["poller"].set_session("stored-2")
            return {"privacy": "metadata_only", "session": session}

        poller = holder["poller"] = self.poller(FakeReader(respond))
        poller.set_session("stored-1")
        poller.take()
        self.assertEqual(poller.poll_once(), HUD_POLL_MIN_SECONDS)
        self.assertEqual(poller.take(), HudSnapshot("stored-2", None, 50.0))

    def test_unchanged_payloads_back_off_to_eight_seconds(self) -> None:
        payloads = [{"privacy": "metadata_only", "rows": [{"state": "running", "elapsed_seconds": 1}]}]
        poller = self.poller(FakeReader(lambda session: copy.deepcopy(payloads[-1])))
        poller.set_session("stored-1")
        intervals = [poller.poll_once() for _ in range(4)]
        self.assertEqual(intervals, [HUD_POLL_MIN_SECONDS, 4.0, HUD_POLL_MAX_SECONDS, HUD_POLL_MAX_SECONDS])
        # A figure that drifts every poll is delivered, but is not a change.
        poller.take()
        payloads.append({"privacy": "metadata_only", "rows": [{"state": "running", "elapsed_seconds": 9}]})
        self.assertEqual(poller.poll_once(), HUD_POLL_MAX_SECONDS)
        self.assertEqual(poller.take().payload["rows"][0]["elapsed_seconds"], 9)
        payloads.append({"privacy": "metadata_only", "rows": [{"state": "done", "elapsed_seconds": 9}]})
        self.assertEqual(poller.poll_once(), HUD_POLL_MIN_SECONDS)

    def test_a_reader_failure_is_a_notice_and_backs_off(self) -> None:
        def respond(session: str) -> dict:
            raise sqlite3.OperationalError("database is locked")

        poller = self.poller(FakeReader(respond))
        poller.set_session("stored-1")
        poller.take()
        self.assertEqual(poller.poll_once(), HUD_POLL_MIN_SECONDS)
        snapshot = poller.take()
        self.assertEqual(snapshot.payload, {"error": "OperationalError"})
        self.assertEqual(
            texts(panel_lines(snapshot.payload, 80, 4, THEME, 0.0)), ["⚚ OMH HUD unavailable: OperationalError"]
        )
        self.assertEqual(poller.poll_once(), 4.0)

    def test_the_thread_waits_for_a_session_delivers_and_stops_promptly(self) -> None:
        reader = FakeReader()
        poller = HudPoller("/omh", "/hermes", reader=reader)
        poller.start()
        self.addCleanup(poller.stop)
        self.assertFalse(reader.called.wait(0.2), "no read before a session is named")
        self.assertIsNone(poller.take())
        poller.set_session("stored-1")
        self.assertTrue(reader.called.wait(2.0), "naming a session wakes the thread at once")
        deadline = time.monotonic() + 2.0
        snapshot = poller.take()
        while (snapshot is None or snapshot.payload is None) and time.monotonic() < deadline:
            time.sleep(0.01)
            snapshot = poller.take() or snapshot
        self.assertEqual(snapshot.payload["session"], "stored-1")
        started = time.monotonic()
        poller.stop()
        self.assertLess(time.monotonic() - started, 0.5, "stop must not wait out the two-second interval")
        self.assertFalse(poller._thread.is_alive())


WIDGET_HARNESS = r"""
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
const [widgetPath, casesPath] = process.argv.slice(2)
const apps = []
const sdk = {
  Box: 'Box', Dialog: 'Dialog', Overlay: 'Overlay', Text: 'Text',
  h: (type, props, ...children) => (typeof type === 'function' ? type({ ...(props || {}), children }) : { type, props, children }),
  defineWidgetApp: app => { apps.push(app); return app },
  openWidget() {}, updateWidget() {},
}
const mod = await import(pathToFileURL(widgetPath).href)
mod.default(sdk)
const text = node => {
  if (node === null || node === undefined || node === false) return ''
  if (Array.isArray(node)) return node.map(text).join('')
  if (typeof node === 'string') return node
  if (typeof node === 'object') return text(node.children)
  return ''
}
const lines = node => {
  if (node === null || node === undefined || node === false) return []
  if (Array.isArray(node)) return node.flatMap(lines)
  if (typeof node === 'object' && node.type === 'Box') return lines(node.children)
  return [node]
}
const out = []
for (const { payload, cols } of JSON.parse(readFileSync(casesPath, 'utf8'))) {
  const render = id => {
    const app = apps.find(candidate => candidate.id === id)
    const state = app.reduce(app.init(''), { kind: 'snapshot', payload })
    return lines(app.render({ cols, rows: 200, state, t: { color: {} } })).map(text).filter(Boolean)
  }
  out.push({ todo: render('omh-todo'), status: render('omh-status') })
}
process.stdout.write(JSON.stringify(out))
"""


@unittest.skipUnless(NODE, "node renders the widget this panel is a port of")
class WidgetParityTests(unittest.TestCase):
    def test_plan_and_agent_rows_read_exactly_as_the_widget_draws_them(self) -> None:
        items = [
            {
                "text": f"Task number {index} with a fairly long description to truncate",
                "state": "active" if index == 5 else "done" if index < 5 else "pending",
                "phase": f"Phase {min(index, 7)}",
            }
            for index in range(12)
        ]
        items[2]["blocked_reason"] = "not needed"
        items[7]["blocked_reason"] = "needs the owner's call on the layout of the panel"
        items.append({"text": "Subtask", "state": "pending", "depth": 1})
        rows = [
            agent_row(0, turn_count=3, tool_count=12, cost_usd=0.0431, cost_approximate=True, tokens_per_second=41.6,
                      cache_hit_percentage=88, context_percentage=40, fallback_count=1),
            agent_row(1, state="done", category="critic", model="gpt-6.1-sol", effort="xhigh", route_origin="fallback"),
            agent_row(2, state="queued", lane_backend="kanban", assignee="miku", native_status="ready", model="kimi-k3"),
            agent_row(3, dispatch_lane="maestro", executor_profile="claude_code", model="claude-fable-5-1"),
            agent_row(
                4, state="blocked", action="Blocked worker 한글 제목", category="inherit", cost_usd=0, cost_status="included"
            ),
        ]
        full = with_graph(with_agents(hud_payload(items, title="Parity plan"), rows), 3)
        full["graph"]["nodes"][2].update(state="failed", blocked_by=["unit0", "unit1"])
        small = with_agents(hud_payload(items[:3], title="Small plan"), rows[:2])
        cases = [{"payload": payload, "cols": cols} for cols in (60, 80, 99, 100, 140, 200) for payload in (full, small)]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            widget = resources.files("omh.tui_widgets").joinpath("omh-status.mjs").read_text(encoding="utf-8")
            # No interpreter behind the widget's HUD read: it fails to null and
            # the frame renders only the payload handed in below.
            (root / "widget.mjs").write_text(
                widget.replace(INTERPRETER_MARKER, json.dumps(str(root / "no-interpreter"))), encoding="utf-8"
            )
            (root / "harness.mjs").write_text(WIDGET_HARNESS, encoding="utf-8")
            (root / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(root / "harness.mjs"), str(root / "widget.mjs"), str(root / "cases.json")],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
                cwd=str(root),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for case, widget_rows in zip(cases, json.loads(completed.stdout), strict=True):
            with self.subTest(cols=case["cols"], plan=case["payload"]["todo"]["title"]):
                mine = [text(line).rstrip() for line in panel_lines(case["payload"], case["cols"], 100, THEME, 0.0)]
                plan_at = next(index for index, row in enumerate(mine) if row.startswith("[Plan]"))
                # The widget's dock-top ends with the composer rule, and its
                # dock-bottom opens with a rule and the branded header, which
                # the TUI's status line and `[Agents]` header replace.
                self.assertEqual(mine[plan_at:], [row.rstrip() for row in widget_rows["todo"][:-1]])
                self.assertEqual(mine[1:plan_at], [row.rstrip() for row in widget_rows["status"][2:]])
                self.assertGreater(len(mine[1:plan_at]), 1)


if __name__ == "__main__":
    unittest.main()
