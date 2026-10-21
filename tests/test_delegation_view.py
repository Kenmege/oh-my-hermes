"""The delegation view: the boxes for a dag run and its variants, the tree, and what it never draws.

The snapshots are the drawings themselves, reviewed as drawings: the export
run (five nodes over three frontiers, one in each state a box colours
apart) at 100 columns and in ASCII at 80, and the tree a narrow terminal or
a tight budget gets. The other tests hold what one drawing cannot: each
box's four lines, frontiers side by side with their label, one connector per
frontier gap and its colour, Dependencies that are the graph's edges
exactly, Node details only for a running or highlighted node, the width
fallback, the roles a painter colours by state, `/dag`'s fold and highlight,
no line wider than its width in terminal cells, and no exception for
anything malformed.
"""

from __future__ import annotations

import random
import re
import unittest

from _local_package import load_local_package

load_local_package()

from _delegation_graphs import (  # noqa: E402
    dag_export_repair_run,
    dag_export_run,
    graph,
    lane,
    nested,
    owner_mock,
    parallel,
    root,
    with_tokens,
)
from omh.plugin_bundle.omh.delegation_view import (  # noqa: E402
    BOX_MIN_WIDTH,
    Focus,
    cells,
    clean_text,
    render_delegation_view,
)

EXPORT100 = """\
OMH / DAG · 5 tasks
export feature · 1/1 · main opus-5-5:high
Running · Done 1/5 · 1 failed

                                  ┌──────────────────────────────┐
                                  │ Export API                   │
                                  │ quick · glm-5.3-flash:low    │
                                  │ ✓ Accepted 12s               │
                                  │ Start node                   │
                                  └──────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────┐  ┌──────────────────────────────┐  ┌──────────────────────────────┐
│ Export button                │  │ Export docs                  │  │ Export CLI flag              │
│ unspecified-high · glm-5.3:… │  │ writing                      │  │ quick · glm-5.3-flash:low    │
│ ● Running 21s                │  │ ◌ Handed out                 │  │ ✗ Failed 8s                  │
│ ← Export API                 │  │ ← Export API                 │  │ ← Export API                 │
└──────────────────────────────┘  └──────────────────────────────┘  └──────────────────────────────┘
· Same frontier
                                                  │
                                                  ▼
                                  ┌──────────────────────────────┐
                                  │ Verify export                │
                                  │ unspecified-high             │
                                  │ ○ Waits on 3 lanes           │
                                  │ ← Export button, Export doc… │
                                  └──────────────────────────────┘

Dependencies
  Export API → Export button
  Export API → Export docs
  Export API → Export CLI flag
  Export button → Verify export
  Export docs → Verify export
  Export CLI flag → Verify export

Node details
  ● Export button
    unspecified-high · glm-5.3:low
    Running 21s · layer 2 · session 7c41e9a2
"""

EXPORT80ASCII = """\
OMH / DAG - 5 tasks
export feature - 1/1 - main opus-5-5:high
Running - Done 1/5 - 1 failed

                  +--------------------------------+
                  | Export API                     |
                  | quick - glm-5.3-flash:low      |
                  | + Accepted 12s                 |
                  | Start node                     |
                  +--------------------------------+
                                   |
                                   v
+--------------------------------+  +--------------------------------+
| Export button                  |  | Export docs                    |
| unspecified-high - glm-5.3:low |  | writing                        |
| * Running 21s                  |  | o Handed out                   |
| <- Export API                  |  | <- Export API                  |
+--------------------------------+  +--------------------------------+
+--------------------------------+
| Export CLI flag                |
| quick - glm-5.3-flash:low      |
| x Failed 8s                    |
| <- Export API                  |
+--------------------------------+
- Same frontier
                                   |
                                   v
                  +--------------------------------+
                  | Verify export                  |
                  | unspecified-high               |
                  | . Waits on 3 lanes             |
                  | <- Export button, Export do... |
                  +--------------------------------+

Dependencies
  Export API -> Export button
  Export API -> Export docs
  Export API -> Export CLI flag
  Export button -> Verify export
  Export docs -> Verify export
  Export CLI flag -> Verify export

Node details
  * Export button
    unspecified-high - glm-5.3:low
    Running 21s - layer 2 - session 7c41e9a2
"""

NESTED34TREE = """\
DAG · delegation · 7 nodes · 6 ed…
3 running · 2 dispatched · 1 done
◆ main · opus-5-5:high
├─ ● Port the auth module
│    architect · opu… · running 6…
│  ├─ ✓ Port token store
│  │    inherit · opu… · done 2m…
│  └─ ● Sub-orchestrate refresh
│       inherit · opu… · running…
│     ├─ ◌ Refresh tokens
│     │    ? · dispatched
│     └─ ◌ Rotate keys
│          ? · dispatched
└─ ● 인증 모듈 문서 정리
     writing · opu… · running 5m…
"""

NESTED40BUDGET = """\
DAG · delegation · 7 nodes · 6 edges
3 running · 2 dispatched · 1 done
◆ main · opus-5-5:high
├─ ● Port the auth module
│    architect · opus-… · running 6m 40s
│  ├─ ✓ Port token store
│  │    inherit · opus-5-… · done 2m 11s
│  └─ ● Sub-orchestrate refresh
│       inherit · opus-… · running 3m 2s
│     ├─ ◌ Refresh tokens
│     │    ? · dispatched
│     └─ ◌ Rotate keys
│          ? · dispatched
└─ ● 인증 모듈 문서 정리
     writing · opus-5-… · running 5m 55s
"""

EXPORT_TREE_CUT_8 = """\
DAG · export feature · 6 nodes · 7 edges · 1 running · 1 failed · 1 dispatched · 1 waiting · 1 done
◆ main · opus-5-5:high
├─ layer 2
│  ├─ ● Export button    unspecified-high  glm-5.3:low        running 21s
│  ├─ ◌ Export docs      writing                              handed out
│  └─ ✗ Export CLI flag  quick             glm-5.3-flash:low  failed 8s
└─ +2 more nodes (1 done)
"""

EXPORT_TREE_CUT_6 = """\
DAG · export feature · 6 nodes · 7 edges · 1 running · 1 failed · 1 dispatched · 1 waiting · 1 done
◆ main · opus-5-5:high
├─ layer 2
│  ├─ ● Export button    unspecified-high  glm-5.3:low        running 21s
│  └─ ✗ Export CLI flag  quick             glm-5.3-flash:low  failed 8s
└─ +3 more nodes (1 done)
"""

LAYER_CUT_7 = """\
DAG · delegation · 7 nodes · 6 edges · 1 running · 5 waiting
◆ main · opus-5-5:high
├─ layer 1
│  └─ ● Schema           deep   opus-5-5:high      running 30s
└─ layer 2
   ├─ · Migrate table 4  quick  glm-5.3-flash:low  waiting
   └─ +4 more nodes
"""

BUDGET = dict(width=40, max_chars=1782, max_lines=42)
BUTTON, DOCS, CLI = "d:r1:button", "d:r1:docs", "d:r1:cli"


def text(graph_payload: dict, **options) -> str:
    return render_delegation_view(graph_payload, **options).text()


