"""Uninstall must prove ownership before recursively removing a configured home."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from _cli_harness import run_cli
from _local_package import load_local_package
from _platform_support import requires_symlinks

load_local_package()

from omh.install.installer import uninstall_skill_pack  # noqa: E402
from omh.paths import OmhPaths  # noqa: E402


class UninstallHomeOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="uninstall-ownership-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.hermes = self.home / ".hermes"
        self.omh = self.home / ".omh"
        self.hermes.mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "HERMES_HOME": str(self.hermes), "OMH_HOME": str(self.omh),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def _setup(self, target: Path) -> None:
        status, _, stderr = run_cli([
            "--hermes-home", str(self.hermes), "--omh-home", str(target),
            "setup", "--core", "--yes", "--no-interactive", "--no-menubar", "--json",
        ])
        self.assertEqual(status, 0, stderr)
        self.assertTrue((target / "manifest.json").is_file())

    def _uninstall(self, target: Path, *, dry_run: bool = False, force: bool = False) -> dict:
        return uninstall_skill_pack(
            OmhPaths(omh_home=target, hermes_home=self.hermes),
            remove_files=True, dry_run=dry_run, force=force,
        )

    def _assert_kept(self, result: dict, target: Path) -> None:
        entries = [item for item in result["kept_paths"] if item["path"] == str(target)]
        self.assertTrue(entries, result)
        self.assertTrue(entries[0]["reason"])
        self.assertNotIn(str(target), result["would_remove"])
        self.assertNotIn(str(target), result["removed_paths"])

    def test_unrelated_directory_is_kept_with_reason_through_cli(self) -> None:
        target = self.root / "unrelated"
        target.mkdir()
        sentinel = target / "personal.txt"
        sentinel.write_text("not OMH data\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"OMH_HOME": str(target)}):
            status, stdout, stderr = run_cli([
                "uninstall", "--remove-files", "--keep-command", "--json",
            ])
        self.assertEqual(status, 0, stderr)
        self.assertTrue(sentinel.exists(), "unrelated OMH_HOME was recursively deleted")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "not OMH data\n")
        self._assert_kept(json.loads(stdout), target)

    def test_home_refused_even_with_force_and_real_setup_manifest(self) -> None:
        self._setup(self.home)
        sentinel = self.home / "personal.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        result = self._uninstall(self.home, force=True)
        self.assertTrue(sentinel.is_file(), "--force deleted HOME")
        self._assert_kept(result, self.home)

    def test_genuinely_setup_home_is_removed(self) -> None:
        self._setup(self.omh)
        result = self._uninstall(self.omh)
        self.assertFalse(self.omh.exists())
        self.assertIn(str(self.omh), result["removed_paths"])

    def test_unowned_dry_run_matches_real_refusal(self) -> None:
        target = self.root / "unrelated"
        target.mkdir()
        (target / "personal.txt").write_text("keep\n", encoding="utf-8")
        planned = self._uninstall(target, dry_run=True)
        actual = self._uninstall(target)
        self._assert_kept(planned, target)
        self._assert_kept(actual, target)
        self.assertEqual(planned["kept_paths"], actual["kept_paths"])

    def test_owned_dry_run_matches_real_removal(self) -> None:
        self._setup(self.omh)
        before = (self.omh / "manifest.json").read_bytes()
        planned = self._uninstall(self.omh, dry_run=True)
        self.assertEqual((self.omh / "manifest.json").read_bytes(), before)
        actual = self._uninstall(self.omh)
        self.assertEqual(planned["would_remove"], actual["removed_paths"])

    def test_hermes_home_and_home_ancestor_are_refused_with_force(self) -> None:
        for target in (self.hermes, self.root):
            with self.subTest(target=target):
                self._setup(target)
                sentinel = target / "personal.txt"
                sentinel.write_text("keep\n", encoding="utf-8")
                result = self._uninstall(target, force=True)
                self.assertTrue(sentinel.is_file())
                self._assert_kept(result, target)

    def test_filesystem_root_is_never_planned_for_removal(self) -> None:
        target = Path(self.root.anchor)
        self._assert_kept(self._uninstall(target, dry_run=True, force=True), target)

    @requires_symlinks
    def test_symlink_home_is_kept_even_when_its_target_is_owned(self) -> None:
        self._setup(self.omh)
        alias = self.root / "alias"
        alias.symlink_to(self.omh, target_is_directory=True)
        result = self._uninstall(alias, force=True)
        self.assertTrue(alias.is_symlink())
        self.assertTrue((self.omh / "manifest.json").is_file())
        self._assert_kept(result, alias)

    @requires_symlinks
    def test_environment_symlink_is_not_lost_during_cli_path_resolution(self) -> None:
        self._setup(self.omh)
        alias = self.root / "alias"
        alias.symlink_to(self.omh, target_is_directory=True)
        with mock.patch.dict(os.environ, {"OMH_HOME": str(alias)}):
            status, stdout, stderr = run_cli([
                "uninstall", "--remove-files", "--force", "--keep-command", "--json",
            ])
        self.assertEqual(status, 0, stderr)
        self.assertTrue((self.omh / "manifest.json").is_file())
        self._assert_kept(json.loads(stdout), self.omh)

    def _assert_symlinked_home_survives_cli(self, argv: list[str], env: dict[str, str]) -> None:
        self._setup(self.omh)
        sentinel = self.omh / "personal.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        alias = self.root / "alias"
        alias.symlink_to(self.omh, target_is_directory=True)
        argv = [arg.replace("{alias}", str(alias)) for arg in argv]
        env = {key: value.replace("{alias}", str(alias)) for key, value in env.items()}
        with mock.patch.dict(os.environ, env):
            status, stdout, stderr = run_cli([
                *argv, "uninstall", "--remove-files", "--force", "--keep-command", "--json",
            ])
        self.assertEqual(status, 0, stderr)
        self.assertTrue(alias.is_symlink())
        self.assertTrue((self.omh / "manifest.json").is_file(), "symlinked OMH home target was deleted")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
        result = json.loads(stdout)
        self._assert_kept(result, self.omh)
        reason = next(item["reason"] for item in result["kept_paths"] if item["path"] == str(self.omh))
        self.assertIn("symlink", reason)

    @requires_symlinks
    def test_explicit_omh_home_flag_symlink_target_survives_cli_uninstall(self) -> None:
        self._assert_symlinked_home_survives_cli(
            ["--hermes-home", str(self.hermes), "--omh-home", "{alias}"],
            {"OMH_HOME": str(self.root / "elsewhere")},
        )

    @requires_symlinks
    def test_omh_home_env_symlink_target_survives_cli_uninstall(self) -> None:
        self._assert_symlinked_home_survives_cli(["--hermes-home", str(self.hermes)], {"OMH_HOME": "{alias}"})

    def test_invalid_or_foreign_manifest_does_not_establish_ownership(self) -> None:
        self.omh.mkdir()
        marker = self.omh / "manifest.json"
        for content in ("{}", '{"package": "another-app", "schema_version": 1}', "{broken", "[]"):
            with self.subTest(content=content):
                marker.write_text(content, encoding="utf-8")
                self._assert_kept(self._uninstall(self.omh, force=True), self.omh)
                self.assertEqual(marker.read_text(encoding="utf-8"), content)
        marker.write_bytes(b"\xff")
        self._assert_kept(self._uninstall(self.omh, force=True), self.omh)

    def test_unowned_menubar_directory_is_kept(self) -> None:
        from omh.surfaces.menubar_app import uninstall_menubar_app

        target = self.omh / "menubar"
        target.mkdir(parents=True)
        sentinel = target / "personal.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        planned = uninstall_menubar_app(OmhPaths(self.omh, self.hermes), dry_run=True, platform_name="Linux")
        self.assertNotIn(str(target), planned["would_remove"])
        result = uninstall_menubar_app(OmhPaths(self.omh, self.hermes), platform_name="Linux")
        self.assertTrue(sentinel.is_file())
        self.assertTrue(result["kept_paths"])

    @requires_symlinks
    def test_symlinked_plugin_is_kept_with_force(self) -> None:
        from omh.install.installer import uninstall_profile_plugin

        self._setup(self.omh)
        plugin = self.hermes / "plugins" / "omh"
        moved = self.root / "foreign-plugin"
        plugin.rename(moved)
        plugin.symlink_to(moved, target_is_directory=True)
        result = uninstall_profile_plugin(OmhPaths(self.omh, self.hermes), force=True)
        self.assertTrue(plugin.is_symlink())
        self._assert_kept(result, plugin)

    def test_team_manifest_cannot_authorize_recursive_directory_deletion(self) -> None:
        from omh.profiles.team import TEAM_PROFILE_SCHEMA_VERSION

        target = self.hermes / "agents" / "personal"
        target.mkdir(parents=True)
        sentinel = target / "personal.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        manifests = self.omh / "team-profile-packs"
        manifests.mkdir(parents=True)
        (manifests / "bad.json").write_text(json.dumps({
            "schema_version": TEAM_PROFILE_SCHEMA_VERSION, "files": [str(target)],
        }), encoding="utf-8")
        result = uninstall_skill_pack(OmhPaths(self.omh, self.hermes), remove_all=True, force=True)
        self.assertTrue(sentinel.is_file())
        self._assert_kept(result, target)

    def test_command_venv_override_cannot_claim_a_parent_of_the_runtime(self) -> None:
        import sys

        target = self.root / "unrelated"
        (target / "nested" / "bin").mkdir(parents=True)
        executable = target / "nested" / "bin" / "python"
        executable.write_text("not a venv\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"OMH_VENV_DIR": str(target)}), mock.patch.object(sys, "executable", str(executable)):
            result = uninstall_skill_pack(OmhPaths(self.omh, self.hermes), remove_command_package=True)
        self.assertTrue(executable.is_file())
        self.assertFalse(result["command_package_removed"])
        self.assertTrue(result["command_package_kept"])


if __name__ == "__main__":
    unittest.main()
