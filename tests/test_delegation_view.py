"""The delegation view: what it draws for the owner's mock-up and its variants, and what it never draws.

The snapshots are the drawings themselves, reviewed as drawings: the box DAG
of the owner's mock-up at 73 columns, 1, 3 and 6 parallel lanes (6 wrap into a
second row a trunk feeds), a fan-in, nesting down to a sub-orchestrator, a
Korean title, the ASCII charset, the tree a narrow terminal gets, and what a
40-column messenger block holds. The property tests hold what no snapshot
can: no line wider than its width in terminal cells, no arrow without a
recorded edge, the tree for every shape the boxes cannot draw honestly, and
no exception for anything malformed.
"""

from __future__ import annotations

import random
import unittest

from _local_package import load_local_package

load_local_package()

from _delegation_graphs import graph, lane, nested, owner_mock, parallel, root  # noqa: E402
from omh.plugin_bundle.omh.delegation_view import (  # noqa: E402
    BOX_MIN_WIDTH,
    cells,
    clean_text,
    render_delegation_view,
)

MOCK73 = """\
DAG · ulw-work hud-graph · 5 nodes · 6 edges
2 running · 1 waiting · 1 done
                        ┌───────────────────────┐
                        │ ◆ main · orchestrator │
                        │ opus-5-5 · high       │
                        └───────────┬───────────┘
           ┌────────────────────────┼────────────────────────┐
           ▼                        ▼                        ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ ● Refresh HUD docs  │  │ ✓ Polish HUD CLI    │  │ ● Update HUD tests  │
│ writing             │  │ quick               │  │ deep                │
│ opus-5-5 · medium   │  │ glm-5.3-flash · low │  │ opus-5-5 · high     │
│ running 2m 10s      │  │ done 1m 2s          │  │ running 2m 8s       │
└──────────┬──────────┘  └──────────┬──────────┘  └──────────┬──────────┘
           └────────────────────────┼────────────────────────┘
                                    ▼
                         ┌─────────────────────┐
                         │ · Integration veri… │
                         │ inherit             │
                         │ opus-5-5            │
                         │ waits on 3 lanes    │
                         └─────────────────────┘
"""

ONE80 = """\
DAG · delegation · 2 nodes · 1 edge · 1 running
┌───────────────────────┐
│ ◆ main · orchestrator │
│ opus-5-5 · high       │
└───────────┬───────────┘
            │
            ▼
 ┌─────────────────────┐
 │ ● Lane 0 migrate    │
 │ quick               │
 │ glm-5.3-flash · low │
 │ running 1m 0s       │
 └─────────────────────┘
"""

THREE80 = """\
DAG · delegation · 4 nodes · 3 edges · 3 running
                        ┌───────────────────────┐
                        │ ◆ main · orchestrator │
                        │ opus-5-5 · high       │
                        └───────────┬───────────┘
           ┌────────────────────────┼────────────────────────┐
           ▼                        ▼                        ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ ● Lane 0 migrate    │  │ ● Lane 1 migrate    │  │ ● Lane 2 migrate    │
│ quick               │  │ quick               │  │ quick               │
│ glm-5.3-flash · low │  │ glm-5.3-flash · low │  │ glm-5.3-flash · low │
│ running 1m 0s       │  │ running 1m 1s       │  │ running 1m 2s       │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
"""

SIX80 = """\
DAG · delegation · 7 nodes · 6 edges · 6 running
                          ┌───────────────────────┐
                          │ ◆ main · orchestrator │
                          │ opus-5-5 · high       │
                          └───────────┬───────────┘
┌────────────┬────────────────────────┼────────────────────────┐
│            ▼                        ▼                        ▼
│ ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ │ ● Lane 0 migrate    │  │ ● Lane 1 migrate    │  │ ● Lane 2 migrate    │
│ │ quick               │  │ quick               │  │ quick               │
│ │ glm-5.3-flash · low │  │ glm-5.3-flash · low │  │ glm-5.3-flash · low │
│ │ running 1m 0s       │  │ running 1m 1s       │  │ running 1m 2s       │
│ └─────────────────────┘  └─────────────────────┘  └─────────────────────┘
└────────────┬────────────────────────┬────────────────────────┐
             ▼                        ▼                        ▼
  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
  │ ● Lane 3 migrate    │  │ ● Lane 4 migrate    │  │ ● Lane 5 migrate    │
  │ quick               │  │ quick               │  │ quick               │
  │ glm-5.3-flash · low │  │ glm-5.3-flash · low │  │ glm-5.3-flash · low │
  │ running 1m 3s       │  │ running 1m 4s       │  │ running 1m 5s       │
  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘
"""

