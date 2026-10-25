"""Whether stdin is a terminal, answered without raising.

A shell that closes stdin (`<&-`) starts Python with `sys.stdin` set to None,
so `sys.stdin.isatty()` raises AttributeError; a stream closed underneath the
process raises ValueError. Every interactive entry point asks this question
before it prompts, and each of them used to ask it bare, so a closed stdin
became a traceback instead of the refusal the command already had for a pipe.
One home, so a new prompt cannot reintroduce the crash by copying the bare
probe.
"""

from __future__ import annotations

import sys


def stdin_is_tty() -> bool:
    """True only when stdin is open and attached to a terminal."""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False
