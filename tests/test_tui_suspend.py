"""Ctrl+Z suspends the OMH TUI to the shell (parity P19).

Raw mode turns ISIG off, so Ctrl+Z reaches the TUI as a byte rather than
the terminal's SIGTSTP. `Terminal.suspend` puts the terminal back as the
shell had it, sends SIGTSTP to the process group (the job stops there, until
the shell continues it), and enters raw mode again. The signal is patched
here: the test process must not stop.
"""

from __future__ import annotations

import os
import select
import signal
import types
import unittest
from unittest.mock import patch

from test_tui_modes import drawn, make_app, type_text
from omh.tui import app as app_module
from omh.tui import terminal as terminal_module
from omh.tui.keys import CHAR, DOWN, Key
from omh.tui.terminal import BRACKETED_PASTE_ON, RESTORE_SEQUENCE, Terminal

CTRL_Z = Key(CHAR, "z", ctrl=True)

try:
    import termios
except ImportError:  # pragma: no cover - Windows
    termios = None  # type: ignore[assignment]


@unittest.skipUnless(hasattr(signal, "SIGTSTP"), "no job control on this platform")
class AppSuspendTests(unittest.TestCase):
    def test_ctrl_z_erases_the_region_suspends_and_draws_it_again_after(self) -> None:
        app, _client = make_app()
        type_text(app, "a draft")
        before = drawn(app)
        self.assertIn("› a draft", before)
        app._on_input(CTRL_Z)
        terminal = app.terminal
        self.assertEqual(terminal.suspended, 1)  # type: ignore[attr-defined]
        self.assertNotIn("› a draft", terminal.screen.lines())  # type: ignore[attr-defined]  # erased before stopping
        self.assertEqual(app.region.height, 0)  # a new region, drawn from wherever the shell left the cursor
        self.assertIn("› a draft", drawn(app))  # and drawn again once continued, the draft as it was
        self.assertEqual(app.composer.text, "a draft")

    def test_mouse_reports_go_off_before_the_shell_gets_the_terminal(self) -> None:
        from test_tui_agent_strip import agent_app

        app, _client = agent_app()
        app._on_input(Key(CHAR, "l", ctrl=True))  # the region's row known, so the list asks for reports
        app._on_input(Key(DOWN))
        drawn(app)
        self.assertTrue(app.terminal.mouse_reporting)  # type: ignore[attr-defined]
        app._on_input(CTRL_Z)
        self.assertFalse(app.terminal.mouse_reporting)  # type: ignore[attr-defined]

    def test_it_works_while_a_question_has_the_keys(self) -> None:
        from omh.tui.gateway import ServerRequest

        now = [100.0]
        app, client = make_app(clock=lambda: now[0])
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        app._on_input(CTRL_Z)
        self.assertEqual(app.terminal.suspended, 1)  # type: ignore[attr-defined]
        self.assertEqual(client.answers, [])  # suspending answers nothing
        self.assertIsNotNone(app.slot)


class NoJobControlTests(unittest.TestCase):
    def test_without_sigtstp_ctrl_z_does_nothing(self) -> None:
        app, client = make_app()
        type_text(app, "a draft")
        with patch.object(app_module, "signal", types.SimpleNamespace()):
            app._on_input(CTRL_Z)
        self.assertEqual((app.terminal.suspended, app.composer.text, client.calls), (0, "a draft", []))  # type: ignore[attr-defined]

    def test_the_terminal_does_nothing_without_sigtstp_or_outside_its_block(self) -> None:
        sent: list[tuple[int, int]] = []
        with patch.object(terminal_module.os, "kill", lambda pid, sig: sent.append((pid, sig))):
            self.assertFalse(Terminal(0, 1).suspend())  # not entered
            with patch.object(terminal_module, "signal", types.SimpleNamespace()):
                self.assertFalse(Terminal(0, 1).suspend())
        self.assertEqual(sent, [])


@unittest.skipUnless(hasattr(signal, "SIGTSTP") and termios is not None and hasattr(os, "openpty"), "needs a POSIX pty")
class TerminalSuspendPtyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.master, self.slave = os.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)

    def master_output(self) -> bytes:
        """Everything the terminal side has written so far (bounded reads)."""
        chunks = []
        while select.select([self.master], [], [], 0.2)[0]:
            chunk = os.read(self.master, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def test_suspend_restores_stops_the_group_and_enters_raw_mode_again(self) -> None:
        seen: list[tuple[int, int, bool, bytes]] = []

        def kill(pid: int, sig: int) -> None:
            cooked = bool(termios.tcgetattr(self.slave)[3] & termios.ICANON)
            seen.append((pid, sig, cooked, self.master_output()))

        with Terminal(self.slave, self.slave) as term:
            self.master_output()
            with patch.object(terminal_module.os, "kill", kill):
                self.assertTrue(term.suspend())
            (pid, sig, cooked, written), = seen
            self.assertEqual((pid, sig), (0, signal.SIGTSTP))  # the caller's whole process group
            self.assertTrue(cooked, "the shell gets its own terminal modes back while stopped")
            self.assertTrue(written.endswith(RESTORE_SEQUENCE.encode()), written)
            self.assertFalse(termios.tcgetattr(self.slave)[3] & termios.ICANON)  # raw again
            self.assertEqual(self.master_output(), BRACKETED_PASTE_ON.encode())


if __name__ == "__main__":
    unittest.main()
