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
    dag_ulw_control,
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
    # A request put politely as a question.
    "can you dag ulw the migration?",
    "could you please dag ulw the billing refactor",
    "could you run dag ulw on the billing refactor?",
    # An `or` between two targets weighs nothing.
    "can you dag ulw the auth or billing refactor?",
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
    "can you explain dag ulw?",
    "could you describe dag ulw",
    "can you not dag ulw this?",
    # A polite question that weighs the run against another way, or asks what you would do.
    "can you dag ulw this or should I just fix it myself?",
    "would you dag ulw, or just ultrawork, the auth refactor?",
    "could you dag ulw this, or is that overkill?",
    "would you dag ulw this or not?",
    "can you dag ulw the migration? or is that overkill",
    "would you dag ulw it, if you were me?",
    "can you dag ulw the migration 아니면 그냥 ultrawork로 할까?",
    # Asking to stop using it declines it.
    "can you stop using dag ulw for small fixes?",
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
                        # A fault two named parts cause at once, with one fix asked for.
                        "api 모듈과 ui 모듈이 동시에 요청하면 캐시가 깨져, 고쳐줘",
                        "the payment and order services crashed when called concurrently, fix it",
                        # A remark on a measure, and a small text task.
                        "the fan out across the services is too high", "our split into parallel lanes was too coarse",
                        "split the paragraph into parallel parts for the translation", "이 문단을 병렬로 나눠서 번역해줘",
                        # A question or an explain request about how work splits.
                        "explain how to split the work into parallel lanes", "how do I split the work into parallel lanes?",
                        "what does it mean to fan out across services", "병렬로 나눠서 하는 방법 설명해줘",
                        "병렬로 나눠서 하는 방법 알려줘", "병렬로 나눠서 작업하는 방법이 뭐야",
                        "show me how to split the work into parallel lanes", "is it possible to split the work into parallel lanes",
                        # Only the question mark says it: a question, not a request put as one.
                        "should we split the api, ui and docs work into parallel lanes?",
                        "i'd like to know how to split the work into parallel lanes",
                        "why not just explain how to split the work into parallel lanes",
                        # Talking it over, or a split weighed against another way.
                        "can we talk about how to split the work into parallel lanes?",
                        "could we split the work into parallel lanes, or is it overkill?",
                        # A condition clause, not a request, after the concurrency word.
                        "api 모듈과 ui 모듈이 동시에 처리해서 캐시가 깨져, 고쳐줘",
                        "api 모듈과 ui 모듈이 동시에 진행해서 데드락이 걸려 고쳐줘",
                        # Ultrawork named anywhere stays ultrawork's.
                        "fix the auth and billing modules in parallel with ulw"):
            with self.subTest(message=message):
                self.assertFalse(big_work_for_dag_ulw(message))
        for message in ("api 모듈과 ui 모듈 테스트가 실패하는데 병렬로 고쳐줘", "the migration is big, split it into parallel lanes",
                        "can you split the api, ui and docs work into parallel lanes?",
                        "split the paragraphs of the manual into parallel parts for translation",
                        "api, ui, docs 작업을 병렬로 나눠서 구현하고 변경 내용 설명해줘",
                        # Fixes said to run in parallel a word or two on, or the work said to run at once.
                        "api 모듈과 ui 모듈 테스트가 깨졌어, 병렬로 각각 고쳐줘",
                        "api 모듈과 ui 모듈 작업을 동시에 진행해줘, 실패하는 테스트도 고쳐줘",
                        # Openers that propose the work, requests put as questions, and an explain clause after the work.
                        "how about we split the api, ui and docs work into parallel lanes",
                        "why don't we split the api, ui and docs work into parallel lanes",
                        "what we need is to split the api, ui and docs work into parallel lanes",
                        "tell me when done: split the api, ui and docs work into parallel lanes",
                        "can we split the api, ui and docs work into parallel lanes?",
                        "split the api, ui and docs work into parallel lanes, ok?",
                        "api, ui, docs 작업을 병렬로 나눠서 구현해줄 수 있어?",
                        "api, ui, docs 작업을 병렬로 나눠서 구현하고 나눈 방법 설명해줘",
                        # A paragraph or sentence that is not what the split's object is.
                        "split the api, ui and docs work into parallel lanes and fix the paragraph in the README",
                        "split the paragraph parser and the sentence splitter work into parallel lanes",
                        "api, ui, docs 작업을 병렬로 나눠서 구현하고 README 한 문단 추가해줘",
                        "what i want is to split the api, ui and docs work into parallel lanes",
                        "그냥 병렬로 나눠서 하지 뭐", "api, ui, docs 작업을 병렬로 나눠서 진행해주고 다 됐는지 알려줘",
                        "api, ui, docs 작업을 병렬로 나눠서 구현하면 어때?"):
            with self.subTest(message=message):
                # Fixes said to run in parallel, a split asked for after a remark, a polite request, and many paragraphs.
                self.assertTrue(big_work_for_dag_ulw(message))

    def test_ultrawork_capabilities_answer_before_the_size_reading(self) -> None:
        # The retired-engine alias route answers coordinated workers before scoring, lanes phrase or not.
        route = route_chat_message("run three coordinated workers on one shared task list in parallel lanes")
        self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "ultrawork"))

    def test_a_question_about_dag_ulw_is_answered_whichever_skill_scored(self) -> None:
        for message in ("dag ulw는 뭐야?", "dag ulw 설명해줘", "how does dag-ulw handle repairs?", "tell me about dag-ulw",
                        "dag ulw 결과 알려줘", "dag ulw 쓰면 비용이 얼마나 나와?", "dag ulw 문서 보여줘", "dag ulw 어떻게 써?",
                        "dag ulw is broken, it never settles the verify node", "can you explain dag ulw?",
                        "can you dag ulw this or should I just fix it myself?", "would you dag ulw it, if you were me?",
                        "can you dag ulw the migration 아니면 그냥 ultrawork로 할까?"):
            with self.subTest(message=message):
                self.assertTrue(asks_about_dag_ulw(message))
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("fallback", "oh-my-hermes"))
                self.assertFalse([hint for hint in awareness_route_hint(message)["hints"]
                                  if hint.get("workflow") == "dag-ulw"])
        for message in ("dag ulw는 취소 어떻게 해?", "dag ulw 취소할 수 있어?", "dag ulw 취소 어떻게 해", "dag ulw 취소 방법 알려줘",
                        "dag ulw 취소할 수 있어", "dag ulw 취소 가능해", "dag ulw 취소 뭐야", "dag ulw 취소 왜 했어",
                        "dag ulw 취소해줘 왜 안 돼", "dag ulw 멈출 수 있어", "dag ulw 중단하면 어떻게 돼"):
            with self.subTest(message=message):
                # Asking how to cancel, or whether one can, is a question about the skill, not a stop or a run.
                self.assertEqual(dag_ulw_control(message), "")
                self.assertTrue(asks_about_dag_ulw(message))
                self.assertFalse(invokes_dag_ulw(message))
                self.assertEqual(route_chat_message(message, source="discord")["action"], "fallback")
                self.assertFalse(awareness_route_hint(message)["hints"])
        self.assertFalse(asks_about_dag_ulw("dag ulw add the export API and its docs"))
        self.assertFalse(asks_about_dag_ulw("can you dag ulw the migration?"))

    def test_a_polite_request_runs_it_on_both_paths(self) -> None:
        for message in ("can you dag ulw the migration?", "could you please dag ulw the billing refactor"):
            with self.subTest(message=message):
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "dag-ulw"))
                hints = awareness_route_hint(message)["hints"]
                self.assertEqual((hints[0]["workflow"], hints[0]["next_action"]), ("dag-ulw", "start_dag_run"))

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
                        "the fan out of this function is too high, fix it", "동시에 두 요청이 오면 캐시가 깨지는 버그 고쳐줘",
                        "the fan out across the services is too high", "split the paragraph into parallel parts for the translation",
                        "what does it mean to fan out across services", "api 모듈과 ui 모듈이 동시에 요청하면 캐시가 깨져, 고쳐줘"):
            with self.subTest(message=message):
                self.assertFalse([hint for hint in awareness_route_hint(message)["hints"]
                                  if hint.get("workflow") == "dag-ulw"])

    def test_a_stop_or_status_word_for_the_run_starts_nothing_and_points_at_the_run(self) -> None:
        # The delta review's sentences: a person stopping or checking a paid run must not be steered into another.
        cancel = ("dag ulw stop", "dag ulw cancel the run", "dag ulw 멈춰", "dag ulw 그만해", "dag ulw 취소",
                  "dag ulw 중지해줘", "dag ulw 돌린 거 취소해줘", "/dag-ulw stop", "cancel dag ulw",
                  # A verb ending after any words, or a word for the run before the stop noun (owner decision O7).
                  "dag ulw 주문 취소해줘", "dag ulw 주문취소해줘", "dag ulw 작업 취소", "dag ulw 실행 중단", "dag ulw 런 취소",
                  "dag ulw 지금 거 취소", "dag ulw 지금 돌리는 거 취소", "dag ulw의 실행 취소",
                  # Polite and casual verb endings, and 그만 돌려.
                  "dag ulw 취소하세요", "dag ulw 취소해요", "dag ulw 취소할게", "dag ulw 취소합니다", "dag ulw 취소 부탁해",
                  "dag ulw 취소 부탁드려요", "dag ulw 취소 좀 해줘", "dag ulw 취소요", "dag ulw 취소좀", "dag ulw 취소 ㄱ",
                  "dag ulw 주문 취소하세요", "dag ulw 그거 취소해요", "dag ulw 그만 돌려", "dag ulw 멈춰요", "dag ulw 그만하세요",
                  # A modified word for the run, or a stop adverb.
                  "dag ulw 진행 중인 작업 취소", "dag ulw 현재 작업 취소", "dag ulw 이번 실행 취소", "dag ulw 방금 거 취소",
                  "dag ulw 아까 그거 취소", "dag ulw 모든 작업 취소", "dag ulw 전체 취소", "dag ulw 바로 취소", "dag ulw 즉시 중단",
                  "dag ulw 지금 진행 중인 작업 바로 취소해줘", "dag ulw 지금 바로 취소", "dag ulw 그것도 취소",
                  "dag ulw 이전 거 취소", "dag ulw 취소요",
                  # The commonest polite ending and other plain stops, and stop words beyond 취소/중지/중단.
                  "dag ulw 취소해줘요", "dag ulw 중단해줘요", "dag ulw 멈춰줘요", "dag ulw 그만해줘요", "dag ulw 작업 취소해줘요",
                  "dag ulw 주문 취소해줘요", "dag ulw 취소시켜줘요", "dag ulw 취소해줄래요", "dag ulw 그만둬요", "dag ulw 그만 돌리자",
                  "dag ulw 멈춰라", "dag ulw 취소해봐", "dag ulw 취소 ㄱㄱ", "dag ulw 취소하고 싶어", "dag ulw 취소해야 해",
                  "dag ulw 정지해줘", "dag ulw 종료해줘", "dag ulw 스톱", "dag ulw 꺼줘", "dag ulw 취소해줄 수 있어?",
                  "dag ulw 취소해 주시겠어요?", "dag ulw 취소해주실래요",
                  # A polite opener or a trailing please before an English stop word.
                  "can you stop dag ulw", "could you cancel the dag ulw run", "can you please cancel dag ulw?",
                  "would you stop the dag ulw run now", "stop dag ulw please")
        status = ("dag ulw status", "dag ulw 상태 보여줘", "dag ulw 진행 상황", "dag ulw 로그 보여줘")
        for message, action in [*((m, "cancel") for m in cancel), *((m, "show_status") for m in status)]:
            with self.subTest(message=message):
                self.assertFalse(invokes_dag_ulw(message))
                self.assertEqual(route_chat_message(message, source="discord")["action"], "fallback")
                payload = awareness_route_hint(message)
                self.assertEqual([(hint["workflow"], hint["next_action"]) for hint in payload["hints"]],
                                 [("dag-ulw", action)])
                context = awareness_route_hint_context_from_payload(payload)
                self.assertIn("call omh_dag action=cancel to stop the run in progress, or action=status", context)
                self.assertNotIn("Start at once", context)
                self.assertNotIn("plan_todo=", context)
        # Work that names a cancel button, a status page or a log-in is still work.
        for message in ("dag ulw add a cancel button to the checkout", "dag ulw build the status page",
                        "dag ulw stop words in the search index", "dag ulw 결제 취소 기능 구현해줘",
                        "dag ulw 상태 관리 모듈 리팩터링해줘", "dag ulw 로그인 기능 구현해줘", "dag ulw 주문 취소 기능 구현해줘",
                        # A work noun or verb after the stop word, or a clause that makes it a condition of the work.
                        "dag ulw 주문 취소 로직 좀 정리해줘요", "dag ulw 결제 취소 플로우 리팩터링 ㄱ",
                        "dag ulw 주문 취소 시 환불되게 해줘", "dag ulw 종료 조건 정리해줘",
                        # 그만큼 ("that much") holds no stop word.
                        "dag ulw 그만큼 빠르게 해줘",
                        # Work words after the stop word, a work-condition clause with nothing else, and an object
                        # before 꺼줘.
                        "dag ulw 결제 취소 이슈 해결해줘", "dag ulw 결제 취소 버그 잡아줘", "dag ulw 결제 취소 디버깅해줘",
                        "dag ulw 결제 취소 쿼리 최적화해줘", "dag ulw 결제 취소 응답 캐싱해줘", "dag ulw 결제 취소 스펙 검토해줘",
                        "dag ulw 주문 취소 문서 써줘", "dag ulw 주문 취소 dto 정의해줘", "dag ulw 중단점 걸고 디버깅해줘",
                        "dag ulw로 중단점 디버깅해줘", "dag ulw 결제 취소 시 쿠폰 복구해줘", "dag ulw 다크모드 꺼줘",
                        "dag ulw 디버그 로그 꺼줘", "dag ulw로 주문 취소 API 만들어줘", "dag ulw 종료 시 정리 작업 추가해줘"):
            with self.subTest(message=message):
                self.assertTrue(invokes_dag_ulw(message))
                self.assertEqual(route_chat_message(message, source="discord")["selected_skill"], "dag-ulw")

    def test_a_stop_noun_after_other_words_asks_whether_to_stop_or_run(self) -> None:
        # Owner decision O7: "주문 취소" names an order cancel as easily as a cancel of the run, so with no verb ending
        # it neither cancels the run nor starts one; both paths ask, naming both readings.
        for message in ("dag ulw 주문 취소", "dag ulw 결제 취소", "dag ulw 예약 취소", "dag ulw 구독 취소",
                        "dag ulw 장바구니 주문 취소", "dag ulw 업로드 중단", "dag ulw 주문취소", "dag ulw 주문을 취소",
                        # Past three words a verb ending asks too: the words may still name the work.
                        "dag ulw 지금 돌리는 결제 모듈 작업 취소해줘", "dag ulw 아까 내가 시작한 결제 작업 좀 취소해줘",
                        # After other words a bare 요, 좀 or ㄱ is no verb; and any other stop with no work after it.
                        "dag ulw 주문 취소요", "dag ulw 결제 취소 좀", "dag ulw 주문 취소 ㄱ", "dag ulw 취소 요청",
                        "dag ulw 취소 바람", "dag ulw로 취소해줘", "dag ulw 이 주문 취소", "dag ulw 결과 취소",
                        # The last stop word decides: a stop of the work on a cancel feature asks, never runs.
                        "dag ulw 결제 취소 기능 만드는 거 그만해줘", "dag ulw 주문 취소 로직 작업 중단해줘",
                        "dag ulw 결제 취소 api 작업 멈춰", "dag ulw 주문 취소 기능 구현하던 거 취소해줘",
                        "dag ulw 결제 중단 처리 리팩터링 그만해", "dag ulw 종료 시 정리 작업 하던 거 멈춰줘",
                        "dag ulw 결제 취소 테스트 돌리는 거 중단해", "dag ulw 아까 시작한 주문 취소 기능 작업 취소해줘",
                        "dag ulw 결제 취소 플로우 리팩터링 중단해주세요",
                        "dag ulw 결제 모듈에 주문 취소 기능 넣고 기존 주문 취소해줘",
                        # A stop of the run, then more work: stop and restart, so it asks.
                        "dag ulw 지금 거 취소하고 결제 모듈부터 다시 해줘", "dag ulw 취소하고 다시 처음부터 구현해줘",
                        "dag ulw 취소하고 결과 정리해줘"):
            with self.subTest(message=message):
                self.assertEqual(dag_ulw_control(message), "cancel_or_run")
                self.assertFalse(invokes_dag_ulw(message))
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"], route["candidate_skill"]),
                                 ("clarify", "oh-my-hermes", "dag-ulw"))
                self.assertIn("stop the dag-ulw run in progress, or start a dag-ulw run", route["clarification"])
                payload = awareness_route_hint(message)
                self.assertEqual([(hint["workflow"], hint["next_action"]) for hint in payload["hints"]],
                                 [("dag-ulw", "ask_one_clarification")])
                self.assertIn("it does neither until the user says which", payload["hints"][0]["reason"])
                context = awareness_route_hint_context_from_payload(payload)
                self.assertIn("Neither define a run nor cancel one yet: ask one short question", context)
                self.assertNotIn("Start at once", context)
                self.assertNotIn("call omh_dag action=cancel to stop the run in progress, or action=status", context)
                self.assertNotIn("plan_todo=", context)

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
