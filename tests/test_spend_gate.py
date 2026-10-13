from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.native_tools import native_tool_presence_clause
from omh.routing.localization import normalized_phrase
from omh.routing.policy import everyday_sense_phrase_unanchored
from omh.routing.recommend import _WHOLE_PHRASE_ONLY_TRIGGER_TOKENS, _prepare_definition
from omh.skills.catalog import builtin_definitions
from omh.skills.packaging import builtin_skill_reference_templates, builtin_skill_templates
from omh.wrapper.contract import build_chat_interaction_payload

SKILL = "spend-gate"
REFERENCE_PATH = "references/spend-rails.md"


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


class SpendGateContractTests(unittest.TestCase):
    """A pre-spend card on any rail, done only on an observed receipt, never spent from an unattended run."""

    def test_card_confirmation_receipt_and_ledger_are_named_outputs(self) -> None:
        mine = _definition(SKILL)
        self.assertEqual(
            mine.expected_outputs,
            ("pre_spend_card/v1", "spend_confirmation/v1", "spend_receipt/v1", "spend_cap_ledger/v1"),
        )
        for schema in mine.expected_outputs:
            with self.subTest(expectation=schema):
                self.assertEqual(len([line for line in mine.artifact_expectations if line.startswith(schema)]), 1)

    def test_the_card_names_every_field_the_person_decides_on(self) -> None:
        card = next(line for line in _definition(SKILL).artifact_expectations if line.startswith("pre_spend_card/v1"))
        self.assertIn(
            "names the payee with its exact origin or address, the item, the total with fees and taxes in its "
            "currency, the rail, the cap and what remains of it, the refund terms, whether the spend can be reversed, "
            "and the approver",
            card,
        )

    def test_omh_confirmation_stands_whether_or_not_a_host_prompt_appears(self) -> None:
        rule = _definition(SKILL).safety_rules[0]
        self.assertEqual(
            rule,
            "Show the pre-spend card and get the person's typed confirmation before any money moves, whether or not "
            "Hermes shows an approval prompt of its own; a host prompt is additional evidence, never a replacement, "
            "and a prompt that did not appear is no confirmation.",
        )
        reference = _reference()
        self.assertIn("none replaces the person's typed confirmation in this session", reference)
        self.assertIn(
            "An auto-approval (`/yolo`, `approvals.mode: off`, or a call the default `smart` mode judged low-risk "
            "and approved itself) or a command the check did not flag is no confirmation at all.",
            reference,
        )

    def test_a_run_nobody_can_answer_never_spends(self) -> None:
        rules = _definition(SKILL).safety_rules
        self.assertIn(
            "Never spend from a run nobody can answer -- a cron job, a webhook, an API-server run, a single-query "
            "`hermes chat -q`, a kanban worker, or a delegated subagent; prepare the card and hand it to the person's "
            "next interactive turn.",
            rules,
        )
        self.assertTrue(_definition(SKILL).opening_steps[0].startswith("Check that a person can answer in this session"))

    def test_only_an_observed_receipt_completes_a_spend(self) -> None:
        mine = _definition(SKILL)
        self.assertIn(
            "A quote is not a purchase and a confirmation is not a charge; call a spend done only on an observed "
            "receipt -- an order id, a transaction id, or a receipt header -- with the amount charged.",
            mine.safety_rules,
        )
        receipt = next(line for line in mine.artifact_expectations if line.startswith("spend_receipt/v1"))
        self.assertIn("keeps quoted, confirmed, submitted, charged, declined, and unknown as separate states", receipt)

    def test_an_unknown_outcome_is_reconciled_before_any_retry(self) -> None:
        self.assertIn(
            "Treat an outcome nobody saw -- a timeout, a dropped connection, a page that reloaded -- as unknown, not "
            "failed; find the charge or show it absent before any retry, and a retry is a new spend with a new card.",
            _definition(SKILL).safety_rules,
        )
        section = _reference().split("## 3. Unknown outcomes", 1)[1].split("## 4.", 1)[0]
        self.assertIn("is `unknown`, not failed", section)
        self.assertIn("3. Found: record the receipt and stop; there is nothing to retry.", section)
        self.assertIn("4. Shown absent: a retry is a new spend, so show the card again and get a new confirmation.", section)
        self.assertIn("5. Still unknown: report it as unknown", section)

    def test_the_ledger_is_metadata_only(self) -> None:
        ledger = next(line for line in _definition(SKILL).artifact_expectations if line.startswith("spend_cap_ledger/v1"))
        self.assertIn(
            "holds metadata only -- date, payee, amount, currency, rail, and receipt reference -- with the running "
            "total against the cap, and never a card number, security code, wallet key, password, or one-time code",
            ledger,
        )
        self.assertIn("An `unknown` row counts against the cap until it is reconciled.", _reference())

    def test_native_surfaces_are_named_behind_the_presence_rule(self) -> None:
        body = _body()
        reference = _reference()
        for tool in ("browser_vault", "clarify"):
            clause = native_tool_presence_clause(tool, code=True)
            with self.subTest(tool=tool):
                self.assertIn(f"when {clause}", body)
                self.assertIn(f"when {clause}", reference)
        self.assertIn(
            "When it is absent, never type card details into a page; stop at the payment step and let the person "
            "enter them.",
            reference,
        )
        self.assertIn(
            "The optional Hermes skills `stripe-link-cli` and `mpp-agent`, only when the person installed them",
            reference,
        )
        for text in (body, reference):
            self.assertNotIn("0.21", text)

    def test_the_reference_table_has_every_rail_and_its_four_columns(self) -> None:
        table = _reference().split("## 1. Rails", 1)[1].split("## 2.", 1)[0]
        rows = [line for line in table.splitlines() if line.startswith("| ") and not line.startswith("| Rail") and "---" not in line]
        rails = {row.strip("|").split("|")[0].strip() for row in rows}
        self.assertEqual(
            rails,
            {
                "card checkout in a page",
                "HTTP 402 or pay-per-request call",
                "wallet transfer",
                "booking or deposit",
                "paid data endpoint or metered API",
                "subscription or renewal",
            },
        )
        for row in rows:
            with self.subTest(row=row):
                cells = row.strip("|").split("|")
                self.assertEqual(len(cells), 4)
                self.assertTrue(all(cell.strip() for cell in cells))
        self.assertIn(REFERENCE_PATH, _body())

    def test_each_sibling_is_named_once(self) -> None:
        mine = _definition(SKILL).do_not_use_when
        for other in ("browser-operator", "feedback-triage", "finance-analysis", "provider-profile-posture"):
            with self.subTest(sibling=other):
                self.assertEqual(len([text for text in mine if f"`{other}`" in text]), 1)


