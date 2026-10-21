"""A dag-ulw run where people see it: the delegation graph, its drawing, `omh hud --graph`, and the chat posts.

The run is kept through the `omh_dag` handler and its children start and stop
through the ledger's own hook observers, as in Hermes. The graph reader then
draws the run's nodes with their recorded edges, and a messenger reply gets
the three moments only: a start block, a count line when counts change, and
an end block once the run itself is over.
"""

from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from _cli_harness import run_cli  # noqa: E402
from _delegation_state import PARENT, homes, start, stop  # noqa: E402
from omh.plugin_bundle.omh import dag_run  # noqa: E402
from omh.plugin_bundle.omh.delegation_graph import build_delegation_graph  # noqa: E402
from omh.plugin_bundle.omh.delegation_view import render_delegation_view  # noqa: E402
from omh.plugin_bundle.omh.hooks import result_transforms  # noqa: E402
from omh.plugin_bundle.omh.tools import dag_tool  # noqa: E402

PROMPT = "TASK: t\nDELIVERABLE: d\nSCOPE: s\nVERIFY: v\nSTOP WHEN: w"
NODES = [
    {"id": "api", "label": "Export API", "category": "quick", "prompt": PROMPT, "write_scope": ["src/api.py"]},
    {"id": "ui", "label": "Export button", "category": "unspecified-low", "prompt": PROMPT,
     "write_scope": ["src/ui.py"]},
    {"id": "verify", "label": "Verify export", "category": "unspecified-high", "prompt": PROMPT,
     "depends_on": ["api", "ui"]},
]


def call(**arguments: object) -> dict:
    return json.loads(dag_tool.omh_dag_handler(arguments, session_id=PARENT))


def write_handoff(home, node: str) -> None:
    path = dag_run.handoff_path(home.omh, PARENT, "export", node)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("done", encoding="utf-8")


def reply(text: str, *, platform: str = "discord", turn: str = "t1") -> str | None:
    return result_transforms.transform_llm_output(
        response_text=text, session_id=PARENT, model="claude-opus-5-5", platform=platform, turn_id=turn
    )


def defined(home) -> None:
    # Hermes' own table carries the owner pid the pool belongs to; with it the slots are read, not guessed.
    home.execute("ALTER TABLE async_delegations ADD COLUMN owner_pid INTEGER")
    home.session(PARENT, "claude-opus-5-5", effort="high")
    home.hermes.joinpath("config.yaml").write_text("delegation:\n  max_concurrent_children: 10\n")
    call(action="define", run="export", name="export feature", nodes=NODES)


def started(home, node: str, *, now: float) -> None:
    start(home, f"child-{node}", f"sa-{node}", f"[dag:export/{node}] {node}", now=now)
    home.session(f"child-{node}", "glm-5.3-flash", parent=PARENT, effort="low")
    home.usage(f"child-{node}", "glm-5.3-flash", last_seen=now)


