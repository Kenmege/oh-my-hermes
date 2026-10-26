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
from omh.config_adapter import external_dirs, plugin_omh_home_setting


class LifecycleOrderingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="lifecycle-ordering-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.hermes = self.root / "hermes"
        self.bot = self.hermes / "profiles" / "bot"
        self.bot.mkdir(parents=True)
        # Hermes profile creation seeds identity before OMH bootstraps config.
        (self.bot / "SOUL.md").write_text("Bot profile identity.\n", encoding="utf-8")
        self.store = self.root / "store"
        self.command = self.root / "command" / "venv"
        self.python = self.command / "bin" / "python"
        self.python.parent.mkdir(parents=True)
        self.python.write_bytes(b"owned\n")
        (self.command / "pyvenv.cfg").write_text("home = synthetic-python\n", encoding="utf-8")
        (self.python.parent / "omh").write_text("# synthetic OMH entry\n", encoding="utf-8")
        self.environment = patch.dict(os.environ, {"OMH_VENV_DIR": str(self.command),
                                                   "OMH_BIN_DIR": str(self.root / "bin"),
                                                   "HOME": str(self.root / "home"),
                                                   "USERPROFILE": str(self.root / "home")})
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
            self.store.parent.mkdir(parents=True, exist_ok=True)
            os.environ["HOME"] = str(self.store.parent)
            os.environ["USERPROFILE"] = str(self.store.parent)
            self.assertEqual(Path.home().resolve(), self.store.parent.resolve())
            os.environ.pop("OMH_HOME", None)
        code, payload, stderr = self.cli("setup", "--core", "--yes", "--no-interactive",
                                         "--no-menubar", store=not default)
        self.assertEqual(code, 0, (payload, stderr))
        self.record = self.store / "runtime" / "record"
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_bytes(b"MUST KEEP\n")
        self.assert_binding(self.bot)
        self.assertIn((self.store / "skills").resolve(),
                      {Path(p).resolve() for p in external_dirs((self.bot / "config.yaml").read_text())})

    def assert_binding(self, home):
        self.assertEqual(Path(plugin_omh_home_setting((home / "config.yaml").read_text())).resolve(), self.store.resolve())

    def resolved_paths(self, paths):
        return {Path(p).resolve() for p in paths}

    def assert_retained_roots(self):
        self.assertEqual(self.record.read_bytes(), b"MUST KEEP\n")
        self.assertEqual(self.python.read_bytes(), b"owned\n")

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
        self.assert_retained_roots()
        self.assertEqual(saved.read_bytes(), before)
        self.assertTrue(cfg.is_symlink())
        self.assert_binding(self.bot)
        self.assertEqual(payload["store_removal"]["status"], "kept")
        self.assertFalse(payload["command_package_removed"])
        self.assertNotIn(self.store.resolve(), self.resolved_paths(payload["removed_paths"]))

    def test_markerless_shell_is_ignored_but_configured_profile_is_detached(self):
        ghost = self.hermes / "profiles" / "old-log-shell"
        (ghost / "logs").mkdir(parents=True)
        (ghost / "cache").mkdir()
        (ghost / "logs" / "sentinel").write_bytes(b"KEEP LOGS\n")
        before = {p.relative_to(ghost): p.read_bytes() for p in ghost.rglob("*") if p.is_file()}
        self.setup()
        self.assertFalse((ghost / "config.yaml").exists())
        self.assertFalse((ghost / "plugins").exists())
        # OMH's own config remains identity after the initial marker is gone.
        (self.bot / "SOUL.md").unlink()
        code, payload, stderr = self.uninstall()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual([(row["profile"], row["status"]) for row in payload["hermes_profiles"]],
                         [("bot", "cleared")])
        self.assertEqual({p.relative_to(ghost): p.read_bytes() for p in ghost.rglob("*") if p.is_file()}, before)
        self.assertEqual(sorted(p.name for p in ghost.iterdir()), ["cache", "logs"])
        self.assertFalse(self.store.exists(), payload)
        self.assertFalse(self.command.exists(), payload)

    def test_successful_primary_and_child_detach_removes_roots(self):
        self.setup()
        code, payload, stderr = self.uninstall()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertFalse(self.store.exists(), payload)
        self.assertFalse(self.command.exists(), payload)
        self.assertEqual(payload["hermes_profiles"][0]["status"], "cleared")
        self.assertIn(self.store.resolve(), self.resolved_paths(payload["removed_paths"]))

    def test_successful_custom_binding_cleanup_without_store_override(self):
        self.setup()
        # Pinning the resolved paths matters after primary reversal removes its
        # persisted binding; child registration must still use the old store.
        code, payload, stderr = self.uninstall(store=False)
        self.assertEqual(code, 0, (payload, stderr))
        self.assertFalse(self.store.exists(), payload)
        self.assertFalse(self.command.exists(), payload)
        text = (self.bot / "config.yaml").read_text()
        self.assertEqual(plugin_omh_home_setting(text), "")
        self.assertNotIn((self.store / "skills").resolve(), {Path(p).resolve() for p in external_dirs(text)})

    def test_default_home_store_successful_cleanup(self):
        self.setup(default=True)
        code, payload, stderr = self.uninstall(store=False)
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(Path(payload["omh_home"]).resolve(), self.store.resolve())
        self.assertFalse(self.store.exists(), payload)
        self.assertFalse(self.command.exists(), payload)

    def test_user_edited_retained_binding_protects_store_and_command(self):
        self.setup()
        cfg = self.bot / "config.yaml"
        # Same real store, different user value: ownership reversal must keep it.
        before = cfg.read_bytes()
        current = plugin_omh_home_setting(before.decode("utf-8"))
        edited = before.replace(f"'{current}'".encode(), f"'{current}/../{self.store.name}'".encode())
        self.assertNotEqual(edited, before, "the actual serialized binding must be edited")
        cfg.write_bytes(edited)
        self.assertNotEqual(plugin_omh_home_setting(edited.decode("utf-8")), current)
        self.assert_binding(self.bot)
        code, payload, stderr = self.uninstall()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(payload["hermes_profiles"][0]["status"], "partially_cleared")
        self.assertIn(f"'{current}/../{self.store.name}'".encode(), cfg.read_bytes())
        self.assert_binding(self.bot)
        self.assert_retained_roots()
        self.assertEqual(payload["store_removal"]["status"], "kept")
        self.assertFalse(payload["command_package_removed"])

    def test_targeted_profile_detach_keeps_primary_roots(self):
        self.setup()
        primary = (self.hermes / "config.yaml").read_bytes()
        code, payload, stderr = self.uninstall(home=self.bot, store=False)
        self.assertEqual(code, 0, (payload, stderr))
        self.assert_retained_roots()
        self.assertEqual((self.hermes / "config.yaml").read_bytes(), primary)

    def test_remove_files_keeps_child_binding_plugin_and_shared_roots(self):
        self.setup()
        plugin = self.bot / "plugins" / "omh"
        before = {p.relative_to(plugin): p.read_bytes() for p in plugin.rglob("*") if p.is_file()}
        code, payload, stderr = self.uninstall("--remove-files")
        self.assertEqual(code, 0, (payload, stderr))
        self.assert_retained_roots()
        # Existing remove-files semantics unregister profiles, but do not reverse
        # their runtime binding or remove the plugin bundle.
        self.assert_binding(self.bot)
        self.assertNotIn((self.store / "skills").resolve(),
                         {Path(p).resolve() for p in external_dirs((self.bot / "config.yaml").read_text())})
        self.assertTrue(plugin.is_dir())
        self.assertEqual({p.relative_to(plugin): p.read_bytes() for p in plugin.rglob("*") if p.is_file()}, before)
        self.assertNotIn(self.store.resolve(), self.resolved_paths(payload["removed_paths"]))

    def test_dry_run_preserves_configs_and_does_not_assume_detach(self):
        self.setup()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        code, payload, stderr = self.uninstall("--dry-run")
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before)
        self.assertNotIn(self.store.resolve(), self.resolved_paths(payload["would_remove"]))
        self.assertFalse(payload["command_package_removed"])


if __name__ == "__main__":
    unittest.main()
