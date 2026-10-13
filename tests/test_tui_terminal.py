"""The OMH TUI's tty layer (`omh.tui.terminal`).

`terminal_supported` decides between the OMH TUI and the fallback, so its
matrix is pinned with fake streams. `Terminal` is the one piece that touches a
real tty, so it runs against a pty: raw mode must make Ctrl+C a byte rather
than a signal, and the saved attributes must come back on every exit path,
including an exception and a window that closed under it.

Every read of the pty master is bounded, and nobody reads the master while a
`Terminal` changes modes: entering and leaving use TCSANOW, which does not
wait for output to drain, so the fixture cannot hang on its own buffer.
"""

from __future__ import annotations

import os
import select
import signal
import threading
import unittest
from unittest.mock import patch

from omh.tui import terminal as terminal_module
from omh.tui.terminal import (
    BRACKETED_PASTE_OFF,
    BRACKETED_PASTE_ON,
    KITTY_KEYBOARD_POP,
    KITTY_KEYBOARD_PUSH,
    MODIFY_OTHER_KEYS_OFF,
    MODIFY_OTHER_KEYS_ON,
    RESTORE_SEQUENCE,
    Terminal,
    keyboard_protocol_request,
    terminal_supported,
)

try:  # POSIX only; Windows has no pty and no termios.
    import pty
    import termios
except ImportError:  # pragma: no cover - exercised by the skip below
    pty = None
    termios = None

_IFLAG = 0
_OFLAG = 1
_LFLAG = 3
_READ_WATCHDOG_SECONDS = 2.0


class _Stream:
    def __init__(self, tty: bool = True, error: Exception | None = None) -> None:
        self._tty = tty
        self._error = error

    def isatty(self) -> bool:
        if self._error is not None:
            raise self._error
        return self._tty


def _comparable_mode(attributes: list) -> list:
    """Terminal attributes without PENDIN, which the kernel flips across a mode change on its own."""
    settled = list(attributes)
    settled[_LFLAG] = settled[_LFLAG] & ~getattr(termios, "PENDIN", 0)
    return settled


class TerminalSupportedTests(unittest.TestCase):
    @unittest.skipIf(terminal_module.termios is None, "no termios on this platform")
    def test_two_terminals_and_a_plain_environment_are_supported(self) -> None:
        self.assertTrue(terminal_supported(_Stream(), _Stream(), {"TERM": "xterm-256color"}))
        self.assertTrue(terminal_supported(_Stream(), _Stream(), {}))

    @unittest.skipIf(terminal_module.termios is None, "no termios on this platform")
    def test_an_empty_opt_out_does_not_opt_out(self) -> None:
        self.assertTrue(terminal_supported(_Stream(), _Stream(), {"OMH_NO_TUI": ""}))
        # Only "1" turns it off, as for the theme and setup pickers.
        self.assertTrue(terminal_supported(_Stream(), _Stream(), {"OMH_NO_TUI": "0"}))

    def test_each_reason_to_fall_back(self) -> None:
        cases = {
            "stdin not a tty": (_Stream(tty=False), _Stream(), {}),
            "stdout not a tty": (_Stream(), _Stream(tty=False), {}),
            "stdin closed": (_Stream(error=ValueError("I/O operation on closed file")), _Stream(), {}),
            "stdout detached": (_Stream(), _Stream(error=OSError("bad descriptor")), {}),
            "stdin missing": (None, _Stream(), {}),
            "TERM=dumb": (_Stream(), _Stream(), {"TERM": "dumb"}),
            "OMH_NO_TUI=1": (_Stream(), _Stream(), {"OMH_NO_TUI": "1"}),
        }
        for reason, (stdin, stdout, environ) in cases.items():
            with self.subTest(reason=reason):
                self.assertFalse(terminal_supported(stdin, stdout, environ))

    def test_no_termios_means_no_tui(self) -> None:
        with patch.object(terminal_module, "termios", None):
            self.assertFalse(terminal_supported(_Stream(), _Stream(), {}))


BOTH_REPORTS = KITTY_KEYBOARD_PUSH + MODIFY_OTHER_KEYS_ON


