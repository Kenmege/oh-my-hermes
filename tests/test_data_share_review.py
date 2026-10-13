from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import awareness_route_hint
from omh.plugin_bundle.omh.data_share_cues import DATA_SHARE_REVIEW_PHRASES
from omh.routing.localization import normalized_phrase, routing_tokens
from omh.routing.policy import data_share_review_offers_itself, data_share_review_spoken
from omh.routing.recommend import recommend_skills
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
    return data_share_review_offers_itself(query, routing_tokens(query))


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
                "Strip the PII from the file itself and scan it before sending it, once sharing it is cleared; use "
                "`data-share-review`."
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

    def test_the_owners_keep_requests_whose_object_is_not_a_file_leaving(self) -> None:
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

    def test_only_a_request_led_by_a_protection_verb_is_admitted(self) -> None:
        for message, offered in (
            ("pseudonymize the customer ids before the vendor gets the export", True),
            ("de-identify this export for the researcher", True),
            ("review the export for residual pii before we send it to the vendor", True),
            # The sentence only mentions the work; its request is something else.
            ("send the customer details out to the vendor in the export file", False),
            ("write a blog post on how to strip pii from a dataset before sharing it with vendors", False),
            ("tell the data team on slack that we will strip the pii from the export before the vendor gets it", False),
            ("after we strip the pii, email the export to the vendor", False),
            ("upload this file to the vendor after we pseudonymize it", False),
            # A lead verb with no phrase of the skill: nothing personal is named.
            ("remove the old rows from this export before we send it to the vendor", False),
            ("review the export before we send it to the vendor", False),
        ):
            with self.subTest(message=message):
                self.assertIs(_offered(message), offered)

    def test_filler_is_skipped_and_a_later_clause_can_lead(self) -> None:
        for message in (
            "hey, redact the pii from this export before we send it to the vendor",
            "can you strip the pii from this csv before we share it with the agency",
            "go ahead and pseudonymize the user ids in this dataset before it goes to the vendor",
            "we need to share a ticket export with a vendor, strip the pii first",
            "the analytics vendor wants last month's ticket export, take the customer details out first",
            "i need you to strip the pii from the logs, then upload them to the vendor",
        ):
            with self.subTest(message=message):
                self.assertTrue(_offered(message))

    def test_a_carrier_and_a_recipient_must_both_be_present(self) -> None:
        for message, offered in (
            ("strip the pii from the export and hand it over to the vendor", True),
            ("mask personal data in this spreadsheet before I give it to the contractor", True),
            # No carrier: a sample is as often a sample message.
            ("redact the pii from the sample before we send it to the vendor", False),
            # No recipient: by hand, back to me, or to me.
            ("strip the pii from the export by hand", False),
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
            "scan for pii in the files we share with vendors on s3",
            "scan for pii across our shared files before the vendor audit",
        ):
            with self.subTest(message=message):
                self.assertFalse(_offered(message))

    def test_held_back_words_score_nothing_for_the_skill(self) -> None:
        # The request is admitted, so only the hold-back keeps "strip", "pii",
        # "data", "export" and "share" from crediting the skill one by one.
        recommendation = next(
            item
            for item in recommend_skills("strip the pii from the data export before we share it with the vendor")
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

    def test_the_route_hint_needs_the_same_request(self) -> None:
        def hinted(message: str) -> bool:
            return any(hint.get("workflow") == SKILL for hint in awareness_route_hint(message).get("hints", []))

        self.assertTrue(hinted("redact pii from this csv before sending to the vendor"))
        self.assertTrue(hinted("pseudonymize the emails in customers.csv before I share it with the agency"))
        for message in (
            # A finished state: "pseudonymize" is a whole token.
            "pseudonymized exports go to the vendor, redact the logs too",
            "run churn analysis on the pseudonymized dataset",
            "does gdpr require us to pseudonymize customer data",
            "close the github issue titled redact pii from logs",
            "how do I enable redact pii in hermes",
            "write a blog post on how to strip pii from a dataset before sharing it with vendors",
            "send a slack message saying the export file is ready to share once we redact pii",
        ):
            with self.subTest(message=message):
                self.assertFalse(hinted(message))


INTERVENTIONS = (
    "redact the pii in this crash log before attaching it to the public github issue",
    "we need to share a ticket export with a vendor, strip the pii first",
    "pseudonymize the customer ids in this dataset before it goes into the fine-tune corpus",
    "redact personal data from these server logs before I attach them to a public github issue",
    "redact the pii in this support transcript excerpt before we post it in the public tracker",
    "strip the pii from this dataset before we send it to an external contractor",
    "de-identify this csv of patient records before the model provider sees it",
    "scan the masked export for residual pii before we hand it over",
    "the analytics vendor wants last month's ticket export, take the customer details out first",
    "redact pii from this csv before sending to the vendor",
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
)
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
)


if __name__ == "__main__":
    unittest.main()
