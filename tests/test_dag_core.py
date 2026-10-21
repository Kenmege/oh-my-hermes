"""The one DAG core (`plugin_bundle/omh/dag_core.py`) and the callers that decide topology through it.

`omh_dag`, `omh_team`, the fanout contract and the delegation graph and view
all read a graph's references, cycles, layers and shared write scopes here.
These tests pin the core's answers and that each caller still refuses or
draws exactly what it did, wording included.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.coding.fanout import detect_boundary_overlaps, merge_order, validate_fanout_units  # noqa: E402
from omh.coding.fanout_contracts import FanoutContractError  # noqa: E402
from omh.plugin_bundle.omh import dag_core  # noqa: E402
from omh.plugin_bundle.omh.delegation_graph import _layers as graph_layers  # noqa: E402
from omh.plugin_bundle.omh.delegation_view import _layers as view_layers  # noqa: E402
from omh.workflows import team  # noqa: E402

DIAMOND = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}


class EdgeProblemTests(unittest.TestCase):
    def test_unknown_comes_before_self_and_order_is_kept(self) -> None:
        self.assertIsNone(dag_core.edge_problem("b", ["a"], {"a", "b"}))
        self.assertEqual(dag_core.edge_problem("b", ["x", "b"], {"a", "b"}), (dag_core.UNKNOWN_DEPENDENCY, "x"))
        self.assertEqual(dag_core.edge_problem("b", ["b", "x"], {"a", "b"}), (dag_core.SELF_DEPENDENCY, "b"))


class LayerTests(unittest.TestCase):
    def test_layers_are_longest_paths_in_input_order(self) -> None:
        self.assertEqual(dag_core.layers(list(DIAMOND), DIAMOND), [["a"], ["b", "c"], ["d"]])
        # A node waiting on a deep chain lands below the deepest parent, not beside the shallow one.
        chain = {"a": [], "b": ["a"], "c": ["b"], "x": ["a", "c"]}
        self.assertEqual(dag_core.layers(list(chain), chain), [["a"], ["b"], ["c"], ["x"]])

    def test_a_cycle_leaves_its_nodes_and_everything_below_unplaced(self) -> None:
        graph = {"a": [], "b": ["a", "c"], "c": ["b"], "d": ["c"]}
        placed, stuck = dag_core.layer_of(list(graph), graph)
        self.assertEqual(placed, {"a": 0})
        self.assertEqual(stuck, ["b", "c", "d"])
        self.assertIsNone(dag_core.layers(list(graph), graph))

    def test_a_self_edge_is_a_cycle_and_unknown_parents_are_ignored(self) -> None:
        self.assertIsNone(dag_core.layers(["a"], {"a": ["a"]}))
        self.assertEqual(dag_core.layers(["a", "b"], {"b": ["a", "ghost"]}), [["a"], ["b"]])

    def test_ready_needs_every_parent_satisfied(self) -> None:
        self.assertEqual(dag_core.ready(["b", "c", "d"], DIAMOND, {"a", "b"}), ["b", "c"])
        self.assertEqual(dag_core.ready(["d"], DIAMOND, {"a", "b", "c"}), ["d"])
        self.assertEqual(dag_core.ready(["d"], DIAMOND, {"a", "b"}), [])


class PathAndBottleneckTests(unittest.TestCase):
    def test_critical_path_is_the_longest_chain_with_ties_to_the_smaller_id(self) -> None:
        self.assertEqual(dag_core.critical_path(list(DIAMOND), DIAMOND), ["a", "b", "d"])
        two = {"z": [], "y": []}
        self.assertEqual(dag_core.critical_path(list(two), two), ["y"])
        self.assertEqual(dag_core.critical_path([], {}), [])
        self.assertEqual(dag_core.critical_path(["a"], {"a": ["a"]}), [])

    def test_bottlenecks_count_transitive_dependents(self) -> None:
        self.assertEqual(dag_core.bottlenecks(list(DIAMOND), DIAMOND), [("a", 3), ("b", 1), ("c", 1)])
        self.assertEqual(dag_core.bottlenecks(list(DIAMOND), DIAMOND, limit=1), [("a", 3)])
        self.assertEqual(dag_core.bottlenecks(["a", "b"], {}), [])


class SharedScopeTests(unittest.TestCase):
    def test_pairs_name_the_shared_entries_and_whether_a_direct_edge_joins_them(self) -> None:
        scopes = [("a", ["x.py", "y.py"]), ("b", ["y.py"]), ("c", ["y.py"]), ("d", ["z.py"])]
        parents = {"b": ["a"], "c": ["b"]}
        self.assertEqual(
            dag_core.shared_scope_pairs(scopes, parents),
            [("a", "b", ["y.py"], True), ("a", "c", ["y.py"], False), ("b", "c", ["y.py"], True)],
        )


class CallerTests(unittest.TestCase):
    """Each caller decides through the core and keeps its own refusal."""

    def test_team_refuses_cycles_and_bad_references(self) -> None:
        units = [{"unit_id": "a", "depends_on": ["b"]}, {"unit_id": "b", "depends_on": ["a"]}]
        with self.assertRaises(team.TeamRefusal) as caught:
            team._require_acyclic(units)
        self.assertEqual(caught.exception.reason, "dependency_cycle")
        team._require_acyclic([{"unit_id": "a", "depends_on": []}, {"unit_id": "b", "depends_on": ["a"]}])

    def test_fanout_messages_are_unchanged(self) -> None:
        units = [
            {"unit_id": "a", "file_scope": ["x.py"], "depends_on": []},
            {"unit_id": "b", "file_scope": ["x.py"], "depends_on": ["a"]},
            {"unit_id": "c", "file_scope": ["c.py"], "depends_on": ["b", "a"]},
        ]
        self.assertEqual(merge_order(units), ["a", "b", "c"])
        self.assertEqual(
            detect_boundary_overlaps(units),
            [{"units": ["a", "b"], "shared_files": ["x.py"],
              "resolution": "ordered by depends_on edge; merge strictly in merge_order"}],
        )
        with self.assertRaisesRegex(FanoutContractError, r"^depends_on contains a cycle among units: a, b$"):
            merge_order([{"unit_id": "a", "depends_on": ["b"]}, {"unit_id": "b", "depends_on": ["a"]}])
        with self.assertRaisesRegex(FanoutContractError, r"^units a and b share files \['x.py'\] without a depends_on"):
            detect_boundary_overlaps([{"unit_id": "a", "file_scope": ["x.py"]}, {"unit_id": "b", "file_scope": ["x.py"]}])
        with self.assertRaisesRegex(FanoutContractError, r"^unit b depends on unknown unit 'zz'$"):
            validate_fanout_units([{"unit_id": "a", "file_scope": ["a"]}, {"unit_id": "b", "file_scope": ["b"],
                                                                            "depends_on": ["zz"]}])
        with self.assertRaisesRegex(FanoutContractError, r"^unit b cannot depend on itself$"):
            validate_fanout_units([{"unit_id": "b", "file_scope": ["b"], "depends_on": ["b"]}])

    def test_graph_and_view_layers_come_from_the_core(self) -> None:
        nodes = [{"id": "root"}, {"id": "a"}, {"id": "b"}]
        edges = [{"from": "root", "to": "a"}, {"from": "a", "to": "b"}, {"from": "root", "to": "b"}]
        self.assertEqual(graph_layers(nodes, edges), [["root"], ["a"], ["b"]])
        self.assertEqual(graph_layers(nodes, edges + [{"from": "b", "to": "a"}]), [])
        self.assertEqual(view_layers(["root", "a", "b"], [("root", "a"), ("a", "b")]), {"root": 0, "a": 1, "b": 2})
        self.assertIsNone(view_layers(["a", "b"], [("a", "b"), ("b", "a")]))


if __name__ == "__main__":
    unittest.main()
