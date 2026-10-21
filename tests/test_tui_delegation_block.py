"""The delegation block in the OMH TUI at the heights people have, the layout kept per structure, and `/dag`.

The panel gets a third of the terminal's rows (`layout.slot_cap`), so the
block is drawn with boxes, three-line boxes or the tree depending on that
height. `TIERS` is what each common size gets, read off the drawing: a box
holds four lines (title, category and model, status, parents), a three-line
box leaves out its category and model, and the tree has branches and no box.
The boxes with their Dependencies and Node details are taller than a third of
50 rows for these graphs, so the panel draws three-line boxes or the tree.
"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from _delegation_graphs import export_run, owner_mock, parallel  # noqa: E402
from _tui_lines import lines_text  # noqa: E402
from omh.plugin_bundle.omh import delegation_view  # noqa: E402
from omh.plugin_bundle.omh.delegation_view import render_delegation_view  # noqa: E402
from omh.tui.dag_pager import DAG_KEYS, DagPager  # noqa: E402
from omh.tui.keys import CHAR, DOWN, ENTER, ESCAPE, UP, Key  # noqa: E402
from omh.tui.keys import TAB as TAB_KEY  # noqa: E402
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
        **{size: "tree" for size in SIZES if size[0] < 40},
        (40, 80): "tree", (40, 100): "compact", (40, 120): "compact",
        (50, 80): "compact", (50, 100): "compact", (50, 120): "compact",
    },
    "owner mock-up": {size: "tree" for size in SIZES},
    "six lanes": {
        **{size: "tree" for size in SIZES if size[0] < 50},
        (50, 80): "tree", (50, 100): "compact", (50, 120): "compact",
    },
}
GRAPHS = {"three lanes": parallel(3), "owner mock-up": owner_mock(), "six lanes": parallel(6)}

THREE_40x100 = """\
[Agents] │ ready
  OMH / DAG · 3 tasks
  delegation · 1/1 · main opus-5-5:high
  Running · Done 0/3

  ┌────────────────────────┐  ┌────────────────────────┐  ┌────────────────────────┐
  │ Lane 0 migrate         │  │ Lane 1 migrate         │  │ Lane 2 migrate         │
  │ ● Running 1m 0s        │  │ ● Running 1m 1s        │  │ ● Running 1m 2s        │
  │ Start node             │  │ Start node             │  │ Start node             │
  └────────────────────────┘  └────────────────────────┘  └────────────────────────┘
  · Same frontier
"""

SIX_50x120 = """\
[Agents] │ ready
  OMH / DAG · 6 tasks
  delegation · 1/1 · main opus-5-5:high
  Running · Done 0/6

  ┌────────────────────────┐  ┌────────────────────────┐  ┌────────────────────────┐  ┌────────────────────────┐
  │ Lane 0 migrate         │  │ Lane 1 migrate         │  │ Lane 2 migrate         │  │ Lane 3 migrate         │
  │ ● Running 1m 0s        │  │ ● Running 1m 1s        │  │ ● Running 1m 2s        │  │ ● Running 1m 3s        │
  │ Start node             │  │ Start node             │  │ Start node             │  │ Start node             │
  └────────────────────────┘  └────────────────────────┘  └────────────────────────┘  └────────────────────────┘
  ┌────────────────────────┐  ┌────────────────────────┐
  │ Lane 4 migrate         │  │ Lane 5 migrate         │
  │ ● Running 1m 4s        │  │ ● Running 1m 5s        │
  │ Start node             │  │ Start node             │
  └────────────────────────┘  └────────────────────────┘
  · Same frontier
