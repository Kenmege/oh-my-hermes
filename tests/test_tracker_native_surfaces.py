"""Contracts for the tracker skills that point at Hermes' own surfaces.

Hermes records each session's tokens and cost in its own `state.db`, has its
own `/compress` with a preview form, defers tools behind tool_search, lists
its own delegate children and kanban workers, stops its own work with its own
commands, and manages skills with its own tools. These skills name those
surfaces as the source, and keep three things OMH-side:

* a usage figure is observed only from what Hermes recorded -- no row or no
  tokens is not_observed, never zero -- and provider billing stays apart;
* OMH never compresses a context and never ends a host process; the person's
  own command does, and a host action is observed only from Hermes' reply;
* an OMH-managed skill is never edited in place, since OMH regenerates it.

The runtime half (`host_usage` in `run_efficiency_report/v1`) is pinned in
`tests/test_run_efficiency.py`.
"""

from __future__ import annotations

import re
import unittest

from _local_package import load_local_package

load_local_package()
from omh.plugin_bundle.omh.native_tools import native_tool_presence_clause
from omh.skills.catalog import builtin_definitions, omh_skill_display_name
from omh.skills.packaging import builtin_skill_templates
from omh.skills.render import agent_skill_templates

# Feature-surface skills that state their own Why and Do-Not-Use.
OWN_WHY_FEATURE_SURFACE_SKILLS = ("run-efficiency", "ops-observability-card", "toolbelt-readiness")

# The generic lines the feature-surface shape and the operator default fall
# back to; a skill that states its own carries none of them.
GENERIC_WHY_FRAGMENTS = (
    "so Hermes users can ask for this workflow in chat",
    "work explicit, evidence-backed, and inside the Hermes/executor boundary",
)
GENERIC_DO_NOT_USE = (
    "The request is already handled by a narrower explicit skill with stronger evidence.",
    "The user asks OMH to secretly run external platforms, connectors, schedulers, file exports, or runtime agents.",
    "The only safe answer is to ask for missing authority, credentials, target, or observed evidence first.",
    "The request is casual chat, a status-only acknowledgement, or another workflow has stronger routing evidence.",
    "The user needs implementation, review, CI, merge, or external publishing evidence that has not been delegated or observed.",
)

COMPRESS_CHECKPOINT = (
    "On Hermes, bind the summarization checkpoint to Hermes' own compaction: render the must-keep classes as one "
    "short focus topic and hand the person `/compress <focus>` to run when the checkpoint is due. OMH never "
    "compresses, and a compaction is observed only from Hermes' own reply to that command."
)
COMPRESS_PREVIEW = (
    "On Hermes, offer `/compress --preview <focus>` only in the classic CLI or on a messaging platform, and count it "
    "as a preview only when the reply says `Preview — no changes made.`: a modern TUI whose `/compress` does not "
    "parse the flag compresses for real with the flag in its focus."
)
CANCEL_NO_HOST_KILL = (
    "Never end, kill, or signal a host process, background job, or delegation from this workflow, through the "
    "terminal or any other tool (on Hermes, `process_manage` included); that stop is the person's own host command."
)
CANCEL_HOST_STOPS = (
    "Hand host work to the person's own stop command; on Hermes `/stop` (background processes, plus background "
    "delegations in the classic CLI; the running turn on a messaging platform), `/loop stop`, `/goal pause` or "
    "`/goal clear`, `/heartbeat clear`, `/pause` (all new work on a gateway)."
)
CANCEL_REPORTING = (
    "Report an OMH clear as done only from its command result, and a host stop as requested until the host's own "
    "reply to it is seen."
)
SKILL_MANAGED_EDIT = (
    "Never edit or delete an OMH-managed skill (an `omh-*` or `ulw-*` name) with `skill_manage`: OMH generates its "
    "body from its catalog, so the change does not carry into the next generation; put the lesson in a separately "
    "named skill instead."
)

# Clauses stated in the Hermes body and, for a projected skill, in the
# portable body too: a non-Hermes host loads the portable copy, and the rule
# holds there in the same words.
CLAUSES = {
    "run-efficiency": (
        "observed only when Hermes recorded tokens for it; no session row or no tokens stays not_observed, never zero",
        "Host usage is Hermes' recorded accounting for the named session, observed only when it recorded tokens; "
        "provider billing and cron stay not_observed.",
    ),
    "ops-observability-card": (
        "labeled observed: a conversation with no recorded tokens is not_observed, never zero, and provider billing "
        "stays not_observed.",
    ),
    "context-budget-review": (COMPRESS_CHECKPOINT, COMPRESS_PREVIEW),
    "toolbelt-readiness": (
        f"On Hermes, count a native tool as present when {native_tool_presence_clause('it')}: a tool deferred behind "
        "tool_search is reached through `tool_call` and is never listed as missing, and a Hermes version number never "
        "decides presence.",
    ),
    "running-work-board": (
        "On Hermes, `delegate_task` children and kanban workers of this conversation come from `omh_hud` (its "
        "`subagents` rows and `kanban` block, read from Hermes' own records) beside the OMH units, each labelled by "
        "source",
        "If `omh_hud` is not available, say delegate children and kanban workers were not read rather than implying "
        "none are running.",
    ),
    "cancel": (CANCEL_NO_HOST_KILL, CANCEL_HOST_STOPS, CANCEL_REPORTING),
    "skill": (SKILL_MANAGED_EDIT,),
    "capability-toggle": (
        "The user wants one skill hidden on one messaging platform or everywhere; that is Hermes' own hermes skills "
        "config, an interactive command the person runs in a terminal, and OMH writes no Hermes config.",
    ),
}

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_NEGATED = re.compile(r"\b(?:never|not|no)\b", re.IGNORECASE)


