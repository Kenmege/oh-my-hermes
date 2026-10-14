"""The OMH TUI transcript model and its flush policy (`omh.tui.transcript`).

Event payloads are copied from real Hermes 6c61c3e27d `tui_gateway` recordings
(`tests/fixtures/tui_gateway/`, session and approval), trimmed to the fields the
transcript reads. Every step of every sequence runs through `LoopTestCase.step`,
which plays the app loop (apply, take stable lines, read live lines) and holds
the inline-scrollback invariant: the lines taken so far followed by the live
lines equal `history_lines(details=False)`. So no line is ever taken twice,
none goes missing, and nothing already in scrollback would need a repaint.
"""

from __future__ import annotations

import unicodedata
import unittest

from _tui_lines import lines_text
from omh.tui.gateway import Event
from omh.tui.style import Line, Span
from omh.tui.theme import DEFAULT_THEME as THEME
from omh.tui.transcript import (
    AssistantBlock,
    ThinkingBlock,
    Transcript,
    tool_result_status,
)
from omh.tui.width import is_control, line_width

SID = "b6af062a"
WIDTH = 60

PLAIN_ANSWER = "ok\n\nI'm Hermes, your agent — `/help` lists the commands."
DENIED_RESULT = {
    "output": "",
    "exit_code": -1,
    "error": "BLOCKED: Command denied by user. The user has NOT consented to this action.",
    "status": "blocked",
    "user_summary": "You denied this command — it did not run.",
}


def ev(event_type: str, /, **payload: object) -> Event:
    return Event(type=event_type, session_id=SID, payload=dict(payload))


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class LoopTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.transcript = Transcript(clock=self.clock)
        self.taken: list[Line] = []

    def step(self, event: Event | None = None, *, at: float | None = None, width: int = WIDTH) -> list[str]:
        """One frame of the app loop; returns the texts of the lines this frame flushed."""
        if at is not None:
            self.clock.now = at
        if event is not None:
            self.transcript.apply(event)
        fresh = self.transcript.take_stable_lines(width, THEME)
        self.assertEqual(self.transcript.take_stable_lines(width, THEME), [], "a line was offered twice")
        self.taken.extend(fresh)
        live = self.transcript.live_lines(width, THEME)
        self.assertEqual(self.taken + live, self.transcript.history_lines(width, THEME, details=False))
        return lines_text(fresh)

    def user(self, text: str) -> list[str]:
        self.transcript.add_user(text)
        return self.step()

    def live(self, width: int = WIDTH) -> list[str]:
        return lines_text(self.transcript.live_lines(width, THEME))

    def history(self, details: bool = True) -> list[str]:
        return lines_text(self.transcript.history_lines(WIDTH, THEME, details=details))


class SpacingTests(LoopTestCase):
    def test_one_blank_row_separates_blocks_and_none_opens_the_transcript(self) -> None:
        self.user("first")
        self.step(ev("message.start"))
        self.step(ev("reasoning.delta", text="thinking it over"))
        self.step(ev("tool.start", tool_id="t1", name="terminal", context="ls"))
        self.step(ev("tool.complete", tool_id="t1", name="terminal", result={"exit_code": 0}, duration_s=0.5))
        self.step(ev("message.delta", text="done\n"))
        self.step(ev("message.complete", text="done", status="complete"))
        self.user("second")
        expected = ["› first", "", "▸ thought for 0.0s", "", "  ✓ terminal  ls  0.5s", "", "⏺ done", "", "› second"]
        self.assertEqual(lines_text(self.taken), expected)
        self.assertEqual(self.history(details=False), expected)

    def test_a_resumed_reply_renders_like_a_streamed_one(self) -> None:
        self.user("hi")
        self.transcript.add_assistant("**hello** there\n\n```sh\nls\n```")
        self.transcript.add_assistant("   ")
        self.assertEqual(self.step(), ["", "⏺ hello there", "", "  sh", "  ls"])
        self.assertEqual(len(self.transcript.blocks), 2)


class RetakeTests(LoopTestCase):
    def test_retake_offers_the_whole_transcript_again_at_a_new_width(self) -> None:
        self.user("first question that is long enough to wrap at a narrow width")
        self.step(ev("message.start"))
        self.step(ev("message.delta", text="line one of the answer\nline two is still streaming"))
        self.transcript.retake()
        narrow = self.transcript.take_stable_lines(30, THEME)
        live = self.transcript.live_lines(30, THEME)
        self.assertEqual(narrow + live, self.transcript.history_lines(30, THEME, details=False))
        self.assertEqual(self.transcript.take_stable_lines(30, THEME), [], "a retaken line was offered twice")
        self.assertIn("line two is still streaming", "".join(lines_text(live)))


