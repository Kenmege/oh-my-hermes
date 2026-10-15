"""The Korean request reader the two safety gates share (`korean_request`)."""

from __future__ import annotations

import unicodedata
import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.korean_request import korean_request


class KoreanRequestTests(unittest.TestCase):
    def test_the_predicate_is_the_verb_a_request_ending_closes(self) -> None:
        for message, predicate in (
            ("내 카드로 결제해줘", "결제"),
            ("결제해 주세요", "결제"),
            ("결제 해 줘", "결제"),
            ("결제 좀 해줘", "결제"),
            ("결제를 해줘", "결제"),
            ("결제까지 진행해 주세요", "결제"),
            ("결제 부탁드립니다", "결제"),
            ("비식별 처리해줘", "비식별"),
            ("이 책 사 주세요", "사"),
            ("엄마한테 돈 보내줘", "보내"),
            ("결제해줘~ ㅎㅎ", "결제"),
            # The everyday sense of a spend word ends on another verb.
            ("결제 시스템 설계해줘", "설계"),
            ("결제 버그 고쳐줘", "고쳐"),
        ):
            with self.subTest(message=message):
                request = korean_request(message)
                self.assertIsNotNone(request)
                assert request is not None
                self.assertEqual(request.predicate, predicate)

    def test_a_sentence_that_ends_on_no_request_is_none(self) -> None:
        for message in (
            "결제했어",
            "결제하지 마",
            "송금해 줬어",
            "결제 안 해줘도 돼",
            "결제해줘 버튼이 안 눌려",
            "결제해줘?",
            "결제가 왜 실패했어?",
        ):
            with self.subTest(message=message):
                self.assertIsNone(korean_request(message))

    def test_an_asking_ending_is_a_request_with_its_question_mark(self) -> None:
        for message in ("결제해 줄 수 있어?", "결제해줄래?", "결제해줄수있어?", "결제해 주시겠어요?"):
            with self.subTest(message=message):
                request = korean_request(message)
                self.assertIsNotNone(request)
                assert request is not None
                self.assertEqual(request.predicate, "결제")

    def test_only_the_first_sentence_is_read(self) -> None:
        self.assertIsNone(korean_request("결제 페이지가 깨졌어. 내 카드로 결제해줘"))
        request = korean_request("내 카드로 결제해줘. 영수증은 메일로")
        assert request is not None
        self.assertEqual((request.predicate, request.sentence), ("결제", "내 카드로 결제해줘"))
        # A decimal point ends no sentence.
        request = korean_request("이더 0.1개 송금해줘")
        assert request is not None
        self.assertEqual(request.predicate, "송금")

    def test_joined_clauses_and_before_clauses(self) -> None:
        request = korean_request("고객 정보 마스킹해서 벤더한테 보내줘")
        assert request is not None
        self.assertEqual((request.predicate, request.joined), ("보내", (("마스킹", 2),)))
        request = korean_request("개인정보 제거한 다음에 외부에 올려줘")
        assert request is not None
        self.assertEqual((request.predicate, request.joined), ("올려", (("제거", 1),)))
        request = korean_request("벤더한테 보내기 전에 개인정보 마스킹해줘")
        assert request is not None
        self.assertEqual((request.predicate, request.before), ("마스킹", ("보내",)))
        request = korean_request("외부에 업로드하기 전에 개인정보 지워줘")
        assert request is not None
        self.assertEqual((request.predicate, request.before), ("지워", ("업로드",)))

    def test_the_router_fold_reads_as_the_hint_fold(self) -> None:
        # The router folds Hangul into jamo (NFKD); the hint keeps it composed.
        for message in ("내 카드로 결제해줘", "결제 좀 해줘", "고객 정보 마스킹해서 벤더한테 보내줘", "결제했어"):
            with self.subTest(message=message):
                composed = korean_request(message)
                folded = korean_request(unicodedata.normalize("NFKD", message))
                if composed is None:
                    self.assertIsNone(folded)
                    continue
                assert folded is not None
                self.assertEqual(
                    (folded.words, folded.predicate, folded.joined, folded.before),
                    (composed.words, composed.predicate, composed.joined, composed.before),
                )
                # The sentence stays in the caller's own fold.
                self.assertEqual(folded.sentence, unicodedata.normalize("NFKD", composed.sentence))

    def test_a_loop_prompt_needs_no_request_ending(self) -> None:
        self.assertIsNone(korean_request("재고 생기면 결제"))
        request = korean_request("재고 생기면 결제", require_ending=False)
        assert request is not None
        self.assertEqual(request.predicate, "결제")


if __name__ == "__main__":
    unittest.main()
