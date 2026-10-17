"""Round 4, the Claude Code look in the transcript: per-call `⏺` rows, results under `⎿`, summarised reads.

Each tool call is one row: a `⏺` in its state's colour (the accent while it
runs, green once done, red when it failed or was denied), the tool's name and
what it ran on in parentheses, its duration at the end. What it printed goes
under `  ⎿  `; a read or a load says in one line what it read instead of
echoing its first lines. Result shapes are Hermes 6c61c3e27d's
(`tools/file_tools.py`, `tools/skills_tool.py`, `tools/file_operations_common.py`).
"""

from __future__ import annotations

from pathlib import Path
import unittest

from _tui_lines import line_text, lines_text
from omh.skin_pack import SKIN_THEMES
from omh.tui.diff_view import DIFF_ADDED, DIFF_FILE, DIFF_HUNK, DIFF_INDENT, DiffLine, diff_rows
from omh.tui.gateway import Event
from omh.tui.style import Span
from omh.tui.theme import DEFAULT_THEME as THEME
from omh.tui.theme import build_theme, contrast_ratio
from omh.tui.transcript import (
    PULSE_SECONDS,
    TOOL_DENIED,
    TOOL_ERROR,
    TOOL_OK,
    TOOL_RUNNING,
    ToolGroup,
    ToolRow,
    Transcript,
    block_rows,
)
from omh.tui.width import line_width

SID = "s"


def rows_of(tool: ToolRow, width: int = 60, theme=THEME, now: float | None = None, expanded: bool = False) -> list:
    return block_rows(ToolGroup([tool], done=tool.status != TOOL_RUNNING), width, theme, expanded, now=now)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def completed(name: str, result: object, context: str = "x") -> list[str]:
    """The rows a call that completed with `result` flushes."""
    transcript = Transcript(clock=Clock())
    transcript.apply(Event("message.start", SID, {}))
    transcript.apply(Event("tool.start", SID, {"tool_id": "t", "name": name, "context": context}))
    transcript.apply(Event("tool.complete", SID, {"tool_id": "t", "name": name, "duration_s": 0.2, "result": result}))
    transcript.apply(Event("message.complete", SID, {"text": "", "status": "complete"}))
    return lines_text(transcript.take_stable_lines(60, THEME))