class ToolPathArgumentTests(LoopTestCase):
    def test_a_path_argument_keeps_its_file_name_when_cut(self) -> None:
        path = "/private/tmp/claude-501/" + "y" * 60 + "/project/hello.py"
        self.step(ev("message.start"))
        self.step(ev("tool.start", tool_id="t1", name="patch", context=path))
        row = [line for line in self.live(width=50) if "patch" in line][0]
        self.assertIn("hello.py", row)
        self.assertIn("…", row)

    def test_prose_arguments_are_still_cut_at_the_end(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("tool.start", tool_id="t1", name="terminal", context="echo " + "z" * 80))
        row = [line for line in self.live(width=50) if "terminal" in line][0]
        self.assertIn("echo", row)


class SessionRelativePathTests(LoopTestCase):
    def test_paths_inside_the_session_cwd_show_relative_to_it(self) -> None:
        self.transcript.cwd = "/work/project"
        self.step(ev("message.start"))
        self.step(ev("tool.start", tool_id="t1", name="patch", context="/work/project/src/app.py"))
        diff = "\x1b[1ma//work/project/src/app.py → b//work/project/src/app.py\x1b[0m\n@@ -1 +1 @@\n-a\n+b"
        self.step(ev("tool.complete", tool_id="t1", name="patch", result={"exit_code": 0}, inline_diff=diff))
        live = self.live()
        self.assertIn("  ✓ patch  src/app.py", "\n".join(live))
        self.assertIn("    src/app.py", live)

    def test_a_cwd_of_root_leaves_absolute_paths_absolute(self) -> None:
        self.transcript.cwd = "/"
        self.step(ev("message.start"))
        self.step(ev("tool.start", tool_id="t1", name="read_file", context="/etc/hosts"))
        self.assertIn("read_file  /etc/hosts", "\n".join(self.live()))

    def test_paths_outside_the_cwd_and_prose_stay_as_they_are(self) -> None:
        self.transcript.cwd = "/work/project"
        self.step(ev("message.start"))
        self.step(ev("tool.start", tool_id="t1", name="read_file", context="/etc/hosts"))
        self.step(ev("tool.start", tool_id="t2", name="terminal", context="ls /work/project/src"))
        joined = "\n".join(self.live())
        self.assertIn("read_file  /etc/hosts", joined)
        self.assertIn("terminal  ls /work/project/src", joined)