def box_lines(view) -> dict[str, list[str]]:
    """Each box's lines, by node id: what sits between its side borders, read off the drawing."""
    lines = view.text_lines()
    by_top: dict[int, list[str]] = {}
    for node_id, top, _bottom in view.boxes:
        by_top.setdefault(top, []).append(node_id)
    found: dict[str, list[str]] = {}
    for node_id, top, bottom in view.boxes:
        column = by_top[top].index(node_id)
        found[node_id] = [re.findall(r"[│|] (.*?) *[│|]", lines[at])[column] for at in range(top + 1, bottom)]
    return found


def box_spans(view, node_id: str) -> list[list[tuple[str, str]]]:
    """One box's lines as (text, role) pieces, cut out of the rows it shares: its top border, then each line inside
    its side borders (the borders left out), then its bottom border."""
    top, bottom = next((first, last) for named, first, last in view.boxes if named == node_id)
    column = [named for named, first, _last in view.boxes if first == top].index(node_id)
    row = view.text_lines()[top]
    left = [at for at, char in enumerate(row) if char == "┌"][column]
    right = [at for at, char in enumerate(row) if char == "┐"][column]
    found = []
    for at in range(top, bottom + 1):
        pieces, place = [], 0
        for segment in view.lines[at]:
            for char in segment.text:
                inside = left < place < right if top < at < bottom else left <= place <= right
                if inside:
                    if pieces and pieces[-1][1] == segment.role:
                        pieces[-1] = (pieces[-1][0] + char, segment.role)
                    else:
                        pieces.append((char, segment.role))
                place += 1
        found.append([(text, role) for text, role in pieces if text.strip()])
    return found


def section(view, heading: str) -> list[str]:
    """The lines under ``heading`` up to the next blank line, their indent kept."""
    lines = view.text_lines()
    if heading not in lines:
        return []
    start = lines.index(heading) + 1
    end = next((at for at in range(start, len(lines)) if not lines[at]), len(lines))
    return lines[start:end]


def cards(view) -> list[str]:
    """The titles of the Node details cards, in order."""
    return [line[4:] for line in section(view, "Node details") if line.startswith("  ") and not line.startswith("    ")]


def branch_problems(drawing: str) -> list[str]:
    """Where a tree's branches lie: a `├─` with no sibling after it, or a `└─` that one follows."""
    rows = [row for row in drawing.splitlines() if row[:1] in "├└│ "]
    found = []
    for at, row in enumerate(rows):
        column = next((index for index, char in enumerate(row) if char in "├└"), None)
        if column is None:
            continue
        sibling = False
        for later in rows[at + 1:]:
            cell = later[column] if column < len(later) else " "
            if cell in "├└":
                sibling = True
                break
            if cell != "│" and (cell != " " or later[:column].strip(" │")):
                break
        if row[column] == "├" and not sibling:
            found.append(f"a ├─ with nothing after it: {row!r}")
        if row[column] == "└" and sibling:
            found.append(f"a └─ with a sibling after it: {row!r}")
    return found


def lane_edges(payload: dict) -> set[tuple[str, str, str]]:
    titles = {node["id"]: node["title"] for node in payload["nodes"] if node["kind"] != "root"}
    return {(titles[edge["from"]], titles[edge["to"]], edge["kind"]) for edge in payload["edges"]
            if edge["from"] in titles and edge["to"] in titles}


class SnapshotTests(unittest.TestCase):
    maxDiff = None

    def test_the_export_run_at_100_columns(self) -> None:
        view = render_delegation_view(dag_export_run(), width=100)
        self.assertEqual(view.layout, "box")
        self.assertEqual(view.text() + "\n", EXPORT100)

    def test_ascii_charset(self) -> None:
        drawn = text(dag_export_run(), width=80, charset="ascii")
        self.assertEqual(drawn + "\n", EXPORT80ASCII)
        self.assertTrue(drawn.isascii(), "the ASCII charset draws nothing else when the titles are ASCII")

    def test_a_narrow_terminal_gets_the_tree(self) -> None:
        self.assertEqual(render_delegation_view(nested(), width=BOX_MIN_WIDTH - 1).layout, "tree")
        view = render_delegation_view(nested(), width=34, layout="tree")
        self.assertEqual(view.text() + "\n", NESTED34TREE)

    def test_a_budget_gives_up_node_details_then_dependencies_then_the_boxes(self) -> None:
        whole = render_delegation_view(owner_mock(), width=40)
        self.assertEqual((whole.layout, bool(section(whole, "Node details"))), ("box", True))
        view = render_delegation_view(owner_mock(), **BUDGET)
        self.assertEqual(view.layout, "box")
        self.assertEqual(section(view, "Node details"), [], "Node details go first")
        self.assertEqual(len(section(view, "Dependencies")), 3, "the edges stay while they fit")
        tighter = render_delegation_view(owner_mock(), width=40, max_lines=len(view.lines) - 1)
        self.assertEqual((tighter.layout, section(tighter, "Dependencies")), ("box", []))
        self.assertIn("← Refresh HUD docs, Poli…", tighter.text(), "each box still names its parents")
        view = render_delegation_view(nested(), **BUDGET)
        self.assertEqual((view.layout, view.text() + "\n"), ("tree", NESTED40BUDGET))

    def test_a_character_cap_discloses_what_it_left_out(self) -> None:
        view = render_delegation_view(parallel(60), width=40, max_chars=1782, max_lines=42)
        lines = view.text_lines()
        self.assertEqual(view.layout, "tree")
        self.assertLessEqual(len(view.text()), 1782)
        self.assertLessEqual(len(lines), 42)
        self.assertEqual(lines[-1], f"└─ +{view.hidden_nodes} more nodes")
        self.assertGreater(view.hidden_nodes, 0)
        shown = sum(1 for line in lines if " Lane " in line)
        self.assertEqual(shown + view.hidden_nodes, 60, "every lane is either drawn or counted")


