"""Your prompt's band, the queue's tag, and where a steer lands.

Your prompt sits on `user_band` behind `›`, filled to the right edge by an
erase in that background rather than by spaces. A prompt the queue sent once
the turn before it ended says `⧗ from queue`. A steer shows as
`↪ steer · <text>` under what is running until Hermes answers, and is
written into the transcript at the next block boundary after that, so it
never closes a running tool group (whose calls would read as cut off) or
splits a streaming reply. A turn's done line sits right above the panel
however tall its last tool group was.
"""

from __future__ import annotations

import unittest

from _tui_lines import line_text, lines_text
from test_tui_modes import CTRL_ENTER, SID, answer, drawn, make_app, type_text
from test_tui_panels import hud_payload, phased_plan
from test_tui_theme import DARK_BACKGROUNDS, LIGHT_BACKGROUNDS, contrast
from omh.skin_pack import SKIN_THEMES, skin_colors
from omh.tui.app import SLOT_ARM_SECONDS
from omh.tui.gateway import Event, Response, ServerRequest
from omh.tui.keys import CHAR, ENTER, RIGHT, Key
from omh.tui.panels import HudSnapshot
from omh.tui.screen import ERASE_TO_LINE_END, paint_line
from omh.tui.style import SGR_RESET, Span, Style, fill
from omh.tui.theme import DEFAULT_THEME as THEME
from omh.tui.theme import build_theme, is_light
from omh.tui.transcript import (
    NOTICE_ERROR,
    NOTICE_OK,
    NOTICE_WARN,
    OUTPUT_FAILED,
    STEER_QUEUED,
    TOOL_DENIED,
    TOOL_ERROR,
    TOOL_OK,
    TOOL_RUNNING,
    AssistantBlock,
    CommandBlock,
    NoticeBlock,
    SteerBlock,
    ToolGroup,
    ToolRow,
    TurnSummaryBlock,
    UserBlock,
    block_rows,
)
from omh.tui.width import line_width


def ev(kind: str, **payload: object) -> Event:
    return Event(kind, SID, payload)


# A theme measured against a background the terminal reported: only then is there a band to paint.
REPORTED = build_theme(SKIN_THEMES[0], "#1E1E1E")