NESTED120 = """\
DAG · delegation · 7 nodes · 6 edges · 3 running · 2 dispatched · 1 done
                                  ┌───────────────────────┐
                                  │ ◆ main · orchestrator │
                                  │ opus-5-5 · high       │
                                  └───────────┬───────────┘
                              ┌───────────────┴──────────────┐
                              ▼                              ▼
                ┌───────────────────────────┐  ┌───────────────────────────┐
                │ ● Port the auth module    │  │ ● 인증 모듈 문서 정리     │
                │ architect                 │  │ writing                   │
                │ opus-5-5 · xhigh          │  │ opus-5-5 · medium         │
                │ running 6m 40s            │  │ running 5m 55s            │
                └─────────────┬─────────────┘  └───────────────────────────┘
              ┌───────────────┴──────────────┐
              ▼                              ▼
┌───────────────────────────┐  ┌───────────────────────────┐
│ ✓ Port token store        │  │ ● Sub-orchestrate refresh │
│ inherit                   │  │ inherit                   │
│ opus-5-5 · xhigh          │  │ opus-5-5 · xhigh          │
│ done 2m 11s               │  │ running 3m 2s             │
└───────────────────────────┘  └─────────────┬─────────────┘
                             ┌───────────────┴──────────────┐
                             ▼                              ▼
               ┌───────────────────────────┐  ┌───────────────────────────┐
               │ ◌ Refresh tokens          │  │ ◌ Rotate keys             │
               │ ?                         │  │ ?                         │
               │ ?                         │  │ ?                         │
               │ dispatched                │  │ dispatched                │
               └───────────────────────────┘  └───────────────────────────┘
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
│     │    ? · ? · dispatched
│     └─ ◌ Rotate keys
│          ? · ? · dispatched
└─ ● 인증 모듈 문서 정리
     writing · opu… · running 5m…
"""

MOCK73ASCII = """\
DAG - ulw-work hud-graph - 5 nodes - 6 edges
2 running - 1 waiting - 1 done
                        +-----------------------+
                        | # main - orchestrator |
                        | opus-5-5 - high       |
                        +-----------+-----------+
           +------------------------+------------------------+
           v                        v                        v
+---------------------+  +---------------------+  +---------------------+
| * Refresh HUD docs  |  | + Polish HUD CLI    |  | * Update HUD tests  |
| writing             |  | quick               |  | deep                |
| opus-5-5 - medium   |  | glm-5.3-flash - low |  | opus-5-5 - high     |
| running 2m 10s      |  | done 1m 2s          |  | running 2m 8s       |
+----------+----------+  +----------+----------+  +----------+----------+
           +------------------------+------------------------+
                                    v
                         +---------------------+
                         | . Integration ve... |
                         | inherit             |
                         | opus-5-5            |
                         | waits on 3 lanes    |
                         +---------------------+
"""

MOCK40MSG = """\
DAG · ulw-work hud-graph · 5 nodes · 6…
2 running · 1 waiting · 1 done
  ┌───────────────────────┐
  │ ◆ main · orchestrator │
  │ opus-5-5 · high       │
  └───────────┬───────────┘
┌─────────────┤
│             ▼
│ ┌──────────────────────┐
│ │ ● Refresh HUD docs   │
│ │ writing              │
│ │ opus-5-5 · medium    │
│ │ running 2m 10s       │
│ └───────────┬──────────┘
│             └────────────┐
├─────────────┐            │
│             ▼            │
│ ┌──────────────────────┐ │
│ │ ✓ Polish HUD CLI     │ │
│ │ quick                │ │
│ │ glm-5.3-flash · low  │ │
│ │ done 1m 2s           │ │
│ └───────────┬──────────┘ │
│             └────────────┤
└─────────────┐            │
              ▼            │
  ┌──────────────────────┐ │
  │ ● Update HUD tests   │ │
  │ deep                 │ │
  │ opus-5-5 · high      │ │
  │ running 2m 8s        │ │
  └───────────┬──────────┘ │
              ├────────────┘
              ▼
  ┌──────────────────────┐
  │ · Integration verify │
  │ inherit              │
  │ opus-5-5             │
  │ waits on 3 lanes     │
  └──────────────────────┘
"""

