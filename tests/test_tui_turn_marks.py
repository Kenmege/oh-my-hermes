"""Your prompt's band, the queue's tag, and where a steer lands.

Your prompt sits on `user_band` behind `›`, filled to the right edge by an
erase in that background rather than by spaces. A prompt the queue sent once
the turn before it ended says `⧗ from queue`. A steer shows as
`↪ steer · <text>` under what is running until Hermes answers, and is
written into the transcript at the next block boundary after that, so it
never closes a running tool group (whose calls would read as cut off) or
splits a streaming reply.
"""

from __future__ import annotations

import unittest

from _tui_lines import line_text, lines_text
from test_tui_modes import CTRL_ENTER, SID, answer, drawn, make_app, type_text
from test_tui_theme import DARK_BACKGROUNDS, LIGHT_BACKGROUNDS, contrast
from omh.skin_pack import SKIN_THEMES, skin_colors
from omh.tui.app import SLOT_ARM_SECONDS
from omh.tui.gateway import Event, ServerRequest
from omh.tui.keys import CHAR, ENTER, RIGHT, Key
from omh.tui.screen import ERASE_TO_LINE_END, paint_line
from omh.tui.style import SGR_RESET, Span, Style, fill
from omh.tui.theme import DEFAULT_THEME as THEME
from omh.tui.theme import build_theme
from omh.tui.transcript import (
    STEER_QUEUED,
    TOOL_OK,
    TOOL_RUNNING,
    AssistantBlock,
    NoticeBlock,
    SteerBlock,
    ToolGroup,
    TurnSummaryBlock,
    UserBlock,
    block_rows,
)
from omh.tui.width import line_width


def ev(kind: str, **payload: object) -> Event:
    return Event(kind, SID, payload)


