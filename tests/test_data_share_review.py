from __future__ import annotations

import unicodedata
import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import (
    _ROUTE_HINT_RULES,
    _data_share_awareness_marked,
    awareness_context_matches_message,
    awareness_route_hint,
)
from omh.plugin_bundle.omh.data_share_cues import (
    DATA_SHARE_REVIEW_KOREAN_PHRASES,
    DATA_SHARE_REVIEW_PHRASES,
    data_share_phrases_said,
    data_share_request_admitted,
)
from omh.routing.localization import normalized_phrase, routing_tokens
from omh.routing.recommend import _SKILL_OFFERS_ITSELF, recommend_skills
from omh.skills.catalog import builtin_definitions
from omh.skills.packaging import builtin_skill_reference_templates, builtin_skill_templates
from omh.wrapper.contract import build_chat_interaction_payload

SKILL = "data-share-review"
REFERENCE_PATH = "references/data-share-method.md"


def _definition(name: str):
    return next(definition for definition in builtin_definitions() if definition.name == name)


def _route(message: str) -> dict[str, object]:
    return build_chat_interaction_payload(message, source="discord")["route"]


def _body() -> str:
    return next(template.content for template in builtin_skill_templates() if template.name == SKILL)


def _reference() -> str:
    return next(
        template.content
        for template in builtin_skill_reference_templates()
        if template.skill_name == SKILL and template.relative_path == REFERENCE_PATH
    )


def _offered(message: str) -> bool:
    query = normalized_phrase(message)
    return _SKILL_OFFERS_ITSELF[SKILL](query, routing_tokens(query))


class DataShareReviewCatalogTests(unittest.TestCase):
    """A file leaving the team is redacted only after an observed scan of what leaves."""

    def test_each_named_output_has_one_expectation(self) -> None:
        mine = _definition(SKILL)
        self.assertEqual(
            mine.expected_outputs,
            ("field_inventory/v1", "minimization_plan/v1", "residual_scan/v1", "share_record/v1"),
        )
        for schema in mine.expected_outputs:
            with self.subTest(schema=schema):
                self.assertEqual(len([line for line in mine.artifact_expectations if line.startswith(schema)]), 1)

    def test_the_triggers_are_the_routed_phrases(self) -> None:
        self.assertEqual(_definition(SKILL).triggers, DATA_SHARE_REVIEW_PHRASES)

    def test_redacted_needs_an_observed_scan_of_the_file_that_leaves(self) -> None:
        mine = _definition(SKILL)
        self.assertEqual(
            mine.safety_rules[0],
            "Call the output redacted only after an observed residual scan of the exact file that leaves, counted per "
            "class; without one the state is not_observed, whatever the masking step reported.",
        )
        scan = next(line for line in mine.artifact_expectations if line.startswith("residual_scan/v1"))
        self.assertEqual(
            scan,
            "residual_scan/v1 records, for the exact file that leaves, the scan method and an observed count per class "
            "with zero hits or each remaining hit accepted by name, and its verdict: redacted, not redacted with the "
            "counts while a hit remains unaccepted, or not_observed when no scan ran",
        )

    def test_the_mapping_stays_local_and_the_record_holds_no_value(self) -> None:
        mine = _definition(SKILL)
        self.assertIn(
            "Keep the pseudonym mapping local, outside the shipped file and its folder; a mapping that travels with "
            "the data reverses it.",
            mine.safety_rules,
        )
        record = next(line for line in mine.artifact_expectations if line.startswith("share_record/v1"))
        self.assertEqual(
            record,
            "share_record/v1 is metadata only -- destination, date, fields shipped, row count, and the sha256 digest "
            "of the shipped file -- and holds no value from it",
        )

    def test_rows_are_read_only_when_shape_counts_leave_a_field_unclear(self) -> None:
        self.assertIn(
            "- Inventory fields from the header and the schema, read with `read_file` and its line limit; when a "
            "field's class is unclear, prefer a script that prints per-column shape counts, and read a row only when "
            "that still leaves it unclear, because every row read enters the session.",
            _body(),
        )
        self.assertIn(
            "Run it as a script that reads the source column and the output file and prints one count; never type a "
            "value into a search pattern, which puts it into the session.",
            _reference(),
        )

    def test_a_scan_prints_counts_and_its_error_output_returns_too(self) -> None:
        body = _body()
        self.assertIn(
            "- A scan prints counts, never matched values; a hit printed to the session reaches the model provider "
            "the same way the raw file would.",
            body,
        )
        self.assertIn(
            "- Count each class with `search_files` in its `count` output mode, or with a detector script run "
            "through `execute_code` when `execute_code` is in your tool list or tool_search finds it; its stdout "
            "returns, and so do stderr and any traceback, so it prints counts only and catches its own errors without "
            "echoing a value. With neither, hand the user the scan to run and keep the verdict not_observed until the "
            "counts come back.",
            body,
        )

    def test_hermes_native_redaction_is_stated_as_not_this_scan(self) -> None:
        self.assertIn(
            "- Hermes' `privacy.redact_pii` hashes user and chat identifiers in the gateway's session prompt on the "
            "platforms that allow it, and `security.redact_secrets` masks secret-shaped strings in tool output; "
            "neither changes a file that leaves, so neither is this scan.",
            _body(),
        )
        self.assertIn(
            "Neither edits a file on disk or a file that leaves, so neither replaces the scan in section 4.",
            _reference(),
        )

    def test_the_reference_classes_and_scan_rows_cover_the_same_classes(self) -> None:
        reference = _reference()
        classes = reference.split("## 1. Field classes", 1)[1].split("## 2.", 1)[0]
        scan = reference.split("## 4. Residual scan", 1)[1].split("## 5.", 1)[0]

        def first_cells(section: str) -> list[str]:
            rows = [line for line in section.splitlines() if line.startswith("| ") and "---" not in line]
            return [row.strip("|").split("|")[0].strip() for row in rows[1:]]

        self.assertEqual(
            first_cells(classes),
            ["direct identifier", "quasi-identifier", "secret", "free text", "not personal"],
        )
        self.assertEqual(first_cells(scan), ["direct identifier", "secret", "quasi-identifier", "free text"])
        self.assertIn(REFERENCE_PATH, _body())

    def test_the_reference_and_the_why_use_the_skills_own_words(self) -> None:
        # A state or a sibling the model cannot use is a pointer it cannot follow.
        self.assertIn(
            "OMH prepares; the scan runs where the user or the session's tools run, and the file's verdict stays "
            "`not_observed` until its scan counts are observed.",
            _reference(),
        )
        self.assertNotIn("`prepared`", _reference())
        self.assertIn(
            "`omh-security-safety-review` reviews an agent's own exposure, and `omh-legal-compliance-review` reads "
            "the obligations",
            _body(),
        )

    def test_siblings_are_named_once_each_and_legal_points_back(self) -> None:
        mine = _definition(SKILL).do_not_use_when
        for other in (
            "legal-compliance-review",
            "security-safety-review",
            "data-analysis",
            "github-issue-intake",
            "model-finetuning",
            "support-operations",
        ):
            with self.subTest(sibling=other):
                self.assertEqual(len([text for text in mine if f"`{other}`" in text]), 1)
        back = [text for text in _definition("legal-compliance-review").do_not_use_when if f"`{SKILL}`" in text]
        self.assertEqual(
            back,
            [
                "Redact the PII in the file itself and scan it before sending it, once sharing it is cleared; use "
                "`data-share-review`."
            ],
        )


