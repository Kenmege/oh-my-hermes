"""Clicks on the agent strip and the agent list, and when the OMH TUI asks the terminal for mouse reports at all.

Where a click lands is never taken from the app. Each case reads the VT
screen the scripted terminal painted (`drawn`), finds the row and the cells
the thing to click is shown on, and sends a report for that cell the way the
terminal counts it: columns and rows from 1. What the click did is then read
from the app. A click "one off" is a report for the neighbouring cell or row.

The app works out which screen row its live region ends on and reads no
click while that row is not certain. A scripted terminal starts the way a
real launch does, with the app not told the cursor row, so most cases first
press Ctrl+L (`settled`): the redraw clears the screen and homes the cursor,
and from there the row is known. `UnknownRowTests` covers the launch itself.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from _tui_gateway_replay import Scenario
from test_tui_agent_list import INTERRUPTED_PROMPT, delegating_scenario, focused, more_agents, under_the_frame
from test_tui_agent_strip import STARTS, agent_app, relay
from test_tui_app import LIVE_ID, NO_TERMIOS, AppTestCase, FakePoller, ScriptedTerminal, Step, status_index
from test_tui_modes import SID, RecordingClient, drawn, make_app, show_mode, type_text
from test_tui_subagents import FIRST, FRAMES, SECOND
from omh.tui import keys as keys_module
from omh.tui import terminal as terminal_module
from omh.tui.app import (
    AGENT_LIST_HINT,
    CURSOR_POSITION_REQUEST,
    EXIT_DRAIN_SECONDS,
    READ_TIMEOUT_SECONDS,
    RESIZE_SETTLE_SECONDS,
    SLOT_ARM_SECONDS,
    App,
)
from omh.tui.frame import MODE_ACCEPT, MODE_PLAN
from omh.tui.gateway import Event, ServerRequest
from omh.tui.keys import (
    BUTTON_LEFT,
    BUTTON_MIDDLE,
    BUTTON_RIGHT,
    CHAR,
    DOWN,
    ENTER,
    ESCAPE,
    NO_BUTTON,
    WHEEL_DOWN,
    WHEEL_UP,
    Key,
    KeyDecoder,
    Mouse,
)
from omh.tui.pager import ALT_SCREEN_OFF
from omh.tui.session_picker import SessionPicker, SessionRow

MOUSE_ON = terminal_module.MOUSE_REPORTING_ON
MOUSE_OFF = terminal_module.MOUSE_REPORTING_OFF
CTRL_L = Key(CHAR, "l", ctrl=True)


def settled(app: App) -> App:
    """Ctrl+L: the screen is cleared and redrawn from the top row, so the app knows where its region is."""
    app._on_input(CTRL_L)
    drawn(app)
    return app


def reporting(app: App) -> bool:
    """Whether mouse reports are on after one repaint."""
    drawn(app)
    return app.terminal.mouse_reporting  # type: ignore[attr-defined]


def mouse_calls(app: App) -> list[bool]:
    return app.terminal.mouse_calls  # type: ignore[attr-defined]


def row_of(app: App, text: str) -> int:
    """The 1-based screen row whose text contains `text`, as a mouse report counts rows."""
    screen = drawn(app)
    (row,) = [index for index, line in enumerate(screen) if text in line]
    return row + 1


def column_of(app: App, text: str) -> int:
    """The 1-based column of the first cell of `text` on the screen (the strip's rows hold no wide glyph)."""
    (line,) = [line for line in drawn(app) if text in line]
    return line.index(text) + 1


def click(app: App, x: int, y: int) -> None:
    app._on_input(Mouse(BUTTON_LEFT, x, y))


def state(app: App, client: RecordingClient) -> tuple[object, ...]:
    """Everything a mouse report could change."""
    return (
        app.composer.text,
        app.pager is not None,
        app.log_agent,
        app.list_selected,
        len(client.calls),
        len(client.answers),
        len(app.transcript.blocks),
        app.exit_code,
        app.mode,
    )


def running(**options: object) -> tuple[App, RecordingClient]:
    """A turn running with the two recorded agents on the strip, the region's row known."""
    app, client = agent_app(**options)
    return settled(app), client


class MouseStateTests(unittest.TestCase):
    def test_reports_are_on_only_while_there_is_an_agent_to_click(self) -> None:
        now = [100.0]
        app, client = make_app(clock=lambda: now[0])
        settled(app)
        steps: list[tuple[str, bool]] = []

        def note(what: str) -> None:
            steps.append((what, reporting(app)))

        note("idle, no agent")
        app.turn_pending = True
        note("a turn running, no agent")
        relay(app, STARTS)
        note("a turn running, agents on the strip")
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        note("an approval open")
        now[0] += SLOT_ARM_SECONDS
        app._on_input(Key(CHAR, "d"))
        note("the approval denied, the turn still running")
        app._on_input(Key(DOWN))
        note("the list focused")
        app._on_input(Key(ENTER))
        note("an agent's log open")
        app._on_input(Key(CHAR, "q"))
        note("back in the list")
        app._on_input(Key(ESCAPE))
        note("back on the strip, the turn still running")
        relay(app, FRAMES[2:])
        app._on_event(Event("message.complete", SID, {"text": "done"}))
        note("the turn over, the finished strip still shown")
        app._on_input(Key(DOWN))
        note("the list focused while idle")
        app._on_input(Key(ESCAPE))
        note("the list left while idle")
        type_text(app, "next")
        app._on_input(Key(ENTER))
        note("the next turn, the roster empty")
        self.assertEqual(
            steps,
            [
                ("idle, no agent", False),
                ("a turn running, no agent", False),
                ("a turn running, agents on the strip", True),
                ("an approval open", False),
                ("the approval denied, the turn still running", True),
                ("the list focused", True),
                ("an agent's log open", False),
                ("back in the list", True),
                ("back on the strip, the turn still running", True),
                ("the turn over, the finished strip still shown", False),
                ("the list focused while idle", True),
                ("the list left while idle", False),
                ("the next turn, the roster empty", False),
            ],
        )
        self.assertEqual(mouse_calls(app), [True, False, True, False, True, False, True, False])
        self.assertEqual(client.answers, [("srq-1", {"result": {"choice": "deny"}})])

    def test_the_switch_is_written_only_when_the_state_changes(self) -> None:
        app, _client = running()
        for _ in range(5):
            drawn(app)
        written = "".join(app.terminal.writes)  # type: ignore[attr-defined]
        self.assertEqual((written.count(MOUSE_ON), written.count(MOUSE_OFF)), (1, 0))
        # Clicks and the wheel, in SGR form; never the modes that report motion.
        self.assertEqual(MOUSE_ON, "\x1b[?1000h\x1b[?1006h")
        for mode in ("?1002h", "?1003h", "?1015h"):
            self.assertNotIn(mode, written)

    def test_no_reports_below_ten_rows(self) -> None:
        for rows, on in ((6, False), (9, False), (10, True), (24, True)):
            with self.subTest(rows=rows):
                app, _client = running(rows=rows)
                app._on_input(Key(DOWN))
                self.assertEqual(reporting(app), on)
                self.assertEqual(mouse_calls(app), [True] if on else [])

    def test_a_strip_that_names_no_agent_has_nothing_to_click_but_the_list_does(self) -> None:
        app, _client = running(cols=40)
        self.assertIn("● 2 agents ↓", "\n".join(drawn(app)))
        self.assertFalse(reporting(app))
        app._on_input(Key(DOWN))
        self.assertTrue(reporting(app))

    def test_a_pager_that_is_no_agents_log_turns_them_off_too(self) -> None:
        app, _client = running()
        self.assertTrue(reporting(app))
        app._on_input(Key(CHAR, "?"))
        self.assertFalse(reporting(app))
        app._on_input(Key(CHAR, "q"))
        self.assertTrue(reporting(app))

    def test_a_question_answered_with_text_turns_them_off(self) -> None:
        app, _client = running()
        app._on_server_request(ServerRequest("srq-1", "vault.code", {}))
        self.assertFalse(reporting(app))

    def test_the_session_picker_leaves_the_running_strip_clickable(self) -> None:
        app, _client = running()
        app.picker = SessionPicker([SessionRow("stored-1", "a title", "", 0.0, 1)], current="")
        self.assertTrue(reporting(app))

    def test_the_model_chain_picker_runs_with_them_off_and_the_strip_gets_them_back(self) -> None:
        during: list[bool] = []

        def pick(environ: object, theme: object, *, read_key: object, write: object, width: int) -> str:
            during.append(app.terminal.mouse_reporting)  # type: ignore[attr-defined]
            return "Cancelled; nothing was changed."

        app, _client = running(pick_model_chains=pick)
        self.assertTrue(reporting(app))
        type_text(app, "/omh-model")
        app._on_input(Key(ENTER))
        self.assertEqual(during, [False])
        self.assertTrue(reporting(app))
        self.assertEqual(mouse_calls(app), [True, False, True])

    def test_shutdown_turns_them_off_before_the_screen_is_handed_back(self) -> None:
        app, _client = running()
        self.assertTrue(reporting(app))
        app._shutdown()
        self.assertFalse(app.terminal.mouse_reporting)  # type: ignore[attr-defined]
        writes = app.terminal.writes  # type: ignore[attr-defined]
        self.assertEqual("".join(writes).count(MOUSE_OFF), 1)

    def test_shutdown_from_an_open_log_leaves_them_off(self) -> None:
        app, _client = running()
        app._on_input(Key(DOWN))
        app._on_input(Key(ENTER))
        self.assertFalse(reporting(app))
        app._shutdown()
        self.assertEqual(mouse_calls(app), [True, False])
        self.assertIn(ALT_SCREEN_OFF, "".join(app.terminal.writes))  # type: ignore[attr-defined]


class StripClickTests(unittest.TestCase):
    def test_a_click_on_an_agents_cells_opens_that_agents_log(self) -> None:
        expected = {"find usages of": FIRST, "read the diff": SECOND}
        for name, subagent_id in expected.items():
            app, client = running()
            y = row_of(app, "● main  ")
            first = column_of(app, name) - 2  # the spinner glyph and the space before the name
            for x in range(first, first + 2 + len(name)):
                with self.subTest(name=name, x=x):
                    click(app, x, y)
                    self.assertEqual(app.log_agent, subagent_id)
                    app._on_input(Key(CHAR, "q"))
                    self.assertEqual((app.pager, app.list_selected), (None, None))  # back at the composer
            self.assertEqual(client.calls, [])

    def test_one_column_off_either_side_opens_nothing(self) -> None:
        app, client = running()
        y = row_of(app, "● main  ")
        before = state(app, client)
        for name in ("find usages of", "read the diff"):
            first = column_of(app, name) - 2
            last = first + 1 + len(name)
            for x in (first - 1, last + 1):
                with self.subTest(name=name, x=x):
                    click(app, x, y)
                    self.assertEqual(state(app, client), before)

    def test_one_row_off_either_side_opens_nothing(self) -> None:
        app, client = running()
        y = row_of(app, "● main  ")
        x = column_of(app, "read the diff")
        before = state(app, client)
        screen = drawn(app)
        self.assertEqual(set(screen[y - 2]), {"─"})  # the row above is the composer's bottom rule
        self.assertIn("running", screen[y])  # the row below is the status line
        for off in (-1, 1, -2, 2):
            with self.subTest(off=off):
                click(app, x, y + off)
                self.assertEqual(state(app, client), before)
        click(app, x, y)
        self.assertEqual(app.log_agent, SECOND)

    def test_main_the_arrow_and_the_hints_open_nothing(self) -> None:
        app, client = running()
        y = row_of(app, "● main  ")
        before = state(app, client)
        for text in ("● main", "main", "↓", "esc interrupt"):
            with self.subTest(text=text):
                click(app, column_of(app, text), y)
                self.assertEqual(state(app, client), before)
        for x in (1, 80, 81, 500):
            click(app, x, y)
        self.assertEqual(state(app, client), before)

    def test_the_cells_follow_the_strip_when_a_mode_label_moves_it(self) -> None:
        for mode in (MODE_ACCEPT, MODE_PLAN):
            with self.subTest(mode=mode):
                app, _client = running(cols=120)
                show_mode(app, mode)
                y = row_of(app, "● main  ")
                x = column_of(app, "read the diff")
                self.assertGreater(x, 40)  # well right of where it sits in normal mode
                click(app, column_of(app, "find usages of") - 3, y)  # the gap before the first agent
                self.assertIsNone(app.pager)
                click(app, x, y)
                self.assertEqual(app.log_agent, SECOND)

    def test_a_report_decoded_from_the_terminals_bytes_opens_the_log(self) -> None:
        app, _client = running()
        y, x = row_of(app, "● main  "), column_of(app, "read the diff")
        for event in KeyDecoder().feed(f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m".encode("ascii")):
            app._on_input(event)
        self.assertEqual(app.log_agent, SECOND)

    def test_a_click_as_the_prompt_that_empties_the_roster_goes_out_opens_nothing(self) -> None:
        app, client = running(busy=False)
        relay(app, STARTS)
        app.turn_pending = True
        y, x = row_of(app, "● main  "), column_of(app, "read the diff")
        app.turn_pending = False
        type_text(app, "next")
        app._on_input(Key(ENTER))  # one read: the Enter, then a click on the strip it just cleared
        click(app, x, y)
        self.assertEqual((app.pager, app.log_agent), (None, ""))
        self.assertEqual(len(client.named("prompt.submit")), 1)


class ListClickTests(unittest.TestCase):
    def open(self, **options: object) -> tuple[App, RecordingClient]:
        app, client = focused(**options)
        return settled(app), client

    def test_a_click_on_an_agents_row_highlights_it_and_opens_its_log(self) -> None:
        for x in (1, 40, 80):
            with self.subTest(x=x):
                app, client = self.open()
                self.assertEqual(app.list_selected, FIRST)
                click(app, x, row_of(app, "read the diff for risks"))
                self.assertEqual((app.list_selected, app.log_agent), (SECOND, SECOND))
                app._on_input(Key(ESCAPE))
                self.assertEqual((app.pager, app.list_selected), (None, SECOND))  # back in the list, on that row
                self.assertEqual(under_the_frame(app)[-1], AGENT_LIST_HINT)
                self.assertEqual(client.calls, [])

    def test_a_click_on_main_leaves_the_list(self) -> None:
        app, client = self.open()
        click(app, 5, row_of(app, "  ● main"))
        self.assertEqual((app.list_selected, app.pager, client.calls), (None, None, []))
        self.assertEqual(len(under_the_frame(app)), 1)

    def test_the_hint_row_the_rule_above_the_list_and_the_status_line_open_nothing(self) -> None:
        app, client = self.open()
        before = state(app, client)
        top = row_of(app, "  ● main")
        for y in (top - 1, row_of(app, AGENT_LIST_HINT), row_of(app, "⚚ OMH"), top - 2, 1, 24, 25):
            with self.subTest(y=y):
                click(app, 10, y)
                self.assertEqual(state(app, client), before)
        click(app, 81, row_of(app, "read the diff for risks"))  # past the right edge
        self.assertEqual(state(app, client), before)

    def test_every_row_of_a_scrolled_list_opens_the_agent_it_shows(self) -> None:
        app, _client = agent_app()
        more_agents(app, 7)
        app._on_input(Key(DOWN))
        for _ in range(8):
            app._on_input(Key(DOWN))  # the last agent: main and the two recorded ones scrolled off
        settled(app)
        self.assertEqual([row.split("task ")[1][0] for row in under_the_frame(app)[:-1]], list("0123456"))
        # Each click moves the highlight, and the list scrolls to it; the next row is found on the new screen.
        for number in (6, 4, 0):
            with self.subTest(number=number):
                click(app, 10, row_of(app, f"task {number}"))
                self.assertEqual(app.log_agent, f"sa-extra-{number}")
                app._on_input(Key(CHAR, "q"))
                self.assertEqual(app.list_selected, f"sa-extra-{number}")
        self.assertEqual(under_the_frame(app)[0], "  ● main")  # scrolled back up to the highlight on task 0
        click(app, 10, row_of(app, "read the diff for risks"))
        self.assertEqual(app.log_agent, SECOND)

    def test_the_rows_of_a_short_terminals_list_are_the_ones_clicked(self) -> None:
        app, _client = agent_app(rows=10)
        app._on_input(Key(DOWN))
        settled(app)
        click(app, 3, row_of(app, "read the diff for risks"))
        self.assertEqual(app.log_agent, SECOND)

    def test_while_the_list_is_shown_the_strip_is_not_there_to_click(self) -> None:
        app, client = running()
        y, x = row_of(app, "● main  "), column_of(app, "read the diff")
        app._on_input(Key(DOWN))
        self.assertEqual(row_of(app, AGENT_LIST_HINT), y)  # the hint row stands where the strip was
        click(app, x, y)
        self.assertEqual((app.pager, app.list_selected, client.calls), (None, FIRST, []))


class IgnoredReportTests(unittest.TestCase):
    def reports(self, x: int, y: int) -> dict[str, Mouse]:
        return {
            "a release": Mouse(BUTTON_LEFT, x, y, pressed=False),
            "a release naming no button": Mouse(NO_BUTTON, x, y, pressed=False),
            "motion with the button held": Mouse(BUTTON_LEFT, x, y, motion=True),
            "motion with none held": Mouse(NO_BUTTON, x, y, motion=True),
            "the wheel up": Mouse(WHEEL_UP, x, y),
            "the wheel down": Mouse(WHEEL_DOWN, x, y),
            "the middle button": Mouse(BUTTON_MIDDLE, x, y),
            "the right button": Mouse(BUTTON_RIGHT, x, y),
            "a shift click": Mouse(BUTTON_LEFT, x, y, shift=True),
            "an alt click": Mouse(BUTTON_LEFT, x, y, alt=True),
            "a ctrl click": Mouse(BUTTON_LEFT, x, y, ctrl=True),
        }

    def test_on_an_agents_cells_only_a_plain_left_press_does_anything(self) -> None:
        app, client = running()
        y, x = row_of(app, "● main  "), column_of(app, "read the diff")
        before = state(app, client)
        for name, report in self.reports(x, y).items():
            with self.subTest(name):
                app._on_input(report)
                self.assertEqual(state(app, client), before)
        app._on_input(Mouse(BUTTON_LEFT, x, y))
        self.assertEqual(app.log_agent, SECOND)

    def test_on_a_list_row_only_a_plain_left_press_does_anything(self) -> None:
        app, client = focused()
        settled(app)
        y = row_of(app, "read the diff for risks")
        before = state(app, client)
        for name, report in self.reports(10, y).items():
            with self.subTest(name):
                app._on_input(report)
                self.assertEqual(state(app, client), before)

    def test_the_same_reports_as_bytes_type_nothing_and_open_nothing(self) -> None:
        app, client = running()
        y, x = row_of(app, "● main  "), column_of(app, "read the diff")
        before = state(app, client)
        codes = (64, 65, 66, 67, 32, 35, 1, 2, 3, 4, 8, 16, 128)  # wheel, motion, other buttons, modifiers, an unnamed one
        data = "".join(f"\x1b[<{code};{x};{y}M" for code in codes) + f"\x1b[<0;{x};{y}m" + "\x1b[M !!"
        for event in KeyDecoder().feed(data.encode("ascii")):
            app._on_input(event)
        self.assertEqual(state(app, client), before)

    def test_a_report_in_the_legacy_encoding_does_not_close_the_list(self) -> None:
        # A terminal without SGR reports answers mode 1000 with `ESC[M` and three bytes, which decodes
        # to one key without a name. The list reads every other key as "leave".
        app, client = focused()
        settled(app)
        before = state(app, client)
        for event in KeyDecoder().feed(b"\x1b[M !!\x1b[M#!!"):  # a press and its release
            app._on_input(event)
        self.assertEqual(state(app, client), before)
        self.assertEqual(app.list_selected, FIRST)
        self.assertEqual(under_the_frame(app)[-1], AGENT_LIST_HINT)

    def test_a_click_opens_nothing_whenever_the_tui_did_not_ask_for_reports(self) -> None:
        def idle(app: App, now: list[float]) -> None:
            app.turn_pending = False  # the finished strip is still drawn

        def approval(app: App, now: list[float]) -> None:
            app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
            now[0] += SLOT_ARM_SECONDS

        def keys_pager(app: App, now: list[float]) -> None:
            app._on_input(Key(CHAR, "?"))

        def agent_log(app: App, now: list[float]) -> None:
            app._on_input(Key(DOWN))
            app._on_input(Key(ENTER))

        def resizing(app: App, now: list[float]) -> None:
            app._on_resize()  # the size has not settled: the old frame is still on screen, nothing is redrawn

        for into in (idle, approval, keys_pager, agent_log, resizing):
            with self.subTest(into.__name__):
                now = [100.0]
                app, client = running(clock=lambda: now[0])
                y, x = row_of(app, "● main  "), column_of(app, "read the diff")
                into(app, now)
                app._render_when_due()
                before = state(app, client)
                click(app, x, y)
                self.assertEqual(state(app, client), before)
                if into is not resizing:
                    self.assertFalse(app.terminal.mouse_reporting)  # type: ignore[attr-defined]

    def test_while_a_question_is_open_a_click_opens_nothing_before_any_frame_redraws_the_cells(self) -> None:
        # The question opens and the click comes in the same read: `clickable` still holds the last frame's cells.
        for where in ("strip", "list"):
            for method, params in (("approval", {"command": "rm -rf ./x"}), ("vault.code", {})):
                with self.subTest(where=where, method=method):
                    app, client = running()
                    if where == "list":
                        app._on_input(Key(DOWN))
                    y = row_of(app, "read the diff")
                    x = column_of(app, "read the diff")
                    self.assertTrue(app.clickable)
                    app._on_server_request(ServerRequest("srq-1", method, params))
                    self.assertTrue(app.clickable)  # no frame since: the cells are still there
                    click(app, x, y)
                    self.assertEqual((app.pager, app.log_agent, client.calls), (None, "", []))
                    self.assertIsNotNone(app.slot)

    def test_another_programs_leftover_reports_open_nothing_in_a_session_without_agents(self) -> None:
        app, client = make_app(busy=True)
        settled(app)
        before = state(app, client)
        for y in range(1, 25):
            for x in (1, 10, 40, 80):
                click(app, x, y)
        self.assertEqual(state(app, client), before)
        self.assertEqual(mouse_calls(app), [])


class UnknownRowTests(unittest.TestCase):
    """The region's place on the screen: read where it is certain, and no click read where it is not."""

    def test_at_launch_the_row_is_not_known_so_reports_stay_off_and_a_click_is_not_read(self) -> None:
        app, client = agent_app()  # no Ctrl+L: the app was never told which row the cursor started on
        y, x = row_of(app, "● main  "), column_of(app, "read the diff")
        self.assertIsNone(app.region.last_row(24))
        self.assertFalse(reporting(app))
        before = state(app, client)
        for off in (-1, 0, 1):
            click(app, x, y + off)
        self.assertEqual(state(app, client), before)
        self.assertEqual(mouse_calls(app), [])
        app._on_input(Key(DOWN))  # the keyboard needs no row: the list and the log still open
        app._on_input(Key(ENTER))
        self.assertEqual(app.log_agent, FIRST)

    def test_once_output_has_pushed_the_region_to_the_last_row_the_click_is_read(self) -> None:
        app, _client = agent_app()
        self.assertFalse(reporting(app))
        app.transcript.add_notice("\n".join(f"line {number}" for number in range(40)))  # more than the screen holds
        self.assertTrue(reporting(app))
        self.assertEqual(app.region.last_row(24), 23)
        y, x = row_of(app, "● main  "), column_of(app, "read the diff")
        self.assertEqual(y, 23)  # the row above the status line, which is on the screen's last row
        click(app, x, y)
        self.assertEqual(app.log_agent, SECOND)

    def test_after_a_redraw_the_region_ends_one_row_above_the_screens_last_and_clicks_follow_it(self) -> None:
        app, client = running()
        self.assertEqual(app.region.last_row(24), 22)
        screen = drawn(app)
        self.assertEqual(status_index(screen), 22)
        self.assertEqual(screen[23], "")  # the boundary case: the screen's last row is empty
        y, x = row_of(app, "● main  "), column_of(app, "read the diff")
        self.assertEqual(y, 22)
        before = state(app, client)
        click(app, x, 23)  # where the strip would be if the region ended on the last row: the status line
        self.assertEqual(state(app, client), before)
        click(app, x, y)
        self.assertEqual(app.log_agent, SECOND)

    def test_when_the_region_then_reaches_the_last_row_the_same_click_is_one_row_lower(self) -> None:
        app, client = running()
        x = column_of(app, "read the diff")
        self.assertEqual(row_of(app, "● main  "), 22)
        app.transcript.add_notice("\n".join(f"line {number}" for number in range(40)))
        self.assertEqual(row_of(app, "● main  "), 23)
        self.assertEqual(app.region.last_row(24), 23)
        before = state(app, client)
        click(app, x, 22)  # the strip's old row is the composer's bottom rule now
        self.assertEqual(state(app, client), before)
        click(app, x, 23)
        self.assertEqual(app.log_agent, SECOND)

    def test_a_redraw_behind_an_open_pager_does_not_claim_the_top_row(self) -> None:
        # The clear went to the alternate screen; the main screen's cursor is wherever the old frame left it.
        app, _client = agent_app()
        app._on_input(Key(CHAR, "?"))
        app._full_redraw()
        app._on_input(Key(CHAR, "q"))
        drawn(app)
        self.assertIsNone(app.region.last_row(24))
        self.assertFalse(app.terminal.mouse_reporting)  # type: ignore[attr-defined]


def step_through(app: App, now: list[float], reads: list[tuple[float, bytes]]) -> None:
    """Run the loop one step per read: each read takes its time on the app's clock, then hands back its bytes."""
    queue = list(reads)

    def read(timeout: float) -> bytes:
        delay, data = queue.pop(0)
        now[0] += delay
        return data

    app.terminal.read = read  # type: ignore[method-assign]
    app.client.poll = lambda: []  # type: ignore[attr-defined]
    while queue:
        app._step()


def paused(first: bytes, pause: float, rest: bytes) -> list[tuple[float, bytes]]:
    """`first`, then `pause` seconds of empty reads, then `rest`, then two seconds of quiet."""
    silences = int(pause / READ_TIMEOUT_SECONDS + 1e-9)
    quiet = [(READ_TIMEOUT_SECONDS, b"")]
    return [(0.0, first), *quiet * silences, (pause - silences * READ_TIMEOUT_SECONDS, rest), *quiet * 40]


class SplitReportLoopTests(unittest.TestCase):
    """The review's two splits of a wheel report, in the loop, while a running turn's strip is clickable."""

    def setUp(self) -> None:
        self.now = [100.0]
        self.app, self.client = running(clock=lambda: self.now[0])
        self.assertTrue(reporting(self.app))

    def test_a_report_split_after_its_marker_types_nothing_and_does_nothing(self) -> None:
        for pause in (0.06, 0.4, 0.6):
            with self.subTest(pause=pause):
                before = state(self.app, self.client)
                step_through(self.app, self.now, paused(b"\x1b[<65;12", pause, b";40M"))
                self.assertEqual(state(self.app, self.client), before)
                self.assertEqual(self.app.composer.text, "")

    def test_a_report_split_after_its_escape_types_nothing_and_within_300_ms_interrupts_nothing(self) -> None:
        before = state(self.app, self.client)
        step_through(self.app, self.now, paused(b"\x1b", 0.06, b"[<65;12;40M"))
        self.assertEqual(state(self.app, self.client), before)
        # Split longer than the escape-time, the ESC is Esc, as in tmux and vim; the rest is still dropped.
        step_through(self.app, self.now, paused(b"\x1b", 0.4, b"[<65;12;40M"))
        self.assertEqual(self.app.composer.text, "")
        self.assertEqual(self.client.named("session.interrupt"), [{"session_id": SID}])

    def test_esc_pressed_while_reports_are_on_interrupts_once_300_ms_have_passed(self) -> None:
        step_through(self.app, self.now, [(0.0, b"\x1b"), *[(READ_TIMEOUT_SECONDS, b"")] * 5])
        self.assertEqual(self.client.named("session.interrupt"), [])  # 250 ms: it may still be a report
        step_through(self.app, self.now, [(READ_TIMEOUT_SECONDS, b"")] * 2)
        self.assertEqual(self.client.named("session.interrupt"), [{"session_id": SID}])  # by 350 ms

    def test_with_reports_off_esc_interrupts_at_the_first_silence(self) -> None:
        now = [100.0]
        app, client = make_app(busy=True, clock=lambda: now[0])
        settled(app)
        self.assertFalse(reporting(app))
        step_through(app, now, [(0.0, b"\x1b"), (READ_TIMEOUT_SECONDS, b"")])
        self.assertEqual(client.named("session.interrupt"), [{"session_id": SID}])


class ResizeTests(unittest.TestCase):
    def test_reports_go_off_as_the_size_changes_and_come_back_with_the_settled_redraw(self) -> None:
        now = [100.0]
        app, _client = running(clock=lambda: now[0])
        self.assertTrue(reporting(app))
        app.terminal.screen.resize(24, 100)  # type: ignore[attr-defined]
        app._on_resize()
        self.assertFalse(app.terminal.mouse_reporting)  # type: ignore[attr-defined]  # before any frame
        now[0] += RESIZE_SETTLE_SECONDS / 2
        app._render_when_due()
        self.assertFalse(app.terminal.mouse_reporting)  # type: ignore[attr-defined]  # still settling
        now[0] += RESIZE_SETTLE_SECONDS
        app._render_when_due()
        self.assertTrue(app.terminal.mouse_reporting)  # type: ignore[attr-defined]
        self.assertEqual(mouse_calls(app), [True, False, True])

    def test_a_click_read_in_the_step_that_sees_the_size_change_is_dropped(self) -> None:
        for paging in (False, True):
            with self.subTest(paging=paging):
                now = [100.0]
                app, client = running(clock=lambda: now[0])
                y, x = row_of(app, "● main  "), column_of(app, "read the diff")
                data = f"\x1b[<0;{x};{y}M".encode("ascii")
                if paging:
                    app._on_input(Key(CHAR, "?"))
                    data = b"q" + data  # the key list left and the click, in one read
                else:
                    app.terminal.screen.resize(24, 100)  # type: ignore[attr-defined]  # the VT model resizes the main screen only
                app.terminal._resized = True  # type: ignore[attr-defined]
                step_through(app, now, [(0.0, data)])
                self.assertEqual((app.pager, app.log_agent, client.calls), (None, "", []))


class DrainTerminal(ScriptedTerminal):
    """A scripted terminal whose reads are a list on its own clock: each `read` takes the next entry, then `endless`.

    A read that brings bytes takes `READ_TAKES` of the clock; one that brings nothing waits its whole timeout.
    """

    READ_TAKES = 0.01

    def __init__(self, incoming: list[bytes | type[BaseException]], endless: bytes = b"") -> None:
        super().__init__()
        self.incoming = list(incoming)
        self.endless = endless
        self.now = [0.0]
        self.log: list[tuple[str, object]] = []  # ("write", text) and ("read", timeout), in order

    def write(self, text: str) -> None:
        self.log.append(("write", text))
        super().write(text)

    def read(self, timeout: float) -> bytes:
        self.log.append(("read", timeout))
        item = self.incoming.pop(0) if self.incoming else self.endless
        if not isinstance(item, bytes):
            raise item("the terminal hung up")
        self.now[0] += min(self.READ_TAKES, timeout) if item else timeout
        return item


class ExitDrainTests(unittest.TestCase):
    REPORT = b"\x1b[<0;30;22M\x1b[<0;30;22m"
    ANSWER = b"\x1b[24;1R"  # the terminal's answer to the cursor position request

    def leaving(self, terminal: DrainTerminal, *, with_reports: bool = True) -> tuple[App, RecordingClient]:
        client = RecordingClient()
        app = App(terminal, client, {}, poller=FakePoller(), clock=lambda: terminal.now[0])  # type: ignore[arg-type]
        app.sid = SID
        app.turn_pending = True
        if with_reports:
            relay(app, STARTS)
        settled(app)
        self.assertEqual(terminal.mouse_reporting, with_reports)
        terminal.log.clear()
        return app, client

    def reads(self, terminal: DrainTerminal) -> list[float]:
        return [timeout for kind, timeout in terminal.log if kind == "read"]  # type: ignore[misc]

    def writes(self, terminal: DrainTerminal) -> list[str]:
        return [str(what) for kind, what in terminal.log if kind == "write"]

    def test_input_is_dropped_up_to_the_answer_to_a_cursor_position_request_and_no_further(self) -> None:
        incoming: list[bytes | type[BaseException]] = [
            self.REPORT,
            self.REPORT + b"x",  # a key typed meanwhile goes with the reports: the TUI read it and acts on nothing
            b"\x1b[<0;31;22M" + self.ANSWER,
        ]
        terminal = DrainTerminal(incoming, endless=b"typed for the shell")
        app, client = self.leaving(terminal)
        before = state(app, client)
        app._shutdown()
        self.assertEqual(len(self.reads(terminal)), 3)  # it stopped the moment the answer came
        self.assertEqual(state(app, client), before)  # nothing read there was acted on
        self.assertEqual(CURSOR_POSITION_REQUEST, "\x1b[6n")
        writes = self.writes(terminal)
        self.assertEqual((writes[0], writes[-1]), (MOUSE_OFF, CURSOR_POSITION_REQUEST))  # asked after the off
        kinds = [kind for kind, _what in terminal.log]
        self.assertEqual(set(kinds[kinds.index("read") :]), {"read"})  # nothing is written once the drain began

    def test_an_answer_split_across_reads_ends_it_once_whole(self) -> None:
        terminal = DrainTerminal([self.REPORT + b"\x1b[2", b"4;", b"1R"], endless=self.REPORT)
        app, _client = self.leaving(terminal)
        app._shutdown()
        self.assertEqual(len(self.reads(terminal)), 3)

    def test_what_only_looks_like_the_answer_does_not_end_it(self) -> None:
        for lookalike in (b"\x1b[<0;24;1M", b"\x1b[24R", b"\x1b[;1R", b"\x1b[24;R", b"\x1b[1;5A", b"R;1"):
            with self.subTest(lookalike=lookalike):
                terminal = DrainTerminal([lookalike, self.ANSWER], endless=self.REPORT)
                app, _client = self.leaving(terminal)
                app._shutdown()
                self.assertEqual(len(self.reads(terminal)), 2)

    def test_a_terminal_that_never_answers_is_drained_for_a_quarter_second_at_most(self) -> None:
        terminal = DrainTerminal([], endless=self.REPORT)  # a terminal that never stops sending
        app, client = self.leaving(terminal)
        before = state(app, client)
        app._shutdown()
        self.assertEqual(EXIT_DRAIN_SECONDS, 0.25)
        self.assertAlmostEqual(terminal.now[0], EXIT_DRAIN_SECONDS)
        waits = self.reads(terminal)
        self.assertTrue(all(0 < wait <= EXIT_DRAIN_SECONDS for wait in waits))
        self.assertEqual(state(app, client), before)

    def test_a_read_that_waits_out_the_time_left_ends_it(self) -> None:
        terminal = DrainTerminal([self.REPORT])
        app, _client = self.leaving(terminal)
        app._shutdown()
        self.assertEqual(len(self.reads(terminal)), 2)
        self.assertAlmostEqual(terminal.now[0], EXIT_DRAIN_SECONDS)

    def test_a_terminal_that_hung_up_ends_it_quietly(self) -> None:
        terminal = DrainTerminal([self.REPORT, EOFError])
        app, _client = self.leaving(terminal)
        app._shutdown()
        self.assertEqual(len(self.reads(terminal)), 2)

    def test_nothing_is_asked_or_read_when_reports_were_never_on(self) -> None:
        terminal = DrainTerminal([], endless=b"typed ahead for the shell")
        app, _client = self.leaving(terminal, with_reports=False)
        app._shutdown()
        self.assertEqual(self.reads(terminal), [])
        written = "".join(self.writes(terminal))
        self.assertNotIn(MOUSE_OFF, written)
        self.assertNotIn(CURSOR_POSITION_REQUEST, written)

    def test_reports_on_earlier_in_the_run_are_drained_though_they_are_off_at_exit(self) -> None:
        terminal = DrainTerminal([self.REPORT, self.ANSWER], endless=b"typed for the shell")
        app, _client = self.leaving(terminal)
        app.turn_pending = False  # the turn ended: the strip is no longer clickable
        drawn(app)
        self.assertFalse(terminal.mouse_reporting)
        terminal.log.clear()
        app._shutdown()
        self.assertEqual(self.writes(terminal)[-1], CURSOR_POSITION_REQUEST)
        self.assertEqual(len(self.reads(terminal)), 2)

    def test_run_drains_on_its_way_out_however_the_loop_ended(self) -> None:
        terminal = DrainTerminal([SystemExit, self.REPORT, self.ANSWER])  # SIGTERM arrives as SystemExit in a read
        app, _client = self.leaving(terminal)
        with self.assertRaises(SystemExit):
            app.run()
        self.assertEqual(len(self.reads(terminal)), 3)
        self.assertFalse(terminal.mouse_reporting)


class DocumentedMouseTests(unittest.TestCase):
    """What INSTALLATION and the AGENTS.md boundary say about the mouse, held to the code they describe."""

    ROOT = Path(__file__).resolve().parents[1]

    def prose(self, name: str) -> str:
        return " ".join((self.ROOT / name).read_text(encoding="utf-8").split())

    def test_the_escape_time_the_docs_name_is_the_decoders(self) -> None:
        seconds = keys_module.REPORTING_ESCAPE_SECONDS
        self.assertIn(f"Esc acts after {seconds:g} s", self.prose("docs/INSTALLATION.md"))
        self.assertIn(f"more than {round(seconds * 1000)} ms before the rest is read as Esc", self.prose("AGENTS.md"))

    def test_the_way_to_select_anyway_is_named_for_each_terminal_no_more_surely_than_it_is_known(self) -> None:
        text = self.prose("docs/INSTALLATION.md")
        self.assertIn(
            "hold Fn while dragging in Terminal.app (or turn off View → Allow Mouse Reporting), Option in iTerm2, "
            "Shift in most other terminals",
            text,
        )
        self.assertNotIn("Option-drag in Terminal.app", text)

    def test_the_boundary_says_what_each_click_does(self) -> None:
        text = self.prose("AGENTS.md")
        self.assertIn("No report ever types. A plain left click on an agent opens its log, one on the list's `main` row "
                      "leaves the list, and every other report does nothing", text)
        self.assertNotIn("a report can open an agent's log and nothing else", text)


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class MouseReplayTests(AppTestCase):
    def scenario(self) -> Scenario:
        return delegating_scenario(("subagent.tail", {"result": {"subagent_id": SECOND, "available": False}}))

    def test_in_the_real_loop_a_click_on_the_strip_opens_that_agent_and_every_other_report_is_nothing(self) -> None:
        run = self.prepare(self.scenario())
        terminal = run.terminal
        x, y = 29, 22  # the r of "read the diff", on the row above the status line of a region redrawn from the top row
        ignored = b"".join(f"\x1b[<{code};{x};{y}M".encode("ascii") for code in (64, 65, 35, 2)) + (
            f"\x1b[<0;{x};{y}m\x1b[<0;{x};{y - 1}M\x1b[<0;{x};{y + 1}M\x1b[<0;25;{y}M".encode("ascii")
        )
        on = lambda: terminal.mouse_reporting  # noqa: E731
        terminal.script(
            self.submit(run, INTERRUPTED_PROMPT),
            Step(b"\x0c", when=terminal.shows("● main  "), capture="launched", label="the strip"),
            Step(ignored, when=lambda: on() and terminal.shows("● main  ")(), capture="redrawn", label="reports on"),
            Step(f"\x1b[<0;{x};{y}M".encode("ascii"), when=terminal.shows("● main  "), capture="ignored", label="ignored"),
            Step(b"q", when=terminal.shows("read the diff · ←/→ agents"), capture="log", label="the second agent's log"),
            Step(b"\x1b", when=lambda: on() and terminal.shows("● main  ")(), capture="back", label="the strip again"),
            Step(b"\x04", when=lambda: "Interrupted." in terminal.history() and terminal.idle(), capture="over", label="the end"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        captures = terminal.captures
        # The coordinates above are where the screen showed the thing clicked.
        redrawn = captures["redrawn"]
        self.assertEqual(status_index(redrawn), y)  # 0-based row 22: the status line, one row under the strip
        self.assertEqual(redrawn[y - 1][x - 1 : x + 12], "read the diff")
        self.assertEqual(redrawn[y - 1][24:26], "  ")  # column 25 is the gap between the two agents
        # Wheel, motion, a right press, a release and three near misses: still the main screen with the
        # strip where it was, and nothing typed. (The spinner and the activity row moved on meanwhile.)
        ignored = captures["ignored"]
        self.assertEqual(status_index(ignored), y)
        self.assertEqual(ignored[y - 1][x - 1 : x + 12], "read the diff")
        self.assertEqual(composer_text(ignored), "› Ask Hermes anything")
        self.assertEqual(captures["log"][0], "read the diff for risks")
        self.assertEqual(composer_text(captures["back"]), "› Ask Hermes anything")
        self.assertEqual(run.calls("subagent.tail")[0]["params"], {"session_id": LIVE_ID, "subagent_id": SECOND})
        # Off at launch (the row was not known), on after the redraw, off for the log, on again, off as the turn ended.
        self.assertEqual(terminal.mouse_calls, [True, False, True, False])
        self.assertFalse(terminal.mouse_reporting)
        self.assertEqual(len(run.calls("session.interrupt")), 1)


def composer_text(screen: list[str]) -> str:
    rules = [index for index, line in enumerate(screen) if line.strip() and set(line.strip()) <= set("─━┄")]
    return screen[rules[-2] + 1]


if __name__ == "__main__":
    unittest.main()
