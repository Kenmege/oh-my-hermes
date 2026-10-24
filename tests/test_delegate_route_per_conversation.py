"""A delegation route is the route of the conversation that set it (#2251).

The three `delegation.*` keys are one per profile, and a released Hermes reads
a child's model from them alone. Two sessions routing close together lost the
earlier session's route between its `set` and its `delegate_task`: the child
ran on the other session's model with nothing reported, and the other
session's turn-end restore could take the route out from under it.

Each test drives the real host surfaces: the tool for `set`/`clear`/
`fallback`, `pre_tool_call` for the moment just before Hermes reads the keys
for a `delegate_task`, and `on_session_end` for the turn end. What a child
runs on is what the keys hold when `pre_tool_call` returns.

Every test passes explicit temporary homes. Nothing here may read or write
the developer's own `~/.hermes` or `~/.omh`.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _hermes_session_rows import compacted, row, write_sessions
from omh.plugin_bundle.omh import delegation_route_restore
from omh.plugin_bundle.omh.awareness_delivery import _awareness_delivery_lock
from omh.plugin_bundle.omh.delegation_route_restore import (
    conversation_routes_path,
    delegation_route_restore_path,
    load_conversation_routes,
    conversation_writes_path,
    load_conversation_take_outs,
    load_conversation_writes,
    load_route_restore_record,
)
from omh.plugin_bundle.omh.delegation_routing import read_delegation_route
from omh.plugin_bundle.omh.hermes_delegation import (
    append_delegation_route_provenance,
    load_delegation_route_provenance,
)
from omh.plugin_bundle.omh.hooks.session_hooks import on_session_end, on_session_start
from omh.plugin_bundle.omh.hooks.tool_hooks import pre_tool_call
from omh.plugin_bundle.omh.tools.delegate_route_tool import omh_delegate_route_handler

BASE_CONFIG = (
    "model:\n"
    "  provider: test-parent\n"
    "delegation:\n"
    "  max_concurrent_children: 4\n"
)

CHAINS = {
    "schema_version": "mixture_chain_overrides/v1",
    "categories": {
        "deep": [
            {"model": "deep-head", "reasoning_effort": "high"},
            {"model": "deep-next", "reasoning_effort": "high"},
        ],
        "quick": [
            {"model": "quick-head", "reasoning_effort": "low"},
            {"model": "quick-next", "reasoning_effort": "low"},
        ],
    },
}

DEEP = {"model": "deep-head", "reasoning_effort": "high"}
DEEP_NEXT = {"model": "deep-next", "reasoning_effort": "high"}
QUICK = {"model": "quick-head", "reasoning_effort": "low"}


class PerConversationRouteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.hermes_home = root / "hermes"
        self.omh_home = root / "omh"
        self.hermes_home.mkdir()
        (self.hermes_home / "config.yaml").write_text(BASE_CONFIG, encoding="utf-8")
        chains = self.omh_home / "routing" / "model-chains.json"
        chains.parent.mkdir(parents=True)
        chains.write_text(json.dumps(CHAINS), encoding="utf-8")

    def sessions(self, *rows: dict) -> None:
        """The `state.db` rows Hermes writes before a session's first tool call."""
        write_sessions(self.hermes_home, *rows)

    def top_level(self, *ids: str) -> None:
        self.sessions(*(row(session_id, last_activity_at=time.time()) for session_id in ids))

    def homes(self) -> dict[str, str]:
        return {"omh_home": str(self.omh_home), "hermes_home": str(self.hermes_home)}

    def tool(self, session: str | None, task: str | None, **args) -> dict:
        """One `omh_delegate_route` call; `None` is a keyword the host did not pass."""
        dispatch = {} if session is None else {"session_id": session}
        if task is not None:
            dispatch["task_id"] = task
        return json.loads(omh_delegate_route_handler({**args, **self.homes()}, **dispatch))

    def set_route(self, session: str | None, task: str | None, category: str) -> None:
        result = self.tool(session, task, action="set", category=category)
        self.assertEqual(result["status"], "routed", result)
        self.assertEqual(result["conversation_route"], "recorded", result)

    def dispatch(self, session: str, task: str, **args) -> dict | None:
        """`pre_tool_call` for a `delegate_task`: the host's own call shape."""
        return pre_tool_call(
            tool_name="delegate_task",
            args={"goal": "lane", **args},
            session_id=session,
            task_id=task,
            tool_call_id=f"{session}-{task}-call",
            turn_id=f"{session}-{task}-turn",
            **self.homes(),
        )

    def end_turn(self, session: str, task: str) -> dict:
        return on_session_end(session_id=session, task_id=task, **self.homes())

    def keys(self) -> dict[str, str]:
        """What Hermes reads for the child when `pre_tool_call` has returned."""
        return read_delegation_route(self.hermes_home)


class RouteSurvivesAnotherSessionTest(PerConversationRouteTestCase):
    def test_another_sessions_set_does_not_route_this_sessions_dispatch(self) -> None:
        # The issue's reproduction: A sets, B sets before A dispatches.
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")
        self.assertEqual(self.keys(), QUICK)

        directive = self.dispatch("A", "A-t1")

        self.assertIsNone(directive)
        self.assertEqual(self.keys(), DEEP)
        record = load_route_restore_record(self.omh_home)
        self.assertEqual((record["writer_session_id"], record["writer_task_id"]), ("A", "A-t1"))
        # The newest provenance record names the route really in the keys,
        # which is what the leftover check and the HUD label read.
        newest = load_delegation_route_provenance(self.omh_home)[-1]
        self.assertEqual((newest["alias"], newest["session_id"]), ("deep-head", "A"))

        # And B's own dispatch still runs on B's route, not on A's.
        self.assertIsNone(self.dispatch("B", "B-t1"))
        self.assertEqual(self.keys(), QUICK)

    def test_another_sessions_turn_end_does_not_remove_this_sessions_route(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        ended = self.end_turn("B", "B-t1")

        # B was the last writer, so its turn end put the baseline back...
        self.assertEqual(ended["route_restore"]["status"], "cleared", ended)
        self.assertEqual(ended["route_restore"]["writer_routes"], "released")
        self.assertEqual(self.keys(), {})
        # ...and A's dispatch still runs on A's route.
        self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), DEEP)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"A"})

    def test_a_turn_end_after_this_sessions_dispatch_leaves_its_route(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")
        self.assertIsNone(self.dispatch("A", "A-t1"))

        ended = self.end_turn("B", "B-t1")

        self.assertEqual(ended["route_restore"]["status"], "not_last_writer", ended)
        self.assertEqual(self.keys(), DEEP)
        # A's own turn end is what takes A's route back out.
        ended = self.end_turn("A", "A-t1")
        self.assertEqual(ended["route_restore"]["status"], "cleared", ended)
        self.assertEqual(self.keys(), {})
        self.assertFalse(conversation_routes_path(self.omh_home).exists())

    def test_another_sessions_clear_does_not_remove_this_sessions_route(self) -> None:
        self.set_route("B", "B-t1", "quick")

        cleared = self.tool("C", "C-t1", action="clear")

        self.assertEqual(cleared["status"], "cleared", cleared)
        self.assertEqual(self.keys(), {})
        self.assertIsNone(self.dispatch("B", "B-t1"))
        self.assertEqual(self.keys(), QUICK)

    def test_this_sessions_clear_releases_only_its_own_route(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        cleared = self.tool("A", "A-t1", action="clear")

        self.assertEqual(cleared["conversation_route"]["released"], 1, cleared)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"B"})

    def test_another_sessions_fallback_does_not_route_this_sessions_dispatch(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        fell_back = self.tool("A", "A-t2", action="fallback", category="deep")

        self.assertEqual(fell_back["status"], "fell_back", fell_back)
        self.assertEqual(self.keys(), DEEP_NEXT)
        self.assertIsNone(self.dispatch("B", "B-t1"))
        self.assertEqual(self.keys(), QUICK)
        self.assertIsNone(self.dispatch("A", "A-t2"))
        self.assertEqual(self.keys(), DEEP_NEXT)


class ContinuationTest(PerConversationRouteTestCase):
    def test_a_compression_split_keeps_the_route_by_its_task(self) -> None:
        # The host moves the session id mid-turn; the task id stays.
        self.set_route("A0", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("A1", "A-t1"))

        self.assertEqual(self.keys(), DEEP)

    def test_a_route_with_no_task_is_found_through_the_continuation(self) -> None:
        write_sessions(
            self.hermes_home,
            compacted("A0", at=1050.0),
            row("A1", parent="A0", started_at=1050.0),
            row("B"),
        )
        self.set_route("A0", None, "deep")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("A1", "A-t9"))

        self.assertEqual(self.keys(), DEEP)


