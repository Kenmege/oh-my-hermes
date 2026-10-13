"""Hermes edits to OMH-managed skills: redirected at the hook, kept by the installer, reported by doctor.

Hermes' background self-improvement review may patch, edit, or write files into
any skill it loaded, `skills.external_dirs` included; only delete and archive
are guarded upstream (`tools/skill_manager_guards.py`), and every write lands
in `<hermes_home>/skills/.curator_ledger.jsonl` (`tools/skill_ledger.py`).
Before this, the next `omh update` of a standalone install refused with "local
modifications detected", and a staged update left the edit in a generation
Hermes stopped loading.

Three layers are pinned here, each against the shape upstream really sends:

- `HookRedirectTests`: `pre_tool_call` blocks `skill_manage` edit, patch,
  write_file and remove_file on a skill the install manifest records, and the
  block names where the lesson goes. A skill OMH did not install passes
  untouched, whatever its name looks like.
- `InstallerKeepsHermesEditTests`: the installer keeps a skill a ledger row
  still accounts for and reports it, carries it into a new generation, and
  `--force` still replaces it, saying so. A hand edit with no ledger row still
  refuses, as it always did.
- `DoctorLedgerTests`: `omh doctor` reads the ledger without writing it.

Live firing in the background-review fork is NOT observed here: it needs a
Hermes host that ships that review, and the owner machine does not run one.
What is established is the code path on upstream origin/main 63d0b94120:
`agent/background_review.py::_run_review_fork` sets a thread tool whitelist
that admits the skills toolset, `agent/tool_executor.py::_pre_tool_block`
dispatches every call through `hermes_cli/plugins.py::_dispatch_pre_tool_call_hooks`,
and `_get_pre_tool_call_directive_details` checks that whitelist and then
calls `invoke_hook("pre_tool_call", tool_name=..., args=...)` for every tool
it admits -- `skill_manage` among them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from _local_package import load_local_package

load_local_package()

from omh.core.errors import OmhError
from omh.hashutil import sha256_file
from omh.install.curator_ledger import CURATOR_LEDGER_RELATIVE_PATH, read_ledger_writes
from omh.install.installer import HERMES_EDIT_RECORD_KEY, install_skill_pack, skill_install_relative_dir
from omh.maintenance.doctor import run_doctor
from omh.manifest import read_manifest
from omh.paths import OmhPaths
from omh.plugin_bundle.omh import skill_ownership
from omh.plugin_bundle.omh.hooks import nudge_budget, session_attendance
from omh.plugin_bundle.omh.hooks.tool_hooks import pre_tool_call
from omh.skill_pack import builtin_skill_reference_templates, builtin_skill_templates

SESSION = "20261013_101010_abc123"
# A core skill that ships reference files, so a supporting-file write has a
# real target.
SKILL = "oh-my-hermes"


def _ledger_row(path: Path, *, actor: str = "curator", action: str = "patch", before: str = "", ts: str = "") -> str:
    """One row the way `tools/skill_ledger.py::append_entry` writes it after `_delta`."""
    real = os.path.realpath(path)
    after = [{"path": real, "sha256": sha256_file(path)}] if path.exists() else []
    before_items = [{"path": real, "sha256": before}] if before else []
    return json.dumps(
        {
            "id": "a1b2c3d4e5f6",
            "ts": ts or "2026-10-13T01:02:03.000000+00:00",
            "actor": actor,
            "action": action,
            "skill": path.parent.name,
            "evidence": {},
            "before": before_items,
            "after": after,
        }
    )


def _append_ledger(hermes_home: Path, *rows: str) -> Path:
    ledger = hermes_home / CURATOR_LEDGER_RELATIVE_PATH
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(row + "\n")
    return ledger


class _Home(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.paths = OmhPaths(omh_home=self.root / "omh", hermes_home=self.root / "hermes")
        self.paths.hermes_home.mkdir(parents=True)

    def skill_file(self, paths: OmhPaths | None = None, name: str = SKILL) -> Path:
        return (paths or self.paths).skills_dir / skill_install_relative_dir(name) / "SKILL.md"

    def hermes_patch(self, skill_file: Path, text: str = "\nlearned: check the lockfile first\n", **row) -> None:
        """Edit a managed file in place and ledger it, as a skill_manage patch does."""
        before = sha256_file(skill_file) if skill_file.exists() else ""
        with skill_file.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        _append_ledger(self.paths.hermes_home, _ledger_row(skill_file, before=before, **row))


class HookRedirectTests(_Home):
    def setUp(self) -> None:
        super().setUp()
        session_attendance.reset_session_attendance()
        nudge_budget.reset_nudge_budget()
        self.addCleanup(session_attendance.reset_session_attendance)
        self.addCleanup(nudge_budget.reset_nudge_budget)
        install_skill_pack(self.paths, profile="core")
        self.label = self.skill_file().parent.name

    def call(self, args: dict, *, session: str = SESSION) -> dict | None:
        # `args`, not `tool_input`: the keyword Hermes passes.
        return pre_tool_call(
            tool_name="skill_manage",
            args=args,
            session_id=session,
            omh_home=str(self.paths.omh_home),
            hermes_home=str(self.paths.hermes_home),
        )

    def test_every_content_write_action_on_a_managed_skill_is_blocked(self) -> None:
        for index, action in enumerate(sorted(skill_ownership.CONTENT_WRITE_ACTIONS)):
            with self.subTest(action=action):
                directive = self.call(
                    {"operations": [{"action": action, "name": self.label, "file_path": "references/x.md"}]},
                    session=f"{SESSION}-{index}",
                )
                self.assertIsNotNone(directive)
                self.assertEqual(directive["action"], "block")
                self.assertIn(f"Do not retry this {action} on '{self.label}'", directive["message"])

    def test_the_block_names_where_the_lesson_goes(self) -> None:
        directive = self.call({"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]})
        message = directive["message"]
        # A differently named companion the fork can create with the one tool it has...
        self.assertIn("skill_manage action=create with a different name", message)
        self.assertIn(f"'{self.label}-notes'", message)
        self.assertIn("rather than a copy of it", message)
        # ...or the workflow-learning lane. A core install does not carry that
        # skill, so the lane is named without a skill_view call that would fail.
        self.assertIn("OMH's workflow-learning lane", message)
        self.assertNotIn("skill_view name=", message)

    def test_the_lane_pointer_names_the_installed_workflow_learning_skill(self) -> None:
        install_skill_pack(self.paths, profile="full")
        directive = self.call({"operations": [{"action": "edit", "name": self.label, "content": "x"}]})
        self.assertIn("(skill_view name='omh-workflow-learning')", directive["message"])

    def test_the_legacy_flat_shape_and_a_mixed_batch_are_read(self) -> None:
        self.assertEqual(self.call({"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"})["action"], "block")
        mixed = {
            "operations": [
                {"action": "create", "name": "my-notes", "content": "---\nname: my-notes\n---\n"},
                {"action": "write_file", "file_path": "references/a.md", "file_content": "x"},
            ],
            "name": self.label,
        }
        self.assertEqual(self.call(mixed, session=f"{SESSION}-mixed")["action"], "block")

    def test_a_skill_omh_did_not_install_passes_untouched(self) -> None:
        # A user's own skill, one wearing OMH's prefix, and OMH's canonical
        # name (Hermes resolves `oh-my-hermes` apart from the installed label).
        for index, name in enumerate(("my-deploy-notes", "omh-notes", SKILL)):
            with self.subTest(name=name):
                self.assertIsNone(
                    self.call(
                        {"operations": [{"action": "patch", "name": name, "old_string": "a", "new_string": "b"}]},
                        session=f"{SESSION}-pass-{index}",
                    )
                )

    def test_create_delete_and_reads_are_not_this_guards_business(self) -> None:
        for index, action in enumerate(("create", "delete")):
            with self.subTest(action=action):
                self.assertIsNone(
                    self.call({"operations": [{"action": action, "name": self.label}]}, session=f"{SESSION}-own-{index}")
                )
        self.assertIsNone(
            pre_tool_call(
                tool_name="skill_view",
                args={"name": self.label},
                session_id=SESSION,
                omh_home=str(self.paths.omh_home),
                hermes_home=str(self.paths.hermes_home),
            )
        )

    def test_a_same_name_skill_in_the_hermes_home_takes_the_edit_and_passes(self) -> None:
        # `_find_skill` serves the profile's own skills directory first, so the
        # edit lands on that copy, which OMH does not own.
        local = self.paths.hermes_home / "skills" / "workflow" / self.label
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text(f"---\nname: {self.label}\n---\n", encoding="utf-8")
        self.assertIsNone(self.call({"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]}))

    def write_config(self, text: str | bytes) -> None:
        config = self.paths.hermes_home / "config.yaml"
        if isinstance(text, bytes):
            config.write_bytes(text)
        else:
            config.write_text(text, encoding="utf-8")

    def patch_call(self, session: str) -> dict | None:
        return self.call(
            {"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]},
            session=session,
        )

    def test_a_same_name_skill_in_skills_create_dir_takes_the_edit_and_passes(self) -> None:
        # `_find_skill` searches create_dir after the profile's own directory
        # and before every external one, relative paths anchored at the home.
        self.write_config("model: x\nskills:\n  external_dirs:\n    - /elsewhere\n  create_dir: fleet-skills  # shared\n")
        copy = self.paths.hermes_home / "fleet-skills" / self.label
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text(f"---\nname: {self.label}\n---\n", encoding="utf-8")
        self.assertIsNone(self.patch_call(f"{SESSION}-create-dir"))

    def test_a_create_dir_without_that_name_still_blocks(self) -> None:
        create_dir = self.root / "fleet-skills"
        (create_dir / "someone-elses-skill").mkdir(parents=True)
        (create_dir / "someone-elses-skill" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
        self.write_config(f'skills:\n  create_dir: "{create_dir}"\n')
        self.assertEqual(self.patch_call(f"{SESSION}-create-dir-other")["action"], "block")
        # A deeper `create_dir` key is some other setting, not the tier.
        copy = self.root / "nested" / self.label
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text(f"---\nname: {self.label}\n---\n", encoding="utf-8")
        self.write_config(f"skills:\n  hub:\n    create_dir: {self.root / 'nested'}\n")
        self.assertEqual(self.patch_call(f"{SESSION}-create-dir-deep")["action"], "block")

    def test_an_empty_skills_block_still_blocks(self) -> None:
        for index, text in enumerate(("skills: {}\n", "skills:\n  create_dir: ~\n", "skills:\n  create_dir: ''\n")):
            with self.subTest(text=text):
                self.write_config(text)
                self.assertEqual(self.patch_call(f"{SESSION}-empty-{index}")["action"], "block")

    def test_a_config_this_scan_cannot_read_lets_the_call_through(self) -> None:
        # Fail open: create_dir might hold the name, and a refusal aimed at a
        # copy OMH does not own is the worse error.
        for index, text in enumerate((b"skills:\n  create_dir: \xff\xfe\n", "skills: {create_dir: fleet-skills}\n")):
            with self.subTest(text=text):
                self.write_config(text)
                self.assertIsNone(self.patch_call(f"{SESSION}-unread-{index}"))

    def test_no_readable_manifest_lets_the_call_through(self) -> None:
        manifest = self.paths.omh_home / "manifest.json"
        manifest.write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.call({"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]}))
        manifest.unlink()
        self.assertIsNone(
            self.call(
                {"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]},
                session=f"{SESSION}-gone",
            )
        )

    def test_a_manifest_record_whose_skill_is_gone_is_not_a_target(self) -> None:
        self.skill_file().unlink()
        self.assertIsNone(self.call({"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]}))


class InstallerKeepsHermesEditTests(_Home):
    def test_a_hermes_edit_is_kept_and_reported_instead_of_refusing_the_update(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        self.hermes_patch(skill_file)
        edited = skill_file.read_bytes()

        result = install_skill_pack(self.paths, profile="core")

        self.assertEqual(skill_file.read_bytes(), edited)
        [item] = result["hermes_edited_skills"]
        self.assertEqual(item["name"], SKILL)
        self.assertEqual(item["disposition"], "kept")
        self.assertEqual((item["actor"], item["action"]), ("curator", "patch"))
        self.assertEqual(item["ts"], "2026-10-13T01:02:03.000000+00:00")
        record = next(record for record in read_manifest(self.paths.manifest_path)["skills"] if record["name"] == SKILL)
        self.assertEqual(record["sha256"], sha256_file(skill_file))
        self.assertEqual(record[HERMES_EDIT_RECORD_KEY]["actor"], "curator")

    def test_a_kept_skill_stays_kept_after_the_ledger_row_is_gone(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        self.hermes_patch(skill_file)
        install_skill_pack(self.paths, profile="core")
        (self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH).unlink()
        edited = skill_file.read_bytes()

        result = install_skill_pack(self.paths, profile="core")

        self.assertEqual(skill_file.read_bytes(), edited)
        self.assertEqual([item["name"] for item in result["hermes_edited_skills"]], [SKILL])

    def test_a_hand_edit_with_no_ledger_row_still_refuses(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        with skill_file.open("a", encoding="utf-8") as handle:
            handle.write("\nlocal edit\n")

        with self.assertRaisesRegex(OmhError, "local modifications detected"):
            install_skill_pack(self.paths, profile="core")

    def test_a_ledger_row_whose_content_is_no_longer_on_disk_is_history(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        rendered = skill_file.read_bytes()
        self.hermes_patch(skill_file)
        skill_file.write_bytes(rendered)
        # A rollback row restoring OMH's render is not an edit either.
        _append_ledger(self.paths.hermes_home, _ledger_row(skill_file, actor="user", action="rollback"))

        result = install_skill_pack(self.paths, profile="core")

        self.assertNotIn("hermes_edited_skills", result)

    def test_force_replaces_the_edit_and_says_so(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        rendered = skill_file.read_bytes()
        self.hermes_patch(skill_file)

        result = install_skill_pack(self.paths, profile="core", force=True)

        self.assertEqual(skill_file.read_bytes(), rendered)
        self.assertEqual([item["disposition"] for item in result["hermes_edited_skills"]], ["replaced"])
        self.assertTrue(all(HERMES_EDIT_RECORD_KEY not in record for record in read_manifest(self.paths.manifest_path)["skills"]))
        # The old row now describes content that is gone, so nothing is kept next time.
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_a_supporting_file_patch_keeps_the_whole_skill(self) -> None:
        install_skill_pack(self.paths, profile="core")
        reference = next(template for template in builtin_skill_reference_templates() if template.skill_name == SKILL)
        target = self.skill_file().parent / reference.relative_path
        self.hermes_patch(target, action="patch")
        patched = target.read_bytes()

        result = install_skill_pack(self.paths, profile="core")

        # Reference files are not in the manifest, so without the ledger this
        # overwrite was silent.
        self.assertEqual(target.read_bytes(), patched)
        self.assertEqual([item["name"] for item in result["hermes_edited_skills"]], [SKILL])

    def test_a_staged_update_carries_the_edit_into_the_new_generation(self) -> None:
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        third = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-3" / "skills")
        install_skill_pack(first, profile="core")
        old_file = self.skill_file(first)
        self.hermes_patch(old_file, action="edit")
        edited = old_file.read_bytes()
        reference = next(template for template in builtin_skill_reference_templates() if template.skill_name == SKILL)

        result = install_skill_pack(second, profile="core")

        self.assertEqual(self.skill_file(second).read_bytes(), edited)
        # Carried whole, supporting files included.
        self.assertTrue((self.skill_file(second).parent / reference.relative_path).is_file())
        self.assertEqual([item["disposition"] for item in result["hermes_edited_skills"]], ["kept"])
        # The next generation carries it again from the manifest's own record:
        # the ledger row named the first generation, and now it is gone too.
        (self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH).unlink()
        carried = install_skill_pack(third, profile="core")
        self.assertEqual(self.skill_file(third).read_bytes(), edited)
        self.assertEqual([item["name"] for item in carried["hermes_edited_skills"]], [SKILL])
        # Every other skill is OMH's current render.
        other = next(template for template in builtin_skill_templates() if template.name == "doctor")
        self.assertEqual(self.skill_file(third, "doctor").read_text(encoding="utf-8"), other.content)

    def test_a_dry_run_reports_without_writing(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        before = self.paths.manifest_path.read_bytes()

        result = install_skill_pack(self.paths, profile="core", dry_run=True)

        self.assertEqual([item["disposition"] for item in result["hermes_edited_skills"]], ["kept"])
        self.assertEqual(self.paths.manifest_path.read_bytes(), before)


class DoctorLedgerTests(_Home):
    def _check(self, name: str):
        return next(check for check in run_doctor(self.paths) if check.name == name)

    def test_a_clean_home_reports_no_write(self) -> None:
        install_skill_pack(self.paths, profile="core")
        check = self._check("hermes_skill_edits")
        self.assertTrue(check.ok)
        self.assertEqual(check.severity, "ok")
        self.assertIn("records no skill write under OMH-managed skills", check.message)

    def test_a_hermes_edit_is_a_warning_not_a_local_modification(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        ledger = self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH
        ledger_bytes = ledger.read_bytes()

        checks = {check.name: check for check in run_doctor(self.paths)}

        edits = checks["hermes_skill_edits"]
        self.assertTrue(edits.ok)
        self.assertEqual(edits.severity, "warning")
        self.assertIn(f"{SKILL} at {skill_install_relative_dir(SKILL).as_posix()}/SKILL.md (curator patch", edits.message)
        self.assertIn("records 1 skill write(s) under OMH-managed skills", edits.message)
        self.assertIn("separately named skill", edits.next_action)
        # The installer keeps it, so it no longer blocks doctor.
        self.assertTrue(checks["local_modifications"].ok)
        # Read only.
        self.assertEqual(ledger.read_bytes(), ledger_bytes)

    def test_a_kept_skill_is_not_reported_stale(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        install_skill_pack(self.paths, profile="core")

        checks = {check.name: check for check in run_doctor(self.paths)}

        self.assertTrue(checks["skill_freshness"].ok, checks["skill_freshness"].message)
        self.assertEqual(checks["hermes_skill_edits"].severity, "warning")

    def test_a_write_outside_omh_skills_is_not_counted(self) -> None:
        install_skill_pack(self.paths, profile="core")
        own = self.paths.hermes_home / "skills" / "my-notes" / "SKILL.md"
        own.parent.mkdir(parents=True)
        own.write_text("---\nname: my-notes\n---\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(own, action="create"))

        self.assertEqual(len(read_ledger_writes(self.paths.hermes_home)[0]), 1)
        check = self._check("hermes_skill_edits")
        self.assertEqual(check.severity, "ok")
        self.assertIn("records no skill write under OMH-managed skills", check.message)

    def test_an_unreadable_ledger_is_named_not_read_as_empty(self) -> None:
        install_skill_pack(self.paths, profile="core")
        ledger = _append_ledger(self.paths.hermes_home)
        ledger.write_bytes(b"\xff\xfe not utf-8")

        check = self._check("hermes_skill_edits")

        self.assertEqual(check.severity, "warning")
        self.assertIn("ledger unreadable:", check.message)


if __name__ == "__main__":
    unittest.main()