class BoxTests(unittest.TestCase):
    def test_each_box_holds_its_title_facts_status_and_parents(self) -> None:
        boxes = box_lines(render_delegation_view(dag_export_run(), width=120))
        self.assertEqual(boxes["d:r1:api"], ["Export API", "quick · glm-5.3-flash:low", "✓ Accepted 12s", "Start node"])
        self.assertEqual(boxes[BUTTON], ["Export button", "unspecified-high · glm-5.3:low", "● Running 21s",
                                         "← Export API"])
        self.assertEqual(boxes[DOCS], ["Export docs", "writing", "◌ Handed out", "← Export API"],
                         "a model no child has reported yet is left out, never `?`")
        self.assertEqual(boxes[CLI], ["Export CLI flag", "quick · glm-5.3-flash:low", "✗ Failed 8s", "← Export API"])
        self.assertEqual(boxes["d:r1:verify"],
                         ["Verify export", "unspecified-high", "○ Waits on 3 lanes", "← Export button, Export docs,…"])
        self.assertNotIn("main", " ".join(" ".join(lines) for lines in boxes.values()), "the root is not a box")

    def test_a_node_added_mid_run_says_where_it_came_from(self) -> None:
        boxes = box_lines(render_delegation_view(dag_export_repair_run(), width=100))
        self.assertEqual(boxes["d:r1:csv"][0], "+ CSV export", "an extend")
        self.assertEqual(boxes["d:r1:csv-fix"][0], "↻ CSV export, with the head…", "a repair")
        self.assertEqual(boxes["d:r1:verify"][0], "Verify export")
        ascii_boxes = box_lines(render_delegation_view(dag_export_repair_run(), width=100, charset="ascii"))
        self.assertEqual((ascii_boxes["d:r1:csv"][0], ascii_boxes["d:r1:csv-fix"][0][:4]), ("+ CSV export", "~ CS"))

    def test_the_header_names_the_run_its_size_and_its_state(self) -> None:
        lines = text(dag_export_run(), width=100).splitlines()[:3]
        self.assertEqual(lines, ["OMH / DAG · 5 tasks", "export feature · 1/1 · main opus-5-5:high",
                                 "Running · Done 1/5 · 1 failed"])
        self.assertEqual(text(dag_export_repair_run(), width=100).splitlines()[2], "Running · Done 5/7 · 1 failed")
        settled = dag_export_run()
        for node in settled["nodes"][1:]:
            node["state"] = "done"
        self.assertEqual(text(settled, width=100).splitlines()[2], "Completed · Done 5/5")
        settled["nodes"][2]["state"] = "failed"
        self.assertEqual(text(settled, width=100).splitlines()[2], "Failed · Done 4/5 · 1 failed")
        cut = {**dag_export_run(), "hidden_nodes": 3}
        self.assertEqual(text(cut, width=100).splitlines()[:3:2],
                         ["OMH / DAG · 8 tasks", "Running · Done 1/5 · 1 failed · +3 more nodes"])
        fanout = graph([root(model="", effort=""), lane("u", "Core", "codex", "?", "", "running")],
                       [("root", "u", "dispatch")], name="fanout fanout-0123456789ab", source="fanout")
        self.assertEqual(text(fanout, width=80).splitlines()[1], "fanout fanout-0123456789ab · 1/1",
                         "a root that names no model adds nothing")

    def test_frontiers_sit_side_by_side_and_say_so(self) -> None:
        view = render_delegation_view(dag_export_run(), width=120)
        rows = {node_id: (top, bottom) for node_id, top, bottom in view.boxes}
        self.assertEqual(len({rows[node_id] for node_id in (BUTTON, DOCS, CLI)}), 1, "one row")
        self.assertLess(rows["d:r1:api"][1], rows[BUTTON][0])
        self.assertLess(rows[BUTTON][1], rows["d:r1:verify"][0])
        labels = [at for at, line in enumerate(view.text_lines()) if line.strip() == "· Same frontier"]
        self.assertEqual(labels, [rows[BUTTON][1] + 1], "one label, under the frontier of three, none under a lone box")

    def test_a_frontier_too_wide_wraps_and_keeps_its_label(self) -> None:
        view = render_delegation_view(dag_export_run(), width=80)
        rows = {node_id: (top, bottom) for node_id, top, bottom in view.boxes}
        self.assertEqual(rows[BUTTON], rows[DOCS])
        self.assertEqual(rows[CLI][0], rows[BUTTON][1] + 1, "the third box wraps to the next row")
        lines = view.text_lines()
        self.assertEqual(lines[rows[CLI][1] + 1], "· Same frontier", "the label stays under the frontier")
        self.assertEqual(lines[rows[CLI][0]].index("┌"), lines[rows[BUTTON][0]].index("┌"),
                         "a wrapped row starts where the first one does")

    def test_one_connector_per_frontier_gap(self) -> None:
        for case, frontiers in ((dag_export_run(), 3), (dag_export_repair_run(), 5), (nested(), 3), (parallel(6), 1)):
            for width in (40, 80, 120):
                view = render_delegation_view(case, width=width)
                with self.subTest(name=case["name"], nodes=len(case["nodes"]), width=width):
                    self.assertEqual(view.layout, "box")
                    lines = view.text_lines()
                    arrows = [at for at, line in enumerate(lines) if "▼" in line]
                    self.assertEqual(len(arrows), frontiers - 1)
                    for at in arrows:
                        self.assertEqual(lines[at].strip(), "▼")
                        self.assertEqual(lines[at - 1].strip(), "│")
                        self.assertEqual(lines[at - 1].index("│"), lines[at].index("▼"), "one straight connector")
                        self.assertTrue(lines[at + 1].lstrip().startswith("┌"), "it ends at the next frontier")

    def test_dependencies_are_the_graph_edges_exactly(self) -> None:
        for case in (dag_export_run(), dag_export_repair_run(), nested(), owner_mock()):
            view = render_delegation_view(case, width=120)
            drawn = set()
            for line in section(view, "Dependencies"):
                found = re.fullmatch(r"  (.+?) → (.+?)(?: \((\w+)\))?", line)
                self.assertIsNotNone(found, line)
                drawn.add((found[1], found[2], found[3] or "depends_on"))
            with self.subTest(name=case["name"], nodes=len(case["nodes"])):
                self.assertEqual(drawn, lane_edges(case))
                self.assertEqual(len(section(view, "Dependencies")), len(lane_edges(case)), "each edge once")
        later = text(dag_export_repair_run(), width=120).splitlines()
        self.assertIn("  Verify export → CSV export (extend)", later)
        self.assertIn("  CSV export → CSV export, with the header row (repair)", later)
        self.assertNotIn("Dependencies", text(parallel(3), width=120), "a fan-out from main has none to list")

    def test_node_details_only_for_running_or_highlighted_nodes(self) -> None:
        self.assertEqual(cards(render_delegation_view(dag_export_run(), width=100)), ["Export button"])
        self.assertEqual(cards(render_delegation_view(dag_export_run(), width=100, focus=Focus(node=CLI))),
                         ["Export button", "Export CLI flag"])
        self.assertEqual(cards(render_delegation_view(dag_export_run(), width=100, focus=Focus(node=BUTTON))),
                         ["Export button"], "a running node highlighted has one card")
        hidden = render_delegation_view(dag_export_run(), width=100, focus=Focus(node=CLI, details=False))
        self.assertNotIn("Node details", hidden.text_lines())
        stuck = dag_export_run()
        stuck["nodes"][2].update(state="stuck", status="stuck: same error", elapsed_seconds=400)
        self.assertEqual(cards(render_delegation_view(stuck, width=100)), ["Export button"])
        self.assertIn("    Stuck 6m 40s: same error · layer 2 · session 7c41e9a2", text(stuck, width=100).splitlines())
        settled = dag_export_repair_run()
        settled["nodes"][-1]["state"] = "done"
        self.assertNotIn("Node details", text(settled, width=100), "nothing runs: no card")

    def test_a_card_holds_the_whole_title(self) -> None:
        long = dag_export_run()
        long["nodes"][2]["title"] = "Export button with a confirmation dialog and a keyboard shortcut"
        view = render_delegation_view(long, width=60)
        self.assertEqual(box_lines(view)[BUTTON][0], "Export button with a confirma…")
        self.assertEqual(section(view, "Node details")[:2],
                         ["  ● Export button with a confirmation dialog and a keyboard", "    shortcut"])

    def test_the_width_falls_back_to_wrapping_then_cut_labels_then_the_tree(self) -> None:
        def per_row(view) -> int:
            tops = [top for node_id, top, _bottom in view.boxes if node_id in (BUTTON, DOCS, CLI)]
            return max(tops.count(top) for top in tops)

        # A frontier goes on one row while its boxes can narrow to it (82 columns for three); past that it wraps.
        for width, expected, facts in ((140, 3, "unspecified-high · glm-5.3:low"), (106, 3, "unspecified-high · glm-5.3:low"),
                                       (100, 3, "unspecified-high · glm-5.3:…"), (82, 3, "unspecified-high · gl…"),
                                       (81, 2, "unspecified-high · glm-5.3:low"), (60, 1, "unspecified-high · glm-5.3:low"),
                                       (34, 1, "unspecified-high · glm-5.3:low")):
            view = render_delegation_view(dag_export_run(), width=width)
            with self.subTest(width=width):
                self.assertEqual((view.layout, per_row(view)), ("box", expected))
                self.assertEqual(box_lines(view)[BUTTON][1], facts, "the category and model line is what is cut")
                self.assertEqual([lines[0] for lines in box_lines(view).values()],
                                 ["Export API", "Export button", "Export docs", "Export CLI flag", "Verify export"])
        for width, facts in ((30, "unspecified-high · glm-5.…"), (BOX_MIN_WIDTH, "unspecified-high · gl…")):
            view = render_delegation_view(dag_export_run(), width=width)
            with self.subTest(width=width):
                self.assertEqual(view.layout, "box")
                self.assertEqual(box_lines(view)[BUTTON][1], facts)
                self.assertTrue(all(cells(line) <= width for line in view.text_lines()))
        self.assertEqual(render_delegation_view(dag_export_run(), width=BOX_MIN_WIDTH - 1).layout, "tree")

    def test_dag_draws_fold_markers_and_the_highlight(self) -> None:
        static = render_delegation_view(dag_export_run(), width=100)
        self.assertNotIn("[-]", static.text(), "no key acts on the transcript's drawing")
        view = render_delegation_view(dag_export_run(), width=100, focus=Focus(node=CLI, folded=frozenset({"d:r1:api"})))
        boxes = box_lines(view)
        self.assertEqual(boxes["d:r1:api"], ["[+] ✓ Export API · Accepted"],
                         "a folded box is one line, and keeps its glyph and its status word")
        self.assertEqual(boxes[CLI][0], "[-] > Export CLI flag")
        self.assertEqual(boxes[BUTTON][0], "[-] Export button")
        borders = {node_id: {role for _text, role in box_spans(view, node_id)[0]} for node_id, _top, _bottom in view.boxes}
        self.assertEqual(borders, {"d:r1:api": {"node:done"}, BUTTON: {"node:running"}, DOCS: {"node:dispatched"},
                                   CLI: {"node:selected"}, "d:r1:verify": {"node:waiting"}})
        self.assertEqual([node_id for node_id, _top, _bottom in view.boxes],
                         ["d:r1:api", BUTTON, DOCS, CLI, "d:r1:verify"], "reading order")


