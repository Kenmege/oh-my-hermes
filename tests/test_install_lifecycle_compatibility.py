"""Adjacent lifecycle boundaries, independent of the audit regressions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _cli_harness import run_cli
from _local_package import load_local_package
from _platform_support import requires_symlinks

load_local_package()

from omh.install.installer import uninstall_skill_pack  # noqa: E402
from omh.paths import OmhPaths  # noqa: E402


class LifecycleCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="lifecycle-compatibility-")
        self.addCleanup(temporary.cleanup)
        # Resolve the root: Windows hands out an 8.3 alias (RUNNER~1) while OMH
        # reports long-name paths, and kept_paths entries are compared whole.
        self.root = Path(temporary.name).resolve()
        self.hermes = self.root / "hermes"
        self.store = self.root / "store"
        self.hermes.mkdir()
        self.store.mkdir()
        self.sentinel = self.store / "user-record.json"
        self.sentinel.write_text('{"keep": true}\n', encoding="utf-8")

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def binding(self, home, store):
        self.write(home / "config.yaml", "plugins:\n  entries:\n    omh:\n      settings:\n"
                   f"        omh_home: {store}\n")

    def uninstall(self, home, *flags):
        code, stdout, stderr = run_cli(["--hermes-home", str(home), "--omh-home", str(self.store),
                                       "uninstall", "--json", *flags])
        return code, json.loads(stdout), stderr

    def test_unreadable_sibling_inventory_fails_closed_before_teardown(self):
        bot = self.hermes / "profiles" / "bot"
        self.binding(bot, self.store)
        bad = self.hermes / "profiles" / "bad" / "config.yaml"
        bad.parent.mkdir()
        bad.write_bytes(b"plugins: \xff\n")
        before = (bot / "config.yaml").read_bytes()
        code, payload, stderr = self.uninstall(bot)
        self.assertNotEqual(code, 0, (payload, stderr))
        self.assertTrue(self.sentinel.is_file())
        self.assertEqual((bot / "config.yaml").read_bytes(), before)
        self.assertIn("unreadable", str(payload).lower())

    def test_unknown_dotenv_reference_protects_store(self):
        bot = self.hermes / "profiles" / "bot"
        self.binding(bot, self.store)
        self.write(self.hermes / ".env", "OMH_HOME=${UNKNOWN_STORE}\n")
        code, payload, stderr = self.uninstall(bot, "--remove-files")
        self.assertNotEqual(code, 0, (payload, stderr))
        self.assertTrue(self.sentinel.is_file())
        self.assertNotIn(str(self.store), payload["removed_paths"])

    def test_shared_store_keeps_command_without_keep_command_flag(self):
        bot = self.hermes / "profiles" / "bot"
        self.binding(bot, self.store)
        self.binding(self.hermes, self.store)
        venv = self.root / "command" / "venv"
        python = venv / "bin" / "python"
        self.write(python, "owned\n")
        with patch.dict(os.environ, {"OMH_VENV_DIR": str(venv)}), patch.object(sys, "executable", str(python)):
            code, payload, stderr = self.uninstall(bot, "--all")
        self.assertEqual(code, 0, stderr)
        self.assertTrue(python.is_file(), payload)
        self.assertFalse(payload["command_package_removed"])
        self.assertTrue(payload["command_package_kept"])

    def test_default_primary_full_cleanup_removes_unshared_store(self):
        code, _, stderr = run_cli([
            "--hermes-home", str(self.hermes), "--omh-home", str(self.store),
            "setup", "--core", "--yes", "--no-interactive", "--no-menubar", "--json",
        ])
        self.assertEqual(code, 0, stderr)
        self.binding(self.hermes, self.store)
        code, payload, stderr = self.uninstall(self.hermes, "--keep-command")
        self.assertEqual(code, 0, stderr)
        # The store's OMH entries go; the fixture's user-record.json is not OMH's,
        # so it and therefore the store directory itself are kept.
        self.assertEqual(sorted(self.store.iterdir()), [self.sentinel], payload)
        self.assertIn({"path": str(self.sentinel), "reason": "not created by OMH"}, payload["kept_paths"])
        self.sentinel.unlink()
        code, payload, stderr = run_cli([
            "--hermes-home", str(self.hermes), "--omh-home", str(self.store),
            "setup", "--core", "--yes", "--no-interactive", "--no-menubar", "--json",
        ])
        self.assertEqual(code, 0, stderr)
        code, payload, stderr = self.uninstall(self.hermes, "--keep-command")
        self.assertEqual(code, 0, stderr)
        self.assertFalse(self.store.exists(), payload)

    def test_explicit_source_update_remains_content_only(self):
        source = self.root / "source" / "sample"
        self.write(source / "SKILL.md", "---\nname: sample\ndescription: sample\n---\n# sample\n")
        code, stdout, stderr = run_cli(["--hermes-home", str(self.hermes), "--omh-home", str(self.store),
                                       "update", "--source", str(source.parent), "--json"])
        payload = json.loads(stdout)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(payload["operation"], "update")
        self.assertNotIn("hermes_profiles", payload)
        self.assertFalse((self.hermes / "plugins").exists())
        self.assertFalse((self.hermes / "config.yaml").exists())
        for key in ("skills", "skills_dir", "source", "command_package", "workflow_content", "release_update"):
            self.assertIn(key, payload)

    def test_update_invalid_utf8_reports_partial_and_continues(self):
        bad = self.hermes / "profiles" / "a-bad" / "config.yaml"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"plugins: \xff\n")
        good = self.hermes / "profiles" / "z-good"
        self.write(good / "config.yaml", "model: original\n")
        code, stdout, stderr = run_cli(["--hermes-home", str(self.hermes), "--omh-home", str(self.store),
                                       "update", "--core", "--json"])
        payload = json.loads(stdout)
        self.assertNotEqual(code, 0, stderr)
        self.assertEqual(payload["status"], "partial")
        self.assertTrue(payload["failures"])
        self.assertNotIn("Traceback", stderr)
        rows = {row["profile"]: row for row in payload["hermes_profiles"]}
        self.assertEqual(rows["a-bad"]["status"], "failed")
        self.assertEqual(rows["z-good"]["status"], "bootstrapped")
        self.assertEqual(bad.read_bytes(), b"plugins: \xff\n")
        self.assertIn("original", (good / "config.yaml").read_text(encoding="utf-8"))

    def test_human_partial_update_does_not_claim_completion(self):
        bad = self.hermes / "profiles" / "bad" / "config.yaml"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"plugins: \xff\n")
        code, stdout, stderr = run_cli(["--hermes-home", str(self.hermes), "--omh-home", str(self.store),
                                       "update", "--core"], output_json=False)
        self.assertNotEqual(code, 0, stderr)
        self.assertNotIn("update complete", stdout.lower())
        self.assertIn("bad", stdout)
        self.assertNotIn("Traceback", stderr)

    def test_staging_override_does_not_attribute_foreign_interpreter(self):
        generation = self.root / "command" / "generations" / "g1"
        self.write(generation / "venv" / "bin" / "python", "owned\n")
        with patch.dict(os.environ, {"OMH_VENV_DIR": str(self.root / "command" / "venv"),
                                     "OMH_SELF_UPDATE_GENERATION": str(generation)}), \
                patch.object(sys, "executable", "/usr/bin/true"):
            payload = uninstall_skill_pack(OmhPaths(omh_home=self.store, hermes_home=self.hermes),
                                           remove_all=True, remove_command_package=True)
        self.assertTrue(generation.is_dir(), payload)
        self.assertFalse(payload["command_package_removed"])

    @requires_symlinks
    def test_foreign_generation_alias_is_not_owned(self):
        command = self.root / "command"
        foreign = self.root / "foreign"
        python = foreign / "venv" / "bin" / "python"
        self.write(python, "foreign\n")
        (command / "generations").mkdir(parents=True)
        (command / "generations" / "alias").symlink_to(foreign, target_is_directory=True)
        with patch.dict(os.environ, {"OMH_VENV_DIR": str(command / "venv")}), \
                patch.object(sys, "executable", str(python)):
            payload = uninstall_skill_pack(OmhPaths(omh_home=self.store, hermes_home=self.hermes),
                                           remove_all=True, remove_command_package=True)
        self.assertTrue(python.is_file(), payload)
        self.assertTrue((command / "generations" / "alias").is_symlink(), payload)
        self.assertFalse(payload["command_package_removed"])


if __name__ == "__main__":
    unittest.main()
