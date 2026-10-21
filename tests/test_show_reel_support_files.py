"""Declared text support is data, not imported/executed companion code."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()


class SupportFilesTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("omh.skills.support_files"), "missing declared support-file seam")
        return importlib.import_module("omh.skills.support_files")

    def test_closed_utf8_inventory_notice_hashes_and_nonexecution(self):
        api = self.api()
        with patch("subprocess.Popen", side_effect=AssertionError("hidden process")), patch("socket.socket", side_effect=AssertionError("hidden network")):
            templates = api.builtin_skill_support_templates()
        self.assertTrue(templates)
        mapping = {t.relative_path: t.content for t in templates}
        self.assertEqual(len(mapping), len(templates))
        self.assertTrue(all(t.skill_name == "show-reel" and t.kind == "support" for t in templates))
        self.assertIn("Copyright (c) 2026 Minho Hwang (revfactory)", mapping["toolkit/LICENSE.upstream"])
        manifest = json.loads(mapping["toolkit/upstream-manifest.json"])
        self.assertNotIn("upstream-manifest.json", manifest["files"])
        self.assertEqual(set(manifest["files"]), {p.removeprefix("toolkit/") for p in mapping} - {"upstream-manifest.json"})
        for p, entry in manifest["files"].items():
            self.assertEqual(hashlib.sha256(mapping["toolkit/"+p].encode()).hexdigest(), entry["local_sha256"])
            if entry["classification"] == "modified_derivative":
                self.assertIn("64521220a2f8e91cb9894400c48c15b7f45205d6", mapping["toolkit/"+p])
                self.assertIn("Minho Hwang", mapping["toolkit/"+p])

    def test_support_bytes_remain_lf_under_windows_autocrlf(self):
        import subprocess
        api = self.api()
        root = Path(__file__).resolve().parents[1]
        paths = [f"{prefix}/{t.relative_path.removeprefix('toolkit/')}" for t in api.builtin_skill_support_templates() for prefix in ("src/skills/show_reel_toolkit", "skills/omh-show-reel/toolkit", "agent-skills/omh-show-reel/toolkit")]
        result = subprocess.run(["git", "check-attr", "eol", "--", *paths], cwd=root, capture_output=True, text=True, timeout=20, check=True)
        self.assertEqual(len(result.stdout.splitlines()), len(paths))
        self.assertTrue(all(line.endswith(": eol: lf") for line in result.stdout.splitlines()), result.stdout)

    def test_tap_docs_gate_counts_and_detects_missing_actual_support(self):
        from omh.commands.docs import tap_skills_check_payload
        from omh.skills.skill_record import project, skill_record
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            files = project(skill_record("show-reel"), "hermes")
            for rel, content in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            relative = "omh-show-reel/toolkit/scripts/timeline.py"
            (root / relative).unlink()
            report = tap_skills_check_payload(root)
            self.assertIn(relative, report["missing"], "core tap byte gate ignores actual support")

    def test_paths_casefold_collisions_and_symlinks_refuse_before_materializing(self):
        api = self.api()
        for name in ["/toolkit/x.py", "C:/x.py", "toolkit/../x.py", "toolkit/./x.py", "toolkit//x.py", "toolkit\\x.py", "toolkit/x.py:ads", "toolkit/CON.py", "toolkit/nul.txt", "toolkit/a\x00.py", "toolkit/a\n.py", "toolkit/a.bin", "references/hidden.py", "toolkit/COM1/x.py", "toolkit/LPT9.py", "toolkit/trail./x.py"]:
            with self.subTest(path=repr(name)), self.assertRaises(ValueError):
                api.validate_support_templates([api.SkillSupportTemplate("show-reel", name, "x")])
        with self.assertRaises(ValueError):
            api.validate_support_templates([api.SkillSupportTemplate("show-reel", "toolkit/a.py", "x"), api.SkillSupportTemplate("show-reel", "toolkit/A.py", "y")])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "toolkit").symlink_to(root / "elsewhere", target_is_directory=True)
            with self.assertRaises(ValueError):
                api.checked_support_path(root, "toolkit/a.py")
            self.assertFalse((root / "elsewhere").exists())


if __name__ == "__main__":
    unittest.main()
