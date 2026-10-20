"""The delegation graph: every field from a record, `?` where there is none, edges only where recorded."""

from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

from _local_package import load_local_package

load_local_package()

from _delegation_state import PARENT, dispatched, homes, route, start, stop  # noqa: E402
from omh.plugin_bundle.omh.delegation_graph import (  # noqa: E402
    CHILD_STALE_SECONDS,
    GRAPH_LINGER_SECONDS,
    GRAPH_NODE_LIMIT,
    build_delegation_graph,
    fanout_graph,
    graph_fingerprint,
    team_unit_state,
)
from omh.plugin_bundle.omh.delegation_ledger import ledger_key  # noqa: E402

NOW = 1_800_000_000.0


def built(home, **options) -> dict:
    return build_delegation_graph(home.omh, home.hermes, PARENT, now=options.pop("now", NOW + 100), **options)


def node(graph: dict, title: str) -> dict:
    return next(item for item in graph["nodes"] if item.get("title") == title)


def three_lanes(home, *, routed: bool = True) -> None:
    """Three lanes routed writing / quick / deep, dispatched in one call, as ulw-work does it."""
    home.session(PARENT, "claude-opus-5-5", effort="high")
    lanes = [("Refresh HUD docs", "writing", "claude-opus-5-5", "medium"),
             ("Polish HUD CLI", "quick", "glm-5.3-flash", "low"),
             ("Update HUD tests", "deep", "claude-opus-5-5", "high")]
    for index, (title, category, model, effort) in enumerate(lanes):
        if routed:
            route(home, category, model, effort, written_at=NOW + index - 0.5)
        start(home, f"child-{index}", f"sa-{index}", title, now=NOW + index)
        home.session(f"child-{index}", model, parent=PARENT, effort=effort, started=NOW + index)
    dispatched(home, ["sa-0", "sa-1", "sa-2"], now=NOW + 3)


class FieldEvidenceTests(unittest.TestCase):
    def test_the_category_is_the_recorded_route_while_the_observed_model_matches_it(self) -> None:
        with homes() as home:
            three_lanes(home)
            graph = built(home)
            self.assertEqual(
                [(item["title"], item["category"], item["model"], item["effort"]) for item in graph["nodes"][1:]],
                [("Refresh HUD docs", "writing", "claude-opus-5-5", "medium"),
                 ("Polish HUD CLI", "quick", "glm-5.3-flash", "low"),
                 ("Update HUD tests", "deep", "claude-opus-5-5", "high")],
            )
            self.assertEqual({item["category_source"] for item in graph["nodes"][1:]}, {"route_provenance"})

    def test_a_route_the_child_did_not_run_is_no_category(self) -> None:
        with homes() as home:
            home.session(PARENT, "claude-opus-5-5")
            route(home, "deep", "claude-opus-5-5", "high", written_at=NOW - 1)
            start(home, "child-0", "sa-0", "Lane", now=NOW)
            home.session("child-0", "gpt-6.1-sol", parent=PARENT)  # a provider fallback ran something else
            lane = node(built(home), "Lane")
            self.assertEqual((lane["category"], lane["category_source"], lane["model"]),
                             ("?", "route_model_mismatch", "gpt-6.1-sol"))

    def test_inherit_only_by_equality_with_the_parents_recorded_model(self) -> None:
        with homes() as home:
            home.session(PARENT, "claude-opus-5-5")
            start(home, "child-0", "sa-0", "Same model", now=NOW)
            home.session("child-0", "claude-opus-5-5", parent=PARENT)
            start(home, "child-1", "sa-1", "Other model", now=NOW + 1)
            home.session("child-1", "glm-5.3-flash", parent=PARENT)
            graph = built(home)
            self.assertEqual(node(graph, "Same model")["category"], "inherit")
            self.assertEqual(node(graph, "Other model")["category"], "?")

    def test_the_model_is_never_taken_from_current_config(self) -> None:
        with homes() as home:
            (home.hermes / "config.yaml").write_text("delegation:\n  model: claude-fable-5-1\n", encoding="utf-8")
            route(home, "deep", "claude-fable-5-1", "high", written_at=NOW - 1)
            start(home, "child-0", "sa-0", "Not started yet", now=NOW)
            lane = node(built(home), "Not started yet")
            self.assertEqual((lane["model"], lane["category"], lane["state"]), ("?", "?", "dispatched"))

    def test_the_model_hermes_recorded_at_dispatch_fills_in_before_the_child_row_exists(self) -> None:
        with homes() as home:
            start(home, "child-0", "sa-0", "Pinned lane", now=NOW)
            dispatched(home, ["sa-0"], delegation_id="deleg-9", now=NOW + 1)
            home.delegation("deleg-9", ["Pinned lane"], model="glm-5.3-flash")
            self.assertEqual(node(built(home), "Pinned lane")["model"], "glm-5.3-flash")

    def test_titles_come_from_hermes_records_when_this_process_did_not_see_the_start(self) -> None:
        from omh.plugin_bundle.omh import delegation_ledger

        with homes() as home:
            start(home, "child-0", "sa-0", "goal seen here", now=NOW)
            start(home, "child-1", "sa-1", "goal seen here too", now=NOW + 1)
            dispatched(home, ["sa-0", "sa-1"], delegation_id="deleg-7", now=NOW + 2)
            delegation_ledger._titles.clear()
            home.delegation("deleg-7", ["TASK: First lane\nSCOPE: x", "Second lane"])
            home.opener("child-1", "Opener says otherwise")
            titles = [item["title"] for item in built(home)["nodes"][1:]]
            self.assertEqual(titles, ["First lane", "Second lane"])
            start(home, "child-2", "sa-2", "x", now=NOW + 3)
            delegation_ledger._titles.clear()
            home.opener("child-2", "From the opener")
            self.assertEqual(built(home)["nodes"][-1]["title"], "From the opener")
            start(home, "child-3", "sa-3", "x", now=NOW + 4)
            delegation_ledger._titles.clear()
            self.assertEqual(built(home)["nodes"][-1]["title"], "?")