def _sentences(content: str) -> list[str]:
    return [sentence for line in content.splitlines() for sentence in _SENTENCE_END.split(line)]


def _projections(name: str) -> dict[str, str]:
    """The Hermes body and, when the skill is projected, the portable body."""
    hermes = {template.name: template.content for template in builtin_skill_templates()}
    portable = {template.name: template.content for template in agent_skill_templates()}
    bodies = {"hermes": hermes[name]}
    if omh_skill_display_name(name) in portable:
        bodies["portable"] = portable[omh_skill_display_name(name)]
    return bodies


class OwnWhyAndDoNotUseTests(unittest.TestCase):
    def test_the_tracker_skills_carry_none_of_the_generic_lines(self) -> None:
        definitions = {definition.name: definition for definition in builtin_definitions()}
        for name in (*OWN_WHY_FEATURE_SURFACE_SKILLS, "cancel", "skill"):
            definition = definitions[name]
            with self.subTest(skill=name):
                for fragment in GENERIC_WHY_FRAGMENTS:
                    self.assertNotIn(fragment, definition.why_this_exists)
                self.assertTrue(definition.why_this_exists.startswith(f"`{name}` exists "))
                self.assertEqual(set(GENERIC_DO_NOT_USE) & set(definition.do_not_use_when), set())


class TrackerClauseTests(unittest.TestCase):
    def test_each_clause_is_in_every_projection_of_its_skill(self) -> None:
        for name, clauses in CLAUSES.items():
            for projection, content in _projections(name).items():
                for clause in clauses:
                    with self.subTest(skill=name, projection=projection, clause=clause[:60]):
                        self.assertIn(clause, content)

    def test_the_skill_skill_ships_to_hermes_only(self) -> None:
        # Its tools and the managed-skill boundary are Hermes' own; the
        # portable projection leaves it out rather than restating them.
        self.assertEqual(set(_projections("skill")), {"hermes"})
        self.assertEqual(set(_projections("capability-toggle")), {"hermes"})


class HostActionTests(unittest.TestCase):
    def test_every_compress_preview_sentence_carries_the_surface_restriction(self) -> None:
        # A preview the modern TUI does not parse compresses for real, so the
        # flag is never offered without the surfaces that honour it.
        for projection, content in _projections("context-budget-review").items():
            naming = [sentence for sentence in _sentences(content) if "--preview" in sentence]
            self.assertTrue(naming, "no sentence names --preview; the check below would be vacuous")
            for sentence in naming:
                with self.subTest(projection=projection, sentence=sentence[:80]):
                    self.assertIn("only in the classic CLI or on a messaging platform", sentence)

    def test_no_cancel_sentence_has_omh_end_a_host_process(self) -> None:
        # Every instruction that names ending or killing a process says OMH
        # does not, leaves the process untouched, or hands it to the person's
        # own command. An example prompt is the user's words, not an
        # instruction, so it is not read here.
        ending = re.compile(r"\b(?:kill|kills|end|ends|terminate|signal)\b", re.IGNORECASE)
        process = re.compile(r"\bprocess(?:es)?\b|`process_manage`", re.IGNORECASE)
        refused = re.compile(r"\b(?:never|not)\b|person's own|untouched", re.IGNORECASE)
        for projection, content in _projections("cancel").items():
            naming = [
                sentence
                for sentence in _sentences(content)
                if not sentence.startswith("- Prompt:") and ending.search(sentence) and process.search(sentence)
            ]
            self.assertTrue(naming, "no sentence names ending a process; the check below would be vacuous")
            for sentence in naming:
                with self.subTest(projection=projection, sentence=sentence[:80]):
                    self.assertRegex(sentence, refused, f"lets OMH end a host process: {sentence}")

    def test_no_skill_sentence_edits_an_omh_managed_skill_in_place(self) -> None:
        for projection, content in _projections("skill").items():
            naming = [sentence for sentence in _sentences(content) if "OMH-managed" in sentence]
            self.assertTrue(naming, "no sentence names an OMH-managed skill; the check below would be vacuous")
            for sentence in naming:
                with self.subTest(projection=projection, sentence=sentence[:80]):
                    self.assertTrue(
                        _NEGATED.search(sentence) or "out of hand edits" in sentence or "instead" in sentence,
                        f"edits an OMH-managed skill in place: {sentence}",
                    )


if __name__ == "__main__":
    unittest.main()