class UnroutedDispatchTest(PerConversationRouteTestCase):
    def test_a_session_that_set_no_route_does_not_run_on_another_sessions(self) -> None:
        self.top_level("B", "C")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("C", "C-t1"))

        # C runs on the profile's default, and B gets its route back at its
        # own next dispatch.
        self.assertEqual(self.keys(), {})
        self.assertFalse(delegation_route_restore_path(self.omh_home).exists())
        self.assertIsNone(self.dispatch("B", "B-t1"))
        self.assertEqual(self.keys(), QUICK)

    def test_a_route_from_an_earlier_turn_is_not_this_turns(self) -> None:
        # TUI: a route lives one turn. A restore missed at that turn's end
        # (the keys still hold it) is not a route for the next turn's dispatch.
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")

        self.assertIsNone(self.dispatch("A", "A-t2"))

        self.assertEqual(self.keys(), {})

    def test_a_route_whose_writer_recorded_no_conversation_is_left_alone(self) -> None:
        # An older OMH still running, or an entry that failed to write: the
        # owner could not put its route back, so nobody else takes it out.
        self.top_level("B", "C")
        self.set_route("B", "B-t1", "quick")
        conversation_routes_path(self.omh_home).unlink()
        record = load_route_restore_record(self.omh_home)

        self.assertIsNone(self.dispatch("C", "C-t1"))

        self.assertEqual(self.keys(), QUICK)
        self.assertEqual(load_route_restore_record(self.omh_home), record)


