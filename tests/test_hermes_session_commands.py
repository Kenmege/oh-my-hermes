"""Hermes' own session commands are not OMH's to route over.

Turns Hermes writes for its own session commands (`/plan`, `/loop` wakeups,
`/goal` continuations, `/heartbeat`, `/learn`, goal-mode kanban nudges,
`/initiate-setup`) are host-written from fixed templates, and a loop asked for
on a timer is Hermes' native `/loop`. One predicate module in the plugin
bundle answers both for the per-turn hint and the router; these pin the
predicates, the hint and candidate surfaces, and the router and wrapper
decision, each beside the counterexample that must keep routing.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import awareness_route_hint
from omh.plugin_bundle.omh.hermes_session_commands import (
    HOST_COMMAND_TURN_FRAMES,
    NATIVE_LOOP_CADENCE_HINT_KEY,
    host_command_turn,
    native_loop_cadence,
)
from omh.plugin_bundle.omh.skill_shortlist import skill_candidates_for_turn
from omh.routing.chat import (
    HOST_COMMAND_TURN_REASON,
    NATIVE_LOOP_CADENCE_REASON,
    route_chat_message,
)
from omh.wrapper.contract import build_chat_interaction_payload

_GOAL = "ship the OMH loop docs, fix the failing CI on PR 42, and open a PR with the release notes"

# Each host template's opening frame, verbatim from upstream, and a short body
# standing in for the rest of the turn (the goal, the reason, the request). The
# bodies keep enough of each turn's vocabulary that most of them route on
# their own, which is what makes the frame the only difference below.
HOST_TURNS = {
    "plan": (
        "[/plan — plan mode]\n\n",
        "For this turn, you are in PLAN MODE — planning only.\n\n- Do not run mutating terminal commands, "
        "commit, push, or perform external actions.\n\nTask to plan:\nadd a dark mode toggle to the settings page\n",
    ),
    "loop wakeup": (
        "[/loop wakeup #3, every 5m]\n",
        "Recurring task: check whether CI is green on PR 42\n\nPerform the task now against the CURRENT state.",
    ),
    "goal continuation": (
        "[Continuing toward your standing goal]\n",
        f"Goal: {_GOAL}\n\nCompletion contract:\nOutcome: docs merged\nVerify: CI green\n\n"
        "Continue working toward the outcome above. Take the next concrete step. Before claiming the goal is "
        "done, satisfy the Verification criterion and show the concrete evidence (command output, file "
        "contents, test result).",
    ),
    "goal gate failed": (
        "[Continuing toward your standing goal — a quality gate failed]\n",
        f"Goal: {_GOAL}\n\nThe quality gate command below must pass before this goal can be declared done, "
        "and it just failed (attempt 1/3):\n  $ pytest -q\nExit code: 1",
    ),
    "goal kick": ("[Goal set] Continue with the goal you were just given", "; there is no need to re-read it."),
    "kanban continuation": (
        "[Continuing toward this kanban task — judge says it is not done yet]\n",
        "Reason: tests still failing on the login flow\n\nTake the next concrete step toward completing the task. "
        "When the work is genuinely finished, call kanban_complete with a summary.",
    ),
    "kanban finalize": (
        "[The work looks complete, but the task is still open]\n",
        "Reason: the PR is open and CI is green\n\nIf the task is genuinely done, call kanban_complete now with "
        "a short summary of what you did.",
    ),
    "heartbeat": (
        "[Heartbeat — recurring instruction, fires every 10m]\n",
        "check whether CI is green and summarize open PRs",
    ),
    "learn": (
        "[/learn] The user wants you to learn a reusable skill from the request below, and save it.\n\n",
        "THE REQUEST:\nthe deploy workflow we just went through",
    ),
    "initiate setup": (
        "[/initiate-setup]\n\n",
        "Guide the user through first-run setup: pick a model provider, connect accounts, and install plugins.",
    ),
}


def _hint_workflows(message: str) -> list[str]:
    return [str(hint.get("workflow")) for hint in awareness_route_hint(message, max_hints=4)["hints"]]


_PLAN = "".join(HOST_TURNS["plan"])
_GOAL = "".join(HOST_TURNS["goal continuation"])
# What a messaging gateway puts before the turn text (`gateway/run_inbound.py`,
# modelled in `jev_consent`): a Discord trigger note and a reply pointer, each
# closed by `]` and a blank line, channel-history backfill ending in
# `[New message]`, and a shared session's `[<name>] ` sender prefix.
_DISCORD_NOTE = "[Triggering message id: `1234` — use as `message_id` for reply/react/pin via the discord tools.]\n\n"
_REPLY_POINTER = '[Replying to bob: "can you plan it"]\n\n'
_BACKFILL = "bob: the release is friday\n\n[New message]\n"
_SENDER = "[alice] "

# Loops on a timer that name OMH's goal loop -- by a goal-loop phrase or by the
# skill's own name -- and stay with it, and loops on a timer that name nothing
# but the timer, which are Hermes' `/loop`.
_GOAL_LOOP_ON_A_TIMER = (
    "start a goal loop that iterates every 2 hours until the onboarding drop-off is under 20%",
    "goal loop every hour until onboarding drop-off is under 20%",
    "start the goal loop every 30 minutes",
    "1시간마다 온보딩을 계속 개선하는 루프 돌려줘, 첫 실행 성공률 90% 될 때까지",
    "use the ulw-loop skill every 2 hours to check whether CI is green",
    "ulw-loop: every 5 minutes check CI",
    "run ulw-loop every hour on the onboarding docs",
    "/ulw-loop every 5m check CI",
    "./loop every 5 minutes check whether CI is green",
    "run a loop every 30 minutes to improve first-run experience until install friction is lower",
)
_NATIVE_LOOP_ON_A_TIMER = (
    "loop every 5 minutes and check whether CI is green",
    "/loop 5m check whether CI is green",
    "/loop 1h30m check whether the nightly build passed",
    "loop 10m check the queue depth",
    "5분마다 CI가 초록색인지 확인하는 루프 돌려줘",
    "주기적으로 루프 돌면서 큐 상태 봐줘",
)


class HostCommandTurnTest(unittest.TestCase):
    def test_each_host_frame_opens_a_command_turn(self):
        # One fixture per frame: a frame added without a turn to prove it, or a
        # fixture whose frame left the list, fails here.
        self.assertEqual(len(HOST_COMMAND_TURN_FRAMES), len(HOST_TURNS))
        for frame in HOST_COMMAND_TURN_FRAMES:
            with self.subTest(frame=frame.opening):
                self.assertTrue(any((opening + body).startswith(frame.opening) for opening, body in HOST_TURNS.values()))
        for name, (opening, body) in HOST_TURNS.items():
            with self.subTest(turn=name):
                self.assertTrue(host_command_turn(opening + body))
                self.assertFalse(host_command_turn(body))
        for message in (
            "[/loop wakeup #12, self-paced]\nRecurring task: watch the deploy",
            "  [/LOOP WAKEUP #1, every 1h]\nRecurring task: x",
        ):
            with self.subTest(message=message[:30]):
                self.assertTrue(host_command_turn(message))

    def test_a_gateway_prefix_before_the_frame_is_the_hosts(self):
        for name, prefix in (
            ("sender", _SENDER),
            ("discord note", _DISCORD_NOTE),
            ("reply pointer", _REPLY_POINTER),
            ("backfill", _BACKFILL),
            ("discord note and sender", _DISCORD_NOTE + _SENDER),
            ("reply pointer and sender", _REPLY_POINTER + _SENDER),
        ):
            for turn_name, turn in (("plan", _PLAN), ("goal", _GOAL)):
                with self.subTest(prefix=name, turn=turn_name):
                    self.assertTrue(host_command_turn(prefix + turn))
                    # The same prefix before a person's words opens nothing.
                    self.assertFalse(host_command_turn(prefix + "what does plan mode mean?"))

    def test_the_frame_anywhere_but_the_start_is_a_person_writing(self):
        for message in (
            "what does [/loop wakeup #3] mean in my transcript?",
            "please explain [/plan — plan mode]",
            "/plan add a dark mode toggle",
            "[/plan] add a dark mode toggle",
            "",
            "[alice] what does [/plan — plan mode] mean?",
            # A frame followed by the person's own words is not the template:
            # the first line or the template's next fixed text differs.
            "[/initiate-setup] is not working on my desktop app, help",
            "[/loop wakeup #3] what does this line mean in my terminal?",
            "[Goal set] I want to build a todo app in React",
            "[Heartbeat — recurring instruction, fires every 10m] why do I keep seeing this?",
            "[/plan — plan mode]\n\nwhat is this mode for?",
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
            "/loop 1h30m check whether the nightly build passed",
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
            "use the ulw-loop skill every 2 hours to check whether CI is green",
            "run omh-loop every hour on the docs",
            "/ulw-loop every 5m check CI",
            "loop until the tests pass",
            "/loop fix the flaky site-two integration test",
            "grow to 5m users",
            "every user sees the loop",
            # `/loop` takes hours, minutes, then seconds; `5ms` is none of them.
            "loop 5ms check the queue",
            "loop 0m check the queue",
        ):
            with self.subTest(text=text):
                self.assertFalse(native_loop_cadence(text))


class RouteHintTest(unittest.TestCase):
    def test_a_loop_on_a_timer_leaves_the_goal_loop_off_the_candidate_line(self):
        # The candidate line ranks the catalog on its own words; before, the
        # Korean timed loop listed the goal loop there after the hint dropped it.
        message = "5분마다 CI가 초록색인지 확인하는 루프 돌려줘"
        payload = awareness_route_hint(message)
        self.assertIs(payload.get(NATIVE_LOOP_CADENCE_HINT_KEY), True)
        self.assertNotIn("ulw-loop", [label for label, _ in skill_candidates_for_turn(message, route_hint_payload=payload)])
        # The same message without the hint's verdict still lists it, so the
        # verdict is what removes it.
        self.assertIn("ulw-loop", [label for label, _ in skill_candidates_for_turn(message, route_hint_payload={})])

    def test_a_loop_on_a_timer_gets_no_loop_hint(self):
        for message in _NATIVE_LOOP_ON_A_TIMER:
            with self.subTest(message=message):
                self.assertNotIn("ulw-loop", _hint_workflows(message))

    def test_a_timed_loop_hints_like_its_check_alone(self):
        # With the goal-loop hint gone, the slots go to what the rest of the
        # message asks for, exactly as if the timer had not been said: the CI
        # check draws its verification and delivery hints either way.
        for timed, check in (
            ("loop every 5 minutes and check whether CI is green", "check whether CI is green"),
            ("/loop 5m check whether CI is green", "check whether CI is green"),
            ("5분마다 CI가 초록색인지 확인하는 루프 돌려줘", "CI가 초록색인지 확인해줘"),
        ):
            with self.subTest(message=timed):
                self.assertEqual(_hint_workflows(timed), _hint_workflows(check))

    def test_the_goal_loop_keeps_its_hint(self):
        for message in (
            "./loop make OMH a credible Hermes workflow pack with install, docs, QA, and feedback cycles.",
            "./loop every 5 minutes check whether CI is green",
            "run a loop every 30 minutes to improve first-run experience until install friction is lower",
            "start a goal loop that iterates every 2 hours until the onboarding drop-off is under 20%",
            "goal loop every hour until onboarding drop-off is under 20%",
            "run ulw-loop every hour on the onboarding docs",
            "/loop fix the flaky site-two integration test",
        ):
            with self.subTest(message=message):
                self.assertIn("ulw-loop", _hint_workflows(message))
                self.assertNotIn(NATIVE_LOOP_CADENCE_HINT_KEY, awareness_route_hint(message))


class RouterTest(unittest.TestCase):
    _DIRECT = "Answer directly in the current chat; do not open an OMH workflow unless the user asks for one."

    def _route(self, message: str) -> dict[str, object]:
        return route_chat_message(message, source="discord")

    def test_host_command_turns_are_answered_directly(self):
        for name, (opening, body) in HOST_TURNS.items():
            with self.subTest(turn=name):
                route = self._route(opening + body)
                self.assertEqual(route["action"], "fallback")
                self.assertEqual(route["reason"], HOST_COMMAND_TURN_REASON)
                self.assertEqual(route["clarification"], self._DIRECT)
                # The wrapper keeps the router's answer: a goal contract's
                # verification sentence once read there as a catalog question
                # and opened the workflow picker.
                payload = build_chat_interaction_payload(opening + body, source="discord")
                self.assertEqual((payload["route"]["action"], payload["next_action"]), ("fallback", "answer_directly"))

    def test_a_gateway_prefixed_host_turn_is_answered_directly(self):
        for prefix in (_SENDER, _DISCORD_NOTE, _REPLY_POINTER, _BACKFILL, _DISCORD_NOTE + _SENDER):
            for turn in (_PLAN, _GOAL):
                with self.subTest(prefix=prefix[:20], turn=turn[:20]):
                    self.assertEqual(self._route(prefix + turn)["reason"], HOST_COMMAND_TURN_REASON)

    def test_the_same_bodies_without_their_frame_still_route(self):
        # Measured on the bodies above: without the frame each of these
        # dispatches a workflow, so the frame is what the router reads.
        for name in ("plan", "goal continuation", "goal gate failed", "kanban continuation", "heartbeat", "initiate setup"):
            with self.subTest(turn=name):
                self.assertEqual(self._route(HOST_TURNS[name][1])["action"], "dispatch")

    def test_a_loop_on_a_timer_is_answered_with_the_native_command(self):
        for message in _NATIVE_LOOP_ON_A_TIMER:
            with self.subTest(message=message):
                route = self._route(message)
                self.assertEqual(route["action"], "fallback")
                self.assertEqual(route["reason"], NATIVE_LOOP_CADENCE_REASON)
                self.assertEqual(route["clarification"], self._DIRECT)
        self.assertIn("/loop 5m", NATIVE_LOOP_CADENCE_REASON)

    def test_a_timer_beside_the_goal_loop_still_opens_it(self):
        for message in _GOAL_LOOP_ON_A_TIMER:
            with self.subTest(message=message):
                route = self._route(message)
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "loop"))

    def test_the_router_and_the_hint_reach_one_timer_verdict(self):
        # The router answers a timed loop directly exactly when the hint drops
        # the goal loop; neither surface may keep it while the other lets go.
        for message in (*_GOAL_LOOP_ON_A_TIMER, *_NATIVE_LOOP_ON_A_TIMER, "check the deploy every 5 minutes", "keep looping every 2 hours on the release checklist"):
            with self.subTest(message=message):
                router = self._route(message)["reason"] == NATIVE_LOOP_CADENCE_REASON
                hint = awareness_route_hint(message).get(NATIVE_LOOP_CADENCE_HINT_KEY) is True
                self.assertEqual(router, hint)


if __name__ == "__main__":  # pragma: no cover - module entry point
    unittest.main()
