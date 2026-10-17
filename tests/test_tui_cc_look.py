"""Round 4, the Claude Code look in the transcript: per-call `⏺` rows, results under `⎿`, the working row.

Each tool call is one row: a `⏺` in its state's colour (the accent while it
runs, green once done, red when it failed or was denied), the tool's name and
what it ran on in parentheses, its duration at the end. What it printed goes
under `  ⎿  `; a read or a load says in one line what it read instead of
echoing its first lines. Result shapes are Hermes 6c61c3e27d's
(`tools/file_tools.py`, `tools/skills_tool.py`, `tools/file_operations_common.py`).

While a turn runs the working row reads `✶ Synthesizing… (1m 24s · ↓4.2k
tokens · thinking with high effort · esc to interrupt)`: a glyph that turns,
Hermes' activity as a verb with its kaomoji and dots taken off, and under it
one tip a turn (`  ⎿  Tip: …`) where the rows allow.

Your prompt reads `> 오마이 헤르메스란`: a dim `>` and dim text with no band, as
a command's echo does. A turn closes with a dim `  ✻ 5s · 3 tools · ↓1.3k`
and no rule after it.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest import mock

from _tui_lines import line_text, lines_text
from test_tui_modes import drawn, make_app, type_text
from omh.skin_pack import SKIN_THEMES
from omh.tui.app import INTERRUPTING, TICK_SECONDS, TIP_MIN_ROWS, TIPS, WORKING_GLYPHS, queue_lines, working_verb
from omh.tui.frame import sticky_line
from omh.tui.diff_view import DIFF_ADDED, DIFF_FILE, DIFF_HUNK, DIFF_INDENT, DiffLine, diff_rows
from omh.tui.gateway import Event
from omh.tui.style import Span
from omh.tui.theme import DEFAULT_THEME as THEME
from omh.tui.theme import build_theme, contrast_ratio
from omh.tui.transcript import (
    NOTICE_INFO,
    PULSE_SECONDS,
    TOOL_DENIED,
    TOOL_ERROR,
    TOOL_OK,
    TOOL_RUNNING,
    CommandBlock,
    ToolGroup,
    ToolRow,
    Transcript,
    TurnSummaryBlock,
    UserBlock,
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

    def test_a_binary_read_or_a_failed_call_is_not_summarised(self) -> None:
        self.assertEqual(completed("read_file", {"error": "File not found: x"})[1:], ["  ⎿  File not found: x"])
        binary = completed("read_file", {"content": "", "is_binary": True, "error": None})
        self.assertNotIn("Read", "\n".join(binary))
        # Hermes' SearchResult always carries total_count, a failed search too: its error is what it says.
        self.assertEqual(completed("search_files", {"total_count": 0, "error": "rg failed"})[1:], ["  ⎿  rg failed"])

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


class WorkingVerbTests(unittest.TestCase):
    def test_hermes_spinner_text_loses_its_face_and_its_dots(self) -> None:
        # Hermes 6c61c3e27d writes f"{face} {verb}..." (agent/turn_iteration_prep.py), faces from KAWAII_THINKING
        # and verbs from THINKING_VERBS or a locale's display.thinking_verbs, some of them two words.
        for text, verb in (("(⌐■_■) analyzing...", "Analyzing"), ("( •_•)>⌐■-■ brainstorming...", "Brainstorming"),
                           ("( ͡° ͜ʖ ͡°) pondering...", "Pondering"), ("٩(๑❛ᴗ❛๑)۶ kafa yoruyor...", "Kafa yoruyor"),
                           ("(°ロ°) 思案中...", "思案中"), ("ಠ_ಠ synthesizing...", "Synthesizing"),
                           ("(⊙_⊙) يتأمّل...", "يتأمّل")):  # locales/ar.yaml: a shadda, a combining mark, in the verb
            with self.subTest(text=text):
                self.assertEqual(working_verb(text), verb)

    def test_every_face_hermes_and_its_skins_ship_is_taken_off(self) -> None:
        # 6c61c3e27d: KawaiiSpinner.KAWAII_THINKING (agent/display.py) and the skins' thinking_faces
        # (hermes_cli/skin_engine.py); none has an ASCII letter or digit.
        faces = ("(｡•́︿•̀｡)", "(◔_◔)", "(¬‿¬)", "( •_•)>⌐■-■", "(⌐■_■)", "(´･_･`)", "◉_◉", "(°ロ°)", "( ˘⌣˘)♡",
                 "ヽ(>∀<☆)☆", "٩(๑❛ᴗ❛๑)۶", "(⊙_⊙)", "(¬_¬)", "( ͡° ͜ʖ ͡°)", "ಠ_ಠ", "(⚔)", "(⛨)", "(▲)", "(⌁)", "(<>)",
                 "(Ψ)", "(∿)", "(≈)", "(◌)", "(◉)", "(◬)", "(○)", "(●)", "(✦)", "(◇)", "(🔥)")
        for face in faces:
            with self.subTest(face=face):
                self.assertEqual(working_verb(f"{face} pondering..."), "Pondering")

    def test_hermes_diagnostics_that_end_in_dots_keep_their_words(self) -> None:
        # 6c61c3e27d: the stall notices (agent/chat_completion_nonstream.py, agent/chat_completion_helpers.py), the
        # retry status (agent/turn_recovery.py) and the compaction status (agent/conversation_compression.py).
        for text, verb in (
            ("⚠ no response from provider in 120s — reconnecting...", "No response from provider in 120s — reconnecting"),
            ("⚠ no output from provider for 45s — reconnecting...", "No output from provider for 45s — reconnecting"),
            ("⏳ Retrying in 5.0s (attempt 1/3)...", "Retrying in 5.0s (attempt 1/3)"),
            ("🗜️ Compacting context — summarizing earlier conversation so I can continue...",
             "Compacting context — summarizing earlier conversation so I can continue"),
        ):
            with self.subTest(text=text):
                self.assertEqual(working_verb(text), verb)

    def test_other_text_loses_only_a_glyph_before_its_words(self) -> None:
        wait = "⏳ waiting on deepseek/deepseek-v4 — no response for 30s"  # agent/chat_completion_wait_notice.py
        self.assertEqual(working_verb(wait), "Waiting on deepseek/deepseek-v4 — no response for 30s")
        self.assertEqual(working_verb("compressing context…"), "Compressing context")
        self.assertEqual(working_verb("Reading the router"), "Reading the router")

    def test_a_face_alone_or_nothing_leaves_no_verb(self) -> None:
        for text in ("(◔_◔)...", "", "   ", "..."):
            with self.subTest(text=text):
                self.assertEqual(working_verb(text), "")


def working_rows(app, cols: int = 100) -> list[str]:
    """The rows under the live transcript while a turn runs: the working row, then any tip."""
    return [line for line in lines_text(app._tail(cols)) if line][-2:]


class WorkingRowTests(unittest.TestCase):
    def busy(self, rows: int = 24, **options: object):
        clock = Clock()
        app, _client = make_app(busy=True, rows=rows, clock=clock, **options)
        app.busy_since = clock.now
        return app, clock

    def test_the_row_reads_glyph_verb_and_its_facts(self) -> None:
        app, clock = self.busy()
        app.transcript.activity = "(⌐■_■) synthesizing..."
        app.effort = "xhigh"
        app.usage_output, app.turn_output_base = 4200, 0
        clock.now += 84
        working = working_rows(app)[0]
        self.assertEqual(working[2:], "Synthesizing… (1m 24s · ↓4.2k tokens · thinking with xhigh effort · esc to interrupt)")
        self.assertIn(working[0], WORKING_GLYPHS)

    def test_without_words_from_hermes_it_is_working_and_an_unknown_effort_is_left_out(self) -> None:
        for effort in ("", "default", "none"):  # none: thinking is off
            with self.subTest(effort=effort):
                app, _clock = self.busy()
                app.effort = effort
                self.assertEqual(working_rows(app)[0][2:], "Working… (0s · esc to interrupt)")
        app, _clock = self.busy()
        app.transcript.activity, app.transcript.status = "(◔_◔)...", "compressing context"
        self.assertTrue(working_rows(app)[0][2:].startswith("Compressing context… (0s"))

    def test_a_stall_notice_keeps_its_cause_and_how_long_on_the_row(self) -> None:
        app, _clock = self.busy()
        app.effort = ""
        app.transcript.activity = "⚠ no response from provider in 120s — reconnecting..."
        self.assertEqual(working_rows(app)[0][2:], "No response from provider in 120s — reconnecting… (0s · esc to interrupt)")
        self.assertEqual(line_width(app._tail(30)[-2]), 30)  # a narrow row is cut at the edge

    def test_a_narrow_row_drops_the_effort_before_the_interrupt(self) -> None:
        app, clock = self.busy()
        app.transcript.activity, app.effort = "(⌐■_■) synthesizing...", "high"
        clock.now += 84
        self.assertEqual(working_rows(app, 60)[0][2:], "Synthesizing… (1m 24s · esc to interrupt)")  # the info line says it
        self.assertEqual(working_rows(app, 80)[0][2:], "Synthesizing… (1m 24s · thinking with high effort · esc to interrupt)")
        self.assertEqual(line_width(app._tail(30)[-2]), 30)  # past that, the row is cut at the edge

    def test_the_glyph_turns_through_claude_codes_set_and_holds_under_reduced_motion(self) -> None:
        app, clock = self.busy()
        seen = []
        for _ in range(len(WORKING_GLYPHS)):
            clock.now += TICK_SECONDS
            seen.append(app._tail(100)[-2][0])
        self.assertEqual({span.text for span in seen}, set("·✢✳✶✻✽"))
        self.assertEqual({span.style for span in seen}, {THEME.accent})
        still, clock = self.busy(environ={"OMH_TUI_REDUCED_MOTION": "1"})
        held = set()
        for _ in range(len(WORKING_GLYPHS)):
            clock.now += TICK_SECONDS
            held.add(still._tail(100)[-2][0].text)
        self.assertEqual(held, {WORKING_GLYPHS[0]})

    def test_the_interrupting_row_lines_up_with_the_working_row(self) -> None:
        app, _clock = self.busy()
        app.interrupting = True
        (row,) = [line for line in app._tail(100) if INTERRUPTING in line_text(line)]
        self.assertEqual(line_text(row), f"{WORKING_GLYPHS[0]} {INTERRUPTING} (0s)")
        self.assertEqual((row[0].style, row[2].style), (THEME.warn, THEME.warn))

    def test_one_tip_a_turn_under_the_working_row_in_turn(self) -> None:
        app, clock = self.busy()
        tips = []
        for _ in range(len(TIPS) + 1):
            working, tip = working_rows(app)
            self.assertTrue(working[2:].startswith("Working…"), working)
            self.assertEqual(working_rows(app)[1], tip)  # the same tip all turn
            tips.append(tip)
            clock.now += 30
            app.busy_since = clock.now  # the next turn
        self.assertEqual(tips[:-1], [f"  ⎿  Tip: {text}" for text in TIPS])
        self.assertEqual(tips[-1], tips[0])

    def test_no_tip_on_a_short_terminal_or_without_the_working_row(self) -> None:
        short, _clock = self.busy(rows=TIP_MIN_ROWS - 1)
        self.assertFalse([line for line in lines_text(short._tail(100)) if "Tip:" in line])
        tall, _clock = self.busy(rows=TIP_MIN_ROWS)
        self.assertTrue([line for line in lines_text(tall._tail(100)) if "Tip:" in line])
        waiting, clock = self.busy()  # a question: the waiting row stands in its place
        waiting.waiting_since = clock.now
        self.assertFalse([line for line in lines_text(waiting._tail(100)) if "Tip:" in line])
        stopping, _clock = self.busy()
        stopping.interrupting = True
        self.assertFalse([line for line in lines_text(stopping._tail(100)) if "Tip:" in line])

    def test_the_tip_stays_as_a_menu_or_the_agent_list_opens_and_takes_no_row_of_theirs(self) -> None:
        app, _clock = self.busy(rows=TIP_MIN_ROWS)
        type_text(app, "/")
        screen = drawn(app)
        self.assertTrue([line for line in screen if "Tip:" in line], screen)  # nothing above the composer moved
        menu = [line for line in screen if line.startswith(" /")]
        self.assertEqual(len(menu), 5, screen)
        alone, _clock = self.busy(rows=TIP_MIN_ROWS)
        type_text(alone, "/")
        with mock.patch("omh.tui.app.TIP_MIN_ROWS", TIP_MIN_ROWS + 1):  # the same frame with no tip
            self.assertEqual([line for line in drawn(alone) if line.startswith(" /")], menu)
        listing, _clock = self.busy()
        listing.list_selected = 0  # the agent list has the keys
        self.assertTrue([line for line in lines_text(listing._tail(100)) if "Tip:" in line])

class PromptRowTests(unittest.TestCase):
    def test_a_prompt_is_a_dim_mark_and_dim_text_on_no_band(self) -> None:
        for theme in (THEME, build_theme(SKIN_THEMES[0], "#1E1E1E"), build_theme(SKIN_THEMES[0], "#FFFFFF")):
            rows = block_rows(UserBlock("first line\nsecond line"), 40, theme)
            with self.subTest(theme=theme.name):
                self.assertEqual(lines_text(rows), ["> first line", "  second line"])
                self.assertEqual(rows[0][0], Span("> ", theme.dim))
                self.assertEqual({span.style for row in rows for span in row[1:]}, {theme.muted})  # after the lead
                self.assertEqual({span.style.bg for row in rows for span in row}, {None})  # no band, no fill

    def test_a_prompt_the_queue_sent_says_so_in_the_same_dim(self) -> None:
        (row,) = block_rows(UserBlock("next one", from_queue=True), 40, THEME)
        self.assertEqual(line_text(row), "> next one  ⧗ from queue")
        self.assertEqual(row[-1].style, THEME.muted)

    def test_the_sticky_reminder_and_the_queue_read_apart_from_a_prompt(self) -> None:
        prompt = line_text(block_rows(UserBlock("fix it"), 40, THEME)[0])
        sticky = line_text(sticky_line("fix it", 40, THEME))
        (queued,) = lines_text(queue_lines(["fix it"], 40, 3, THEME))
        self.assertEqual([prompt[:2], sticky[:7], queued[:2]], ["> ", "↳ you: ", "⧗ "])

    def test_a_command_echoes_as_a_prompt_does_in_the_hint_colour(self) -> None:
        rows = block_rows(CommandBlock("/model", [("Current model: x", NOTICE_INFO)], done=True), 40, THEME)
        self.assertEqual(lines_text(rows), ["> /model", "  ⎿  Current model: x"])
        self.assertEqual((rows[0][0], rows[0][1].style), (Span("> ", THEME.dim), THEME.hint))


class DoneLineTests(unittest.TestCase):
    def test_the_done_line_is_one_dim_line_without_a_rule(self) -> None:
        (row,) = block_rows(TurnSummaryBlock(5.0, 3, 1300), 80, THEME)
        self.assertEqual(line_text(row), "  ✻ 5s · 3 tools · ↓1.3k")
        self.assertNotIn("─", line_text(row))
        self.assertEqual([span.style for span in row if span.text.strip()], [THEME.dim, THEME.muted])

    def test_the_wait_on_you_stays_beside_the_time(self) -> None:
        (row,) = block_rows(TurnSummaryBlock(30.0, 1, None, waited=27.0), 80, THEME)
        self.assertEqual(line_text(row), "  ✻ 30s (27s waiting on you) · 1 tool")

    def test_a_narrow_terminal_cuts_it_and_a_wide_one_adds_nothing(self) -> None:
        (narrow,) = block_rows(TurnSummaryBlock(5.0, 3, 1300), 12, THEME)
        self.assertEqual(line_width(narrow), 12)
        (wide,) = block_rows(TurnSummaryBlock(5.0, 3, 1300), 200, THEME)
        self.assertEqual(line_text(wide), "  ✻ 5s · 3 tools · ↓1.3k")


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

    def test_installation_shows_the_working_row_the_code_draws(self) -> None:
        prose = installation()
        clock = Clock()
        app, _client = make_app(busy=True, clock=clock)
        app.busy_since = clock.now
        app.transcript.activity, app.effort = "(⌐■_■) synthesizing...", "high"
        app.usage_output, app.turn_output_base = 4200, 0
        clock.now += 84
        working, tip = working_rows(app)
        self.assertTrue(f"`✶ {working[2:]}`" in prose, working)
        self.assertTrue(f"(`{' '.join(tip.split())}`)" in prose, tip)
        self.assertTrue("(`· ✢ ✳ ✶ ✻ ✽`)" in prose)
        self.assertEqual(set(WORKING_GLYPHS), set("·✢✳✶✻✽"))
        self.assertTrue(f"On a terminal {TIP_MIN_ROWS} rows tall or more a tip sits under it" in prose)
        app.interrupting, app.usage_output = True, None  # the example has written nothing yet
        (row,) = [line for line in lines_text(app._tail(100)) if INTERRUPTING in line]
        self.assertTrue(f"`{row.replace('1m 24s', '12s')}`" in prose, row)


    def test_installation_shows_the_prompt_and_the_done_line_the_code_draws(self) -> None:
        prose = installation()
        (done,) = block_rows(TurnSummaryBlock(34.0, 3, 1200), 80, THEME)
        self.assertTrue(f"(`{line_text(done).strip()}`)" in prose, line_text(done))
        echo = block_rows(CommandBlock("/model", done=True), 80, THEME)[0]
        self.assertTrue(f"(`{line_text(echo)}`)" in prose, line_text(echo))
        self.assertTrue("a dim `>` and dim text with no band behind it" in prose)


if __name__ == "__main__":
    unittest.main()