class SingleSessionUnchangedTest(PerConversationRouteTestCase):
    def test_a_dispatch_on_its_own_route_writes_nothing(self) -> None:
        self.set_route("A", "A-t1", "deep")
        config = (self.hermes_home / "config.yaml").read_bytes()
        record = load_route_restore_record(self.omh_home)
        provenance = load_delegation_route_provenance(self.omh_home)

        with mock.patch.object(
            delegation_route_restore, "_awareness_delivery_lock", side_effect=AssertionError("locked")
        ):
            self.assertIsNone(self.dispatch("A", "A-t1"))

        self.assertEqual((self.hermes_home / "config.yaml").read_bytes(), config)
        self.assertEqual(load_route_restore_record(self.omh_home), record)
        self.assertEqual(load_delegation_route_provenance(self.omh_home), provenance)

    def test_no_route_anywhere_reads_two_absent_files_and_nothing_else(self) -> None:
        with mock.patch.object(
            delegation_route_restore, "read_delegation_route", side_effect=AssertionError("read")
        ):
            self.assertIsNone(self.dispatch("A", "A-t1"))

    def test_a_turn_end_still_puts_the_baseline_back(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.assertIsNone(self.dispatch("A", "A-t1"))

        ended = self.end_turn("A", "A-t1")

        self.assertEqual(ended["route_restore"]["status"], "cleared", ended)
        self.assertEqual(self.keys(), {})
        self.assertFalse(conversation_routes_path(self.omh_home).exists())

    def test_control_actions_start_no_child_and_read_no_route(self) -> None:
        self.set_route("B", "B-t1", "quick")
        for action in ("list", "steer", "stop"):
            with self.subTest(action=action):
                self.assertIsNone(self.dispatch("C", "C-t1", action=action))
                self.assertEqual(self.keys(), QUICK)

    def test_other_tools_are_not_touched(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")
        self.assertIsNone(
            pre_tool_call(tool_name="read_file", args={"path": "x"}, session_id="A", task_id="A-t1", **self.homes())
        )
        self.assertEqual(self.keys(), QUICK)


class RefusedRatherThanMisroutedTest(PerConversationRouteTestCase):
    def blocked(self, session: str, task: str) -> dict:
        lock = _awareness_delivery_lock(delegation_route_restore_path(self.omh_home))
        with mock.patch.object(delegation_route_restore, "_DISPATCH_LOCK_SECONDS", 0.05), lock:
            directive = self.dispatch(session, task)
        self.assertIsNotNone(directive)
        self.assertEqual(directive["action"], "block")
        return json.loads(directive["message"])

    def test_a_route_that_cannot_be_put_back_refuses_the_dispatch(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        refusal = self.blocked("A", "A-t1")

        self.assertEqual(refusal["status"], "route_overwritten_by_other_session")
        self.assertEqual((refusal["session_id"], refusal["live_route_writer"]), ("A", "B"))
        self.assertEqual(refusal["reason"], "lock_unavailable")
        self.assertFalse(refusal["dispatched"])
        self.assertIn("'B'", refusal["error"])
        self.assertEqual(self.keys(), QUICK)

    def test_an_unrouted_dispatch_that_cannot_clear_another_route_is_refused(self) -> None:
        self.top_level("B", "C")
        self.set_route("B", "B-t1", "quick")

        refusal = self.blocked("C", "C-t1")

        self.assertEqual(refusal["status"], "other_session_route_live")
        self.assertEqual((refusal["session_id"], refusal["live_route_writer"]), ("C", "B"))
        self.assertEqual(self.keys(), QUICK)

    def test_a_dispatch_with_nothing_to_change_goes_ahead_when_the_lock_is_busy(self) -> None:
        # The #2258 review's probe: a person edited the keys after B's route,
        # so the unlocked read finds nothing to take out. The lock timing out
        # must not turn that into a refusal in the name of an empty writer.
        self.top_level("B", "C")
        self.set_route("B", "B-t1", "quick")
        config = self.hermes_home / "config.yaml"
        config.write_text(config.read_text(encoding="utf-8").replace("quick-head", "person-edit"), encoding="utf-8")
        edited = {**QUICK, "model": "person-edit"}
        self.assertIsNone(self.dispatch("C", "C-t1"))

        lock = _awareness_delivery_lock(delegation_route_restore_path(self.omh_home))
        with mock.patch.object(delegation_route_restore, "_DISPATCH_LOCK_SECONDS", 0.05), lock:
            directive = self.dispatch("C", "C-t2")

        self.assertIsNone(directive)
        self.assertEqual(self.keys(), edited)

    def test_an_unresolved_lineage_goes_ahead_named_when_the_lock_is_busy(self) -> None:
        self.set_route("B", "B-t1", "quick")
        lock = _awareness_delivery_lock(delegation_route_restore_path(self.omh_home))
        with mock.patch.object(delegation_route_restore, "_DISPATCH_LOCK_SECONDS", 0.05), lock, \
                self.assertLogs("omh.delegation_route", level="WARNING"):
            result = self.dispatch("C", "C-t1")

        self.assertNotIn("action", result)
        self.assertEqual(result["omh_route_fallback"]["status"], "lineage_unresolved")
        self.assertEqual(self.keys(), QUICK)

    def test_a_refusal_never_names_an_empty_writer(self) -> None:
        # A host that names no session records its route by task alone.
        self.top_level("C")
        self.set_route(None, "B-t1", "quick")

        refusal = self.blocked("C", "C-t1")

        self.assertEqual(refusal["status"], "other_session_route_live")
        self.assertEqual(refusal["live_route_writer"], "")
        self.assertNotIn("session ''", refusal["error"])
        self.assertIn("another conversation", refusal["error"])
        self.assertEqual(self.keys(), QUICK)


class UnresolvedLineageTest(PerConversationRouteTestCase):
    def test_a_caller_whose_row_cannot_be_read_leaves_the_route_alone(self) -> None:
        # It might be that route's child, so the keys are not stripped: the
        # behaviour before conversation routes existed.
        self.top_level("B")
        self.set_route("B", "B-t1", "quick")
        record = load_route_restore_record(self.omh_home)

        with self.assertLogs("omh.delegation_route", level="WARNING") as logged:
            result = self.dispatch("C", "C-t1")

        # Not blocked (no `action`), but named: in the hook result and the log.
        self.assertNotIn("action", result)
        fallback = result["omh_route_fallback"]
        self.assertEqual(fallback["status"], "lineage_unresolved")
        self.assertEqual((fallback["session_id"], fallback["live_route_writer"]), ("C", "B"))
        self.assertTrue(fallback["dispatched_on_other_route"])
        self.assertIn("'C'", logged.output[0])
        self.assertEqual(self.keys(), QUICK)
        self.assertEqual(load_route_restore_record(self.omh_home), record)

    def test_no_state_db_at_all_leaves_the_route_alone(self) -> None:
        self.set_route("B", "B-t1", "quick")

        with self.assertLogs("omh.delegation_route", level="WARNING"):
            result = self.dispatch("C", "C-t1")

        self.assertEqual(result["omh_route_fallback"]["status"], "lineage_unresolved")
        self.assertEqual(self.keys(), QUICK)

    def test_an_approve_keeps_its_action_and_carries_the_fallback(self) -> None:
        self.set_route("B", "B-t1", "quick")
        approve = {"action": "approve", "message": "repeat", "rule_key": "k"}
        with mock.patch(
            "omh.plugin_bundle.omh.hooks.tool_hooks.repeat_call_directive", return_value=approve
        ), self.assertLogs("omh.delegation_route", level="WARNING"):
            result = self.dispatch("C", "C-t1")

        self.assertEqual(result["action"], "approve")
        self.assertEqual(result["omh_route_fallback"]["status"], "lineage_unresolved")

    def test_a_resolved_unrouted_dispatch_carries_no_note(self) -> None:
        self.top_level("B", "C")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("C", "C-t1"))


class NestedDelegationTest(PerConversationRouteTestCase):
    """`max_spawn_depth` > 1: an orchestrator child dispatches its own workers."""

    def setUp(self) -> None:
        super().setUp()
        self.sessions(
            row("P", last_activity_at=time.time()),
            row("B", last_activity_at=time.time()),
            row("O", parent="P", source="subagent", delegate_from="P", started_at=1050.0),
            row("W", parent="O", source="subagent", delegate_from="O", started_at=1060.0),
        )

    def test_workers_under_a_routed_parent_run_on_the_parents_route(self) -> None:
        self.set_route("P", "P-t1", "deep")
        self.assertIsNone(self.dispatch("P", "P-t1"))

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), DEEP)
        record = load_route_restore_record(self.omh_home)
        self.assertEqual((record["writer_session_id"], record["writer_task_id"]), ("P", "P-t1"))

    def test_a_deeper_worker_still_finds_the_routed_ancestor(self) -> None:
        self.set_route("P", "P-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("W", "W-task"))

        self.assertEqual(self.keys(), DEEP)

    def test_the_parents_route_comes_back_over_another_sessions_and_stays_the_parents(self) -> None:
        self.set_route("P", "P-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), DEEP)
        # Written on the parent's behalf, so the parent's own turn end takes
        # it back out, and the child's does not.
        record = load_route_restore_record(self.omh_home)
        self.assertEqual((record["writer_session_id"], record["writer_task_id"]), ("P", "P-t1"))
        self.assertEqual(self.end_turn("O", "O-task")["route_restore"]["status"], "not_last_writer")
        self.assertEqual(self.end_turn("P", "P-t1")["route_restore"]["status"], "cleared")
        self.assertEqual(self.keys(), {})

    def test_a_parent_route_written_without_a_conversation_entry_is_left_for_its_workers(self) -> None:
        self.set_route("P", "P-t1", "deep")
        conversation_routes_path(self.omh_home).unlink()

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), DEEP)
        self.assertTrue(delegation_route_restore_path(self.omh_home).exists())


class GatewayStaleRouteTest(PerConversationRouteTestCase):
    """Gateway shape: the task id IS the session id, so every turn shares it."""

    def test_the_turn_end_releases_the_route_with_the_restore(self) -> None:
        self.top_level("S")
        self.set_route("S", "S", "deep")

        ended = self.end_turn("S", "S")

        self.assertEqual(ended["route_restore"]["status"], "cleared", ended)
        self.assertEqual(ended["route_restore"]["writer_routes"], "released")
        # The next turn dispatches without setting a route.
        self.assertIsNone(self.dispatch("S", "S"))
        self.assertEqual(self.keys(), {})

    def test_a_turn_end_that_cannot_take_the_lock_releases_nothing_and_restores_nothing(self) -> None:
        # One lock for both, so they cannot come apart: before, a release
        # that timed out after a restore succeeded left the route recorded,
        # and the next turn's dispatch wrote it back.
        self.top_level("S")
        self.set_route("S", "S", "deep")
        with _awareness_delivery_lock(delegation_route_restore_path(self.omh_home)):
            ended = self.end_turn("S", "S")

        self.assertEqual(ended["route_restore"]["status"], "lock_unavailable", ended)
        self.assertNotIn("conversation_route", ended["route_restore"])
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"S"})
        self.assertEqual(self.keys(), DEEP)

    def test_the_session_start_restore_releases_a_dead_writers_route(self) -> None:
        old = time.time() - 3 * 24 * 3600
        with mock.patch.object(delegation_route_restore.time, "time", return_value=old):
            self.set_route("S", "S", "quick")

        started = on_session_start(session_id="NEW", **self.homes())

        self.assertEqual(started["route_restore"]["status"], "cleared", started)
        self.assertEqual(started["route_restore"]["writer_routes"], "released")
        self.assertEqual(load_conversation_routes(self.omh_home), {})
        self.top_level("S")
        self.assertIsNone(self.dispatch("S", "S"))
        self.assertEqual(self.keys(), {})

    def test_an_entry_older_than_the_liveness_bound_is_not_a_route(self) -> None:
        # Nothing has stored a route since (a store prunes old entries), so
        # the entry is still on disk; only its age says it is gone.
        self.top_level("S", "T")
        old = time.time() - 3 * 24 * 3600
        with mock.patch.object(delegation_route_restore.time, "time", return_value=old):
            self.set_route("S", "S", "quick")
        with mock.patch.object(delegation_route_restore.time, "time", return_value=old + 1):
            self.set_route("T", "T", "deep")
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"S", "T"})

        self.assertIsNone(self.dispatch("S", "S"))

        # S's three-day-old route is not written back over the keys.
        self.assertEqual(self.keys(), DEEP)


