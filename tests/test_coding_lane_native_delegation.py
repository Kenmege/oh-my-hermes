"""The coding lane's clauses about Hermes' own delegation, board, and bundled skills.

Each locked sentence below is the one that keeps a host mechanism from being
read as more than it is: a schema check read as validation, a lane's branch
read as merged work, a board receipt read as merge evidence, a bundled skill
read as a second method. A rewrite that drops one fails here rather than
shipping quietly. The texts that name a Hermes-only skill, command, or setting
must stay out of the Agent Skills projection, and none may gate on a Hermes
version number: a version says what a release shipped, not what this session
was given.
"""

from __future__ import annotations

import re
import unittest

from omh.skills.catalog import builtin_definitions
from omh.skills.catalog_portable import PORTABLE_REFERENCE_PATHS
from omh.skills.catalog_types import HermesReferenceLine
from omh.skills.packaging import builtin_skill_reference_templates, builtin_skill_templates
from omh.skills.render import agent_skill_reference_templates, agent_skill_templates

PRESENCE_CLAUSE = "is in your tool list or tool_search finds it"
_VERSION_GATE = re.compile(r"\b0\.\d+\.\d+\b|\bv20\d\d\.\d+")

# Hermes-only references this change adds, by skill.
NEW_HERMES_REFERENCES = {
    ("ultrawork", "references/native-delegation.md"),
    ("plan", "references/hermes-plan-mode.md"),
    ("ask", "references/cli-advisors.md"),
    ("decision-prototype", "references/spike-composition.md"),
    ("app-debugging", "references/debugging-skill-composition.md"),
}


def _flat(text: str) -> str:
    """Collapse line wrapping, so a locked sentence survives a reflow of its paragraph."""
    return " ".join(text.split())


def _reference(skill: str, path: str) -> str:
    for template in builtin_skill_reference_templates():
        if (template.skill_name, template.relative_path) == (skill, path):
            return _flat(template.content)
    raise AssertionError(f"{skill} ships no {path}")


def _body(skill: str) -> str:
    return {template.name: template.content for template in builtin_skill_templates()}[skill]


def _portable_body(display_name: str) -> str:
    return {template.name: template.content for template in agent_skill_templates()}[display_name]


def _definition(skill: str):
    return next(definition for definition in builtin_definitions() if definition.name == skill)


class HermesOnlyReferencesStayOnHermes(unittest.TestCase):
    def test_each_new_reference_ships_to_hermes_and_never_to_other_hosts(self) -> None:
        for skill, path in sorted(NEW_HERMES_REFERENCES):
            with self.subTest(skill=skill, path=path):
                self.assertTrue(_reference(skill, path))
                self.assertNotIn(f"{skill}/{path}", PORTABLE_REFERENCE_PATHS)
        shipped = {(template.skill_name, template.relative_path) for template in agent_skill_reference_templates()}
        self.assertFalse({path for _, path in NEW_HERMES_REFERENCES} & {path for _, path in shipped})

    def test_no_new_text_gates_on_a_hermes_version(self) -> None:
        for skill, path in sorted(NEW_HERMES_REFERENCES):
            with self.subTest(skill=skill, path=path):
                self.assertIsNone(_VERSION_GATE.search(_reference(skill, path)))
        for path in ("references/kanban-lane.md", "references/team-lane.md", "references/file-ownership-manifest.md"):
            with self.subTest(path=path):
                self.assertIsNone(_VERSION_GATE.search(_reference("ultrawork", path)))


