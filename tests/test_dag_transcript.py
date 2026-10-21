"""The dag-ulw graph printed into the TUI transcript: at define, repair, extend and the run's end, once each.

The `omh_dag` results are the tool's own, from runs kept through its handler;
the app gets them as Hermes' `tool.complete` events and reads the graph the
way its panel does (`dag_block.read_graph`).
"""

from __future__ import annotations

import json
import unittest

from _local_package import load_local_package

load_local_package()

from _delegation_state import PARENT, homes  # noqa: E402
from omh.plugin_bundle.omh import dag_run  # noqa: E402
from omh.plugin_bundle.omh.delegation_graph import build_delegation_graph  # noqa: E402
from omh.tui import dag_block  # noqa: E402
from omh.tui.gateway import Event  # noqa: E402
from omh.tui.transcript import block_rows  # noqa: E402
from test_dag_tool import call, handoff, node, slots_table  # noqa: E402
from _tui_lines import lines_text  # noqa: E402
from test_tui_modes import SID, make_app  # noqa: E402
from test_tui_muscle_memory import send  # noqa: E402
from test_tui_panels import THEME  # noqa: E402

NODES = [node("api", scope=("src/api.py",), label="Export API"), node("ui", scope=("src/ui.py",), label="Export button"),
         node("verify", depends_on=("api", "ui"), label="Verify export")]


def completed(result: dict, *, name: str = "omh_dag", tool_id: str = "t1") -> Event:
    return Event("tool.complete", SID, {"tool_id": tool_id, "name": name, "result": json.dumps(result)})


def run_results(home) -> dict[str, dict]:
    """One result per action of a run that is defined, repaired, settled to done, extended, and done again."""
    slots_table(home)
    results = {"define": call(action="define", run="export", name="export feature", nodes=NODES)}
    results["reused"] = call(action="define", run="export", name="export feature", nodes=NODES)
    results["status"] = call(action="status", run="export")
    handoff(home, "export", "api")
    results["settle"] = call(action="settle", run="export", node="api", outcome="accepted")
    call(action="settle", run="export", node="ui", outcome="failed")
    results["repair"] = call(action="repair", run="export", node="ui", nodes=[
        node("ui-fix", scope=("src/ui.py",), label="Fix the button")])
    handoff(home, "export", "ui-fix")
    call(action="settle", run="export", node="ui-fix", outcome="accepted")
    handoff(home, "export", "verify")
    results["end"] = call(action="settle", run="export", node="verify", outcome="accepted")
    results["status-end"] = call(action="status", run="export")
    results["ready"] = call(action="ready", run="export")
    results["extend"] = call(action="extend", run="export", nodes=[
        node("csv", scope=("src/api.py",), depends_on=("api",), label="CSV export"),
        node("verify-csv", depends_on=("csv",), label="Verify CSV")])
    for node_id in ("csv", "verify-csv"):
        handoff(home, "export", node_id)
        results[f"end-{node_id}"] = call(action="settle", run="export", node=node_id, outcome="accepted")
    return results


class MomentTests(unittest.TestCase):
    def test_define_repair_extend_and_the_end_print_and_nothing_else_does(self) -> None:
        with homes() as home:
            results = run_results(home)
        ended: set[tuple[str, int]] = set()
        seen = {key: dag_block.moment({"name": "omh_dag", "result": json.dumps(result)}, ended)
                for key, result in results.items()}
        self.assertEqual(seen["define"], ("plan", "export", []))
        self.assertEqual(seen["repair"], ("repair · +1 node", "export", ["ui-fix"]))
        self.assertEqual(seen["extend"], ("extend · +2 nodes", "export", ["csv", "verify-csv"]))
        self.assertEqual(seen["end"], ("end · done", "export", []))
        self.assertEqual(seen["end-verify-csv"], ("end · done", "export", []))  # the extended run ends again
        for quiet in ("reused", "status", "settle", "ready", "end-csv"):
            self.assertIsNone(seen[quiet], quiet)
        # A status or ready that reads an ended run is not its end, even where no end was printed yet.
        for late in ("status-end", "ready"):
            self.assertIsNone(dag_block.moment({"name": "omh_dag", "result": json.dumps(results[late])}, set()), late)
        # The same end read again prints nothing: one block per moment.
        self.assertIsNone(dag_block.moment({"name": "omh_dag", "result": json.dumps(results["end"])}, ended))
        self.assertIsNone(dag_block.moment({"name": "terminal", "result": json.dumps(results["define"])}, set()))
        self.assertIsNone(dag_block.moment({"name": "omh_dag", "result": json.dumps(
            {"status": "refused", "action": "define", "reason": "dependency_cycle"})}, set()))


