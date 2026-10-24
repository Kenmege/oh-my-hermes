"""Shared roots are removable only after real profile detach outcomes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _cli_harness import run_cli
from _platform_support import requires_symlinks


class LifecycleOrderingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="lifecycle-ordering-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.hermes = self.root / "hermes"
        self.bot = self.hermes / "profiles" / "bot"
        self.bot.mkdir(parents=True)
        self.store = self.root / "store"
        self.command = self.root / "command" / "venv"
        self.python = self.command / "bin" / "python"
        self.python.parent.mkdir(parents=True)
        self.python.write_text("owned\n", encoding="utf-8")
        self.environment = patch.dict(os.environ, {"OMH_VENV_DIR": str(self.command),
                                                   "OMH_BIN_DIR": str(self.root / "bin")})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def cli(self, *args, store=True, home=None):
        argv = ["--hermes-home", str(home or self.hermes)]
        if store:
            argv += ["--omh-home", str(self.store)]
        code, stdout, stderr = run_cli([*argv, *args, "--json"])
        return code, json.loads(stdout), stderr

    def setup(self, *, default=False):
        if default:
            self.store = self.root / "home" / ".omh"
            os.environ["HOME"] = str(self.store.parent)
            os.environ.pop("OMH_HOME", None)
        code, payload, stderr = self.cli("setup", "--core", "--yes", "--no-interactive",
                                         "--no-menubar", store=not default)
        self.assertEqual(code, 0, (payload, stderr))
        self.record = self.store / "runtime" / "record"
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_bytes(b"MUST KEEP\n")
        self.assertIn(str(self.store), (self.bot / "config.yaml").read_text())

    def uninstall(self, *flags, store=True, home=None):
        with patch.object(sys, "executable", str(self.python)):
            return self.cli("uninstall", *flags, store=store, home=home)

    @requires_symlinks
    def test_readable_symlink_child_refusal_retains_store_record_and_owned_command(self):
        self.setup()
        cfg = self.bot / "config.yaml"
        saved = cfg.with_name("config.saved")
        cfg.rename(saved)
        cfg.symlink_to(saved.name)
        before = saved.read_bytes()
        code, payload, stderr = self.uninstall()
        self.assertEqual(code, 1, (payload, stderr))
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["hermes_profiles"][0]["status"], "failed")
        self.assertIn("symlink", payload["hermes_profiles"][0]["error"])
        self.assertTrue(self.store.is_dir(), payload)
        self.assertEqual(self.record.read_bytes(), b"MUST KEEP\n")
        self.assertTrue(self.python.is_file(), payload)
        self.assertEqual(saved.read_bytes(), before)
        self.assertTrue(cfg.is_symlink())
        self.assertIn(str(self.store).encode(), before)
        self.assertEqual(payload["store_removal"]["status"], "kept")
        self.assertFalse(payload["command_package_removed"])
        self.assertNotIn(str(self.store), payload["removed_paths"])

    def test_successful_primary_and_child_detach_removes_roots(self):
        self.setup()
        code, payload, stderr = self.uninstall()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertFalse(self.store.exists(), payload)
        self.assertFalse(self.command.exists(), payload)
        self.assertEqual(payload["hermes_profiles"][0]["status"], "cleared")
        self.assertIn(str(self.store), payload["removed_paths"])

    def test_successful_custom_binding_cleanup_without_store_override(self):
        self.setup()
        # Pinning the resolved paths matters after primary reversal removes its
        # persisted binding; child registration must still use the old store.
        code, payload, stderr = self.uninstall(store=False)
        self.assertEqual(code, 0, (payload, stderr))
        self.assertFalse(self.store.exists(), payload)
        self.assertFalse(self.command.exists(), payload)
        self.assertNotIn(str(self.store), (self.bot / "config.yaml").read_text())

    def test_default_home_store_successful_cleanup(self):
        self.setup(default=True)
        code, payload, stderr = self.uninstall(store=False)
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(Path(payload["omh_home"]), self.store)
        self.assertFalse(self.store.exists(), payload)
        self.assertFalse(self.command.exists(), payload)

    def test_user_edited_retained_binding_protects_store_and_command(self):
        self.setup()
        cfg = self.bot / "config.yaml"
        # Same real store, different user value: ownership reversal must keep it.
        cfg.write_text(cfg.read_text().replace(str(self.store), str(self.store / ".." / "store")))
        code, payload, stderr = self.uninstall()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(payload["hermes_profiles"][0]["status"], "partially_cleared")
        self.assertTrue(self.record.exists(), payload)
        self.assertTrue(self.python.exists(), payload)
        self.assertEqual(payload["store_removal"]["status"], "kept")
        self.assertFalse(payload["command_package_removed"])

    def test_targeted_profile_detach_keeps_primary_roots(self):
        self.setup()
        primary = (self.hermes / "config.yaml").read_bytes()
        code, payload, stderr = self.uninstall(home=self.bot, store=False)
        self.assertEqual(code, 0, (payload, stderr))
        self.assertTrue(self.record.exists(), payload)
        self.assertTrue(self.python.exists(), payload)
        self.assertEqual((self.hermes / "config.yaml").read_bytes(), primary)

    def test_remove_files_keeps_child_binding_plugin_and_shared_roots(self):
        self.setup()
        code, payload, stderr = self.uninstall("--remove-files")
        self.assertEqual(code, 0, (payload, stderr))
        self.assertTrue(self.record.exists(), payload)
        self.assertTrue(self.python.exists(), payload)
        # Existing remove-files semantics unregister profiles, but do not reverse
        # their runtime binding or remove the plugin bundle.
        self.assertIn(str(self.store), (self.bot / "config.yaml").read_text())
        self.assertTrue((self.bot / "plugins" / "omh").is_dir())
        self.assertNotIn(str(self.store), payload["removed_paths"])

    def test_dry_run_preserves_configs_and_does_not_assume_detach(self):
        self.setup()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        code, payload, stderr = self.uninstall("--dry-run")
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before)
        self.assertNotIn(str(self.store), payload["would_remove"])
        self.assertFalse(payload["command_package_removed"])


if __name__ == "__main__":
    unittest.main()
