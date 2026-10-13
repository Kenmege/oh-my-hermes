"""What OMH's research skills say about the skills Hermes bundles.

Hermes ships `grounded-citations` (a source ledger with a `verify` command),
`blocked-page-recovery`, `competitor-news-monitor`, `llm-wiki`, `arxiv`, and
`pdf`. The research skills used to restate what those do as prose rules
("cite the source behind each claim"), which nothing could observe. They now
route to the bundled skill when it is present and keep the evidence boundary
OMH adds: a citation check is reported from a run or as `not_observed`, and it
is never called fact verification.

These pin the sentences that carry that boundary, because the digest fixture
only says that a body moved and a `docs ... --check` gate cannot see a body
change at all.
"""

from __future__ import annotations

import unittest

from omh.capabilities.skills import skill_capabilities
from omh.skills.catalog import installable_skill_definitions
from omh.skills.render import agent_skill_templates, builtin_skill_templates
from omh.workflows.paper_learning import PAPER_LEARNING_SOURCE_STATES
from omh.workflows.source_finder import (
    SOURCE_FINDER_ACQUISITION_STATES,
    SOURCE_FINDER_OBSERVATION_PROVENANCE,
    SOURCE_FINDER_SOURCE_KINDS,
)

CITATION_SKILLS = ("web-research", "research-brief", "research")
TOUCHED_SKILLS = (*CITATION_SKILLS, "research-department", "source-finder", "paper-learning")
BUNDLED_SKILLS_NAMED_AS_OWNERS = ("grounded-citations", "competitor-news-monitor", "arxiv")