class PlainTurnTests(LoopTestCase):
    def test_plain_turn_flushes_per_completed_line_and_fully_at_complete(self) -> None:
        self.assertEqual(self.user("Reply with exactly the word: ok"), ["› Reply with exactly the word: ok"])
        self.assertEqual(self.step(ev("message.start")), [])
        self.assertTrue(self.transcript.running)
        self.step(ev("thinking.delta", text="ヽ(>∀<☆)☆ formulating..."))
        self.assertEqual(self.transcript.activity, "ヽ(>∀<☆)☆ formulating...")
        self.step(ev("thinking.delta", text=""))
        self.assertEqual(self.transcript.activity, "")
        self.assertEqual(self.step(ev("message.delta", text="ok\n\n")), ["", "⏺ ok"])
        self.assertEqual(self.step(ev("message.delta", text="I")), [])
        self.step(ev("message.delta", text="'m Hermes,"))
        self.step(ev("message.delta", text=" your agent — `/help` lists the commands."))
        self.assertEqual(self.live(), ["", "  I'm Hermes, your agent — /help lists the commands."])
        self.step(ev("reasoning.available", text=PLAIN_ANSWER))
        flushed = self.step(ev("message.complete", text=PLAIN_ANSWER, status="complete", usage={"total": 13874}))
        self.assertEqual(flushed, ["", "  I'm Hermes, your agent — /help lists the commands."])
        self.assertEqual(self.live(), [])
        self.assertFalse(self.transcript.running)
        self.assertFalse(any(isinstance(block, ThinkingBlock) for block in self.transcript.blocks))
        self.assertEqual(
            self.history(),
            [
                "› Reply with exactly the word: ok",
                "",
                "⏺ ok",
                "",
                "  I'm Hermes, your agent — /help lists the commands.",
            ],
        )

    def test_live_lines_shrink_as_lines_flush(self) -> None:
        self.transcript.apply(ev("message.start"))
        self.transcript.apply(ev("message.delta", text="line one\nline two\npartial"))
        self.assertEqual(self.live(), ["⏺ line one", "  line two", "  partial"])
        self.assertEqual(self.step(), ["⏺ line one", "  line two"])
        self.assertEqual(self.live(), ["  partial"])
        self.assertEqual(self.taken[0][0], Span("⏺ ", THEME.accent))

    def test_a_trailing_blank_line_waits_for_the_text_after_it(self) -> None:
        self.step(ev("message.start"))
        self.assertEqual(self.step(ev("message.delta", text="1\n\n")), ["⏺ 1"])
        self.assertEqual(self.step(ev("message.delta", text="\n")), [])
        self.assertEqual(self.step(ev("message.delta", text="2\n")), ["", "", "  2"])
        self.assertEqual(self.step(ev("message.complete", text="1\n\n\n2", status="complete")), [])

    def test_any_chunking_of_the_stream_flushes_the_same_lines(self) -> None:
        answer = "# Plan\n\n- step **one**\n- step two\n\n```python\nx = 1\n```\nDone, see [docs](https://x.y)."
        expected: list[str] | None = None
        for size in (1, 2, 3, 7, 50):
            with self.subTest(chunk=size):
                self.setUp()
                self.step(ev("message.start"))
                for start in range(0, len(answer), size):
                    self.step(ev("message.delta", text=answer[start : start + size]))
                self.step(ev("message.complete", text=answer, status="complete"))
                self.assertEqual(self.live(), [])
                expected = expected or lines_text(self.taken)
                self.assertEqual(lines_text(self.taken), expected)
        self.assertEqual(
            expected,
            ["⏺ Plan", "", "  • step one", "  • step two", "", "  python", "  x = 1", "  Done, see docs (https://x.y)."],
        )

    def test_an_open_fence_stays_code_across_the_flush_boundary(self) -> None:
        self.step(ev("message.start"))
        flushed = self.step(ev("message.delta", text="Here:\n```python\nx = None  # one\n"))
        self.assertEqual(flushed, ["⏺ Here:", "  python", "  x = None  # one"])
        self.assertEqual(
            self.taken[-1],
            (Span("  "), Span("x = ", THEME.text), Span("None", THEME.syntax_keyword), Span("  ", THEME.text),
             Span("# one", THEME.syntax_comment)),
        )
        self.assertEqual(self.step(ev("message.delta", text="y = '*no*'\n``")), ["  y = '*no*'"])
        self.assertEqual(self.taken[-1], (Span("  "), Span("y = ", THEME.text), Span("'*no*'", THEME.syntax_string)))
        self.assertEqual(self.live(), ["  ``"])
        self.assertEqual(self.step(ev("message.delta", text="`\nafter *it*")), [])
        self.assertEqual(self.live(), ["  after it"])
        self.assertEqual(self.step(ev("message.complete", text="", status="complete")), ["  after it"])

    def test_a_docstring_open_across_flushes_stays_a_string_for_any_chunking(self) -> None:
        answer = 'Code:\n```python\ndef f():\n    """Doc.\n\n    # not a comment\n    """\n    return 1  # one\n```\nDone.'
        expected: list[Line] | None = None
        for size in (1, 4, 9, 200):
            with self.subTest(chunk=size):
                self.setUp()
                self.step(ev("message.start"))
                for start in range(0, len(answer), size):
                    self.step(ev("message.delta", text=answer[start : start + size]))
                self.step(ev("message.complete", text=answer, status="complete"))
                expected = expected or list(self.taken)
                self.assertEqual(self.taken, expected)
        self.assertEqual(expected[5], (Span("  "), Span("    # not a comment", THEME.syntax_string)))
        self.assertEqual(expected[6], (Span("  "), Span('    """', THEME.syntax_string)))
        self.assertEqual(expected[7][-1], Span("# one", THEME.syntax_comment))

    def test_a_resize_changes_only_lines_not_yet_taken(self) -> None:
        self.step(ev("message.start"))
        long_line = "alpha beta gamma delta epsilon zeta eta theta"
        self.assertEqual(self.step(ev("message.delta", text=long_line + "\n" + long_line), width=80), ["⏺ " + long_line])
        narrow = self.live(width=20)
        self.assertEqual(narrow, ["  alpha beta gamma", "  delta epsilon zeta", "  eta theta"])
        self.transcript.apply(ev("message.complete", text="", status="complete"))
        fresh = self.transcript.take_stable_lines(20, THEME)
        self.assertEqual(lines_text(fresh), narrow)
        self.assertEqual(self.transcript.take_stable_lines(80, THEME), [])

    def test_authoritative_text_replaces_the_stream_only_where_nothing_was_taken(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("message.delta", text="Hello wrld"))
        self.step(ev("message.complete", text="Hello world", status="complete"))
        self.assertEqual(self.history(), ["⏺ Hello world"])
        self.setUp()
        self.step(ev("message.start"))
        self.step(ev("message.delta", text="first\nsecond"))
        self.step(ev("message.complete", text="FIRST\nsecond", status="complete"))
        self.assertEqual(self.history(), ["⏺ first", "  second"])

    def test_text_that_never_streamed_arrives_with_complete(self) -> None:
        self.step(ev("message.start"))
        self.assertEqual(self.step(ev("message.complete", text="blue", status="complete")), ["⏺ blue"])

    def test_control_characters_never_reach_a_line(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("message.delta", text="\x1b[2Jboom\x07\n"))
        self.step(ev("message.complete", text="", status="complete"))
        self.assertEqual(lines_text(self.taken), ["⏺ boom"])
        self.assertFalse(any(is_control(ch) for row in self.taken for span in row for ch in span.text))

    def test_user_text_is_plain_wrapped_and_composed(self) -> None:
        decomposed = unicodedata.normalize("NFD", "한글")
        self.assertEqual(self.user(decomposed + " **not bold**\nsecond line"), ["› 한글 **not bold**", "  second line"])