"""


def tier(rows: list[str]) -> str:
    tops = [at for at, row in enumerate(rows) if "┌" in row]
    if tops:
        bottom = next(at for at in range(tops[0], len(rows)) if "└" in rows[at])
        return "box" if bottom - tops[0] - 1 == 4 else "compact"
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

    def test_three_line_boxes_at_40_and_50_rows(self) -> None:
        for snapshot, graph, size in ((THREE_40x100, parallel(3), (40, 100)), (SIX_50x120, parallel(6), (50, 120))):
            with self.subTest(size=size):
                self.assertEqual("\n".join(row.rstrip() for row in panel(graph, *size)) + "\n", snapshot)

    def test_the_tree_stays_the_last_fallback(self) -> None:
        rows = panel(owner_mock(), 24, 80)
        self.assertEqual(tier(rows), "tree")
        self.assertLessEqual(len(rows), slot_cap(24, 80))

    def test_three_line_boxes_are_the_boxes_without_facts_or_sections(self) -> None:
        for name, graph in GRAPHS.items():
            full = render_delegation_view(graph, width=118, layout="box")
            compact = render_delegation_view(graph, width=118, layout="compact")
            with self.subTest(graph=name):
                self.assertEqual(compact.layout, "compact")
                self.assertEqual([box[0] for box in compact.boxes], [box[0] for box in full.boxes], "the same boxes")
                self.assertEqual(compact.text().count("▼"), full.text().count("▼"), "one connector per frontier gap")
                self.assertNotIn("Dependencies", compact.text_lines())
                self.assertNotIn("Node details", compact.text_lines())
                self.assertLess(len(compact.lines), len(full.lines))
        drawn = render_delegation_view(owner_mock(), width=98, layout="compact").text()
        self.assertIn("│ Integration verify ", drawn)
        self.assertIn("│ ○ Waits on 3 lanes ", drawn)
        self.assertNotIn("opus-5-5", drawn.split("\n", 3)[3], "no category and model line in a box")


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
        widths = []
        for graph in (short, long):
            rows = render_delegation_view(graph, width=118, layout="box").text_lines()
            widths.append([len(row) for row in rows[:rows.index("Node details")]])  # a card's status is not boxed
        self.assertEqual(widths[0], widths[1])


def with_hud(app, graph: dict | None) -> None:
    payload = {"privacy": "metadata_only"}
    if graph is not None:
        payload["delegation_graph"] = graph
    app.hud = type("Hud", (), {"payload": payload, "received_at": None})()


def screen(app) -> list[str]:
    """What the terminal shows now, after the app paints its frame."""
    app._render()
    return [row.rstrip() for row in app.terminal.screen.lines()]


def press(app, *keys: Key) -> list[str]:
    for key in keys:
        app._on_input(key)
    return screen(app)


TAB, SHIFT_TAB, SPACE = Key(TAB_KEY), Key(TAB_KEY, shift=True), Key(CHAR, " ")


class DagCommandTests(unittest.TestCase):
    def test_dag_opens_the_boxes_on_the_running_node_and_q_closes_it(self) -> None:
        app, client = make_app(rows=60, cols=100, environ={"COLORTERM": "truecolor", "TERM": "xterm-256color"})
        with_hud(app, export_run())
        send(app, "/dag")
        self.assertIsInstance(app.pager, DagPager)
        rows = screen(app)
        self.assertEqual(rows[0], "OMH / DAG · 5 tasks")
        self.assertIn("│ [-] > Export button", "\n".join(rows), "it opens on the running node")
        self.assertIn("Node details", rows)
        self.assertEqual(rows[-2:], [f" {DAG_KEYS}", " delegation graph · rows 1-40/40"])
        self.assertNotIn("Runs", rows[-2], "one graph per conversation: no other run to switch to")
        spans = [span for line in app.pager.lines for span in line if span.text.strip()]
        self.assertTrue(all(span.style.depth == TRUECOLOR for span in spans))
        self.assertTrue(any(span.style.fg for span in spans if "│" in span.text), "the borders take their state's colour")
        app._on_input(Key(CHAR, "q"))
        self.assertIsNone(app.pager)
        send(app, "/dag")
        app._on_input(Key(ESCAPE))
        self.assertIsNone(app.pager, "Esc closes it too")
        self.assertEqual(client.calls, [], "nothing in /dag reaches the gateway")

    def test_tab_and_shift_tab_move_the_highlight_in_reading_order(self) -> None:
        app, client = make_app(rows=60, cols=100)
        with_hud(app, export_run())
        send(app, "/dag")
        screen(app)
        order = []
        for key in (TAB, TAB, TAB, TAB, Key(CHAR, "n")):
            press(app, key)
            order.append(app.pager.node)
        self.assertEqual(order, ["d:r1:docs", "d:r1:cli", "d:r1:verify", "d:r1:api", "d:r1:button"], "and round again")
        for key in (SHIFT_TAB, Key(CHAR, "p")):
            press(app, key)
        self.assertEqual(app.pager.node, "d:r1:verify")
        rows = "\n".join(screen(app))
        self.assertIn("│ [-] > Verify export", rows)
        self.assertEqual(rows.count(" > "), 1, "one box is highlighted")
        self.assertIn("  ○ Verify export", rows, "the highlighted node has a card")
        self.assertEqual(client.calls, [])

    def test_space_or_enter_folds_and_d_switches_node_details(self) -> None:
        app, _client = make_app(rows=60, cols=100)
        with_hud(app, export_run())
        send(app, "/dag")
        screen(app)
        rows = press(app, SPACE)
        self.assertIn("│ [+] > ● Export button", "\n".join(rows), "folded to its title line, its state's glyph kept")
        self.assertNotIn("● Running 21s", "\n".join(rows[:20]))
        rows = press(app, Key(ENTER))
        self.assertIn("│ [-] > Export button", "\n".join(rows))
        rows = press(app, Key(CHAR, "d"))
        self.assertNotIn("Node details", rows)
        rows = press(app, Key(CHAR, "d"))
        self.assertIn("Node details", rows)

    def test_the_highlight_is_scrolled_into_view_and_the_arrows_scroll(self) -> None:
        app, _client = make_app(rows=16, cols=100)
        with_hud(app, export_run())
        send(app, "/dag")
        rows = screen(app)
        self.assertEqual(app.pager.top, 4, "the running node it opens on is scrolled whole into the 14 rows")
        self.assertTrue(rows[8].startswith("┌") and rows[9].startswith("│ [-] > Export button")
                        and rows[13].startswith("└"), rows)
        rows = press(app, SHIFT_TAB, SHIFT_TAB)  # Export button, then back past Export API to Verify export
        self.assertEqual(app.pager.node, "d:r1:verify")
        self.assertTrue(any("[-] > Verify export" in row for row in rows[:14]), rows)
        top = app.pager.top
        press(app, Key(UP))
        self.assertEqual(app.pager.top, top - 1)
        press(app, Key(DOWN), Key(DOWN))
        self.assertEqual(app.pager.top, top + 1)
        press(app, Key(CHAR, "g"))
        self.assertEqual(app.pager.top, 0)

    def test_dag_is_live_from_the_newest_hud_read(self) -> None:
        app, client = make_app(rows=60, cols=100)
        with_hud(app, export_run())
        send(app, "/dag")
        self.assertIn("│ ● Running 21s", "\n".join(screen(app)))
        later = export_run()
        later["nodes"][2].update(state="done", status="accepted", elapsed_seconds=40)
        later["nodes"][3].update(state="running", status="running", elapsed_seconds=5, model="claude-opus-5-5",
                                 effort="medium")
        with_hud(app, later)
        rows = "\n".join(screen(app))
        self.assertIn("✓ Accepted 40s", rows)
        self.assertIn("● Running 5s", rows)
        self.assertEqual(app.pager.node, "d:r1:button", "the highlight stays on its node while that node is drawn")
        with_hud(app, None)
        self.assertIn("✓ Accepted 40s", "\n".join(screen(app)), "a graph that aged out leaves the last drawing")
        self.assertEqual(client.calls, [])

    def test_dag_follows_the_terminals_depth(self) -> None:
        app, _client = make_app(rows=24, cols=100, environ={"NO_COLOR": "1"})
        with_hud(app, parallel(3))
        send(app, "/dag")
        screen(app)
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
