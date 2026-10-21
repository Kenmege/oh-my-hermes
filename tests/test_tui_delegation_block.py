"""The delegation block in the OMH TUI: the tree under the info line at the heights people have, and `/dag`.

The agent view under the info line gets `layout.agent_view_cap` rows (seven
at most unless running lanes raise it), under the nine rows even two-line
boxes take for one layer under main, so the panel draws the delegation graph
as its tree at every size (the owner's call of 2026-10-21: the agent view goes
under the prompt). `/dag` draws the four-line boxes in a pager. The layout
cache cases drive `delegation_view` itself.
"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from _delegation_graphs import owner_mock, parallel  # noqa: E402
from _tui_lines import lines_text  # noqa: E402
from omh.plugin_bundle.omh import delegation_view  # noqa: E402
from omh.plugin_bundle.omh.delegation_view import render_delegation_view  # noqa: E402
from omh.tui.keys import CHAR, ESCAPE, Key  # noqa: E402
from omh.tui.layout import AGENT_VIEW_ROWS, agent_view_cap, slot_cap  # noqa: E402
from omh.tui.panels import agent_view_keep, panel_lines  # noqa: E402
from omh.tui.style import NO_COLOR, TRUECOLOR  # noqa: E402
from omh.tui.theme import DEFAULT_THEME  # noqa: E402
from test_tui_modes import make_app  # noqa: E402
from test_tui_muscle_memory import send  # noqa: E402

SIZES = [(rows, cols) for rows in (12, 24, 30, 40, 50) for cols in (40, 80, 100, 120)]
GRAPHS = {"three lanes": parallel(3), "owner mock-up": owner_mock(), "six lanes": parallel(6), "twelve": parallel(12)}

MOCK_24x80 = """\
  DAG · ulw-work hud-graph · 5 nodes · 6 edges · 2 running · 1 waiting · 1 done
  ◆ main · opus-5-5:high
  ├─ ● Refresh HUD docs    writing  opus-5-5:medium    running 2m 10s
  ├─ ✓ Polish HUD CLI      quick    glm-5.3-flash:low  done 1m 2s
  ├─ ● Update HUD tests    deep     opus-5-5:high      running 2m 8s
  └─ · Integration verify  inherit  opus-5-5           waits on 3 lanes