class ReasoningAndToolTests(LoopTestCase):
    def test_reasoning_flushes_as_one_collapsed_row_then_the_tool_group(self) -> None:
        self.user("Use your terminal tool to run: rm -rf ./omh-m0-probe-dir")
        self.step(ev("message.start"), at=100.0)
        self.step(ev("thinking.delta", text="(⌐■_■) analyzing..."))
        self.step(ev("thinking.delta", text=""))
        self.step(ev("reasoning.delta", text="The"), at=110.0)
        self.step(ev("reasoning.delta", text=" user asks"))
        self.step(ev("reasoning.delta", text=" to run `rm -rf ./omh-m0-probe-dir`.\n\nLet me just run it."))
        self.assertEqual(self.live(), ["", "▸ thinking… Let me just run it."])

        self.assertTrue(self.transcript.toggle_details())
        self.assertEqual(
            self.live(),
            ["", "▾ thinking…", "  The user asks to run `rm -rf ./omh-m0-probe-dir`.", "", "  Let me just run it."],
        )
        self.transcript.toggle_details()

        flushed = self.step(ev("tool.generating", name="terminal"), at=114.2)
        self.assertEqual(flushed, ["", "▸ thought for 4.2s"])
        self.assertEqual(self.live(), ["", "  ◌ terminal"])
        self.step(
            ev(
                "tool.start",
                tool_id="call_3e3b1bebaaee48e68c56001e",
                name="terminal",
                context="rm -rf ./omh-m0-probe-dir",
                args={"command": "rm -rf ./omh-m0-probe-dir"},
            ),
            at=114.3,
        )
        self.assertEqual(self.live(), ["", "  ◔ terminal  rm -rf ./omh-m0-probe-dir"])
        self.step(ev("session.usage", usage={"total": 27946}))
        self.step(
            ev(
                "tool.complete",
                tool_id="call_3e3b1bebaaee48e68c56001e",
                name="terminal",
                args={"command": "rm -rf ./omh-m0-probe-dir"},
                duration_s=1.9490349292755127,
                result={
                    "output": "",
                    "exit_code": 0,
                    "error": None,
                    "approval": "Command was flagged (recursive delete) and auto-approved by smart approval.",
                },
            )
        )
        self.assertEqual(self.live(), ["", "  ✓ terminal  rm -rf ./omh-m0-probe-dir  1.9s"])
        self.step(ev("thinking.delta", text="(◔_◔) brainstorming..."))
        flushed = self.step(ev("message.delta", text="\n\nRan it"))
        self.assertEqual(flushed, ["", "  ✓ terminal  rm -rf ./omh-m0-probe-dir  1.9s"])
        self.step(ev("message.delta", text=" — exit code 0."))
        self.step(ev("reasoning.available", text="Ran it — exit code 0."))
        flushed = self.step(
            ev("message.complete", text="Ran it — exit code 0.", status="complete", reasoning="The user asks ...")
        )
        self.assertEqual(flushed, ["", "⏺ Ran it — exit code 0."])
        self.assertEqual(self.history(details=False), lines_text(self.taken))
        self.assertEqual(
            self.history(),
            [
                "› Use your terminal tool to run: rm -rf ./omh-m0-probe-dir",
                "",
                "▾ thought for 4.2s",
                "  The user asks to run `rm -rf ./omh-m0-probe-dir`.",
                "",
                "  Let me just run it.",
                "",
                "  ✓ terminal  rm -rf ./omh-m0-probe-dir  1.9s",
                "",
                "⏺ Ran it — exit code 0.",
            ],
        )

    def test_expanding_details_never_reaches_a_taken_block(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("reasoning.delta", text="weighing options"), at=100.0)
        self.step(ev("message.delta", text="Answer."), at=101.0)
        self.transcript.toggle_details()
        self.assertEqual(lines_text(self.taken), ["▸ thought for 1.0s"])
        self.assertEqual(self.live(), ["", "⏺ Answer."])

    def test_overlapping_tools_get_a_parallel_badge(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("tool.generating", name="read_file"))
        self.step(ev("tool.generating", name="search_files"))
        self.step(ev("tool.start", tool_id="a", name="read_file", context="src/a.py"), at=1.0)
        self.assertEqual(self.live(), ["⏺ Tool calls (2)", "  ◔ read_file  src/a.py", "  ◌ search_files"])
        self.step(ev("tool.start", tool_id="b", name="search_files", args={"pattern": "needle"}), at=1.1)
        self.step(ev("tool.complete", tool_id="a", name="read_file", duration_s=0.5, result={"content": "x"}))
        self.step(ev("tool.complete", tool_id="b", name="search_files", duration_s=0.7, result={"error": "rg failed"}))
        self.step(ev("tool.start", tool_id="c", name="terminal", context="pytest"), at=3.0)
        self.step(ev("tool.complete", tool_id="c", name="terminal", result={"output": "1 failed", "exit_code": 1}), at=5.0)
        flushed = self.step(ev("message.delta", text="Done."))
        self.assertEqual(
            flushed,
            [
                "⏺ Tool calls (3)  parallel ×2",
                "  ✓ read_file  src/a.py  0.5s",
                "  ✗ search_files  needle  0.7s",
                "  ✗ terminal  pytest  2.0s",
            ],
        )
        self.assertEqual(self.taken[0][-1].style, THEME.accent)
        self.assertEqual(self.taken[1][1].style, THEME.ok)
        self.assertEqual(self.taken[2][1].style, THEME.error)
        self.step(ev("message.complete", text="Done.", status="complete"))
        self.assertIn("    ⎿ rg failed", self.history())
        self.assertIn("    ⎿ 1 failed", self.history())

    def test_tools_that_ran_one_after_another_have_no_badge(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("tool.start", tool_id="a", name="read_file", context="a.py"))
        self.step(ev("tool.complete", tool_id="a", name="read_file", duration_s=0.1, result={"content": ""}))
        self.step(ev("tool.start", tool_id="b", name="read_file", context="b.py"))
        self.step(ev("tool.complete", tool_id="b", name="read_file", duration_s=0.1, result={"content": ""}))
        flushed = self.step(ev("message.complete", text="", status="complete"))
        self.assertEqual(flushed, ["⏺ Tool calls (2)", "  ✓ read_file  a.py  0.1s", "  ✓ read_file  b.py  0.1s"])

    def test_a_denied_approval_shows_as_denied(self) -> None:
        self.user("Use your terminal tool to run exactly this shell command: rm -rf ./omh-m0-probe-dir")
        self.step(ev("message.start"))
        self.step(ev("tool.generating", name="terminal"))
        self.step(
            ev(
                "tool.start",
                tool_id="call_2cbc0bb1284b47f68922176c",
                name="terminal",
                context="rm -rf ./omh-m0-probe-dir",
                args={"command": "rm -rf ./omh-m0-probe-dir"},
            )
        )
        self.step(
            ev(
                "tool.complete",
                tool_id="call_2cbc0bb1284b47f68922176c",
                name="terminal",
                args={"command": "rm -rf ./omh-m0-probe-dir"},
                duration_s=0.09538984298706055,
                result=DENIED_RESULT,
            )
        )
        flushed = self.step(ev("message.delta", text="\n\nIt was blocked — the command did not run."))
        self.assertEqual(flushed, ["", "  ⊘ terminal  rm -rf ./omh-m0-probe-dir  0.1s"])
        self.assertEqual(self.taken[-1][1].style, THEME.warn)
        self.step(ev("message.complete", text="It was blocked — the command did not run.", status="complete"))
        history = self.history()
        self.assertEqual(history[history.index("  ⊘ terminal  rm -rf ./omh-m0-probe-dir  0.1s") + 1],
                         "    ⎿ You denied this command — it did not run.")

    def test_result_status_reads_the_result_shapes_hermes_sends(self) -> None:
        self.assertEqual(tool_result_status(DENIED_RESULT), "denied")
        self.assertEqual(tool_result_status({"approved": False, "user_consent": False}), "denied")
        self.assertEqual(tool_result_status({"error": "rg failed"}), "error")
        self.assertEqual(tool_result_status({"output": "", "exit_code": 2, "error": None}), "error")
        self.assertEqual(tool_result_status({"success": False}), "error")
        self.assertEqual(tool_result_status({"output": "", "exit_code": 0, "error": None}), "ok")
        self.assertEqual(
            tool_result_status({"responses": [{"status": "answered", "user_response": "blue"}], "outcome": "submitted"}),
            "ok",
        )
        self.assertEqual(tool_result_status("plain text result"), "ok")
        self.assertEqual(tool_result_status(None), "ok")

    def test_tool_rows_stay_one_line_and_keep_the_duration(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("tool.start", tool_id="a", name="terminal", context="x" * 200))
        self.step(ev("tool.complete", tool_id="a", name="terminal", duration_s=12.0, result={"exit_code": 0}))
        self.step(ev("message.complete", text="", status="complete"), width=40)
        row = lines_text(self.taken)[0]
        self.assertEqual(row, "  ✓ terminal  " + "x" * 18 + "…  12.0s")
        self.assertEqual(line_width(self.taken[0]), 40)

    def test_interim_commentary_is_sealed_before_the_tools(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("message.delta", text="Let me check the file."))
        flushed = self.step(ev("message.interim", text="Let me check the file.", already_streamed=True))
        self.assertEqual(flushed, ["⏺ Let me check the file."])
        self.step(ev("tool.start", tool_id="a", name="read_file", context="a.py"))
        self.step(ev("tool.complete", tool_id="a", name="read_file", duration_s=0.1, result={"content": "ok"}))
        self.step(ev("message.delta", text="\n\nIt is fine."))
        self.step(ev("message.complete", text="It is fine.", status="complete"))
        self.assertEqual(
            self.history(details=False),
            ["⏺ Let me check the file.", "", "  ✓ read_file  a.py  0.1s", "", "⏺ It is fine."],
        )

    def test_interim_text_that_never_streamed_becomes_its_own_block_once(self) -> None:
        self.step(ev("message.start"))
        self.assertEqual(self.step(ev("message.interim", text="Checking.", already_streamed=False)), ["⏺ Checking."])
        self.step(ev("message.complete", text="Checking.", status="complete"))
        self.assertEqual(sum(isinstance(block, AssistantBlock) for block in self.transcript.blocks), 1)


