"""Contracts for the memory and learning skills that read Hermes' own loop.

Hermes holds some memory and skill writes for the person's approval under
`pending/`, its background review writes skills and memory after a turn, every
skill write lands in an audit ledger, and it runs one external memory provider
at a time. The memory and learning skills now read those surfaces as observed
evidence. These pin:

* the seven feature-surface skills state their own Why and Do-Not-Use;
* each skill's native-loop clause, as one whole sentence the model reads, in
  every projection the skill ships in;
* Hermes' `write_approval` is Hermes' gate on Hermes' writes: no body routes an
  `omh_memory` capture through a person or proposes turning the gate on;
* a write result carrying `staged: true` is never reported as landed;
* no body offers use or load counts as a reason to prune, archive, or retire a
  skill;
* memory-sync takes Hermes' memory caps from the plugin bundle's reader rather
  than restating them.
"""

from __future__ import annotations

import re
import unittest
from unittest import mock

from _local_package import load_local_package

load_local_package()
from omh.plugin_bundle.omh.hermes_memory import HERMES_MEMORY_FILES
from omh.skills import render
from omh.skills.catalog import builtin_definitions, omh_skill_display_name
from omh.skills.catalog_portable import portable_skill_names
from omh.skills.catalog_types import _feature_surface_skill
from omh.skills.packaging import builtin_skill_templates
from omh.skills.render import agent_skill_templates

# Feature-surface skills that state their own Why and Do-Not-Use in this change.
OWN_WHY_FEATURE_SURFACE_SKILLS = (
    "memory-sync",
    "external-connector-readiness",
    "prompt-import-readiness",
    "instinct-ledger",
    "skill-scout",
    "skill-health",
    "workflow-learning",
)

# Every skill whose body this change wrote native-loop text into.
NATIVE_LOOP_SKILLS = (*OWN_WHY_FEATURE_SURFACE_SKILLS, "rules-distill")