class TreeTests(unittest.TestCase):
    maxDiff = None

    def test_a_cut_tree_closes_its_branches_and_counts_the_cut_where_it_was_made(self) -> None:
        for budget, snapshot in ((8, EXPORT_TREE_CUT_8), (6, EXPORT_TREE_CUT_6)):
            view = render_delegation_view(dag_export_run(), width=120, layout="tree", max_lines=budget)
            with self.subTest(budget=budget):
                self.assertEqual(view.text() + "\n", snapshot, "whole layers were cut: the count is at the root")
                self.assertEqual(branch_problems(view.text()), [])
        wide = graph(
            [root(), lane("a", "Schema", "deep", "claude-opus-5-5", "high", "running", elapsed=30, group="layer 1",
                          order=1),
             *(lane(f"b{index}", f"Migrate table {index}", "quick", "glm-5.3-flash", "low", "waiting",
                    group="layer 2", order=2 + index) for index in range(5))],
            [("root", "a", "dispatch"), *(("a", f"b{index}", "depends_on") for index in range(5))],
        )
        view = render_delegation_view(wide, width=100, layout="tree", max_lines=7)
        self.assertEqual(view.text() + "\n", LAYER_CUT_7, "only the last layer was cut: the count is under it")
        segments = [segment for line in view.lines for segment in line]
        self.assertEqual([segment.role for segment in segments if "more" in segment.text], ["more"])

    def test_every_cut_tree_is_well_formed(self) -> None:
        generator = random.Random(2026_10_21)
        for trial in range(80):
            nodes, edges = [root()], []
            for index in range(generator.randint(2, 12)):
                parent = generator.choice(["root"] + [node["id"] for node in nodes[1:]])
                nodes.append(lane(f"n{index}", f"Task {index}", "quick", "glm-5.3-flash", "",
                                  generator.choice(["running", "done", "failed", "waiting"]), order=index,
                                  group=generator.choice(["", "wave 1 · 14:02", "wave 2 · 14:09", "layer 3"])))
                edges.append((parent, f"n{index}", "dispatch"))
            case = graph(nodes, edges)
            for width in (34, 100):
                for budget in (4, 6, 9):
                    view = render_delegation_view(case, width=width, layout="tree", max_lines=budget)
                    with self.subTest(trial=trial, width=width, budget=budget):
                        self.assertEqual(branch_problems(view.text()), [], view.text())

    def test_a_field_not_recorded_yet_is_blank_in_the_tree(self) -> None:
        drawn = text(dag_export_run(), width=120, layout="tree").splitlines()
        docs = next(row for row in drawn if "Export docs" in row)
        self.assertEqual(docs.split()[-3:], ["writing", "handed", "out"], "no `?` where the model is not known yet")
        self.assertNotIn("?", text(dag_export_run(), width=120, layout="tree"))
        narrow = text(dag_export_run(), width=60, layout="tree")
        self.assertIn("writing · handed out", narrow)
        self.assertIn("unspecified-high · waits on 3 lanes", narrow)
        bare = graph([root(model="", effort=""), {"id": "x", "kind": "lane", "state": "dispatched"}],
                     [("root", "x", "dispatch")])
        self.assertIn("? · dispatched", text(bare, width=60, layout="tree"), "one `?` where neither is known")
        self.assertEqual(text(bare, width=100, layout="tree").count("?"), 2, "the title's and the category column's")

    def test_the_category_column_holds_the_longest_category_and_the_model_gives_way(self) -> None:
        for width in (120, 74, 72):
            rows = text(dag_export_run(), width=width, layout="tree").splitlines()
            button = next(row for row in rows if "Export button" in row)
            with self.subTest(width=width):
                self.assertIn(" unspecified-high ", button, "never cut to `unspecified…`")
                self.assertTrue(all(cells(row) <= width for row in rows))
        tight = next(row for row in text(dag_export_run(), width=72, layout="tree").splitlines() if "Export API" in row)
        self.assertIn("quick             glm-5.3-flas…  accepted 12s", tight, "the model is cut, not the category")

    def test_the_widened_category_never_takes_a_statuss_room(self) -> None:
        """The cells a long category takes come from room no status needs, then the model, then the title: a
        status main drew whole stays whole, and one main cut is cut no further."""
        late = dag_export_repair_run()
        late["nodes"][2]["elapsed_seconds"] = 3_725
        button = next(row for row in text(late, width=90, layout="tree").splitlines() if "Export button" in row)
        self.assertTrue(button.endswith("unspecified-high  glm-5.3:low    accepted 1h 02m"), button)
        verify = next(row for row in text(dag_export_run(), width=72, layout="tree").splitlines() if "Verify export" in row)
        self.assertTrue(verify.endswith("waits on 3 la…"), f"cut where main cut it, no further: {verify!r}")
        self.assertTrue(next(row for row in text(dag_export_run(), width=74, layout="tree").splitlines()
                             if "Verify export" in row).endswith("waits on 3 lanes"))

    def test_the_model_gives_way_down_to_its_floor_and_then_the_title(self) -> None:
        wide = graph([root(), lane("v", "Render the landing page hero", "visual-engineering", "glm-5.3-flash", "",
                                   "running", elapsed=5)], [("root", "v", "dispatch")])
        row = next(line for line in text(wide, width=72, layout="tree").splitlines() if "Render" in line)
        self.assertEqual(row, "└─ ● Render the landing pag…  visual-engineering  glm-5.3…  running 5s",
                         "the model keeps TREE_MODEL_MIN (8) cells; the title gives the rest")

    def test_a_cut_that_hides_only_nested_children_of_the_last_group_is_counted_under_it(self) -> None:
        nested_cut = graph(
            [root(),
             lane("x", "Schema", "deep", "claude-opus-5-5", "high", "running", elapsed=30, group="layer 1", order=1),
             lane("y", "Migrate", "quick", "glm-5.3-flash", "low", "running", elapsed=20, group="layer 2", order=2),
             lane("y1", "Table one", "quick", "glm-5.3-flash", "low", "waiting", order=3),
             lane("y2", "Table two", "quick", "glm-5.3-flash", "low", "waiting", order=4)],
            [("root", "x", "dispatch"), ("root", "y", "dispatch"), ("y", "y1", "dispatch"), ("y", "y2", "dispatch")],
        )
        lines = text(nested_cut, width=100, layout="tree", max_lines=7).splitlines()
        self.assertEqual(lines[-3:], ["└─ layer 2", "   ├─ ● Migrate   quick  glm-5.3-flash:low  running 20s",
                                      "   └─ +2 more nodes"], "the hidden children sit under layer 2")

    def test_a_known_model_with_no_category_record_shows_no_question_mark(self) -> None:
        unrouted = graph([root(), lane("a", "Port it", "?", "glm-5.3-flash", "low", "running", elapsed=5)],
                         [("root", "a", "dispatch")])
        self.assertEqual(text(unrouted, width=100, layout="tree").splitlines()[-1],
                         "└─ ● Port it     glm-5.3-flash:low  running 5s", "the category column is blank")
        self.assertIn("     glm-5.3-flash:low · running 5s", text(unrouted, width=50, layout="tree"))
        self.assertEqual(box_lines(render_delegation_view(unrouted, width=80))["a"][1], "glm-5.3-flash:low")