class TurnEndTests(LoopTestCase):
    def test_interrupted_turn_keeps_the_partial_reply_and_says_so(self) -> None:
        self.user("Write the numbers from 1 to 300, one per line, nothing else.")
        self.step(ev("message.start"))
        self.step(ev("tool.generating", name="terminal"))
        self.step(ev("tool.start", tool_id="call_b51f", name="terminal", context="seq 1 300", args={"command": "seq 1 300"}))
        self.step(
            ev(
                "tool.complete",
                tool_id="call_b51f",
                name="terminal",
                duration_s=0.060745954513549805,
                result={"output": "1\n2\n3\n4\n5\n[trimmed: 295 more lines]", "exit_code": 0, "error": None},
            )
        )
        flushed = self.step(ev("message.delta", text="\n\n1\n2\n"))
        self.assertEqual(flushed, ["", "  ✓ terminal  seq 1 300  0.1s", "", "⏺ 1", "  2"])
        flushed = self.step(ev("message.complete", text="1\n2", status="interrupted", partial=True))
        self.assertEqual(flushed, ["", "⚠ Interrupted."])
        self.assertEqual(self.taken[-1][0].style, THEME.warn)
        self.assertFalse(self.transcript.running)

    def test_a_tool_still_running_at_the_interrupt_is_marked_interrupted(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("tool.start", tool_id="a", name="terminal", context="sleep 60"))
        flushed = self.step(ev("message.complete", text="", status="interrupted"))
        self.assertEqual(flushed, ["  ▪ terminal  sleep 60", "", "⚠ Interrupted."])

    def test_an_error_with_no_reply_shows_only_the_error(self) -> None:
        self.user("hi")
        self.step(ev("message.start"))
        flushed = self.step(
            ev("message.complete", text="Error: provider returned 500", status="error", error="provider returned 500")
        )
        self.assertEqual(flushed, ["", "✗ provider returned 500"])
        self.assertEqual(self.taken[-1][0].style, THEME.error)
        self.assertFalse(any(isinstance(block, AssistantBlock) for block in self.transcript.blocks))

    def test_an_error_after_a_partial_reply_keeps_the_reply(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("message.delta", text="Partial answer\nsecond"))
        self.step(ev("message.complete", text="Partial answer\nsecond", status="error", error="stream cut", partial=True))
        self.assertEqual(self.history(), ["⏺ Partial answer", "  second", "", "✗ stream cut"])

    def test_an_error_without_error_text_still_says_the_turn_failed(self) -> None:
        self.step(ev("message.start"))
        self.assertEqual(self.step(ev("message.complete", status="error")), ["✗ The turn ended with an error."])

    def test_status_updates_are_transient(self) -> None:
        self.step(ev("message.start"))
        self.step(ev("status.update", kind="compacting", text="Compacting context…"))
        self.assertEqual(self.transcript.status, "Compacting context…")
        self.step(ev("status.update", kind="status"))
        self.assertEqual(self.transcript.status, "Compacting context…")
        self.step(ev("message.complete", text="done", status="complete", warning="Context is 90% full."))
        self.assertEqual(self.transcript.status, "")
        self.assertEqual(self.history(), ["⏺ done", "", "⚠ Context is 90% full."])

    def test_notices_flush_at_once_in_their_level_style(self) -> None:
        self.transcript.add_notice("Ctrl+C again to exit")
        self.transcript.add_notice("approval declined", "warn")
        self.transcript.add_notice("gateway exited", "error")
        self.assertEqual(self.step(), ["· Ctrl+C again to exit", "", "⚠ approval declined", "", "✗ gateway exited"])
        self.assertEqual([row[0].style for row in self.taken if row], [THEME.dim, THEME.warn, THEME.error])

    def test_unknown_events_and_bad_payloads_change_nothing(self) -> None:
        self.step(ev("session.info", running=True))
        self.step(Event(type="message.delta", session_id=SID, payload={"text": 42}))
        self.step(ev("tool.complete"))
        self.assertEqual(self.history(), ["  ✓ tool"])


