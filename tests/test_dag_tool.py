"""`omh_dag`: a dag-ulw run kept by OMH and run by the parent agent with Hermes' own `delegate_task`.

The handler is called the way Hermes calls a plugin tool: the model's
arguments, and the session id as a keyword. Children start and stop through
the delegation ledger's own observers with the payloads Hermes' hooks carry,
so the goal marker binding is exercised end to end.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from _delegation_state import PARENT, homes, start, stop  # noqa: E402
from omh.coding.fanout import build_fanout_contract  # noqa: E402
from omh.coding.fanout_contracts import FANOUT_SPAWN_PLAN_FIELDS, FANOUT_SPAWN_PLAN_THRESHOLD  # noqa: E402
from omh.coding.executors import EXTERNAL_CLI_PROFILES  # noqa: E402
from omh.plugin_bundle.omh import dag_run  # noqa: E402
from omh.plugin_bundle.omh.delegation_ledger import dag_binding, read_ledger  # noqa: E402
from omh.plugin_bundle.omh.tools import dag_tool  # noqa: E402
from omh.routing import owner_preference  # noqa: E402
from omh.routing.coding_route_actions import OWNER_PREFERENCE_ROUTE_FAMILY  # noqa: E402

PROMPT = "TASK: do it\nDELIVERABLE: a change\nSCOPE: only its files\nVERIFY: run `make test`, exit 0\nSTOP WHEN: green"


def node(node_id: str, *, category: str = "quick", depends_on: tuple[str, ...] = (), scope: tuple[str, ...] = (),
         label: str = "", prompt: str = PROMPT) -> dict:
    return {"id": node_id, "label": label or f"Node {node_id}", "category": category, "prompt": prompt,
            "depends_on": list(depends_on), "write_scope": list(scope)}


FEATURE = [
    node("api", scope=("src/api.py",)),
    node("ui", category="unspecified-low", scope=("src/ui.py",)),
    node("docs", category="writing", depends_on=("api",), scope=("docs/api.md",)),
    node("verify", category="unspecified-high", depends_on=("api", "ui", "docs"), label="Verify the feature"),
]


def call(**arguments: object) -> dict:
    return json.loads(dag_tool.omh_dag_handler(arguments, session_id=PARENT))


def handoff(home, run: str, node_id: str, text: str = "done") -> Path:
    path = dag_run.handoff_path(home.omh, PARENT, run, node_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def child_started(home, run: str, node_id: str, *, now: float | None = None) -> None:
    start(home, f"child-{node_id}", f"sa-{node_id}", f"[dag:{run}/{node_id}] Node {node_id}",
          now=time.time() if now is None else now)


def slots_table(home, *, running: list[tuple[str, int]] = ()) -> None:
    """Hermes' `async_delegations` rows with the owner pid the pool belongs to."""
    home.execute("DROP TABLE async_delegations")
    home.execute("CREATE TABLE async_delegations (delegation_id TEXT PRIMARY KEY, parent_session_id TEXT, "
                 "state TEXT, dispatched_at REAL, task_json TEXT, owner_pid INTEGER)")
    for delegation_id, pid in running:
        home.execute("INSERT INTO async_delegations VALUES (?, ?, 'running', 0, '{}', ?)",
                     (delegation_id, PARENT, pid))


