"""A managed file already holding the incoming bundle's bytes is not a conflict (#2300).

An older release recorded hash A for a managed file; the person edited it to B;
the release being installed ships exactly B. A non-forced install loses nothing
by writing B over B, so it must go through and rewrite the manifest. A local
file that matches neither the old record nor the incoming bundle is still a
local change and still needs --force.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any
import unittest
from unittest import mock

from _local_package import load_local_package

load_local_package()

from omh.core.errors import OmhError
from omh.install import plugin_pack as plugin_pack_module
from omh.install.installer import install_skill_pack
from omh.install.plugin_pack import (
    PLUGIN_MANAGED_MANIFEST,
    PluginContentProtectedError,
    PluginPackError,
    bundled_plugin_records,
    install_plugin_bundle,
    read_plugin_manifest,
)
from omh.maintenance.doctor import run_doctor
from omh.paths import resolve_paths

EDITED = "config.yaml"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _age_manifest_record(manifest_path: Path, rel: str, old_bytes: bytes) -> None:
    """Rewrite one record as an older release would have written it."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["files"]:
        if record["path"] == rel:
            record["sha256"] = _sha(old_bytes)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


class PluginIdenticalUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths = resolve_paths(self.root / ".omh", self.root / ".hermes")
        install_plugin_bundle(self.paths)
        self.target = self.paths.hermes_plugin_dir
        self.manifest_path = self.target / PLUGIN_MANAGED_MANIFEST
        self.edited = self.target / EDITED
        self.incoming = self.edited.read_bytes()
        # Old release A carried four more allowlist lines; the person removed
        # them, which leaves exactly the incoming bytes B on disk.
        self.old_bytes = self.incoming + b"# allow: a\n# allow: b\n# allow: c\n# allow: d\n"
        _age_manifest_record(self.manifest_path, EDITED, self.old_bytes)

    def _doctor(self) -> dict[str, Any]:
        return {check.name: check for check in run_doctor(self.paths)}

    def test_local_equal_to_incoming_upgrades_without_force(self) -> None:
        before = self._doctor()
        self.assertFalse(before["plugin_manifest"].ok)

        install_plugin_bundle(self.paths)

        self.assertEqual(self.edited.read_bytes(), self.incoming)
        manifest = read_plugin_manifest(self.target)
        assert manifest is not None
        recorded = {item["path"]: item["sha256"] for item in manifest["files"]}
        self.assertEqual(recorded[EDITED], _sha(self.incoming))
        self.assertEqual(recorded, {item["path"]: item["sha256"] for item in bundled_plugin_records()})
        after = self._doctor()
        self.assertTrue(after["plugin_manifest"].ok, after["plugin_manifest"].message)
        self.assertTrue(after["plugin_bundle_current"].ok, after["plugin_bundle_current"].message)

    def test_local_divergent_from_incoming_still_refuses(self) -> None:
        divergent = self.incoming + b"# my own edit\n"
        self.edited.write_bytes(divergent)
        manifest_before = self.manifest_path.read_bytes()

        with self.assertRaisesRegex(PluginContentProtectedError, f"managed plugin files changed: {EDITED}"):
            install_plugin_bundle(self.paths)

        self.assertEqual(self.edited.read_bytes(), divergent)
        self.assertEqual(self.manifest_path.read_bytes(), manifest_before)
        self.assertEqual({entry.name for entry in self.target.parent.iterdir()}, {"omh"})
        install_plugin_bundle(self.paths, force=True)
        self.assertEqual(self.edited.read_bytes(), self.incoming)

    def test_staging_revalidation_exempts_incoming_bytes(self) -> None:
        existing = read_plugin_manifest(self.target)

        plugin_pack_module._copy_plugin_bundle(
            self.target, bundled_plugin_records(), existing_manifest=existing, initially_occupied=False, force=False
        )

        manifest = read_plugin_manifest(self.target)
        assert manifest is not None
        recorded = {item["path"]: item["sha256"] for item in manifest["files"]}
        self.assertEqual(recorded[EDITED], _sha(self.incoming))
        self.assertEqual({entry.name for entry in self.target.parent.iterdir()}, {"omh"})

    def test_staging_revalidation_refuses_late_divergent_edit(self) -> None:
        original_copy = plugin_pack_module._copy_resource_tree
        manifest_before = self.manifest_path.read_bytes()
        late = b"# late private edit\n"

        def inject(root: Any, dest: Path) -> None:
            original_copy(root, dest)
            if dest.name == ".omh.installing":
                self.edited.write_bytes(late)

        with mock.patch.object(plugin_pack_module, "_copy_resource_tree", side_effect=inject):
            with self.assertRaisesRegex(PluginPackError, f"managed plugin files changed: {EDITED}; original bundle restored"):
                install_plugin_bundle(self.paths)
        self.assertEqual(self.edited.read_bytes(), late)
        self.assertEqual(self.manifest_path.read_bytes(), manifest_before)
        self.assertEqual({entry.name for entry in self.target.parent.iterdir()}, {"omh"})


class SkillIdenticalUpgradeTests(unittest.TestCase):
    """The skill installer's sibling: a SKILL.md already equal to the incoming render."""

    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths = resolve_paths(self.root / ".omh", self.root / ".hermes")
        install_skill_pack(self.paths)
        self.manifest_path = self.paths.manifest_path
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.rel = manifest["skills"][0]["path"]
        self.skill_file = self.paths.skills_dir / self.rel
        # What this install renders for the skill; an older release recorded A.
        self.incoming = self.skill_file.read_bytes()
        self.old_bytes = self.incoming + b"\n<!-- older release -->\n"
        manifest["skills"][0]["sha256"] = _sha(self.old_bytes)
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def _recorded(self) -> dict[str, str]:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {item["path"]: item["sha256"] for item in manifest["skills"]}

    def test_local_equal_to_incoming_installs_without_force(self) -> None:
        self.assertEqual(self._recorded()[self.rel], _sha(self.old_bytes))

        install_skill_pack(self.paths)

        self.assertEqual(self.skill_file.read_bytes(), self.incoming)
        self.assertEqual(self._recorded()[self.rel], _sha(self.incoming))

    def test_local_divergent_from_incoming_still_refuses(self) -> None:
        divergent = b"# a private edit that matches neither release\n"
        self.skill_file.write_bytes(divergent)
        manifest_before = self.manifest_path.read_bytes()

        with self.assertRaisesRegex(OmhError, f"local modifications detected; .*{re.escape(self.rel)}"):
            install_skill_pack(self.paths)

        self.assertEqual(self.skill_file.read_bytes(), divergent)
        self.assertEqual(self.manifest_path.read_bytes(), manifest_before)


if __name__ == "__main__":
    unittest.main()
