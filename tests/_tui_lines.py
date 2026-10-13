"""Plain text out of the OMH TUI's styled lines, for assertions in the TUI tests."""

from __future__ import annotations

from collections.abc import Iterable

from omh.tui.style import Line


def line_text(line: Line) -> str:
    """The characters one styled line shows, styles dropped."""
    return "".join(span.text for span in line)


def lines_text(lines: Iterable[Line]) -> list[str]:
    """`line_text` of each line, in order."""
    return [line_text(line) for line in lines]