class DefineTests(unittest.TestCase):
    def test_define_records_topology_and_hands_out_the_first_layer(self) -> None:
        with homes() as home:
            slots_table(home)
            result = call(action="define", run="feat", name="export feature", goal="export works", nodes=FEATURE)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["state"], "running")
        self.assertEqual(result["topology"]["layers"], [["api", "ui"], ["docs"], ["verify"]])
        self.assertEqual(result["topology"]["critical_path"], ["api", "docs", "verify"])
        self.assertEqual(result["topology"]["bottlenecks"][0], {"node": "api", "blocks": 2})
        self.assertEqual(result["lint"], [])
        self.assertEqual([(c["category"], c["nodes"], c["route"]["action"]) for c in result["calls"]],
                         [("quick", ["api"], "set"), ("unspecified-low", ["ui"], "set")])
        task = result["calls"][0]["tasks"][0]
        self.assertTrue(task["goal"].startswith("[dag:feat/api] "))
        self.assertIn("Handoff: before you stop, write your report to", task["context"])
        self.assertEqual(dag_binding(task["goal"]), {"run": "feat", "node": "api"})

    def test_hard_rejects(self) -> None:
        cases = {
            "dependency_cycle": [node("a", depends_on=("b",)), node("b", depends_on=("a",))],
            "unknown_dependency": [node("a", depends_on=("ghost",))],
            "self_dependency": [node("a", depends_on=("a",))],
            "write_scope_overlap": [node("a", scope=("x.py",)), node("b", scope=("x.py",))],
            "too_many_nodes": [node(f"n{index}") for index in range(dag_run.MAX_NODES + 1)],
            "empty_graph": [],
            "duplicate_node_id": [node("a"), node("a")],
            "invalid_category": [node("a", category="fastest")],
        }
        for reason, nodes in cases.items():
            with self.subTest(reason=reason), homes():
                result = call(action="define", run="feat", nodes=nodes)
                self.assertEqual((result["status"], result["reason"]), ("refused", reason), result)

    def test_an_edge_lets_two_nodes_share_a_write_scope(self) -> None:
        with homes():
            result = call(action="define", run="feat",
                          nodes=[node("a", scope=("x.py",)), node("b", scope=("x.py",), depends_on=("a",))])
        self.assertEqual(result["status"], "ok", result)

    def test_lint_is_advice_and_never_rejects(self) -> None:
        nodes = [node("a", category="deep", prompt="Do the thing."), node("b", category="deep-work", scope=("b.py",)),
                 node("c", category="ultrabrain"), node("d", category="ultrabrain")]
        with homes():
            result = call(action="define", run="feat", nodes=nodes)
        self.assertEqual(result["status"], "ok", result)
        self.assertIn("missing_markers:a:TASK,DELIVERABLE,SCOPE,VERIFY,STOP_WHEN", result["lint"])
        self.assertIn("no_verification_node", result["lint"])
        self.assertIn("ultrabrain_more_than_once", result["lint"])
        with homes():
            deep = call(action="define", run="deep", nodes=[node("a", category="deep"), node("b", category="deep-work")])
        self.assertIn("all_nodes_deep", deep["lint"])
        with homes():
            miss = call(action="define", run="miss", nodes=[node("a", scope=("a.py",)), node("verify")])
        self.assertIn("verification_misses:a", miss["lint"])

    def test_the_same_definition_is_reused_and_a_different_one_refused(self) -> None:
        with homes():
            call(action="define", run="feat", nodes=FEATURE)
            again = call(action="define", run="feat", nodes=FEATURE)
            other = call(action="define", run="feat", nodes=[node("solo")])
        self.assertTrue(again.get("reused"), again)
        self.assertEqual(other["reason"], "run_exists")

    def test_a_delegated_child_cannot_keep_a_run(self) -> None:
        with homes(), patch.object(dag_tool._nudge_budget, "session_is_delegated", return_value=True):
            result = call(action="define", run="feat", nodes=FEATURE)
        self.assertEqual(result["reason"], "called_from_child")

    def test_no_session_no_run(self) -> None:
        with homes():
            result = json.loads(dag_tool.omh_dag_handler({"action": "status"}, session_id=""))
        self.assertEqual(result["reason"], "session_required")


