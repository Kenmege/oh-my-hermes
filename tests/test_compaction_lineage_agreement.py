"""One compaction rule: conversation identity and the compaction walkers agree.

OMH reads Hermes' compaction lineage in three places. The conversation
identity (`conversation_session_ids`) owns HUD rows, board lanes, cost and a
fallback's route position; `compaction_continuations` decides whether a
compacted writer's route may be put back; `compaction_ancestors` links a
compacted helper's return and a compacted parent's team. Before they shared
one link rule, #2177 measured five row shapes on which the identity answered
differently from the walkers. Each is pinned here with the answer all three
now give.

Rows are in the shape Hermes' own `SessionDB` writes (`_hermes_session_rows`).
Mutations: put back the presence-only marker rule or the one-candidate rule in
the identity; let a reset fork count as a continuation; stop a walker at an
inherited `_branched_from`.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _hermes_session_rows import compacted, row, write_sessions
from omh.plugin_bundle.omh.kanban_board_reader import conversation_session_ids
from omh.plugin_bundle.omh.live_session import compaction_ancestors, compaction_continuations


class LineageAgreementTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.hermes = Path(temporary.name) / "hermes"
        self.hermes.mkdir()

    def identity(self, session: str) -> set[str]:
        return conversation_session_ids(self.hermes, session)

    def ancestors(self, session: str) -> list[str]:
        found = compaction_ancestors(str(self.hermes), session)
        self.assertEqual(found["status"], "resolved", found)
        return found["ancestors"]

    def tips(self, session: str) -> set[str]:
        found = compaction_continuations(str(self.hermes), session)
        self.assertEqual(found["status"], "resolved", found)
        return {tip["id"] for tip in found["tips"]}

    def assert_one_conversation(self, members: set[str]) -> None:
        """Every member names the same conversation, and the walkers reach only inside it."""
        for member in sorted(members):
            with self.subTest(member=member):
                self.assertEqual(self.identity(member), members)
                self.assertLessEqual(set(self.ancestors(member)), members)

    def test_a_plain_rotation_is_one_conversation(self) -> None:
        write_sessions(self.hermes, compacted("A", at=1050.0), row("A2", parent="A", started_at=1050.0))
        self.assert_one_conversation({"A", "A2"})
        self.assertEqual((self.ancestors("A2"), self.tips("A")), (["A"], {"A2"}))

    def test_a_rotated_delegate_child_is_its_own_conversation_across_the_rotation(self) -> None:
        # #2177: the identity returned the current segment only, because the
        # continuation carries the inherited `_delegate_from`.
        write_sessions(
            self.hermes,
            row("P"),
            compacted("C1", at=1050.0, parent="P", source="subagent", delegate_from="P"),
            row("C2", parent="C1", source="subagent", delegate_from="P", started_at=1050.0),
        )
        self.assert_one_conversation({"C1", "C2"})
        self.assertEqual(self.identity("P"), {"P"})
        self.assertEqual(self.tips("C1"), {"C2"})

    def test_a_stale_sibling_beside_the_continuation_is_the_same_chat(self) -> None:
        # #2177: the identity dropped the edge when a closed segment had two
        # continuation-shaped children, so the conversation split in two.
        write_sessions(
            self.hermes,
            compacted("A", at=1050.0),
            row("A2", parent="A", started_at=1050.0),
            row("A2-stale", parent="A", started_at=1050.0, ended_at=1060.0, end_reason="ws_orphan_reap"),
        )
        self.assert_one_conversation({"A", "A2", "A2-stale"})
        self.assertEqual(self.tips("A"), {"A2", "A2-stale"})

    def test_a_reset_fork_beside_the_continuation_is_another_conversation(self) -> None:
        # #2177: the identity counted the reset as a second candidate and lost
        # the real continuation with it.
        write_sessions(
            self.hermes,
            compacted("A", at=1050.0),
            row("A2", parent="A", started_at=1050.0),
            row("R", parent="A", config={"_reset_from": "A"}, started_at=1060.0),
        )
        self.assert_one_conversation({"A", "A2"})
        self.assertEqual(self.identity("R"), {"R"})
        self.assertEqual(self.ancestors("R"), [])

    def test_a_rotated_branch_keeps_its_inherited_marker_and_its_lineage(self) -> None:
        # #2177: the identity stopped at any `_branched_from`, so a branch's
        # continuation that inherited it was cut off from the branch.
        write_sessions(
            self.hermes,
            row("X"),
            compacted("B", at=1050.0, parent="X", config={"_branched_from": "X"}),
            row("B2", parent="B", config={"_branched_from": "X"}, started_at=1050.0),
        )
        self.assert_one_conversation({"B", "B2"})
        self.assertEqual(self.identity("X"), {"X"})

    def test_a_compaction_reason_on_an_open_row_joins_nothing(self) -> None:
        # Hermes writes `ended_at` and `end_reason` in one UPDATE, so a row
        # carrying the reason while still open is not a segment it closed, in
        # any of the three readers.
        write_sessions(
            self.hermes,
            row("A", end_reason="compression"),
            row("A2", parent="A", started_at=1050.0),
        )
        self.assertEqual((self.identity("A"), self.identity("A2")), ({"A"}, {"A2"}))
        self.assertEqual(self.ancestors("A2"), [])

    def test_a_reset_fork_that_is_the_only_child_is_not_a_continuation(self) -> None:
        # #2177: the identity included it, which put a new chat's rows in the
        # old one's. The walkers find no continuation at all.
        write_sessions(
            self.hermes,
            compacted("A", at=1050.0),
            row("R", parent="A", config={"_reset_from": "A"}, started_at=1060.0),
        )
        self.assertEqual((self.identity("A"), self.identity("R")), ({"A"}, {"R"}))
        self.assertEqual(compaction_continuations(str(self.hermes), "A")["reason"], "no_continuation")


if __name__ == "__main__":
    unittest.main()