class StateTests(unittest.TestCase):
    def test_states_from_stops_model_calls_and_session_rows(self) -> None:
        with homes() as home:
            three_lanes(home)
            home.usage("child-0", "claude-opus-5-5", last_seen=NOW + 90)  # running
            home.usage("child-1", "glm-5.3-flash", last_seen=NOW + 60)
            stop(home, "child-1", now=NOW + 62)  # completed after a model call
            stop(home, "child-2", now=NOW + 70)  # completed with no model call at all
            graph = built(home)
            self.assertEqual(
                [(item["state"], item["status"]) for item in graph["nodes"][1:]],
                [("running", "running"), ("done", "done"), ("failed", "no model call")],
            )
            self.assertAlmostEqual(node(graph, "Polish HUD CLI")["elapsed_seconds"], 62.0)

    def test_a_child_with_no_news_is_unknown_never_running(self) -> None:
        with homes() as home:
            start(home, "child-0", "sa-0", "Quiet", now=NOW)
            home.session("child-0", "m", parent=PARENT)
            home.usage("child-0", "m", last_seen=NOW + 5)
            later = NOW + CHILD_STALE_SECONDS + 60
            start(home, "child-1", "sa-1", "Fresh", now=later - 10)  # keeps the graph from settling
            lane = node(built(home, now=later), "Quiet")
            self.assertEqual((lane["state"], lane["status"]), ("unknown", "no news"))

    def test_a_segment_closed_for_compaction_is_unknown_never_done(self) -> None:
        with homes() as home:
            start(home, "child-0", "sa-0", "Compacted", now=NOW)
            home.session("child-0", "m", parent=PARENT, ended=NOW + 30, end_reason="compression")
            lane = node(built(home), "Compacted")
            self.assertEqual((lane["state"], lane["status"]), ("unknown", "compacted"))

    def test_failed_and_interrupted_stops(self) -> None:
        with homes() as home:
            for index, status in enumerate(("failed", "timeout", "interrupted")):
                start(home, f"child-{index}", f"sa-{index}", status.title(), now=NOW + index)
                stop(home, f"child-{index}", status, now=NOW + 10 + index)
            states = [(item["state"], item["status"]) for item in built(home)["nodes"][1:]]
            self.assertEqual(states, [("failed", "failed"), ("failed", "timeout"), ("interrupted", "interrupted")])


