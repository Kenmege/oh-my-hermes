"""Hermes' own session commands are not OMH's to route over.

`/plan` and `/loop` wakeup turns are host-written from fixed templates, and a
loop asked for on a timer is Hermes' native `/loop`. One predicate module in
the plugin bundle answers both for the per-turn hint and the router; these
pin the predicates, the hint surface, and the router decision, each beside the
counterexample that must keep routing.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import awareness_route_hint
from omh.plugin_bundle.omh.hermes_session_commands import (
    HOST_COMMAND_TURN_FRAMES,
    host_command_turn,
    native_loop_cadence,
)
from omh.routing.chat import (
    HOST_COMMAND_TURN_REASON,
    NATIVE_LOOP_CADENCE_REASON,
    route_chat_message,
)


def _hint_workflows(message: str) -> list[str]:
    return [str(hint.get("workflow")) for hint in awareness_route_hint(message, max_hints=4)["hints"]]


class HostCommandTurnTest(unittest.TestCase):
    def test_each_host_frame_opens_a_command_turn(self):
        self.assertEqual(HOST_COMMAND_TURN_FRAMES, ("[/plan — plan mode]", "[/loop wakeup #"))
        for message in (
            "[/plan — plan mode]\n\nFor this turn, you are in PLAN MODE",
            "[/loop wakeup #3, every 5m]\nRecurring task: check CI",
            "[/loop wakeup #12, self-paced]\nRecurring task: watch the deploy",
            "  [/LOOP WAKEUP #1, every 1h]\nRecurring task: x",
        ):
            with self.subTest(message=message[:30]):
                self.assertTrue(host_command_turn(message))

    def test_the_frame_anywhere_but_the_start_is_a_person_writing(self):
        for message in (
            "what does [/loop wakeup #3] mean in my transcript?",
            "please explain [/plan — plan mode]",
            "/plan add a dark mode toggle",
            "[/plan] add a dark mode toggle",
            "",
        ):
            with self.subTest(message=message):
                self.assertFalse(host_command_turn(message))
        for value in (None, 7, ["[/loop wakeup #1]"]):
            with self.subTest(value=value):
                self.assertFalse(host_command_turn(value))


class NativeLoopCadenceTest(unittest.TestCase):
    def test_a_timer_is_a_cadence(self):
        for text in (
            "loop every 5 minutes and check whether CI is green",
            "loop every 5m and tell me when the deploy is live",
            "every hour, loop over the queue",
            "/loop 5m check whether CI is green",
            "loop 30s ping the health endpoint",
            "5분마다 CI가 초록색인지 확인하는 루프 돌려줘",
            "매시간 루프로 큐 상태 봐줘",
        ):
            with self.subTest(text=text):
                self.assertTrue(native_loop_cadence(text))

    def test_omh_invocation_forms_and_untimed_text_are_not(self):
        for text in (
            "./loop every 5 minutes check whether CI is green",
            "$loop every hour check the deploy",
            "omh loop every 10 minutes poll the queue",
            "loop until the tests pass",
            "/loop fix the flaky site-two integration test",
            "grow to 5m users",
            "every user sees the loop",
        ):
            with self.subTest(text=text):
                self.assertFalse(native_loop_cadence(text))


class RouteHintTest(unittest.TestCase):
    def test_a_loop_on_a_timer_gets_no_loop_hint(self):
        for message in (
            "loop every 5 minutes and check whether CI is green",
            "5분마다 CI가 초록색인지 확인하는 루프 돌려줘",
        ):
            with self.subTest(message=message):
                self.assertNotIn("ulw-loop", _hint_workflows(message))

    def test_the_goal_loop_keeps_its_hint(self):
        # OMH's own form, the same timer under it, and a timer beside a goal
        # phrase of the loopability rule.
        for message in (
            "./loop make OMH a credible Hermes workflow pack with install, docs, QA, and feedback cycles.",
            "./loop every 5 minutes check whether CI is green",
            "run a loop every 30 minutes to improve first-run experience until install friction is lower",
            "/loop fix the flaky site-two integration test",
        ):
            with self.subTest(message=message):
                self.assertIn("ulw-loop", _hint_workflows(message))


class RouterTest(unittest.TestCase):
    def _route(self, message: str) -> dict[str, object]:
        return route_chat_message(message, source="discord")

    def test_host_command_turns_are_answered_directly(self):
        for message in (
            "[/plan — plan mode]\n\nFor this turn, you are in PLAN MODE — planning only.\n"
            "- Do not run mutating terminal commands, commit, push, or perform external actions.\n",
            "[/loop wakeup #3, every 5m]\nRecurring task: check whether CI is green on PR 42",
        ):
            with self.subTest(message=message[:30]):
                route = self._route(message)
                self.assertEqual(route["action"], "fallback")
                self.assertEqual(route["reason"], HOST_COMMAND_TURN_REASON)

    def test_a_loop_on_a_timer_is_answered_with_the_native_command(self):
        route = self._route("loop every 5 minutes and check whether CI is green")
        self.assertEqual(route["action"], "fallback")
        self.assertEqual(route["reason"], NATIVE_LOOP_CADENCE_REASON)
        self.assertIn("/loop 5m", NATIVE_LOOP_CADENCE_REASON)

    def test_a_timer_beside_a_goal_still_opens_the_goal_loop(self):
        for message in (
            "./loop every 5 minutes check whether CI is green",
            "loop every hour until the repo hits 10k stars, improving first-run friction",
        ):
            with self.subTest(message=message):
                route = self._route(message)
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "loop"))


if __name__ == "__main__":  # pragma: no cover - module entry point
    unittest.main()
