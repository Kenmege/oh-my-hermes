"""Reaching `dag-ulw`: by its two-word name the way people type it, and by intent for big parallel work.

The corpus cases in `quality/routing_precision.py` hold the routed outcome;
these pin the readings it rests on (`dag_ulw_cues`: the name, a question
about it, and big work by size), that the router and Hermes' own route hint
read them alike, and that the bare words stay where they were: `dag` with
data pipelines, `ulw` / `ultrawork` with `ultrawork`.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import awareness_route_hint, awareness_route_hint_context_from_payload  # noqa: E402
from omh.plugin_bundle.omh.dag_ulw_cues import (  # noqa: E402
    asks_about_dag_ulw,
    big_work_for_dag_ulw,
    declines_dag_ulw,
    invokes_dag_ulw,
)
from omh.routing.chat import route_chat_message  # noqa: E402
from omh.routing.policy import skill_is_negated  # noqa: E402

INVOKES = (
    "dag ulw add the export API and its docs",
    "dag ulw해줘 결제 모듈이랑 알림 모듈",
    "dag ulw로 해줘",
    "dag ulw 해주세요",
    "결제 모듈 dag ulw로 고쳐줘",
    "ulw dag split the auth refactor",
    "/dag-ulw build the search feature",
    "$dag-ulw refactor the parser",
    "./dag-ulw 해줘",
    "use dag ulw for the release checklist",
    "please dag ulw the migration",
    "refactor the billing modules with dag ulw",
    "Dag-ULW 해주세요",
    "dag ultrawork the onboarding flow",
)
DOES_NOT_INVOKE = (
    "don't use dag ulw for this, just fix the typo",
    "dag ulw 말고 그냥 고쳐줘",
    "dag ulw는 쓰지 마",
    "not dag ulw, just answer",
    "the airflow dag failed last night",
    "open the dag file",
    "is dag ulw any good?",
    "어제 돌린 dag ulw 결과가 어떻게 됐어?",
    # Questions about the skill, answered as a question about any other skill is.
    "dag ulw는 뭐야?",
    "dag ulw 설명해줘",
    "dag ulw 결과 알려줘",
    "dag ulw 문서 보여줘",
    "dag ulw 어떻게 써?",
    "dag ulw 쓰면 비용이 얼마나 나와?",
    "dag ulw is broken, it never settles the verify node",
    "how does dag-ulw handle repairs?",
    "tell me about dag-ulw",
    "ulw dagger",
    "ulw로 다 고쳐줘",
    "ultrawork the auth refactor",
)


class InvocationTests(unittest.TestCase):
    def test_the_name_in_the_shapes_people_type_invokes_it(self) -> None:
        for message in INVOKES:
            with self.subTest(message=message):
                self.assertTrue(invokes_dag_ulw(message))

    def test_a_declined_bare_or_merely_mentioned_name_does_not(self) -> None:
        for message in DOES_NOT_INVOKE:
            with self.subTest(message=message):
                self.assertFalse(invokes_dag_ulw(message))

    def test_a_declined_name_leaves_the_candidate_list(self) -> None:
        self.assertTrue(declines_dag_ulw("don't use dag ulw for this"))
        self.assertTrue(skill_is_negated("dag ulw 말고 그냥 고쳐줘", "dag-ulw"))
        self.assertFalse(skill_is_negated("dag ulw add the API", "dag-ulw"))


class RouterTests(unittest.TestCase):
    def test_the_explicit_forms_dispatch_dag_ulw(self) -> None:
        for message in ("dag ulw해줘 결제 모듈이랑 알림 모듈 같이 리팩터링해", "ulw dag split the auth refactor across the service",
                        "refactor the billing and invoicing modules with dag ulw"):
            with self.subTest(message=message):
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "dag-ulw"))

    def test_bare_words_keep_their_owners(self) -> None:
        self.assertEqual(route_chat_message("ultrawork the auth refactor across the service")["selected_skill"],
                         "ultrawork")
        self.assertEqual(route_chat_message("$ulw-work fix the build")["selected_skill"], "ultrawork")
        for message in ("the airflow dag failed last night, check the scheduler logs", "open the dag file"):
            with self.subTest(message=message):
                route = route_chat_message(message)
                self.assertNotEqual(route["candidate_skill"], "dag-ulw")

    def test_size_evidence_reads_how_the_work_splits_or_how_wide_it_reaches(self) -> None:
        for message in ("implement the export feature in parallel across the api and ui modules",
                        "api, ui, docs 작업을 병렬로 나눠서 만들어줘", "migrate every module from requests to httpx",
                        "fan out across the services, then integrate"):
            with self.subTest(message=message):
                self.assertTrue(big_work_for_dag_ulw(message))
        for message in ("fix the flaky checkout test", "README 오타 고쳐줘",
                        # A concurrency word or a second step that names the subject, not the work's shape.
                        "fix the bug where uploads run in parallel and corrupt the file", "병렬 처리 버그 고쳐줘",
                        "동시에 두 요청이 오면 캐시가 깨지는 버그 고쳐줘", "병렬로 진행되는 작업이 데드락 걸리는 버그 고쳐줘",
                        "the parallel work queue drops jobs, fix it", "the fan out of this function is too high, fix it",
                        "add the field to the form, then integrate it with the api",
                        # Ultrawork named anywhere stays ultrawork's.
                        "fix the auth and billing modules in parallel with ulw"):
            with self.subTest(message=message):
                self.assertFalse(big_work_for_dag_ulw(message))

    def test_ultrawork_capabilities_answer_before_the_size_reading(self) -> None:
        # The retired-engine alias route answers coordinated workers before scoring, lanes phrase or not.
        route = route_chat_message("run three coordinated workers on one shared task list in parallel lanes")
        self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "ultrawork"))

    def test_a_question_about_dag_ulw_is_answered_whichever_skill_scored(self) -> None:
        for message in ("dag ulw는 뭐야?", "dag ulw 설명해줘", "how does dag-ulw handle repairs?", "tell me about dag-ulw",
                        "dag ulw 결과 알려줘", "dag ulw 쓰면 비용이 얼마나 나와?", "dag ulw 문서 보여줘", "dag ulw 어떻게 써?",
                        "dag ulw is broken, it never settles the verify node"):
            with self.subTest(message=message):
                self.assertTrue(asks_about_dag_ulw(message))
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("fallback", "oh-my-hermes"))
                self.assertFalse([hint for hint in awareness_route_hint(message)["hints"]
                                  if hint.get("workflow") == "dag-ulw"])
        self.assertFalse(asks_about_dag_ulw("dag ulw add the export API and its docs"))
        self.assertFalse(asks_about_dag_ulw("can you dag ulw the migration?"))

    def test_small_coding_work_stays_on_ultrawork(self) -> None:
        for message in ("rename the helper function in the parser module", "fix the flaky checkout test",
                        "build a login component"):
            with self.subTest(message=message):
                self.assertEqual(route_chat_message(message)["selected_skill"], "ultrawork")

    def test_inside_hermes_the_route_hint_reaches_dag_ulw_the_same_way(self) -> None:
        # The review's three sentences: big work by intent reaches dag-ulw in Hermes' own per-turn hint too.
        for message in ("implement the export feature in parallel across the api and ui modules, then integrate",
                        "split this into parallel work lanes with disjoint ownership",
                        "do the api and the ui in parallel then integrate"):
            with self.subTest(message=message):
                hints = awareness_route_hint(message)["hints"]
                self.assertEqual((hints[0]["workflow"], hints[0]["next_action"]), ("dag-ulw", "start_dag_run"))
                self.assertEqual(route_chat_message(message, source="discord")["selected_skill"], "dag-ulw")
        for message in ("fix the bug where uploads run in parallel and corrupt the file", "list all modules that import requests",
                        "the fan out of this function is too high, fix it", "동시에 두 요청이 오면 캐시가 깨지는 버그 고쳐줘"):
            with self.subTest(message=message):
                self.assertFalse([hint for hint in awareness_route_hint(message)["hints"]
                                  if hint.get("workflow") == "dag-ulw"])

    def test_a_dag_ulw_hint_asks_the_first_response_to_start_at_once(self) -> None:
        # Its lane's card says to plan first and ask; dag-ulw starts at once (owner decision D1), by name or by size.
        for message in ("dag ulw add the export API, its UI button and docs, then verify with the test suite",
                        "split this into parallel work lanes with disjoint ownership"):
            with self.subTest(message=message):
                context = awareness_route_hint_context_from_payload(awareness_route_hint(message))
                self.assertIn("first_response_shape=Start at once: plan the graph and call omh_dag define", context)

    def test_big_parallel_work_reaches_dag_ulw_without_its_name(self) -> None:
        route = route_chat_message("implement the export feature in parallel across the api and ui modules, then integrate")
        self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "dag-ulw"))


if __name__ == "__main__":
    unittest.main()
