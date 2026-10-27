"""A parent directory swapped mid-write must not redirect the Jev ask ledger.

`append_ledger_record` used to check `<omh_home>/jev` for a symlink and then
create its temp file, take its lock and rename by pathname, so swapping that
directory for a symlink between the check and the write sent the ledger into
the attacker's directory. Each test performs the swap at a deterministic point
-- after the parent is checked and opened, before the temp file exists -- and
checks the write landed in the original directory and the target is untouched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _local_package import load_local_package
from _platform_support import requires_secure_dir_io, requires_symlinks

load_local_package()
from omh.plugin_bundle.omh import jev_ask_store  # noqa: E402
from omh.plugin_bundle.omh.jev_ask_store import (  # noqa: E402
    LEDGER_SCHEMA_VERSION,
    append_ledger_record,
    ledger_path,
)


def _record(n: int) -> dict[str, object]:
    return {"schema_version": LEDGER_SCHEMA_VERSION, "n": n}


class _SwapOnSerialize:
    """Stands in for the store's `json`; the first `dumps` call performs the swap.

    Both the old and the new write serialize the record after the parent check
    and before creating the temp file, so this is the race window either way.
    """

    def __init__(self, swap) -> None:
        self._swap = swap
        self.swapped = False

    def dumps(self, *args, **kwargs):
        if not self.swapped:
            self.swapped = True
            self._swap()
        return json.dumps(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(json, name)


@requires_symlinks
@requires_secure_dir_io
class ParentDirectorySwapTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name).resolve()
        self.home = root / "home"
        self.path = ledger_path(self.home)
        self.path.parent.mkdir(parents=True, mode=0o700)
        self.moved = self.path.parent.with_name("jev.moved")
        self.attacker = root / "attacker"
        self.attacker.mkdir()

    def _swap_parent_for_symlink(self) -> None:
        os.rename(self.path.parent, self.moved)
        os.symlink(self.attacker, self.path.parent)

    def _append_with_swap(self, n: int) -> tuple[bool, _SwapOnSerialize]:
        shim = _SwapOnSerialize(self._swap_parent_for_symlink)
        with patch.object(jev_ask_store, "json", shim):
            written = append_ledger_record(self.home, _record(n))
        return written, shim

    def test_swap_after_the_check_keeps_the_write_in_the_original_directory(self):
        self.assertTrue(append_ledger_record(self.home, _record(0)))

        written, shim = self._append_with_swap(1)

        self.assertTrue(shim.swapped, "the swap hook never ran")
        self.assertTrue(written)
        self.assertEqual(sorted(os.listdir(self.attacker)), [], "attacker directory was written")
        moved_ledger = self.moved / self.path.name
        rows = [json.loads(line)["n"] for line in moved_ledger.read_text().splitlines()]
        self.assertEqual(rows, [0, 1])
        self.assertEqual([p.name for p in self.moved.glob("*.tmp")], [])

    def test_swap_on_first_write_creates_nothing_in_the_attacker_directory(self):
        written, shim = self._append_with_swap(1)

        self.assertTrue(shim.swapped)
        self.assertTrue(written)
        self.assertEqual(sorted(os.listdir(self.attacker)), [])
        self.assertTrue((self.moved / self.path.name).is_file())
        self.assertTrue((self.moved / f".{self.path.name}.lock").is_file())

    def test_parent_symlinked_before_the_call_is_refused(self):
        self._swap_parent_for_symlink()

        self.assertFalse(append_ledger_record(self.home, _record(1)))

        self.assertEqual(sorted(os.listdir(self.attacker)), [])

    def test_ledger_symlink_is_neither_read_nor_replaced(self):
        target = self.attacker / "secret.jsonl"
        target.write_text(json.dumps(_record(99)) + "\n", encoding="utf-8")
        self.path.symlink_to(target)

        self.assertFalse(append_ledger_record(self.home, _record(1)))

        self.assertTrue(self.path.is_symlink())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["n"], 99)


if __name__ == "__main__":
    unittest.main()