class StructureTests(unittest.TestCase):
    def test_waves_are_dispatch_turns_and_only_dispatch_edges_are_drawn(self) -> None:
        with homes() as home:
            three_lanes(home)
            start(home, "child-9", "sa-9", "Integration verify", turn="turn-5", now=NOW + 400)
            graph = built(home, now=NOW + 410)
            groups = [item["group"] for item in graph["nodes"][1:]]
            self.assertEqual([group.split(" · ")[0] for group in groups], ["wave 1"] * 3 + ["wave 2"])
            self.assertEqual({edge["kind"] for edge in graph["edges"]}, {"dispatch"})
            self.assertEqual({edge["from"] for edge in graph["edges"]}, {"root"})
            self.assertEqual(graph["layers"][0], ["root"])

    def test_a_nested_child_hangs_from_the_child_that_started_it(self) -> None:
        with homes() as home:
            start(home, "child-0", "sa-0-orch", "Orchestrator", now=NOW)
            start(home, "grand-0", "sa-0-leaf", "Leaf", parent="child-0", parent_subagent="sa-0-orch",
                  now=NOW + 1)
            graph = built(home)
            self.assertIn({"from": "s:sa-0-orch", "to": "s:sa-0-leaf", "kind": "dispatch"}, graph["edges"])
            self.assertEqual(len(graph["layers"]), 3)

    def test_the_graph_is_bounded_and_every_kept_edge_has_both_ends(self) -> None:
        with homes() as home:
            for index in range(GRAPH_NODE_LIMIT + 20):
                start(home, f"child-{index}", f"sa-{index}", f"Lane {index}", now=NOW + index)
            graph = built(home, now=NOW + 200)
            ids = {item["id"] for item in graph["nodes"]}
            self.assertLessEqual(len(graph["nodes"]), GRAPH_NODE_LIMIT)
            self.assertEqual(graph["hidden_nodes"], GRAPH_NODE_LIMIT + 21 - len(graph["nodes"]))
            self.assertTrue(all(edge["from"] in ids and edge["to"] in ids for edge in graph["edges"]))

    def test_a_settled_graph_goes_quiet_after_the_linger(self) -> None:
        with homes() as home:
            start(home, "child-0", "sa-0", "Done lane", now=NOW)
            stop(home, "child-0", now=NOW + 10)
            self.assertEqual(built(home, now=NOW + 20)["status"], "active")
            later = built(home, now=NOW + 10 + GRAPH_LINGER_SECONDS + 1)
            self.assertEqual((later["status"], later["reason"]), ("inactive", "settled"))

    def test_the_fingerprint_ignores_elapsed_time(self) -> None:
        with homes() as home:
            three_lanes(home)
            home.usage("child-0", "claude-opus-5-5", last_seen=NOW + 90)
            first, second = built(home, now=NOW + 100), built(home, now=NOW + 130)
            self.assertNotEqual(node(first, "Refresh HUD docs")["elapsed_seconds"],
                                node(second, "Refresh HUD docs")["elapsed_seconds"])
            self.assertEqual(graph_fingerprint(first), graph_fingerprint(second))
            stop(home, "child-0", now=NOW + 140)
            self.assertNotEqual(graph_fingerprint(first), graph_fingerprint(built(home, now=NOW + 150)))


def team_record(units: list[dict]) -> dict:
    return {"schema_version": "omh_team/v1", "team_id": "hud-graph", "units": units, "events": []}


def unit(unit_id: str, title: str, depends_on: list[str], attempts: list[dict] | None = None) -> dict:
    return {"unit_id": unit_id, "title": title, "depends_on": depends_on, "verification_command": "make test",
            "plan_item": title, "attempts": attempts or [], "blocked": None, "lease": None}


class TeamOverlayTests(unittest.TestCase):
    def write_team(self, home, units: list[dict]) -> None:
        directory = home.omh / "runtime" / "teams" / ledger_key(PARENT)
        directory.mkdir(parents=True)
        (directory / "hud-graph.json").write_text(json.dumps(team_record(units)), encoding="utf-8")

    def test_recorded_dependencies_are_the_edges_and_a_waiting_part_counts_its_unmet_parents(self) -> None:
        with homes() as home:
            home.session(PARENT, "claude-opus-5-5")
            route(home, "writing", "claude-opus-5-5", "medium", written_at=NOW - 1)
            start(home, "child-0", "sa-0", "[omh-team:hud-graph/docs/attempt-1] Refresh docs", now=NOW)
            home.session("child-0", "claude-opus-5-5", parent=PARENT, effort="medium")
            accepted = {"n": 1, "dispatch": {"child_session_id": "child-0", "subagent_id": "sa-0"},
                        "returned_at": "t", "check": {"outcome": "passed", "command": "c", "exit_code": 0,
                                                      "observed_at": "t"}, "inconclusive": []}
            self.write_team(home, [
                unit("docs", "Refresh docs", [], [accepted]),
                unit("cli", "Polish CLI", []),
                unit("verify", "Integration verify", ["docs", "cli"]),
            ])
            graph = built(home)
            self.assertEqual(graph["name"], "team hud-graph")
            self.assertEqual(
                sorted((edge["from"], edge["to"], edge["kind"]) for edge in graph["edges"]),
                [("root", "t:cli", "dispatch"), ("root", "t:docs", "dispatch"),
                 ("t:cli", "t:verify", "depends_on"), ("t:docs", "t:verify", "depends_on")],
            )
            docs, verify = node(graph, "Refresh docs"), node(graph, "Integration verify")
            self.assertEqual((docs["state"], docs["status"], docs["category"], docs["model"]),
                             ("done", "accepted", "writing", "claude-opus-5-5"))
            self.assertEqual((verify["state"], verify["waits_on"], verify["category"], verify["model"]),
                             ("waiting", 1, "?", "?"))
            self.assertEqual(verify["group"], "stage 2")
            self.assertEqual(sum(1 for item in graph["nodes"] if item["title"] == "Refresh docs"), 1,
                             "the helper is the part, not a second node")

    def test_the_part_state_mirrors_the_team_records_own(self) -> None:
        from omh.workflows.team import unit_state

        dispatch = {"child_session_id": "c", "subagent_id": "s"}
        shapes = [
            unit("a", "A", []),
            unit("a", "A", [], [{"n": 1, "dispatch": None, "inconclusive": []}]),
            unit("a", "A", [], [{"n": 1, "dispatch": dispatch, "inconclusive": []}]),
            unit("a", "A", [], [{"n": 1, "dispatch": dispatch, "returned_at": "t", "inconclusive": []}]),
            unit("a", "A", [], [{"n": 1, "dispatch": dispatch, "returned_at": "t",
                                 "check": {"outcome": "passed"}, "inconclusive": []}]),
            unit("a", "A", [], [{"n": 1, "inconclusive": []}, {"n": 2, "dispatch": dispatch, "inconclusive": []}]),
            {**unit("a", "A", []), "blocked": {"reason": "repair_budget_exhausted"}},
        ]
        for shape in shapes:
            with self.subTest(shape=shape):
                self.assertEqual(team_unit_state(shape), unit_state(shape))