class GraphTests(unittest.TestCase):
    def test_the_graph_draws_the_run_with_its_recorded_edges(self) -> None:
        with homes() as home:
            defined(home)
            now = time.time()
            started(home, "api", now=now)
            graph = build_delegation_graph(home.omh, home.hermes, PARENT, now=now + 1)
        self.assertEqual(graph["status"], "active")
        self.assertEqual(graph["name"], "export feature")
        self.assertTrue(graph["dag_open"])
        by_id = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual(set(by_id), {"root", "d:export:api", "d:export:ui", "d:export:verify"})
        self.assertEqual(by_id["d:export:api"]["state"], "running")
        self.assertEqual(by_id["d:export:api"]["model"], "glm-5.3-flash")
        self.assertEqual(by_id["d:export:ui"]["state"], "dispatched")
        self.assertEqual((by_id["d:export:ui"]["category"], by_id["d:export:ui"]["category_source"]),
                         ("unspecified-low", "dag_plan"))
        self.assertEqual(by_id["d:export:verify"]["waits_on"], 2)
        edges = {(edge["from"], edge["to"], edge["kind"]) for edge in graph["edges"]}
        self.assertEqual(edges, {("root", "d:export:api", "dispatch"), ("root", "d:export:ui", "dispatch"),
                                 ("d:export:api", "d:export:verify", "depends_on"),
                                 ("d:export:ui", "d:export:verify", "depends_on")})
        # The bound child is the node, never a second lane beside it.
        self.assertFalse([node for node in graph["nodes"] if node["id"].startswith("s:")])
        header = render_delegation_view(graph, width=100).text_lines()[0]
        self.assertIn("export feature", header)

    def test_a_repair_hangs_below_the_node_it_repairs_and_superseded_nodes_go(self) -> None:
        with homes() as home:
            defined(home)
            for node in ("api", "ui"):
                write_handoff(home, node)
                call(action="settle", run="export", node=node, outcome="accepted")
            call(action="settle", run="export", node="verify", outcome="failed")
            call(action="repair", run="export", node="verify", nodes=[
                {"id": "fix", "label": "Fix export", "category": "quick", "prompt": PROMPT}])
            call(action="settle", run="export", node="fix", outcome="failed")
            call(action="repair", run="export", node="fix", nodes=[
                {"id": "fix2", "label": "Fix export again", "category": "quick", "prompt": PROMPT}])
            graph = build_delegation_graph(home.omh, home.hermes, PARENT)
        ids = {node["id"] for node in graph["nodes"]}
        self.assertNotIn("d:export:fix", ids)
        self.assertIn(("d:export:verify", "d:export:fix2", "repair"),
                      {(edge["from"], edge["to"], edge["kind"]) for edge in graph["edges"]})

    def test_a_compacted_parent_still_finds_its_run(self) -> None:
        with homes() as home:
            defined(home)
            later = "20261020_160000_later"
            with patch("omh.plugin_bundle.omh.delegation_graph.conversation_session_ids",
                       return_value={PARENT, later}):
                graph = build_delegation_graph(home.omh, home.hermes, later)
        self.assertEqual(graph["name"], "export feature")

    def test_omh_hud_graph_prints_the_run(self) -> None:
        with homes() as home:
            defined(home)
            start(home, "child-api", "sa-api", "[dag:export/api] api", now=time.time())
            with patch("omh.commands.hud._encodes", return_value=True):
                code, stdout, stderr = run_cli(
                    ["--omh-home", str(home.omh), "--hermes-home", str(home.hermes), "hud", "--graph"],
                    output_json=False)
        self.assertEqual(code, 0, stderr)
        self.assertIn("DAG · export feature", stdout)


class MessengerTests(unittest.TestCase):
    def test_start_then_count_lines_then_the_end_once_the_run_is_over(self) -> None:
        with homes() as home:
            defined(home)
            now = time.time()
            for node in ("api", "ui"):
                started(home, node, now=now)
            first = reply("Dispatched the first layer.", turn="t1")
            for node in ("api", "ui"):
                stop(home, f"child-{node}", now=now + 5)
                write_handoff(home, node)
                call(action="settle", run="export", node=node, outcome="accepted")
            # The parent answers a completion turn with the run's own status line: no second line is added, and the
            # counts are recorded, so the next reply with nothing new adds nothing either.
            second = reply("DAG export feature · 2/3 done · 0 running", turn="t2")
            quiet = reply("Waiting on verification.", turn="t2b")
            call(action="settle", run="export", node="verify", outcome="failed", reason="1 test red")
            third = reply("Verification failed; repairing.", turn="t3")
            call(action="repair", run="export", node="verify", nodes=[
                {"id": "fix", "label": "Fix the red test", "category": "quick", "prompt": PROMPT}])
            write_handoff(home, "fix")
            call(action="settle", run="export", node="fix", outcome="accepted")
            fourth = reply("Repaired.", turn="t4")
            fifth = reply("Anything else?", turn="t5")
        self.assertIn("DAG · export feature · 3 tasks · 2 layers", first)
        self.assertIsNone(second)
        self.assertIsNone(quiet)
        # Every drawn lane has settled, but the run can still repair: a count line, never the end block.
        self.assertTrue(third.endswith("DAG export feature · 2/3 done · 0 running · 1 failed (Verify export)"), third)
        self.assertIn("DAG · export feature · ended", fourth)
        self.assertIsNone(fifth)

    def test_a_subagent_turn_gets_no_post(self) -> None:
        with homes() as home:
            defined(home)
            started(home, "api", now=time.time())
            self.assertIsNone(reply("child reply", platform="subagent"))
            self.assertIn("DAG · export feature", reply("parent reply"))


if __name__ == "__main__":
    unittest.main()