class SpendGateRoutingTests(unittest.TestCase):
    def test_spends_dispatch_here_without_naming_the_skill(self) -> None:
        for message in INTERVENTIONS:
            with self.subTest(message=message):
                route = _route(message)
                self.assertEqual(route["action"], "dispatch")
                self.assertEqual(route["selected_skill"], SKILL)

    def test_the_same_words_elsewhere_stay_away(self) -> None:
        for message in NOT_SPENDING:
            with self.subTest(message=message):
                route = _route(message)
                self.assertNotEqual(route.get("selected_skill"), SKILL)
                self.assertNotEqual(route.get("candidate_skill"), SKILL)

    def test_siblings_keep_their_owners(self) -> None:
        for message, owner in OWNERS:
            with self.subTest(message=message):
                route = _route(message)
                self.assertEqual(route["action"], "dispatch")
                self.assertEqual(route["selected_skill"], owner)

    def test_everyday_cap_and_wallet_phrases_need_a_word_of_paying(self) -> None:
        for message, withdrawn in (
            ("the government raised the spending cap again this year", True),
            ("the photo fell out from my wallet on the bus", True),
            ("set a spending cap before the agent buys anything", False),
            ("transfer 40 dollars from my wallet to the vendor", False),
        ):
            with self.subTest(message=message):
                self.assertIs(everyday_sense_phrase_unanchored(SKILL, normalized_phrase(message)), withdrawn)

    def test_only_the_two_hyphenated_names_score_as_bare_tokens(self) -> None:
        prepared = _prepare_definition(_definition(SKILL))
        held_back = _WHOLE_PHRASE_ONLY_TRIGGER_TOKENS[SKILL]
        self.assertEqual(set(prepared.trigger_tokens) - held_back, {"spend-gate", "pre-spend"})


INTERVENTIONS = (
    "the api returned http 402 payment required, go ahead and pay it",
    "pay the 402 challenge on this paid data endpoint",
    "buy this monitor for me on amazon with my saved card",
    "book the hotel for friday and pay with my card",
    "set a spending cap before the agent buys anything",
    "the checkout page timed out after you clicked pay, did the payment go through",
    "transfer 40 dollars from my wallet to the vendor",
    "before you spend any money on my behalf show me the total and fees",
    "place the order for the replacement keyboard and pay for it",
    "go ahead and buy it for me if the total stays under 50 dollars",
)
# Each is a sense of the gate's words that moves no money for the person:
# everyday money talk, a branch checkout, payment software being built, a
# payment someone else asked about, and an ordering of things.
NOT_SPENDING = (
    "I paid the rent yesterday",
    "what's my budget for lunch",
    "pay attention to the error log after the deploy",
    "we spend too much time in meetings every week",
    "git checkout main and pull the latest changes",
    "I lost my wallet at the train station",
    "transfer the repo ownership to the new github org",
    "the government raised the spending cap again this year",
    "the photo fell out from my wallet on the bus",
    "our wallet transfer feature needs integration tests",
    "why is the paid endpoint in our api returning 401",
    "return http 402 from our api when the trial expires",
    "a customer emailed asking did the payment go through",
    "support ticket: did the charge go through for order 5512",
    "place the order of the slides so the demo comes first",
    "complete the purchase flow redesign in figma by friday",
    "which model provider costs less for this workload",
)
OWNERS = (
    ("open the staging checkout url, click login and fill the form", "browser-operator"),
    ("triage the payment failure reports from support this week", "feedback-triage"),
    ("budget vs actual for q3 marketing spend", "finance-analysis"),
    ("what was our budget variance last month", "finance-analysis"),
)


if __name__ == "__main__":
    unittest.main()