class DataShareReviewRoutingTests(unittest.TestCase):
    """Conservative by design: the skill takes nothing another skill holds."""

    def test_a_protective_lead_on_a_file_leaving_dispatches_here(self) -> None:
        for message in INTERVENTIONS:
            with self.subTest(message=message):
                route = _route(message)
                self.assertEqual(route["action"], "dispatch")
                self.assertEqual(route["selected_skill"], SKILL)

    def test_the_same_words_elsewhere_do_not_dispatch_it(self) -> None:
        for message in NEGATIVES:
            with self.subTest(message=message):
                route = _route(message)
                self.assertNotEqual(route["action"], "dispatch")
                self.assertNotEqual(route.get("candidate_skill"), SKILL)

    def test_a_request_another_skill_holds_stays_there(self) -> None:
        for message, owner in OWNERS:
            with self.subTest(message=message):
                route = _route(message)
                self.assertEqual(route["action"], "dispatch")
                self.assertEqual(route["selected_skill"], owner)

    def test_two_owners_with_a_phrase_each_ask_instead_of_dispatching(self) -> None:
        route = _route("pseudonymize the customer ids in this dataset before it goes into the fine-tune corpus")
        self.assertEqual(route["action"], "clarify")
        self.assertEqual(route["candidate_skill"], SKILL)

    def test_only_an_inherently_protective_lead_admits_the_skill(self) -> None:
        for message, offered in (
            ("redact pii from this csv before sending to the vendor", True),
            ("pseudonymize the customer ids before the vendor gets the export", True),
            ("anonymize the dataset before we upload it to the provider", True),
            ("hey, redact personal data from the spreadsheet before it goes to the contractor", True),
            ("can you de-identify this export for the researcher", True),
            # Every courtesy opener the other readers strip is stripped here
            # too (`request_lead`); before, only "hey," of these was.
            ("hi, redact pii from this csv before sending to the vendor", True),
            ("pls redact pii from this csv before sending to the vendor", True),
            ("kindly redact pii from this csv before sending to the vendor", True),
            ("would you redact pii from this csv before sending to the vendor", True),
            ("hermes, redact pii from this csv before sending to the vendor", True),
            ("hey hermes, de-identify this export for the researcher", True),
            # An everyday verb, a later clause, or another job leads.
            ("strip the pii from this dataset before we send it to an external contractor", False),
            ("take the customer names out of the export before sending it to the vendor", False),
            ("we need to share a ticket export with a vendor, redact the pii first", False),
            ("review the export before we share it with the vendor and redact the pii", False),
            ("scan the export for pii before sharing it with the vendor", False),
            ("write a blog post about the export, then redact the pii before we share the file with the vendor", False),
            # A sentence that opens in Hangul has no Latin lead.
            ("깃허브 이슈 라벨 달아줘, 제목: redact the pii from the export before sending to the vendor", False),
        ):
            with self.subTest(message=message):
                self.assertIs(_offered(message), offered)

    def test_the_lead_verbs_object_must_be_data(self) -> None:
        for message, offered in (
            ("redact the customer names in the export before sending it to the vendor", True),
            ("redact the pii redaction label from the export before sending it to the vendor", False),
            ("pseudonymize the export script before we share the file with the vendor", False),
            ("redact the header row from the csv before we send it to the vendor", False),
        ):
            with self.subTest(message=message):
                self.assertIs(_offered(message), offered)

    def test_a_defined_lead_verb_or_a_question_is_not_a_command(self) -> None:
        for message, offered in (
            ("de-identify this export for the researcher", True),
            ("de-identify means what exactly, before we send the file to the researcher?", False),
            ("de-identify is the term we use in the guide; write a page on how we send files to vendors", False),
            ("redact stands for removing text, so redact the export before sending it to the vendor", False),
            ("pseudonymize refers to replacing ids in the export we send the vendor", False),
            ("redact the pii in this export before sending it to the vendor?", False),
        ):
            with self.subTest(message=message):
                self.assertIs(_offered(message), offered)

    def test_a_carrier_and_a_recipient_must_both_be_present(self) -> None:
        for message, offered in (
            ("redact the pii in the export and hand it over to the vendor", True),
            ("redact the pii in this spreadsheet before I give it to the contractor", True),
            # No carrier: a sample is as often a sample message.
            ("redact the pii from the sample before we send it to the vendor", False),
            # No recipient: by hand, back to me, or to me.
            ("redact the pii in the export by hand", False),
            ("pseudonymize the user ids in this file and give it back to me", False),
            ("redact pii from this file and give me a summary", False),
            ("redact pii from this csv", False),
        ):
            with self.subTest(message=message):
                self.assertIs(_offered(message), offered)

    def test_a_legal_question_a_setting_or_a_storage_scan_withdraws_it(self) -> None:
        for message in (
            "pseudonymize this export for the vendor as gdpr requires",
            "redact pii in the export before hermes uploads it to the vendor",
            # Hermes named after the opener withdraws it whatever the opener.
            "hi, redact pii in the export before hermes uploads it to the vendor",
            "hermes, redact pii in the export before hermes uploads it to the vendor",
            "redact the pii in the files we share with vendors on s3",
            "redact pii across our shared files before the vendor audit",
            "redact pii in the export before sending it to the vendor, then turn on the gateway hashing",
        ):
            with self.subTest(message=message):
                self.assertFalse(_offered(message))

    def test_held_back_words_score_nothing_for_the_skill(self) -> None:
        # The sentence is admitted, so only the hold-back keeps "redact",
        # "pii", "data", "export" and "share" from crediting the skill one by
        # one; a finished state ("pseudonymized") is not its trigger either.
        recommendation = next(
            item
            for item in recommend_skills("redact the pii from the pseudonymized data export before we share it with the vendor")
            if item["skill"] == SKILL
        )
        single_word_credits = [
            label for label in recommendation["matched"] if label.startswith("trigger:") and " " not in label
        ]
        self.assertEqual(single_word_credits, [])

    def test_only_words_that_mean_nothing_else_stay_creditable(self) -> None:
        from omh.routing.recommend import _tokens, held_back_trigger_tokens

        creditable = _tokens(" ".join(_definition(SKILL).triggers)) - held_back_trigger_tokens(SKILL)
        self.assertEqual(
            creditable,
            {
                "de-identify", "de-identifying", "deidentify", "pseudonymisation", "pseudonymise", "pseudonymization",
                "pseudonymize", "pseudonymizing", "quasi-identifier", "quasi-identifiers",
            },
        )

    def test_the_route_hint_needs_the_same_lead(self) -> None:
        def hinted(message: str) -> bool:
            return any(hint.get("workflow") == SKILL for hint in awareness_route_hint(message).get("hints", []))

        self.assertTrue(hinted("redact pii from this csv before sending to the vendor"))
        self.assertTrue(hinted("pseudonymize the emails in customers.csv before I share it with the agency"))
        # The hint reads the router's own trigger table. Before, a smaller one
        # gave this sentence source-finder's hint while the router dispatched
        # it here; and nothing armed awareness for it on a later turn.
        message = "anonymize the dataset before we upload it to the provider"
        self.assertEqual((_route(message)["action"], _route(message)["selected_skill"]), ("dispatch", SKILL))
        self.assertTrue(hinted(message))
        self.assertTrue(awareness_context_matches_message(message))
        rule = next(rule for rule in _ROUTE_HINT_RULES if rule["id"] == "data_share_review")
        self.assertIs(rule["phrases"], DATA_SHARE_REVIEW_PHRASES)
        # The spaced name is no invocation on either surface; the hyphenated
        # name is one on both.
        self.assertFalse(hinted("data share review"))
        self.assertNotIn(SKILL, (_route("data share review").get("selected_skill"), _route("data share review").get("candidate_skill")))
        self.assertTrue(hinted("data-share-review"))
        self.assertEqual(_route("data-share-review")["selected_skill"], SKILL)
        for message in (
            # Admitted, but "pseudonymized" is a finished state, not the token cue.
            "redact the names in the pseudonymized export before sending it to the vendor",
            "strip the pii from this dataset before we send it to an external contractor",
            "run churn analysis on the pseudonymized dataset",
            "does gdpr require us to pseudonymize customer data",
            "close the github issue titled redact pii from logs",
            "how do I enable redact pii in hermes",
            "send a slack message saying the export file is ready to share once we redact pii",
            "de-identify means what exactly, before we send the file to the researcher?",
            "de-identify is the term we use in the guide; write a page on how we send files to vendors",
        ):
            with self.subTest(message=message):
                self.assertFalse(hinted(message))