class ResearchCitationLedgerTests(unittest.TestCase):
    hermes: dict[str, str]
    portable: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.hermes = {template.name: template.content for template in builtin_skill_templates()}
        cls.portable = {template.name: template.content for template in agent_skill_templates()}

    def test_sources_are_registered_at_retrieval_time_when_the_ledger_is_present(self) -> None:
        for name in CITATION_SKILLS:
            with self.subTest(skill=name):
                body = self.hermes[name]
                self.assertIn("When Hermes' bundled `grounded-citations` skill is in the skill index", body)
                self.assertIn("in its ledger when it is retrieved (`scripts/sources.py add`)", body)
                self.assertIn("Without the skill,", body)

    def test_the_citation_check_is_a_run_or_not_observed(self) -> None:
        for name in CITATION_SKILLS:
            with self.subTest(skill=name):
                body = self.hermes[name]
                self.assertIn("the exit status of `sources.py verify <draft>", body)
                self.assertIn("`0` is a pass and any other status is not", body)
                self.assertIn("carries no verbatim quote", body)
                self.assertIn("`not_observed`", body)

    def test_a_citation_check_is_never_called_fact_verification(self) -> None:
        self.assertIn(
            "it does not test whether a claim is true, so a passing citation check is never reported as fact verification",
            self.hermes["web-research"],
        )
        self.assertIn("it is a citation check, not fact verification", self.hermes["research-brief"])
        self.assertIn(
            "It is a citation check, not fact verification, and it never stands in for the contested-claim gate",
            self.hermes["research"],
        )

    def test_research_brief_rows_carry_the_ledger_id_and_the_quote(self) -> None:
        body = self.hermes["research-brief"]
        self.assertIn("carry two more fields on every evidence row: the ledger id, and the verbatim quote", body)
        self.assertIn("`sources.py quote <id> --text <quote> --from <page text file>`", body)
        self.assertIn("`sources.py verify <draft> --evidence`", body)

    def test_research_lanes_share_one_ledger_and_only_the_parent_resets_it(self) -> None:
        body = self.hermes["research"]
        self.assertIn("All lanes register into one ledger", body)
        self.assertIn("the parent resets it once before the first wave and no lane resets it", body)

    def test_what_omh_adds_is_still_in_the_web_research_bar(self) -> None:
        # The ledger sits beside OMH's own rules; it replaces none of them.
        body = self.hermes["web-research"]
        for kept in (
            "Name the question, freshness window, and version or jurisdiction scope before retrieving.",
            "Cite the source behind each claim and mark it official, practitioner, or unattributed.",
            "Bind every as-of claim to an eligible temporal_source_receipt/v1",
            "Stop at the answer: one retrieval round settles a lookup",
            "The ledger id sits beside the source class and the retrieval date and replaces neither.",
        ):
            self.assertIn(kept, body)

    def test_web_research_tries_blocked_page_recovery_once_and_only_hermes_names_the_skill(self) -> None:
        rule = (
            "If a source fails with HTTP 403, HTTP 429, a paywall, or a WAF or bot wall, "
            "load Hermes' `blocked-page-recovery` skill once for that source when it is available"
        )
        self.assertIn(rule, self.hermes["web-research"])
        self.assertIn("never as the live page", self.hermes["web-research"])
        portable = self.portable["omh-web-research"]
        self.assertNotIn("blocked-page-recovery", portable)
        self.assertIn("use the host's blocked-page recovery capability once for that source", portable)
        self.assertIn("never as the live page", portable)

    def test_research_department_carries_the_recurring_watch_rules(self) -> None:
        body = self.hermes["research-department"]
        for rule in (
            "write the watchlist and the materiality threshold down once, before the first run, and reuse them unchanged",
            "A source the run could not read is unknown coverage for that period and is named as a gap; "
            "it is never reported as a quiet period or as nothing new.",
            "Keep one cutoff per source and move it only for a source the run covered.",
            "Count by the underlying event, not by article or URL",
        ):
            self.assertIn(rule, body)

    def test_research_department_routes_to_bundled_skills_and_keeps_cron_observed_only(self) -> None:
        body = self.hermes["research-department"]
        self.assertIn(
            "when the watch area is a set of named companies and Hermes' bundled `competitor-news-monitor` skill "
            "is in the skill index, run the watch through that skill",
            body,
        )
        self.assertIn("the bundled `llm-wiki` skill is in the skill index, offer it as the store", body)
        self.assertIn(
            "A schedule stays a prepared job description in the plan and OMH creates no job. In a Hermes session "
            "the job is created with the `cronjob_manage` tool, and it counts as created only from that tool's "
            "observed result or a cron listing.",
            body,
        )
        # The existing boundary stays: a prepared plan is never cron creation.
        self.assertIn("Do not claim web retrieval, synthesis-tool query, knowledge-store write, cron creation", body)

    def test_source_finder_records_an_arxiv_result_as_an_observed_link(self) -> None:
        body = self.hermes["source-finder"]
        self.assertIn("For `paper` candidates, when Hermes' bundled `arxiv` skill is in the skill index", body)
        self.assertIn("record each entry it returns as `link_observed` with `hermes_tool` provenance", body)
        self.assertIn("a paper candidate stays `candidate_prepared` until a link is observed another way", body)
        # The states the line names are the workflow's own vocabulary.
        self.assertIn("paper", SOURCE_FINDER_SOURCE_KINDS)
        self.assertIn("link_observed", SOURCE_FINDER_ACQUISITION_STATES)
        self.assertIn("candidate_prepared", SOURCE_FINDER_ACQUISITION_STATES)
        self.assertIn("hermes_tool", SOURCE_FINDER_OBSERVATION_PROVENANCE)

    def test_paper_learning_maps_acquisition_to_source_state(self) -> None:
        body = self.hermes["paper-learning"]
        self.assertIn(
            "An entry that Hermes' bundled `arxiv` skill returned (search or id lookup) is `metadata_only` "
            "until paper text is read.",
            body,
        )
        self.assertIn(
            "Text that the bundled `pdf` skill's `pdf_read.py <file> --text` run returned is "
            "`file_text_extraction_observed` for the pages that came back with text",
            body,
        )
        self.assertIn("take the chunks from `long-document-reading`'s page ranges", body)
        self.assertIn("metadata_only", PAPER_LEARNING_SOURCE_STATES)
        self.assertIn("file_text_extraction_observed", PAPER_LEARNING_SOURCE_STATES)

    def test_a_bundled_skill_declines_a_request_only_where_it_is_present(self) -> None:
        """A do-not-use line that names a bundled skill is conditional on it.

        The portable projection carries the same lines to hosts that ship none
        of these skills, where an unconditional line would decline a request
        and point at nothing.
        """
        named = [
            (definition.name, statement)
            for definition in installable_skill_definitions()
            if definition.name in TOUCHED_SKILLS
            for statement in definition.do_not_use_when
            if any(f"`{skill}`" in statement for skill in BUNDLED_SKILLS_NAMED_AS_OWNERS)
        ]
        self.assertEqual(
            sorted({(owner, skill) for owner, statement in named for skill in BUNDLED_SKILLS_NAMED_AS_OWNERS if f"`{skill}`" in statement}),
            [
                ("research-department", "arxiv"),
                ("research-department", "competitor-news-monitor"),
                ("web-research", "arxiv"),
                ("web-research", "grounded-citations"),
            ],
        )
        for owner, statement in named:
            with self.subTest(skill=owner, statement=statement[:60]):
                self.assertTrue(statement.endswith("skill is in the skill index; load that skill."), statement)

    def test_the_capability_rows_do_not_carry_the_bundled_skill_detail(self) -> None:
        """The capability payload shows the head of each quality bar, not this detail.

        Every bundled-skill line sits past the lines a capability row carries,
        so the per-request payload did not grow with the bodies.
        """
        rows = {str(row.get("id")): row for row in skill_capabilities()}
        for name in TOUCHED_SKILLS:
            with self.subTest(skill=name):
                row = rows[name]
                shown = " ".join([*row["quality_bar"], str(row["handoff_policy"]), *row["do_not_use_when"]])
                self.assertNotIn("bundled", shown)
                self.assertIn("bundled", self.hermes[name])


if __name__ == "__main__":
    unittest.main()