class ReviewTests(unittest.TestCase):
    """What the #2191 review measured: cut lines, `?`, the hud probe, folded words, the run word, and survivors."""

    def test_a_narrow_dependency_line_keeps_its_kind_and_cuts_its_titles(self) -> None:
        for charset, arrow, ellipsis in (("unicode", "→", "…"), ("ascii", "->", "...")):
            for width in (50, 40, 30):
                view = render_delegation_view(dag_export_repair_run(), width=width, charset=charset)
                lines = section(view, "Dependencies")
                with self.subTest(charset=charset, width=width):
                    self.assertEqual(len(lines), 8)
                    self.assertTrue(lines[-2].endswith(" (extend)") and lines[-1].endswith(" (repair)"), lines)
                    for line in lines:
                        source, _, target = line.strip().partition(f" {arrow} ")
                        self.assertTrue(source and target, f"both ends drawn: {line!r}")
                        self.assertLessEqual(cells(line), width)
                    self.assertTrue(lines[-1].startswith("  CSV ") and ellipsis in lines[-1])
        self.assertEqual(section(render_delegation_view(dag_export_repair_run(), width=30), "Dependencies")[-2:],
                         ["  Verify expo… → CSV… (extend)", "  CSV export → CSV e… (repair)"])

    def test_every_cut_line_ends_in_the_ellipsis(self) -> None:
        generator = random.Random(2191)
        words = ["Export", "보고서 작성 및 검토 단계", "x" * 40, "CSV export, with the header row", "A"]
        for trial in range(80):
            nodes = [root()] + [lane(f"n{index}", " ".join(generator.choice(words) for _ in range(generator.randint(1, 3))),
                                     "quick", "glm-5.3-flash", "", generator.choice(["running", "done", "waiting"]),
                                     order=index) for index in range(6)]
            edges = [("root", "n0", "dispatch")] + [
                (f"n{generator.randint(0, index - 1)}", f"n{index}", generator.choice(["depends_on", "repair", "extend"]))
                for index in range(1, 6)
            ]
            for charset, arrow, ellipsis in (("unicode", "→", "…"), ("ascii", "->", "...")):
                for width in (26, 30, 34, 40):
                    view = render_delegation_view(graph(nodes, edges), width=width, charset=charset)
                    for line in section(view, "Dependencies"):
                        with self.subTest(trial=trial, width=width, charset=charset, line=line):
                            self.assertFalse(line.rstrip().endswith((arrow, "·", "-")), "never an arrow with no target")
                            whole = {f"  {node['title']} {arrow} {other['title']}" for node in nodes for other in nodes}
                            if not any(line.startswith(prefix) for prefix in whole):
                                self.assertIn(ellipsis, line, "a cut line says so")

    def test_a_line_filled_to_the_edge_still_says_it_was_cut(self) -> None:
        """A category that fills the box exactly leaves no room for ` · model`: the category gives way to the
        ellipsis rather than the model vanishing with nothing to say so."""
        for charset, category, expected in (("unicode", "a" * 22, "a" * 21 + "…"), ("ascii", "a" * 21, "a" * 19 + "...")):
            full = graph([root(), lane("x", "Short", category, "glm-5.3-flash", "low", "running", elapsed=5)],
                         [("root", "x", "dispatch")])
            with self.subTest(charset=charset):
                self.assertEqual(box_lines(render_delegation_view(full, width=26, charset=charset))["x"][1], expected)

    def test_a_box_draws_no_question_mark(self) -> None:
        unowned = graph([root(model="", effort=""),
                         lane("u", "Unit one", "?", "?", "", "running", elapsed=30),
                         lane("w", "Unit two", "?", "?", "", "unknown", status="no news")],
                        [("root", "u", "dispatch"), ("root", "w", "dispatch")], source="fanout")
        view = render_delegation_view(unowned, width=80)
        boxes = box_lines(view)
        self.assertEqual(boxes["u"], ["Unit one", "", "● Running 30s", "Start node"])
        self.assertEqual(boxes["w"], ["Unit two", "", "◇ No news", "Start node"], "an unknown state's glyph is no `?`")
        self.assertNotIn("?", view.text())
        self.assertEqual(section(view, "Node details"), ["  ● Unit one", "    Running 30s"], "no facts line, no `?`")
        self.assertIn("<> No news", text(unowned, width=80, charset="ascii"))

    def test_the_hud_probe_names_every_character_a_box_draws(self) -> None:
        from omh.commands.hud import DELEGATION_VIEW_PROBE

        drawn = set()
        counted = with_tokens(dag_export_run(), {"d:r1:button": 1234})
        for case in (dag_export_run(), dag_export_repair_run(), nested(), owner_mock(), counted):
            for focus in (None, Focus(node="d:r1:api", folded=frozenset({"d:r1:api"}))):
                drawn |= set(render_delegation_view(case, width=120, focus=focus).text())
        unknown = graph([root(), lane("w", "Unit", "quick", "m", "", "unknown")], [("root", "w", "dispatch")])
        drawn |= set(render_delegation_view(unknown, width=80).text())
        lines_only = {char for char in drawn if not char.isascii() and not char.isalnum() and char not in "인증모듈문서정리"}
        self.assertEqual(lines_only - set(DELEGATION_VIEW_PROBE), set())
        self.assertIn("↻", lines_only)
        self.assertIn("↓", lines_only)

    def test_a_folded_box_keeps_its_status_word_in_both_charsets(self) -> None:
        focus = Focus(node="d:r1:cli", folded=frozenset({"d:r1:api", "d:r1:cli"}))
        unicode_boxes = box_lines(render_delegation_view(dag_export_run(), width=140, focus=focus))
        self.assertEqual(unicode_boxes["d:r1:api"], ["[+] ✓ Export API · Accepted"])
        self.assertEqual(unicode_boxes["d:r1:cli"], ["[+] > ✗ Export CLI f… · Failed"], "past 30 cells the title gives way")
        ascii_boxes = box_lines(render_delegation_view(dag_export_run(), width=140, focus=focus, charset="ascii"))
        self.assertEqual(ascii_boxes["d:r1:api"], ["[+] + Export API - Accepted"])
        self.assertEqual(ascii_boxes["d:r1:cli"], ["[+] > x Export CLI... - Failed"])
        # A folded, selected box whose one line is the widest of the drawing measures all of it: `[+] > ✓ ` is 8
        # cells, then the title, then ` · Accepted`, so nothing is cut while the width allows.
        alone = graph([root(), lane("a", "Export API", "quick", "m", "", "done", status="accepted", elapsed=12)],
                      [("root", "a", "dispatch")])
        line = box_lines(render_delegation_view(alone, width=80, focus=Focus(node="a", folded=frozenset({"a"}))))["a"]
        self.assertEqual(line, ["[+] > ✓ Export API · Accepted"])

    def test_a_run_that_only_lost_its_news_does_not_read_as_an_error(self) -> None:
        lost = dag_export_run()
        for node in lost["nodes"][1:]:
            node.update(state="unknown", status="no news")
        view = render_delegation_view(lost, width=100)
        self.assertEqual(view.text_lines()[2], "Ended · Done 0/5")
        self.assertEqual(view.lines[2][0].role, "node-word:unknown")
        cancelled = dag_export_run()
        cancelled["nodes"][2].update(state="interrupted", status="cancelled")
        for node in cancelled["nodes"][1:]:
            if node["state"] != "interrupted":
                node["state"] = "done"
        self.assertEqual(render_delegation_view(cancelled, width=100).lines[2][0].role, "node-word:interrupted")

    def test_a_connector_into_a_frontier_with_nothing_running_is_dim(self) -> None:
        handed = dag_export_run()
        handed["nodes"][2].update(state="dispatched", status="handed out")  # the frontier: handed out, failed
        view = render_delegation_view(handed, width=100)
        roles = [segment.role for line in view.lines for segment in line
                 if "".join(piece.text for piece in line).strip() in ("│", "▼") and segment.text.strip()]
        self.assertEqual(roles, ["connector"] * 4, "handed out is not running: the connector stays dim")

    def test_a_duplicated_edge_is_listed_once(self) -> None:
        twice = dag_export_run()
        twice["edges"].append({"from": "d:r1:api", "to": "d:r1:docs", "kind": "depends_on"})
        lines = section(render_delegation_view(twice, width=100), "Dependencies")
        self.assertEqual(lines.count("  Export API → Export docs"), 1)
        self.assertEqual(len(lines), 6)

    def test_a_repair_mark_wins_over_an_extend_mark(self) -> None:
        both = dag_export_repair_run()
        both["edges"].append({"from": "d:r1:verify", "to": "d:r1:csv-fix", "kind": "extend"})
        self.assertEqual(box_lines(render_delegation_view(both, width=100))["d:r1:csv-fix"][0][:2], "↻ ")