MEMORY_SYNC_PENDING = (
    "Read the directory whatever `memory.write_approval` says now, since a record stays there until the person "
    "decides it. A pending write is not in `MEMORY.md` or `USER.md` yet: review it as a proposal, never as a "
    "remembered claim, and leave approving or rejecting it to the person (`/memory approve <id>` or "
    "`/memory reject <id>`)."
)
MEMORY_SYNC_STAGED = (
    "When the native write returns `staged: true` with a `pending_id`, nothing was saved even though `success` is "
    "true: Hermes' own write approval, the user's setting, is holding it, so report it as staged and waiting for the "
    "person's `/memory approve <id>`, never as an applied write, and do not repeat the write."
)
MEMORY_SYNC_DECLINED = (
    "When Hermes asks the person before the write and they decline, the error result saved nothing: report that "
    "entry as declined and do not retry it."
)
# Owner directive: OMH memory needs no operator step. Hermes' gate never
# reaches the model's own capture.
MEMORY_SYNC_CAPTURE_NOT_GATED = (
    "Hermes' `write_approval` gates only Hermes' own memory and skill writes, never this capture: OMH's own policy "
    "approves a safe capture at once (`approved_auto_safe`), and a capture waits for review only when its result "
    "names a `review_reason`."
)
MEMORY_SYNC_HANDS_OFF = (
    "A write Hermes holds in `pending/memory/` is the person's to decide: OMH never approves, rejects, edits, or "
    "deletes one, and never proposes turning `memory.write_approval` on or off."
)
PROVIDER_SLOT = (
    "On Hermes, the memory provider is one slot: Hermes runs its built-in memory and no more than one external provider, "
    "the one `memory.provider` names, so adopting another provider (honcho or mem0, for example) where that key reads "
    "`omh` displaces OMH's provider. OMH's per-turn recall and dreaming reminders then stop, and the `omh_memory` tool "
    "stays registered but refuses to recall or write records (`session_lens_unavailable`), because the session lens it "
    "reads comes from the provider. State that displacement in the posture before adoption, and keep the switch a "
    "proposed `memory.provider` diff the user applies."
)
INSTALL_SCANS = (
    "A scan counts only when its output was seen: the plugin scan does not run with `plugins.scan_on_install` off, "
    "and the OSV check covers known malware only and lets the server start when its lookup fails, so a quiet install "
    "or launch is not a clean result."
)
IMPORT_PREVIEW = (
    "Seed `prompt_source_inventory/v1` from the user's `hermes import-agent claude-code --dry-run` (or `codex`) "
    "preview, which stops before importing anything: it lists Claude Code's `commands/*.md` as skipped with no direct "
    "Hermes equivalent, the list this card converts, and it reads no Codex prompt files at all, so those come from the "
    "files the user names."
)
SLASH_PRECEDENCE = (
    "In the Hermes CLI a slash name resolves to a built-in command first, then a quick command, a plugin command, a "
    "skill bundle, and a skill, so `slash_command_collision_report/v1` checks each candidate name against all five in "
    "that order."
)
SCOUT_TRUST = (
    "Keep the trust level each shows (`builtin`, `trusted`, or `community`, highest first) beside the candidate as its "
    "source's standing, never as a review of its content, and record a scan verdict (`safe`, `caution`, or "
    "`dangerous`) only from install or `hermes skills audit` output that was seen, so a candidate nobody installed has "
    "none."
)
# Owner directive: skill breadth is the product; usage never decides removal.
HEALTH_NO_USAGE_PRUNE = (
    "Never present use, view, or load counts, `.usage.json` included, as a health, prune, archive, or retire signal: "
    "a rarely used skill is not an unhealthy one, and no health card recommends removing, archiving, or pruning a "
    "skill for being used little."
)
HEALTH_LEDGER = (
    "An empty ledger is unknown, not clean, because `skills.ledger: false` turns it off."
)
LEARNING_UNREVIEWED = (
    "Read Hermes' skill audit ledger read-only (`hermes curator ledger`, or `skills/.curator_ledger.jsonl` under the "
    "active profile's Hermes home) and report each entry whose `actor` is `agent` or `curator` (Hermes' curator or its "
    "background review) that no store-route record (`omh learning store-routes`) or approved `skill_update_candidate` "
    "accounts for as `unreviewed_native_write`."
)
LEARNING_LANDED = (
    "Mark an approved `skill_update_candidate` landed only from a `skill_manage` result for that skill that succeeded "
    "without `staged: true`, or a ledger entry for that skill dated after the approval; a staged result waits in "
    "`/skills pending` for the person and has not landed."
)
LEARNING_MEMORY_GAP = (
    "The ledger records skill writes only: a memory write by Hermes' background review is visible only when Hermes "
    "staged it under `pending/memory/` with `origin: background_review`, and is `not_observed` otherwise."
)
INSTINCT_LEDGER = (
    "an instinct that matches a ledger entry no store route accounts for is reported as `unreviewed_native_write`, "
    "never counted as repeated evidence for the instinct."
)
DISTILL_LANDING = (
    "On Hermes, when the destination is the user's own Hermes skills, an approved candidate lands through "
    "`skill_manage`: report it landed only from a result that succeeded without `staged: true`, with the matching "
    "skill audit ledger entry as its record while the ledger is on, and report a staged write as waiting in "
    "`/skills pending` for the person's own `/skills approve <id>` under their `skills.write_approval` setting."
)
DISTILL_CURATOR_REPORT = (
    "it records which skills the curator consolidated, archived, or created in that run, and one report is one "
    "observation, never repeated evidence by itself."
)

