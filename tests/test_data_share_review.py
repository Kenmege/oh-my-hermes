from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.routing.localization import normalized_phrase, routing_tokens
from omh.routing.policy import data_share_review_offers_itself, data_share_review_spoken
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
            "with zero hits or each remaining hit accepted by name, and its verdict: redacted, or not_observed when no "
            "scan ran",
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

    def test_a_scan_prints_counts_and_names_the_native_tools_that_produce_them(self) -> None:
        body = _body()
        self.assertIn(
            "- A scan prints counts, never matched values; a hit printed to the session reaches the model provider "
            "the same way the raw file would.",
            body,
        )
        self.assertIn(
            "- Count each class with `search_files` in its `count` output mode, or with a detector script run "
            "through `execute_code` when `execute_code` is in your tool list or tool_search finds it; only that "
            "script's stdout returns, so it prints counts. With neither, hand the user the scan to run and keep the "
            "verdict not_observed until the counts come back.",
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
                "The file itself is about to leave and needs its PII redaction and a residual PII scan first; use "
                "`data-share-review` once sharing it is cleared."
            ],
        )


class DataShareReviewRoutingTests(unittest.TestCase):
    def test_a_file_leaving_with_personal_data_dispatches_here(self) -> None:
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

    def test_the_destination_owners_keep_requests_with_nothing_to_strip(self) -> None:
        for message, owner in OWNERS:
            with self.subTest(message=message):
                route = _route(message)
                self.assertEqual(route["action"], "dispatch")
                self.assertEqual(route["selected_skill"], owner)

    def test_a_phrase_counts_as_its_own_words_and_not_as_a_finished_state(self) -> None:
        for message, spoken in (
            ("pseudonymize the user ids before the export", True),
            ("run churn analysis on the pseudonymized dataset", False),
            ("de-identify these records first", True),
            ("analyze the de-identified extract", False),
            ("set privacy.redact_pii true in hermes", False),
            ("what does pii stand for", False),
        ):
            with self.subTest(message=message):
                self.assertIs(data_share_review_spoken(normalized_phrase(message)), spoken)

    def test_a_legal_question_or_a_system_change_withdraws_the_skill(self) -> None:
        for message, offered in (
            ("pseudonymize the customer ids before the vendor gets the export", True),
            ("does gdpr require us to pseudonymize customer data", False),
            ("our privacy policy says we redact pii from logs, is that gdpr compliant", False),
            ("strip pii from our logging pipeline going forward", False),
            ("remove the pii field from the api response", False),
        ):
            with self.subTest(message=message):
                query = normalized_phrase(message)
                self.assertIs(data_share_review_offers_itself(query, routing_tokens(query)), offered)


INTERVENTIONS = (
    "we need to share a ticket export with a vendor, strip the pii first",
    "pseudonymize the customer ids in this dataset before it goes into the fine-tune corpus",
    "redact personal data from these server logs before I attach them to a public github issue",
    "redact the pii in this support transcript excerpt before we post it in the public tracker",
    "strip the pii from this dataset before we send it to an external contractor",
    "de-identify this csv of patient records before the model provider sees it",
    "scan the masked export for residual pii before we hand it over",
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
)
OWNERS = (
    ("attach the crash log to the github issue", "github-event-ops"),
    ("summarize this customer support transcript", "media-input-operator"),
    ("fine-tune a model on our support tickets", "model-finetuning"),
    ("file a bug report on github about the login crash", "github-issue-intake"),
)


if __name__ == "__main__":
    unittest.main()