TOKEN_PART = re.compile(r"( · ↓| - out )[0-9.]+[kM]?( tokens)?")


def card_start(view) -> int:
    """Where the Node details section starts in ``view``, or its end when it has none."""
    lines = view.text_lines()
    return lines.index("Node details") if "Node details" in lines else len(lines)
EXPORT_COUNTS = {"d:r1:button": 1234, "d:r1:api": 5600, "d:r1:cli": 800}


class OutputTokenTests(unittest.TestCase):
    """A lane's `output_tokens`: after its status in its box and its tree row where they have room, in its card."""

    def test_a_box_says_its_count_after_its_status(self) -> None:
        boxes = box_lines(render_delegation_view(with_tokens(dag_export_run(), EXPORT_COUNTS), width=100))
        self.assertEqual(
            [boxes[node_id][2] for node_id in ("d:r1:api", "d:r1:button", "d:r1:docs", "d:r1:cli")],
            ["✓ Accepted 12s · ↓5.6k", "● Running 21s · ↓1.2k", "◌ Handed out", "✗ Failed 8s · ↓800"],
        )
        ascii_drawn = text(with_tokens(dag_export_run(), EXPORT_COUNTS), width=80, charset="ascii")
        self.assertIn("| * Running 21s - out 1.2k       |", ascii_drawn)
        self.assertIn("| + Accepted 12s                 |", ascii_drawn, "no room at this width: no count")
        self.assertTrue(ascii_drawn.isascii())

    def test_a_card_says_the_count_in_tokens(self) -> None:
        view = render_delegation_view(with_tokens(dag_export_run(), EXPORT_COUNTS), width=100)
        self.assertEqual(
            section(view, "Node details"),
            ["  ● Export button", "    unspecified-high · glm-5.3:low",
             "    Running 21s · ↓1.2k tokens · layer 2 · session 7c41e9a2"],
        )

    def test_the_tree_status_column_takes_the_count_where_the_row_has_room(self) -> None:
        counted = with_tokens(nested(), {"o": 45_600, "k": 900, "o1": 3000, "o2": 120})
        wide = render_delegation_view(counted, width=60, layout="tree").text_lines()
        self.assertEqual(wide[4], "│    architect · opus-5-5:xhigh · running 6m 40s · ↓45.6k")
        self.assertEqual(wide[-1], "     writing · opus-5-5:medium · running 5m 55s · ↓900")
        self.assertNotIn("↓", render_delegation_view(counted, width=34, layout="tree").text(), "the model first")
        row = render_delegation_view(with_tokens(owner_mock(), {"a": 1234}), width=100, layout="tree").text_lines()[2]
        self.assertTrue(row.endswith("running 2m 10s · ↓1.2k"), row)

    def test_a_count_takes_only_room_nothing_else_needs_and_never_moves_the_layout(self) -> None:
        # The same drawing with the count parts taken out, at every width: no box is widened and no field cut for it.
        cases = [(dag_export_run, EXPORT_COUNTS), (nested, {"o": 45_600, "k": 900, "o1": 3000, "o2": 120}),
                 (owner_mock, {"a": 1234, "b": 62_000, "c": 7})]
        for make, counts in cases:
            for width in range(20, 161, 3):
                for layout in ("box", "tree"):
                    plain = render_delegation_view(make(), width=width, layout=layout)
                    for scale in (1, 1000, 100_000):
                        scaled = {key: value * scale for key, value in counts.items()}
                        counted = render_delegation_view(with_tokens(make(), scaled), width=width, layout=layout)
                        with self.subTest(case=make.__name__, width=width, layout=layout, scale=scale):
                            self.assertEqual((counted.layout, counted.boxes), (plain.layout, plain.boxes))
                            # A count blanked out where it stands: a box keeps its padding, a row ends there.
                            # Node details are left out: a card's group and session give way to a count.
                            blanked = TOKEN_PART.sub(lambda part: " " * cells(part.group()), counted.text())
                            self.assertEqual(
                                [line.rstrip() for line in blanked.splitlines()][:card_start(counted)],
                                [line.rstrip() for line in plain.text_lines()][:card_start(plain)],
                            )
                            cut = [line for line in section(counted, "Node details") if "↓" in line]
                            self.assertFalse([line for line in cut if not TOKEN_PART.search(line)], "whole or none")
                            self.assertTrue(all(cells(line) <= width for line in counted.text_lines()))

    def test_a_card_keeps_its_status_whole_and_drops_its_session_before_the_count(self) -> None:
        counted = with_tokens(dag_export_run(), EXPORT_COUNTS)
        cases = {
            70: "    Running 21s · ↓1.2k tokens · layer 2 · session 7c41e9a2",
            45: "    Running 21s · ↓1.2k tokens · layer 2 · s…",
            40: "    Running 21s · ↓1.2k tokens · layer…",
            33: "    Running 21s · layer 2 · sess…",  # no room for a count at its widest: none at all
        }
        for width, line in cases.items():
            with self.subTest(width=width):
                view = render_delegation_view(counted, width=width, layout="box")
                self.assertEqual(view.layout, "box")
                self.assertEqual(section(view, "Node details")[-1], line)

    def test_no_count_draws_nothing(self) -> None:
        plain = render_delegation_view(dag_export_run(), width=100).text()
        for count in (None, 0, -5, True, "1200", 1.5, 10**9):
            with self.subTest(count=count):
                counted = with_tokens(dag_export_run(), {"d:r1:button": count})
                self.assertEqual(render_delegation_view(counted, width=100).text(), plain)