class ApprovalGateTest(PerConversationRouteTestCase):
    """An earlier OMH gate that asks a person (`approve`) still gets the route gate."""

    def approved(self, session: str, task: str) -> dict | None:
        approve = {"action": "approve", "message": "repeat", "rule_key": "k"}
        with mock.patch(
            "omh.plugin_bundle.omh.hooks.tool_hooks.repeat_call_directive", return_value=approve
        ):
            return self.dispatch(session, task)

    def test_an_approved_dispatch_runs_on_its_own_route(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        directive = self.approved("A", "A-t1")

        self.assertEqual(directive["action"], "approve")
        self.assertEqual(self.keys(), DEEP)

    def test_a_route_refusal_wins_over_the_approve(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")
        lock = _awareness_delivery_lock(delegation_route_restore_path(self.omh_home))
        with mock.patch.object(delegation_route_restore, "_DISPATCH_LOCK_SECONDS", 0.05), lock:
            directive = self.approved("A", "A-t1")

        self.assertEqual(directive["action"], "block")
        self.assertEqual(json.loads(directive["message"])["status"], "route_overwritten_by_other_session")
        self.assertEqual(self.keys(), QUICK)


class RecordingIdentityTest(PerConversationRouteTestCase):
    def test_a_host_that_names_no_session_is_recorded_by_its_task(self) -> None:
        self.set_route(None, "T1", "deep")
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"task:T1"})
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("", "T1"))

        self.assertEqual(self.keys(), DEEP)

    def test_a_route_with_no_identity_says_it_was_not_recorded(self) -> None:
        result = self.tool(None, None, action="set", category="deep")

        self.assertEqual(result["status"], "routed", result)
        self.assertEqual(result["conversation_route"], "unrecorded: no session or task")
        self.assertIn("will not be put back", result["conversation_route_note"])

    def test_the_turn_end_releases_an_entry_recorded_before_a_compression_split(self) -> None:
        self.sessions(compacted("A0", at=1050.0), row("A1", parent="A0", started_at=1050.0))
        self.set_route("A0", None, "deep")

        ended = self.end_turn("A1", "A-t9")

        self.assertEqual(ended["route_restore"]["conversation_route"]["released"], 1, ended)
        self.assertFalse(conversation_routes_path(self.omh_home).exists())