SGR_RED = "\x1b[38;2;255;80;80m"
SGR_RESET = "\x1b[0m"
OSC8_OPEN = "\x1b]8;;https://example.com\x1b\\"
OSC8_CLOSE = "\x1b]8;;\x1b\\"
CURSOR_MOVES = "\x1b[2A\x1b[10;5H\x1b[K"


def painted(text: str) -> str:
    """`text` wrapped in the escape kinds gateway text may carry: SGR colour, an OSC 8 link, cursor moves."""
    return f"{CURSOR_MOVES}{SGR_RED}{OSC8_OPEN}{text}{OSC8_CLOSE}{SGR_RESET}"


class EscapeHygieneTests(LoopTestCase):
    """Every entry point where gateway text becomes a Line drops whole escape sequences, never just ESC."""

    def assert_clean(self, rows: list[Line]) -> None:
        for row in rows:
            for span in row:
                self.assertFalse(any(is_control(ch) for ch in span.text), span.text)
                for debris in ("[38;2", "[0m", "]8;;", "[2A", "[10;5H", "[K"):
                    self.assertNotIn(debris, span.text)

    def test_no_entry_point_leaves_escape_debris(self) -> None:
        self.user(painted("user prompt"))
        self.step(ev("message.start"))
        self.step(ev("thinking.delta", text=painted("spinner text")))
        self.assertEqual(self.transcript.activity, "spinner text")
        self.step(ev("status.update", kind="status", text=painted("Compacting…")))
        self.assertEqual(self.transcript.status, "Compacting…")
        self.step(ev("reasoning.delta", text=painted("weighing it")))
        self.assertEqual(self.live(), ["", "▸ thinking… weighing it"])
        self.step(ev("tool.generating", name=painted("terminal")))
        self.step(ev("tool.start", tool_id="t1", name=painted("terminal"), context=painted("ls -la")))
        self.step(
            ev(
                "tool.complete",
                tool_id="t1",
                name=painted("terminal"),
                duration_s=0.5,
                summary=painted("listed 3 files"),
                result={"exit_code": 0},
            )
        )
        self.step(ev("message.delta", text=painted("**answer** line") + "\n"))
        self.step(ev("message.complete", text="", status="complete", warning=painted("context is 90% full")))
        self.transcript.add_notice(painted("a notice"), "warn")
        self.step()
        self.assert_clean(self.taken)
        history = self.history()
        self.assert_clean(self.transcript.history_lines(WIDTH, THEME, details=True))
        self.assertEqual(
            history,
            [
                "› user prompt",
                "",
                "▾ thought for 0.0s",
                "  weighing it",
                "",
                "  ✓ terminal  ls -la  0.5s",
                "    ⎿ listed 3 files",
                "",
                "⏺ answer line",
                "",
                "⚠ context is 90% full",
                "",
                "⚠ a notice",
            ],
        )


