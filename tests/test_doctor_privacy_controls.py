"""`omh doctor --no-personal-files` and `--no-state-write`, through the real CLI entry.

Personal-file mode is proven by watching every `open` of SOUL.md,
memories/MEMORY.md and memories/USER.md with an audit hook (any reader, any
module) and, as a second net, by replacing the two known readers with raisers.
State-write mode is proven by snapshotting every file under the OMH home --
bytes and mtime -- before and after the run, including when a check or an
advisory raises.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from _cli_harness import run_cli
from _test_temp_root import make_test_tempdir

PERSONAL_FILE_NAMES = frozenset({"SOUL.md", "MEMORY.md", "USER.md"})
SKIPPED_ADVISORIES = ("soul_missing_or_starter", "hermes_memory_staleness")

# Audit hooks cannot be removed, so one hook is installed for the module and
# records only while a test has armed it.
_WATCH: dict[str, object] = {"root": None, "opens": []}


def _audit(event: str, args: tuple[object, ...]) -> None:
    root = _WATCH["root"]
    if root is None or event != "open" or not args:
        return
    target = args[0]
    if not isinstance(target, (str, bytes, os.PathLike)):
        return
    path = os.fsdecode(target)
    if path.startswith(str(root)) and Path(path).name in PERSONAL_FILE_NAMES:
        opens = _WATCH["opens"]
        assert isinstance(opens, list)
        opens.append(path)


sys.addaudithook(_audit)


def _raiser(name: str):
    def _raise(*_args: object, **_kwargs: object) -> object:
        raise AssertionError(f"{name} read a Hermes personal file body under --no-personal-files")

    return _raise


class DoctorPrivacyControlsTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = make_test_tempdir(prefix="omh-doctor-privacy-")
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.omh_home = self.root / ".omh"
        self.hermes_home = self.root / ".hermes"
        self.base = ["--omh-home", str(self.omh_home), "--hermes-home", str(self.hermes_home)]
        self.addCleanup(self._disarm)

    # -- fixtures -----------------------------------------------------------

    def _setup_install(self) -> None:
        with patch("omh.command_path.shutil.which", return_value="/usr/local/bin/omh"):
            status, _stdout, stderr = run_cli(self.base + ["setup", "--json"], output_json=False)
        self.assertEqual(status, 0, stderr)

    def _write_personal_files(self) -> None:
        memories = self.hermes_home / "memories"
        memories.mkdir(parents=True, exist_ok=True)
        (self.hermes_home / "SOUL.md").write_text("A private persona the operator wrote.\n", encoding="utf-8")
        (memories / "MEMORY.md").write_text("private fact one\n§\nprivate fact two\n", encoding="utf-8")
        (memories / "USER.md").write_text("private user note\n", encoding="utf-8")

    def _arm(self) -> list[str]:
        opens: list[str] = []
        _WATCH["opens"] = opens
        _WATCH["root"] = str(self.root)
        return opens

    def _disarm(self) -> None:
        _WATCH["root"] = None

    def _snapshot(self) -> dict[str, tuple[str, int]]:
        """Every path under the OMH home with its content digest and mtime."""
        if not self.omh_home.exists():
            return {}
        return {
            str(path.relative_to(self.omh_home)): (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "<dir>",
                path.stat().st_mtime_ns,
            )
            for path in sorted(self.omh_home.rglob("*"))
        }

    def _doctor(self, *flags: str, json_output: bool = True) -> tuple[int, str]:
        argv = self.base + ["doctor", *(["--json"] if json_output else []), *flags]
        with patch("omh.command_path.shutil.which", return_value="/usr/local/bin/omh"):
            status, stdout, stderr = run_cli(argv, output_json=False)
        self.assertEqual(stderr, "")
        return status, stdout

    def _advisories(self, payload: dict[str, object]) -> dict[str, dict[str, object]]:
        advisories = payload["advisories"]
        assert isinstance(advisories, dict)
        return {str(entry["check_id"]): entry for entry in advisories["entries"]}

    def _checks(self, payload: dict[str, object]) -> dict[str, dict[str, object]]:
        checks = payload["checks"]
        assert isinstance(checks, list)
        return {str(check["name"]): check for check in checks}

    @property
    def _state_path(self) -> Path:
        return self.omh_home / "runtime" / "state.json"

    # -- personal files -----------------------------------------------------

    def test_no_personal_files_never_opens_soul_memory_or_user(self) -> None:
        self._setup_install()
        self._write_personal_files()
        opens = self._arm()
        with (
            patch("omh.maintenance.advisory.check_soul_missing_or_starter", _raiser("check_soul_missing_or_starter")),
            patch("omh.maintenance.advisory.check_hermes_memory_staleness", _raiser("check_hermes_memory_staleness")),
            patch("omh.plugin_bundle.omh.hermes_memory.read_hermes_memory", _raiser("read_hermes_memory")),
            patch("omh.plugin_bundle.omh.hermes_memory.read_hermes_memory_file", _raiser("read_hermes_memory_file")),
        ):
            _status, stdout = self._doctor("--no-personal-files")
        self._disarm()
        self.assertEqual(opens, [])
        payload = json.loads(stdout)
        advisories = self._advisories(payload)
        for check_id in SKIPPED_ADVISORIES:
            self.assertEqual(advisories[check_id]["status"], "skipped")
            self.assertIn("--no-personal-files", str(advisories[check_id]["reason"]))
        self.assertIn(advisories["auxiliary_routing_unset"]["status"], {"advice", "ok", "unobserved"})
        self.assertNotIn("reason", advisories["auxiliary_routing_unset"])
        controls = payload["privacy_controls"]
        self.assertEqual(controls["personal_files"], "not_read")
        self.assertEqual(
            controls["personal_files_skipped"],
            ["advisory:soul_missing_or_starter", "advisory:hermes_memory_staleness"],
        )
        self.assertEqual(controls["state_write"], "enabled")

    def test_default_run_does_open_the_personal_files(self) -> None:
        # The positive control: the watcher sees the reads it must not see above.
        self._setup_install()
        self._write_personal_files()
        opens = self._arm()
        _status, stdout = self._doctor()
        self._disarm()
        self.assertEqual(
            {Path(path).name for path in opens},
            {"SOUL.md", "MEMORY.md", "USER.md"},
        )
        advisories = self._advisories(json.loads(stdout))
        self.assertEqual(advisories["soul_missing_or_starter"]["status"], "ok")
        self.assertNotEqual(advisories["hermes_memory_staleness"]["status"], "skipped")

    def test_no_personal_files_leaves_every_check_unchanged(self) -> None:
        self._setup_install()
        self._write_personal_files()
        _status, default_out = self._doctor("--no-state-write")
        _status, private_out = self._doctor("--no-state-write", "--no-personal-files")
        default_checks = self._checks(json.loads(default_out))
        private_checks = self._checks(json.loads(private_out))
        self.assertEqual(sorted(default_checks), sorted(private_checks))
        for name, check in default_checks.items():
            self.assertEqual((check["ok"], check["severity"]), (private_checks[name]["ok"], private_checks[name]["severity"]), name)
        default_adv = self._advisories(json.loads(default_out))
        private_adv = self._advisories(json.loads(private_out))
        self.assertEqual(list(default_adv), list(private_adv))
        for check_id, entry in default_adv.items():
            if check_id not in SKIPPED_ADVISORIES:
                self.assertEqual(entry, private_adv[check_id], check_id)

    def test_human_output_lists_skipped_advisories(self) -> None:
        self._setup_install()
        self._write_personal_files()
        _status, stdout = self._doctor("--no-personal-files", json_output=False)
        self.assertIn("Personal files: not read (--no-personal-files)", stdout)
        for check_id in SKIPPED_ADVISORIES:
            self.assertIn(f"- {check_id}: skipped (not run: personal-file reads are off", stdout)

    # -- state write --------------------------------------------------------

    def test_no_state_write_leaves_absent_state_absent_and_writes_nothing(self) -> None:
        # An empty home is where a default doctor run creates runtime/ and state.json.
        status, stdout = self._doctor("--no-state-write")
        payload = json.loads(stdout)
        self.assertFalse(self._state_path.exists())
        self.assertFalse(self.omh_home.exists(), sorted(str(p) for p in self.omh_home.rglob("*")))
        self.assertEqual(payload["state_log"], {})
        self.assertEqual(payload["privacy_controls"]["state_write"], "disabled")
        self.assertEqual(payload["privacy_controls"]["personal_files"], "read")
        checks = self._checks(payload)
        for name in ("runtime_artifacts", "workflow_state"):
            self.assertFalse(checks[name]["observed"], name)
            self.assertIn("not probed (--no-state-write)", str(checks[name]["message"]))
        self.assertIn(status, {0, 1})

    def test_no_state_write_keeps_existing_state_bytes_and_mtime(self) -> None:
        self._setup_install()
        self.assertTrue(self._state_path.is_file())
        before = self._snapshot()
        self._doctor("--no-state-write")
        self.assertEqual(self._snapshot(), before)
        self.assertNotIn("last_doctor", json.loads(self._state_path.read_text(encoding="utf-8")))

    def test_no_state_write_holds_when_an_advisory_raises(self) -> None:
        self._setup_install()
        before = self._snapshot()
        with patch("omh.commands.setup.run_doctor_advisories", side_effect=RuntimeError("advisory blew up")):
            with self.assertRaises(RuntimeError):
                self._doctor("--no-state-write")
        self.assertEqual(self._snapshot(), before)

    def test_no_state_write_holds_when_a_check_raises(self) -> None:
        with patch("omh.maintenance.doctor._security_posture_check", side_effect=RuntimeError("check blew up")):
            with self.assertRaises(RuntimeError):
                self._doctor("--no-state-write")
        self.assertFalse(self.omh_home.exists())

    def test_human_output_says_state_write_is_disabled(self) -> None:
        self._setup_install()
        before = self._snapshot()
        _status, stdout = self._doctor("--no-state-write", json_output=False)
        self.assertIn("State write: disabled (--no-state-write)", stdout)
        self.assertNotIn("last_doctor", stdout)
        self.assertEqual(self._snapshot(), before)

    # -- independence and defaults -----------------------------------------

    def test_flags_are_independent(self) -> None:
        self._setup_install()
        self._write_personal_files()
        _status, stdout = self._doctor("--no-personal-files")
        payload = json.loads(stdout)
        self.assertEqual(payload["state_log"].get("entry"), "last_doctor")
        self.assertIn("last_doctor", json.loads(self._state_path.read_text(encoding="utf-8")))

        before = self._snapshot()
        opens = self._arm()
        _status, stdout = self._doctor("--no-state-write")
        self._disarm()
        self.assertEqual(self._snapshot(), before)
        self.assertEqual({Path(path).name for path in opens}, {"SOUL.md", "MEMORY.md", "USER.md"})
        advisories = self._advisories(json.loads(stdout))
        for check_id in SKIPPED_ADVISORIES:
            self.assertNotEqual(advisories[check_id]["status"], "skipped")

    def test_default_run_is_unchanged(self) -> None:
        self._setup_install()
        _status, stdout = self._doctor()
        payload = json.loads(stdout)
        self.assertNotIn("privacy_controls", payload)
        self.assertEqual(payload["state_log"]["entry"], "last_doctor")
        self.assertIn("last_doctor", json.loads(self._state_path.read_text(encoding="utf-8")))
        checks = self._checks(payload)
        self.assertTrue(checks["runtime_artifacts"]["observed"])
        self.assertEqual(checks["runtime_artifacts"]["message"], f"{self.omh_home / 'runtime'} writable")
        for entry in payload["advisories"]["entries"]:
            self.assertNotEqual(entry["status"], "skipped")
            self.assertNotIn("reason", entry)


if __name__ == "__main__":
    unittest.main()