class SlotTests(unittest.TestCase):
    def test_ready_never_hands_out_more_calls_than_free_slots(self) -> None:
        with homes() as home:
            # Cap 3 with two units of one call and one other call running in this process: one slot left.
            home.hermes.joinpath("config.yaml").write_text("delegation:\n  max_concurrent_children: 3\n")
            slots_table(home, running=[("deleg_a-1", os.getpid()), ("deleg_a-2", os.getpid()),
                                       ("deleg_b", os.getpid()), ("deleg_other", os.getpid() + 1)])
            result = call(action="define", run="feat", nodes=FEATURE)
        self.assertEqual(result["slots"], {"basis": "state_db", "cap": 3, "free": 1, "independent_completions": False,
                                           "running_calls": 2})
        self.assertEqual([c["nodes"] for c in result["calls"]], [["api"]])
        self.assertEqual(result["held_for_slots"], ["ui"])

    def test_an_unreadable_state_db_admits_one_call(self) -> None:
        with homes():
            result = call(action="define", run="feat", nodes=FEATURE)
        self.assertEqual(result["slots"]["basis"], "unread")
        self.assertEqual(len(result["calls"]), 1)

    def test_a_category_larger_than_the_cap_splits_into_calls(self) -> None:
        nodes = [node(f"n{index}", scope=(f"f{index}",)) for index in range(3)] + [
            node("verify", depends_on=("n0", "n1", "n2"))]
        with homes() as home:
            home.hermes.joinpath("config.yaml").write_text("delegation:\n  max_concurrent_children: 2\n")
            slots_table(home)
            result = call(action="define", run="feat", nodes=nodes)
        self.assertEqual([c["nodes"] for c in result["calls"]], [["n0", "n1"], ["n2"]])

    def test_the_cap_reads_config_then_environment_then_hermes_default(self) -> None:
        self.assertEqual(dag_tool._cap("4"), 4)
        with patch.dict(os.environ, {dag_tool.MAX_CONCURRENT_ENV: "6"}):
            self.assertEqual(dag_tool._cap(""), 6)
            self.assertEqual(dag_tool._cap("x"), 6)
        with patch.dict(os.environ, {dag_tool.MAX_CONCURRENT_ENV: ""}):
            self.assertEqual(dag_tool._cap(None), dag_tool.DEFAULT_MAX_CONCURRENT_CHILDREN)


class SettleTests(unittest.TestCase):
    def _run(self, home) -> None:
        slots_table(home)
        home.hermes.joinpath("config.yaml").write_text("delegation:\n  max_concurrent_children: 10\n")
        call(action="define", run="feat", nodes=FEATURE)

    def test_accepted_needs_the_handoff_and_returns_what_is_ready_next(self) -> None:
        with homes() as home:
            self._run(home)
            child_started(home, "feat", "api")
            missing = call(action="settle", run="feat", node="api", outcome="accepted")
            handoff(home, "feat", "api")
            stop(home, "child-api", now=time.time())
            accepted = call(action="settle", run="feat", node="api", outcome="accepted")
        self.assertEqual(missing["reason"], "handoff_missing")
        self.assertEqual([c["nodes"] for c in accepted["calls"]], [["docs"]])
        self.assertEqual(accepted["counts"]["done"], 1)
        self.assertEqual(accepted["status_line"], "DAG feat · 1/4 done · 2 running")

    def test_a_child_hermes_reported_failed_is_never_accepted(self) -> None:
        with homes() as home:
            self._run(home)
            child_started(home, "feat", "api")
            handoff(home, "feat", "api")
            stop(home, "child-api", "failed", now=time.time())
            refused = call(action="settle", run="feat", node="api", outcome="accepted")
            blocked = call(action="settle", run="feat", node="api", outcome="blocked", reason="needs a key")
        self.assertEqual(refused["reason"], "child_reported_failure")
        self.assertEqual(blocked["status"], "ok")
        rows = {row["id"]: row for row in blocked["nodes"]}
        self.assertEqual(rows["api"]["state"], "blocked")
        self.assertEqual(rows["api"]["reason"], "needs a key")

    def test_a_failed_node_blocks_only_its_dependents(self) -> None:
        with homes() as home:
            self._run(home)
            result = call(action="settle", run="feat", node="api", outcome="failed", reason="tests red")
        rows = {row["id"]: row["state"] for row in result["nodes"]}
        self.assertEqual(rows, {"api": "failed", "ui": "handed_out", "docs": "held", "verify": "held"})
        self.assertEqual(result["calls"], [])

    def test_a_provider_error_gets_one_retry_on_the_fallback_route(self) -> None:
        with homes() as home:
            self._run(home)
            first = call(action="settle", run="feat", node="api", outcome="failed", provider_error=True)
            second = call(action="settle", run="feat", node="api", outcome="failed", provider_error=True)
        self.assertEqual(first["settled"], {"node": "api", "outcome": "retry"})
        self.assertEqual([(c["nodes"], c["route"]["action"]) for c in first["calls"]], [(["api"], "fallback")])
        self.assertEqual(second["settled"], {"node": "api", "outcome": "failed"})

    def test_a_node_not_out_is_not_settled(self) -> None:
        with homes() as home:
            self._run(home)
            result = call(action="settle", run="feat", node="docs", outcome="accepted")
        self.assertEqual(result["reason"], "node_not_out")


