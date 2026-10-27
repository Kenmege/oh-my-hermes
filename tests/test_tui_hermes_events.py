"""Output Hermes' Ink TUI shows and the OMH TUI used to drop: the review summary, the goal row, MoA progress.

The gateway already writes these events to its client (`gateway_contract`
names each field read); showing them sends nothing back, which the recording
client checks. Driven through `test_tui_modes`' app, the events handed in as
the gateway would send them.
"""

from __future__ import annotations

import time
import unittest

from _tui_lines import line_text, RULE_ROW
from test_tui_modes import SID, drawn, make_app

from omh.tui.app import goal_done_text, goal_line
from omh.tui.gateway import Event
from omh.tui.theme import DEFAULT_THEME as THEME
from omh.tui.transcript import NOTICE_OK, NoticeBlock, ThinkingBlock, Transcript, block_rows

REVIEW = "💾 Self-improvement review: saved 1 memory (the user prefers tabs)"


def goal(**fields: object) -> dict:
    return {"title": "ship the parity round", "status": "active", "turns_used": 3, "max_turns": 20, **fields}


def control(goal_snapshot: dict | None) -> Event:
    return Event("session.control.update", SID, {"control": {"goal": goal_snapshot, "loop": None, "heartbeat": None,
                                                             "revision": "r1", "updated_at": 1.0}})


class ReviewSummaryTests(unittest.TestCase):
    def test_what_the_background_review_saved_is_a_transcript_line_and_nothing_is_sent(self) -> None:
        app, client = make_app()
        app._on_event(Event("review.summary", SID, {"text": f"  {REVIEW}\n"}))
        self.assertEqual([block.text for block in app.transcript.blocks if isinstance(block, NoticeBlock)], [REVIEW])
        self.assertIn(f"· {REVIEW}", drawn(app))
        self.assertEqual(client.calls, [])

    def test_one_that_comes_during_a_turn_closes_nothing_that_runs(self) -> None:
        # Hermes sends it from its background review after a turn, when the next turn (a queued prompt) may run.
        app, client = make_app(busy=True)
        app._on_event(Event("message.start", SID, {}))
        app._on_event(Event("message.delta", SID, {"text": "Hello wor"}))
        app._on_event(Event("tool.start", SID, {"tool_id": "t1", "name": "terminal", "context": "sleep 5"}))
        app._on_event(Event("review.summary", SID, {"text": REVIEW}))
        app._on_event(Event("tool.complete", SID, {"tool_id": "t1", "name": "terminal", "duration_s": 5}))
        app._on_event(Event("message.delta", SID, {"text": "ld"}))
        kinds = [type(block).__name__ for block in app.transcript.blocks]
        self.assertEqual(kinds.count("ToolGroup"), 1)  # the running tool's group was not closed under it
        self.assertNotIn("NoticeBlock", kinds[: kinds.index("ToolGroup") + 1])
        app._on_event(Event("message.complete", SID, {"text": "Hello world"}))
        self.assertIn(REVIEW, [block.text for block in app.transcript.blocks if isinstance(block, NoticeBlock)])
        self.assertEqual(client.calls, [])

    def test_an_empty_summary_or_another_sessions_says_nothing(self) -> None:
        app, _client = make_app()
        app._on_event(Event("review.summary", SID, {"text": "  "}))
        app._on_event(Event("review.summary", "another-session", {"text": REVIEW}))
        self.assertEqual(app.transcript.blocks, [])


class GoalRowTests(unittest.TestCase):
    def test_the_row_reads_as_hermes_ink_goal_bar_for_each_state(self) -> None:
        until = time.mktime((2026, 10, 16, 14, 5, 0, 0, 0, -1))
        cases = {
            "active": (goal(), "⊙ goal · 3/20 turns · ship the parity round"),
            "parked until": (
                goal(wait_barrier={"type": "until", "until_at": until, "reason": "CI runs"}),
                "⏳ goal parked · until 14:05 · CI runs · 3/20 turns · ship the parity round",
            ),
            "parked on a session": (
                goal(wait_barrier={"type": "session", "target": "s-42", "reason": ""}),
                "⏳ goal parked · on session s-42 · 3/20 turns · ship the parity round",
            ),
            "paused": (goal(status="paused", paused_reason="budget"), "⏸ goal paused · budget · 3/20 turns · ship the parity round"),
        }
        for name, (snapshot, expected) in cases.items():
            with self.subTest(state=name):
                line = goal_line(snapshot, 120, THEME)
                assert line is not None
                self.assertEqual(line_text(line), expected)
                self.assertEqual(line[0].style, THEME.running if name == "active" else THEME.warn)
        for status in ("done", "cleared", "failed", None):
            with self.subTest(status=status):
                self.assertIsNone(goal_line(goal(status=status), 120, THEME))
        self.assertIsNone(goal_line(None, 120, THEME))
        self.assertEqual(line_text(goal_line(goal(), 30, THEME)), "⊙ goal · 3/20 turns · ship th…")  # type: ignore[arg-type]

    def test_the_barriers_type_and_target_are_cleaned_like_every_other_field(self) -> None:
        cases = {
            "escapes and controls": ({"type": "ses\x1b[31msion", "target": "s-\x0742  "}, "on session s-42"),
            "a number": ({"type": "turns", "target": 5}, "on turns 5"),
        }
        for name, (barrier, wait) in cases.items():
            with self.subTest(case=name):
                line = goal_line(goal(wait_barrier=barrier), 120, THEME)
                assert line is not None
                self.assertEqual(line_text(line), f"⏳ goal parked · {wait} · 3/20 turns · ship the parity round")

    def test_the_row_stands_above_the_composer_while_a_goal_stands_and_goes_with_it(self) -> None:
        app, client = make_app()
        app._on_event(control(goal()))
        screen = drawn(app)
        rule = next(index for index, row in enumerate(screen) if row.startswith(RULE_ROW))
        self.assertEqual(screen[rule - 1], "⊙ goal · 3/20 turns · ship the parity round")
        app._on_event(control(goal(status="done")))
        self.assertFalse([row for row in drawn(app) if row.startswith("⊙ goal")])  # the row goes; GoalDoneTests owns the line it leaves
        app._on_event(control(goal()))
        app._on_event(control(None))
        self.assertFalse([row for row in drawn(app) if row.startswith("⊙ goal")])
        self.assertEqual(client.calls, [])