class FieldTests(unittest.TestCase):
    def test_a_field_without_evidence_is_a_question_mark(self) -> None:
        bare = graph([root(model="", effort=""), {"id": "x", "kind": "lane", "state": "dispatched"}],
                     [("root", "x", "dispatch")])
        self.assertEqual(box_lines(render_delegation_view(bare, width=80))["x"],
                         ["?", "", "◌ Dispatched", "Start node"], "neither category nor model known: the line is blank")
        self.assertIn("?  ", text(bare, width=80, layout="tree"))

    def test_a_question_mark_is_marked_unknown_for_the_painter(self) -> None:
        bare = graph([root(model="", effort=""), {"id": "x", "kind": "lane", "state": "dispatched"}],
                     [("root", "x", "dispatch")])
        for layout, width in (("box", 80), ("tree", 80), ("tree", 40)):
            view = render_delegation_view(bare, width=width, layout=layout)
            segments = [segment for line in view.lines for segment in line]
            with self.subTest(layout=layout, width=width):
                self.assertEqual({segment.text.strip() for segment in segments if segment.role == "unknown"}, {"?"})
                self.assertFalse(any(segment.text.strip() == "?" and segment.role != "unknown" for segment in segments))
        known = render_delegation_view(owner_mock(), width=73)
        self.assertFalse(any(segment.role == "unknown" for line in known.lines for segment in line))

    def test_waits_on_names_how_many_recorded_dependencies_are_unmet(self) -> None:
        self.assertIn("○ Waits on 3 lanes", text(owner_mock(), width=73))
        single = owner_mock()
        single["nodes"][-1]["waits_on"] = 1
        self.assertIn("○ Waits on 1 lane", text(single, width=73))

    def test_a_stuck_unit_is_never_drawn_running(self) -> None:
        stuck = graph([root(), lane("s", "Fan out", "codex", "gpt-6.1-sol", "high", "stuck", elapsed=400,
                                     status="stuck: same error")], [("root", "s", "dispatch")])
        drawn = text(stuck, width=80)
        self.assertIn("! Stuck 6m 40s: same error", drawn)
        self.assertIn("Running · Done 0/1 · 1 stuck", drawn)
        self.assertNotIn("● ", drawn)

    def test_waves_of_one_root_are_one_frontier_and_the_tree_keeps_their_labels(self) -> None:
        waves = graph(
            [root(),
             lane("a", "Refresh docs", "writing", "claude-opus-5-5", "medium", "done", elapsed=250,
                  group="wave 1 · 14:02", order=1),
             lane("b", "Polish CLI output", "quick", "glm-5.3-flash", "low", "done", elapsed=62,
                  group="wave 1 · 14:02", order=2),
             lane("v", "Integration verify", "inherit", "claude-opus-5-5", "", "running", elapsed=30,
                  group="wave 2 · 14:09", order=3)],
            [("root", "a", "dispatch"), ("root", "b", "dispatch"), ("root", "v", "dispatch")],
        )
        view = render_delegation_view(waves, width=120)
        self.assertEqual(len({top for _node, top, _bottom in view.boxes}), 1, "a wave is order, not a dependency")
        self.assertIn("    Running 30s · wave 2 · 14:09", view.text_lines(), "the running node's card names its wave")
        self.assertIn("    Running 30s - wave 2 - 14:09", text(waves, width=120, charset="ascii").splitlines())
        tree = text(waves, width=80, layout="tree")
        self.assertLess(tree.index("wave 1 · 14:02"), tree.index("wave 2 · 14:09"))

    def test_text_is_folded_before_it_is_measured(self) -> None:
        self.assertEqual(clean_text("a​b‮c\x1b[31m d\n e"), "abc[31m d e")
        self.assertEqual(cells("인증 모듈"), 9)