class FanoutTests(unittest.TestCase):
    def test_a_fanout_draws_its_units_owners_models_and_dependencies(self) -> None:
        record = {
            "contract": {"fanout_id": "fanout-0123456789ab", "units": [
                {"unit_id": "core", "title": "Core", "owner": "codex", "depends_on": []},
                {"unit_id": "ui", "title": "UI", "owner": "claude-code", "depends_on": ["core"]},
            ]},
            "roster": {"units": [
                {"unit_id": "core", "status": "running", "model": "gpt-6.1-sol", "reasoning_effort": "high",
                 "unit_state": "progress_stalled", "state_reason": "same error", "stalled_for_seconds": "400"},
                {"unit_id": "ui", "status": "prepared_not_observed"},
            ]},
        }
        graph = fanout_graph(record)
        self.assertEqual((graph["source"], graph["scope"], graph["name"]),
                         ("fanout", "global", "fanout fanout-0123456789ab"))
        core, ui = node(graph, "Core"), node(graph, "UI")
        self.assertEqual((core["state"], core["status"], core["category"], core["model"], core["elapsed_seconds"]),
                         ("stuck", "stuck: same error", "codex", "gpt-6.1-sol", 400))
        self.assertEqual((ui["state"], ui["status"], ui["waits_on"], ui["model"]), ("waiting", "prepared", 1, "?"))
        self.assertIn({"from": "f:core", "to": "f:ui", "kind": "depends_on"}, graph["edges"])

    def test_a_native_graph_wins_over_the_fanout(self) -> None:
        record = {"contract": {"fanout_id": "fanout-0123456789ab", "units": [
            {"unit_id": "core", "title": "Core", "owner": "codex", "depends_on": []}]},
            "roster": {"units": [{"unit_id": "core", "status": "running"}]}}
        with homes() as home:
            self.assertEqual(built(home, fanout=record)["source"], "fanout")
            start(home, "child-0", "sa-0", "Native", now=NOW)
            self.assertEqual(built(home, fanout=record)["source"], "native")


class NoRaiseTests(unittest.TestCase):
    def test_a_broken_database_or_record_leaves_the_graph_smaller_never_an_exception(self) -> None:
        with homes() as home:
            start(home, "child-0", "sa-0", "Lane", now=NOW)
            (home.hermes / "state.db").write_bytes(b"not a database")
            lane = node(built(home), "Lane")
            self.assertEqual((lane["model"], lane["category"]), ("?", "?"))
            os.remove(home.hermes / "state.db")
            self.assertEqual(node(built(home), "Lane")["model"], "?")
            teams = home.omh / "runtime" / "teams" / ledger_key(PARENT)
            teams.mkdir(parents=True)
            (teams / "bad.json").write_text("{", encoding="utf-8")
            (teams / "worse.json").write_text(json.dumps({"schema_version": "omh_team/v1", "units": [
                {"unit_id": "a", "depends_on": ["missing"]}]}), encoding="utf-8")
            self.assertEqual(built(home)["source"], "native")
        for session in ("", "no-such-session"):
            graph = build_delegation_graph(Path("/nonexistent/omh"), Path("/nonexistent/hermes"), session, now=NOW)
            self.assertEqual(graph["status"], "inactive")


if __name__ == "__main__":
    unittest.main()
