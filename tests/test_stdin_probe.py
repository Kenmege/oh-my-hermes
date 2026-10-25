"""`stdin_is_tty()`: closed stdin, the Windows null device, and a real console.

The Windows cases are simulated on every platform: `isatty()` answers True the
way the CRT answers it for `NUL`, and `GetConsoleMode` is stood in for, so the
console check is exercised where CI has no Windows console. The real
`os.devnull` case runs through setup in `tests/test_setup_wizard_progress.py`,
which Windows CI executes against the real CRT.
"""

from __future__ import annotations

import ctypes
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from omh.system import stdin_probe  # noqa: E402


class _Stream:
    def __init__(self, *, tty: bool, fileno: int = 0) -> None:
        self._tty = tty
        self._fileno = fileno

    def isatty(self) -> bool:
        return self._tty

    def fileno(self) -> int:
        return self._fileno


def _windows_modules(console_mode_result: int, calls: list[int]) -> dict[str, object]:
    def get_console_mode(handle, _mode) -> int:
        calls.append(handle.value)
        return console_mode_result

    kernel32 = SimpleNamespace(GetConsoleMode=get_console_mode)
    fake_ctypes = SimpleNamespace(
        WinDLL=lambda _name, use_last_error=False: kernel32,
        c_uint32=ctypes.c_uint32,
        c_void_p=ctypes.c_void_p,
        byref=ctypes.byref,
    )
    fake_msvcrt = SimpleNamespace(get_osfhandle=lambda descriptor: 4096 + descriptor)
    return {"ctypes": fake_ctypes, "msvcrt": fake_msvcrt}


class StdinProbeTests(unittest.TestCase):
    def test_a_closed_stdin_is_not_a_terminal(self) -> None:
        with patch.object(sys, "stdin", None):
            self.assertFalse(stdin_probe.stdin_is_tty())

    def test_a_posix_tty_is_a_terminal_without_a_console_check(self) -> None:
        with patch.object(stdin_probe, "_IS_WINDOWS", False), patch.object(sys, "stdin", _Stream(tty=True)):
            self.assertTrue(stdin_probe.stdin_is_tty())

    def test_the_windows_null_device_is_not_a_terminal(self) -> None:
        # `NUL` answers isatty() True; it has no console mode.
        calls: list[int] = []
        with (
            patch.object(stdin_probe, "_IS_WINDOWS", True),
            patch.object(sys, "stdin", _Stream(tty=True, fileno=0)),
            patch.dict(sys.modules, _windows_modules(0, calls)),
        ):
            self.assertFalse(stdin_probe.stdin_is_tty())
        self.assertEqual(calls, [4096])

    def test_a_windows_console_is_a_terminal(self) -> None:
        calls: list[int] = []
        with (
            patch.object(stdin_probe, "_IS_WINDOWS", True),
            patch.object(sys, "stdin", _Stream(tty=True, fileno=0)),
            patch.dict(sys.modules, _windows_modules(1, calls)),
        ):
            self.assertTrue(stdin_probe.stdin_is_tty())
        self.assertEqual(calls, [4096])

    def test_a_stand_in_stream_without_a_descriptor_keeps_its_own_answer(self) -> None:
        # Tests stand a StringIO in for a terminal; it has no handle to ask about.
        class _NoDescriptor(io.StringIO):
            def isatty(self) -> bool:
                return True

        calls: list[int] = []
        with (
            patch.object(stdin_probe, "_IS_WINDOWS", True),
            patch.object(sys, "stdin", _NoDescriptor()),
            patch.dict(sys.modules, _windows_modules(0, calls)),
        ):
            self.assertTrue(stdin_probe.stdin_is_tty())
        self.assertEqual(calls, [])

    def test_a_windows_probe_that_cannot_ask_answers_false(self) -> None:
        # No msvcrt (or no kernel32) must read as "not a console", never raise.
        with (
            patch.object(stdin_probe, "_IS_WINDOWS", True),
            patch.object(sys, "stdin", _Stream(tty=True)),
            patch.dict(sys.modules, {"msvcrt": None}),
        ):
            self.assertFalse(stdin_probe.stdin_is_tty())


if __name__ == "__main__":
    unittest.main()