NESTED40MSG = """\
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
│     │    ? · ? · dispatched
│     └─ ◌ Rotate keys
│          ? · ? · dispatched
└─ ● 인증 모듈 문서 정리
     writing · opus-5-… · running 5m 55s
"""


def text(graph_payload: dict, **options) -> str:
    return render_delegation_view(graph_payload, **options).text()


class SnapshotTests(unittest.TestCase):
    maxDiff = None

    def test_the_owner_mock_up_at_73_columns(self) -> None:
        view = render_delegation_view(owner_mock(), width=73)
        self.assertEqual(view.layout, "box")
        self.assertEqual(view.text() + "\n", MOCK73)

    def test_one_three_and_six_parallel_lanes(self) -> None:
        self.assertEqual(text(parallel(1), width=80) + "\n", ONE80)
        self.assertEqual(text(parallel(3), width=80) + "\n", THREE80)
        # Six do not fit one row at 80 columns: a trunk on the left feeds the second row.
        self.assertEqual(text(parallel(6), width=80) + "\n", SIX80)

    def test_nesting_down_to_a_sub_orchestrator_with_a_korean_title(self) -> None:
        self.assertEqual(text(nested(), width=120) + "\n", NESTED120)

    def test_ascii_charset(self) -> None:
        drawn = text(owner_mock(), width=73, charset="ascii")
        self.assertEqual(drawn + "\n", MOCK73ASCII)
        self.assertTrue(drawn.isascii(), "the ASCII charset draws nothing else when the titles are ASCII")

    def test_a_narrow_terminal_gets_the_tree(self) -> None:
        view = render_delegation_view(nested(), width=BOX_MIN_WIDTH - 2)
        self.assertEqual(view.layout, "tree")
        self.assertEqual(view.text() + "\n", NESTED34TREE)

    def test_a_messenger_block_keeps_boxes_when_they_fit_and_the_tree_when_they_do_not(self) -> None:
        budget = dict(width=40, max_chars=1782, max_lines=42)
        view = render_delegation_view(owner_mock(), **budget)
        self.assertEqual((view.layout, view.text() + "\n"), ("box", MOCK40MSG))
        view = render_delegation_view(nested(), **budget)
        self.assertEqual((view.layout, view.text() + "\n"), ("tree", NESTED40MSG))

    def test_a_messenger_cap_discloses_what_it_left_out(self) -> None:
        view = render_delegation_view(parallel(60), width=40, max_chars=1782, max_lines=42)
        lines = view.text_lines()
        self.assertEqual(view.layout, "tree")
        self.assertLessEqual(len(view.text()), 1782)
        self.assertLessEqual(len(lines), 42)
        self.assertEqual(lines[-1], f"└─ +{view.hidden_nodes} more nodes")
        self.assertGreater(view.hidden_nodes, 0)
        shown = sum(1 for line in lines if " Lane " in line)
        self.assertEqual(shown + view.hidden_nodes, 60, "every lane is either drawn or counted")