class NativeDelegation(unittest.TestCase):
    def test_the_body_names_the_reference_on_exactly_one_line(self) -> None:
        naming = [line for line in _body("ultrawork").splitlines() if "references/native-delegation.md" in line]
        self.assertEqual(
            naming,
            ["- Before a `delegate_task` dispatch, load `references/native-delegation.md`: the answer schema, "
             "worktree-isolated lanes, and the one-shot budget."],
        )

    def test_every_mechanism_is_gated_on_presence(self) -> None:
        self.assertIn(
            f"Each applies only while `delegate_task` {PRESENCE_CLAUSE}.",
            _reference("ultrawork", "references/native-delegation.md"),
        )

    def test_schema_valid_is_read_only_from_a_same_turn_result(self) -> None:
        # The wording `code-review`'s native review contract already ships (#2105).
        self.assertIn(
            "Its `schema_valid` record reaches you only when the call returns its results in the same turn; "
            "a result that arrives later as a completion message carries none, so check that summary against "
            "the schema yourself and never read `completed` as validated.",
            _reference("ultrawork", "references/native-delegation.md"),
        )

    def test_a_matched_shape_is_not_a_checked_claim(self) -> None:
        text = _reference("ultrawork", "references/native-delegation.md")
        self.assertIn("A matched shape is a checked shape, not a checked claim.", text)
        self.assertIn(
            "A lane's answer stays the lane's report whatever the host checked; the run is complete only on "
            "the verification fan-in this session runs",
            text,
        )

    def test_a_team_entry_is_dispatched_unchanged(self) -> None:
        self.assertIn(
            "An `omh_team` entry is the exception to all of this section: dispatch it unchanged "
            "(`references/team-lane.md`), because its acceptance is the check OMH runs.",
            _reference("ultrawork", "references/native-delegation.md"),
        )

    def test_isolation_is_the_users_setting_and_integration_is_a_merge(self) -> None:
        text = _reference("ultrawork", "references/native-delegation.md")
        for clause in (
            "`delegation.worktree_isolation` is the user's Hermes setting and is off unless they turned it on. "
            "OMH never writes it: when lanes would collide in one checkout, propose the one-line change for the "
            "user to apply.",
            "each lane gets `<repo>/.worktrees/subagent-<id>` on branch `hermes-subagent/subagent-<id>`, created "
            "from the commit your checkout is on. Anywhere else the host skips isolation without saying so, and "
            "the lanes write into the shared checkout.",
            "Integration is a merge you perform: merge each lane's branch in dependency order, then run the "
            "verification fan-in on the merged tree. A check before that merge tests a tree without the lane's work.",
            "A completion message carries no `worktree` entry.",
            "Isolation moves a collision from the working tree to the merge; it does not remove it.",
        ):
            with self.subTest(clause=clause[:60]):
                self.assertIn(clause, text)

    def test_a_spent_one_shot_budget_runs_the_rest_inline(self) -> None:
        text = _reference("ultrawork", "references/native-delegation.md")
        for clause in (
            "When `delegate_task` refuses with an error that names `delegation.oneshot_max_children`, "
            "this run's budget is spent:",
            "Do not retry the call, and do not split it smaller to fit under the cap.",
            "Run each remaining lane yourself in this session, one at a time in dependency order, each with its "
            "own `VERIFY`.",
            "The rule follows the refusal, not a version: a host without the cap never sends it.",
        ):
            with self.subTest(clause=clause[:60]):
                self.assertIn(clause, text)

    def test_a_team_check_waits_for_the_merge_under_isolation(self) -> None:
        self.assertIn(
            "A check run here then tests a tree without that lane's work, and a pass would accept a lane nobody "
            "checked. Merge each returned helper's branch into this workspace before `team_reconcile`;",
            _reference("ultrawork", "references/team-lane.md"),
        )

    def test_the_manifest_is_compared_per_lane_branch(self) -> None:
        self.assertIn(
            "Write the manifest exactly as strictly, and run the comparison above per lane against that lane's "
            "branch, which is where its changes are, before merging any of them.",
            _reference("ultrawork", "references/file-ownership-manifest.md"),
        )


class BoardFields(unittest.TestCase):
    def setUp(self) -> None:
        self.text = _reference("ultrawork", "references/kanban-lane.md")

    def test_board_records_are_extra_evidence(self) -> None:
        self.assertIn(
            "What the board records for them is the host's record: one more piece of evidence beside OMH's own "
            "checks, never a replacement for them.",
            self.text,
        )

    def test_a_completion_contract_is_not_merge_evidence(self) -> None:
        for clause in (
            "the board refuses `kanban_complete` until every check the repository requires has passed on that "
            "PR's current head commit.",
            "A repository with no required checks never satisfies the contract, so a lane there omits the field, "
            "as does a lane that publishes nothing.",
            "the closing brief still reads the PR head again before it says a change landed, and a passed contract "
            "is neither review nor merge evidence.",
        ):
            with self.subTest(clause=clause[:60]):
                self.assertIn(clause, self.text)

    def test_a_judge_agreeing_is_still_a_worker_report(self) -> None:
        self.assertIn(
            "The judge's agreement is a model's reading of the card, so the row is still `worker reported done`.",
            self.text,
        )

    def test_review_goes_to_the_review_column_and_its_bundled_skill(self) -> None:
        self.assertIn(
            "Unless the operator turned review dispatch off, the dispatcher then opens a review run on the card "
            "with Hermes' bundled `sdlc-review` loaded beside the card's own skills,",
            self.text,
        )

    def test_parking_a_card_is_gated_on_presence_and_names_who_wakes_it(self) -> None:
        self.assertIn(
            f"In a worker, when `kanban_schedule` {PRESENCE_CLAUSE}, a lane that must wait for a time or an outside "
            "event parks its card with it and puts the wake condition in `reason`.",
            self.text,
        )
        self.assertIn(
            "the tool sets no timer, so the lane's `body` names who unblocks it. Without the tool, the worker blocks "
            "the card with the same reason (`kanban_block`).",
            self.text,
        )

    def test_a_worker_is_told_its_delegation_budget_may_be_capped(self) -> None:
        self.assertIn(
            "A worker runs as a one-shot session, so a host that caps one-shot delegation refuses its "
            "`delegate_task` calls past that budget;",
            self.text,
        )


