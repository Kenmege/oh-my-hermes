"""The OMH memory store must not write, chmod or adopt anything outside itself.

An external review reproduced two escapes. ``_principal_key`` chmodded and
read whatever a link planted at ``memory/principal.key`` pointed at, so an
outside file became OMH's HMAC key. The store writers created, chmodded and
wrote through a linked ``memory`` root or store subdirectory, and the block
writer wrote through a linked target file. Each test plants a link, checks
the call is refused with ``OSError`` and that the outside file and directory
keep their bytes and mode, and the last class checks a real store still works.
"""

from __future__ import annotations

import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _local_package import load_local_package
from _platform_support import requires_posix_permissions, requires_symlinks

load_local_package()
from omh.plugin_bundle.omh.memory_blocks import (  # noqa: E402
    approve_memory_block,
    block_path,
    build_memory_block,
    write_memory_block,
)
from omh.plugin_bundle.omh.memory_principals import PrincipalContractError, derive_principal_ref  # noqa: E402
from omh.plugin_bundle.omh.memory_session_lens import publish_session_lens, session_lens_path  # noqa: E402
from omh.plugin_bundle.omh.memory_store_io import (  # noqa: E402
    memory_dir,
    memory_index_path,
    write_memory_index,
    write_project_memory_review_decision,
)

VICTIM_KEY = bytes(range(32))


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _derive(home: Path) -> str:
    return derive_principal_ref(home, profile_ref="default", surface_ref="cli", host_identity="operator")


def _lens() -> dict[str, object]:
    return {"shared_surface": False, "writes_enabled": True, "principal_context": None, "homes": [], "project_identity": "", "executor_target": "hermes"}


@requires_symlinks
@requires_posix_permissions
class PlantedStoreLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name).resolve()
        self.home = root / "omh"
        self.home.mkdir()
        self.outside = root / "outside"
        self.outside.mkdir()
        self.outside.chmod(0o755)
        self.victim = self.outside / "victim.key"
        self.victim.write_bytes(VICTIM_KEY)
        self.victim.chmod(0o644)

    def _assert_outside_untouched(self) -> None:
        self.assertEqual(sorted(path.name for path in self.outside.iterdir()), ["victim.key"])
        self.assertEqual(self.victim.read_bytes(), VICTIM_KEY)
        self.assertEqual(_mode(self.victim), 0o644)
        self.assertEqual(_mode(self.outside), 0o755)

    def test_a_linked_memory_root_is_refused_by_every_writer(self) -> None:
        memory_dir(self.home).symlink_to(self.outside, target_is_directory=True)
        writes = {
            "principal_key": lambda: _derive(self.home),
            "index": lambda: write_memory_index(self.home),
            "review": lambda: write_project_memory_review_decision(self.home, {"review_id": "review_1"}),
            "block": lambda: write_memory_block(self.home, approve_memory_block(build_memory_block("facts", "v"))),
            "session_lens": lambda: publish_session_lens(self.home, "session-a", _lens(), reset=True),
        }
        for name, write in writes.items():
            with self.subTest(writer=name):
                with self.assertRaises(OSError):
                    write()
                self._assert_outside_untouched()

    def test_a_linked_store_subdirectory_is_refused(self) -> None:
        store = memory_dir(self.home)
        store.mkdir()
        cases = {
            "reviews": lambda: write_project_memory_review_decision(self.home, {"review_id": "review_1"}),
            "blocks": lambda: write_memory_block(self.home, approve_memory_block(build_memory_block("facts", "v"))),
            "sessions": lambda: publish_session_lens(self.home, "session-a", _lens(), reset=True),
        }
        for subdirectory, write in cases.items():
            with self.subTest(subdirectory=subdirectory):
                link = store / subdirectory
                link.symlink_to(self.outside, target_is_directory=True)
                self.addCleanup(link.unlink)
                # `records/`, `reviews/` and `candidates/` already raised the
                # store's ValueError on a resolved escape; the rest raise OSError.
                with self.assertRaises((OSError, ValueError)):
                    write()
                self._assert_outside_untouched()

    def test_a_linked_principal_key_is_never_chmodded_or_adopted(self) -> None:
        store = memory_dir(self.home)
        store.mkdir()
        (store / "principal.key").symlink_to(self.victim)
        with self.assertRaises(OSError):
            _derive(self.home)
        self._assert_outside_untouched()
        self.assertTrue((store / "principal.key").is_symlink())

    def test_a_linked_record_target_is_not_written_through(self) -> None:
        block = approve_memory_block(build_memory_block("facts", "planted"))
        target = block_path(self.home, block.label, block.tier)
        target.parent.mkdir(parents=True)
        target.symlink_to(self.victim)
        with self.assertRaises(OSError):
            write_memory_block(self.home, block)
        self._assert_outside_untouched()
        self.assertTrue(target.is_symlink())


class PrincipalKeyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.home = Path(self._temporary.name) / "omh"

    def test_a_key_of_the_wrong_length_is_refused(self) -> None:
        store = memory_dir(self.home)
        store.mkdir(parents=True)
        for size in (0, 31, 33):
            with self.subTest(size=size):
                (store / "principal.key").write_bytes(b"k" * size)
                with self.assertRaises(PrincipalContractError):
                    _derive(self.home)

    def test_a_directory_at_the_key_name_is_refused(self) -> None:
        (memory_dir(self.home) / "principal.key").mkdir(parents=True)
        with self.assertRaises((OSError, PrincipalContractError)):
            _derive(self.home)


class LegitimateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.home = Path(self._temporary.name) / "omh"

    def test_a_real_store_still_creates_its_key_and_writes(self) -> None:
        first = _derive(self.home)
        self.assertEqual(_derive(self.home), first)
        key = memory_dir(self.home) / "principal.key"
        self.assertEqual(len(key.read_bytes()), 32)
        write_memory_index(self.home)
        write_project_memory_review_decision(self.home, {"review_id": "review_1"})
        written = write_memory_block(self.home, approve_memory_block(build_memory_block("facts", "v")))
        publish_session_lens(self.home, "session-a", _lens(), reset=True)
        for path in (memory_index_path(self.home), written, session_lens_path(self.home, "session-a")):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
        if os.name == "posix":
            self.assertEqual(_mode(key), 0o600)
            self.assertEqual(_mode(memory_dir(self.home)), 0o700)
            self.assertEqual(_mode(memory_index_path(self.home)), 0o600)


if __name__ == "__main__":
    unittest.main()