"""


def tier(rows: list[str]) -> str:
    if any("┌" in row and "▼" in row for row in rows):
        return "compact"
    if any("┌" in row for row in rows):
        return "box"
    if any(row.strip().startswith(("├─", "└─")) for row in rows):
        return "tree"
    return "header"


def payload_of(graph: dict) -> dict:
    return {"privacy": "metadata_only", "delegation_graph": graph}


def panel(graph: dict, rows: int, cols: int) -> list[str]:
    """The block as the agent view draws it at this size: in its cap, raised for the running lanes."""
    cap = agent_view_cap(rows, cols, agent_view_keep(payload_of(graph), cols))
    return lines_text(panel_lines(payload_of(graph), cols, cap, DEFAULT_THEME, 0.0))


class TreeTests(unittest.TestCase):
    def test_every_common_size_gets_the_tree_and_never_a_box(self) -> None:
        for name, graph in GRAPHS.items():
            for rows, cols in SIZES:
                with self.subTest(graph=name, size=(rows, cols)):
                    drawn = panel(graph, rows, cols)
                    self.assertEqual(tier(drawn), "tree")
                    self.assertLessEqual(len(drawn), agent_view_cap(rows, cols, 99))

    def test_the_owner_mock_up_under_the_info_line(self) -> None:
        self.assertEqual("\n".join(row.rstrip() for row in panel(owner_mock(), 24, 80)) + "\n", MOCK_24x80)

    def test_running_lanes_raise_the_cap_up_to_a_third_of_the_screen_then_the_tree_says_how_many_more(self) -> None:
        twelve = panel(parallel(12), 24, 100)  # twelve running lanes: more than a third of 24 rows holds
        self.assertEqual(len(twelve), 24 // 3)
        self.assertTrue(twelve[-1].strip().startswith("└─ +"), twelve)
        self.assertIn("more nodes", twelve[-1])
        three = panel(parallel(3), 24, 100)
        self.assertEqual(len(three), 5, "the header, main and three lanes: the usual cap holds them")
        self.assertFalse([row for row in three if "more nodes" in row])

    def test_two_line_boxes_take_more_rows_than_the_view_usually_has(self) -> None:
        for name, graph in GRAPHS.items():
            with self.subTest(graph=name):
                compact = render_delegation_view(graph, width=118, layout="compact")
                self.assertEqual(compact.layout, "compact")
                self.assertGreaterEqual(len(compact.lines), 9)
                self.assertGreater(len(compact.lines), AGENT_VIEW_ROWS)

    def test_two_line_boxes_keep_the_same_edges_and_rules(self) -> None:
        for name, graph in GRAPHS.items():
            full = render_delegation_view(graph, width=118, layout="box")
            compact = render_delegation_view(graph, width=118, layout="compact")
            with self.subTest(graph=name):
                self.assertEqual(compact.layout, "compact")
                self.assertEqual(compact.text().count("▼"), full.text().count("▼"), "one arrow per recorded edge end")
                self.assertLess(len(compact.lines), len(full.lines))
        drawn = render_delegation_view(owner_mock(), width=98, layout="compact").text()
        self.assertIn("│ deep · opus-5-5 · high", drawn, "the effort stays where it fits")
        self.assertIn("│ writing · opus-5-5 ", drawn, "and gives way where it does not")


class LayoutCacheTests(unittest.TestCase):
    def test_new_durations_redraw_without_choosing_the_layout_again(self) -> None:
        graph = parallel(12)
        render_delegation_view(graph, width=118, max_lines=13, compact=True)
        later = copy.deepcopy(graph)
        for node in later["nodes"][1:]:
            node["elapsed_seconds"] += 3_600 * 3  # a duration grows from minutes to hours
        with patch.object(delegation_view, "_choose", wraps=delegation_view._choose) as choose:
            redrawn = render_delegation_view(later, width=118, max_lines=13, compact=True)
            self.assertEqual(choose.call_count, 0, "same structure: the kept decision is redrawn")
            changed = copy.deepcopy(later)
            changed["nodes"][1]["state"] = "done"
            render_delegation_view(changed, width=118, max_lines=13, compact=True)
            self.assertEqual(choose.call_count, 1, "a state change is a new structure")
        self.assertEqual(redrawn.layout, render_delegation_view(graph, width=118, max_lines=13, compact=True).layout)

    def test_a_box_is_as_wide_whatever_its_duration_reads(self) -> None:
        short, long = copy.deepcopy(parallel(3)), copy.deepcopy(parallel(3))
        for graph, elapsed in ((short, 5), (long, 3_599)):
            for node in graph["nodes"][1:]:
                # Every other line is short, so the status line (`running 5s`, `running 59m 59s`) is the widest.
                node.update(title="A", category="c", model="m", effort="", elapsed_seconds=elapsed)
        widths = [
            [len(row) for row in render_delegation_view(graph, width=118, layout="box").text_lines()]
            for graph in (short, long)
        ]
        self.assertEqual(widths[0], widths[1])


def with_hud(app, graph: dict | None) -> None:
    payload = {"privacy": "metadata_only"}
    if graph is not None:
        payload["delegation_graph"] = graph
    app.hud = type("Hud", (), {"payload": payload, "received_at": None})()


class DagCommandTests(unittest.TestCase):
    def test_dag_opens_the_whole_graph_with_four_line_boxes_and_q_closes_it(self) -> None:
        app, _client = make_app(rows=24, cols=100, environ={"COLORTERM": "truecolor", "TERM": "xterm-256color"})
        with_hud(app, owner_mock())
        send(app, "/dag")
        self.assertIsNotNone(app.pager)
        self.assertEqual(app.pager.title, "delegation graph")
        rows = lines_text(app.pager.lines)
        self.assertEqual(tier(rows), "box", "full boxes, however short the terminal")
        self.assertIn("│ waits on 3 lanes", "\n".join(rows))
        self.assertGreater(len(rows), slot_cap(24, 100), "the pager scrolls; the panel would have drawn the tree")
        edges = [span for line in app.pager.lines for span in line if span.text.strip("─│┌┐└┘┬┴┼▼ ") == ""
                 and span.text.strip()]
        self.assertTrue(edges and all(span.style.fg for span in edges), "the same colours as the panel")
        self.assertTrue(all(span.style.depth == TRUECOLOR for line in app.pager.lines for span in line))
        app._on_input(Key(CHAR, "q"))
        self.assertIsNone(app.pager)
        send(app, "/dag")
        app._on_input(Key(ESCAPE))
        self.assertIsNone(app.pager, "Esc closes it too")

    def test_dag_follows_the_terminals_depth(self) -> None:
        app, _client = make_app(rows=24, cols=100, environ={"NO_COLOR": "1"})
        with_hud(app, parallel(3))
        send(app, "/dag")
        self.assertTrue(all(";" not in span.style.sgr() for line in app.pager.lines for span in line))
        self.assertEqual(app.color_depth, NO_COLOR)

    def test_no_graph_is_one_line_saying_so(self) -> None:
        for graph in (None, {"status": "inactive", "nodes": []}):
            with self.subTest(graph=graph):
                app, _client = make_app()
                with_hud(app, graph)
                send(app, "/dag")
                self.assertIsNone(app.pager)
                said = [getattr(block, "text", "") for block in app.transcript.blocks]
                self.assertIn("No delegation graph: this session has no recent delegated work.", said)


if __name__ == "__main__":
    unittest.main()