NATIVE_LOOP_CLAUSES = {
    "memory-sync": (
        MEMORY_SYNC_PENDING,
        MEMORY_SYNC_STAGED,
        MEMORY_SYNC_DECLINED,
        MEMORY_SYNC_CAPTURE_NOT_GATED,
        MEMORY_SYNC_HANDS_OFF,
    ),
    "external-connector-readiness": (PROVIDER_SLOT, INSTALL_SCANS),
    "prompt-import-readiness": (IMPORT_PREVIEW, SLASH_PRECEDENCE),
    "skill-scout": (SCOUT_TRUST,),
    "skill-health": (HEALTH_NO_USAGE_PRUNE, HEALTH_LEDGER),
    "workflow-learning": (LEARNING_UNREVIEWED, LEARNING_LANDED, LEARNING_MEMORY_GAP),
    "instinct-ledger": (INSTINCT_LEDGER,),
    "rules-distill": (DISTILL_LANDING, DISTILL_CURATOR_REPORT),
}

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_NEGATED = re.compile(r"\b(?:never|not|no)\b", re.IGNORECASE)
# One clause of a sentence; a negation elsewhere in the sentence does not
# govern it ("OMH never approves one, and proposes turning it on").
_CLAUSE_END = re.compile(r"[;,]|: ")
# A clause that would turn Hermes' approval gate on.
# The span may cross a dot: the gate's own key is `memory.write_approval`.
_ENABLES_GATE = re.compile(
    r"\b(?:enables?|enabling|turn(?:s|ing)?\b.*?\bon|switch(?:es|ing)?\b.*?\bon)\b", re.IGNORECASE
)
# A sentence that ties a use or load count to removing a skill.
_USAGE_COUNT = re.compile(r"\.usage\.json|\b(?:use|usage|view|load)\s+counts?\b", re.IGNORECASE)
_REMOVAL = re.compile(r"\b(?:prune|pruning|retire|retiring|archive|archiving|remove|removing)\b", re.IGNORECASE)
# The operator verb that approves an OMH memory candidate by hand.
_HUMAN_CAPTURE_APPROVAL = "omh memory approve"


def _sentences(content: str) -> list[str]:
    return [sentence for line in content.splitlines() for sentence in _SENTENCE_END.split(line)]


def _negated_before(text: str, pattern: re.Pattern[str]) -> bool:
    """A negation comes before the first match: "never present X as Y", not "present X as Y, not Z"."""
    match = pattern.search(text)
    return match is not None and _NEGATED.search(text[: match.start()]) is not None


def _bodies() -> list[tuple[str, str, str]]:
    """(projection, display name, content) for every Hermes and portable body."""
    return [
        *(("hermes", omh_skill_display_name(template.name), template.content) for template in builtin_skill_templates()),
        *(("portable", template.name, template.content) for template in agent_skill_templates()),
    ]


def _shape(**override: object):
    return _feature_surface_skill(
        "skill-health",
        "Probe description for the shape.",
        ("probe trigger",),
        "Use when probing the shape.",
        category="operations",
        phase="probe",
        next_action="probe_next_action",
        boundary="A probe card is not evidence.",
        good_prompt="probe good prompt",
        bad_prompt="probe bad prompt",
        **override,
    )


class OwnWhyTests(unittest.TestCase):
    def test_the_memory_and_learning_skills_carry_none_of_the_generic_lines(self) -> None:
        generic = _shape()
        generic_lines = set(generic.do_not_use_when)
        definitions = {definition.name: definition for definition in builtin_definitions()}
        for name in OWN_WHY_FEATURE_SURFACE_SKILLS:
            with self.subTest(skill=name):
                definition = definitions[name]
                self.assertNotIn("so Hermes users can ask for this workflow in chat", definition.why_this_exists)
                self.assertTrue(definition.why_this_exists.startswith(f"`{name}` exists because "))
                self.assertEqual(generic_lines & set(definition.do_not_use_when), set())