class KeyboardProtocolRequestTests(unittest.TestCase):
    """Which terminals are asked for Ctrl+Enter and friends; the list is Hermes' own TUI's."""

    def test_the_allowlisted_terminals_are_asked_for_both_reports(self) -> None:
        for program in ("iTerm.app", "kitty", "WezTerm", "tmux", "vscode", "windows-terminal"):
            with self.subTest(program=program):
                self.assertEqual(keyboard_protocol_request({"TERM_PROGRAM": program}), BOTH_REPORTS)

    def test_ghostty_is_asked_for_modify_other_keys_only(self) -> None:
        # Its kitty mode drops Alt from Backspace (Hermes' own TUI skips it there too).
        self.assertEqual(keyboard_protocol_request({"TERM_PROGRAM": "ghostty"}), MODIFY_OTHER_KEYS_ON)
        self.assertEqual(keyboard_protocol_request({"TERM": "xterm-ghostty"}), MODIFY_OTHER_KEYS_ON)

    def test_other_terminals_are_not_asked(self) -> None:
        for environ in (
            {},
            {"TERM_PROGRAM": "Apple_Terminal"},
            {"TERM_PROGRAM": "Hyper"},
            {"TERM_PROGRAM": "iterm.app"},  # the name is matched exactly
            {"TERM": "xterm-256color"},
            {"STY": "1234.pts-0.host"},  # GNU screen
            # Cursor sets TERM_PROGRAM=vscode; Hermes reads it as its own terminal, not allowlisted.
            {"TERM_PROGRAM": "vscode", "CURSOR_TRACE_ID": "abc"},
        ):
            with self.subTest(environ=environ):
                self.assertEqual(keyboard_protocol_request(environ), "")

    def test_the_terminal_is_named_as_hermes_names_it(self) -> None:
        # kitty sets no TERM_PROGRAM; tmux and Windows Terminal are also known by their own variables.
        for environ in (
            {"TERM": "xterm-kitty"},
            {"KITTY_WINDOW_ID": "1"},
            {"TMUX": "/tmp/tmux-501/default,1,0"},
            {"WT_SESSION": "id"},
        ):
            with self.subTest(environ=environ):
                self.assertEqual(keyboard_protocol_request(environ), BOTH_REPORTS)
        # TERM_PROGRAM wins over TMUX, as in Hermes.
        self.assertEqual(keyboard_protocol_request({"TERM_PROGRAM": "Apple_Terminal", "TMUX": "x"}), "")

    def test_legacy_keeps_plain_bytes_everywhere(self) -> None:
        for program in ("iTerm.app", "kitty", "ghostty", "tmux"):
            with self.subTest(program=program):
                environ = {"TERM_PROGRAM": program, "OMH_TUI_KEYBOARD": "legacy"}
                self.assertEqual(keyboard_protocol_request(environ), "")
        for value in ("", "kitty", "LEGACY "):
            with self.subTest(value=value):
                environ = {"TERM_PROGRAM": "iTerm.app", "OMH_TUI_KEYBOARD": value}
                self.assertEqual(keyboard_protocol_request(environ), BOTH_REPORTS)

    def test_the_restore_sequence_always_switches_both_reports_off(self) -> None:
        # Written on every exit, a crash and a signal included, whether or not they were asked for.
        self.assertIn(MODIFY_OTHER_KEYS_OFF, RESTORE_SEQUENCE)
        self.assertIn(KITTY_KEYBOARD_POP, RESTORE_SEQUENCE)
        self.assertEqual(KITTY_KEYBOARD_POP, "\x1b[<u")
        self.assertEqual(MODIFY_OTHER_KEYS_OFF, "\x1b[>4m")
        self.assertEqual(BOTH_REPORTS, "\x1b[>1u\x1b[>4;2m")


