"""Hermes' own session commands are not OMH's to route over.

Turns Hermes writes for its own session commands (`/plan`, `/loop` wakeups,
`/goal` continuations, `/heartbeat`, `/learn`, goal-mode kanban nudges,
`/initiate-setup`) are host-written from fixed templates, and a loop asked for
on a timer is Hermes' native `/loop`. One module in the plugin bundle answers
both questions; the chat router, the loop guard, the route hint and the
candidate line all take its answer. These pin the two functions and then pin
that every surface says what they say, each beside the counterexample that
must keep routing.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import awareness_route_hint
from omh.plugin_bundle.omh.hermes_session_commands import (
    HOST_COMMAND_TURN_FRAMES,
    LOOP_CONSTRUCT_PHRASES,
    LOOP_TIMER_CONSTRUCT,
    LOOP_TIMER_GOAL,
    LOOP_TIMER_NATIVE,
    LOOP_TIMER_NONE,
    LOOP_TIMERS_ANSWERED_DIRECTLY,
    NATIVE_LOOP_CADENCE_HINT_KEY,
    host_command_turn,
    loop_timer_verdict,
)
from omh.plugin_bundle.omh.skill_shortlist import skill_candidates_for_turn
from omh.routing.chat import (
    HOST_COMMAND_TURN_REASON,
    LOOP_CONSTRUCT_REASON,
    NATIVE_LOOP_CADENCE_REASON,
    route_chat_message,
)
from omh.routing.policy import LOOP_GOAL_GUARD, active_routing_guard_rules
from omh.routing.query import RoutingQuery
from omh.routing.reference_regions import executable_routing_text
from omh.wrapper.contract import build_chat_interaction_payload

PROBE_SENTENCES = Path(__file__).resolve().parent / "fixtures" / "loop_timer_probe_sentences.json"

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
        "---\nname: initiate-setup\ndescription: Run the first-run setup chat.\n---\n\n"
        "Guide the user through first-run setup: pick a model provider, connect accounts, and install plugins.",
    ),
}

_PLAN = "".join(HOST_TURNS["plan"])
_GOAL_TURN = "".join(HOST_TURNS["goal continuation"])

# What a messaging gateway puts before the turn text (`gateway/run_inbound.py`,
# modelled in `jev_consent`): a Discord trigger note and a reply pointer, each
# closed by `]` and a blank line, channel-history backfill ending in
# `[New message]`, and a shared session's `[<name>] ` sender prefix. v2026.9.7
# assembles them reply pointer outermost, then the trigger note, then the
# sender-prefixed text.
_NOTE = "[Triggering message id: `1234` — use as `message_id` for reply/react/pin via the discord tools.]\n\n"
_REPLY = '[Replying to: "is CI green?"]\n\n'
_BACKFILL = "[bob] hi\n[carol] ok\n\n[New message]\n"
# Backfill is earlier messages, never the request: a loop on a timer or OMH's
# own name there decides nothing about the person's current message.
_LOOP_BACKFILL = "[bob] loop every 5 minutes and check CI\n[carol] ok\n\n[New message]\n"
_GOAL_LOOP_BACKFILL = "[bob] I use ulw-loop every hour\n\n[New message]\n"
_SENDER = "[alice] "
GATEWAY_PREFIXES = {
    "none": "",
    "sender": _SENDER,
    "note": _NOTE,
    "note+sender": _NOTE + _SENDER,
    "reply": _REPLY,
    "reply+sender": _REPLY + _SENDER,
    "backfill+sender": _BACKFILL + _SENDER,
    "note+backfill+sender": _NOTE + _BACKFILL + _SENDER,
    "loop backfill+sender": _LOOP_BACKFILL + _SENDER,
    "goal loop backfill+sender": _GOAL_LOOP_BACKFILL + _SENDER,
    "reply+note+sender": _REPLY + _NOTE + _SENDER,
    "slack sender": "[alice | Slack user <@U123ABC>] ",
}

# One row per way a request can open, with the verdict it has behind every
# gateway prefix above.
TIMED_LOOP_FORMS = {
    # OMH's own forms where the loop command would be: the goal loop.
    "omh loop every 30 minutes check CI": LOOP_TIMER_GOAL,
    "$loop every hour check CI": LOOP_TIMER_GOAL,
    "./loop every 5 minutes check CI": LOOP_TIMER_GOAL,
    "/ulw-loop every 5m check CI": LOOP_TIMER_GOAL,
    "omh loop every 2 hours improve the docs": LOOP_TIMER_GOAL,
    # The loop command with a goal: the goal loop.
    "loop every hour and improve the docs": LOOP_TIMER_GOAL,
    # The loop command and its cadence: Hermes' `/loop`.
    "/loop 5m check CI": LOOP_TIMER_NATIVE,
    "loop every 5 minutes and check whether CI is green": LOOP_TIMER_NATIVE,
    "every 10 minutes loop over the inbox and triage new issues": LOOP_TIMER_NATIVE,
    "5분마다 CI가 초록색인지 확인하는 루프 돌려줘": LOOP_TIMER_NATIVE,
    # Asked to write one that runs on a timer: a construct.
    "write a bash loop that pings the server every 10 seconds": LOOP_TIMER_CONSTRUCT,
    # OMH's name away from the opening, or a loop the request talks about:
    # nothing to decide, so the turn routes as it did before.
    "run ulw-loop every hour on the onboarding docs": LOOP_TIMER_NONE,
    "the retry loop fires every 5 seconds, is the backoff right?": LOOP_TIMER_NONE,
}

# Everything else the verdict has to tell apart, one sentence per reason.
VERDICTS = {
    # The loop command first, then its cadence, typed or bare, after a
    # courtesy opener or not.
    "loop every 5m and tell me when the deploy is live": LOOP_TIMER_NATIVE,
    "/loop 1h30m check whether the nightly build passed": LOOP_TIMER_NATIVE,
    "loop 30s ping the health endpoint": LOOP_TIMER_NATIVE,
    "loop every 5 min check CI": LOOP_TIMER_NATIVE,
    "loop every 2 hrs check CI": LOOP_TIMER_NATIVE,
    "loop every 30 sec check the queue": LOOP_TIMER_NATIVE,
    "loop every 5mins check CI": LOOP_TIMER_NATIVE,
    "loop: every 5 minutes check CI": LOOP_TIMER_NATIVE,
    "loop each hour and fix one flaky test until none are left": LOOP_TIMER_NATIVE,
    "loop every 10 minutes and keep fixing failing tests until the suite is green": LOOP_TIMER_NATIVE,
    "please loop every 5 minutes and check CI": LOOP_TIMER_NATIVE,
    "hey, can you loop every hour and check the queue?": LOOP_TIMER_NATIVE,
    # The cadence first, then the loop as the request's verb or adverbial.
    "every hour, loop over the queue": LOOP_TIMER_NATIVE,
    "every 2 hours loop and make the test suite faster": LOOP_TIMER_NATIVE,
    "every 5 minutes check CI in a loop": LOOP_TIMER_NATIVE,
    # Korean: the cadence opens, and the loop is what it runs.
    "5분마다 반복해서 CI 확인해줘": LOOP_TIMER_NATIVE,
    "10분마다 loop 돌려서 빌드 상태 체크해줘": LOOP_TIMER_NATIVE,
    "루프 돌면서 5분마다 CI 확인해줘": LOOP_TIMER_NATIVE,
    # A check that mentions a star, adoption, product, or friction figure is
    # still a check, and so is a measure moved with no product or star in it.
    "loop every hour: check the adoption dashboard": LOOP_TIMER_NATIVE,
    "loop every hour and tell me the stars count": LOOP_TIMER_NATIVE,
    "loop every 5 minutes and check whether the friction log has new entries": LOOP_TIMER_NATIVE,
    "loop every 5 minutes and check whether install got faster": LOOP_TIMER_NATIVE,
    "loop every 5 minutes: run the benchmark and make p95 latency lower until it is under 100ms": LOOP_TIMER_NATIVE,
    "loop every hour and cut a release branch": LOOP_TIMER_NATIVE,
    "loop every 10 minutes and get to the bottom of the flaky test": LOOP_TIMER_NATIVE,
    "1시간마다 루프 돌려서 테스트 커버리지 올려줘": LOOP_TIMER_NATIVE,
    # Typed with its cadence, `/loop` is Hermes' command whatever its prompt mentions.
    "/loop 5m check whether the stars badge updated": LOOP_TIMER_NATIVE,
    "/loop 10m improve nothing, just check CI": LOOP_TIMER_NATIVE,
    "/loop 10m check whether the improve button renders": LOOP_TIMER_NATIVE,
    "/loop 30m keep improving the docs until the repo hits 10k stars": LOOP_TIMER_NATIVE,
    "/loop 5m check whether ulw-loop finished": LOOP_TIMER_NATIVE,
    "/loop 5m run ulw-loop on the docs": LOOP_TIMER_NATIVE,
    "/loop 10m ask omh loop for status": LOOP_TIMER_NATIVE,
    # A construct named inside the check is still asked of the loop command.
    "loop every 5 minutes and check whether the event loop lag is under 100ms": LOOP_TIMER_NATIVE,
    # "loop skill" names the goal loop only as those two whole words.
    "loop every 5 minutes and post in the loop skills channel": LOOP_TIMER_NATIVE,
    "loop every 5 minutes and check the loop skillet timer": LOOP_TIMER_NATIVE,
    # Asked to write or explain a code or media loop that runs on a timer.
    "write a for loop in python that prints a timestamp every 5 seconds": LOOP_TIMER_CONSTRUCT,
    "How do I write a bash loop that pings a server every 30s?": LOOP_TIMER_CONSTRUCT,
    "explain the event loop that ticks every 5 seconds": LOOP_TIMER_CONSTRUCT,
    # ... unless a goal rides along; with no timer it is no question here.
    "write a for loop every 5 minutes that keeps improving the onboarding docs": LOOP_TIMER_GOAL,
    "write a for loop in python that prints a timestamp": LOOP_TIMER_NONE,
    "explain how the event loop works": LOOP_TIMER_NONE,
    # The skill's own name in the command's place: spaced, after a colon,
    # after a cadence, or with a non-breaking hyphen.
    "ulw loop every 5 minutes check CI": LOOP_TIMER_GOAL,
    "ulw-loop: every 5 minutes check CI": LOOP_TIMER_GOAL,
    "every 2 hours ulw-loop the flaky test list": LOOP_TIMER_GOAL,
    "ulw‑loop every 5 minutes check CI": LOOP_TIMER_GOAL,
    # OMH's name anywhere in a loop command that is not typed out.
    "loop every 5 minutes and check whether ulw-loop finished": LOOP_TIMER_GOAL,
    # A name for the goal loop beside the command.
    "loop every 2 hours with the loop skill and check CI": LOOP_TIMER_GOAL,
    "loop every hour, use the goal loop on onboarding": LOOP_TIMER_GOAL,
    # A goal: making something better, or moving a product or star measure.
    "loop every hour and enhance the docs": LOOP_TIMER_GOAL,
    "loop every 5 minutes improve the onboarding docs until signups rise": LOOP_TIMER_GOAL,
    "loop every 30 minutes to lower install friction": LOOP_TIMER_GOAL,
    "loop every hour to get us to 10k stars": LOOP_TIMER_GOAL,
    "loop every hour to raise adoption": LOOP_TIMER_GOAL,
    "loop every hour until the repo hits 10k stars, improving first-run friction": LOOP_TIMER_GOAL,
    "1시간마다 온보딩을 계속 개선하는 루프 돌려줘, 첫 실행 성공률 90% 될 때까지": LOOP_TIMER_GOAL,
    "2시간마다 루프 돌려서 설치 후 첫 성공까지 걸리는 시간을 줄여줘": LOOP_TIMER_GOAL,
    # A loop word that is a thing the request talks about, not its command.
    "check CI every 5 min in a loop": LOOP_TIMER_NONE,
    "the music loop restarts every 30 seconds in my game, how do I fix it?": LOOP_TIMER_NONE,
    "my polling loop fires every 2 seconds and leaks memory": LOOP_TIMER_NONE,
    "keep me in the loop every 30 minutes about the incident": LOOP_TIMER_NONE,
    "every 10 minutes loop detection kills my agent": LOOP_TIMER_NONE,
    "every engineer spends 30 minutes in the review loop; draft a proposal to cut it": LOOP_TIMER_NONE,
    "every 30 minutes run a loop that reduces friction in the product onboarding": LOOP_TIMER_NONE,
    "event loop every 5 minutes improve the onboarding docs until signups rise": LOOP_TIMER_NONE,
    "매시간 루프로 큐 상태 봐줘": LOOP_TIMER_NONE,
    "반복문 말고 5분마다 CI 확인하는 루프 돌려줘": LOOP_TIMER_NONE,
    # A Korean request to write a code loop is not read as a construct; it
    # routes as it did before.
    "파이썬 for 루프로 5초마다 로그 출력하는 코드 짜줘": LOOP_TIMER_NONE,
    # OMH's names and goal-loop names away from the opening decide nothing either.
    "use the ulw-loop skill every 2 hours to check whether CI is green": LOOP_TIMER_NONE,
    "start the goal loop every 30 minutes": LOOP_TIMER_NONE,
    "omh 루프 30분마다 문서 개선": LOOP_TIMER_NONE,
    # No timer, no loop command, or a different skill's name: nothing to decide.
    "loop until the tests pass": LOOP_TIMER_NONE,
    "/loop fix the flaky site-two integration test": LOOP_TIMER_NONE,
    "check the deploy every 5 minutes until it finishes": LOOP_TIMER_NONE,
    "grow to 5m users": LOOP_TIMER_NONE,
    "every user sees the loop": LOOP_TIMER_NONE,
    "run omh-cto-loop every 30 minutes on the roadmap": LOOP_TIMER_NONE,
    "my-ulw-loop every 5 minutes check CI": LOOP_TIMER_NONE,
    "cto loop every hour check the roadmap": LOOP_TIMER_NONE,
    "run the cto loop every 2 hours on the roadmap": LOOP_TIMER_NONE,
    # `/loop` takes hours, minutes, then seconds; `5ms` and `0m` are neither.
    "loop 5ms check the queue": LOOP_TIMER_NONE,
    "loop 0m check the queue": LOOP_TIMER_NONE,
    # A turn Hermes wrote is no request at all, and a host block that never
    # closes still holds what follows it.
    "[/loop wakeup #3, every 5m]\nRecurring task: check whether CI is green": LOOP_TIMER_NONE,
    '[Replying to: "is CI green?"\n\nloop every 5 minutes and check CI': LOOP_TIMER_NONE,
}

# Each names another skill, so that skill keeps the turn; the last two open
# with a loop command, which the router's explicit-invocation gate decides.
INVOKED_BESIDE_A_TIMED_LOOP = {
    "$code-review check the loop that retries every 5 seconds": "code-review",
    "ulw-plan: replace the polling loop that hits the API every 5 minutes with webhooks": "ralplan",
    "use the app-debugging skill: my polling loop fires every 2 seconds and leaks memory": "app-debugging",
    "ulw-qa: the carousel loop advances every 3 seconds, verify it on mobile": "ultraqa",
    "/code-review my for loop sleeps every 2 seconds, is that a race?": "code-review",
    "cto loop every hour check the roadmap": "cto-loop",
    "run the cto loop every 2 hours on the roadmap": "cto-loop",
    "loop every 5 minutes and run the code-review skill on new PRs": "code-review",
    "loop every hour and run the cto-loop skill on the roadmap": "cto-loop",
}

_DIRECT = "Answer directly in the current chat; do not open an OMH workflow unless the user asks for one."
_EXPLICIT_INVOCATION = "Explicit workflow invocation wins over heuristic routing."


def _hint_workflows(message: str) -> list[str]:
    return [str(hint.get("workflow")) for hint in awareness_route_hint(message, max_hints=4)["hints"]]


def _router_answer(message: str) -> str:
    """The loop-timer verdict the chat router acted on, read off its reason."""
    reason = route_chat_message(message, source="discord")["reason"]
    return {NATIVE_LOOP_CADENCE_REASON: LOOP_TIMER_NATIVE, LOOP_CONSTRUCT_REASON: LOOP_TIMER_CONSTRUCT}.get(reason, "")


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
            "[/plan — plan mode]\r\n\r\nFor this turn, you are in PLAN MODE — planning only.",
        ):
            with self.subTest(message=message[:30]):
                self.assertTrue(host_command_turn(message))

    def test_a_gateway_prefix_before_the_frame_is_the_hosts(self):
        for name, prefix in GATEWAY_PREFIXES.items():
            for turn_name, turn in (("plan", _PLAN), ("goal", _GOAL_TURN)):
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
            "[/initiate-setup]\nwhy is this here?",
            # The setup template's body opens with a frontmatter fence on its
            # own line, not with any text that starts with dashes.
            "[/initiate-setup]\n\n--- it hangs on the first card",
            "[/initiate-setup]\n\n----- separator -----\nset up memory and the toolbelt",
        ):
            with self.subTest(message=message):
                self.assertFalse(host_command_turn(message))
        for value in (None, 7, ["[/loop wakeup #1]"]):
            with self.subTest(value=value):
                self.assertFalse(host_command_turn(value))

    def test_a_host_block_that_never_closes_leaves_the_turn_a_persons(self):
        # No blank line closes the reply pointer, so the frame after it is
        # inside the pointer as far as anyone can tell.
        self.assertFalse(host_command_turn("[Replying to x] " + _GOAL_TURN))
        self.assertFalse(host_command_turn("[Replying to x] " + _PLAN))
        # A reply pointer whose quoted text itself holds `]` and a blank line is
        # cut at that point, and the turn reads as a person's: the documented
        # way this fails, which routes as it did before the gate existed.
        self.assertFalse(host_command_turn('[Replying to: "x ]\n\nfoo"]\n\n' + _PLAN))

    def test_a_heartbeat_frame_cannot_tell_who_typed_the_next_line(self):
        # The heartbeat's next line is the person's own recurring instruction,
        # so a person typing under a pasted frame reads as the host. Pinned so
        # the limit stays stated rather than discovered.
        self.assertTrue(host_command_turn("[Heartbeat — recurring instruction, fires every 10m]\nwhy do I see this?"))


class LoopTimerVerdictTest(unittest.TestCase):
    def test_each_kind_of_sentence_gets_its_verdict(self):
        for sentence, verdict in {**TIMED_LOOP_FORMS, **VERDICTS}.items():
            with self.subTest(sentence=sentence):
                self.assertEqual(loop_timer_verdict(sentence), verdict)

    def test_a_gateway_prefix_never_changes_the_verdict(self):
        # A Discord or Slack thread is a shared session by default, so every
        # message there carries a sender prefix, and a reply or a backfilled
        # channel adds the host's blocks before it.
        for prefix_name, prefix in GATEWAY_PREFIXES.items():
            for form, verdict in TIMED_LOOP_FORMS.items():
                with self.subTest(prefix=prefix_name, form=form):
                    self.assertEqual(loop_timer_verdict(prefix + form), verdict)

    def test_every_construct_phrase_is_a_construct_only_when_asked_for(self):
        for phrase in LOOP_CONSTRUCT_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    loop_timer_verdict(f"write a {phrase} that runs every 5 seconds"), LOOP_TIMER_CONSTRUCT
                )
                # Talked about, it is a thing, and nothing here decides the turn.
                self.assertEqual(loop_timer_verdict(f"my {phrase} stalls every 5 seconds, why?"), LOOP_TIMER_NONE)
                self.assertEqual(
                    loop_timer_verdict(f"loop every 5 minutes and check the {phrase}"), LOOP_TIMER_NATIVE
                )

    def test_the_two_answered_directly_are_the_native_loop_and_the_construct(self):
        self.assertEqual(LOOP_TIMERS_ANSWERED_DIRECTLY, {LOOP_TIMER_NATIVE, LOOP_TIMER_CONSTRUCT})


class OneVerdictOnEverySurfaceTest(unittest.TestCase):
    """The chat router, the loop guard, the hint and the candidate line say what the function says."""

    def test_the_router_and_the_hint_agree_with_the_verdict_on_the_whole_probe_corpus(self):
        # Every sentence four review rounds probed this change with. The
        # verdict is read on the turn's executable text; the router answers
        # directly exactly on its two direct verdicts -- or dispatches the
        # other skill the person invoked beside the loop command -- and the
        # hint withholds the goal loop exactly then.
        sentences = json.loads(PROBE_SENTENCES.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(sentences), 500)
        disagreements = []
        for sentence in sentences:
            verdict = loop_timer_verdict(executable_routing_text(sentence))
            direct = verdict if verdict in LOOP_TIMERS_ANSWERED_DIRECTLY else ""
            route = route_chat_message(sentence, source="discord")
            router = _router_answer(sentence)
            invoked_other = (
                bool(direct)
                and route["reason"] == _EXPLICIT_INVOCATION
                and route["selected_skill"] != "loop"
            )
            hint = awareness_route_hint(sentence).get(NATIVE_LOOP_CADENCE_HINT_KEY) is True
            if (router != direct and not invoked_other) or hint != bool(direct):
                disagreements.append((sentence, verdict, router, route["selected_skill"], hint))
        self.assertEqual(disagreements, [])

    def test_the_loop_guard_reads_the_querys_verdict_behind_every_gateway_prefix(self):
        # The scorer's normalized text joins every whitespace run, so the
        # blank lines that close a gateway block are gone from it; the guard
        # is handed the verdict read on the query's executable text instead.
        for prefix_name, prefix in GATEWAY_PREFIXES.items():
            for form, verdict in TIMED_LOOP_FORMS.items():
                with self.subTest(prefix=prefix_name, form=form):
                    query = RoutingQuery.from_message(prefix + form)
                    self.assertEqual(query.loop_timer, verdict)
                    untimed = active_routing_guard_rules(query.normalized, set(query.tokens))
                    guards = active_routing_guard_rules(
                        query.normalized, set(query.tokens), loop_timer=query.loop_timer
                    )
                    if verdict in LOOP_TIMERS_ANSWERED_DIRECTLY:
                        self.assertNotIn(LOOP_GOAL_GUARD, guards)
                        if form.startswith("loop "):
                            # The bare word alone would have raised the guard
                            # (`/loop` never reaches it: the scorer drops
                            # path-like fragments).
                            self.assertIn(LOOP_GOAL_GUARD, untimed)
                    else:
                        # The goal loop's guard reads exactly as it does without a timer.
                        self.assertEqual(guards, untimed)

    def test_the_router_answers_behind_every_gateway_prefix_as_the_verdict_says(self):
        for prefix_name, prefix in GATEWAY_PREFIXES.items():
            for form, verdict in TIMED_LOOP_FORMS.items():
                with self.subTest(prefix=prefix_name, form=form):
                    direct = verdict in LOOP_TIMERS_ANSWERED_DIRECTLY
                    self.assertEqual(_router_answer(prefix + form), verdict if direct else "")
                    hint = awareness_route_hint(prefix + form)
                    self.assertEqual(hint.get(NATIVE_LOOP_CADENCE_HINT_KEY) is True, direct)

    def test_a_native_loop_is_answered_with_the_native_command(self):
        for sentence, verdict in {**TIMED_LOOP_FORMS, **VERDICTS}.items():
            if verdict != LOOP_TIMER_NATIVE:
                continue
            with self.subTest(sentence=sentence):
                route = route_chat_message(sentence, source="discord")
                self.assertEqual((route["action"], route["reason"]), ("fallback", NATIVE_LOOP_CADENCE_REASON))
                self.assertEqual(route["clarification"], _DIRECT)
                self.assertNotIn("ulw-loop", _hint_workflows(sentence))
        self.assertIn("/loop 5m", NATIVE_LOOP_CADENCE_REASON)

    def test_the_wrapper_keeps_a_native_loop_answer_over_its_omh_cards(self):
        # The prompt asks about OMH, but the request is the loop: before, the
        # chat wrapper replaced the router's answer with OMH's intro,
        # quickstart, status card, or the workflow picker. One sentence per card.
        for sentence in (
            "5분마다 반복해서 omh 소개해줘",
            "/loop 5m omh quickstart",
            "/loop 10m ask omh loop for status",
            "/loop 5m what omh skills are there",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(loop_timer_verdict(sentence), LOOP_TIMER_NATIVE)
                payload = build_chat_interaction_payload(sentence, source="discord")
                self.assertEqual(
                    (payload["route"]["action"], payload["route"]["reason"], payload["next_action"]),
                    ("fallback", NATIVE_LOOP_CADENCE_REASON, "answer_directly"),
                )

    def test_a_loop_construct_is_answered_without_loop_advice(self):
        for sentence, verdict in VERDICTS.items():
            if verdict != LOOP_TIMER_CONSTRUCT:
                continue
            with self.subTest(sentence=sentence):
                route = route_chat_message(sentence, source="discord")
                self.assertEqual((route["action"], route["reason"]), ("fallback", LOOP_CONSTRUCT_REASON))
                self.assertEqual(route["clarification"], _DIRECT)
        self.assertNotIn("/loop", LOOP_CONSTRUCT_REASON)

    def test_the_goal_loop_on_a_timer_still_opens(self):
        # Measured to dispatch the goal loop on 8616ce7d0; a timer beside OMH's
        # own forms, the skill's name, a goal-loop name, or a goal must not
        # take that away, whether the verdict reads them as the goal loop or
        # finds no loop command at the opening at all.
        for sentence in (
            "start a goal loop that iterates every 2 hours until the onboarding drop-off is under 20%",
            "goal loop every hour until onboarding drop-off is under 20%",
            "start the goal loop every 30 minutes",
            "1시간마다 온보딩을 계속 개선하는 루프 돌려줘, 첫 실행 성공률 90% 될 때까지",
            "use the ulw-loop skill every 2 hours to check whether CI is green",
            "ulw-loop: every 5 minutes check CI",
            "run ulw-loop every hour on the onboarding docs",
            "/ulw-loop every 5m check CI",
            "[ulw-loop] every 5 minutes check CI",
            "./loop every 5 minutes check whether CI is green",
            "run a loop every 30 minutes to improve first-run experience until install friction is lower",
            "loop every hour and improve the docs",
            "use the ulw-loop skill every 2 hours to improve the README",
            "use the ulw-loop skill every 2 hours to fix src/app.py",
            "please use the ulw-loop skill every 2 hours on the README",
            "use the omh-loop skill every 2 hours to improve the README",
            "use the loop skill every 2 hours to improve the README",
            "use the loop skill every 2 hours to check whether CI is green",
            "ulw-loop 스킬로 1시간마다 README 개선해줘",
            # Invoked by its leading name: the display-name fold leaves the
            # catalog key in place, which that invocation needs to resolve.
            "ulw-loop, every 2 hours, look at README.md",
            "[alice] $loop every hour check CI",
            "[alice] omh loop every 30 minutes check CI",
            _REPLY + "[alice] omh loop every 30 minutes check CI",
            _BACKFILL + "[alice] omh loop every 2 hours improve the docs",
            _NOTE + "[alice] omh 루프 30분마다 문서 개선",
            # An outcome goal on a product or star measure.
            "loop every 30 minutes to lower install friction",
            "loop every hour to get us to 10k stars",
            "loop every hour and enhance the docs",
            "2시간마다 루프 돌려서 설치 후 첫 성공까지 걸리는 시간을 줄여줘",
            "every 30 minutes run a loop that reduces friction in the product onboarding",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(
                    loop_timer_verdict(executable_routing_text(sentence)), (LOOP_TIMER_GOAL, LOOP_TIMER_NONE)
                )
                route = route_chat_message(sentence, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "loop"))

    def test_the_skills_name_after_a_cadence_opens_the_goal_loop(self):
        # A declared change from 8616ce7d0, where the name folded to the bare
        # word and the rest of the sentence won (app-debugging,
        # build-failure-triage). The person named the skill.
        for sentence in (
            "every 2 hours ulw-loop the flaky test list",
            "every hour ulw-loop the build failures on main",
            "every 2 hours omh-loop the flaky test list",
            "every hour ulw loop the build failures on main",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(loop_timer_verdict(sentence), LOOP_TIMER_GOAL)
                route = route_chat_message(sentence, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "loop"))

    def test_a_skill_invoked_beside_a_timed_loop_keeps_the_turn(self):
        for sentence, skill in INVOKED_BESIDE_A_TIMED_LOOP.items():
            with self.subTest(sentence=sentence):
                route = route_chat_message(sentence, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", skill))
        # The last two open with the loop command, so the hint still withholds
        # the goal loop; the router's gate is what hands the turn on.
        for sentence in list(INVOKED_BESIDE_A_TIMED_LOOP)[-2:]:
            with self.subTest(sentence=sentence):
                self.assertEqual(loop_timer_verdict(sentence), LOOP_TIMER_NATIVE)
                self.assertEqual(route_chat_message(sentence, source="discord")["reason"], _EXPLICIT_INVOCATION)
                self.assertIs(awareness_route_hint(sentence).get(NATIVE_LOOP_CADENCE_HINT_KEY), True)

    def test_the_scorer_ranks_a_native_loop_without_the_goal_loop_guard(self):
        # `omh recommend` and every other reader of the ranking: the guard's
        # boost follows the verdict the query carries.
        from omh.routing.recommend import recommend_skills

        def loop_evidence(sentence: str) -> list[str]:
            return next(
                (list(row["matched"]) for row in recommend_skills(sentence, limit=200) if row["skill"] == "loop"), []
            )

        self.assertNotIn("guard:loop_goal", loop_evidence("loop every 5 minutes and check whether CI is green"))
        # The guard's own goal words do not bring it back under a typed `/loop`:
        # the person typed Hermes' command.
        self.assertNotIn(
            "guard:loop_goal", loop_evidence("[alice] /loop 30m keep improving the docs until the repo hits 10k stars")
        )
        self.assertIn("guard:loop_goal", loop_evidence("loop every hour and improve the docs"))
        self.assertIn("guard:loop_goal", loop_evidence("loop until the tests pass"))

    def test_another_skills_name_that_ends_in_loop_keeps_its_route(self):
        route = route_chat_message("run omh-cto-loop every 30 minutes on the roadmap", source="discord")
        self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "cto-loop"))

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
            "run ulw-loop every hour on the onboarding docs",
            "/loop fix the flaky site-two integration test",
        ):
            with self.subTest(message=message):
                self.assertIn("ulw-loop", _hint_workflows(message))
                self.assertNotIn(NATIVE_LOOP_CADENCE_HINT_KEY, awareness_route_hint(message))

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


class HostTurnRouterTest(unittest.TestCase):
    def _route(self, message: str) -> dict[str, object]:
        return route_chat_message(message, source="discord")

    def test_host_command_turns_are_answered_directly(self):
        for name, (opening, body) in HOST_TURNS.items():
            with self.subTest(turn=name):
                route = self._route(opening + body)
                self.assertEqual(route["action"], "fallback")
                self.assertEqual(route["reason"], HOST_COMMAND_TURN_REASON)
                self.assertEqual(route["clarification"], _DIRECT)
                # The wrapper keeps the router's answer: a goal contract's
                # verification sentence once read there as a catalog question
                # and opened the workflow picker.
                payload = build_chat_interaction_payload(opening + body, source="discord")
                self.assertEqual((payload["route"]["action"], payload["next_action"]), ("fallback", "answer_directly"))

    def test_a_gateway_prefixed_host_turn_is_answered_directly(self):
        for prefix_name, prefix in GATEWAY_PREFIXES.items():
            for turn in (_PLAN, _GOAL_TURN):
                with self.subTest(prefix=prefix_name, turn=turn[:20]):
                    self.assertEqual(self._route(prefix + turn)["reason"], HOST_COMMAND_TURN_REASON)

    def test_the_same_bodies_without_their_frame_still_route(self):
        # Measured on the bodies above: without the frame each of these
        # dispatches a workflow, so the frame is what the router reads.
        for name in ("plan", "goal continuation", "goal gate failed", "kanban continuation", "heartbeat", "initiate setup"):
            with self.subTest(turn=name):
                self.assertEqual(self._route(HOST_TURNS[name][1])["action"], "dispatch")


if __name__ == "__main__":  # pragma: no cover - module entry point
    unittest.main()