class BandTests(unittest.TestCase):
    def test_every_row_of_a_prompt_is_on_the_band_and_ends_in_a_fill(self) -> None:
        rows = block_rows(UserBlock("first line\nsecond line"), 40, REPORTED)
        self.assertEqual(lines_text(rows), ["› first line", "  second line"])  # no spaces to copy
        for row in rows:
            self.assertEqual({span.style.bg for span in row}, {REPORTED.user_band.bg})
            self.assertEqual(row[-1], fill(REPORTED.user_band))

    def test_with_no_background_reported_the_prompt_has_no_band(self) -> None:
        # The theme would only guess dark, and the person's own text colour on a guessed band read 1.4:1 on light.
        for skin in SKIN_THEMES:
            theme = build_theme(skin, None)
            with self.subTest(theme=skin.short_name):
                self.assertIsNone(theme.user_band.bg)
                rows = block_rows(UserBlock("a prompt"), 40, theme)
                self.assertEqual({span.style.bg for row in rows for span in row}, {None})

    def test_the_fill_erases_to_the_edge_in_its_background_and_a_full_row_erases_nothing(self) -> None:
        band = Style(bg="#102030")
        painted = paint_line((Span("› go", band), fill(band)), 20)
        self.assertTrue(painted.endswith(f"{band.sgr()}{ERASE_TO_LINE_END}{SGR_RESET}"), repr(painted))
        self.assertEqual(paint_line((Span("› go"),), 20), f"› go{ERASE_TO_LINE_END}")  # no fill: a plain erase
        self.assertEqual(paint_line((Span("› go"), Span("")), 20), f"› go{ERASE_TO_LINE_END}")  # empty, no background
        self.assertNotIn(ERASE_TO_LINE_END, paint_line((Span("x" * 20, band), fill(band)), 20))

    def test_the_band_is_a_tint_the_ink_still_reads_on_for_every_palette_and_background(self) -> None:
        for skin in SKIN_THEMES:
            for background in (*LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                theme = build_theme(skin, background)
                surface = background
                ink = "#000000" if background in LIGHT_BACKGROUNDS else skin_colors(skin.skin_name)["banner_text"]
                with self.subTest(theme=skin.short_name, background=background):
                    self.assertNotEqual(theme.user_band.bg, surface)
                    self.assertLess(contrast(theme.user_band.bg, surface), 1.5)  # a tint, not a block of colour
                    self.assertGreaterEqual(contrast(ink, theme.user_band.bg), 4.5)


class OutcomeBandTests(unittest.TestCase):
    """Owner decision 6: an outcome's whole row sits on a band, green for allowed, red for a denial or a failure.

    Built like the prompt band: every span on the band's background and an
    erase to the right edge, so a copy carries no trailing spaces. Words on a
    band reach 4.5:1 against it and marks 3:1; with no background reported
    there is no band. Warnings and running rows carry none.
    """

    DENIAL = "You denied this command — it did not run."

    def outcomes(self, theme: object) -> list[tuple[str, list]]:
        """(band role, rows) for every kind of row that carries a band."""
        failed = ToolGroup([ToolRow("t1", "terminal", "pytest -q", TOOL_ERROR, duration_s=1.2, output=("1 failed",))], done=True)
        denied = ToolGroup([ToolRow("t2", "terminal", "rm -rf ./build", TOOL_DENIED, duration_s=0.1, output=(self.DENIAL,))],
                           done=True)
        command = CommandBlock("!false", [("partial output", OUTPUT_FAILED), ("exit 1", NOTICE_ERROR)], done=True)
        summarized = ToolGroup([ToolRow("t3", "read_file", "/nope.txt", TOOL_ERROR, duration_s=0.1,
                                        summary="read_file could not open the path", output=("File not found",))], done=True)
        return [
            ("ok_band", block_rows(NoticeBlock("allowed once · ls", NOTICE_OK), 60, theme)),  # type: ignore[arg-type]
            ("error_band", block_rows(NoticeBlock("denied · rm -rf ./build", NOTICE_ERROR), 60, theme)),  # type: ignore[arg-type]
            ("error_band", block_rows(NoticeBlock("The turn ended with an error.", NOTICE_ERROR), 60, theme)),  # type: ignore[arg-type]
            ("error_band", block_rows(failed, 60, theme)),  # type: ignore[arg-type]
            ("error_band", block_rows(denied, 60, theme)),  # type: ignore[arg-type]
            ("error_band", block_rows(summarized, 60, theme, expanded=True)),  # type: ignore[arg-type]  # Ctrl+O's summary row
            ("error_band", block_rows(command, 60, theme)[1:]),  # type: ignore[arg-type]  # its output, not its echo
        ]

    def test_every_outcome_row_is_on_its_band_to_the_edge_and_every_word_reads_on_it(self) -> None:
        for skin in SKIN_THEMES:
            for background in (*LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                theme = build_theme(skin, background)
                for role, rows in self.outcomes(theme):
                    band = getattr(theme, role)
                    for row in rows:
                        with self.subTest(theme=skin.short_name, background=background, row=line_text(row)):
                            self.assertEqual(row[-1], fill(band))  # an erase, not spaces
                            self.assertEqual(line_text(row), line_text(row).rstrip())
                            for span in row:
                                self.assertEqual(span.style.bg, band.bg)
                                if span.style.fg and span.text.strip():
                                    minimum = 4.5 if any(char.isalnum() for char in span.text) else 3.0
                                    self.assertGreaterEqual(contrast(span.style.fg, band.bg), minimum, span.text)

    def test_bands_are_light_on_a_light_terminal_and_dark_on_a_dark_one(self) -> None:
        for skin in SKIN_THEMES:
            for background in (*LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                theme = build_theme(skin, background)
                for role in ("ok_band", "error_band"):
                    with self.subTest(theme=skin.short_name, background=background, role=role):
                        band = getattr(theme, role).bg
                        self.assertEqual(is_light(band), background in LIGHT_BACKGROUNDS)
                        self.assertNotEqual(band, background)
        light = build_theme(SKIN_THEMES[0], "#FFFFFF")
        self.assertNotEqual(light.ok_band.bg, light.error_band.bg)  # green is not red

    def test_a_background_the_person_names_with_theme_light_or_dark_is_banded(self) -> None:
        # No guess there: the person said which background it is, though the terminal did not.
        for word, light in (("light", True), ("dark", False)):
            with self.subTest(theme=word):
                app, _client = make_app()
                app.adopt_background(None)
                type_text(app, f"/theme {word}")
                app._on_input(Key(ENTER))
                for role in ("user_band", "ok_band", "error_band"):
                    band = getattr(app.theme, role).bg
                    self.assertIsNotNone(band, role)
                    self.assertEqual(is_light(band), light, role)
                type_text(app, "/theme auto")
                app._on_input(Key(ENTER))
                self.assertIsNone(app.theme.error_band.bg)  # back to following a terminal that reports nothing

    def test_a_dark_background_colorfgbg_names_is_banded_as_theme_dark_bands_it(self) -> None:
        dark, _client = make_app()
        dark.adopt_background(None)
        type_text(dark, "/theme dark")
        dark._on_input(Key(ENTER))
        for value, expected in (("15;0", dark.theme), ("7;0", dark.theme), ("15;default;0", dark.theme),
                                ("0;15", build_theme(SKIN_THEMES[0], "#FFFFFF"))):
            with self.subTest(colorfgbg=value):
                app, _client = make_app()
                app.adopt_background(None, {"COLORFGBG": value})  # the terminal did not answer OSC 11
                for role in ("user_band", "ok_band", "error_band"):
                    self.assertIsNotNone(getattr(app.theme, role).bg, role)
                    self.assertEqual(getattr(app.theme, role), getattr(expected, role), role)
        for environ in ({}, {"COLORFGBG": "garbage"}, {"COLORFGBG": "15;99"}):
            with self.subTest(environ=environ):  # nothing reported: no band
                app, _client = make_app()
                app.adopt_background(None, environ)
                self.assertEqual([app.theme.user_band.bg, app.theme.ok_band.bg, app.theme.error_band.bg], [None] * 3)
        app, _client = make_app()
        app.adopt_background("#FFFFFF", {"COLORFGBG": "15;0"})  # an answer outranks the variable
        self.assertTrue(is_light(app.theme.error_band.bg))

    def test_with_no_background_reported_nothing_is_banded(self) -> None:
        for skin in SKIN_THEMES:
            theme = build_theme(skin, None)
            for _role, rows in self.outcomes(theme):
                with self.subTest(theme=skin.short_name):
                    self.assertEqual({span.style.bg for row in rows for span in row}, {None})

    def test_a_denial_shows_one_mark_the_failures_red_one(self) -> None:
        theme = build_theme(SKIN_THEMES[0], "#FFFFFF")
        styled = [row for _role, rows in self.outcomes(theme) for row in rows]
        rows = [line_text(row) for row in styled]
        self.assertFalse([row for row in rows if "⊘" in row], rows)
        denied = [row for row in styled if "terminal(rm -rf ./build)" in line_text(row)]
        failed = [row for row in styled if "terminal(pytest -q)" in line_text(row)]
        self.assertEqual([row[0] for row in denied], [row[0] for row in failed])
        self.assertEqual([row[0].text for row in denied], ["⏺"])
        self.assertIn("  ⎿  " + self.DENIAL, rows)  # told apart from a failure by its words, not only its colour

    def test_warnings_and_running_rows_carry_no_band(self) -> None:
        theme = build_theme(SKIN_THEMES[0], "#FFFFFF")
        running = ToolGroup([ToolRow("t1", "terminal", "sleep 9", TOOL_RUNNING, started_at=0.0)])
        for rows in (block_rows(NoticeBlock("careful", NOTICE_WARN), 60, theme), block_rows(running, 60, theme, now=0.7)):
            self.assertEqual({span.style.bg for row in rows for span in row}, {None})


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

    def test_the_tag_reads_on_the_band_it_sits_on(self) -> None:
        # Measured against the terminal's background, muted read 3.7 to 4.0:1 on the band.
        for skin in SKIN_THEMES:
            for background in (*LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                theme = build_theme(skin, background)
                row = block_rows(UserBlock("next", from_queue=True), 80, theme)[0]
                (tag,) = [span for span in row if "from queue" in span.text]
                with self.subTest(theme=skin.short_name, background=background):
                    self.assertGreaterEqual(contrast(tag.style.fg, theme.user_band.bg), 4.5)


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
        self.assertEqual(line_text(row), "⧗ queued — Hermes refused the steer (agent does not support steer) · keep this")
        self.assertEqual({span.style for span in row}, {THEME.warn})
        self.assertEqual([text for text, *_ in app.queued], ["keep this"])


class UnansweredSteerTests(unittest.TestCase):
    """A steer Hermes never answers is settled when its call expires: its text comes back, and is never sent again.

    Hermes may have taken it (an answer lost is no refusal), so nothing goes
    out for it, idle or not; the text returns to the composer, and to the
    draft a question gives back, never into an answer to Hermes.
    """

    BACK = "⧗ back in the composer — Hermes did not answer the steer in 120 s · use the other file"

    def steered(self, clock: Clock, busy_after: bool) -> tuple[object, object, int]:
        app, client = make_app(busy=True, clock=clock)
        app._on_event(ev("message.start"))
        type_text(app, "use the other file")
        app._on_input(CTRL_ENTER)
        steer = len(client.calls)
        if not busy_after:
            app._on_event(ev("message.complete", text="done", status="complete"))
        type_text(app, "an unrelated draft")  # the person moved on
        clock.now += 3600  # far past any call's answer time
        app._expire_calls()
        return app, client, steer

    def test_an_unanswered_steer_during_the_turn_comes_back_and_its_row_leaves_the_live_region(self) -> None:
        app, client, steer = self.steered(Clock(), busy_after=True)
        self.assertEqual(app.composer.text, "an unrelated draft\nuse the other file")
        self.assertFalse(app.queued)  # not queued: Hermes may have taken it
        app._on_event(ev("message.complete", text="done", status="complete"))
        taken = lines_text(app.transcript.take_stable_lines(100, THEME))  # what reaches scrollback
        self.assertNotIn("steer", " ".join(lines_text(app.transcript.live_lines(100, THEME))))  # nothing held back
        self.assertIn(self.BACK, taken)
        self.assertEqual([method for method, _params in client.calls], ["session.steer"])  # nothing sent for it

    def test_an_unanswered_steer_after_its_turn_ended_is_not_sent_as_a_prompt(self) -> None:
        app, client, steer = self.steered(Clock(), busy_after=False)
        self.assertEqual([method for method, _params in client.calls], ["session.steer"])
        self.assertFalse(app._busy())
        self.assertEqual(app.composer.text, "an unrelated draft\nuse the other file")
        app._on_response(Response(steer, {"status": "queued"}, None))  # Hermes' late answer: it had taken it
        self.assertIn(steer, client.forgotten)
        self.assertEqual([method for method, _params in client.calls], ["session.steer"])
        self.assertEqual(app.composer.text, "an unrelated draft\nuse the other file")  # given back once
        rows = [line_text(row) for block in app.transcript.blocks if isinstance(block, SteerBlock)
                for row in block_rows(block, 100, THEME)]
        self.assertEqual(rows, [self.BACK])

    def agent_steered(self, clock: Clock) -> tuple[object, object, int]:
        from test_tui_agent_strip import STARTS, agent_app, relay
        from omh.tui.keys import DOWN

        app, client = agent_app(busy=True, frames=[], clock=clock)
        app._on_event(ev("message.start"))
        relay(app, STARTS)
        app._on_input(Key(DOWN))
        app._on_input(Key(CHAR, "s"))
        type_text(app, "skip tests")
        app._on_input(Key(ENTER))
        return app, client, len(client.calls)

    def test_an_unanswered_agent_steer_gives_its_text_back_and_says_hermes_did_not_answer(self) -> None:
        clock = Clock()
        app, client, _steer = self.agent_steered(clock)
        clock.now += 3600
        app._expire_calls()
        self.assertEqual(app.composer.text, "skip tests")
        app._on_event(ev("message.complete", text="done", status="complete"))
        taken = lines_text(app.transcript.take_stable_lines(100, THEME))
        self.assertNotIn("steer", " ".join(lines_text(app.transcript.live_lines(100, THEME))))
        self.assertIn(
            "⧗ back in the composer — Hermes did not answer the steer in 120 s · find usages of: skip tests", taken
        )
        self.assertNotIn("prompt.submit", [method for method, _params in client.calls])

    def test_an_agent_steer_given_back_while_a_masked_question_is_open_stays_out_of_the_answer(self) -> None:
        clock = Clock()
        app, client, _steer = self.agent_steered(clock)
        app._on_server_request(ServerRequest("srq-1", "sudo", {"prompt": "[sudo] password"}))
        clock.now += SLOT_ARM_SECONDS  # keys in the first moment are ignored
        type_text(app, "hunter")  # the answer, masked
        clock.now += 3600
        app._expire_calls()
        self.assertEqual(app.composer.text, "hunter")
        app._on_input(Key(ENTER))
        self.assertEqual(client.answers, [("srq-1", {"result": {"value": "hunter"}})])
        self.assertEqual(app.composer.text, "skip tests")  # back once the question closed

    def test_an_agent_steer_refused_while_a_clarify_question_is_open_stays_out_of_the_answer(self) -> None:
        clock = Clock()
        app, client, steer = self.agent_steered(clock)
        app._on_server_request(ServerRequest("srq-1", "clarify", {"questions": [{"qid": "q0", "question": "Which branch?"}]}))
        clock.now += SLOT_ARM_SECONDS
        type_text(app, "main")
        app._on_response(Response(steer, None, {"code": 4009, "message": "no tool call left."}))
        self.assertEqual(app.composer.text, "main")
        app._on_input(Key(ENTER))
        self.assertEqual(client.answers, [("srq-1", {"result": {"answers": {"q0": "main"}}})])
        self.assertEqual(app.composer.text, "skip tests")
        held = [line for line in lines_text(app.transcript.live_lines(140, THEME)) if "steer" in line]
        self.assertEqual(
            held, ["⧗ back in the composer — Hermes did not take the steer (no tool call left) · find usages of: skip tests"]
        )


class CommandPlacementTests(unittest.TestCase):
    """A command typed while a tool runs is held under it, as a steer is, and never cuts the running call off."""

    COMMANDS = ("/details", "/effort high", "/keys", "/todos", "/model", "/queue", "/steer")  # bare /model, /steer: no echo

    def mid_tool(self, command: str) -> tuple[object, object]:
        app, client = make_app(busy=True)
        app._on_event(ev("message.start"))
        app._on_event(ev("tool.start", tool_id="t1", name="terminal", context="pytest -q"))
        type_text(app, command)
        app._on_input(Key(ENTER))
        if app.pager is not None:  # /keys: the key list, left with q
            app._on_input(Key(CHAR, "q"))
        return app, client

    def test_every_command_mid_tool_leaves_one_call_and_the_done_line_counts_it_once(self) -> None:
        for command in self.COMMANDS:
            with self.subTest(command=command):
                app, _client = self.mid_tool(command)
                group = app.transcript.blocks[-1]
                self.assertIsInstance(group, ToolGroup)
                self.assertFalse(group.done)  # still the live block
                app._on_event(ev("tool.complete", tool_id="t1", name="terminal", result={"output": "1 passed"}))
                app._on_event(ev("message.complete", text="done", status="complete"))
                calls = [(tool.name, tool.status) for block in app.transcript.blocks
                         if isinstance(block, ToolGroup) for tool in block.tools]
                self.assertEqual(calls, [("terminal", TOOL_OK)])
                summaries = [block.tools for block in app.transcript.blocks if isinstance(block, TurnSummaryBlock)]
                self.assertEqual(summaries, [1])

    def test_details_mid_turn_reaches_the_running_call(self) -> None:
        app, _client = self.mid_tool("/details")
        self.assertTrue(app.transcript.details_expanded)
        self.assertIs(type(app.transcript.blocks[-1]), ToolGroup)
        self.assertFalse(app.transcript.blocks[-1].done)

    def test_the_echo_and_its_output_wait_under_the_call_then_land_after_its_group(self) -> None:
        app, _client = self.mid_tool("/queue")
        live = lines_text(app.transcript.live_lines(80, THEME))
        self.assertEqual(live[-2:], ["› /queue", "  ⎿  Nothing is queued."])  # under what runs, its output under it
        app._on_event(ev("tool.complete", tool_id="t1", name="terminal", result={"output": "1 passed"}))
        app._on_event(ev("message.delta", text="All green."))
        kinds = [type(block).__name__ for block in app.transcript.blocks]
        self.assertEqual(kinds, ["ToolGroup", "CommandBlock", "AssistantBlock"])
        self.assertEqual(app.transcript.blocks[1].parts, [("Nothing is queued.", "info")])

    def test_between_turns_a_command_is_written_at_once(self) -> None:
        app, _client = make_app()
        type_text(app, "/queue")
        app._on_input(Key(ENTER))
        (block,) = app.transcript.blocks
        self.assertEqual((block.command, block.done), ("/queue", True))

    def test_an_echo_still_waiting_for_hermes_stays_held_past_a_boundary_and_is_written_once(self) -> None:
        app, client = self.mid_tool("/usage")
        app._on_event(ev("tool.complete", tool_id="t1", name="terminal", result={"output": "1 passed"}))
        app._on_event(ev("message.delta", text="Checking the cost."))  # a boundary while /usage is still out
        self.assertNotIn("CommandBlock", [type(block).__name__ for block in app.transcript.blocks])
        answer(app, client, {"output": "Session cost: $0.01"})
        app._on_event(ev("message.complete", text="Checking the cost.", status="complete"))
        echoes = [block for block in app.transcript.blocks if type(block).__name__ == "CommandBlock"]
        self.assertEqual([(block.command, block.parts) for block in echoes], [("/usage", [("Session cost: $0.01", "info")])])

    def test_a_commands_call_that_expires_mid_tool_says_so_under_its_echo_and_the_call_runs_on(self) -> None:
        clock = Clock()
        app, _client = make_app(busy=True, clock=clock)
        app._on_event(ev("message.start"))
        app._on_event(ev("tool.start", tool_id="t1", name="terminal", context="pytest -q"))
        type_text(app, "/usage")
        app._on_input(Key(ENTER))
        clock.now += 3600
        app._expire_calls()
        group = app.transcript.blocks[-1]
        self.assertIsInstance(group, ToolGroup)
        self.assertEqual(group.tools[0].status, TOOL_RUNNING)
        self.assertEqual(lines_text(app.transcript.live_lines(140, THEME))[-2:],
                         ["› /usage", "  ⎿  ⚠ Hermes has not answered slash.exec in 120 s; the OMH TUI stopped waiting for it."])

    def usage_out_past_the_turn(self) -> tuple[object, object, int]:
        """`/usage` typed over a running tool, the turn ended with it still out, and the next prompt streaming."""
        app, client = self.mid_tool("/usage")
        usage = len(client.calls)
        app._on_event(ev("tool.complete", tool_id="t1", name="terminal", result={"output": "1 passed"}))
        app._on_event(ev("message.complete", text="done", status="complete"))
        type_text(app, "next prompt")
        app._on_input(Key(ENTER))
        app._on_event(ev("message.start"))
        app._on_event(ev("message.delta", text="Second reply "))
        return app, client, usage

    def test_a_command_still_out_at_the_turns_end_is_written_above_that_turns_done_line(self) -> None:
        app, client, usage = self.usage_out_past_the_turn()
        kinds = [type(block).__name__ for block in app.transcript.blocks]
        self.assertEqual(kinds[:4], ["ToolGroup", "AssistantBlock", "CommandBlock", "TurnSummaryBlock"])
        self.assertEqual(app.transcript.blocks[2].command, "/usage")  # dated in the turn it was typed in
        app._on_response(Response(usage, {"output": "Session cost: $0.01"}, None))
        app._on_event(ev("message.complete", text="Second reply done.", status="complete"))
        replies = [block.text for block in app.transcript.blocks if isinstance(block, AssistantBlock)]
        self.assertEqual(replies, ["done", "Second reply done."])  # the late output did not split the reply
        echoes = [(block.command, block.parts) for block in app.transcript.blocks if isinstance(block, CommandBlock)]
        self.assertEqual(echoes, [("/usage", []), ("/usage", [("Session cost: $0.01", "info")])])
        kinds = [type(block).__name__ for block in app.transcript.blocks]
        self.assertEqual(kinds[-3:], ["AssistantBlock", "CommandBlock", "TurnSummaryBlock"])  # under a repeat echo

    def test_a_refusal_that_comes_after_the_turns_end_goes_under_a_repeat_echo_and_splits_no_reply(self) -> None:
        app, client, usage = self.usage_out_past_the_turn()
        app._on_response(Response(usage, None, {"code": 5000, "message": "usage is unavailable"}))
        app._on_event(ev("message.delta", text="goes on."))
        replies = [block.text for block in app.transcript.blocks if isinstance(block, AssistantBlock)]
        self.assertEqual(replies, ["done", "Second reply goes on."])
        held = lines_text(app.transcript.live_lines(100, THEME))
        self.assertEqual(held[-2:], ["› /usage", "  ⎿  ✗ Usage is unavailable."])

    def test_output_for_an_echo_already_closed_waits_under_a_running_call(self) -> None:
        app, client = make_app()
        type_text(app, "/usage")
        app._on_input(Key(ENTER))
        type_text(app, "run the tests")
        app._on_input(Key(ENTER))  # the prompt closes the echo; /usage is still out
        app._on_event(ev("message.start"))
        app._on_event(ev("tool.start", tool_id="t1", name="terminal", context="pytest -q"))
        app._on_response(Response(1, {"output": "Session cost: $0.01"}, None))
        group = app.transcript.blocks[-1]
        self.assertIsInstance(group, ToolGroup)
        self.assertEqual(group.tools[0].status, TOOL_RUNNING)  # the repeated echo did not cut the call off
        self.assertEqual(lines_text(app.transcript.live_lines(80, THEME))[-2:], ["› /usage", "  ⎿  Session cost: $0.01"])


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

    def test_a_turn_without_tools_says_nothing_of_tools(self) -> None:
        (row,) = block_rows(TurnSummaryBlock(2, 0, 40), 30, THEME)
        self.assertEqual(line_text(row).split("  ")[0], "✻ 2s · ↓40")

    def test_a_narrow_terminal_keeps_the_facts_and_drops_the_rule(self) -> None:
        (row,) = block_rows(TurnSummaryBlock(34, 3, 1200), 24, THEME)
        self.assertEqual(line_text(row), "✻ 34s · 3 tools · ↓1.2k")


class FinishedPlanTests(unittest.TestCase):
    """A plan whose every item is done leaves the panel once no turn runs, as Claude Code's list goes once done."""

    OPEN = phased_plan(3, phases=1, active=2)
    DONE = [dict(item, state="done") for item in phased_plan(3, phases=1, active=2)]

    def setUp(self) -> None:
        self.app, _client = make_app(rows=30, cols=100)
        self.app.stored_id = "stored"

    def take(self, items: list[dict]) -> None:
        """A HUD read the poller hands over, as `_take_hud` takes it."""
        snapshot = HudSnapshot("stored", hud_payload(items), 100.0)
        self.app.poller.take = lambda: snapshot  # type: ignore[method-assign]
        self.app._take_hud()

    def headers(self) -> list[str]:
        return [row for row in drawn(self.app) if row.startswith("[Plan]")]

    def notices(self) -> list[str]:
        return [block.text for block in self.app.transcript.blocks if isinstance(block, NoticeBlock)]

    def turn(self, prompt: str) -> None:
        self.app._submit_prompt(prompt)
        self.app._on_event(ev("message.start"))

    def test_the_turn_that_finishes_the_plan_ends_with_it_in_the_transcript_and_off_the_panel(self) -> None:
        self.take(self.OPEN)
        self.turn("finish the plan")
        self.assertEqual(self.headers(), ["[Plan] Plan │ 2/3"])
        self.take(self.DONE)
        self.assertEqual(self.headers(), ["[Plan] Plan │ ✓ 3/3"])  # still the turn's: it shows done until the turn ends
        self.app._on_event(ev("message.complete", text="Done.", status="complete"))
        self.assertEqual(self.headers(), [])
        # The retired header reads as the live one did a moment before (review B NIT 3).
        self.assertEqual(self.notices(), ["\n".join(["[Plan] Plan │ ✓ 3/3", "Phase 0", "  [✓] Task number 0",
                                                     "  [✓] Task number 1", "  [✓] Task number 2"])])
        kinds = [type(block).__name__ for block in self.app.transcript.blocks]
        self.assertLess(kinds.index("NoticeBlock"), kinds.index("TurnSummaryBlock"))  # above the done line
        self.turn("something unrelated")
        self.take(self.DONE)  # the next poll still reads the finished plan
        self.assertEqual(self.headers(), [])
        self.assertEqual(len(self.notices()), 1)

    def test_a_plan_already_finished_when_the_session_opens_is_not_drawn_and_says_nothing(self) -> None:
        self.take(self.DONE)
        self.assertEqual((self.headers(), self.notices()), ([], []))

    def test_a_plan_with_an_item_left_stays_between_turns_and_a_new_plan_is_drawn_again(self) -> None:
        self.take(self.OPEN)
        self.turn("start it")
        self.app._on_event(ev("message.complete", text="Later.", status="complete"))
        self.assertEqual(self.headers(), ["[Plan] Plan │ 2/3"])
        self.take(self.DONE)  # finished between turns, by a poll
        self.assertEqual(self.headers(), [])
        self.take(phased_plan(4, phases=1, active=0))
        self.assertEqual(self.headers(), ["[Plan] Plan │ 0/4"])


class SettledTailTests(unittest.TestCase):
    """The rows a settled tool group gives back are filled by its own lines, never left blank under the done line."""

    def app_with_plan(self, rows: int, cols: int, earlier: int) -> tuple[object, list[float]]:
        now = [100.0]
        app, _client = make_app(rows=rows, cols=cols, clock=lambda: now[0])
        app.stored_id = "stored"
        app.hud = HudSnapshot("stored", hud_payload(phased_plan(4, phases=1, active=1)), now[0])
        for index in range(earlier):  # transcript rows above the region before the turn
            app.transcript.add_notice(f"earlier line {index}")
        app._render()
        return app, now

    def test_the_done_line_sits_one_spacer_row_above_the_panel_however_tall_the_last_group_was(self) -> None:
        for tools in (1, 4, 12):
            with self.subTest(tools=tools):
                app, now = self.app_with_plan(40, 100, earlier=60)  # a screen already full of transcript
                app._submit_prompt("run the tools")
                app._render()
                app._on_event(ev("message.start"))
                for index in range(tools):
                    now[0] += 0.1
                    app._on_event(ev("tool.start", tool_id=f"t{index}", name="read_file", context=f"f{index}.py"))
                    app._render()
                for index in range(tools):
                    app._on_event(ev("tool.complete", tool_id=f"t{index}", name="read_file", result={"content": "x"}))
                    app._render()
                app._on_event(ev("message.delta", text="All read."))
                app._render()
                app._on_event(ev("message.complete", text="All read.", status="complete"))
                app._render()
                screen = app.terminal.screen.lines()
                plan = next(index for index, line in enumerate(screen) if "[Plan]" in line)
                # the spacer row every block opens with, then the panel
                self.assertEqual(screen[plan - 1], "", "\n".join(screen[plan - 4 : plan + 1]))
                self.assertTrue(screen[plan - 2].startswith("✻ "), "\n".join(screen[plan - 4 : plan + 1]))

    def test_the_prompt_and_its_sticky_echo_are_never_on_screen_together(self) -> None:
        # A reply streams line by line under the prompt: the prompt shows until it scrolls off, the echo after.
        for rows, cols, earlier in ((24, 80, 0), (24, 80, 60), (40, 100, 0), (40, 100, 60)):
            with self.subTest(rows=rows, cols=cols, earlier=earlier):
                app, now = self.app_with_plan(rows, cols, earlier)
                app._submit_prompt("run the tools")
                app._render()
                app._on_event(ev("message.start"))
                app._render()
                echoed = 0
                for index in range(rows * 2):
                    now[0] += 0.05
                    app._on_event(ev("message.delta", text=f"line {index}\n"))
                    app._render()
                    screen = app.terminal.screen.lines()
                    prompt = any(line.startswith("› run the tools") for line in screen)
                    echo = any(line.startswith("↳ you: run the tools") for line in screen)
                    self.assertFalse(prompt and echo, f"line {index}: the prompt and its echo are both on screen")
                    self.assertTrue(prompt or echo, f"line {index}: the prompt scrolled off and nothing echoes it")
                    echoed += echo
                self.assertGreater(echoed, 0, "the prompt never scrolled off")

    def assert_prompt_xor_echo(self, app: object, where: str) -> bool:
        """Exactly one of the prompt and its sticky echo is on screen; True when it is the echo."""
        screen = app.terminal.screen.lines()  # type: ignore[attr-defined]
        prompt = any(line.startswith("› run the tools") for line in screen)
        echo = any(line.startswith("↳ you: run the tools") for line in screen)
        self.assertFalse(prompt and echo, f"{where}: the prompt and its echo are both on screen\n" + "\n".join(screen))
        self.assertTrue(prompt or echo, f"{where}: the prompt scrolled off and nothing echoes it")
        return echo

    def test_a_tall_tool_batch_never_shows_the_prompt_and_its_echo_together(self) -> None:
        # The blank row above the prompt is not the prompt's: counted, the echo came one row early, over the prompt.
        shapes = ((24, 80, 60, 8), (24, 80, 60, 14), (24, 80, 0, 14), (40, 100, 60, 14), (24, 80, 0, 2), (40, 100, 0, 2),
                  (40, 100, 0, 8))
        for rows, cols, earlier, tools in shapes:
            with self.subTest(rows=rows, cols=cols, earlier=earlier, tools=tools):
                app, now = self.app_with_plan(rows, cols, earlier)
                app._submit_prompt("run the tools")
                app._render()
                app._on_event(ev("message.start"))
                app._render()
                for index in range(tools):
                    now[0] += 0.1
                    app._on_event(ev("tool.start", tool_id=f"t{index}", name="read_file", context=f"f{index}.py"))
                    app._render()
                    self.assert_prompt_xor_echo(app, f"tool {index} started")
                for index in range(tools):
                    app._on_event(ev("tool.complete", tool_id=f"t{index}", name="read_file", result={"content": "x"}))
                    app._render()
                    self.assert_prompt_xor_echo(app, f"tool {index} completed")

    def test_the_frame_and_its_flush_are_one_write_so_no_state_between_them_shows(self) -> None:
        # Written apart, the frame (echo drawn) showed for a moment over the prompt the flush had not yet moved.
        from omh.tui.screen import SYNC_BEGIN

        for rows, cols, earlier, tools in ((24, 80, 0, 14), (24, 80, 60, 8), (40, 100, 0, 14)):
            with self.subTest(rows=rows, cols=cols, earlier=earlier, tools=tools):
                app, now = self.app_with_plan(rows, cols, earlier)
                app._submit_prompt("run the tools")
                app._render()
                screen = app.terminal.screen
                feed = screen.feed
                synced: list[int] = []

                def checked(text: str) -> None:  # every write reaches the screen as one feed
                    feed(text)
                    synced[-1] += text.count(SYNC_BEGIN)
                    self.assert_prompt_xor_echo(app, "after one write")

                screen.feed = checked  # type: ignore[method-assign]
                events = [ev("message.start")]
                events += [ev("tool.start", tool_id=f"t{n}", name="read_file", context=f"f{n}.py") for n in range(tools)]
                events += [ev("tool.complete", tool_id=f"t{n}", name="read_file", result={"content": "x"}) for n in range(tools)]
                events += [ev("message.delta", text=f"reply {n}\n") for n in range(rows)]  # the group settles at once
                for event in events:
                    now[0] += 0.1
                    app._on_event(event)
                    synced.append(0)
                    app._render()
                self.assertLessEqual(max(synced), 1, "a render wrote more than one synchronized update")
                self.assertIn(1, synced)

    def test_a_resize_redraw_mid_turn_never_shows_the_prompt_and_its_echo_together(self) -> None:
        # The redraw takes the session again from its top: only the rows from the prompt on are the prompt's,
        # and the banner it prints again ahead of them is not.
        cases = ((40, 100, 0, False), (40, 100, 60, False), (24, 80, 0, False), (24, 80, 60, False), (24, 80, 0, True))
        for rows, cols, earlier, banner in cases:
            with self.subTest(rows=rows, cols=cols, earlier=earlier, banner=banner):
                app, now = self.app_with_plan(rows, cols, earlier)
                if banner:
                    app.banner_record = {"cwd": "~/project", "resumed": ""}
                app._submit_prompt("run the tools")
                app._render()
                app._on_event(ev("message.start"))
                app._render()
                for index in range(2):
                    now[0] += 0.05
                    app._on_event(ev("message.delta", text=f"line {index}\n"))
                    app._render()
                app.terminal.screen.resize(rows, cols - 10)  # type: ignore[attr-defined]
                app._on_resize()
                now[0] += 1.0
                app._render_when_due()
                self.assert_prompt_xor_echo(app, "after the resize redraw")
                echoed = False
                for index in range(2, rows * 2):
                    now[0] += 0.05
                    app._on_event(ev("message.delta", text=f"line {index}\n"))
                    app._render()
                    echoed = self.assert_prompt_xor_echo(app, f"line {index} after the redraw") or echoed
                self.assertTrue(echoed, "the prompt never scrolled off after the redraw")


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



class NewSessionSpacerTests(unittest.TestCase):
    def test_the_new_sessions_first_line_is_set_off_from_the_last_session_by_a_spacer(self) -> None:
        # Review B NIT 8: `· New session <id>.` sat right under the previous session's done line.
        from test_tui_modes import drawn, make_app

        app, _client = make_app(rows=24, cols=80)
        app._on_input(Key(CHAR, "l", ctrl=True))
        app._submit_prompt("hello")
        app._on_event(ev("message.start"))
        app._on_event(ev("message.complete", text="Hi.", status="complete"))
        drawn(app)
        app._on_new_session({"session_id": "live-2", "stored_session_id": "stored-2"})
        screen = drawn(app)
        row = next(index for index, line in enumerate(screen) if "New session stored-2." in line)
        self.assertEqual(screen[row - 1], "")
        self.assertTrue(screen[row - 2].startswith("✻"), screen)  # the done line, one blank row above
        app._full_redraw()  # a redraw starts the new session's transcript at the top: no spacer over it
        self.assertTrue(drawn(app)[0].strip())


if __name__ == "__main__":
    unittest.main()
