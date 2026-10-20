"""The delegation block in the OMH TUI at the heights people have, the layout kept per structure, and `/dag`.

The panel gets a third of the terminal's rows (`layout.slot_cap`), so the
block is drawn with four-line boxes, two-line boxes or the tree depending on
that height. `TIERS` is what each common size gets, read off the drawing:
two-line boxes take their arrows into their top border (`─▼─`), four-line
boxes draw them on a row of their own, and the tree has branches and no box.
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
from omh.tui.layout import slot_cap  # noqa: E402
from omh.tui.panels import panel_lines  # noqa: E402
from omh.tui.style import NO_COLOR, TRUECOLOR  # noqa: E402
from omh.tui.theme import DEFAULT_THEME  # noqa: E402
from test_tui_modes import make_app  # noqa: E402
from test_tui_muscle_memory import send  # noqa: E402

SIZES = [(rows, cols) for rows in (24, 30, 40, 50) for cols in (80, 100, 120)]
# (rows, columns) -> the tier each graph gets in the panel's third of the screen.
TIERS = {
    "three lanes": {
        (24, 80): "tree", (24, 100): "tree", (24, 120): "tree",
        (30, 80): "compact", (30, 100): "compact", (30, 120): "compact",
        (40, 80): "compact", (40, 100): "compact", (40, 120): "compact",
        (50, 80): "box", (50, 100): "box", (50, 120): "box",
    },
    "owner mock-up": {
        **{size: "tree" for size in SIZES if size[0] < 50},
        (50, 80): "compact", (50, 100): "compact", (50, 120): "compact",
    },
    "six lanes": {
        **{size: "tree" for size in SIZES if size[0] == 24},
        (30, 80): "tree", (30, 100): "tree", (30, 120): "compact",
        (40, 80): "tree", (40, 100): "tree", (40, 120): "compact",
        (50, 80): "compact", (50, 100): "compact", (50, 120): "box",
    },
}
GRAPHS = {"three lanes": parallel(3), "owner mock-up": owner_mock(), "six lanes": parallel(6)}

THREE_30x100 = """\
[Agents] │ ready
  DAG · delegation · 4 nodes · 3 edges · 3 running
                                   ┌──────────────────────────┐
                                   │ ◆ main · opus-5-5 · high │
                                   └─────────────┬────────────┘
                 ┌───────────────────────────────┼───────────────────────────────┐
  ┌──────────────▼─────────────┐  ┌──────────────▼─────────────┐  ┌──────────────▼─────────────┐
  │ ● Lane 0 migrate           │  │ ● Lane 1 migrate           │  │ ● Lane 2 migrate           │
  │ quick · glm-5.3-flash      │  │ quick · glm-5.3-flash      │  │ quick · glm-5.3-flash      │
  └────────────────────────────┘  └────────────────────────────┘  └────────────────────────────┘
"""

MOCK_50x100 = """\
[Agents] │ ready
  DAG · ulw-work hud-graph · 5 nodes · 6 edges · 2 running · 1 waiting · 1 done
                                   ┌──────────────────────────┐
                                   │ ◆ main · opus-5-5 · high │
                                   └─────────────┬────────────┘
                 ┌───────────────────────────────┼───────────────────────────────┐
  ┌──────────────▼─────────────┐  ┌──────────────▼─────────────┐  ┌──────────────▼─────────────┐
  │ ● Refresh HUD docs         │  │ ✓ Polish HUD CLI           │  │ ● Update HUD tests         │
  │ writing · opus-5-5         │  │ quick · glm-5.3-flash      │  │ deep · opus-5-5 · high     │
  └──────────────┬─────────────┘  └──────────────┬─────────────┘  └──────────────┬─────────────┘
                 └───────────────────────────────┼───────────────────────────────┘
                                  ┌──────────────▼─────────────┐
                                  │ · Integration verify       │
                                  │ inherit · opus-5-5         │
                                  └────────────────────────────┘
"""

SIX_40x120 = """\
[Agents] │ ready
  DAG · delegation · 7 nodes · 6 edges · 6 running
                                               ┌──────────────────────────┐
                                               │ ◆ main · opus-5-5 · high │
                                               └─────────────┬────────────┘
           ┌───────────────────┬───────────────────┬─────────┴─────────┬───────────────────┬───────────────────┐
  ┌────────▼───────┐  ┌────────▼───────┐  ┌────────▼───────┐  ┌────────▼───────┐  ┌────────▼───────┐  ┌────────▼───────┐
  │ ● Lane 0 migr… │  │ ● Lane 1 migr… │  │ ● Lane 2 migr… │  │ ● Lane 3 migr… │  │ ● Lane 4 migr… │  │ ● Lane 5 migr… │
  │ quick · glm-5… │  │ quick · glm-5… │  │ quick · glm-5… │  │ quick · glm-5… │  │ quick · glm-5… │  │ quick · glm-5… │
  └────────────────┘  └────────────────┘  └────────────────┘  └────────────────┘  └────────────────┘  └────────────────┘
"""


def tier(rows: list[str]) -> str:
    if any("┌" in row and "▼" in row for row in rows):
        return "compact"
    if any("┌" in row for row in rows):
        return "box"
    if any(row.strip().startswith(("├─", "└─")) for row in rows):
        return "tree"
    return "header"


def panel(graph: dict, rows: int, cols: int) -> list[str]:
    payload = {"privacy": "metadata_only", "delegation_graph": graph}
    return lines_text(panel_lines(payload, cols, slot_cap(rows, cols), DEFAULT_THEME, 0.0))


class TierTests(unittest.TestCase):
    def test_each_common_size_gets_its_tier(self) -> None:
        for name, graph in GRAPHS.items():
            for size in SIZES:
                with self.subTest(graph=name, size=size):
                    self.assertEqual(tier(panel(graph, *size)), TIERS[name][size])

    def test_two_line_boxes_at_30_40_and_50_rows(self) -> None:
        for snapshot, graph, size in ((THREE_30x100, parallel(3), (30, 100)), (MOCK_50x100, owner_mock(), (50, 100)),
                                      (SIX_40x120, parallel(6), (40, 120))):
            with self.subTest(size=size):
                self.assertEqual("\n".join(row.rstrip() for row in panel(graph, *size)) + "\n", snapshot)

    def test_the_tree_stays_the_last_fallback(self) -> None:
        rows = panel(owner_mock(), 24, 80)
        self.assertEqual(tier(rows), "tree")
        self.assertLessEqual(len(rows), slot_cap(24, 80))

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