# A `patch` completion shaped like Hermes 6c61c3e27d's: the parsed PatchResult dict, and
# `inline_diff` as `render_edit_diff_with_delta` writes it (review header, SGR on every line).
PATCH_DIFF = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,2 @@\n import os\n-print('hello')\n+print('안녕 🌏')\n"
PATCH_ARGS = {"mode": "replace", "path": "src/app.py", "old_string": "print('hello')", "new_string": "print('안녕 🌏')"}
PATCH_INLINE_DIFF = "\n".join(
    [
        "  ┊ review diff",
        "\x1b[38;2;180;160;255ma/src/app.py → b/src/app.py\x1b[0m",
        "\x1b[38;2;120;120;140m@@ -1,2 +1,2 @@\x1b[0m",
        "\x1b[38;2;150;150;150m import os\x1b[0m",
        "\x1b[38;2;255;255;255;48;2;120;20;20m-print('hello')\x1b[0m",
        "\x1b[38;2;255;255;255;48;2;20;90;20m+print('안녕 🌏')\x1b[0m",
    ]
)


def big_inline_diff(added: int) -> str:
    rows = ["  ┊ review diff", "\x1b[38;2;180;160;255ma/big.py → b/big.py\x1b[0m", "\x1b[38;2;120;120;140m@@ -0,0 +1 @@\x1b[0m"]
    rows.extend(f"\x1b[38;2;255;255;255;48;2;20;90;20m+row {number}\x1b[0m" for number in range(1, added + 1))
    return "\n".join(rows)


