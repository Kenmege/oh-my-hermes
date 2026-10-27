"""Offline contracts for the sprite-forge skill definition and closed toolkit inventory."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()
ROOT = Path(__file__).resolve().parents[1]
TOOLKIT = ROOT / "src/skills/sprite_forge_toolkit"


class SpriteForgeDefinitionTests(unittest.TestCase):
    def api(self):
        return importlib.import_module("omh.skills.sprite_forge_skill")

    def test_definition_fields(self):
        definition = self.api().sprite_forge_definition()
        self.assertIs(definition, self.api().DEFINITION)
        self.assertEqual(definition.name, "sprite-forge")
        self.assertEqual(definition.hermes_role, "operator")
        self.assertEqual(definition.category, "analysis")
        self.assertEqual(definition.phase, "sprite-production")
        self.assertEqual(definition.quality_tier, "execution-evidence-gated")
        self.assertTrue(definition.progressive_disclosure)
        self.assertTrue(definition.description.removeprefix("[omh] ").startswith("Character art to animate in a game"))
        self.assertEqual(set(definition.triggers), {"sprite-forge", "sprite sheet", "pixel sprite", "pixel art character",
                                                     "game sprite frames", "2d sprite animation"})
        self.assertTrue(all(t.isascii() for t in definition.triggers))
        deferrals = " ".join(definition.do_not_use_when)
        for sibling in ("`show-reel`", "`visual-qa`", "`img-summary`"):
            self.assertIn(sibling, deferrals)
        rules = " ".join(definition.safety_rules + definition.quality_bar)
        for needle in ("0.08", "0.05", "consent", "OMH generates no images"):
            self.assertIn(needle, rules)
        for step in definition.procedure_steps:
            self.assertTrue(set(step.input_refs) <= set(definition.required_inputs))
            self.assertTrue(set(step.output_refs) <= set(definition.expected_outputs))
            self.assertTrue(set(step.check_ids) <= {c.check_id for c in definition.procedure_checks})

    def test_registered_in_catalog(self):
        from omh.skills.catalog import installable_skill_names
        self.assertIn("sprite-forge", installable_skill_names())

    def test_references_present(self):
        references = self.api().sprite_forge_reference_templates()
        self.assertEqual({r.relative_path for r in references},
                         {"references/execution-and-qa.md", "references/character-art-direction.md"})
        text = {r.relative_path: r.content for r in references}
        self.assertIn("y=430", text["references/character-art-direction.md"])
        self.assertIn("REGENERATE", text["references/execution-and-qa.md"])

    def test_support_templates_validate_digests(self):
        api = self.api()
        with patch("subprocess.Popen", side_effect=AssertionError("hidden process")), \
                patch("socket.socket", side_effect=AssertionError("hidden network")):
            templates = api.sprite_forge_support_templates()
        self.assertEqual({t.relative_path for t in templates}, {"toolkit/" + p for p in api.TOOLKIT_RESOURCES})
        self.assertTrue(all(t.skill_name == "sprite-forge" for t in templates))
        contents = {t.relative_path.removeprefix("toolkit/"): t.content for t in templates}
        manifest = json.loads(contents["upstream-manifest.json"])
        self.assertEqual(manifest["schema"], "omh_sprite_forge_provenance/v1")
        self.assertEqual(set(manifest["files"]), set(api.TOOLKIT_RESOURCES) - {"upstream-manifest.json"})
        for name, entry in manifest["files"].items():
            self.assertEqual(entry["local_sha256"], hashlib.sha256(contents[name].encode()).hexdigest())
            self.assertEqual(entry["classification"], "original_omh")
        self.assertEqual(api.sprite_forge_provenance_manifest()["files"], manifest["files"])
        from omh.skills.support_files import builtin_support_owners
        self.assertIn("sprite-forge", builtin_support_owners())

    def test_tampered_or_expanded_provenance_is_rejected(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            kit = root / "sprite_forge_toolkit"
            for name in api.TOOLKIT_RESOURCES:
                target = kit / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((TOOLKIT / name).read_bytes())
            with patch.object(api, "files", return_value=root):
                manifest_path = kit / "upstream-manifest.json"
                original = manifest_path.read_text()
                expanded = json.loads(original)
                expanded["files"]["scripts/hidden.py"] = {"local_sha256": "0" * 64}
                manifest_path.write_text(json.dumps(expanded))
                with self.assertRaisesRegex(ValueError, "inventory"):
                    api.sprite_forge_support_templates()
                manifest_path.write_text(original)
                (kit / "scripts/forge.py").write_text("raise RuntimeError('not executed')\n")
                with self.assertRaisesRegex(ValueError, "digest mismatch"):
                    api.sprite_forge_support_templates()

    def test_resource_factories_never_execute_companion_or_import_pillow(self):
        script = r'''
import builtins
from pathlib import Path
import sys
from unittest.mock import patch
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
kit = root / 'src/skills/sprite_forge_toolkit'
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {'PIL', 'numpy'}:
        raise AssertionError('image library import during resource preparation')
    return original_import(name, *args, **kwargs)
def audit(event, args):
    if event == 'exec' and args and Path(args[0].co_filename).is_relative_to(kit):
        raise AssertionError('companion executed during preparation')
sys.addaudithook(audit)
with patch('builtins.__import__', side_effect=guarded_import), patch('subprocess.Popen', side_effect=AssertionError('spawn')):
    from omh.skills.sprite_forge_skill import sprite_forge_support_templates, sprite_forge_reference_templates
    assert sprite_forge_support_templates()
    assert sprite_forge_reference_templates()
print('inert resources: PASS')
'''
        result = subprocess.run([sys.executable, "-I", "-S", "-c", script, str(ROOT)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("inert resources: PASS", result.stdout)

    def test_compact_body_retains_execution_pointer(self):
        from omh.skills.render import workflow_skill_from_definition
        definition = self.api().DEFINITION
        rendered = workflow_skill_from_definition(definition, definition.name).content
        self.assertIn("toolkit/README.md", rendered)
        self.assertIn("references/execution-and-qa.md", rendered)
        self.assertLess(len(rendered.encode()), 9000)


if __name__ == "__main__":
    unittest.main()
