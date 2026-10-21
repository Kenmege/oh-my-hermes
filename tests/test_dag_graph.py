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

    def test_added_nodes_hang_below_where_they_came_from(self) -> None:
        with homes() as home:
            defined(home)
            write_handoff(home, "api")
            call(action="settle", run="export", node="api", outcome="accepted")
            call(action="settle", run="export", node="ui", outcome="failed")
            call(action="repair", run="export", node="ui", nodes=[
                {"id": "fix-ui", "label": "Fix the button", "category": "quick", "prompt": PROMPT,
                 "depends_on": ["api"], "write_scope": ["src/ui.py"]}])
            for node in ("fix-ui", "verify"):
                write_handoff(home, node)
                call(action="settle", run="export", node=node, outcome="accepted")
            call(action="extend", run="export", nodes=[
                {"id": "csv", "label": "Add CSV export", "category": "quick", "prompt": PROMPT, "depends_on": ["api"],
                 "write_scope": ["src/api.py"]},
                {"id": "verify2", "label": "Verify CSV", "category": "unspecified-high", "prompt": PROMPT,
                 "depends_on": ["csv"]}])
            graph = build_delegation_graph(home.omh, home.hermes, PARENT)
        by_id = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual({key: by_id[f"d:export:{key}"]["dag_origin"] for key in ("api", "fix-ui", "csv", "verify2")},
                         {"api": "define", "fix-ui": "repair:ui", "csv": "extend:1", "verify2": "extend:1"})
        edges = {(edge["from"], edge["to"], edge["kind"]) for edge in graph["edges"]}
        # The first node of each added group hangs from where it came from, beside its own depends_on edges; a node
        # led to by its own group does not.
        self.assertLessEqual({("d:export:ui", "d:export:fix-ui", "repair"),
                              ("d:export:api", "d:export:fix-ui", "depends_on"),
                              ("d:export:verify", "d:export:csv", "extend"),
                              ("d:export:api", "d:export:csv", "depends_on"),
                              ("d:export:csv", "d:export:verify2", "depends_on")}, edges)
        self.assertFalse({edge for edge in edges if edge[1] == "d:export:verify2" and edge[2] == "extend"})
        # An extend round is its own group in the tree; a repair node keeps its layer.
        self.assertEqual((by_id["d:export:csv"]["group"], by_id["d:export:verify2"]["group"]), ("extend 1", "extend 1"))
        self.assertEqual(by_id["d:export:fix-ui"]["group"], "layer 2")
        lines = render_delegation_view(graph, width=100, layout="tree").text_lines()
        # In the tree an added node sits under the node it came from.
        verify_row = next(index for index, line in enumerate(lines) if "Verify export" in line)
        csv_row = next(index for index, line in enumerate(lines) if "Add CSV export" in line)
        lead = len(lines[verify_row]) - len(lines[verify_row].lstrip(" │├└─"))
        self.assertEqual(csv_row, verify_row + 1)
        self.assertGreater(len(lines[csv_row]) - len(lines[csv_row].lstrip(" │├└─")), lead)

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

    def test_a_node_added_mid_run_is_named_once_on_the_count_line(self) -> None:
        with homes() as home:
            defined(home)
            now = time.time()
            for node in ("api", "ui"):
                started(home, node, now=now)
            off_chat = call(action="status", run="export")["status_line"]
            first = reply("Dispatched the first layer.", turn="t1")
            for node in ("api", "ui"):
                stop(home, f"child-{node}", now=now + 5)
            write_handoff(home, "api")
            call(action="settle", run="export", node="api", outcome="accepted")
            call(action="settle", run="export", node="ui", outcome="failed")
            line = call(action="repair", run="export", node="ui", nodes=[
                {"id": "fix-ui", "label": "Fix the button", "category": "quick", "prompt": PROMPT}])["status_line"]
            second = reply("Repairing the button.", turn="t2")
            again = call(action="status", run="export")["status_line"]
            started(home, "fix-ui", now=now + 10)
            third = reply("Still going.", turn="t3")
        self.assertNotIn("added", off_chat)  # no chat post yet: nothing to name a node against
        self.assertIn("DAG · export feature", first)
        self.assertEqual(line, "DAG export feature · 1/4 done · 0 running · 1 failed (Export button) · "
                               "+1 added (repair: Fix the button)")
        self.assertTrue(second.endswith("DAG export feature · 1/4 done · 0 running · 1 failed (Export button) · "
                                        "+1 added (repair: Fix the button)"), second)
        # Named once: the tool's own line and the next count line no longer carry it.
        self.assertNotIn("added", again)
        self.assertTrue(third.endswith("DAG export feature · 1/4 done · 1 running · 1 failed (Export button)"), third)

    def test_a_reply_that_is_the_status_line_names_the_added_node_and_nothing_repeats_it(self) -> None:
        with homes() as home:
            defined(home)
            now = time.time()
            started(home, "api", now=now)
            reply("Dispatched.", turn="t1")
            stop(home, "child-api", now=now + 5)
            call(action="settle", run="export", node="api", outcome="failed")
            line = call(action="repair", run="export", node="api", nodes=[
                {"id": "fix", "label": "Fix the API", "category": "quick", "prompt": PROMPT}])["status_line"]
            echoed = reply(line, turn="t2")
            started(home, "fix", now=now + 10)
            later = reply("Working.", turn="t3")
        self.assertTrue(line.endswith("+1 added (repair: Fix the API)"), line)
        self.assertIsNone(echoed)
        self.assertNotIn("added", later)

    def test_two_labels_then_a_count_and_the_line_alone_when_the_names_do_not_fit(self) -> None:
        from omh.plugin_bundle.omh.delegation_snapshot import added_clause

        self.assertEqual(added_clause([("repair", "Docs body")]), "+1 added (repair: Docs body)")
        self.assertEqual(added_clause([("repair", "Docs body"), ("extend", "CSV header"), ("extend", "Verify CSV")]),
                         "+3 added (repair: Docs body, extend: CSV header, +1 more)")
        self.assertEqual(added_clause([("extend", "CSV export"), ("extend", "CSV docs"), ("extend", "Verify CSV")]),
                         "+3 added (extend: CSV export, CSV docs, +1 more)")
        with homes() as home:
            defined(home)
            now = time.time()
            started(home, "api", now=now)
            reply("Dispatched.", turn="t1")
            stop(home, "child-api", now=now + 5)
            call(action="settle", run="export", node="api", outcome="failed")
            call(action="repair", run="export", node="api", nodes=[
                {"id": "fix", "label": "Fix the API", "category": "quick", "prompt": PROMPT}])
            line = "DAG export feature · 0/4 done · 0 running · 1 failed (Export API)"
            # Discord's cap is 2,000 characters, 90% of it for the reply and the post: room for the line only.
            tight = reply("x" * (1_800 - len(line) - 2), turn="t2")
            started(home, "fix", now=now + 10)
            roomy = reply("Working.", turn="t3")
        self.assertTrue(tight.endswith("\n\n" + line), tight[-120:])
        self.assertTrue(roomy.endswith("· +1 added (repair: Fix the API)"), roomy)

    def test_an_extend_after_the_end_post_continues_the_run_without_a_second_start(self) -> None:
        with homes() as home:
            defined(home)
            now = time.time()
            for node in ("api", "ui"):
                started(home, node, now=now)
            start_post = reply("Dispatched.", turn="t1")
            for node in ("api", "ui", "verify"):
                stop(home, f"child-{node}", now=now + 5)
                write_handoff(home, node)
                call(action="settle", run="export", node=node, outcome="accepted")
            end_post = reply("All passed.", turn="t2")
            call(action="extend", run="export", nodes=[
                {"id": "csv", "label": "Add CSV export", "category": "quick", "prompt": PROMPT, "depends_on": ["api"],
                 "write_scope": ["src/api.py"]}])
            status = call(action="status", run="export")["status_line"]
            reopened = reply("The goal check found CSV missing.", turn="t3")
            write_handoff(home, "csv")
            call(action="settle", run="export", node="csv", outcome="accepted")
            closing = reply("CSV added.", turn="t4")
        self.assertIn("DAG · export feature · 3 tasks · 2 layers", start_post)
        self.assertIn("DAG · export feature · ended", end_post)
        self.assertEqual(status, "DAG export feature · 3/4 done · 0 running · +1 added (extend: Add CSV export)")
        self.assertTrue(reopened.endswith(status), reopened)
        self.assertNotIn("```", reopened)  # no second start block
        self.assertIn("DAG · export feature · ended", closing)
        self.assertIn("4 tasks · 4 done", closing)

    def test_a_subagent_turn_gets_no_post(self) -> None:
        with homes() as home:
            defined(home)
            started(home, "api", now=time.time())
            self.assertIsNone(reply("child reply", platform="subagent"))
            self.assertIn("DAG · export feature", reply("parent reply"))


if __name__ == "__main__":
    unittest.main()
