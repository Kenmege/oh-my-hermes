"""Public show-reel contract; checks data, not a renderer execution claim."""
from __future__ import annotations

import unittest
import tomllib
from pathlib import Path

from _local_package import load_local_package

load_local_package()
from omh.skills.catalog import builtin_definitions, omh_skill_display_name


class ShowReelSkillTests(unittest.TestCase):
    def test_original_end_to_end_method_has_observed_delivery_gates(self):
        definitions = {d.name: d for d in builtin_definitions()}
        self.assertIn("show-reel", definitions)
        d = definitions["show-reel"]
        self.assertEqual(omh_skill_display_name(d.name), "omh-show-reel")
        self.assertTrue(d.progressive_disclosure)
        self.assertEqual(d.hermes_role, "operator")
        self.assertEqual(len(d.procedure_steps), 10)
        self.assertEqual(d.expected_outputs[-1], "show_reel_delivery_manifest/v1")
        method = " ".join((*d.opening_steps, *d.quality_bar, *d.safety_rules, *d.final_checklist, *(s.instruction for s in d.procedure_steps)))
        for marker in ("at least 3", "keep/fix/rebuild", "weakest", "delivery-size", "codec", "color", "filesize", "concept", "source package", "HOLD"):
            self.assertIn(marker, method)
        project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
        self.assertEqual(project["project"]["dependencies"], [])


if __name__ == "__main__":
    unittest.main()