class BandTests(unittest.TestCase):
    def test_every_row_of_a_prompt_is_on_the_band_and_ends_in_a_fill(self) -> None:
        rows = block_rows(UserBlock("first line\nsecond line"), 40, THEME)
        self.assertEqual(lines_text(rows), ["› first line", "  second line"])  # no spaces to copy
        for row in rows:
            self.assertEqual({span.style.bg for span in row}, {THEME.user_band.bg})
            self.assertEqual(row[-1], fill(THEME.user_band))

    def test_the_fill_erases_to_the_edge_in_its_background_and_a_full_row_erases_nothing(self) -> None:
        band = Style(bg="#102030")
        painted = paint_line((Span("› go", band), fill(band)), 20)
        self.assertTrue(painted.endswith(f"{band.sgr()}{ERASE_TO_LINE_END}{SGR_RESET}"), repr(painted))
        self.assertEqual(paint_line((Span("› go"),), 20), f"› go{ERASE_TO_LINE_END}")  # no fill: a plain erase
        self.assertEqual(paint_line((Span("› go"), Span("")), 20), f"› go{ERASE_TO_LINE_END}")  # empty, no background
        self.assertNotIn(ERASE_TO_LINE_END, paint_line((Span("x" * 20, band), fill(band)), 20))

    def test_the_band_is_a_tint_the_ink_still_reads_on_for_every_palette_and_background(self) -> None:
        for skin in SKIN_THEMES:
            for background in (None, *LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                theme = build_theme(skin, background)
                surface = background or skin_colors(skin.skin_name)["status_bar_bg"]
                ink = "#000000" if background in LIGHT_BACKGROUNDS else skin_colors(skin.skin_name)["banner_text"]
                with self.subTest(theme=skin.short_name, background=background):
                    self.assertNotEqual(theme.user_band.bg, surface)
                    self.assertLess(contrast(theme.user_band.bg, surface), 1.5)  # a tint, not a block of colour
                    self.assertGreaterEqual(contrast(ink, theme.user_band.bg), 4.5)


class FromQueueTests(unittest.TestCase):
    def test_a_prompt_the_queue_sent_says_so_and_a_typed_one_does_not(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "next one")
        app._on_input(Key(ENTER))
        app._on_event(ev("message.complete", text="done"))
        self.assertEqual(client.calls[-1], ("prompt.submit", {"session_id": SID, "text": "next one", "queued": True}))
        users = [block for block in app.transcript.blocks if isinstance(block, UserBlock)]
        self.assertEqual([line_text(block_rows(block, 80, THEME)[0]) for block in users], ["› next one  ⧗ from queue"])
        typed = block_rows(UserBlock("typed"), 80, THEME)[0]
        self.assertEqual(line_text(typed), "› typed")

    def test_the_tag_waits_for_room(self) -> None:
        row = block_rows(UserBlock("a long prompt that fills the row", from_queue=True), 34, THEME)[0]
        self.assertEqual(line_text(row), "› a long prompt that fills the row")


class SteerPlacementTests(unittest.TestCase):
    def steer(self, app: object, text: str) -> None:
        type_text(app, text)  # type: ignore[arg-type]
        app._on_input(CTRL_ENTER)  # type: ignore[attr-defined]

    def test_a_steer_during_a_running_tool_leaves_the_tool_running_and_lands_after_its_group(self) -> None:
        app, client = make_app(busy=True)
        app._on_event(ev("message.start"))
        app._on_event(ev("tool.start", tool_id="t1", name="terminal", context="pytest"))
        self.steer(app, "only the failing test")
        live = lines_text(app.transcript.live_lines(80, THEME))
        self.assertEqual(live[-1], "↪ steer · only the failing test")  # under what is running, until Hermes answers
        answer(app, client, {"status": "queued", "text": "only the failing test"})
        group = app.transcript.blocks[-1]
        self.assertIsInstance(group, ToolGroup)
        self.assertEqual(group.tools[0].status, TOOL_RUNNING)  # not cut off by the steer
        self.assertEqual(lines_text(app.transcript.live_lines(80, THEME))[-1], "↪ steer queued by Hermes · only the failing test")
        app._on_event(ev("tool.complete", tool_id="t1", name="terminal", result={"output": "1 passed"}))
        app._on_event(ev("message.delta", text="Running only that test now."))
        kinds = [type(block) for block in app.transcript.blocks]
        self.assertEqual(kinds, [ToolGroup, SteerBlock, AssistantBlock])
        self.assertEqual(app.transcript.blocks[0].tools[0].status, TOOL_OK)
        self.assertEqual(app.transcript.blocks[1].state, STEER_QUEUED)

    def test_the_steer_command_is_its_own_echo_and_leaves_a_running_tool_running(self) -> None:
        # Found live: `› /steer ...` echoed as a command closed the tool group and marked the running call `▪`.
        app, client = make_app(busy=True)
        app._on_event(ev("message.start"))
        app._on_event(ev("tool.start", tool_id="t1", name="terminal", context="sleep 6"))
        type_text(app, "/steer also echo steered")
        app._on_input(Key(ENTER))
        answer(app, client, {"status": "queued", "text": "also echo steered"})
        (group,) = app.transcript.blocks
        self.assertEqual(group.tools[0].status, TOOL_RUNNING)
        self.assertEqual(lines_text(app.transcript.live_lines(80, THEME))[-1], "↪ steer queued by Hermes · also echo steered")
        self.assertEqual(app.composer.history[-1], "/steer also echo steered")  # Up still finds what was typed

    def test_a_steer_during_a_streaming_reply_does_not_split_it(self) -> None:
        app, client = make_app(busy=True)
        app._on_event(ev("message.start"))
        app._on_event(ev("message.delta", text="First half, "))
        self.steer(app, "shorter please")
        answer(app, client, {"status": "queued", "text": "shorter please"})
        app._on_event(ev("message.delta", text="second half."))
        app._on_event(ev("message.complete", text="First half, second half.", status="complete"))
        replies = [block.text for block in app.transcript.blocks if isinstance(block, AssistantBlock)]
        self.assertEqual(replies, ["First half, second half."])
        kinds = [type(block).__name__ for block in app.transcript.blocks]
        self.assertEqual(kinds[:2], ["AssistantBlock", "SteerBlock"])

    def test_the_screen_shows_the_steer_waiting_then_once_in_scrollback(self) -> None:
        app, client = make_app(busy=True, rows=16)
        app._on_event(ev("message.start"))
        app._on_event(ev("tool.start", tool_id="t1", name="terminal", context="ls"))
        self.steer(app, "and the docs")
        self.assertIn("↪ steer · and the docs", drawn(app))
        answer(app, client, {"status": "queued", "text": "and the docs"})
        app._on_event(ev("tool.complete", tool_id="t1", name="terminal", result={"output": ""}))
        app._on_event(ev("message.complete", text="done", status="complete"))
        drawn(app)
        screen = app.terminal.screen.history()  # type: ignore[attr-defined]  # scrollback and the screen
        self.assertEqual(screen.count("↪ steer queued by Hermes · and the docs"), 1, screen)
        self.assertNotIn("↪ steer · and the docs", screen)

    def test_a_refused_steer_reads_in_amber_and_waits_in_the_queue(self) -> None:
        app, client = make_app(busy=True)
        self.steer(app, "keep this")
        answer(app, client, error={"code": 4010, "message": "agent does not support steer"})
        (block,) = [block for block in app.transcript.blocks if isinstance(block, SteerBlock)]
        (row,) = block_rows(block, 100, THEME)
        self.assertEqual(line_text(row), "⧗ queued — Hermes refused the steer (4010: agent does not support steer) · keep this")
        self.assertEqual({span.style for span in row}, {THEME.warn})
        self.assertEqual([text for text, *_ in app.queued], ["keep this"])


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class TurnRuleTests(unittest.TestCase):
    def test_a_turn_closes_with_its_facts_on_a_thin_rule_to_the_edge(self) -> None:
        (row,) = block_rows(TurnSummaryBlock(34, 3, 1200), 50, THEME)
        text = line_text(row)
        self.assertTrue(text.startswith("✻ 34s · 3 tools · ↓1.2k  ─"), text)
        self.assertEqual(line_width(row), 50)
        self.assertEqual(set(text.split("  ", 1)[1]), {"─"})
        self.assertEqual(row[-1].style, THEME.rule)

    def test_a_narrow_terminal_keeps_the_facts_and_drops_the_rule(self) -> None:
        (row,) = block_rows(TurnSummaryBlock(34, 3, 1200), 24, THEME)
        self.assertEqual(line_text(row), "✻ 34s · 3 tools · ↓1.2k")


class ApprovalOutcomeTests(unittest.TestCase):
    COMMAND = "rm -rf ./build"

    def asked(self) -> tuple[object, object, Clock]:
        clock = Clock()
        app, client = make_app(busy=True, clock=clock)
        app._on_event(ev("message.start"))
        app._on_event(ev("tool.start", tool_id="t1", name="terminal", context=self.COMMAND))
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": self.COMMAND, "description": "delete"}))
        clock.now += SLOT_ARM_SECONDS + 0.1
        return app, client, clock

    def outcome(self, app: object) -> tuple[str, object]:
        (block,) = [block for block in app.transcript.blocks if isinstance(block, NoticeBlock)]  # type: ignore[attr-defined]
        (row,) = block_rows(block, 80, THEME)
        return line_text(row), row[0].style

    def test_a_denial_reads_in_red_ahead_of_the_call_and_leaves_it_running(self) -> None:
        app, client, _clock = self.asked()
        app._on_input(Key(CHAR, "d"))
        self.assertEqual(client.answers, [("srq-1", {"result": {"choice": "deny"}})])
        self.assertEqual(self.outcome(app), (f"✗ denied · {self.COMMAND}", THEME.error))
        notice, group = app.transcript.blocks
        self.assertIsInstance(group, ToolGroup)
        self.assertEqual(group.tools[0].status, TOOL_RUNNING)  # Hermes reports what the call did next

    def test_allowing_reads_in_green(self) -> None:
        for keys, words in (([Key(RIGHT)], "allowed once"), ([Key(RIGHT), Key(RIGHT)], "allowed for this session")):
            with self.subTest(words=words):
                app, _client, _clock = self.asked()
                for key in (*keys, Key(ENTER)):
                    app._on_input(key)
                self.assertEqual(self.outcome(app), (f"✓ {words} · {self.COMMAND}", THEME.ok))

    def test_over_a_streaming_reply_the_answer_waits_for_the_boundary(self) -> None:
        clock = Clock()
        app, _client = make_app(busy=True, clock=clock)
        app._on_event(ev("message.start"))
        app._on_event(ev("message.delta", text="Deleting it now"))
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": self.COMMAND}))
        clock.now += SLOT_ARM_SECONDS + 0.1
        app._on_input(Key(CHAR, "d"))
        app._on_event(ev("message.delta", text=", or not: you said no."))
        app._on_event(ev("message.complete", text="Deleting it now, or not: you said no.", status="complete"))
        kinds = [type(block).__name__ for block in app.transcript.blocks]
        self.assertEqual(kinds[:2], ["AssistantBlock", "NoticeBlock"])
        self.assertEqual([block.text for block in app.transcript.blocks if isinstance(block, AssistantBlock)],
                         ["Deleting it now, or not: you said no."])  # one reply, not split by the answer


if __name__ == "__main__":
    unittest.main()