class BlockTests(unittest.TestCase):
    def _graphs(self) -> tuple[dict, dict]:
        """The plan (two producers fanning in to a verification) and the same run after a repair."""
        with homes() as home:
            slots_table(home)
            call(action="define", run="export", name="export feature", nodes=NODES)
            plan = build_delegation_graph(home.omh, home.hermes, PARENT)
            call(action="settle", run="export", node="ui", outcome="failed")
            call(action="repair", run="export", node="ui", nodes=[node("ui-fix", scope=("src/ui.py",),
                                                                        label="Fix the button")])
            return plan, build_delegation_graph(home.omh, home.hermes, PARENT)

    @staticmethod
    def _text(block: dag_block.DagBlock, width: int) -> list[str]:
        return ["".join(span.text for span in line) for line in block_rows(block, width, THEME)]

    def test_boxes_where_they_fit_the_tree_where_they_do_not(self) -> None:
        plan, _repaired = self._graphs()
        wide = self._text(dag_block.DagBlock("plan", plan), 140)
        narrow = self._text(dag_block.DagBlock("plan", plan), 24)  # under one box's width
        self.assertEqual(wide[0].strip(), "dag-ulw plan")
        self.assertTrue(any("┌" in line for line in wide), wide)
        self.assertFalse(any("┌" in line for line in narrow), narrow)
        self.assertTrue(any("├─" in line or "└─" in line for line in narrow), narrow)
        self.assertTrue(all(len(line) <= 24 for line in narrow), narrow)

    def test_an_added_node_names_where_it_came_from_once(self) -> None:
        _plan, repaired = self._graphs()
        block = dag_block.DagBlock("repair · +1 node", repaired)
        boxes, tree = self._text(block, 140), self._text(block, 24)
        self.assertEqual(boxes[0].strip(), "dag-ulw repair · +1 node")
        # As boxes: the drawing's own repair mark, a parents line naming the node it repairs, and the repair edge
        # under Dependencies; the block adds no mark of its own, so none is doubled.
        added = next(index for index, line in enumerate(boxes) if "↻ Fix the button" in line)
        self.assertIn("← Export button", boxes[added + 3])
        self.assertIn("Export button → Fix the button (repair)", "\n".join(boxes))
        self.assertFalse([line for line in boxes if "+ " in line and "Fix" in line], boxes)
        # As the tree: nested under the node it repairs.
        failed = next(index for index, line in enumerate(tree) if "Export bu" in line)
        self.assertTrue(any("Fix" in line for line in tree[failed + 1:failed + 4]), tree)


def turn_ended() -> Event:
    return Event("message.complete", SID, {"text": "done"})


class AppTests(unittest.TestCase):
    @staticmethod
    def _app(graphs: list[dict]) -> object:
        """An app whose graph reads return ``graphs`` in turn (the last one from then on)."""
        app, _client = make_app(rows=60, cols=140)
        app.stored_id = PARENT

        def read(_omh: object, _hermes: object, session: str) -> dict | None:
            if session != PARENT:
                return None
            return graphs.pop(0) if len(graphs) > 1 else graphs[0]

        app.read_dag_graph = read
        return app

    @staticmethod
    def _titles(app) -> list[str]:
        return [block.title for block in app.transcript.blocks + app.transcript._held
                if isinstance(block, dag_block.DagBlock)]

    def test_the_app_appends_one_block_per_moment_and_none_on_a_settle(self) -> None:
        with homes() as home:
            results = run_results(home)
            graph = build_delegation_graph(home.omh, home.hermes, PARENT)
        app = self._app([graph])
        for index, key in enumerate(("define", "status", "settle", "repair", "end", "end")):
            app._on_event(completed(results[key], tool_id=f"t{index}"))
        self.assertEqual(self._titles(app), ["plan", "repair · +1 node"], "the end waits for the turn's end")
        app._on_event(turn_ended())
        for index, key in enumerate(("ready", "extend")):
            app._on_event(completed(results[key], tool_id=f"u{index}"))
        app._on_event(turn_ended())
        self.assertEqual(self._titles(app), ["plan", "repair · +1 node", "end · done", "extend · +2 nodes"])
        self.assertEqual(dag_run.DAG_SCHEMA_VERSION, "omh_dag/v1")

    def test_an_end_the_same_turn_extends_prints_nothing_and_the_last_end_prints_once(self) -> None:
        with homes() as home:
            results = run_results(home)
            graph = build_delegation_graph(home.omh, home.hermes, PARENT)
        app = self._app([graph])
        # The goal check extends the run in the turn that ended it: the extend's block stands for both.
        for index, key in enumerate(("define", "end", "extend")):
            app._on_event(completed(results[key], tool_id=f"t{index}"))
        app._on_event(turn_ended())
        self.assertEqual(self._titles(app), ["plan", "extend · +2 nodes"])
        app._on_event(completed(results["end-verify-csv"], tool_id="t9"))
        app._on_event(turn_ended())
        app._on_event(turn_ended())
        self.assertEqual(self._titles(app), ["plan", "extend · +2 nodes", "end · done"])

    def test_an_end_whose_run_moved_on_before_the_turn_ended_prints_nothing(self) -> None:
        with homes() as home:
            results = run_results(home)
            graph = build_delegation_graph(home.omh, home.hermes, PARENT)
        app = self._app([graph, {**graph, "dag_open": True}])
        app._on_event(completed(results["define"], tool_id="t0"))
        app._on_event(completed(results["end"], tool_id="t1"))
        app._on_event(turn_ended())
        self.assertEqual(self._titles(app), ["plan"])