class FieldTests(unittest.TestCase):
    def test_a_field_without_evidence_is_a_question_mark(self) -> None:
        bare = graph([root(model="", effort=""), {"id": "x", "kind": "lane", "state": "dispatched"}],
                     [("root", "x", "dispatch")])
        drawn = text(bare, width=80)
        box = [line.strip().strip("│").strip() for line in drawn.splitlines() if line.strip().startswith("│")]
        self.assertEqual(box[-4:], ["◌ ?", "?", "?", "dispatched"])
        tree = text(bare, width=80, layout="tree")
        self.assertIn("?  ", tree)

    def test_waits_on_names_how_many_recorded_dependencies_are_unmet(self) -> None:
        self.assertIn("waits on 3 lanes", text(owner_mock(), width=73))
        single = owner_mock()
        single["nodes"][-1]["waits_on"] = 1
        self.assertIn("waits on 1 lane", text(single, width=73))

    def test_a_stuck_unit_is_never_drawn_running(self) -> None:
        stuck = graph([root(), lane("s", "Fan out", "codex", "gpt-6.1-sol", "high", "stuck", elapsed=400,
                                     status="stuck: same error")], [("root", "s", "dispatch")])
        drawn = text(stuck, width=80)
        self.assertIn("! Fan out", drawn)
        self.assertIn("stuck: same error 6m 40s", drawn)
        self.assertNotIn("running", drawn)

    def test_a_root_without_a_model_is_one_line(self) -> None:
        fanout = graph([root(model="", effort=""), lane("u", "Core", "codex", "?", "", "running")],
                       [("root", "u", "dispatch")], name="fanout fanout-0123456789ab", source="fanout")
        lines = text(fanout, width=80).splitlines()
        self.assertIn("◆ main · orchestrator", lines[2])
        self.assertTrue(lines[3].lstrip().startswith("└"), "the root box closes after its one line")

    def test_waves_of_one_root_each_start_a_row_with_their_label(self) -> None:
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
        lines = text(waves, width=80).splitlines()
        first = next(at for at, line in enumerate(lines) if "wave 1 · 14:02" in line)
        second = next(at for at, line in enumerate(lines) if "wave 2 · 14:09" in line)
        self.assertLess(first, second)
        self.assertTrue(any("Integration verify" in line for line in lines[second:]))
        self.assertFalse(any("Refresh docs" in line for line in lines[second:]), "a wave is its own row")
        self.assertIn("wave 2 - 14:09", text(waves, width=80, charset="ascii"))

    def test_text_is_folded_before_it_is_measured(self) -> None:
        self.assertEqual(clean_text("a​b‮c\x1b[31m d\n e"), "abc[31m d e")
        self.assertEqual(cells("인증 모듈"), 9)


class StructureTests(unittest.TestCase):
    def test_an_arrow_only_where_an_edge_is_recorded(self) -> None:
        """Every node with a recorded incoming edge gets exactly one arrow, and no other node gets one."""
        for case in (owner_mock(), parallel(1), parallel(3), parallel(6), nested()):
            for width in (40, 73, 80, 120):
                view = render_delegation_view(case, width=width)
                if view.layout != "box":
                    continue
                with self.subTest(width=width, name=case["name"], nodes=len(case["nodes"])):
                    lowers = {edge["to"] for edge in case["edges"]}
                    self.assertEqual(view.text().count("▼"), len(lowers))

    def test_shapes_the_boxes_cannot_draw_honestly_are_drawn_as_the_tree(self) -> None:
        long_edge = owner_mock()
        long_edge["edges"].append({"from": "root", "to": "v", "kind": "dispatch"})  # skips a layer
        crossing = graph(
            [root(), *(lane(name, name, "quick", "m", "", "running") for name in "abcd")],
            [("root", "a", "dispatch"), ("root", "b", "dispatch"),
             ("a", "c", "depends_on"), ("a", "d", "depends_on"), ("b", "d", "depends_on")],
        )
        for case in (long_edge, crossing):
            with self.subTest(edges=len(case["edges"])):
                self.assertEqual(render_delegation_view(case, width=120).layout, "tree")

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
                edges.append((parent, node_id, "dispatch"))
            case = graph(nodes, edges)
            width = generator.randint(20, 160)
            for charset in ("unicode", "ascii"):
                view = render_delegation_view(case, width=width, charset=charset,
                                              max_lines=generator.choice([None, 3, 12, 40]))
                with self.subTest(trial=trial, width=width, charset=charset, layout=view.layout):
                    self.assertTrue(all(cells(line) <= max(8, width) for line in view.text_lines()))

    def test_nothing_malformed_raises(self) -> None:
        for payload in (None, {}, {"nodes": "x"}, {"nodes": [1, None, {"id": 3}]},
                        {"nodes": [{"id": "a", "kind": "root"}], "edges": [{"from": "a", "to": "zz"}, "e"]},
                        graph([root(), lane("a", "A", "c", "m", "", "running")],
                              [("a", "root", "dispatch"), ("root", "a", "dispatch")]),  # a cycle
                        {"nodes": [{"id": "a", "title": 5, "state": ["x"], "elapsed_seconds": "1",
                                    "waits_on": True}]}):
            with self.subTest(payload=payload):
                view = render_delegation_view(payload, width=60)
                self.assertIsInstance(view.text(), str)


if __name__ == "__main__":
    unittest.main()
