"""Contract for the shared plan-ambition rules in `plan` and `ralplan`.

Both planners carry `_PLAN_AMBITION_BAR`: the goal beyond the request and the
access an executor needs to reach it (altitude), the raw material and an outside
judge for every verification step (depth), and the deliverable as verbs with
other fields brought in as practitioner actions rather than voices (range). The
completion checklist of each planner asks for the outside judge.

Every assertion re-derives from the producers, never from the generated
`skills/` or `agent-skills/` trees, so dropping a line from the catalog fails
here while a stale generated file still sits on disk. `ralplan`'s portable
override replaces the Hermes quality bar wholesale, so the portable bodies are
checked on their own.

The lines sit after each bar's first four items, so the capability rows
(`_bounded_list(quality_bar, 4)`) do not move; the last test pins that.
"""

from __future__ import annotations

import unittest

from omh.skills.catalog import builtin_definitions
from omh.skills.render import agent_skill_templates, workflow_skill_from_definition

AMBITION_TOKENS = (
    "goal beyond the request",
    "as its own tasks, the access the executor needs",
    "raw material the work starts from",
    "outside judge",
    "recorded as an evidence gap",
    "as the verbs it must accomplish",
    "never an instruction to write or speak like one",
)


def _definition(name: str):
    return next(item for item in builtin_definitions() if item.name == name)


def _hermes_body(name: str) -> str:
    definition = _definition(name)
    return workflow_skill_from_definition(definition, definition.name).content


def _portable_body(name: str) -> str:
    return next(item.content for item in agent_skill_templates() if item.name == name).replace(
        "\n", " "
    )


class PlanAmbitionRulesTests(unittest.TestCase):
    def test_plan_body_carries_the_ambition_rules(self) -> None:
        body = _hermes_body("plan")
        for token in AMBITION_TOKENS:
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_ralplan_body_carries_the_ambition_rules(self) -> None:
        body = _hermes_body("ralplan")
        for token in AMBITION_TOKENS:
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_portable_projection_carries_the_ambition_rules(self) -> None:
        for name in ("omh-plan", "ulw-plan"):
            body = _portable_body(name)
            for token in AMBITION_TOKENS:
                with self.subTest(skill=name, token=token):
                    self.assertIn(token, body)

    def test_completion_checklists_require_an_outside_judge(self) -> None:
        for name in ("plan", "ralplan"):
            with self.subTest(skill=name):
                self.assertTrue(
                    any("outside judge" in line for line in _definition(name).final_checklist),
                    f"{name} can finish without naming an outside judge",
                )

    def test_capability_rows_do_not_move(self) -> None:
        for name in ("plan", "ralplan"):
            head = " ".join(str(line) for line in _definition(name).quality_bar[:4])
            for token in AMBITION_TOKENS:
                with self.subTest(skill=name, token=token):
                    self.assertNotIn(token, head)


if __name__ == "__main__":
    unittest.main()