class NativeLoopClauseTests(unittest.TestCase):
    def test_each_body_states_its_native_loop_clause_in_every_projection_it_ships_in(self) -> None:
        hermes = {template.name: template.content for template in builtin_skill_templates()}
        portable = {template.name: template.content for template in agent_skill_templates()}
        shipped_portable = set(portable_skill_names())
        for name, clauses in NATIVE_LOOP_CLAUSES.items():
            display = omh_skill_display_name(name)
            for clause in clauses:
                with self.subTest(skill=name, clause=clause[:60]):
                    self.assertIn(clause, hermes[name])
                    if display in shipped_portable:
                        self.assertIn(clause, portable[display])

    def test_the_provider_slot_replaces_the_unusable_understatement(self) -> None:
        for projection, name, content in _bodies():
            with self.subTest(body=f"{projection}:{name}"):
                self.assertNotIn("never makes OMH memory unusable", content)


class MemoryCapsTests(unittest.TestCase):
    def test_memory_sync_states_the_caps_the_bundle_reader_falls_back_to(self) -> None:
        body = render.memory_sync_skill().content
        for label, key, default in HERMES_MEMORY_FILES:
            with self.subTest(file=label):
                self.assertIn(f"`memory.{key}`", body)
                self.assertIn(f"{default:,} characters for {label}", body)

    def test_the_caps_follow_the_bundle_reader_rather_than_a_copy(self) -> None:
        moved = (("MEMORY.md", "memory_char_limit", 3100), ("USER.md", "user_char_limit", 1900))
        with mock.patch.object(render, "HERMES_MEMORY_FILES", moved):
            body = render.memory_sync_skill().content
        self.assertIn("3,100 characters for MEMORY.md and 1,900 characters for USER.md", body)
        self.assertNotIn("2,200", body)


class OwnerDirectiveTests(unittest.TestCase):
    def test_no_body_proposes_turning_hermes_write_approval_on(self) -> None:
        mentions = [
            (projection, name, sentence)
            for projection, name, content in _bodies()
            for sentence in _sentences(content)
            if "write_approval" in sentence
        ]
        self.assertTrue(mentions, "no body names write_approval; the check below would be vacuous")
        gate_clauses = [
            (projection, name, clause)
            for projection, name, sentence in mentions
            for clause in _CLAUSE_END.split(sentence)
            if "write_approval" in clause and _ENABLES_GATE.search(clause)
        ]
        self.assertTrue(gate_clauses, "no clause turns the gate on or off; the check below would be vacuous")
        for projection, name, clause in gate_clauses:
            with self.subTest(body=f"{projection}:{name}", clause=clause[:80]):
                self.assertTrue(_negated_before(clause, _ENABLES_GATE), clause)

    def test_no_native_loop_body_routes_a_capture_through_a_person(self) -> None:
        names = {omh_skill_display_name(name) for name in NATIVE_LOOP_SKILLS}
        for projection, name, content in _bodies():
            if name in names:
                with self.subTest(body=f"{projection}:{name}"):
                    self.assertNotIn(_HUMAN_CAPTURE_APPROVAL, content)

    def test_no_body_offers_a_use_count_as_a_reason_to_remove_a_skill(self) -> None:
        mentions = [
            (projection, name, sentence)
            for projection, name, content in _bodies()
            for sentence in _sentences(content)
            if _USAGE_COUNT.search(sentence) and _REMOVAL.search(sentence)
        ]
        self.assertTrue(mentions, "no body ties a count to removal; the check below would be vacuous")
        for projection, name, sentence in mentions:
            with self.subTest(body=f"{projection}:{name}", sentence=sentence[:80]):
                self.assertTrue(_negated_before(sentence, _USAGE_COUNT), sentence)

    def test_a_staged_write_is_never_reported_as_landed(self) -> None:
        mentions = [
            (projection, name, sentence)
            for projection, name, content in _bodies()
            for sentence in _sentences(content)
            if "`staged: true`" in sentence
        ]
        self.assertTrue(mentions, "no body names a staged result; the check below would be vacuous")
        for projection, name, sentence in mentions:
            with self.subTest(body=f"{projection}:{name}", sentence=sentence[:80]):
                self.assertRegex(sentence, re.compile(r"\b(?:never|not|nothing was saved|without)\b"))


if __name__ == "__main__":
    unittest.main()