class StructureTests(unittest.TestCase):
    def test_each_part_of_a_box_carries_its_nodes_state_for_the_painter(self) -> None:
        view = render_delegation_view(dag_export_run(), width=100, focus=Focus(node="d:r1:verify"))
        for node in dag_export_run()["nodes"][1:]:
            named = "selected" if node["id"] == "d:r1:verify" else node["state"]
            border, title, _facts, status, _parents, _bottom = box_spans(view, node["id"])
            with self.subTest(node=node["id"]):
                self.assertEqual({role for _text, role in border}, {f"node:{named}"})
                self.assertEqual({role for _text, role in title if role != "fold"},
                                 {f"node-title:{named}", f"node:{named}"} if named == "selected" else
                                 {f"node-title:{named}"})
                self.assertEqual([role for _text, role in status], [f"node:{node['state']}", f"node-word:{node['state']}"],
                                 "the glyph is a mark, the words a word; both keep the state's colour, selected or not")

    def test_a_connector_is_dim_and_blue_into_a_running_frontier(self) -> None:
        view = render_delegation_view(dag_export_run(), width=100)
        connectors = [
            [segment.role for segment in line if segment.text.strip()] for line in view.lines
            if "".join(segment.text for segment in line).strip() in ("│", "▼")
        ]
        self.assertEqual(connectors, [["connector:running"]] * 2 + [["connector"]] * 2,
                         "into the frontier where Export button runs, then into one where nothing does")
        self.assertEqual({segment.role for line in view.lines for segment in line if segment.text.strip() == "→"},
                         {"connector"})
        self.assertEqual(view.levels, 1, "boxes are coloured by state, not by depth")
        tree = render_delegation_view(nested(), width=34, layout="tree")
        self.assertEqual((tree.layout, tree.levels), ("tree", 3), "the tree's levels are its depths")
        for width in (34, 90):
            columns: dict[int, set[int]] = {}
            for line in render_delegation_view(nested(), width=width, layout="tree").lines:
                at = 0
                for segment in line:
                    for offset, char in enumerate(segment.text):
                        if segment.role == "edge" and char in "│├└─":
                            columns.setdefault(at + offset, set()).add(segment.level)
                    at += len(segment.text)
            with self.subTest(width=width):
                self.assertEqual(sorted(columns), [0, 1, 3, 4, 6, 7], "three depths of branches")
                self.assertEqual([columns[x] for x in (0, 3, 6)], [{0}, {1}, {2}], "a bar keeps its depth's level")

    def test_any_acyclic_shape_is_boxes_and_a_cycle_the_tree(self) -> None:
        skipping = owner_mock()
        skipping["edges"].append({"from": "a", "to": "c", "kind": "depends_on"})  # c drops a frontier; a → v skips one
        crossing = graph(
            [root(), *(lane(name, name, "quick", "m", "", "running") for name in "abcd")],
            [("root", "a", "dispatch"), ("root", "b", "dispatch"),
             ("a", "c", "depends_on"), ("a", "d", "depends_on"), ("b", "d", "depends_on")],
        )
        for case in (skipping, crossing):
            with self.subTest(edges=len(case["edges"])):
                view = render_delegation_view(case, width=120)
                self.assertEqual(view.layout, "box")
                self.assertEqual(len(section(view, "Dependencies")), len(lane_edges(case)))
        loop = graph([root(), lane("a", "A", "c", "m", "", "running"), lane("b", "B", "c", "m", "", "waiting")],
                     [("root", "a", "dispatch"), ("a", "b", "depends_on"), ("b", "a", "depends_on")])
        self.assertEqual(render_delegation_view(loop, width=120).layout, "tree")

    def test_no_line_is_ever_wider_than_its_width(self) -> None:
        generator = random.Random(2026_10_20)
        titles = ["Refresh docs", "인증 모듈 문서 정리와 테스트", "x" * 90, "🙂 emoji title", "a\tb\nc"]
        for trial in range(120):
            nodes = [root()]
            edges = []
            for index in range(generator.randint(1, 14)):
                node_id = f"n{index}"
                parent = generator.choice(["root"] + [node["id"] for node in nodes[1:]])
                nodes.append(lane(node_id, generator.choice(titles), generator.choice(["deep", "?", "x" * 30]),
                                  generator.choice(["claude-opus-5-5", "openrouter/x" * 6, "?"]),
                                  generator.choice(["", "high"]),
                                  generator.choice(["running", "done", "failed", "waiting", "stuck", "bogus"]),
                                  elapsed=generator.choice([None, 5, 4000]), order=index))
                edges.append((parent, node_id, generator.choice(["dispatch", "depends_on", "repair"])))
            case = graph(nodes, edges)
            width = generator.randint(20, 160)
            focus = generator.choice([None, Focus(node="n0", folded=frozenset({"n1"}))])
            for charset in ("unicode", "ascii"):
                view = render_delegation_view(case, width=width, charset=charset, focus=focus,
                                              max_lines=generator.choice([None, 3, 12, 40]))
                with self.subTest(trial=trial, width=width, charset=charset, layout=view.layout):
                    self.assertTrue(all(cells(line) <= max(8, width) for line in view.text_lines()))

    def test_a_chain_too_deep_for_the_width_numbers_its_depth_and_stays_inside(self) -> None:
        nodes = [root()] + [lane(f"n{index}", f"Level {index} task", "deep", "claude-opus-5-5", "high", "running",
                                 elapsed=60) for index in range(16)]
        chain = graph(nodes, [("root", "n0", "dispatch")] + [(f"n{index}", f"n{index + 1}", "dispatch")
                                                             for index in range(15)])
        for width in (34, 40, 80):
            view = render_delegation_view(chain, width=width, layout="tree")
            with self.subTest(width=width):
                self.assertTrue(all(cells(line) <= width for line in view.text_lines()))
                self.assertTrue(any("15 └─ ● Level 15" in line for line in view.text_lines()) or width >= 80)

    def test_inf_nan_and_huge_numbers_draw_without_a_duration(self) -> None:
        for elapsed in (float("inf"), float("nan"), 1e300, -5):
            case = graph([root(), lane("a", "A", "c", "m", "", "running", elapsed=elapsed)], [("root", "a", "dispatch")])
            with self.subTest(elapsed=elapsed):
                self.assertEqual(box_lines(render_delegation_view(case, width=60))["a"][2], "● Running")
                self.assertNotIn("Running 1", render_delegation_view(case, width=60).text())

    def test_nothing_malformed_raises(self) -> None:
        for payload in (None, {}, {"nodes": "x"}, {"nodes": [1, None, {"id": 3}]},
                        {"nodes": [{"id": "a", "kind": "root"}], "edges": [{"from": "a", "to": "zz"}, "e"]},
                        graph([root(), lane("a", "A", "c", "m", "", "running")],
                              [("a", "root", "dispatch"), ("root", "a", "dispatch")]),  # a cycle through the root
                        {"nodes": [{"id": "a", "title": 5, "state": ["x"], "elapsed_seconds": "1",
                                    "waits_on": True}]},
                        {"nodes": [{"id": "a", "kind": "lane"}], "edges": [{"from": "a", "to": "a", "kind": 5}]}):
            for focus in (None, Focus(node="a", folded=frozenset({"a"}))):
                with self.subTest(payload=payload, focus=focus):
                    self.assertIsInstance(render_delegation_view(payload, width=60, focus=focus).text(), str)


if __name__ == "__main__":
    unittest.main()