@unittest.skipUnless(pty is not None and termios is not None, "needs a POSIX pty")
class TerminalPtyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._open: set[int] = set()
        self.master, self.slave = self.open_pty()
        termios.tcsetwinsize(self.slave, (30, 100))

    def open_pty(self) -> tuple[int, int]:
        master, slave = pty.openpty()
        self._open.update((master, slave))
        self.addCleanup(self.close, slave)
        self.addCleanup(self.close, master)
        return master, slave

    def close(self, descriptor: int) -> None:
        """Close once; a descriptor number closed earlier may already belong to someone else."""
        if descriptor in self._open:
            self._open.discard(descriptor)
            os.close(descriptor)

    def master_output(self) -> bytes:
        """Everything the terminal side has written so far."""
        chunks = []
        while select.select([self.master], [], [], 0.2)[0]:
            try:
                chunk = os.read(self.master, 4096)
            except OSError:  # EIO once the slave side is gone (Linux)
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def read_exactly(self, term: Terminal, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = term.read(_READ_WATCHDOG_SECONDS)
            self.assertTrue(chunk, f"read timed out after {data!r}")
            data += chunk
        return data

    def test_raw_mode_while_inside_and_restored_after(self) -> None:
        before = termios.tcgetattr(self.slave)
        with Terminal(self.slave, self.slave):
            during = termios.tcgetattr(self.slave)
            for flag in ("ICANON", "ECHO", "ISIG", "IEXTEN"):
                with self.subTest(flag=flag):
                    self.assertFalse(during[_LFLAG] & getattr(termios, flag))
            for flag in ("ICRNL", "IXON"):
                with self.subTest(flag=flag):
                    self.assertFalse(during[_IFLAG] & getattr(termios, flag))
            self.assertFalse(during[_OFLAG] & termios.OPOST)
        self.assertEqual(_comparable_mode(termios.tcgetattr(self.slave)), _comparable_mode(before))

    def test_bracketed_paste_is_switched_on_and_back_off(self) -> None:
        with Terminal(self.slave, self.slave):
            self.assertEqual(self.master_output(), BRACKETED_PASTE_ON.encode())
        tail = self.master_output()
        self.assertTrue(tail.endswith(BRACKETED_PASTE_OFF.encode()), tail)

    def test_an_allowlisted_terminal_is_asked_for_key_reports_and_released(self) -> None:
        cases = {
            "iTerm.app": BOTH_REPORTS,
            "ghostty": MODIFY_OTHER_KEYS_ON,
        }
        for program, request in cases.items():
            with self.subTest(program=program):
                term = Terminal(self.slave, self.slave, environ={"TERM_PROGRAM": program})
                self.assertFalse(term.keyboard_protocol)
                with term:
                    self.assertEqual(self.master_output(), (BRACKETED_PASTE_ON + request).encode())
                    self.assertTrue(term.keyboard_protocol)
                self.assertFalse(term.keyboard_protocol)
                self.assertTrue(self.master_output().endswith(RESTORE_SEQUENCE.encode()))

    def test_no_request_without_an_allowlisted_terminal_or_with_legacy(self) -> None:
        for environ in (None, {"TERM_PROGRAM": "Apple_Terminal"}, {"TERM_PROGRAM": "kitty", "OMH_TUI_KEYBOARD": "legacy"}):
            with self.subTest(environ=environ):
                term = Terminal(self.slave, self.slave, environ=environ)
                with term:
                    self.assertEqual(self.master_output(), BRACKETED_PASTE_ON.encode())
                    self.assertFalse(term.keyboard_protocol)
                # Released anyway: harmless where nothing was asked.
                self.assertTrue(self.master_output().endswith(RESTORE_SEQUENCE.encode()))

    def test_key_reports_are_released_when_the_block_raises_or_a_signal_exits(self) -> None:
        for error in (RuntimeError("boom"), SystemExit(128 + signal.SIGTERM)):
            with self.subTest(error=type(error).__name__):
                term = Terminal(self.slave, self.slave, environ={"TERM_PROGRAM": "kitty"})
                with self.assertRaises(type(error)):
                    with term:
                        self.master_output()
                        raise error
                self.assertFalse(term.keyboard_protocol)
                tail = self.master_output().decode()
                self.assertIn(MODIFY_OTHER_KEYS_OFF + KITTY_KEYBOARD_POP, tail)

    def test_attributes_come_back_when_the_block_raises(self) -> None:
        before = termios.tcgetattr(self.slave)
        previous_handler = signal.getsignal(signal.SIGWINCH)
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with Terminal(self.slave, self.slave):
                raise RuntimeError("boom")
        self.assertEqual(_comparable_mode(termios.tcgetattr(self.slave)), _comparable_mode(before))
        self.assertIs(signal.getsignal(signal.SIGWINCH), previous_handler)
        self.assertTrue(self.master_output().endswith(BRACKETED_PASTE_OFF.encode()))

    def test_ctrl_c_and_enter_arrive_as_bytes(self) -> None:
        with Terminal(self.slave, self.slave) as term:
            os.write(self.master, b"\x03\r\n")
            # ISIG off: no SIGINT, the byte is input. ICRNL off: CR stays CR.
            self.assertEqual(self.read_exactly(term, 3), b"\x03\r\n")

    def test_read_returns_nothing_when_nothing_arrives(self) -> None:
        with Terminal(self.slave, self.slave) as term:
            self.assertEqual(term.read(0.05), b"")

    def test_output_is_not_post_processed(self) -> None:
        with Terminal(self.slave, self.slave) as term:
            self.master_output()
            term.write("a\nb 한")
            self.assertEqual(self.master_output(), "a\nb 한".encode())

    def test_size_is_columns_then_rows(self) -> None:
        with Terminal(self.slave, self.slave) as term:
            self.assertEqual(term.size(), (100, 30))
            termios.tcsetwinsize(self.slave, (0, 0))
            self.assertEqual(term.size(), (80, 24))

    @unittest.skipUnless(threading.current_thread() is threading.main_thread(), "signal handlers need the main thread")
    def test_resize_signal_is_read_once(self) -> None:
        with Terminal(self.slave, self.slave) as term:
            self.assertFalse(term.resized())
            signal.raise_signal(signal.SIGWINCH)
            self.assertTrue(term.resized())
            self.assertFalse(term.resized())

    def test_a_closed_window_ends_the_session_without_a_traceback_on_exit(self) -> None:
        with Terminal(self.slave, self.slave) as term:
            self.close(self.master)
            with self.assertRaises(EOFError):
                term.read(_READ_WATCHDOG_SECONDS)

    def test_leaving_after_an_unnoticed_hangup_does_not_raise(self) -> None:
        with Terminal(self.slave, self.slave):
            self.close(self.master)


if __name__ == "__main__":
    unittest.main()