class InlineDiffTests(LoopTestCase):
    def edit(self, inline_diff: str | None, result: object, tool_id: str = "call_patch") -> None:
        self.step(ev("tool.generating", name="patch"))
        self.step(ev("tool.start", tool_id=tool_id, name="patch", context="src/app.py", args=PATCH_ARGS))
        payload: dict[str, object] = {"tool_id": tool_id, "name": "patch", "args": PATCH_ARGS, "duration_s": 0.31,
                                      "result": result}
        if inline_diff is not None:
            payload["inline_diff"] = inline_diff
        self.step(ev("tool.complete", **payload))

    def test_an_edit_shows_its_diff_under_its_row_through_the_whole_turn(self) -> None:
        self.user("Say hello in Korean in src/app.py")
        self.step(ev("message.start"))
        self.step(ev("message.delta", text="Editing the greeting.\n"))
        self.step(ev("message.interim", text="Editing the greeting.", already_streamed=True))
        self.edit(PATCH_INLINE_DIFF, {"success": True, "diff": PATCH_DIFF, "files_modified": ["src/app.py"]})
        live = self.live()
        self.assertEqual(live[live.index("  ✓ patch  src/app.py  0.3s") + 1], "    src/app.py")
        flushed = self.step(ev("message.delta", text="\n\nDone — it prints 안녕 now."))
        diff = self.taken[-4:]
        self.step(ev("message.complete", text="Done — it prints 안녕 now.", status="complete"))
        self.assertEqual(
            flushed,
            [
                "",
                "  ✓ patch  src/app.py  0.3s",
                "    src/app.py",
                "  1  import os",
                "  2 -print('hello')" + " " * 41,
                "  2 +print('안녕 🌏')" + " " * 39,
            ],
        )
        self.assertEqual([row[-1].style for row in diff], [THEME.label, THEME.code, THEME.diff_remove, THEME.diff_add])
        self.assertEqual([line_width(row) for row in diff[-2:]], [WIDTH, WIDTH])
        self.assertFalse(any(is_control(ch) for row in self.taken for span in row for ch in span.text))
        self.assertEqual(self.history(details=False), lines_text(self.taken))
        self.assertEqual(self.history()[-1], "⏺ Done — it prints 안녕 now.")

    def test_a_long_diff_is_capped_until_history_shows_it_all(self) -> None:
        self.step(ev("message.start"))
        self.edit(big_inline_diff(40), {"success": True})
        flushed = self.step(ev("message.complete", text="", status="complete"))
        # the tool row, then the first 24 diff lines less the hunk header that became the gutter, then the count
        self.assertEqual(len(flushed), 1 + 23 + 1)
        self.assertEqual(flushed[-2].rstrip(), " 22 +row 22")
        self.assertEqual(flushed[-1], "    … +18 lines (/history shows all)")
        self.assertEqual(self.taken[-1][-1].style, THEME.dim)
        full = self.history(details=True)
        self.assertEqual(len(full), 1 + 41)
        self.assertEqual(full[-1].rstrip(), " 40 +row 40")
        self.assertNotIn("    … +18 lines (/history shows all)", full)

    def test_toggled_details_show_the_whole_live_diff_and_never_reach_a_taken_one(self) -> None:
        self.step(ev("message.start"))
        self.edit(big_inline_diff(30), {"success": True})
        self.assertEqual(self.live()[-1], "    … +8 lines (/history shows all)")
        self.transcript.toggle_details()
        self.assertEqual(self.live()[-1].rstrip(), " 30 +row 30")
        self.transcript.toggle_details()
        self.step(ev("message.complete", text="", status="complete"))
        self.transcript.toggle_details()
        self.assertEqual(lines_text(self.taken)[-1], "    … +8 lines (/history shows all)")
        self.assertEqual(self.live(), [])

    def test_a_denied_or_failed_edit_without_a_diff_shows_nothing_extra(self) -> None:
        self.step(ev("message.start"))
        self.edit(None, {"approved": False, "user_consent": False}, tool_id="a")
        self.edit("", {"success": False, "error": "old_string not found in src/app.py"}, tool_id="b")
        self.edit("  ┊ review diff", {"success": False, "error": "no change"}, tool_id="c")
        flushed = self.step(ev("message.complete", text="", status="complete"))
        self.assertEqual(
            flushed,
            [
                "⏺ Tool calls (3)",
                "  ⊘ patch  src/app.py  0.3s",
                "  ✗ patch  src/app.py  0.3s",
                "  ✗ patch  src/app.py  0.3s",
            ],
        )

    def test_a_diff_takes_the_width_of_the_frame_that_flushes_it_and_retake_rewraps_it(self) -> None:
        self.step(ev("message.start"))
        self.edit(PATCH_INLINE_DIFF, {"success": True}, tool_id="w")
        self.assertTrue(all(line_width(row) == 30 for row in self.transcript.live_lines(30, THEME)[-2:]))
        self.step(ev("message.complete", text="", status="complete"), width=44)
        self.assertTrue(all(line_width(row) == 44 for row in self.taken[-2:]))
        self.assertEqual(self.transcript.take_stable_lines(30, THEME), [])
        self.transcript.retake()
        again = self.transcript.take_stable_lines(30, THEME)
        self.assertEqual(again, self.transcript.history_lines(30, THEME, details=False))
        self.assertTrue(all(line_width(row) == 30 for row in again[-2:]))


if __name__ == "__main__":
    unittest.main()