class ReleaseCoherenceTest(PerConversationRouteTestCase):
    """The caller's own release and the restore it follows cannot come apart."""

    def test_clear_releases_in_the_restores_own_lock(self) -> None:
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        real = delegation_route_restore._awareness_delivery_lock
        with mock.patch.object(delegation_route_restore, "_awareness_delivery_lock", wraps=real) as lock:
            cleared = self.tool("A", "A-t1", action="clear")

        self.assertEqual(cleared["status"], "cleared", cleared)
        self.assertEqual(cleared["conversation_route"]["status"], "released", cleared)
        self.assertEqual(lock.call_count, 1)
        # The same task's next dispatch does not put the cleared route back.
        self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), {})

    def test_a_clear_that_cannot_take_the_lock_releases_nothing(self) -> None:
        # The probe's shape: before, the restore could succeed and a second
        # lock for the release time out, so a "cleared" route came back.
        self.set_route("A", "A-t1", "deep")
        with _awareness_delivery_lock(delegation_route_restore_path(self.omh_home)):
            cleared = self.tool("A", "A-t1", action="clear")

        self.assertEqual(cleared["status"], "lock_unavailable", cleared)
        self.assertNotIn("conversation_route", cleared)
        self.assertEqual(self.keys(), DEEP)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"A"})

    def test_an_exhausted_chain_does_not_come_back_on_the_same_task(self) -> None:
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        self.assertEqual(self.tool("A", "A-t1", action="fallback")["status"], "fell_back")

        exhausted = self.tool("A", "A-t1", action="fallback")

        self.assertEqual(exhausted["status"], "exhausted_to_inherit", exhausted)
        self.assertEqual(exhausted["conversation_route"]["status"], "released", exhausted)
        self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), {})

    def release_fails(self):
        failing = mock.patch.object(delegation_route_restore, "_release_locked", side_effect=PermissionError("denied"))
        return failing, self.assertLogs("omh.delegation_route", level="WARNING")

    def test_a_clear_whose_release_fails_does_not_come_back_on_the_same_task(self) -> None:
        # The #2258 review's probe: the keys were cleared, the release write
        # failed, and the same task's next dispatch put the route back.
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        failing, logged = self.release_fails()
        with failing, logged:
            cleared = self.tool("A", "A-t1", action="clear")

        self.assertEqual(cleared["status"], "cleared", cleared)
        self.assertEqual(cleared["conversation_route"]["status"], "error", cleared)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"A"})
        self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), {})
        status = self.tool("A", "A-t1", action="status")
        self.assertIn("conversation_route", status)
        self.assertIsNone(status["conversation_route"])

    def test_an_exhausted_chain_whose_release_fails_does_not_come_back(self) -> None:
        # For exhaustion the route put back was the model that just failed.
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        self.assertEqual(self.tool("A", "A-t1", action="fallback")["status"], "fell_back")
        failing, logged = self.release_fails()
        with failing, logged:
            exhausted = self.tool("A", "A-t1", action="fallback")

        self.assertEqual(exhausted["status"], "exhausted_to_inherit", exhausted)
        self.assertEqual(exhausted["conversation_route"]["status"], "error", exhausted)
        self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), {})

    def test_a_failed_release_after_a_compression_split_does_not_come_back(self) -> None:
        # The release takes the whole conversation's entries, so the stamp of
        # a continuation retires an entry its parent session wrote.
        self.sessions(compacted("A0", at=1050.0), row("A1", parent="A0", started_at=1050.0))
        self.set_route("A0", None, "deep")
        failing, logged = self.release_fails()
        with failing, logged:
            cleared = self.tool("A1", None, action="clear")

        self.assertEqual(cleared["status"], "cleared", cleared)
        self.assertIsNone(self.dispatch("A1", ""))
        self.assertEqual(self.keys(), {})

    def test_a_route_set_after_a_failed_release_still_comes_back(self) -> None:
        # The stamp retires only what was written before it.
        self.top_level("A", "B")
        self.set_route("A", "A-t1", "deep")
        failing, logged = self.release_fails()
        with failing, logged:
            self.tool("A", "A-t1", action="clear")
        with mock.patch.object(delegation_route_restore.time, "time", return_value=time.time() + 1):
            self.set_route("A", "A-t1", "quick")
            self.set_route("B", "B-t1", "deep")

        self.assertIsNone(self.dispatch("A", "A-t1"))

        self.assertEqual(self.keys(), QUICK)

    def test_another_sessions_failed_release_keeps_this_sessions_route(self) -> None:
        self.top_level("A", "B")
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")
        failing, logged = self.release_fails()
        with failing, logged:
            self.tool("B", "B-t1", action="clear")

        self.assertIsNone(self.dispatch("A", "A-t1"))

        self.assertEqual(self.keys(), DEEP)

    def test_a_failed_release_outlives_the_provenance_cap(self) -> None:
        # The #2264 review's probe A1b: the stamp used to be a provenance
        # record, and 33 later appends pushed it out of the 32-record cap.
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        failing, logged = self.release_fails()
        with failing, logged:
            self.tool("A", "A-t1", action="clear")
        for index in range(33):
            append_delegation_route_provenance(
                {"origin": "explicit", "written_at": time.time(), "session_id": f"Z{index}",
                 "alias": "x", "wire_model": "x"},
                self.omh_home,
            )

        self.assertIsNone(self.dispatch("A", "A-t1"))

        self.assertEqual(self.keys(), {})

    def test_a_clear_over_a_persons_edit_whose_release_fails_does_not_come_back(self) -> None:
        # Probe A1c: the person edited the keys, so the clear ends `foreign_edit`
        # and the tool appended no `cleared` record; the dispatch then wrote the
        # route back over the person's edit.
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        config = self.hermes_home / "config.yaml"
        config.write_text(config.read_text(encoding="utf-8").replace("deep-head", "person-edit"), encoding="utf-8")
        edited = self.keys()
        failing, logged = self.release_fails()
        with failing, logged:
            cleared = self.tool("A", "A-t1", action="clear")

        self.assertEqual(cleared["status"], "foreign_edit", cleared)
        self.assertEqual(cleared["conversation_route"]["take_out"], "recorded", cleared)
        self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), edited)
        self.assertIsNone(self.tool("A", "A-t1", action="status")["conversation_route"])

    def test_a_host_that_names_no_session_keeps_its_clear_after_a_failed_release(self) -> None:
        # The stamp is keyed like the entry, `task:<id>`, so a host that names
        # no session is covered too.
        self.set_route(None, "T1", "deep")
        failing, logged = self.release_fails()
        with failing, logged:
            cleared = self.tool(None, "T1", action="clear")

        self.assertEqual(cleared["status"], "cleared", cleared)
        self.assertEqual(set(load_conversation_take_outs(self.omh_home)), {"task:T1"})
        self.assertIsNone(self.dispatch("", "T1"))
        self.assertEqual(self.keys(), {})

    def test_a_stamp_lasts_only_while_its_entry_does(self) -> None:
        self.top_level("A", "B")
        self.set_route("A", "A-t1", "deep")
        self.assertEqual(self.tool("A", "A-t1", action="clear")["conversation_route"]["take_out"], "recorded")
        self.set_route("B", "B-t1", "quick")
        failing, logged = self.release_fails()
        with failing, logged:
            self.tool("B", "B-t1", action="clear")

        # A's entry was released, so its stamp is dropped at the next stamp.
        self.assertEqual(set(load_conversation_take_outs(self.omh_home)), {"B"})

    def test_a_set_whose_entry_failed_does_not_bring_back_the_older_route(self) -> None:
        # #2264's follow-up: the restore record of the newer route was written
        # and its conversation entry was not, so the entry left was the older
        # route of the same task, and the next dispatch put it back.
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        with mock.patch.object(delegation_route_restore.time, "time", return_value=time.time() + 1), \
                mock.patch.object(delegation_route_restore, "_write_conversation_routes",
                                  side_effect=PermissionError("denied")):
            result = self.tool("A", "A-t1", action="set", category="quick")
        self.assertEqual(result["conversation_route"], "unrecorded: PermissionError", result)
        self.assertEqual(load_conversation_routes(self.omh_home)["A"]["route"], DEEP)

        self.assertIsNone(self.dispatch("A", "A-t1"))

        self.assertEqual(self.keys(), QUICK)
        # The stamp stands in for the stale entry, so status reports QUICK.
        self.assertEqual(self.tool("A", "A-t1", action="status")["conversation_route"]["route"], QUICK)

    def test_a_route_put_back_at_its_dispatch_is_still_its_writers(self) -> None:
        # The control for the rule above: the record a reapply writes is newer
        # than the entry it came from but names the same route, so the entry
        # stands, and a session with no route still gets the default.
        self.top_level("A", "B", "C")
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")
        with mock.patch.object(delegation_route_restore.time, "time", return_value=time.time() + 1):
            self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), DEEP)
        self.assertGreater(load_route_restore_record(self.omh_home)["written_at"],
                           load_conversation_routes(self.omh_home)["A"]["written_at"])

        self.assertIsNone(self.dispatch("C", "C-t1"))

        self.assertEqual(self.keys(), {})

    def test_another_sessions_clear_keeps_this_sessions_route(self) -> None:
        self.set_route("B", "B-t1", "quick")

        cleared = self.tool("C", "C-t1", action="clear")

        self.assertEqual(cleared["writer_routes"], "kept", cleared)
        self.assertEqual(cleared["conversation_route"]["released"], 0, cleared)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"B"})

    def test_a_refused_turn_end_restore_keeps_the_route_recorded(self) -> None:
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        refused = {"status": "error", "error": "config changed during route read"}
        with mock.patch.object(delegation_route_restore, "_put_baseline_back", return_value=refused):
            ended = self.end_turn("A", "A-t1")

        self.assertEqual(ended["route_restore"]["status"], "error", ended)
        self.assertNotIn("conversation_route", ended["route_restore"])
        # The keys still hold the route, and so does the record of it.
        self.assertEqual(self.keys(), DEEP)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"A"})
        # The next turn end does both.
        ended = self.end_turn("A", "A-t1")
        self.assertEqual(ended["route_restore"]["status"], "cleared", ended)
        self.assertFalse(conversation_routes_path(self.omh_home).exists())

    def test_a_session_start_release_that_fails_is_reported(self) -> None:
        old = time.time() - 3 * 24 * 3600
        with mock.patch.object(delegation_route_restore.time, "time", return_value=old):
            self.set_route("S", "S", "quick")
        with mock.patch.object(
            delegation_route_restore, "_release_locked", side_effect=PermissionError("denied")
        ), self.assertLogs("omh.delegation_route", level="WARNING"):
            started = on_session_start(session_id="NEW", **self.homes())

        self.assertEqual(started["route_restore"]["status"], "cleared", started)
        self.assertEqual(started["route_restore"]["writer_routes"], "unreleased: PermissionError")
        components = started["omh_degradation"]["components"]
        self.assertEqual(components[0]["error_type"], "PermissionError", started)


