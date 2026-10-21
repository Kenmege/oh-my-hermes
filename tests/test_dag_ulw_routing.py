"""Reaching `dag-ulw`: by its two-word name the way people type it, and by intent for big parallel work.

The corpus cases in `quality/routing_precision.py` hold the routed outcome;
these pin the two readings it rests on (`dag_ulw_cues.invokes_dag_ulw` and
the router's heavy-lane size check) and that the bare words stay where they
were: `dag` with data pipelines, `ulw` / `ultrawork` with `ultrawork`.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.dag_ulw_cues import declines_dag_ulw, invokes_dag_ulw  # noqa: E402
from omh.routing.chat import _ultrawork_sized, route_chat_message  # noqa: E402
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

    def test_size_evidence_reads_parallel_and_breadth_words(self) -> None:
        self.assertTrue(_ultrawork_sized("implement the export feature in parallel across the api and ui modules"))
        self.assertTrue(_ultrawork_sized("결제 모듈과 알림 모듈을 병렬로 고쳐줘"))
        self.assertTrue(_ultrawork_sized("migrate every module from requests to httpx"))
        self.assertFalse(_ultrawork_sized("fix the flaky checkout test"))
        self.assertFalse(_ultrawork_sized("README 오타 고쳐줘"))
        # Ultrawork's own capabilities stay with it even when the words are parallel.
        self.assertFalse(_ultrawork_sized("run three coordinated workers in parallel on one shared task list"))
        self.assertFalse(_ultrawork_sized("research, plan and implement this change in parallel in one cycle"))

    def test_small_coding_work_stays_on_ultrawork(self) -> None:
        for message in ("rename the helper function in the parser module", "fix the flaky checkout test",
                        "build a login component"):
            with self.subTest(message=message):
                self.assertEqual(route_chat_message(message)["selected_skill"], "ultrawork")

    def test_big_parallel_work_reaches_dag_ulw_without_its_name(self) -> None:
        route = route_chat_message("implement the export feature in parallel across the api and ui modules, then integrate")
        self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "dag-ulw"))


if __name__ == "__main__":
    unittest.main()