class ContextFileLoading(unittest.TestCase):
    """Portable: a fact about how one more reader loads the file, not a Hermes-only tool."""

    CLAUSES = (
        "**Hermes Agent loads one project file type per session.** It takes the first type that has a non-empty "
        "file and reads no other:",
        "It follows no pointer written inside the file it loaded, so a `.hermes.md` that only points elsewhere is "
        "all Hermes reads,",
        "Each directory from the repository root down to the working directory adds its own file, root first,",
        "**An `AGENTS.override.md` replaces the `AGENTS.md` beside it** for Hermes Agent, in that one directory.",
        "The inventory lists every override, and a drift check reads the override rather than the committed file.",
    )

    def test_the_method_states_hermes_loading_rules(self) -> None:
        text = _reference("agent-instructions", "references/instruction-file-method.md")
        for clause in self.CLAUSES:
            with self.subTest(clause=clause[:60]):
                self.assertIn(clause, text)

    def test_the_portable_copy_carries_the_same_rules(self) -> None:
        portable = {
            (template.skill_name, template.relative_path): _flat(template.content)
            for template in agent_skill_reference_templates()
        }[("omh-agent-instructions", "references/instruction-file-method.md")]
        for clause in self.CLAUSES:
            with self.subTest(clause=clause[:60]):
                self.assertIn(clause, portable)


class BundledSkillComposition(unittest.TestCase):
    """Hermes-only lines: each names a bundled skill or command no other host has."""

    HERMES_LINES = {
        "plan": ("references/hermes-plan-mode.md", "omh-plan", "/plan"),
        "app-debugging": ("references/debugging-skill-composition.md", "omh-app-debugging", "systematic-debugging"),
        "ask": ("references/cli-advisors.md", "omh-ask", "claude-code"),
    }

    def test_each_line_is_hermes_only_and_names_its_reference(self) -> None:
        for skill, (path, display, bundled) in self.HERMES_LINES.items():
            with self.subTest(skill=skill):
                marked = [line for line in _definition(skill).quality_bar if isinstance(line, HermesReferenceLine)]
                self.assertEqual([line.reference for line in marked], [path])
                self.assertIn(str(marked[0]), _body(skill))
                portable = _portable_body(display)
                self.assertNotIn(path, portable)
                self.assertNotIn(f"`{bundled}`", portable)

    def test_the_spike_pointer_is_in_the_hermes_compact_body_only(self) -> None:
        line = (
            "When Hermes' bundled `spike` skill is loaded for the same question, build the way it says and keep "
            "these records: `references/spike-composition.md`."
        )
        self.assertIn(line, _body("decision-prototype"))
        portable = _portable_body("omh-decision-prototype")
        self.assertNotIn("spike-composition.md", portable)
        self.assertNotIn("`spike`", portable)

    def test_plan_mode_turn_belongs_to_the_host_and_its_file_is_only_a_draft(self) -> None:
        text = _reference("plan", "references/hermes-plan-mode.md")
        for clause in (
            "In that turn, follow its instructions: write the file, reply, and stop.",
            "The file existing is not acceptance, and neither is `/plan`'s offer to carry it out: nothing is "
            "handed off or started until the user accepts.",
            "`/plan` asks for complete code inside its steps, and this skill keeps code bodies out of a plan:",
        ):
            with self.subTest(clause=clause[:60]):
                self.assertIn(clause, text)

    def test_spike_verdicts_map_onto_the_receipt_without_widening_the_boundary(self) -> None:
        text = _reference("decision-prototype", "references/spike-composition.md")
        for clause in (
            "| `PARTIAL` | `inconclusive` |",
            "A `spike` table of several questions is several decisions: take the one planning is blocked on, or HOLD",
            "A `spikes/` directory left in the repository is a write outside the boundary unless the user approved "
            "it, `discarded` is reported only after the cleanup is observed,",
            "A verdict from a single run, or from output nobody observed, is not `observed`.",
        ):
            with self.subTest(clause=clause[:60]):
                self.assertIn(clause, text)

    def test_a_bundled_debugging_skill_drives_the_method_and_this_skill_keeps_the_records(self) -> None:
        text = _reference("app-debugging", "references/debugging-skill-composition.md")
        for clause in (
            "When one of them is loaded beside `app-debugging`, follow its method; this skill adds its records and "
            "its gates, never a second method.",
            "| Implementation | `fix_handoff/v1`, still blocked until the reproduction reads observed, then "
            "`observed_fix_verification/v1` |",
            "For a hypothesis about ordering or timing, a bug that does not appear under the debugger is still open;",
        ):
            with self.subTest(clause=clause[:60]):
                self.assertIn(clause, text)

    def test_a_cli_advisor_is_never_a_default_and_never_edits(self) -> None:
        text = _reference("ask", "references/cli-advisors.md")
        for clause in (
            "None of these CLIs is the default, and this table recommends none of them.",
            "An advisor answers; it does not edit.",
            "a changed file means the run did more than advise, and you say so.",
            "record no review, verification, or approval from it.",
        ):
            with self.subTest(clause=clause[:60]):
                self.assertIn(clause, text)
        for skill in ("claude-code", "codex", "opencode"):
            with self.subTest(skill=skill):
                self.assertIn(f"`{skill}`", text)


if __name__ == "__main__":
    unittest.main()