class PerWriterStampTest(PerConversationRouteTestCase):
    """A `set` whose entry failed to write stays superseded after another writer's `set`.

    The #2268 Risk: the superseded rule compared an entry with the one
    profile-wide restore record, so once another session's `set` replaced
    that record, the writer's older entry was put back at its next dispatch.
    Since the #2270 review, the writer's stamp also stands in for that older
    entry, so the dispatch runs on the writer's newest route.
    """

    def failed_entry_set(self, session: str | None, task: str, category: str) -> None:
        with mock.patch.object(
            delegation_route_restore, "_write_conversation_routes", side_effect=PermissionError("denied")
        ):
            result = self.tool(session, task, action="set", category=category)
        self.assertEqual(result["conversation_route"], "unrecorded: PermissionError", result)
        self.assertEqual(result["conversation_write"], "recorded", result)

    def other_writer_routes(self) -> None:
        """B ends on DEEP_NEXT, a route neither the stale entry nor the newest write holds."""
        self.set_route("B", "B-t1", "deep")
        self.assertEqual(self.tool("B", "B-t1", action="fallback")["status"], "fell_back")

    def at(self, offset: float):
        return mock.patch.object(delegation_route_restore.time, "time", return_value=self.start + offset)

    def stale_entry_then_another_set(self, session: str | None, task: str) -> None:
        """The exact sequence: W sets, W's next `set` loses its entry, another writer sets."""
        self.start = time.time()
        self.top_level("B", *([session] if session else []))
        with self.at(0):
            self.set_route(session, task, "deep")
        with self.at(1):
            self.failed_entry_set(session, task, "quick")
        with self.at(2):
            self.other_writer_routes()
        record = load_route_restore_record(self.omh_home)
        self.assertEqual(record["writer_session_id"], "B")
        self.assertEqual(load_conversation_routes(self.omh_home)[session or f"task:{task}"]["route"], DEEP)

    def test_a_writer_whose_entry_failed_dispatches_on_its_newest_route(self) -> None:
        # Probe T6 (#2270 review): the stale DEEP stays out, and the stamp
        # puts the writer's own newest QUICK back over B's DEEP_NEXT.
        self.stale_entry_then_another_set("A", "A-t1")

        self.assertIsNone(self.dispatch("A", "A-t1"))

        self.assertEqual(self.keys(), QUICK)
        status = self.tool("A", "A-t1", action="status")["conversation_route"]
        self.assertEqual((status["route"], status["in_keys"]), (QUICK, True), status)

    def test_a_host_that_names_no_session_dispatches_on_its_newest_route(self) -> None:
        # The stamp is keyed like the entry, `task:<id>`, and the route is the
        # caller's own, so no lineage is needed.
        self.stale_entry_then_another_set(None, "T1")

        self.assertIsNone(self.dispatch("", "T1"))

        self.assertEqual(self.keys(), QUICK)
        self.assertEqual(set(load_conversation_writes(self.omh_home)), {"task:T1", "B"})

    def test_a_child_inherits_its_parents_newest_route_over_an_earlier_tasks(self) -> None:
        # Probe W4 (#2270 review): P's t1 entry outlived a turn end whose
        # release failed, and P's t2 `set` lost its entry. O, delegated from
        # P, inherited the t1 DEEP; P's own t2 dispatch had no route at all.
        self.start = time.time()
        self.sessions(
            row("P", last_activity_at=time.time()),
            row("B", last_activity_at=time.time()),
            row("O", parent="P", source="subagent", delegate_from="P", started_at=1050.0),
        )
        with self.at(0):
            self.set_route("P", "P-t1", "deep")
        with mock.patch.object(
            delegation_route_restore, "_release_locked", side_effect=PermissionError("denied")
        ), self.assertLogs("omh.delegation_route", level="WARNING"):
            self.assertEqual(self.end_turn("P", "P-t1")["route_restore"]["status"], "cleared")
        with self.at(1):
            self.failed_entry_set("P", "P-t2", "quick")
        with self.at(2):
            self.other_writer_routes()
        self.assertEqual(load_conversation_routes(self.omh_home)["P"]["task_id"], "P-t1")

        self.assertIsNone(self.dispatch("O", "O-task"))
        self.assertEqual(self.keys(), QUICK)
        with self.at(3):
            self.other_writer_routes()
        self.assertIsNone(self.dispatch("P", "P-t2"))
        self.assertEqual(self.keys(), QUICK)

    def test_a_clear_after_the_failed_set_keeps_the_newest_route_out_too(self) -> None:
        # The clear marks the stamp released, and the take-out runs first:
        # either alone keeps QUICK out, so each is also run without the other
        # -- the stamp left live when marking it fails too.
        for marking in ("released", "unreleased: PermissionError"):
            with self.subTest(marking=marking):
                self.setUp()
                self.stale_entry_then_another_set("A", "A-t1")
                with mock.patch.object(
                    delegation_route_restore, "_release_locked", side_effect=PermissionError("denied")
                ), mock.patch.object(
                    delegation_route_restore,
                    "_release_conversation_writes",
                    wraps=delegation_route_restore._release_conversation_writes,
                    **({} if marking == "released" else {"return_value": marking}),
                ), self.assertLogs("omh.delegation_route", level="WARNING"):
                    cleared = self.tool("A", "A-t1", action="clear")
                self.assertEqual(cleared["conversation_route"]["take_out"], "recorded", cleared)
                stamp = load_conversation_writes(self.omh_home)["A"]
                self.assertEqual((stamp["route"], stamp.get("released", False)), (QUICK, marking == "released"))

                self.assertIsNone(self.dispatch("A", "A-t1"))

                self.assertEqual(self.keys(), {})
                self.assertIsNone(self.tool("A", "A-t1", action="status")["conversation_route"])

    def test_a_turn_end_that_released_the_entry_leaves_the_stamp_standing_for_nothing(self) -> None:
        # Gateway shape (task == session): the turn end takes the record and
        # the entry out, and the stamp it leaves must not bring QUICK back
        # over another session's route.
        self.start = time.time()
        self.top_level("A", "B")
        with self.at(0):
            self.set_route("A", "A", "deep")
        with self.at(1):
            self.failed_entry_set("A", "A", "quick")
        with self.at(2):
            self.other_writer_routes()
        self.assertEqual(self.end_turn("A", "A")["route_restore"]["status"], "not_last_writer")
        self.assertNotIn("A", load_conversation_routes(self.omh_home))
        self.assertIn("A", load_conversation_writes(self.omh_home))

        self.assertIsNone(self.dispatch("A", "A"))

        self.assertEqual(self.keys(), {})

    def test_a_set_of_the_same_route_whose_entry_failed_is_still_reapplied(self) -> None:
        # The control: the newer stamp names the entry's own route, so the
        # entry stands, and so does it after a reapply and another `set`.
        self.start = time.time()
        self.top_level("A", "B")
        with self.at(0):
            self.set_route("A", "A-t1", "deep")
        with self.at(1), mock.patch.object(
            delegation_route_restore, "_write_conversation_routes", side_effect=PermissionError("denied")
        ):
            self.tool("A", "A-t1", action="set", category="deep")
        with self.at(2):
            self.set_route("B", "B-t1", "quick")

        with self.at(3):
            self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), DEEP)
        with self.at(4):
            self.set_route("B", "B-t1", "quick")
        self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), DEEP)

    def test_a_child_still_inherits_its_parents_route(self) -> None:
        self.start = time.time()
        self.sessions(
            row("P", last_activity_at=time.time()),
            row("B", last_activity_at=time.time()),
            row("O", parent="P", source="subagent", delegate_from="P", started_at=1050.0),
        )
        with self.at(0):
            self.set_route("P", "P-t1", "deep")
        with self.at(1):
            self.set_route("B", "B-t1", "quick")
        with self.at(2):
            self.assertIsNone(self.dispatch("O", "O-task"))
        self.assertEqual(self.keys(), DEEP)
        with self.at(3):
            self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), DEEP)
        self.assertEqual(load_conversation_writes(self.omh_home)["P"], load_conversation_routes(self.omh_home)["P"])

    def test_an_absent_or_corrupt_stamp_file_reads_as_no_newer_write(self) -> None:
        # Exactly the behaviour before the file existed: the stale entry is
        # put back. This is also the proof that the stamp is what holds it.
        spoil = {
            "absent": lambda path: path.unlink(),
            "garbage": lambda path: path.write_text("{not json", encoding="utf-8"),
            "schema": lambda path: path.write_text(
                json.dumps({"schema_version": "x", "writes": {}}), encoding="utf-8"
            ),
            "stamp": lambda path: path.write_text(
                json.dumps({"schema_version": "delegation_conversation_writes/v1", "writes": {"A": {"route": 1}}}),
                encoding="utf-8",
            ),
        }
        for kind, spoiled in spoil.items():
            with self.subTest(kind=kind):
                self.setUp()
                self.stale_entry_then_another_set("A", "A-t1")
                spoiled(conversation_writes_path(self.omh_home))

                self.assertIsNone(self.dispatch("A", "A-t1"))

                self.assertEqual(self.keys(), DEEP)

    def test_a_stamp_that_cannot_be_written_does_not_refuse_the_route(self) -> None:
        conversation_writes_path(self.omh_home).mkdir()

        result = self.tool("A", "A-t1", action="set", category="deep")

        self.assertEqual(result["status"], "routed", result)
        # A replace onto a directory is IsADirectoryError on POSIX and
        # PermissionError on Windows; either is a named, not raised, failure.
        prefix, _, error = result["conversation_write"].partition(": ")
        self.assertEqual(prefix, "unrecorded", result)
        self.assertIn(error, {"IsADirectoryError", "PermissionError"}, result)
        self.assertEqual(result["conversation_route"], "recorded", result)
        self.assertEqual(self.keys(), DEEP)

    def release_fails(self):
        return mock.patch.object(
            delegation_route_restore, "_release_locked", side_effect=PermissionError("denied")
        ), self.assertLogs("omh.delegation_route", level="WARNING")

    def test_a_turn_end_whose_release_failed_does_not_let_the_stamp_bring_the_route_back(self) -> None:
        # Probe G1 (#2273 review), gateway shape: the turn end restored the
        # keys and its release failed, so the stale DEEP entry stayed. The
        # stamp of the lost QUICK entry must not stand in for it at the next
        # turn, which set no route: main ran it on the default, and so must
        # this.
        self.start = time.time()
        self.top_level("A", "B")
        with self.at(0):
            self.set_route("A", "A", "deep")
        with self.at(1):
            self.failed_entry_set("A", "A", "quick")
        failing, logged = self.release_fails()
        with failing, logged:
            self.assertEqual(self.end_turn("A", "A")["route_restore"]["status"], "cleared")
        self.assertEqual(load_conversation_routes(self.omh_home)["A"]["route"], DEEP)
        self.assertTrue(load_conversation_writes(self.omh_home)["A"]["released"])
        with self.at(2):
            self.other_writer_routes()

        self.assertIsNone(self.dispatch("A", "A"))

        self.assertEqual(self.keys(), {})
        self.assertIsNone(self.tool("A", "A", action="status")["conversation_route"])

    def test_a_turn_end_whose_release_failed_alone_leaves_the_route_as_before(self) -> None:
        # Probe G2, the control: no entry write failed, so the stamp is the
        # entry, and the six-hour bound is still what retires it, as on main.
        self.start = time.time()
        self.top_level("A", "B")
        with self.at(0):
            self.set_route("A", "A", "quick")
        failing, logged = self.release_fails()
        with failing, logged:
            self.assertEqual(self.end_turn("A", "A")["route_restore"]["status"], "cleared")
        with self.at(1):
            self.other_writer_routes()

        self.assertIsNone(self.dispatch("A", "A"))

        self.assertEqual(self.keys(), QUICK)

    def compaction_family(self) -> None:
        """P1 compacted into P2, whose delegate child is O."""
        self.start = time.time()
        self.sessions(
            compacted("P1", at=self.start - 50),
            row("P2", parent="P1", last_activity_at=self.start),
            row("B", last_activity_at=self.start),
            row("O", delegate_from="P2", source="subagent", last_activity_at=self.start),
        )

    def test_a_child_inherits_the_newest_route_across_a_compaction_split(self) -> None:
        # Probe W4c (#2273 review): P1's t1 DEEP outlived a failed turn-end
        # release, and P2's t2 QUICK lost its entry. O, under P2, inherited
        # P1's DEEP; P2's stamp is the conversation's newest write.
        self.compaction_family()
        with self.at(0):
            self.set_route("P1", "P-t1", "deep")
        failing, logged = self.release_fails()
        with failing, logged:
            self.end_turn("P1", "P-t1")
        with self.at(1):
            self.failed_entry_set("P2", "P-t2", "quick")
        with self.at(2):
            self.other_writer_routes()
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"P1", "B"})

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), QUICK)

    def test_a_child_inherits_across_a_compaction_split_with_no_failures(self) -> None:
        self.compaction_family()
        with self.at(0):
            self.set_route("P1", "P-t1", "deep")
        self.end_turn("P1", "P-t1")
        with self.at(1):
            self.set_route("P2", "P-t2", "quick")
        with self.at(2):
            self.other_writer_routes()

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), QUICK)

    def test_a_child_does_not_inherit_past_its_parents_released_write(self) -> None:
        # P2's turn end marked its stamp released and then failed to release
        # P1's DEEP: neither the released QUICK nor the older DEEP is O's.
        self.compaction_family()
        with self.at(0):
            self.set_route("P1", "P-t1", "deep")
        with self.at(1):
            self.failed_entry_set("P2", "P-t2", "quick")
        failing, logged = self.release_fails()
        with failing, logged:
            self.end_turn("P2", "P-t2")
        with self.at(2):
            self.other_writer_routes()
        self.assertEqual(load_conversation_routes(self.omh_home)["P1"]["route"], DEEP)

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), {})

    def parent_and_child(self) -> None:
        self.start = time.time()
        self.sessions(
            row("P", last_activity_at=self.start),
            row("B", last_activity_at=self.start),
            row("O", delegate_from="P", source="subagent", last_activity_at=self.start),
        )

    def test_a_clear_with_no_routes_file_still_releases_the_stamp(self) -> None:
        # Probe R1 (#2273 re-review): P's only `set` lost its entry, so there
        # was no conversation-routes.json and the clear released nothing; the
        # live stamp then handed the cleared DEEP to P's child.
        self.parent_and_child()
        with self.at(0):
            self.failed_entry_set("P", "P-t1", "deep")
        self.assertFalse(conversation_routes_path(self.omh_home).exists())
        self.assertEqual(self.tool("P", "P-t1", action="clear")["status"], "cleared")
        with self.at(1):
            self.other_writer_routes()

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), {})
        self.assertTrue(load_conversation_writes(self.omh_home)["P"].get("released"))

    def test_a_turn_end_with_no_routes_file_still_releases_the_stamp(self) -> None:
        # Probe R3: both writers' entries failed, so the turn end -- here
        # `not_last_writer` -- had no routes file to release from.
        self.parent_and_child()
        with self.at(0):
            self.failed_entry_set("P", "P-t1", "deep")
        with self.at(1):
            self.failed_entry_set("B", "B-t1", "quick")
        self.assertEqual(self.end_turn("P", "P-t1")["route_restore"]["status"], "not_last_writer")

        self.assertIsNone(self.dispatch("O", "O-task"))

        # B's route is left alone, as on main: B recorded no entry.
        self.assertEqual(self.keys(), QUICK)
        self.assertTrue(load_conversation_writes(self.omh_home)["P"].get("released"))

    def test_an_aged_live_stamp_leaves_an_unrelated_turn_end_on_the_fast_path(self) -> None:
        # Probe B1 (#2273 third review): a killed session's live stamp, seven
        # hours old, kept every turn end off the fast path -- a state.db
        # lookup and the lock -- until some later `set` pruned it.
        self.top_level("K", "U")
        with mock.patch.object(delegation_route_restore.time, "time", return_value=time.time() - 7 * 3600):
            self.failed_entry_set("K", "K-t1", "deep")
        delegation_route_restore_path(self.omh_home).unlink()
        self.assertFalse(conversation_routes_path(self.omh_home).exists())
        self.assertNotIn("released", load_conversation_writes(self.omh_home)["K"])

        with mock.patch.object(
            delegation_route_restore, "_awareness_delivery_lock", side_effect=AssertionError("locked")
        ):
            ended = self.end_turn("U", "U-t1")

        self.assertTrue(ended["route_restore"].get("fast_path"), ended)
        # A fresh live stamp is still release work, as R1 and R3 need.
        self.failed_entry_set("K", "K-t1", "deep")
        delegation_route_restore_path(self.omh_home).unlink()
        self.assertNotIn("fast_path", self.end_turn("U", "U-t1")["route_restore"])

    def three_generations(self) -> None:
        """GG delegated G, which delegated P."""
        self.start = time.time()
        self.sessions(
            row("GG", last_activity_at=self.start),
            row("B", last_activity_at=self.start),
            row("G", delegate_from="GG", source="subagent", last_activity_at=self.start),
            row("P", delegate_from="G", source="subagent", last_activity_at=self.start),
        )

    def test_a_grandparents_later_write_does_not_hide_the_parents_route(self) -> None:
        # Probe GG: no failure anywhere. A newer write in GG's conversation
        # said nothing about G's route, but suppressed it for G's worker.
        self.three_generations()
        with self.at(0):
            self.set_route("GG", "GG-t1", "deep")
        with self.at(1):
            self.set_route("G", "G-task", "quick")
        with self.at(2):
            self.set_route("GG", "GG-t1", "deep")
        self.assertEqual(self.end_turn("GG", "GG-t1")["route_restore"]["status"], "cleared")
        with self.at(3):
            self.other_writer_routes()

        self.assertIsNone(self.dispatch("P", "P-task"))

        self.assertEqual(self.keys(), QUICK)

    def test_a_parents_released_write_is_not_inherited_and_the_grandparents_is(self) -> None:
        # G1's intent one generation up: G's newest write was released by its
        # turn end, whose entry release failed. Neither that write nor G's
        # older QUICK entry is P's; GG's live route, untouched, is.
        self.three_generations()
        with self.at(0):
            self.set_route("GG", "GG-t1", "deep")
            self.assertEqual(self.tool("GG", "GG-t1", action="fallback")["status"], "fell_back")
        with self.at(1):
            self.set_route("G", "G-task", "quick")
        with self.at(2):
            self.failed_entry_set("G", "G-task", "deep")
        failing, logged = self.release_fails()
        with failing, logged:
            self.assertEqual(self.end_turn("G", "G-task")["route_restore"]["status"], "cleared")
        self.assertEqual(load_conversation_routes(self.omh_home)["G"]["route"], QUICK)
        with self.at(3):
            self.set_route("B", "B-t1", "quick")
            self.assertEqual(self.tool("B", "B-t1", action="fallback")["status"], "fell_back")

        self.assertIsNone(self.dispatch("P", "P-task"))

        self.assertEqual(self.keys(), DEEP_NEXT)

    def test_a_release_marks_stamps_released_and_stamps_are_bounded_like_entries(self) -> None:
        self.top_level("A", "B")
        old = time.time() - 7 * 3600
        with mock.patch.object(delegation_route_restore.time, "time", return_value=old):
            self.set_route("C", "C-t1", "quick")
        self.set_route("A", "A-t1", "deep")
        self.assertEqual(self.tool("A", "A-t1", action="clear")["conversation_route"]["status"], "released")
        self.assertTrue(load_conversation_writes(self.omh_home)["A"]["released"])
        self.set_route("B", "B-t1", "quick")
        # A's released stamp stays (it may matter to another session of A's
        # conversation); C's, older than the liveness bound, is dropped.
        self.assertEqual(set(load_conversation_writes(self.omh_home)), {"A", "B"})
        self.assertNotIn("released", load_conversation_writes(self.omh_home)["B"])

        for index in range(70):
            self.set_route(f"S{index}", f"S{index}-t", "deep")
        writes = load_conversation_writes(self.omh_home)
        self.assertEqual(len(writes), 64)
        self.assertIn("S69", writes)