class ToolRowTests(unittest.TestCase):
    def test_a_delegate_task_row_shows_the_node_labels_not_their_markers(self) -> None:
        app, _client = make_app(rows=30, cols=140)
        app._on_event(Event("tool.start", SID, {"tool_id": "d1", "name": "delegate_task",
                                                "context": "2 tasks: Export API [dag:export/api] | Export docs [dag:export/docs]"}))
        app._on_event(Event("tool.start", SID, {"tool_id": "d2", "name": "terminal", "context": "echo [dag:a/b] kept"}))
        text = "\n".join(lines_text(app.transcript.live_lines(140, THEME)))
        self.assertIn("delegate_task(2 tasks: Export API | Export docs)", text)
        self.assertIn("terminal(echo [dag:a/b] kept)", text)  # only a delegate_task preview carries node goals

    def test_a_marker_hermes_cut_goes_too(self) -> None:
        """Hermes cuts each goal of the preview to 40 characters (`agent/display.py` `_tail_trunc`, `...`), so a label
        shorter than 37 characters can leave the head of its marker; these are its previews at d923a5eb48."""
        cases = {
            "1 tasks: Wire the export button to API [dag:ex...": "1 tasks: Wire the export button to API",
            "1 tasks: Document the CSV export in the API [d...": "1 tasks: Document the CSV export in the API",
            "1 tasks: Document the CSV export in the APIs [...": "1 tasks: Document the CSV export in the APIs",
            "2 tasks: Refresh docs [omh-team:hud-graph/docs... | Fix the parser [omh-team:...":
                "2 tasks: Refresh docs | Fix the parser",
            "2 tasks: Fix [omh-team:t1/a/attempt-1] | Docs [omh-team:t1/b/attempt-1]": "2 tasks: Fix | Docs",
            # A goal handed out before an update opens with its marker; a lone `goal` argument previews as itself.
            "2 tasks: [dag:export/ui] Export button | [dag:export/docs] Export docs": "2 tasks: Export button | Export docs",
            "[dag:export/ui] Export button": "Export button",
            # A whole marker goes wherever it is: a model may add text after it.
            "1 tasks: Export API [dag:export/api]. Please h...": "1 tasks: Export API. Please h...",
            "1 tasks: Export API [dag:export/api] Extra ins...": "1 tasks: Export API Extra ins...",
            # Only a head that ends its goal is a cut marker.
            "1 tasks: Explain the [dag:...] marker syntax": "1 tasks: Explain the [dag:...] marker syntax",
            # A cut that took no marker keeps its `...`, and so does a label's own bracket.
            "1 tasks: Render invoice PDFs from the new temp...": "1 tasks: Render invoice PDFs from the new temp...",
            "1 tasks: Handle [...] in args [dag:a/b]": "1 tasks: Handle [...] in args",
            # A head too short to name its kind goes only after the space OMH writes before a marker.
            "1 tasks: Investigate the flaky test in tests/[...": "1 tasks: Investigate the flaky test in tests/[...",
        }
        for context, shown in cases.items():
            with self.subTest(context=context):
                app, _client = make_app(rows=30, cols=140)
                app._on_event(Event("tool.start", SID, {"tool_id": "d1", "name": "delegate_task", "context": context}))
                text = "\n".join(lines_text(app.transcript.live_lines(140, THEME)))
                self.assertIn(f"delegate_task({shown})", text)


class PagerTests(unittest.TestCase):
    def test_dag_closes_a_runs_graph_with_its_critical_path_and_bottlenecks(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="export", name="export feature", nodes=NODES)
            graph = build_delegation_graph(home.omh, home.hermes, PARENT)
        app, _client = make_app(rows=30, cols=120)
        app.hud = type("Hud", (), {"payload": {"privacy": "metadata_only", "delegation_graph": graph},
                                   "received_at": None})()
        send(app, "/dag")
        app._render()  # the pager draws the HUD's newest read at each frame
        rows = lines_text(app.pager.lines)
        self.assertTrue(any("┌" in row for row in rows), rows)
        self.assertEqual(rows[-3:], ["", "critical path: Export API → Verify export",
                                     "bottlenecks: Export API (blocks 1) · Export button (blocks 1)"])

    def test_a_graph_that_is_not_a_dag_run_gets_no_topology_lines(self) -> None:
        from _delegation_graphs import owner_mock

        app, _client = make_app(rows=30, cols=120)
        app.hud = type("Hud", (), {"payload": {"privacy": "metadata_only", "delegation_graph": owner_mock()},
                                   "received_at": None})()
        send(app, "/dag")
        app._render()
        rows = lines_text(app.pager.lines)
        self.assertTrue(rows)
        self.assertFalse([row for row in rows if row.startswith(("critical path", "bottlenecks"))], rows)


if __name__ == "__main__":
    unittest.main()
