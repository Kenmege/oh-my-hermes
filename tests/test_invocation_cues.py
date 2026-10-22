"""After an explicit invocation the default is to run the skill; a stop, a run-status request or a question about the
skill is answered instead (`invocation_cues.invoked_instruction`).

The corpus cases in `quality/routing_precision.py` hold the routed outcome of a few; these pin the reading for every
form, typed and as Hermes sends a slash skill, and that the router, the wrapper and Hermes' own route hint read it
alike.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import awareness_route_hint  # noqa: E402
from omh.plugin_bundle.omh.hermes_session_commands import person_request_text  # noqa: E402
from omh.plugin_bundle.omh.invocation_cues import META, STATUS, STOP, invoked_instruction  # noqa: E402
from omh.quality.routing_precision import _slash_skill_turn  # noqa: E402
from omh.routing.chat import route_chat_message  # noqa: E402
from omh.wrapper.contract import build_chat_interaction_payload  # noqa: E402

# A stop that names no task object, in each language.
STOPS = (
    "/ulw-work stop", "/ulw-loop stop", "/ulw-interview stop", "/omh-plan stop", "/omh-code-review stop",
    "/omh-app-debugging stop", "/ulw-work cancel", "/ulw-work 그만", "/ulw-work 취소해줘", "/ulw-work 이거 취소",
    "$ulw-work stop", "/ralplan 멈춰", "/ulw-work 停止", "/ulw-plan 止めて", "/omh-plan 取消",
)
# A question about the skill itself.
META_QUESTIONS = (
    "/ulw-plan what does this do?", "/ulw-plan what is it?", "/ulw-plan 이게 뭐야?", "/ulw-plan 어떻게 써?",
    "/ulw-plan 何これ", "/omh-code-review 这是什么", "/omh-code-review what is it?", "/omh-app-debugging what does this do?",
)
# How the run stands, after a skill whose work is not that status.
STATUS_REQUESTS = (
    "/ulw-work status", "/ulw-work show status", "/ulw-work show me the run", "/ulw-loop 상태 보여줘",
    "/omh-code-review 진행상황 알려줘", "/ulw-work 뭐 남았어?", "/ultrawork still running?",
    "/omh-app-debugging is it running?", "/omh-app-debugging 끝났어?", "/ulw-plan 状態", "/omh-plan 状态",
)
# Everything else runs the skill: a stop verb with a task object, a question with one, and a status request to a
# skill whose own work that status is.
REQUESTS = (
    ("/ulw-work kill the zombie processes", "ultrawork"), ("/omh-frontend stop the modal from flickering", "frontend"),
    ("/omh-git-workflow abort the rebase", "git-workflow"), ("/ulw-work 중복 결제 멈추게 해줘", "ultrawork"),
    ("/ulw-loop 로그 폭주 멈춰줘", "loop"), ("/ulw-work stop the flaky retry from hiding failures", "ultrawork"),
    ("/omh-verification-gate is it done?", "verification-gate"), ("/omh-code-review is it safe to merge?", "code-review"),
    ("/ulw-plan how should we migrate billing?", "ralplan"), ("/research is rust faster than go?", "research"),
    ("/omh-app-debugging why did the run stop?", "app-debugging"), ("/omh-app-debugging 왜 멈췄어?", "app-debugging"),
    ("/ulw-plan plan the billing migration", "ralplan"), ("/omh-code-review this PR", "code-review"),
    ("/omh-running-work-board status", "running-work-board"),
    ("/omh-running-work-board what's running?", "running-work-board"),
    ("/omh-deploy-and-monitor status", "deploy-and-monitor"),
    ("/omh-ops-observability-card status", "ops-observability-card"),
)


def _forms(message: str) -> tuple[tuple[str, str], ...]:
    """The typed message and, for a skill Hermes lists under its label (`ulw-work`, `omh-plan`), its scaffold."""
    name, rest = message.split(maxsplit=1)
    if not name.startswith(("/ulw-", "/omh-")):
        return (("typed", message),)
    return ("typed", message), ("scaffold", _slash_skill_turn(name.lstrip("/"), rest))


class ReadingTests(unittest.TestCase):
    def test_the_instruction_reads_as_stop_status_meta_or_work(self) -> None:
        self.assertEqual(invoked_instruction("ultrawork", "stop"), STOP)
        self.assertEqual(invoked_instruction("ultrawork", "이거 취소"), STOP)
        self.assertEqual(invoked_instruction("ultrawork", "kill the zombie processes"), "")
        self.assertEqual(invoked_instruction("ultrawork", "status"), STATUS)
        self.assertEqual(invoked_instruction("ralplan", "何これ"), META)
        self.assertEqual(invoked_instruction("code-review", "is it safe to merge?"), "")
        # A question about dag-ulw is about a skill; a report on it is work for the skill invoked.
        self.assertEqual(invoked_instruction("loop", "dag-ulw가 뭐야?"), META)
        self.assertEqual(invoked_instruction("ultrawork", "dag-ulw is broken"), "")
        # A status request is the work of a skill whose work is that status.
        self.assertEqual(invoked_instruction("running-work-board", "status"), "")
        self.assertEqual(invoked_instruction("ops-observability-card", "status"), "")
        self.assertEqual(invoked_instruction("app-debugging", "is it running?"), STATUS)


class RouteTests(unittest.TestCase):
    def test_a_stop_or_a_question_about_the_skill_is_answered(self) -> None:
        for message in STOPS + META_QUESTIONS:
            for form, text in _forms(message):
                with self.subTest(message=message, form=form):
                    if form == "scaffold":
                        self.assertEqual(person_request_text(text), message)
                    self.assertNotEqual(route_chat_message(text, source="discord")["action"], "dispatch")
                    self.assertEqual(awareness_route_hint(text)["hints"], [])
                    interaction = build_chat_interaction_payload(text, source="discord")
                    self.assertEqual(interaction["next_action"], "answer_directly")

    def test_a_status_request_shows_the_status_card(self) -> None:
        for message in STATUS_REQUESTS:
            for form, text in _forms(message):
                with self.subTest(message=message, form=form):
                    route = route_chat_message(text, source="discord")
                    self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "oh-my-hermes"))
                    self.assertEqual(awareness_route_hint(text)["hints"], [])
                    interaction = build_chat_interaction_payload(text, source="discord")
                    self.assertEqual((interaction["mode"], interaction["next_action"]), ("status", "show_status"))

    def test_any_other_instruction_runs_the_skill(self) -> None:
        for message, skill in REQUESTS:
            for form, text in _forms(message):
                with self.subTest(message=message, form=form):
                    route = route_chat_message(text, source="discord")
                    self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", skill))


if __name__ == "__main__":
    unittest.main()