class RepairTests(unittest.TestCase):
    """The goal loop: repair nodes stand in for a failed node, at most two cycles for each."""

    def _failed_verify(self, home) -> None:
        slots_table(home)
        call(action="define", run="feat", nodes=[node("api", scope=("a.py",)), node("verify", depends_on=("api",))])
        handoff(home, "feat", "api")
        call(action="settle", run="feat", node="api", outcome="accepted")
        call(action="settle", run="feat", node="verify", outcome="failed", reason="2 tests red")

    def test_repair_nodes_resolve_the_failed_node(self) -> None:
        with homes() as home:
            self._failed_verify(home)
            repaired = call(action="repair", run="feat", node="verify", nodes=[
                node("fix", scope=("a.py",), depends_on=("api",)), node("verify2", depends_on=("fix",))])
            handoff(home, "feat", "fix")
            call(action="settle", run="feat", node="fix", outcome="accepted")
            handoff(home, "feat", "verify2")
            done = call(action="settle", run="feat", node="verify2", outcome="accepted")
        self.assertEqual(repaired["settled"]["repair_nodes"], ["fix", "verify2"])
        self.assertEqual([c["nodes"] for c in repaired["calls"]], [["fix"]])
        self.assertEqual(done["state"], "done")
        self.assertEqual({row["id"]: row["state"] for row in done["nodes"]}["verify"], "repaired")

    def test_two_cycles_then_the_run_stops_and_reports(self) -> None:
        with homes() as home:
            self._failed_verify(home)
            call(action="repair", run="feat", node="verify", nodes=[node("v2", depends_on=("api",))])
            first = call(action="settle", run="feat", node="v2", outcome="failed")
            call(action="repair", run="feat", node="v2", nodes=[node("v3", depends_on=("api",))])
            second = call(action="settle", run="feat", node="v3", outcome="failed")
            third = call(action="repair", run="feat", node="v3", nodes=[node("v4", depends_on=("api",))])
        self.assertEqual(first["state"], "needs_repair")
        self.assertEqual(second["state"], "stopped")
        # The original node and its latest repair are both still open.
        self.assertEqual(second["open"], ["verify", "v3"])
        self.assertEqual(third["reason"], "repair_limit_reached")
        superseded = {row["id"]: row["state"] for row in second["nodes"]}
        self.assertEqual(superseded["v2"], "superseded")

    def test_a_repair_node_may_not_wait_on_an_open_node(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=FEATURE)
            call(action="settle", run="feat", node="api", outcome="failed")
            result = call(action="repair", run="feat", node="api", nodes=[node("fix", depends_on=("ui",))])
        self.assertEqual(result["reason"], "repair_depends_on_open_node")

    def test_only_a_failed_node_is_repaired(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=FEATURE)
            result = call(action="repair", run="feat", node="docs", nodes=[node("fix")])
        self.assertEqual(result["reason"], "not_failed")


class LifecycleTests(unittest.TestCase):
    def test_status_finds_the_run_from_a_later_segment(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=FEATURE)
            later = "20261020_150000_later"
            with patch.object(dag_tool._kanban_board_reader, "conversation_session_ids",
                              return_value={PARENT, later}):
                result = json.loads(dag_tool.omh_dag_handler({"action": "status", "run": "feat"}, session_id=later))
        self.assertEqual((result["status"], result["run"]), ("ok", "feat"), result)

    def test_cancel_ends_the_run(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=FEATURE)
            result = call(action="cancel", run="feat")
            settle = call(action="settle", run="feat", node="api", outcome="accepted")
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(settle["reason"], "run_cancelled")

    def test_the_record_holds_no_child_output(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=FEATURE)
            child_started(home, "feat", "api")
            stop(home, "child-api", now=time.time())
            text = dag_run.record_path(home.omh, PARENT, "feat").read_text(encoding="utf-8")
        self.assertNotIn("SECRET SUMMARY TEXT", text)

    def test_the_ledger_binds_a_child_by_its_goal_marker_only(self) -> None:
        with homes() as home:
            child_started(home, "feat", "api")
            start(home, "child-x", "sa-x", "[dag:feat/API] wrong case", now=time.time())
            start(home, "child-y", "sa-y", "please [dag:feat/api] not first", now=time.time())
            children = {child["subagent_id"]: child for child in read_ledger(home.omh, PARENT)["children"]}
        self.assertEqual(children["sa-api"]["dag"], {"run": "feat", "node": "api"})
        self.assertNotIn("dag", children["sa-x"])
        self.assertNotIn("dag", children["sa-y"])