INTERVENTIONS = (
    "de-identify this csv of patient records before the model provider sees it",
    "de-identify this export for the researcher",
)
NEGATIVES = (
    "what does pii stand for",
    "does gdpr require us to pseudonymize customer data",
    "our privacy policy says we redact pii from logs, is that gdpr compliant",
    "strip pii from our logging pipeline going forward",
    "the movie is a redacted version of the original cut",
    "share this doc with my team",
    "mask the background in this photo",
    "run churn analysis on the pseudonymized dataset",
    "write a python function to redact pii from strings",
    "how do I enable redact pii in hermes",
    "scan for pii in our s3 buckets",
    "send a slack message to the data team asking who owns pii redaction",
    "remove personal data from my google account",
    "explain de-identify versus anonymize",
    "is an ip address pii",
    "pii",
    "send the customer details out to the vendor in the export file",
    "get the personal details out to the contractor in the spreadsheet",
    "go ahead and merge the pr that adds pii redaction to the export file before sending to the vendor",
    "review the pull request that adds pii redaction to the files we send vendors",
    "tell the data team on slack that we will strip the pii from the export before the vendor gets it",
    "post a message in #privacy that we pseudonymize the csv before sharing with researchers",
    "write a blog post on how to strip pii from a dataset before sharing it with vendors",
    "translate this paragraph about how to redact pii from export files sent to vendors into korean",
    "schedule a calendar reminder to redact pii from the monthly export before it goes to the vendor",
    "watch this video about how to redact pii from log files before sharing them",
    "which vendor is best for pseudonymization of datasets shared with third party processors",
    "give me a sample message to tell the vendor we redact pii",
    "take the vendor names out of the export before sending it to the researcher",
    "remove pseudonymization from the export before sending it to the researcher, they need the real ids",
    "strip the de-identify flag from the csv before uploading it to the vendor",
    "review the pii redaction pull request before we send the export to the vendor",
    "check the log file for pii redaction bugs before sending it to the vendor",
    "merge the pr, then redact the pii in the export before sending it to the vendor",
    "de-identify means what exactly, before we send the file to the researcher?",
    "de-identify is the term we use in the guide; write a page on how we send files to vendors",
)
class DataShareReviewKoreanTests(unittest.TestCase):
    """A Korean request to protect data leaving the team reaches the review (KO-2); the same words elsewhere do not."""

    def _hinted(self, message: str) -> bool:
        return any(hint.get("workflow") == SKILL for hint in awareness_route_hint(message).get("hints", []))

    def _named(self, message: str) -> set[str]:
        route = _route(message)
        handoff = route.get("candidate_handoff") or {}
        return {
            str(route.get("selected_skill") or ""),
            str(route.get("candidate_skill") or ""),
            *(str(item.get("skill")) for item in route.get("recommendations", [])),
            *(str(item.get("skill")) for item in handoff.get("candidates", [])),
        }

    def test_a_korean_data_share_request_reaches_the_review(self) -> None:
        # The first dispatched gateway-intent-card on its "보내줘" and the
        # second asked about automation-blueprint: the review now scores its
        # personal-data phrase and its recipient, in either order of the two
        # clauses.
        for message in (
            "고객 정보 마스킹해서 벤더한테 보내줘",
            "개인정보 지우고 외부에 공유해줘",
            "벤더한테 보내기 전에 개인정보 마스킹해줘",
            "고객 데이터 익명화해서 연구자한테 보내줘",
            "개인정보 제거한 다음에 외부에 올려줘",
            "외부에 공유하기 전에 고객 정보 가려줘",
            # An address to Hermes before the request is its address, not a
            # Hermes setting.
            "헤르메스, 고객 정보 마스킹해서 벤더한테 보내줘",
            "헤르메스야 개인정보 지우고 외부에 공유해줘",
            # Asked as a question that is itself a request.
            "개인정보 마스킹해서 벤더한테 보내줄래?",
            "계약서에서 개인정보 지우고 거래처에 보내줘",
            # A protective verb's noun before "후", and the honorific send.
            "상담 기록 개인정보 마스킹 후 외부 업체에 전달해줘",
            "고객 명단 비식별화해서 외부 연구기관에 보내드려 줘",
        ):
            with self.subTest(message=message):
                route = _route(message)
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", SKILL))
                self.assertTrue(self._hinted(message))
                self.assertTrue(_data_share_awareness_marked(message))

    def test_a_recipient_is_where_the_data_goes_only_as_a_destination(self) -> None:
        # The recipient alone carries the data out here: no verb sends it.
        for message in ("벤더한테 줄 파일에서 개인정보 지워줘", "외부 공유용으로 개인정보 지워줘"):
            with self.subTest(message=message):
                self.assertTrue(data_share_request_admitted(message))
                self.assertIn(SKILL, self._named(message))
                self.assertTrue(self._hinted(message))
        # A recipient's 로 before a verb that sends is a destination; a team
        # named after an outside word is outside.
        for message in ("개인정보 지우고 외부로 보내줘", "고객 정보 가려서 외부 연구팀에 공유해줘"):
            with self.subTest(message=message):
                self.assertTrue(data_share_request_admitted(message))
        # A source, a kind, or a word inside another is not a destination.
        for message in (
            "업체 목록에서 전화번호 삭제해줘",
            "거래처 주소록에서 퇴사자 연락처 삭제해줘",
            "외부 로그에서 개인정보 마스킹해줘",
            "해외부서 명단에서 개인정보 삭제해줘",
            "연구자 명단에서 전화번호 지워줘",
            # 로 before anything but a verb that sends names a means or a basis,
            # and a word that starts with a recipient is not one.
            "업체별로 고객 정보 마스킹해줘",
            "거래처별로 전화번호 삭제해줘",
            "외부키로 연결된 고객 정보 삭제해줘",
            "외부키에 연결된 고객 정보 삭제해줘",
            "벤더로 분류된 고객 정보 마스킹해줘",
            "업체명으로 정렬해서 고객 정보 마스킹해줘",
        ):
            with self.subTest(message=message):
                self.assertFalse(data_share_request_admitted(message))
                self.assertNotIn(SKILL, self._named(message))
                self.assertFalse(self._hinted(message))

    def test_a_later_send_and_a_finished_removal_count(self) -> None:
        # Only a future send carries the data out ("올릴"); only "지운 다음에"
        # protects it.
        for message in ("캐글에 올릴 데이터에서 개인정보 지워줘", "개인정보 지운 다음에 외부에 공유해줘"):
            with self.subTest(message=message):
                self.assertTrue(data_share_request_admitted(message))
                self.assertTrue(self._hinted(message))

    def test_the_same_words_elsewhere_name_no_review(self) -> None:
        for message in (
            # Shared, summarized, sent: nothing protected.
            "화면 공유해줘",
            "이 문서 요약해서 공유해줘",
            "회의록 팀에 공유해줘",
            "파일 보내줘",
            # A background masked, not personal data, even for a vendor; and
            # personal data said, but not as what is removed.
            "사진 배경 마스킹해서 보내줘",
            "사진 배경 마스킹해서 벤더한테 보내줘",
            "개인정보 들어간 사진에서 배경 지우고 벤더한테 보내줘",
            # Code, a feature, a policy, a setting: another job.
            "개인정보 마스킹하는 코드 짜줘",
            "개인정보 마스킹 기능 구현해줘",
            "개인정보 처리방침 검토해서 법무팀에 보내줘",
            "개인정보 마스킹 설정 켜줘",
            "개인정보 마스킹해서 법무팀에 보내줘",
            "개인정보 마스킹해서 법률 검토용으로 외부 업체에 보내줘",
            "개인정보 마스킹해서 PR 올려줘",
            # The request ends on another job.
            "개인정보 지우고 벤더 목록 정리해줘",
            "개인정보 지우고 외부에 공유하는 방법 알려줘",
            # Protected, but leaving for nowhere, or staying inside the team.
            "고객 개인정보 지워줘",
            "개인정보 마스킹해줘",
            "테스트 데이터에서 고객 정보 지우고 스테이징에 올려줘",
            "개인정보 지우고 팀원들한테 공유해줘",
            "고객 정보 마스킹해서 개발팀에 보내줘",
            "개인정보 지우고 QA팀한테 넘겨줘",
            "고객 정보 마스킹해서 테스트 서버에 올려줘",
            # Exported to a file or a folder: nothing leaves.
            "개인정보 지운 다음 엑셀로 내보내줘",
            "고객 정보 마스킹해서 CSV로 내보내줘",
            "고객 정보 지우고 백업 폴더로 내보내줘",
            # A field name alone is no phrase of the skill.
            "이름 지우고 공유해줘",
            # Already anonymized, or a question.
            "익명화된 데이터 벤더한테 보내줘",
            "개인정보 지우고 외부에 공유해도 돼?",
            # A negated clause is not the request.
            "벤더한테 보내지 말고 개인정보만 마스킹해줘",
            "개인정보 마스킹하지 말고 벤더한테 보내줘",
            # Labelled text is not the request.
            "사용자 발화 예시: 고객 정보 마스킹해서 벤더한테 보내줘",
        ):
            with self.subTest(message=message):
                self.assertNotIn(SKILL, self._named(message))
                self.assertFalse(self._hinted(message))
                # Folded as `awareness_context_matches_message` folds it.
                self.assertFalse(_data_share_awareness_marked(message.casefold()))

    def test_a_korean_phrase_inside_a_compound_is_read(self) -> None:
        # The router reads a Korean trigger by containment. Korean writes
        # compounds without a space, and the hint's whole-word reader found
        # neither phrase here, so the hint named nothing while the router
        # dispatched the review.
        message = "고객개인정보 지우고 협력업체에 공유해줘"
        self.assertEqual(data_share_phrases_said(message), ["개인정보", "업체"])
        self.assertEqual(data_share_phrases_said("pseudonymized export"), [])
        self.assertEqual((_route(message)["action"], _route(message)["selected_skill"]), ("dispatch", SKILL))
        self.assertTrue(self._hinted(message))

    def test_the_router_and_the_hint_admit_alike(self) -> None:
        for message in (
            "고객 정보 마스킹해서 벤더한테 보내줘",
            "벤더한테 보내기 전에 개인정보 마스킹해줘",
            "사진 배경 마스킹해서 보내줘",
            "이름 지우고 공유해줘",
            "업체 목록에서 전화번호 삭제해줘",
        ):
            with self.subTest(message=message):
                self.assertEqual(_offered(message), self._hinted(message))
        # A field name admits a request only beside one of the review's
        # phrases; alone, the rule itself refuses it.
        self.assertFalse(data_share_request_admitted("이름 지우고 공유해줘"))
        self.assertTrue(data_share_request_admitted("이름 지우고 외부에 공유해줘"))
        self.assertTrue(data_share_request_admitted("pii 마스킹해서 벤더한테 보내줘"))

    def test_an_admitted_korean_request_always_says_a_scored_phrase(self) -> None:
        for message in ("고객 정보 마스킹해서 벤더한테 보내줘", "데이터셋 가명 처리해서 업로드해줘"):
            with self.subTest(message=message):
                self.assertTrue(data_share_request_admitted(message))
                self.assertTrue(set(data_share_phrases_said(message)) & set(DATA_SHARE_REVIEW_KOREAN_PHRASES))
                score = next(item["score"] for item in recommend_skills(message, limit=50) if item["skill"] == SKILL)
                self.assertGreaterEqual(score, 6)