class LineageCycleTest(PerConversationRouteTestCase):
    def test_a_corrupt_cycle_does_not_make_a_session_its_own_parent(self) -> None:
        # A route from an earlier turn is not this turn's (task-first); a
        # `_delegate_from` cycle through A must not hand it back as a parent's.
        self.sessions(
            row("A", delegate_from="P", last_activity_at=time.time()),
            row("P", delegate_from="A", last_activity_at=time.time()),
        )
        self.set_route("A", "A-t1", "deep")

        self.assertIsNone(self.dispatch("A", "A-t2"))

        self.assertEqual(self.keys(), {})


class StatusTest(PerConversationRouteTestCase):
    def status(self, session: str, task: str) -> dict:
        return self.tool(session, task, action="status")

    def test_status_reports_this_conversations_route_beside_the_keys(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        mine = self.status("A", "A-t1")

        self.assertEqual(mine["route"]["model"], "quick-head")
        self.assertEqual(mine["conversation_route"]["route"], DEEP)
        self.assertFalse(mine["conversation_route"]["in_keys"])
        self.assertTrue(self.status("B", "B-t1")["conversation_route"]["in_keys"])

    def test_status_without_a_route_of_ones_own_says_none(self) -> None:
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.status("C", "C-t1")["conversation_route"])
        self.assertIsNone(self.status("B", "B-t2")["conversation_route"])


if __name__ == "__main__":
    unittest.main()