class ToolRowTests(unittest.TestCase):
    def test_a_call_is_its_mark_then_its_name_and_what_it_ran_on_in_parentheses(self) -> None:
        (row,) = rows_of(ToolRow("t", "terminal", "rm -rf ./tmp-cache", TOOL_OK, duration_s=1.9))
        self.assertEqual(line_text(row), "⏺ terminal(rm -rf ./tmp-cache)  1.9s")
        self.assertEqual(row[0], Span("⏺", THEME.ok))
        self.assertEqual([span.style for span in row if span.text == "terminal"], [THEME.tool])
        self.assertEqual(row[-1].style, THEME.muted)  # the duration

    def test_the_mark_is_green_done_red_failed_or_denied_and_the_accent_while_it_runs(self) -> None:
        for status, style in ((TOOL_OK, THEME.ok), (TOOL_ERROR, THEME.error), (TOOL_DENIED, THEME.error),
                              (TOOL_RUNNING, THEME.accent)):
            with self.subTest(status=status):
                row = rows_of(ToolRow("t", "terminal", "ls", status, started_at=0.0, duration_s=0.1))[0]
                self.assertEqual(row[0], Span("⏺", style))
        running = ToolRow("t", "terminal", "ls", TOOL_RUNNING, started_at=0.0)
        self.assertEqual(rows_of(running, now=PULSE_SECONDS * 1.5)[0][0], Span("⏺", THEME.dim))  # its dim half
        self.assertFalse([row for row in rows_of(ToolRow("t", "terminal", "ls", TOOL_OK)) if "✓" in line_text(row)])

    def test_on_a_light_terminal_each_mark_reads_against_what_it_sits_on(self) -> None:
        for skin in SKIN_THEMES:
            theme = build_theme(skin, "#FFFFFF")
            for status in (TOOL_OK, TOOL_ERROR, TOOL_DENIED, TOOL_RUNNING):
                row = rows_of(ToolRow("t", "terminal", "ls", status, started_at=0.0), theme=theme)[0]
                ground = row[0].style.bg or "#FFFFFF"  # a failure's row is on the red band
                with self.subTest(theme=skin.short_name, status=status):
                    self.assertGreaterEqual(contrast_ratio(row[0].style.fg, ground), 3.0)

    def test_a_call_with_nothing_to_show_has_no_parentheses(self) -> None:
        (row,) = rows_of(ToolRow("t", "todo_list", "", TOOL_OK, duration_s=0.0))
        self.assertEqual(line_text(row), "⏺ todo_list  0.0s")

    def test_a_long_argument_is_cut_inside_its_parentheses_and_a_path_from_the_left(self) -> None:
        (row,) = rows_of(ToolRow("t", "terminal", "x" * 200, TOOL_OK, duration_s=12.0), width=40)
        self.assertEqual(line_text(row), "⏺ terminal(" + "x" * 20 + "…)  12.0s")
        self.assertEqual(line_width(row), 40)
        (row,) = rows_of(ToolRow("t", "read_file", "/repo/a/very/deep/tree/of/dirs/file.py", TOOL_OK), width=30)
        self.assertEqual(line_text(row), "⏺ read_file(…/of/dirs/file.py)")

    def test_what_it_printed_goes_under_the_result_mark_with_the_rest_counted(self) -> None:
        tool = ToolRow("t", "terminal", "seq 5", TOOL_OK, duration_s=0.1, output=("1", "2", "3", "4", "5"))
        self.assertEqual(
            lines_text(rows_of(tool)),
            ["⏺ terminal(seq 5)  0.1s", "  ⎿  1", "     2", "     3", "     … +2 lines · ctrl+o shows all"],
        )
        failed = ToolRow("t", "terminal", "pytest", TOOL_ERROR, duration_s=2.0, output=("a", "b", "c", "1 failed"))
        self.assertEqual(lines_text(rows_of(failed))[1:], ["  ⎿  … +1 line · ctrl+o shows all", "     b", "     c",
                                                           "     1 failed"])

    def test_a_diff_hangs_under_the_result_mark(self) -> None:
        diff = "a/src/app.py → b/src/app.py\n@@ -1,2 +1,2 @@\n import os\n-print('a')\n+print('b')"
        tool = ToolRow("t", "patch", "src/app.py", TOOL_OK, duration_s=0.3, inline_diff=diff.encode("utf-8"))
        shown = [row.rstrip() for row in lines_text(rows_of(tool))]
        self.assertEqual(shown, ["⏺ patch(src/app.py)  0.3s", "  ⎿  src/app.py", "   1  import os",
                                 "   2 -print('a')", "   2 +print('b')"])
        self.assertEqual(DIFF_INDENT, len("  ⎿  "))  # the code starts where a result's words do

    def test_the_lead_takes_only_an_indent_and_a_narrow_diff_has_none_to_take(self) -> None:
        lead = (Span("  "), Span("⎿  ", THEME.dim))
        file_first = (DiffLine(DIFF_FILE, "a.py"), DiffLine(DIFF_HUNK, "@@ -1 +1 @@"), DiffLine(DIFF_ADDED, "+x"))
        self.assertEqual(diff_rows(file_first, 40, THEME, lead=lead)[0], (*lead, Span("a.py", THEME.label)))
        code_first = file_first[1:]  # the gutter, not an indent, opens its first row
        self.assertEqual(diff_rows(code_first, 40, THEME, lead=lead), diff_rows(code_first, 40, THEME))
        self.assertEqual(diff_rows(file_first, 10, THEME, lead=lead), diff_rows(file_first, 10, THEME))


