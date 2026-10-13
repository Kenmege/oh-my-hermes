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
  write_file, remove_file and delete on a skill the install manifest records,
  and the block names where the lesson goes. A skill OMH did not install
  passes untouched, whatever its name looks like, and so does one a higher
  tier (the home's own skills, `skills.create_dir`) also holds.
- `InstallerKeepsHermesEditTests`: the installer keeps a skill only while the
  ledger accounts for what is on disk (a changed SKILL.md through the newest
  write to it, and only the files a row changed), carries it into a new
  generation the profile includes, and `--force` replaces the whole skill,
  saying whether the ledger holds a copy. A hand edit with no ledger row
  still refuses, and a removed skill is rendered again.
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

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from _local_package import load_local_package

load_local_package()

from omh.commands import setup as setup_commands
from omh.core.errors import OmhError
from omh.hashutil import sha256_file
from omh.install import installer
from omh.install.config_adapter import memory_provider_selection, skill_create_dir_selection
from omh.install.curator_ledger import CURATOR_LEDGER_RELATIVE_PATH, read_ledger_writes
from omh.install.installer import HERMES_EDIT_RECORD_KEY, install_skill_pack, skill_install_relative_dir
from omh.maintenance import doctor as doctor_module
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


def _change_row(path: Path, *, before_sha: str = "", after_sha: str = "", action: str, ts: str) -> str:
    """A row that changed one file from `before_sha` to `after_sha` ("" on either side: absent)."""
    real = os.path.realpath(path)
    return json.dumps(
        {
            "id": "c0ffee", "ts": ts, "actor": "curator", "action": action, "skill": path.parent.name, "evidence": {},
            "before": [{"path": real, "sha256": before_sha}] if before_sha else [],
            "after": [{"path": real, "sha256": after_sha}] if after_sha else [],
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

    def store_blob(self, path: Path) -> None:
        """Keep a file's content where Hermes keeps it: `.curator_backups/blobs/<sha256>`."""
        blob = self.paths.hermes_home / ".curator_backups" / "blobs" / sha256_file(path)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(path.read_bytes())


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

    def test_every_guarded_action_on_a_managed_skill_is_blocked(self) -> None:
        self.assertEqual(skill_ownership.GUARDED_ACTIONS, {"edit", "patch", "write_file", "remove_file", "delete"})
        for index, action in enumerate(sorted(skill_ownership.GUARDED_ACTIONS)):
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

    def test_create_and_reads_are_not_this_guards_business(self) -> None:
        # `create` refuses a name that already resolves, so it cannot reach an OMH skill.
        self.assertIsNone(self.call({"operations": [{"action": "create", "name": self.label}]}, session=f"{SESSION}-own"))
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

    def test_a_foreground_delete_is_refused_with_its_own_consequence(self) -> None:
        # Upstream guards a delete only inside the background-review fork.
        directive = self.call({"operations": [{"action": "delete", "name": self.label}]}, session=f"{SESSION}-del")
        self.assertEqual(directive["action"], "block")
        self.assertIn(
            "deleting it here does not last: the next update renders it again or reports it missing",
            directive["message"],
        )

    def test_operations_sent_as_a_json_string_are_read(self) -> None:
        # The hook sees the arguments before Hermes coerces them to the schema.
        encoded = json.dumps([{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}])
        self.assertEqual(self.call({"operations": encoded}, session=f"{SESSION}-json")["action"], "block")
        self.assertIsNone(self.call({"operations": "[not json"}, session=f"{SESSION}-badjson"))

    def test_the_block_says_where_a_created_skill_lands(self) -> None:
        self.assertIn("(it lands in this profile's own skills directory)", self.patch_call(f"{SESSION}-lands")["message"])
        create_dir = self.root / "fleet-skills"
        self.write_config(f"skills:\n  create_dir: {create_dir}\n")
        self.assertIn(f"(it lands in {create_dir})", self.patch_call(f"{SESSION}-lands-fleet")["message"])

    def test_a_create_dir_this_reader_cannot_decode_fails_open(self) -> None:
        # `\n` inside double quotes is an escape this reader does not decode;
        # guessing would resolve a directory Hermes does not use.
        self.write_config('skills:\n  create_dir: "C:\\new\\skills"\n')
        self.assertIsNone(self.patch_call(f"{SESSION}-escape"))

    def test_a_quoted_create_dir_with_a_comment_and_a_bom_still_resolves(self) -> None:
        copy = self.root / "fleet-skills" / self.label
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text(f"---\nname: {self.label}\n---\n", encoding="utf-8")
        text = f'skills:\n  create_dir: "{self.root / "fleet-skills"}"  # shared\n'
        self.write_config(b"\xef\xbb\xbf" + text.encode("utf-8"))
        self.assertIsNone(self.patch_call(f"{SESSION}-quoted"))

    def test_a_stat_that_raises_fails_open_instead_of_escaping_the_hook(self) -> None:
        with mock.patch.object(Path, "is_file", side_effect=PermissionError(13, "Permission denied")):
            self.assertIsNone(
                skill_ownership.managed_skill_edit_directive(
                    tool_name="skill_manage",
                    tool_input={"operations": [{"action": "patch", "name": self.label}]},
                    omh_home=str(self.paths.omh_home),
                    hermes_home=str(self.paths.hermes_home),
                )
            )

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
        # No blob stored here, so the report claims no copy.
        self.assertEqual(result["hermes_edited_skills"][0]["ledger_copy"], [])
        self.assertTrue(all(HERMES_EDIT_RECORD_KEY not in record for record in read_manifest(self.paths.manifest_path)["skills"]))
        # The old row now describes content that is gone, so nothing is kept next time.
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_a_row_listing_unchanged_files_does_not_count_them(self) -> None:
        # Hermes before 0.21.4 listed every file of the skill in a row, OMH's
        # own unchanged files included, with the same hash on both sides.
        install_skill_pack(self.paths, profile="core")
        skill_dir = self.skill_file().parent
        files = sorted(path for path in skill_dir.rglob("*") if path.is_file())
        before = [{"path": os.path.realpath(path), "sha256": sha256_file(path)} for path in files]
        with self.skill_file().open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\nlearned at the floor\n")
        after = [{"path": os.path.realpath(path), "sha256": sha256_file(path)} for path in files]
        row = {"id": "f", "ts": "2026-09-08T00:00:00+00:00", "actor": "curator", "action": "patch",
               "skill": skill_dir.name, "evidence": {}, "before": before, "after": after}
        _append_ledger(self.paths.hermes_home, json.dumps(row))

        self.assertEqual([item["disposition"] for item in install_skill_pack(self.paths, profile="core")["hermes_edited_skills"]], ["kept"])
        install_skill_pack(self.paths, profile="core", force=True)
        # OMH's re-rendered supporting files still match the row's after list,
        # and they were never changed by it, so they are not an edit.
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_a_hand_edit_to_skill_md_beside_a_ledgered_support_write_still_refuses(self) -> None:
        install_skill_pack(self.paths, profile="core")
        reference = next(template for template in builtin_skill_reference_templates() if template.skill_name == SKILL)
        self.hermes_patch(self.skill_file().parent / reference.relative_path)
        with self.skill_file().open("a", encoding="utf-8") as handle:
            handle.write("\nmy own edit\n")

        with self.assertRaisesRegex(OmhError, "local modifications detected"):
            install_skill_pack(self.paths, profile="core")

    def test_a_file_hermes_added_does_not_survive_force(self) -> None:
        install_skill_pack(self.paths, profile="core")
        added = self.skill_file().parent / "references" / "lessons.md"
        added.parent.mkdir(exist_ok=True)
        added.write_text("lesson\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(added, action="write_file"))
        self.assertEqual([item["name"] for item in install_skill_pack(self.paths, profile="core")["hermes_edited_skills"]], [SKILL])

        install_skill_pack(self.paths, profile="core", force=True)

        self.assertFalse(added.exists())
        self.assertTrue(self.skill_file().is_file())
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_the_replaced_report_claims_a_ledger_copy_only_when_one_is_stored(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        self.store_blob(self.skill_file())

        stored = install_skill_pack(self.paths, profile="core", dry_run=True, force=True)["hermes_edited_skills"][0]
        self.assertEqual(stored["ledger_copy"], ["SKILL.md"])
        self.assertIn("still holds a copy of SKILL.md", self._summary({"hermes_edited_skills": [stored]}))

        # Kept through the manifest record after the row and its blob are gone:
        # nothing in the ledger can bring the edit back.
        install_skill_pack(self.paths, profile="core")
        (self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH).unlink()
        for blob in (self.paths.hermes_home / ".curator_backups" / "blobs").iterdir():
            blob.unlink()
        trimmed = install_skill_pack(self.paths, profile="core", force=True)["hermes_edited_skills"][0]
        self.assertEqual(trimmed["ledger_copy"], [])
        summary = self._summary({"hermes_edited_skills": [trimmed]})
        self.assertIn("holds no copy of it, so the edit cannot be recovered from there", summary)
        self.assertNotIn("still holds", summary)

    def _summary(self, payload: dict) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup_commands._print_install_summary({"skills": [], **payload}, command="install")
        return out.getvalue()

    def test_a_deleted_managed_skill_is_rendered_again_and_reported(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_dir = self.skill_file().parent
        for path in sorted(skill_dir.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        skill_dir.rmdir()

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(self.skill_file().is_file())
        self.assertEqual(
            result["restored_skills"],
            [{"name": SKILL, "path": f"{skill_install_relative_dir(SKILL).as_posix()}/SKILL.md", "disposition": "restored"}],
        )
        self.assertIn(f"Restored {SKILL} at", self._summary(result))

    def test_the_pruner_leaves_a_kept_skill_the_catalog_dropped(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        install_skill_pack(self.paths, profile="core")
        kept = self.skill_file().read_bytes()
        templates = [template for template in builtin_skill_templates() if template.name != SKILL]
        references = [template for template in builtin_skill_reference_templates() if template.skill_name != SKILL]

        with (
            mock.patch.object(installer, "builtin_skill_templates", return_value=templates),
            mock.patch.object(installer, "builtin_skill_reference_templates", return_value=references),
        ):
            result = install_skill_pack(self.paths, profile="core")

        self.assertEqual(self.skill_file().read_bytes(), kept)
        self.assertNotIn(SKILL, result["pruned_skills"])

    def test_a_kept_full_only_skill_is_not_carried_into_a_core_generation(self) -> None:
        full_only = "workflow-learning"
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        install_skill_pack(first, profile="full")
        self.hermes_patch(self.skill_file(first, full_only))
        self.hermes_patch(self.skill_file(first))

        result = install_skill_pack(second, profile="core")

        self.assertFalse(self.skill_file(second, full_only).exists())
        self.assertEqual([item["name"] for item in result["hermes_edited_skills"]], [SKILL])

    def test_a_file_added_then_removed_is_not_an_edit(self) -> None:
        # Probe P1: a write_file undone by a remove_file leaves OMH's render.
        install_skill_pack(self.paths, profile="core")
        added = self.skill_file().parent / "references" / "lessons.md"
        added.write_text("lesson\n", encoding="utf-8")
        sha = sha256_file(added)
        _append_ledger(self.paths.hermes_home, _change_row(added, after_sha=sha, action="write_file", ts="2026-10-13T01:00:00+00:00"))
        added.unlink()
        _append_ledger(self.paths.hermes_home, _change_row(added, before_sha=sha, action="remove_file", ts="2026-10-13T02:00:00+00:00"))

        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))
        install_skill_pack(self.paths, profile="core", force=True)
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_removing_a_file_omh_renders_is_an_edit_that_is_kept(self) -> None:
        install_skill_pack(self.paths, profile="core")
        reference = next(template for template in builtin_skill_reference_templates() if template.skill_name == SKILL)
        target = self.skill_file().parent / reference.relative_path
        sha = sha256_file(target)
        target.unlink()
        _append_ledger(self.paths.hermes_home, _change_row(target, before_sha=sha, action="remove_file", ts="2026-10-13T02:00:00+00:00"))

        result = install_skill_pack(self.paths, profile="core")

        self.assertEqual([(item["name"], item["action"]) for item in result["hermes_edited_skills"]], [(SKILL, "remove_file")])
        self.assertFalse(target.exists())

    def test_force_replaces_a_hand_edited_skill_holding_a_hermes_file(self) -> None:
        # Probe P2: the hand edit refuses, and --force must clear the file
        # Hermes added, or the next plain install keeps the skill for it.
        install_skill_pack(self.paths, profile="core")
        added = self.skill_file().parent / "references" / "lessons.md"
        added.write_text("lesson\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(added, action="write_file"))
        with self.skill_file().open("a", encoding="utf-8") as handle:
            handle.write("\nmy own hand edit\n")
        with self.assertRaisesRegex(OmhError, "local modifications detected"):
            install_skill_pack(self.paths, profile="core")

        forced = install_skill_pack(self.paths, profile="core", force=True)

        self.assertEqual([(item["name"], item["disposition"]) for item in forced["hermes_edited_skills"]], [(SKILL, "replaced")])
        self.assertFalse(added.exists())
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_force_never_clears_a_copy_of_a_managed_skill_elsewhere_in_the_pack(self) -> None:
        # Probe P3: the manifest records a person's copy under the skill's name.
        install_skill_pack(self.paths, profile="core")
        backup = self.paths.skills_dir / "my-backups" / self.skill_file().parent.name
        backup.mkdir(parents=True)
        (backup / "SKILL.md").write_bytes(self.skill_file().read_bytes())
        (backup / "personal.md").write_text("mine\n", encoding="utf-8")
        install_skill_pack(self.paths, profile="core")
        _append_ledger(self.paths.hermes_home, _ledger_row(backup / "personal.md", action="write_file"))

        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))
        install_skill_pack(self.paths, profile="core", force=True)

        self.assertTrue((backup / "personal.md").is_file())
        self.assertTrue(self.skill_file().is_file())

    def test_a_record_path_that_leaves_the_pack_is_never_followed(self) -> None:
        # Probe P4: a record climbing out of the pack, with kept evidence.
        install_skill_pack(self.paths, profile="core")
        outside = self.root / "outside" / "victim"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("victim\n", encoding="utf-8")
        manifest = read_manifest(self.paths.manifest_path)
        manifest["skills"].append(
            {
                "name": SKILL,
                "path": os.path.relpath(outside / "SKILL.md", self.paths.skills_dir).replace(os.sep, "/"),
                "sha256": sha256_file(outside / "SKILL.md"),
                "source": "builtin",
                HERMES_EDIT_RECORD_KEY: {"actor": "curator", "ts": "t", "action": "patch", "ledger": "l"},
            }
        )
        self.paths.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = install_skill_pack(self.paths, profile="core", force=True)

        self.assertTrue((outside / "SKILL.md").is_file())
        self.assertNotIn("hermes_edited_skills", result)

    def test_force_on_a_staged_update_leaves_the_older_generation_alone(self) -> None:
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        install_skill_pack(first, profile="core")
        self.hermes_patch(self.skill_file(first))
        edited = self.skill_file(first).read_bytes()

        result = install_skill_pack(second, profile="core", force=True)

        # The rollback target keeps exactly what it held.
        self.assertEqual(self.skill_file(first).read_bytes(), edited)
        self.assertEqual([item["disposition"] for item in result["hermes_edited_skills"]], ["replaced"])
        self.assertNotEqual(self.skill_file(second).read_bytes(), edited)

    def test_force_clears_only_skills_this_install_renders_again(self) -> None:
        # Clearing is for a skill the render below writes back; a skill no
        # template renders is the pruner's to judge, never the force clear's.
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        item = installer.hermes_edited_skills(read_manifest(self.paths.manifest_path), self.paths, overwritten=True)[0]
        writes = read_ledger_writes(self.paths.hermes_home)[0]
        rendered = installer.rendered_skill_files()

        _replaced, without = installer._force_replacements([item], self.paths, {"doctor"}, writes, rendered)
        _replaced, with_skill = installer._force_replacements([item], self.paths, {"doctor", SKILL}, writes, rendered)

        self.assertEqual(without, [])
        self.assertEqual(with_skill, [self.skill_file().parent])

    def test_a_skill_removed_from_the_active_generation_is_reported_restored(self) -> None:
        # Probe R1: judged in the pack the manifest describes, not the new one.
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        install_skill_pack(first, profile="core")
        for path in sorted(self.skill_file(first).parent.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.skill_file(first).parent.rmdir()

        result = install_skill_pack(second, profile="core")

        self.assertTrue(self.skill_file(second).is_file())
        self.assertEqual([(item["name"], item["disposition"]) for item in result["restored_skills"]], [(SKILL, "restored")])

    def test_a_dry_run_reports_a_restore_without_writing(self) -> None:
        install_skill_pack(self.paths, profile="core")
        for path in sorted(self.skill_file().parent.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.skill_file().parent.rmdir()

        result = install_skill_pack(self.paths, profile="core", dry_run=True)

        self.assertEqual([item["disposition"] for item in result["restored_skills"]], ["restored"])
        self.assertFalse(self.skill_file().exists())

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


class ConfigScalarTests(unittest.TestCase):
    """A quoted value ends at its closing quote, in the core reader and the plugin's alike."""

    def test_a_trailing_comment_after_a_quoted_value_is_not_part_of_it(self) -> None:
        self.assertEqual(skill_create_dir_selection('skills:\n  create_dir: "/srv/skills"  # shared\n'), "/srv/skills")
        self.assertEqual(memory_provider_selection("memory:\n  provider: 'omh'  # mine\n"), "omh")
        self.assertEqual(skill_ownership.yaml_scalar(" 'it''s' # c"), "it's")
        # Unchanged: an unquoted value still stops at `#`, an unclosed quote is kept as written.
        self.assertEqual(memory_provider_selection("memory:\n  provider: omh # mine\n"), "omh")
        self.assertEqual(memory_provider_selection('memory:\n  provider: "omh\n'), '"omh')

    def test_an_escape_this_reader_does_not_decode_is_not_guessed(self) -> None:
        self.assertIsNone(skill_ownership.yaml_scalar(' "a\\nb"'))
        self.assertIsNone(skill_ownership.yaml_scalar(' "\\u00e9"'))
        self.assertEqual(skill_ownership.yaml_scalar(' "C:\\\\x\\"y"'), 'C:\\x"y')
        # Only a comment may follow a closing quote.
        self.assertIsNone(skill_ownership.yaml_scalar(' "a"b'))
        self.assertEqual(skill_ownership.yaml_scalar(' "a" # note'), "a")
        # Core keeps its answer from before the shared parser for what it cannot read.
        self.assertEqual(memory_provider_selection('memory:\n  provider: "a\\nb"\n'), "a\\nb")

    def test_a_quoted_tilde_is_a_string_not_a_null(self) -> None:
        self.assertEqual(skill_create_dir_selection('skills:\n  create_dir: "~"\n'), "~")
        self.assertFalse(skill_ownership.yaml_null(' "~"'))
        self.assertTrue(skill_ownership.yaml_null(" ~  # unset"))

    def test_a_null_create_dir_is_unset(self) -> None:
        for text in ("skills:\n  create_dir: ~\n", "skills:\n  create_dir: null\n", "skills:\n  create_dir:\n"):
            with self.subTest(text=text):
                self.assertEqual(skill_create_dir_selection(text), "")


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

    def remove_skill(self, name: str = SKILL) -> str:
        skill_dir = self.skill_file(name=name).parent
        for path in sorted(skill_dir.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        skill_dir.rmdir()
        return f"{skill_install_relative_dir(name).as_posix()}/SKILL.md"

    def test_a_missing_skill_the_next_install_restores_is_a_warning_without_force(self) -> None:
        install_skill_pack(self.paths, profile="core")
        rel = self.remove_skill()

        checks = {check.name: check for check in run_doctor(self.paths)}

        # Not a hand edit: no blocking local modification, no --force advice.
        self.assertTrue(checks["local_modifications"].ok, checks["local_modifications"].message)
        missing = checks["missing_managed_skills"]
        self.assertTrue(missing.ok)
        self.assertEqual(missing.severity, "warning")
        self.assertIn(f"{SKILL} at {rel}: the next install renders it again", missing.message)
        self.assertNotIn("omh install --force", missing.next_action)
        # And the installer does what doctor said.
        self.assertEqual(
            [item["disposition"] for item in install_skill_pack(self.paths, profile="core")["restored_skills"]],
            ["restored"],
        )

    def test_a_missing_skill_outside_the_recorded_profile_reads_not_restored(self) -> None:
        full_only = "workflow-learning"
        install_skill_pack(self.paths, profile="full")
        # A core run keeps installed full-only skills and records `core`.
        install_skill_pack(self.paths, profile="core")
        rel = self.remove_skill(full_only)

        check = next(check for check in run_doctor(self.paths) if check.name == "missing_managed_skills")

        self.assertIn(f"{full_only} at {rel}: the recorded core profile does not include it", check.message)
        self.assertIn("reports it not_restored and leaves it uninstalled", check.message)
        self.assertEqual(
            [item["disposition"] for item in install_skill_pack(self.paths, profile="core")["restored_skills"]],
            ["not_restored"],
        )

    def test_a_hand_edit_keeps_its_force_advice(self) -> None:
        install_skill_pack(self.paths, profile="core")
        with self.skill_file().open("a", encoding="utf-8") as handle:
            handle.write("\nmy own edit\n")

        checks = {check.name: check for check in run_doctor(self.paths)}

        self.assertFalse(checks["local_modifications"].ok)
        self.assertEqual(checks["local_modifications"].severity, "blocking")
        self.assertIn("omh install --force", checks["local_modifications"].next_action)
        self.assertNotIn("missing_managed_skills", checks)

    def test_doctor_reads_the_ledger_once_and_only_rows_under_omh_paths(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        own = self.paths.hermes_home / "skills" / "my-notes" / "SKILL.md"
        own.parent.mkdir(parents=True)
        own.write_text("x\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(own, action="create"), "{not json")

        within = read_ledger_writes(self.paths.hermes_home, within=[self.paths.skills_dir])[0]
        self.assertEqual([Path(write.path).name for write in within], ["SKILL.md"])
        self.assertTrue(all("my-notes" not in write.path for write in within))
        calls: list[object] = []
        real = doctor_module.read_ledger_writes

        def counting(*args, **kwargs):
            calls.append(kwargs.get("within"))
            return real(*args, **kwargs)

        with (
            mock.patch.object(doctor_module, "read_ledger_writes", side_effect=counting),
            mock.patch.object(installer, "read_ledger_writes", side_effect=counting),
        ):
            check = next(check for check in run_doctor(self.paths) if check.name == "hermes_skill_edits")
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0])
        self.assertEqual(check.severity, "warning")

    def test_an_unreadable_ledger_is_named_not_read_as_empty(self) -> None:
        install_skill_pack(self.paths, profile="core")
        ledger = _append_ledger(self.paths.hermes_home)
        ledger.write_bytes(b"\xff\xfe not utf-8")

        check = self._check("hermes_skill_edits")

        self.assertEqual(check.severity, "warning")
        self.assertIn("ledger unreadable:", check.message)


if __name__ == "__main__":
    unittest.main()