class GoalDoneTests(unittest.TestCase):
    """A finished goal leaves its whole wall clock in the scrollback, not only the last turn's.

    A goal that ran 46 minutes across many turns otherwise shows only the final
    turn's `✻ 42s` line, and a person comparing two agents reads that, or
    nothing, as the goal's time.
    """

    def test_the_total_runs_from_the_goals_own_stamps(self) -> None:
        before = goal()
        after = goal(status="done", turns_used=7, created_at=1000.0, updated_at=1000.0 + 46 * 60 + 2)
        self.assertEqual(goal_done_text(before, after), "goal done · 46m 2s · 7/20 turns · ship the parity round")
        paused = goal(status="paused")
        self.assertEqual(goal_done_text(paused, after), "goal done · 46m 2s · 7/20 turns · ship the parity round")

    def test_missing_or_backwards_stamps_leave_the_time_out(self) -> None:
        before = goal()
        for name, fields in {
            "no stamps": {},
            "only the start": {"created_at": 1000.0},
            "backwards": {"created_at": 2000.0, "updated_at": 1000.0},
            "a bool": {"created_at": True, "updated_at": 2000.0},
        }.items():
            with self.subTest(case=name):
                self.assertEqual(goal_done_text(before, goal(status="done", **fields)),
                                 "goal done · 3/20 turns · ship the parity round")

    def test_only_a_shown_goal_turning_done_says_it(self) -> None:
        done = goal(status="done", created_at=1000.0, updated_at=1060.0)
        self.assertEqual(goal_done_text(None, done), "")  # first seen done: a resumed session
        self.assertEqual(goal_done_text(goal(status="done"), done), "")  # already done: said once
        self.assertEqual(goal_done_text(goal(), goal()), "")
        self.assertEqual(goal_done_text(goal(), None), "")  # cleared, not done
        self.assertEqual(goal_done_text(goal(), goal(status="cleared")), "")

    def test_the_row_goes_and_one_ok_line_stays_and_nothing_is_sent(self) -> None:
        app, client = make_app()
        app._on_event(control(goal(created_at=1000.0)))
        app._on_event(control(goal(status="done", turns_used=7, created_at=1000.0, updated_at=1000.0 + 80 * 60)))
        said = [(block.text, block.level) for block in app.transcript.blocks if isinstance(block, NoticeBlock)]
        self.assertEqual(said, [("goal done · 1h 20m · 7/20 turns · ship the parity round", NOTICE_OK)])
        self.assertFalse([row for row in drawn(app) if row.startswith("⊙ goal")])
        app._on_event(control(goal(status="done", turns_used=7, created_at=1000.0, updated_at=1000.0 + 80 * 60)))
        self.assertEqual(len([block for block in app.transcript.blocks if isinstance(block, NoticeBlock)]), 1)
        self.assertEqual(client.calls, [])


class MoaTests(unittest.TestCase):
    def test_a_reference_is_a_collapsed_row_whose_text_the_details_show(self) -> None:
        transcript = Transcript(lambda: 100.0)
        transcript.apply(Event("message.start", SID, {}))
        transcript.apply(Event("moa.reference", SID, {"label": "gpt-6.1-sol", "text": "Use a queue.\nThen drain it.",
                                                      "index": 1, "count": 3}))
        transcript.apply(Event("moa.reference", SID, {"label": "glm-5.3", "text": "Same."}))
        first, second = [block for block in transcript.blocks if isinstance(block, ThinkingBlock)]
        self.assertEqual([line_text(row) for row in block_rows(first, 60, THEME)], ["▸ ◇ Reference 1/3 — gpt-6.1-sol"])
        self.assertEqual(
            [line_text(row) for row in block_rows(first, 60, THEME, expanded=True)],
            ["▾ ◇ Reference 1/3 — gpt-6.1-sol", "  Use a queue.", "  Then drain it."],
        )
        self.assertEqual(line_text(block_rows(second, 60, THEME)[0]), "▸ ◇ Reference — glm-5.3")  # no index given

    def test_progress_and_the_aggregator_phase_are_the_working_rows_words_until_the_turn_ends(self) -> None:
        app, client = make_app(busy=True)
        app._on_event(Event("message.start", SID, {}))
        app._on_event(Event("moa.progress", SID, {"label": "gpt-6.1-sol", "refs_done": 2, "refs_total": 3}))
        self.assertTrue([row for row in drawn(app) if "Working… · MoA: refs 2/3" in row])
        app._on_event(Event("moa.aggregating", SID, {"aggregator": "opus"}))  # read by nothing: the reply follows
        app._on_event(Event("moa.phase", SID, {"phase": "aggregator", "refs_done": 3, "refs_total": 3}))
        self.assertTrue([row for row in drawn(app) if "Working… · MoA: aggregating…" in row])
        app._on_event(Event("message.complete", SID, {"text": "Done.", "status": "complete"}))
        self.assertEqual(app.transcript.moa, "")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