class SummarisedReadTests(unittest.TestCase):
    def test_a_read_file_says_how_many_lines_it_read(self) -> None:
        content = "     1|# OMH\n     2|line\n     3|end\n"
        self.assertEqual(completed("read_file", {"content": content, "total_lines": 715}),
                         ["⏺ read_file(x)  0.2s", "  ⎿  Read 3 lines"])
        self.assertEqual(completed("read_file", {"content": "     1|one", "total_lines": 1})[1], "  ⎿  Read 1 line")

    def test_a_binary_or_failed_read_is_not_summarised(self) -> None:
        self.assertEqual(completed("read_file", {"error": "File not found: x"})[1:], ["  ⎿  File not found: x"])
        binary = completed("read_file", {"content": "", "is_binary": True, "error": None})
        self.assertNotIn("Read", "\n".join(binary))

    def test_a_skill_view_names_the_skill_it_loaded(self) -> None:
        loaded = {"success": True, "name": "omh-docs", "content": "---\nname: omh-docs\n---\n# body"}
        self.assertEqual(completed("skill_view", loaded)[1:], ["  ⎿  loaded skill omh-docs"])
        stub = {"success": True, "status": "unchanged", "name": "omh-docs", "file": "SKILL.md", "dedup": True}
        self.assertEqual(completed("skill_view", stub)[1:], ["  ⎿  loaded skill omh-docs"])
        linked = {"success": True, "name": "omh-docs", "file": "references/api.md", "content": "# api"}
        self.assertEqual(completed("skill_view", linked)[1:], ["  ⎿  loaded references/api.md from skill omh-docs"])

    def test_a_search_says_how_many_it_found(self) -> None:
        matches = [{"path": "a.py", "line": 1, "content": "needle"}]
        self.assertEqual(completed("search_files", {"total_count": 12, "matches": matches * 12})[1:], ["  ⎿  12 matches"])
        self.assertEqual(completed("search_files", {"total_count": 1, "matches": matches})[1:], ["  ⎿  1 match"])
        self.assertEqual(completed("search_files", {"total_count": 3, "files": ["a", "b", "c"]})[1:], ["  ⎿  3 files"])
        capped = {"total_count": 50, "matches_text": "a.py\n  1: x", "truncated": True, "total_count_is_lower_bound": True}
        self.assertEqual(completed("search_files", capped)[1:], ["  ⎿  50+ matches"])
        self.assertEqual(completed("search_files", {"total_count": 0})[1:], ["  ⎿  0 matches"])

    def test_the_details_view_shows_the_same_one_line(self) -> None:
        transcript = Transcript(clock=Clock())
        transcript.apply(Event("tool.start", SID, {"tool_id": "t", "name": "read_file", "context": "a.py"}))
        transcript.apply(Event("tool.complete", SID, {"tool_id": "t", "name": "read_file", "result": {"content": "a\nb"}}))
        self.assertEqual(lines_text(transcript.history_lines(60, THEME, details=True))[1:], ["  ⎿  Read 2 lines"])

    def test_terminal_output_keeps_its_lines(self) -> None:
        self.assertEqual(completed("terminal", {"output": "hello\nworld", "exit_code": 0})[1:], ["  ⎿  hello", "     world"])


def installation() -> str:
    return " ".join((Path(__file__).resolve().parents[1] / "docs" / "INSTALLATION.md").read_text(encoding="utf-8").split())


class DocumentedLookTests(unittest.TestCase):
    def test_installation_shows_the_rows_the_code_draws(self) -> None:
        prose = installation()
        (row,) = rows_of(ToolRow("t", "terminal", "rm -rf ./tmp-cache", TOOL_OK, duration_s=1.9))
        shown = " ".join(line_text(row).split())  # the prose is read with its runs of spaces joined
        self.assertTrue(f"(`{shown}`)" in prose, shown)
        content = "\n".join(f"{number:6}|x" for number in range(1, 716))
        said = [
            completed("read_file", {"content": content})[1],
            completed("skill_view", {"success": True, "name": "omh-docs", "content": "x"})[1],
            completed("skill_view", {"success": True, "name": "omh-docs", "file": "references/api.md"})[1],
            completed("search_files", {"total_count": 12, "matches": []})[1],
            completed("search_files", {"total_count": 3, "files": ["a", "b", "c"]})[1],
            completed("search_files", {"total_count": 50, "matches": [], "total_count_is_lower_bound": True})[1],
        ]
        for line in said:
            with self.subTest(line=line):
                self.assertTrue(f"`{line.removeprefix('  ⎿  ')}`" in prose, line)


if __name__ == "__main__":
    unittest.main()
