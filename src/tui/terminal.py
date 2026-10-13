"""The real terminal: raw mode, bracketed paste, size, resize signal, and byte IO.

This is the only module of the OMH TUI that touches a tty. Everything above it
works on bytes in (`read`) and text out (`write`), so it runs in tests against
a pipe, a pty, or a list.

Raw mode here is full raw, not the cbreak mode `omh theme` uses: ISIG is off so
Ctrl+C arrives as a key the composer can bind (the gateway ignores SIGINT
anyway), IXON is off so Ctrl+S/Ctrl+Q reach it too, ICRNL is off so Enter
reads as CR and Ctrl+J as LF, and OPOST is off so "\\n" is a bare line feed:
the screen writes "\\r\\n" itself and owns every cursor move. Attributes are
put back on exit whether the block ended normally or by an exception.

Windows has no termios; `terminal_supported` reports that, and the caller
takes the fallback path instead of entering a `Terminal`.
"""

from __future__ import annotations

from collections.abc import Mapping
import errno
import os
import select
import signal
import sys
from types import FrameType, TracebackType
from typing import Protocol

from .screen import AUTOWRAP_ON, SHOW_CURSOR
from .style import CSI, SGR_RESET

try:  # pragma: no cover - exercised by platform, not by a branch test
    import termios
except ImportError:  # pragma: no cover - Windows ships no termios
    termios = None  # type: ignore[assignment]

BRACKETED_PASTE_ON = f"{CSI}?2004h"
BRACKETED_PASTE_OFF = f"{CSI}?2004l"
# Written on exit even after a crash mid-frame: the screen hides the cursor and
# turns autowrap off while it paints, and a shell must not inherit either.
RESTORE_SEQUENCE = f"{SGR_RESET}{AUTOWRAP_ON}{SHOW_CURSOR}{BRACKETED_PASTE_OFF}"

READ_CHUNK_BYTES = 4096
FALLBACK_SIZE = (80, 24)

# Indices into the list termios.tcgetattr returns.
_IFLAG = 0
_OFLAG = 1
_CFLAG = 2
_LFLAG = 3
_CC = 6


class _TtyProbe(Protocol):
    def isatty(self) -> bool: ...


def terminal_supported(stdin: _TtyProbe | None, stdout: _TtyProbe | None, environ: Mapping[str, str]) -> bool:
    """True only when the OMH TUI can own this terminal; False means take the fallback."""
    if termios is None:
        return False
    if environ.get("TERM", "") == "dumb" or environ.get("OMH_NO_TUI", ""):
        return False
    for stream in (stdin, stdout):
        if stream is None:
            return False
        try:
            if not stream.isatty():
                return False
        except (OSError, ValueError):
            # A closed or detached stream is not a terminal.
            return False
    return True


def raw_attributes(saved: list) -> list:
    """`saved` with raw mode applied (what `tty.cfmakeraw` does on 3.12+, kept for 3.11)."""
    mode = list(saved)
    mode[_IFLAG] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
    mode[_OFLAG] &= ~termios.OPOST
    mode[_CFLAG] = (mode[_CFLAG] & ~(termios.CSIZE | termios.PARENB)) | termios.CS8
    mode[_LFLAG] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
    special = list(mode[_CC])
    special[termios.VMIN] = 1
    special[termios.VTIME] = 0
    mode[_CC] = special
    return mode


class Terminal:
    def __init__(self, input_fd: int | None = None, output_fd: int | None = None) -> None:
        self._input = input_fd if input_fd is not None else sys.stdin.fileno()
        self._output = output_fd if output_fd is not None else sys.stdout.fileno()
        self._saved: list | None = None
        self._previous_winch: object = None
        self._winch_installed = False
        self._resized = False

    def __enter__(self) -> Terminal:
        if termios is None:
            raise RuntimeError("the OMH TUI needs termios; check terminal_supported() first")
        saved = termios.tcgetattr(self._input)
        # TCSANOW, not TCSAFLUSH: flushing would throw away keys typed before start.
        termios.tcsetattr(self._input, termios.TCSANOW, raw_attributes(saved))
        self._saved = saved
        entered = False
        try:
            self._install_winch()
            self.write(BRACKETED_PASTE_ON)
            entered = True
        finally:
            if not entered:  # __exit__ never runs for a failed __enter__
                self._restore()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._restore()

    def _restore(self) -> None:
        # A hung-up terminal (window closed) refuses the write with EIO, and it
        # has no screen to reset and no shell to hand attributes back to:
        # leaving it must not turn into a traceback on exit.
        saved, self._saved = self._saved, None
        if saved is None:
            return
        hung_up = False
        try:
            self.write(RESTORE_SEQUENCE)
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            hung_up = True
        finally:
            try:
                if not hung_up:
                    termios.tcsetattr(self._input, termios.TCSANOW, saved)
            finally:
                self._uninstall_winch()

    def _on_winch(self, signum: int, frame: FrameType | None) -> None:
        self._resized = True

    def _install_winch(self) -> None:
        winch = getattr(signal, "SIGWINCH", None)
        if winch is None:
            return
        try:
            self._previous_winch = signal.signal(winch, self._on_winch)
        except ValueError:
            # Handlers can only be installed from the main thread; the caller
            # still sees a resize through size() on its next frame.
            return
        self._winch_installed = True

    def _uninstall_winch(self) -> None:
        if not self._winch_installed:
            return
        previous = self._previous_winch if self._previous_winch is not None else signal.SIG_DFL
        signal.signal(signal.SIGWINCH, previous)
        self._winch_installed = False

    def resized(self) -> bool:
        """True once per SIGWINCH since the last call."""
        resized, self._resized = self._resized, False
        return resized

    def size(self) -> tuple[int, int]:
        """(cols, rows); a terminal that reports nothing usable gets 80x24."""
        try:
            size = os.get_terminal_size(self._output)
        except OSError:
            return FALLBACK_SIZE
        return (size.columns or FALLBACK_SIZE[0], size.lines or FALLBACK_SIZE[1])

    def read(self, timeout: float) -> bytes:
        """Whatever input arrives within `timeout` seconds, b"" if none.

        Raises EOFError when the terminal hung up, so a closed window ends the
        session instead of spinning on empty reads.
        """
        ready, _, _ = select.select([self._input], [], [], max(timeout, 0.0))
        if not ready:
            return b""
        try:
            data = os.read(self._input, READ_CHUNK_BYTES)
        except OSError as error:
            if error.errno != errno.EIO:  # Linux reports a hung-up pty as EIO
                raise
            raise EOFError("the terminal hung up") from error
        if not data:  # macOS reports it as end of file
            raise EOFError("the terminal hung up")
        return data

    def write(self, text: str) -> None:
        data = text.encode("utf-8", "replace")
        while data:
            written = os.write(self._output, data)
            data = data[written:]