class DataShareReviewInternalDestinationTests(unittest.TestCase):
    """Owner decision 2026-10-16: data sent to a team inside the same company is internal, in English as in Korean.

    The gate fails safe: a place is inside only when every word of it is
    proven inside. Any other word keeps the review.
    """

    def _hinted(self, message: str) -> bool:
        return any(hint.get("workflow") == SKILL for hint in awareness_route_hint(message).get("hints", []))

    def _named(self, message: str) -> bool:
        route = _route(message)
        return SKILL in (route.get("selected_skill"), route.get("candidate_skill")) or SKILL in (
            item.get("skill") for item in route.get("recommendations", [])
        )

    def test_a_send_to_a_place_proven_inside_names_no_review(self) -> None:
        for message in (
            "redact pii in the export before we send it to the dev team",
            "anonymize the dataset before sharing it with the marketing team",
            "redact pii in the csv before sending it to qa",
            "redact the logs before uploading them to staging",
            "de-identify the records before we upload the file to the test server",
            "redact pii from this csv before sending it to my coworker",
            "anonymize the export and share it with a colleague",
            "pseudonymize the user ids in the dataset before sharing it with another department",
            "redact personal data from the spreadsheet before sending it to the internal analytics team",
            "anonymize the logs before we share them with the in-house data team",
            "redact pii in the export and hand it over to our security team",
            "de-identify this csv before I give it to the qa team",
            "redact the pii in the export before sharing it with my teammates",
            "de-identify the export before posting it to the internal wiki",
            "redact pii in the export and upload it to the in-house analytics tool",
            "redact pii from the records before sending them to a coworker in finance",
            "redact pii in the export, give me a summary, and send it to the dev team",
            "redact pii in the export and send it to the data team and the qa team",
            "개인정보 지우고 동료한테 보내줘",
            "고객 정보 가명 처리해서 QA에 보내줘",
            "개인정보 가명 처리해서 다른 부서에 공유해줘",
            "개인정보 지우고 인사 부서에 보내줘",
            "개인정보 지우고 부서원들한테 공유해줘",
            "이메일 주소를 익명화해서 사내 슬랙 채널에 공유해줘",
            "개인정보 지우고 개발팀에 이메일로 보내줘",
        ):
            with self.subTest(message=message):
                self.assertFalse(_offered(message))
                self.assertFalse(self._hinted(message))
                self.assertFalse(_data_share_awareness_marked(message.casefold()))

    def test_any_word_not_proven_inside_keeps_the_review(self) -> None:
        # A company, a client, a possessive other than ours, a relative
        # clause, a place after "at", "of" or "via", a purpose, and a team with
        # no owner: none is proven inside, so the data leaves.
        for message in (
            "redact the pii in the export and send it to the vendor's team",
            "anonymize the dataset before sharing it with an external team",
            "pseudonymize the customer ids in the csv before sending it to the partner team at acme",
            "redact the pii in the files and share them with the client team",
            "redact the pii in the export before sharing it with the customer's team",
            "anonymize the dataset before sharing it with the partner's staging server",
            "redact pii in the export before we send it to the acme team",
            "redact pii in the export before we send it to acme's data team",
            "anonymize the dataset before sharing it with the hospital team",
            "redact pii in the export before we send it to the regulator's team",
            "redact pii in the export before we send it to the law firm's team",
            "anonymize the dataset before sharing it with the deloitte team",
            "redact pii in the export before we send it to the subcontractor team",
            "redact pii in the export before we send it to the department of health",
            "redact pii in the export before we send it to a former colleague who now works at acme",
            "anonymize the dataset before uploading it to the acme staging server",
            "redact pii in the export before we send it to the data team at acme",
            "redact pii in the export before we send it to the data team over at acme's office",
            "redact pii in the export before we upload it via acme's portal to the data team",
            "redact pii in the export and send it to the dev team for review",
            "redact pii in the export and send it to our team in seoul",
            "redact the pii in the files and share them with the whole team",
            "redact pii in the export and send it to the team",
            # Safe-direction disclosures: these may be internal, but nothing proves it.
            "anonymize the dataset before sharing it with the customer success team",
            "redact pii in the export and send it to my manager",
            "고객 정보 익명화해서 외부 팀에 공유해줘",
            "개인정보 마스킹해서 벤더 팀에 보내줘",
            "개인정보 지우고 외부팀에 보내줘",
            "개인정보 비식별화해서 벤더 테스트 서버에 올려줘",
            "개인정보 마스킹해서 외부 연구팀에 보내줘",
            "개인정보 지우고 벤더 쪽 팀에 보내줘",
            "전화번호 지우고 협력업체 보안팀에 전달해줘",
            "고객 정보 마스킹해서 은행 담당 부서에 보내줘",
            "고객 정보 마스킹해서 카드사 보안 부서에 보내줘",
            "고객 정보 마스킹해서 다른 회사 동료에게 보내줘",
            "고객 정보 마스킹해서 acme qa에 보내줘",
            "이메일 주소 지우고 acme 데이터팀에 보내줘",
            "개인정보 지우고 팀 전체에 공유해줘",
            "개인정보 지우고 고객지원팀에 보내줘",
        ):
            with self.subTest(message=message):
                self.assertTrue(_offered(message))
                self.assertTrue(self._hinted(message))

    def test_an_english_recipient_in_a_korean_sentence_keeps_the_review(self) -> None:
        for message in (
            "고객 정보 마스킹해서 vendor team에 보내줘",
            "고객 정보 마스킹해서 external team에 보내줘",
            "고객 정보 마스킹해서 agency team에 보내줘",
            "고객 정보 마스킹해서 contractor team에 보내줘",
            "고객 정보 마스킹해서 third-party team에 보내줘",
            "고객 정보 마스킹해서 data team에 보내고 vendor에도 보내줘",
        ):
            with self.subTest(message=message):
                self.assertTrue(_offered(message))
                self.assertTrue(self._hinted(message))
        self.assertFalse(_offered("고객 정보 마스킹해서 data team에 보내줘"))

    def test_every_send_and_every_joined_place_must_be_inside(self) -> None:
        for message in (
            "redact pii in the export before we send it",
            "redact pii in the export, share it with the dev team and post it publicly",
            "anonymize the dataset, send it to the qa team and upload it to kaggle",
            "redact pii in the export and send it to the dev team a copy",
            "redact pii in the export and send the dev team a copy",
            "redact pii in the export before we send it to acme so the team can check it",
            "redact pii in the export and upload it to github for the dev team",
            "redact pii in the export and send it to the data team and acme",
            "redact pii in the export and send it to the data team or the auditor",
            "redact pii in the export and send it to the data team plus acme",
            "redact pii in the export and send it to the data team as well as acme",
            "redact pii in the export and send it to both the data team and acme",
            "redact pii in the export and send it to the data team, the auditors, and acme",
            "redact pii in the export and send it to the data team, cc the auditor",
            "redact pii in the export and send it to the data team so they can forward it to acme",
            "redact pii in the export and send it to the data team who will pass it to acme",
            "redact pii in the export, send it to the dev team and email it to acme",
            "redact pii in the export, send it to the dev team and transfer it to acme",
            "개인정보 익명화해서 QA팀에 보내고 캐글에 올려줘",
            "고객 정보 마스킹해서 데이터팀에 보내고 acme에도 보내줘",
        ):
            with self.subTest(message=message):
                self.assertTrue(_offered(message))

    def test_quoted_text_never_proves_a_place_inside(self) -> None:
        # Both surfaces read the request with its quoted text removed; what
        # is left ("the team", nothing) is not proven inside either.
        for message in (
            'redact pii in the export and send it to the "partner" team',
            'redact pii in the export and send it to the team called "acme support"',
            'redact pii in the export and send it to "our team"',
        ):
            with self.subTest(message=message):
                self.assertTrue(_offered(message))
                self.assertTrue(self._named(message))
                self.assertTrue(self._hinted(message))

    def test_english_and_korean_agree(self) -> None:
        for english, korean, offered in (
            ("redact pii in the export before we send it to the dev team", "개인정보 지우고 개발팀에 보내줘", False),
            ("redact the logs before uploading them to staging", "로그에서 개인정보 지우고 스테이징에 올려줘", False),
            ("de-identify the records before we upload the file to the test server", "개인정보 비식별화해서 테스트 서버에 올려줘", False),
            ("redact pii from this csv before sending it to my coworker", "개인정보 지우고 동료한테 보내줘", False),
            ("pseudonymize the user ids in the dataset before sharing it with another department", "개인정보 가명 처리해서 다른 부서에 공유해줘", False),
            ("anonymize the logs before we share them with the in-house data team", "개인정보 익명화해서 사내 데이터팀에 공유해줘", False),
            ("redact pii in the csv before sending to qa", "개인정보 지우고 QA에 보내줘", False),
            ("redact pii in the export before we send it to the vendor", "개인정보 지우고 벤더한테 보내줘", True),
            ("redact the pii in the export and send it to the vendor's team", "개인정보 마스킹해서 벤더 팀에 보내줘", True),
            ("anonymize the dataset before sharing it with an external team", "고객 정보 익명화해서 외부 팀에 공유해줘", True),
            ("anonymize the export and share it with the client's team", "고객 정보 익명화해서 고객사 팀에 공유해줘", True),
            ("anonymize the csv before sending it to the university research team", "개인정보 익명화해서 대학 연구팀에 보내줘", True),
            ("redact the emails in the export before sending it to the data team at acme", "이메일 주소 지우고 acme 데이터팀에 보내줘", True),
            ("anonymize the dataset, send it to the qa team and upload it to kaggle", "개인정보 익명화해서 QA팀에 보내고 캐글에 올려줘", True),
        ):
            with self.subTest(english=english):
                self.assertIs(_offered(english), offered)
                self.assertIs(_offered(korean), offered)

    def test_hangul_in_an_english_place_reads_alike_on_both_folds(self) -> None:
        # The router folds Hangul to NFKD and the hint keeps it NFC; Hangul in
        # an English place is never an inside word, so both admit alike.
        for message in ("redact pii in the export and send it to 벤더 팀", "redact pii in the export and send it to 우리 팀"):
            for form in ("NFC", "NFD"):
                text = unicodedata.normalize(form, message)
                with self.subTest(message=message, form=form):
                    self.assertTrue(_offered(text))
                    self.assertTrue(data_share_request_admitted(text.casefold()))
                    self.assertTrue(self._hinted(text))

    def test_every_inside_word_is_pinned(self) -> None:
        # Each literal is the contract: removing a word from the tables makes
        # its sentence leave.
        english = "redact pii in the export before we send it to {}"
        for place in (
            "staging", "qa", "my teammate", "my teammates", "a colleague", "colleagues", "my coworker", "my coworkers",
            "a co-worker", "my co-workers", "the internal wiki", "an in-house tool", "the test server",
            "the test environment", "the dev server", "our team", "our teams", "another department",
            "the hr departments", "both the data team and the qa team", "our staging servers",
            "the internal dashboard", "the internal slack channel", "the internal workspace",
            "the data team or the qa team", "the data team plus the qa team", "the data team as well as the qa team",
            "the dev team today", "the dev team please",
            *(f"the {owner} team" for owner in (
                "data", "dev", "development", "engineering", "test", "testing", "marketing", "sales", "finance", "hr",
                "security", "platform", "analytics", "support", "ops", "operations", "product", "design", "research",
                "ml", "ai", "bi", "backend", "frontend", "infra", "infrastructure", "it", "mobile", "web", "growth",
            )),
            "the data science team", "my team",
        ):
            with self.subTest(place=place):
                self.assertFalse(_offered(english.format(place)))
        for message in (
            "개인정보 지우고 사내 위키에 올려줘",
            "개인정보 지우고 내부 서버에 올려줘",
            "개인정보 지우고 스테이징에 올려줘",
            "개인정보 지우고 개발서버에 올려줘",
            "개인정보 지우고 테스트 서버에 올려줘",
            "개인정보 지우고 로컬에 올려줘",
            "개인정보 지우고 동료한테 보내줘",
            "개인정보 지우고 우리 팀에 보내줘",
            "개인정보 지우고 다른 부서에 보내줘",
            "개인정보 지우고 우리 팀 전체에 공유해줘",
            "개인정보 지우고 모든 팀원들한테 공유해줘",
            "개인정보 지우고 같은 팀 동료한테 보내줘",
            *(f"개인정보 지우고 사내 {head}에 올려줘" for head in ("서버", "환경", "위키", "대시보드", "툴", "채널", "슬랙", "메신저")),
            *(f"개인정보 지우고 {owner} 팀에 보내줘" for owner in (
                "우리", "내", "제", "저희", "다른", "타", "개발", "데이터", "분석", "마케팅", "영업", "재무", "인사", "보안",
                "플랫폼", "지원", "운영", "제품", "기획", "디자인", "연구", "백엔드", "프론트엔드", "인프라", "엔지니어링", "품질",
            )),
            "개인정보 지우고 데이터 사이언스 팀에 보내줘",
            *(f"개인정보 지운 {step} 개발팀에 보내줘" for step in ("다음", "다음에", "후", "후에", "뒤", "뒤에")),
            *(f"개인정보 지우고 개발팀에 {channel}로 보내줘" for channel in ("이메일", "메일", "메신저")),
            "개인정보 지우고 개발팀에 슬랙으로 보내줘",
            *(f"개인정보 지우고 {file}로 내보내줘" for file in ("엑셀", "csv", "pdf", "json", "xlsx", "txt", "텍스트", "파일", "폴더", "백업 폴더")),
        ):
            with self.subTest(message=message):
                self.assertFalse(_offered(message))


