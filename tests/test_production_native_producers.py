"""Contracts for the production skills that read Hermes' native producers.

Hermes bundles document producers (`powerpoint`, `pdf`, `xlsx`, `docx`), an
image tool, browser tools, an exploratory web-QA skill (`dogfood`), monitor-mode
cron, and a meeting action-item skill. The production skills now name them, and
each one is evidence beside OMH's own gate, never the verdict. These pin:

* a producer result is judged by its own field, not its exit code: the render
  and recalculation scripts exit 0 with `"rendered": false` or
  `"recalculated": false`, and that leaves the rung not observed;
* each rung a body names is a rung of the materials QA ladder for that format;
* a native tool a body relies on for a decision is named only in a sentence
  carrying the shared presence rule, never behind a version;
* a dogfood report, a silent monitor tick, a clean console read, and a
  meeting-notes extraction are each stated as less than a pass;
* `deliverable-package` and `ultraqa` state their own Why and Do-Not-Use.
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
from omh.workflows.materials import FORMAT_QA_LADDER

PRODUCTION_SKILLS = (
    "materials-package",
    "deliverable-package",
    "report-package",
    "img-summary",
    "visual-qa",
    "ultraqa",
    "deploy-and-monitor",
    "operating-rhythm",
    "meeting-brief",
)

DOCUMENT_QA_RULE = (
    "On Hermes, a file's QA rung is observed only from the bundled document skill's result for that exact file, "
    "judged by its field, not its exit code: `\"rendered\": false` or `\"recalculated\": false` exits 0 yet leaves "
    "the rung not observed, blocked on the missing tools it names."
)
DOCUMENT_VERDICT_RULE = (
    "Images that exist are not a render pass until each one has been inspected, and a producer's `\"ok\": true` "
    "is never the QA verdict."
)
DOCX_RULE = "`docx_validate.py` checks package health, never rendering"

# The rung each bundled document skill can show, by the ladder's own format
# key. Every rung named here must be a rung of that format's ladder.
PRODUCER_RUNGS = {
    "pptx": ("slide_count", "render_screenshot"),
    "pdf": ("page_count", "render_preview"),
    "xlsx": ("sheet_schema", "formula_recalculation"),
    "docx": ("section_structure", "required_clause_check", "page_count"),
}

NATIVE_CLAUSES = {
    "materials-package": (DOCUMENT_QA_RULE, DOCUMENT_VERDICT_RULE, DOCX_RULE),
    "deliverable-package": (
        DOCUMENT_QA_RULE,
        DOCUMENT_VERDICT_RULE,
        DOCX_RULE,
        "A file is `generated` only from the producer's read-back of the written file, `approved` only from the "
        "person's answer, and `attached` only from the destination's own read-back, never from a reply that names "
        "the file.",
    ),
    "report-package": (
        "On Hermes, that tool is the bundled `powerpoint` skill, and a deck built from the outline counts as "
        "generated only from its read-back (`pptx_read.py --outline`).",
        "The deck counts as render-checked only from a `pptx_render.py` result reading `\"rendered\": true` whose "
        "slide images were each inspected; `\"rendered\": false` exits 0 yet leaves render QA not observed.",
    ),
    "img-summary": (
        "On Hermes, report image_generation_capability/v1 as `connected` when "
        f"{native_tool_presence_clause('image_generate', code=True)}, since Hermes offers that tool only while an "
        "image backend is configured, and as `prompt_only` otherwise; never infer it from a Hermes version, a "
        "provider key, or a model name.",
        "A supplied-image edit goes through that tool only when its own description offers to edit an existing "
        "image; when it says text-to-image only, the edit stays a prompt card.",
        "a generated-image claim still needs visual_observation/v1, and visual QA and delivery stay separate.",
    ),
    "visual-qa": (
        f"On Hermes, when {native_tool_presence_clause('browser_vision', code=True)}, the session takes its own "
        "captures: each render_capture_manifest/v1 entry records the `screenshot_path` it returned, the URL, the "
        "viewport width the page reported, and the target revision, and an entry missing any of the four is not "
        "evidence; otherwise captures come from the wrapper, executor, or user.",
        "a width that reads back other than the one requested is not that viewport, and without `browser_cdp` every "
        "required width the browser does not already report stays a named blocker.",
        "a clean console read is console evidence only, never network, accessibility, or visual evidence.",
        "its issues and screenshots enter this verdict only as observed findings bound to the target lineage, and a "
        "report with no issues is not a PASS.",
    ),
    "ultraqa": (
        f"On a web app, when {native_tool_presence_clause('browser_vision', code=True)}, run each hostile scenario "
        "through the session's browser tools: read `browser_console` after every navigation and interaction, and "
        "keep a failing scenario's `screenshot_path` and console errors as its observed evidence.",
    ),
    "deploy-and-monitor": (
        "On Hermes, a watchlist that must keep running after this turn is prepared as a native blueprint through "
        "`automation-blueprint` (a cron job whose `monitor` source is a health URL or a script whose output carries "
        "no timestamps), activated by the user or Hermes and never by OMH.",
        "The first monitor tick only sets a baseline, a tick that stays silent means the source output did not "
        "change and never that the release is healthy, and a source that failed is an error, not a change",
    ),
    "operating-rhythm": (
        "Record each action item with its outcome, owner, due date, dependency, acceptance condition, and source "
        "reference, marking an owner or date the notes do not state as unknown",
        "its list is a reading of those notes, so every item keeps its source reference and stays unaccepted until "
        "the notes or the owner confirm it",
    ),
    "meeting-brief": (
        "Give the record template's action-item rows outcome, owner, due date, dependency, acceptance condition, "
        "and source columns, each marked unknown until the meeting record fills it.",
    ),
}

# The portable projection is what a non-Hermes host loads. `ultraqa` carries a
# reviewed portable quality bar, so its browser rule is stated there without
# Hermes tool names; every other clause here is shared by both projections.
PORTABLE_ONLY_CLAUSES = {
    "ultraqa": (
        "On a web app, read the browser console after every navigation and interaction, and keep a failing "
        "scenario's screenshot and console errors as its observed evidence.",
    ),
}
HERMES_ONLY_CLAUSES = {"ultraqa"}

# Native tools a body relies on for a decision. A sentence naming one carries
# the presence rule for that tool, so the decision is made on what this
# session was given, never on a version.
PRESENCE_GATED_TOOLS = ("image_generate", "browser_vision", "browser_cdp")

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _sentences(content: str) -> list[str]:
    return [sentence for line in content.splitlines() for sentence in _SENTENCE_END.split(line)]


def _hermes() -> dict[str, str]:
    return {template.name: template.content for template in builtin_skill_templates()}


def _portable() -> dict[str, str]:
    return {template.name: template.content for template in agent_skill_templates()}


def _production_bodies() -> list[tuple[str, str]]:
    hermes, portable = _hermes(), _portable()
    return [
        *((f"hermes:{name}", hermes[name]) for name in PRODUCTION_SKILLS),
        *((f"portable:{name}", portable[omh_skill_display_name(name)]) for name in PRODUCTION_SKILLS),
    ]


class ProducerClauseTests(unittest.TestCase):
    def test_each_production_body_states_its_native_clause(self) -> None:
        hermes, portable = _hermes(), _portable()
        for name, clauses in NATIVE_CLAUSES.items():
            for clause in clauses:
                with self.subTest(skill=name, projection="hermes", clause=clause[:60]):
                    self.assertIn(clause, hermes[name])
                if name in HERMES_ONLY_CLAUSES:
                    continue
                with self.subTest(skill=name, projection="portable", clause=clause[:60]):
                    self.assertIn(clause, portable[omh_skill_display_name(name)])
        for name, clauses in PORTABLE_ONLY_CLAUSES.items():
            for clause in clauses:
                with self.subTest(skill=name, projection="portable", clause=clause[:60]):
                    self.assertIn(clause, portable[omh_skill_display_name(name)])
                    self.assertNotIn(clause, hermes[name])

    def test_the_portable_ultraqa_names_no_hermes_tool(self) -> None:
        body = _portable()[omh_skill_display_name("ultraqa")]
        for tool in ("browser_console", "browser_vision", "screenshot_path"):
            with self.subTest(tool=tool):
                self.assertNotIn(tool, body)

    def test_a_false_producer_result_is_never_stated_as_observed(self) -> None:
        # `pptx_render.py`, `pdf_page_image.py`, and `xlsx_recalc.py` exit 0
        # when their renderer is missing; only the field says so. Every
        # sentence that names the false field must leave the rung not observed.
        mentions = [
            (label, sentence)
            for label, content in _production_bodies()
            for sentence in _sentences(content)
            if '"rendered": false' in sentence or '"recalculated": false' in sentence
        ]
        self.assertTrue(mentions, "no production body names a false producer result; the check would be vacuous")
        for label, sentence in mentions:
            with self.subTest(body=label, sentence=sentence[:80]):
                self.assertIn("exits 0", sentence)
                self.assertIn("not observed", sentence)

    def test_every_named_rung_is_a_rung_of_its_format_ladder(self) -> None:
        materials = _hermes()["materials-package"]
        for material_format, rungs in PRODUCER_RUNGS.items():
            for rung in rungs:
                with self.subTest(format=material_format, rung=rung):
                    self.assertIn(rung, FORMAT_QA_LADDER[material_format])
                    self.assertIn(f"`{rung}`", materials)


class PresenceRuleTests(unittest.TestCase):
    def test_a_presence_gated_tool_is_named_only_in_a_sentence_carrying_its_rule(self) -> None:
        bodies = [
            *((f"hermes:{name}", content) for name, content in _hermes().items()),
            *((f"portable:{name}", content) for name, content in _portable().items()),
        ]
        for tool in PRESENCE_GATED_TOOLS:
            clause = native_tool_presence_clause(tool, code=True)
            naming = [
                (label, sentence)
                for label, content in bodies
                for sentence in _sentences(content)
                if f"`{tool}`" in sentence
            ]
            self.assertTrue(naming, f"no body names `{tool}`; the check below would be vacuous")
            for label, sentence in naming:
                with self.subTest(tool=tool, body=label, sentence=sentence[:80]):
                    self.assertIn(clause, sentence)


class OwnBoundaryTests(unittest.TestCase):
    def test_deliverable_package_and_ultraqa_state_their_own_why_and_do_not_use(self) -> None:
        definitions = {definition.name: definition for definition in builtin_definitions()}
        generic_lines = {
            # The feature-surface shape's generic lines.
            "The request is already handled by a narrower explicit skill with stronger evidence.",
            "The user asks OMH to secretly run external platforms, connectors, schedulers, file exports, or runtime agents.",
            "The only safe answer is to ask for missing authority, credentials, target, or observed evidence first.",
            # The catalog's fallback lines.
            "The request is casual chat, a status-only acknowledgement, or another workflow has stronger routing evidence.",
            "The user needs implementation, review, CI, merge, or external publishing evidence that has not been delegated or observed.",
        }
        for name in ("deliverable-package", "ultraqa"):
            with self.subTest(skill=name):
                definition = definitions[name]
                self.assertTrue(definition.why_this_exists.startswith(f"`{name}` exists because "))
                self.assertEqual(generic_lines & set(definition.do_not_use_when), set())


if __name__ == "__main__":
    unittest.main()