class ExportTests(unittest.TestCase):
    def _choose(self, home, owner: str, *, hours_ago: float = 0.0) -> None:
        from datetime import datetime, timedelta, timezone

        moment = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        state = owner_preference.record_in_message_explicit_naming(
            None, route_family=OWNER_PREFERENCE_ROUTE_FAMILY, selected_owner=owner,
            naming_cue=f"named_executor:{owner}", occurred_at=moment)
        path = home.omh.joinpath(*dag_tool.OWNER_PREFERENCE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")

    def test_without_a_chosen_owner_nothing_is_exported(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=FEATURE)
            result = call(action="export", run="feat", owner="codex")
            self._choose(home, "codex", hours_ago=30)
            stale = call(action="export", run="feat", owner="codex")
        self.assertEqual(result["reason"], "owner_not_chosen")
        self.assertEqual(stale["reason"], "owner_not_chosen")

    def test_export_round_trips_into_a_fanout_contract(self) -> None:
        nodes = [node("api", scope=("src/api.py",)), node("ui", scope=("src/ui.py",)),
                 node("docs", depends_on=("api",), scope=("docs/api.md",)),
                 node("verify", depends_on=("api", "ui", "docs"))]
        with homes() as home:
            slots_table(home, running=[(f"d{index}", os.getpid()) for index in range(10)])  # no slot: nothing out
            call(action="define", run="feat", goal="ship the export feature", nodes=nodes)
            self._choose(home, "codex")
            result = call(action="export", run="feat", owner="codex")
            payload = json.loads(Path(result["fanout"]["units_file"]).read_text(encoding="utf-8"))
            goal = Path(result["fanout"]["goal_file"]).read_text(encoding="utf-8")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual([unit["unit_id"] for unit in payload["units"]], ["api", "ui", "docs"])
        contract = build_fanout_contract(goal, payload["units"])
        self.assertEqual(contract["fanout_id"], result["fanout"]["fanout_id"])
        self.assertEqual(contract["merge_plan"]["merge_order"], ["api", "ui", "docs"])
        self.assertEqual({unit["unit_id"]: unit["depends_on"] for unit in contract["units"]},
                         {"api": [], "ui": [], "docs": ["api"]})
        rows = {row["id"]: row["state"] for row in result["nodes"]}
        self.assertEqual(rows["api"], "handed_out")
        self.assertEqual(rows["verify"], "waiting")

    def test_constants_held_against_the_router_and_the_fanout_contract(self) -> None:
        self.assertEqual(dag_tool.OWNER_PREFERENCE_FILE, owner_preference.OWNER_PREFERENCE_PATH_SEGMENTS)
        self.assertEqual(dag_tool.OWNER_PREFERENCE_SCHEMA, owner_preference.OWNER_PREFERENCE_SCHEMA_VERSION)
        self.assertEqual(dag_tool.OWNER_PROVENANCES, owner_preference.OWNER_PREFERENCE_EXPLICIT_CHOICE_PROVENANCES)
        self.assertEqual(dag_tool.OWNER_ROUTE_FAMILY, OWNER_PREFERENCE_ROUTE_FAMILY)
        self.assertEqual(set(dag_tool.EXTERNAL_OWNERS), set(EXTERNAL_CLI_PROFILES))
        self.assertEqual(dag_tool.SPAWN_PLAN_THRESHOLD, FANOUT_SPAWN_PLAN_THRESHOLD)
        self.assertEqual(dag_tool.SPAWN_PLAN_FIELDS, FANOUT_SPAWN_PLAN_FIELDS)


class StateDbTests(unittest.TestCase):
    def test_the_slot_read_is_read_only(self) -> None:
        with homes() as home:
            slots_table(home, running=[("deleg_a", os.getpid())])
            before = home.hermes.joinpath("state.db").read_bytes()
            dag_tool.free_slots(home.hermes)
            self.assertEqual(home.hermes.joinpath("state.db").read_bytes(), before)
            connection = sqlite3.connect(home.hermes / "state.db")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM async_delegations").fetchone()[0], 1)
            connection.close()


if __name__ == "__main__":
    unittest.main()