OWNERS = (
    ("attach the crash log to the github issue", "github-event-ops"),
    ("summarize this customer support transcript", "media-input-operator"),
    ("fine-tune a model on our support tickets", "model-finetuning"),
    ("file a bug report on github about the login crash", "github-issue-intake"),
    ("close the github issue titled redact pii from logs", "github-event-ops"),
    ("rerun the failing github action for the pseudonymize job", "github-event-ops"),
    ("write a blog post about pii redaction best practices", "web-research"),
    ("hey, triage the github issues about pii redaction in the public log files", "github-event-ops"),
    ("i need you to triage the issues about pii redaction in log files we post publicly", "connector-operator"),
    ("send a slack message saying the export file is ready to share once we redact pii", "gateway-intent-card"),
    ("redact personal data from these server logs before I attach them to a public github issue", "github-event-ops"),
    ("redact the pii in this support transcript excerpt before we post it in the public tracker", "media-input-operator"),
    ("strip the pii from this dataset before we send it to an external contractor", "connector-operator"),
    ("redact the pii in this crash log before attaching it to the public github issue", "github-event-ops"),
    ("remove the pii redaction label from the github issue about the export we sent the vendor", "github-event-ops"),
    ("transcribe this call, then redact the pii in the transcript excerpt before posting it publicly", "media-input-operator"),
    ("깃허브 이슈 라벨 달아줘, 제목: redact the pii from the export before sending to the vendor", "github-event-ops"),
    ("redact pii 하고 vendor한테 export 파일 보내줘", "gateway-intent-card"),
)


if __name__ == "__main__":
    unittest.main()
