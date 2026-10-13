"""An edit tool's inline diff, as rows under that tool's row in the transcript.

Hermes renders the diff itself before it reaches the wire
(`agent/display.py::render_edit_diff_with_delta`, Hermes 6c61c3e27d):
`_emit_inline_diff` writes a "  ┊ review diff" header meant for its own
terminal printer, then `_render_inline_unified_diff` writes, per file, an
`a/x → b/x` header (the `---`/`+++` pair folded into one line), `@@` hunk
headers, and `+` / `-` / context lines, each wrapped in 24-bit SGR colour;
`_summarize_rendered_diff_sections` caps a long diff and appends one summary
line ("… omitted N diff line(s) …"). `tool.complete.payload.inline_diff` is
those lines joined by "\\n".

So the colour is stripped (`clean_text`, which drops whole escape sequences),
the review header is dropped as Ink drops it, every other line is classified
by its first characters, and the theme paints it: added and removed lines as
full-row bands padded to the width, hunk headers in the accent, the file
header in the label style, context like code, anything else dim. Diff lines
hard-wrap like fenced code, never splitting a glyph; the summary and other
prose word-wrap.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .markdown import band_rows, clean_text, hard_wrap, wrap_prefixed
from .style import Line, Span, Style
from .theme import Theme
from .width import line_width, truncate_left

DIFF_FILE = "file"
DIFF_HUNK = "hunk"
DIFF_ADDED = "added"
DIFF_REMOVED = "removed"
DIFF_CONTEXT = "context"
DIFF_NOTE = "note"  # the omitted-lines summary, "\ No newline at end of file", anything else

DIFF_INDENT = 4  # the diff hangs under the tool row's name, where a result summary sits
PREVIEW_LINES = 24  # diff lines shown in the live and flushed view; /history shows them all

FILE_ARROW = " → "  # `_render_inline_unified_diff` joins the two file names with it
REVIEW_HEADER_MARK = "┊"  # `display.diff.review_header` in every Hermes locale


@dataclass(frozen=True)
class DiffLine:
    kind: str
    text: str


def _kind(text: str) -> str:
    if text.startswith("@@"):
        return DIFF_HUNK
    if text.startswith("+"):
        return DIFF_ADDED
    if text.startswith("-"):
        return DIFF_REMOVED
    if text.startswith(" "):
        return DIFF_CONTEXT
    if FILE_ARROW in text:
        return DIFF_FILE
    return DIFF_NOTE


def _trimmed(lines: list[str]) -> list[str]:
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _side(name: str) -> str:
    """One side of the file header without git's `a/` or `b/` prefix."""
    for prefix in ("a/", "b/"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


_NO_FILE = "/dev/null"  # git's side of a created or deleted file


def compact_file_header(text: str) -> str:
    """`a/x → b/x` as `x` when one file is edited, created or deleted; `x → y` only for a rename."""
    before, _, after = text.strip().partition(FILE_ARROW)
    old, new = _side(before.strip()), _side(after.strip())
    if old in (new, _NO_FILE, ""):
        return new
    if new == _NO_FILE:
        return old
    return f"{old}{FILE_ARROW}{new}"


def parse_inline_diff(payload: str) -> tuple[DiffLine, ...]:
    """The classified lines of an `inline_diff` payload; () for an empty one or one that is only the header."""
    lines = _trimmed([clean_text(line) for line in payload.split("\n")])
    if lines and lines[0].lstrip().startswith(REVIEW_HEADER_MARK):
        lines = _trimmed(lines[1:])
    classified = [DiffLine(_kind(line), line) for line in lines]
    return tuple(
        DiffLine(line.kind, compact_file_header(line.text)) if line.kind == DIFF_FILE else line for line in classified
    )


def _style(kind: str, theme: Theme) -> Style:
    if kind == DIFF_ADDED:
        return theme.diff_add
    if kind == DIFF_REMOVED:
        return theme.diff_remove
    if kind == DIFF_HUNK:
        return theme.accent
    if kind == DIFF_FILE:
        return theme.label
    if kind == DIFF_CONTEXT:
        return theme.code
    return theme.dim


def _indent(width: int) -> Line:
    """The diff's hanging indent; it gives way on a terminal too narrow to leave the text more room than it takes."""
    return (Span(" " * DIFF_INDENT),) if width > DIFF_INDENT * 2 else ()


def _code_rows(text: str, style: Style, width: int, band: bool) -> list[Line]:
    """A diff line hard-wrapped like fenced code under the indent; a band row is padded to the right edge."""
    indent = _indent(width)
    cells = width - line_width(indent)
    rows = band_rows(text, cells, style) if band else hard_wrap((Span(text, style),), cells)
    return [(*indent, *row) if row else () for row in rows]


def _note_rows(text: str, style: Style, width: int) -> list[Line]:
    """Prose (a summary, a count) word-wrapped under the indent."""
    indent = _indent(width)
    return wrap_prefixed((Span(text, style),), indent, indent, width)


def _line_rows(line: DiffLine, width: int, theme: Theme) -> list[Line]:
    style = _style(line.kind, theme)
    if line.kind == DIFF_NOTE:
        return _note_rows(line.text, style, width)
    if line.kind == DIFF_FILE:
        # One row, cut from the left: the end of a path names the file.
        indent = _indent(width)
        return [(*indent, Span(truncate_left(line.text, width - line_width(indent)), style))]
    return _code_rows(line.text, style, width, band=line.kind in (DIFF_ADDED, DIFF_REMOVED))


def _hidden_note(hidden: int) -> str:
    noun = "line" if hidden == 1 else "lines"
    return f"… +{hidden} {noun} (/history shows all)"


def diff_rows(lines: Sequence[DiffLine], width: int, theme: Theme, full: bool = False) -> list[Line]:
    """The rows for a parsed diff at `width`: the first `PREVIEW_LINES` lines and a count of the rest, or all."""
    width = max(1, width)
    shown = lines if full else lines[:PREVIEW_LINES]
    rows: list[Line] = []
    for line in shown:
        rows.extend(_line_rows(line, width, theme))
    hidden = len(lines) - len(shown)
    if hidden:
        rows.extend(_note_rows(_hidden_note(hidden), theme.dim, width))
    return rows
