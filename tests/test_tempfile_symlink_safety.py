"""A symlink planted at a predictable temp or lock name must not redirect a write.

`append_ledger_record` used to write `.asks.jsonl.<pid>.tmp` with a plain
open and chmod, both of which follow a link; the shared delivery lock touched,
chmodded and opened `.<name>.lock` the same way; and the self-update pointer
and launcher swaps used `.<name>.<pid>.tmp`. Each test plants a link at the
name the old code would have used and checks the victim is left byte- and
mode-identical, then checks the normal path still works.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _local_package import load_local_package
from _platform_support import requires_posix_permissions, requires_symlinks

load_local_package()
from omh.install import self_update_state  # noqa: E402
from omh.install.self_update_platform import SelfUpdatePlatform  # noqa: E402
from omh.plugin_bundle.omh.awareness_delivery import _awareness_delivery_lock  # noqa: E402
from omh.plugin_bundle.omh.jev_ask_store import (  # noqa: E402
    append_ledger_record,
    ledger_path,
    read_ledger_records,
)
from omh.plugin_bundle.omh.jev_ask_store import LEDGER_SCHEMA_VERSION  # noqa: E402

VICTIM_TEXT = "VICTIM-ORIGINAL\n"


def _victim(directory: Path) -> Path:
    victim = directory / "victim.txt"
    victim.write_text(VICTIM_TEXT, encoding="utf-8")
    victim.chmod(0o644)
    return victim


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _record(n: int) -> dict[str, object]:
    return {"schema_version": LEDGER_SCHEMA_VERSION, "n": n}


@requires_symlinks
@requires_posix_permissions
class PlantedSymlinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.home = self.root / "home"
        self.victim = _victim(self.root)
        self.path = ledger_path(self.home)
        self.path.parent.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def assertVictimUntouched(self) -> None:
        self.assertEqual(self.victim.read_text(encoding="utf-8"), VICTIM_TEXT)
        self.assertEqual(_mode(self.victim), 0o644)

    def test_ledger_temp_name_symlink_does_not_redirect_the_write(self):
        planted = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        planted.symlink_to(self.victim)

        self.assertTrue(append_ledger_record(self.home, _record(1)))

        self.assertVictimUntouched()
        self.assertFalse(self.path.is_symlink())
        self.assertEqual([r["n"] for r in read_ledger_records(self.home)], [1])
        self.assertTrue(planted.is_symlink(), "the planted entry is not ours to remove")

    def test_ledger_lock_symlink_is_refused_without_touching_the_target(self):
        lock = self.path.with_name(f".{self.path.name}.lock")
        lock.symlink_to(self.victim)

        self.assertFalse(append_ledger_record(self.home, _record(1)))

        self.assertVictimUntouched()
        self.assertFalse(self.path.exists())

    def test_dangling_lock_symlink_creates_nothing_at_its_target(self):
        lock = self.path.with_name(f".{self.path.name}.lock")
        target = self.root / "created-by-attacker.txt"
        lock.symlink_to(target)

        with self.assertRaises(OSError):
            with _awareness_delivery_lock(self.path):
                pass

        self.assertFalse(os.path.lexists(target))

    def test_lock_path_that_is_not_a_regular_file_is_refused(self):
        self.path.with_name(f".{self.path.name}.lock").mkdir()
        with self.assertRaises(OSError):
            with _awareness_delivery_lock(self.path):
                pass

    def test_self_update_pointer_swap_ignores_a_planted_old_temp_name(self):
        root = self.root / "update-root"
        generation = root / "generations" / "g1"
        generation.mkdir(parents=True)
        planted = root / f".current.{os.getpid()}.tmp"
        planted.symlink_to(self.victim)

        SelfUpdatePlatform.host(is_windows=False).replace_current(root, generation)

        self.assertVictimUntouched()
        self.assertTrue(planted.is_symlink())
        self.assertEqual((root / "current").resolve(), generation.resolve())
        self.assertEqual(sorted(p.name for p in root.glob(".current.*")), [planted.name])

    def test_launcher_retarget_ignores_a_planted_old_temp_name(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        launcher = bin_dir / "omh"
        launcher.symlink_to(self.root / "old-omh")
        planted = bin_dir / f".omh.{os.getpid()}.tmp"
        planted.symlink_to(self.victim)
        root = self.root / "update-root"
        platform = SelfUpdatePlatform.host(is_windows=False)

        with patch.object(self_update_state, "managed_command_bin_dir", return_value=bin_dir):
            self.assertTrue(self_update_state._retarget_launcher(root, platform))

        self.assertVictimUntouched()
        self.assertTrue(planted.is_symlink())
        self.assertEqual(os.readlink(planted), str(self.victim))
        self.assertEqual(
            os.readlink(launcher), str(platform.scripts_dir(root / "current" / "venv") / "omh")
        )
        self.assertEqual(sorted(p.name for p in bin_dir.glob(".omh.*")), [planted.name])


class NormalPathTest(unittest.TestCase):
    def test_ledger_appends_keep_order_and_leave_no_temporaries(self):
        with TemporaryDirectory() as temporary:
            home = Path(temporary)
            for n in range(3):
                self.assertTrue(append_ledger_record(home, _record(n)))
            path = ledger_path(home)
            self.assertEqual([r["n"] for r in read_ledger_records(home)], [0, 1, 2])
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            if os.name == "posix":
                self.assertEqual(_mode(path), 0o600)
                self.assertEqual(_mode(path.with_name(f".{path.name}.lock")), 0o600)

    def test_lock_is_reentrant_across_calls_and_reuses_an_existing_lock_file(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "state" / "ledger.json"
            with _awareness_delivery_lock(path) as first:
                pass
            lock = path.with_name(f".{path.name}.lock")
            self.assertTrue(lock.is_file())
            if os.name == "posix":
                lock.chmod(0o644)
            with _awareness_delivery_lock(path) as second:
                pass
            self.assertEqual(first, second)
            if os.name == "posix":
                self.assertEqual(_mode(lock), 0o600)


if __name__ == "__main__":
    unittest.main()
