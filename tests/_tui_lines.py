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


def box_inner(row: str) -> str:
    """A composer row as typed: the box's sides (`│ ` and ` │`) taken off and trailing blanks dropped; any other row
    as it is."""
    if row.startswith("│ ") and row.endswith(" │"):
        return row[2:-2].rstrip()
    return row


def boxed(text: str, cols: int) -> str:
    """The screen text of a composer row holding `text` (single-cell characters) inside the box at `cols` columns."""
    return f"│ {text.ljust(cols - 4)} │"


def box_edges(cols: int) -> tuple[str, str]:
    """The screen text of the box's top and bottom edges at `cols` columns."""
    return "╭" + "─" * (cols - 2) + "╮", "╰" + "─" * (cols - 2) + "╯"
