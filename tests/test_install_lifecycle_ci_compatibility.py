"""Protected plans are not applied refreshes or evidence of healthy inspection."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _cli_harness import run_cli
from omh.install.plugin_pack import PLUGIN_MANAGED_MANIFEST


class ProtectedUpdatePlanTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="protected-update-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.hermes = self.root / "hermes"
        self.store = self.root / "store"
        self.plugin = self.hermes / "plugins" / "omh"
        self.manifest = self.plugin / PLUGIN_MANAGED_MANIFEST
        self.base = ["--hermes-home", str(self.hermes), "--omh-home", str(self.store)]
        code, _, stderr = run_cli([*self.base, "setup", "--core", "--yes", "--no-interactive", "--no-menubar"])
        self.assertEqual(code, 0, stderr)

    def tree(self):
        return {p.relative_to(self.root).as_posix():
                ("file", p.read_bytes()) if p.is_file() else ("dir", None)
                for p in self.root.rglob("*")}

    def update(self, *flags):
        code, stdout, stderr = run_cli([*self.base, "update", *flags, "--json"])
        return code, json.loads(stdout), stderr

    def assert_plan(self):
        before = self.tree()
        code, payload, stderr = self.update("--dry-run")
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(payload["status"], "ok")
        plan = payload["plugin_bundle"]
        self.assertEqual(plan["status"], "would_refuse")
        self.assertFalse(plan["applied"])
        self.assertFalse(plan["changed"])
        self.assertFalse(plan["observed"])
        self.assertTrue(plan["dry_run"])
        self.assertTrue(plan["reason"])
        self.assertEqual(self.tree(), before)

    def assert_failed_plan(self):
        before = self.tree()
        code, payload, stderr = self.update("--dry-run")
        self.assertEqual(code, 1, (payload, stderr))
        self.assertEqual(payload["status"], "partial")
        self.assertTrue(payload["failures"])
        self.assertIsNone(payload["plugin_bundle"])
        self.assertEqual(self.tree(), before)
        return stderr

    def test_unknown_private_content_is_an_explicit_not_applied_plan(self):
        (self.plugin / "private.txt").write_bytes(b"private\r\n\x00bytes")
        self.assert_plan()

    def test_absent_managed_file_is_an_explicit_not_applied_plan(self):
        (self.plugin / "__init__.py").unlink()
        self.assert_plan()

    def test_managed_metadata_errors_are_not_protected_plans(self):
        marker = self.plugin / "__init__.py"
        real_stat = Path.stat
        real_exists = Path.exists
        for error in (PermissionError("CI_STAT_DENIED"), OSError("CI_STAT_IO_ERROR")):
            with self.subTest(error=type(error).__name__):
                reads = []
                before = self.tree()

                def denied(path, *args, **kwargs):
                    if path == marker:
                        reads.append(path)
                        raise error
                    return real_stat(path, *args, **kwargs)

                def suppressed_exists(path, *args, **kwargs):
                    # Python 3.14 suppresses stat errors in exists(); emulate
                    # that public behavior on older supported interpreters too.
                    if path == marker:
                        try:
                            path.stat()
                        except OSError:
                            return False
                    return real_exists(path, *args, **kwargs)

                with patch.object(Path, "stat", denied), patch.object(Path, "exists", suppressed_exists):
                    code, payload, stderr = self.update("--dry-run")
                self.assertTrue(reads, "metadata fault must actually fire")
                self.assertEqual(code, 1, (payload, stderr))
                self.assertEqual(payload["status"], "partial")
                self.assertTrue(payload["failures"])
                self.assertIsNone(payload["plugin_bundle"])
                self.assertIn(str(error), stderr)
                self.assertEqual(self.tree(), before)

    def test_embedded_null_managed_path_is_a_required_failure(self):
        manifest = json.loads(self.manifest.read_bytes())
        manifest["files"][0]["path"] = "invalid\x00path"
        self.manifest.write_bytes(json.dumps(manifest).encode("utf-8"))
        self.assert_failed_plan()

    def test_dirty_managed_content_is_an_explicit_not_applied_plan(self):
        (self.plugin / "__init__.py").write_bytes(b"# user edit\r\n")
        self.assert_plan()

    def test_actual_protection_refusal_is_partial_and_preserves_all_plugin_bytes(self):
        marker = self.plugin / "__init__.py"
        marker.write_bytes(b"# edited\r\n")
        before = {p.relative_to(self.plugin): p.read_bytes() for p in self.plugin.rglob("*") if p.is_file()}
        code, payload, stderr = self.update()
        self.assertEqual(code, 1, (payload, stderr))
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["failures"][0]["phase"], "plugin_bundle")
        self.assertEqual({p.relative_to(self.plugin): p.read_bytes() for p in self.plugin.rglob("*") if p.is_file()}, before)

    def test_real_directory_inspection_error_is_not_a_protected_plan(self):
        real = Path.iterdir
        reads = []

        def denied(path):
            if path.resolve() == self.plugin.resolve():
                reads.append(path)
                raise OSError("CI_TYPED_INSPECTION_DENIED")
            return real(path)

        # The snapshot outside the patch proves bytes; avoid making the checker
        # itself traverse the deliberately unreadable directory.
        before = self.tree()
        with patch.object(Path, "iterdir", denied):
            code, payload, stderr = self.update("--dry-run")
        self.assertTrue(reads)
        self.assertEqual(code, 1, (payload, stderr))
        self.assertEqual(payload["status"], "partial")
        self.assertIsNone(payload["plugin_bundle"])
        self.assertIn("CI_TYPED_INSPECTION_DENIED", stderr)
        self.assertEqual(self.tree(), before)

    def test_manifest_read_error_is_not_a_protected_plan(self):
        real = Path.read_text
        reads = []

        def denied(path, *args, **kwargs):
            if path.resolve() == self.manifest.resolve():
                reads.append(path)
                raise OSError("CI_TYPED_MANIFEST_DENIED")
            return real(path, *args, **kwargs)

        with patch.object(Path, "read_text", denied):
            self.assert_failed_plan()
        self.assertTrue(reads)

    def test_invalid_json_manifest_is_not_a_protected_plan(self):
        self.manifest.write_bytes(b"{invalid json\r\n")
        self.assert_failed_plan()

    def test_semantically_invalid_manifest_objects_are_not_protected_plans(self):
        original = json.loads(self.manifest.read_bytes())
        invalid = [{}, {**original, "files": []}, {**original, "schema_version": "foreign/v1"},
                   {**original, "plugin_name": "foreign"}, {**original, "files": [{"path": "__init__.py"}]},
                   {**original, "files": [{"path": "../outside", "sha256": "a" * 64}]},
                   {**original, "files": [{"path": "__init__.py", "sha256": "invalid"}]}]
        for manifest in invalid:
            with self.subTest(manifest=manifest):
                self.manifest.write_bytes(json.dumps(manifest).encode("utf-8"))
                self.assert_failed_plan()

    def test_missing_manifest_is_not_confirmed_protection(self):
        self.manifest.unlink()
        self.assert_failed_plan()

    def test_host_owned_plugin_is_never_a_content_protection_plan_even_with_force(self):
        self.manifest.unlink()
        metadata = self.plugin.parent / ".install-metadata.json"
        metadata.write_bytes(json.dumps({"omh": {"revision": "host-pin", "source": "host"}}).encode())
        private = self.plugin / "private.txt"
        private.write_bytes(b"host-owned\r\n")
        before = {p.relative_to(self.plugin): p.read_bytes() for p in self.plugin.rglob("*") if p.is_file()}
        for flags in (("--dry-run",), ("--dry-run", "--force"), ("--force",)):
            with self.subTest(flags=flags):
                code, payload, stderr = self.update(*flags)
                self.assertEqual(code, 0, (payload, stderr))
                self.assertEqual(payload["plugin_bundle"]["status"], "host_managed")
                self.assertEqual({p.relative_to(self.plugin): p.read_bytes() for p in self.plugin.rglob("*") if p.is_file()}, before)

    def test_required_child_refusal_still_fails_a_primary_protected_dry_run(self):
        child = self.hermes / "profiles" / "bot"
        child.mkdir(parents=True)
        code, _, stderr = run_cli([*self.base, "setup", "--core", "--yes", "--no-interactive", "--no-menubar"])
        self.assertEqual(code, 0, stderr)
        (self.plugin / "private.txt").write_bytes(b"primary private\r\n")
        (child / "plugins" / "omh" / "private.txt").write_bytes(b"child private\r\n")
        before = self.tree()
        code, payload, stderr = self.update("--dry-run")
        self.assertEqual(code, 1, (payload, stderr))
        self.assertEqual(payload["plugin_bundle"]["status"], "would_refuse")
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["failures"][0]["profile"], "bot")
        self.assertEqual(payload["hermes_profiles"][0]["status"], "failed")
        self.assertEqual(self.tree(), before)

    def test_force_dry_run_and_actual_refresh_retain_explicit_consent(self):
        private = self.plugin / "private.txt"
        private.write_bytes(b"private\r\n")
        before = self.tree()
        code, payload, stderr = self.update("--force", "--dry-run")
        self.assertEqual(code, 0, (payload, stderr))
        self.assertNotEqual(payload["plugin_bundle"].get("status"), "would_refuse")
        self.assertEqual(self.tree(), before)
        code, payload, stderr = self.update("--force")
        self.assertEqual(code, 0, (payload, stderr))
        self.assertFalse(private.exists())


if __name__ == "__main__":
    unittest.main()
