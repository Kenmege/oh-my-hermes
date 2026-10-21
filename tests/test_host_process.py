"""`host_process`: whether the Hermes process a helper ran in still runs, through Hermes' own probes only.

The probes are `gateway.status`'s, as the Hermes process provides them. Only an
answer they give that the recorded process ended reads as gone; anything they
cannot tell, and everything outside Hermes, reads as unknown.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from _local_package import load_local_package
from _module_patch import patch_modules

load_local_package()

from omh.plugin_bundle.omh import host_process  # noqa: E402

NO_HERMES = {"gateway": None, "gateway.status": None}


class _Broken:
    """A Hermes `gateway.status` that fails to load its names with something other than an ImportError."""

    def __getattr__(self, name: str) -> object:
        raise RuntimeError("half-initialized module")


BROKEN_HERMES = {"gateway": SimpleNamespace(status=_Broken()), "gateway.status": _Broken()}


def hermes(*, exists: bool = True, started: object = 777, raises: bool = False) -> dict[str, object]:
    def start_time(pid: int) -> object:
        if raises:
            raise RuntimeError("probe exploded")
        return started

    status = SimpleNamespace(_pid_exists=lambda pid: exists, get_process_start_time=start_time,
                             start_time_fingerprints_match=lambda recorded, current: abs(current - recorded) <= 200)
    return {"gateway": SimpleNamespace(status=status), "gateway.status": status}


class ThisProcess(unittest.TestCase):
    def test_outside_hermes_nothing_is_recorded(self) -> None:
        for probes in (NO_HERMES, BROKEN_HERMES):
            with self.subTest(broken=probes is BROKEN_HERMES), patch_modules(probes):
                self.assertIsNone(host_process.this_process())

    def test_the_pid_and_hermes_start_time_or_none_when_unread(self) -> None:
        for probes, started in ((hermes(started=777), 777), (hermes(started=None), None),
                                (hermes(started="777"), None), (hermes(raises=True), None)):
            with self.subTest(started=started), patch_modules(probes):
                self.assertEqual(host_process.this_process(),
                                 {"pid": os.getpid(), "started": started, "machine": host_process.machine()})


class Machine(unittest.TestCase):
    """Mutation: leave the pid namespace or the boot id out on Linux, or let a host name in elsewhere."""

    def linux(self, boot_id: str | None, namespace: str) -> str:
        with tempfile.TemporaryDirectory() as folder:
            boot = Path(folder) / "boot_id"
            if boot_id is not None:
                boot.write_text(boot_id + "\n", encoding="ascii")
            with mock.patch.object(host_process, "_PLATFORM", "linux"), \
                    mock.patch.object(host_process, "_BOOT_ID", boot), \
                    mock.patch.object(host_process.os, "readlink", return_value=namespace):
                return host_process.machine()

    def test_linux_names_the_boot_and_the_pid_namespace(self) -> None:
        host = self.linux("4f1c0d2e-boot", "pid:[4026531836]")
        self.assertEqual(host, "linux:4f1c0d2e-boot:pid:[4026531836]")
        self.assertNotEqual(self.linux("4f1c0d2e-boot", "pid:[4026532999]"), host, "a container on this host")
        # Another host, or this one after a reboot: unknown, never "gone", since neither can be told apart.
        self.assertNotEqual(self.linux("9a8b7c6d-boot", "pid:[4026531836]"), host)

    def test_linux_that_cannot_read_where_it_is_records_nothing(self) -> None:
        self.assertEqual(self.linux(None, "pid:[4026531836]"), "")
        with mock.patch.object(host_process, "machine", return_value=""), patch_modules(hermes()):
            self.assertIsNone(host_process.this_process())

    def test_elsewhere_the_platform_and_never_the_host_name(self) -> None:
        """A macOS host name follows the network; a record must not turn unknown when it does."""
        names = []
        for node in ("MacBook-Pro.local", "MacBook-Pro-2.local"):
            with mock.patch.object(host_process, "_PLATFORM", "darwin"), \
                    mock.patch("platform.node", return_value=node):
                names.append(host_process.machine())
        self.assertEqual(names, ["host:darwin", "host:darwin"])


class ProcessAlive(unittest.TestCase):
    """Mutation: read a pid that exists as alive under another start time, or an unknown as gone."""

    def setUp(self) -> None:
        self.RECORDED = {"pid": 4242, "started": 777, "machine": host_process.machine()}

    def alive(self, probes: dict[str, object], recorded: object = None) -> bool | None:
        with patch_modules(probes):
            return host_process.process_alive(self.RECORDED if recorded is None else recorded)

    def test_gone_only_when_the_probes_say_the_process_ended(self) -> None:
        self.assertIs(self.alive(hermes(exists=False)), False)
        # No start time recorded: unknown while the pid exists, gone once it does not.
        self.assertIs(self.alive(hermes(exists=False), {**self.RECORDED, "started": None}), False)
        self.assertIs(self.alive(hermes(started=5000)), False)  # the pid now names another process
        self.assertIs(self.alive(hermes(started=777)), True)
        self.assertIs(self.alive(hermes(started=900)), True)  # inside Hermes' drift tolerance

    def test_a_record_names_where_its_pid_was_read(self) -> None:
        self.assertEqual(host_process.recorded_process(self.RECORDED), self.RECORDED)
        for where in (None, "", 7, "x" * 301):
            with self.subTest(machine=where):
                self.assertIsNone(host_process.recorded_process({**self.RECORDED, "machine": where}))

    def test_what_cannot_be_told_is_unknown(self) -> None:
        here = host_process.machine()
        cases = {
            "outside Hermes": (NO_HERMES, None),
            "a Hermes whose probes fail to load": (BROKEN_HERMES, None),
            "a pid read on another machine": (hermes(exists=False), {"pid": 4242, "started": 777,
                                                                   "machine": "host:another-host"}),
            "a pid read in another pid namespace": (hermes(exists=False), {
                "pid": 4242, "started": 777, "machine": "linux:4f1c0d2e-boot:pid:[4026532999]"}),
            "no machine recorded": (hermes(exists=False), {"pid": 4242, "started": 777}),
            "no start time recorded": (hermes(), {"pid": 4242, "started": None, "machine": here}),
            "no start time now": (hermes(started=None), None),
            "a probe raised": (hermes(raises=True), None),
            "nothing recorded": (hermes(exists=False), {}),
            "a pid that is not a number": (hermes(exists=False), {"pid": "4242", "started": 777, "machine": here}),
            "a pid that is a flag": (hermes(exists=False), {"pid": True, "started": 777, "machine": here}),
            "no pid at all": (hermes(exists=False), {"pid": 0, "started": 777, "machine": here}),
            "a start time that is not a number": (hermes(exists=False), {"pid": 4242, "started": "777",
                                                                         "machine": here}),
        }
        for name, (probes, recorded) in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(self.alive(probes, recorded))


if __name__ == "__main__":
    unittest.main()
