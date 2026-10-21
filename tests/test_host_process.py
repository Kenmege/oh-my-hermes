"""`host_process`: whether the Hermes process a helper ran in still runs, through Hermes' own probes only.

The probes are `gateway.status`'s, as the Hermes process provides them. Only an
answer they give that the recorded process ended reads as gone; anything they
cannot tell, and everything outside Hermes, reads as unknown.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from _local_package import load_local_package
from _module_patch import patch_modules

load_local_package()

from omh.plugin_bundle.omh import host_process  # noqa: E402

NO_HERMES = {"gateway": None, "gateway.status": None}


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
        with patch_modules(NO_HERMES):
            self.assertIsNone(host_process.this_process())

    def test_the_pid_and_hermes_start_time_or_none_when_unread(self) -> None:
        for probes, started in ((hermes(started=777), 777), (hermes(started=None), None),
                                (hermes(started="777"), None), (hermes(raises=True), None)):
            with self.subTest(started=started), patch_modules(probes):
                self.assertEqual(host_process.this_process(), {"pid": os.getpid(), "started": started})


class ProcessAlive(unittest.TestCase):
    """Mutation: read a pid that exists as alive under another start time, or an unknown as gone."""

    RECORDED = {"pid": 4242, "started": 777}

    def alive(self, probes: dict[str, object], recorded: object = None) -> bool | None:
        with patch_modules(probes):
            return host_process.process_alive(self.RECORDED if recorded is None else recorded)

    def test_gone_only_when_the_probes_say_the_process_ended(self) -> None:
        self.assertIs(self.alive(hermes(exists=False)), False)
        self.assertIs(self.alive(hermes(started=5000)), False)  # the pid now names another process
        self.assertIs(self.alive(hermes(started=777)), True)
        self.assertIs(self.alive(hermes(started=900)), True)  # inside Hermes' drift tolerance

    def test_what_cannot_be_told_is_unknown(self) -> None:
        cases = {
            "outside Hermes": (NO_HERMES, None),
            "no start time recorded": (hermes(), {"pid": 4242, "started": None}),
            "no start time now": (hermes(started=None), None),
            "a probe raised": (hermes(raises=True), None),
            "nothing recorded": (hermes(exists=False), {}),
            "a pid that is not a number": (hermes(exists=False), {"pid": "4242", "started": 777}),
            "a pid that is a flag": (hermes(exists=False), {"pid": True, "started": 777}),
            "no pid at all": (hermes(exists=False), {"pid": 0, "started": 777}),
            "a start time that is not a number": (hermes(exists=False), {"pid": 4242, "started": "777"}),
        }
        for name, (probes, recorded) in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(self.alive(probes, recorded))


if __name__ == "__main__":
    unittest.main()
