"""The subagent roster in the OMH TUI's loop: which events feed it, what empties it, and its strip on the info line.

Every case drives an `App` over the recording client from `test_tui_modes`.
The subagent frames are the ones Hermes 6c61c3e27d built
(`tests/fixtures/tui_gateway/subagent_relay.jsonl`, replayed whole or up to a
point), re-addressed to the app's live session; a case that needs a frame the
recording does not hold builds it from the recorded identity keys with
`test_tui_subagents.payload`. What is on screen is read back from the VT
screen the scripted terminal paints into.
"""

from __future__ import annotations

import re
import unittest

from _tui_lines import line_text
from test_tui_app import info_index
from test_tui_modes import SID, RecordingClient, answer, drawn, make_app, show_mode, type_text
from test_tui_subagents import FIRST, FRAMES, SECOND, cells, payload
from omh.tui.app import App
from omh.tui.modes import mode_label, info_line, strip_column
from omh.tui.frame import BOX_INSET, MODE_ACCEPT, MODE_NORMAL, MODE_PLAN
from omh.tui.gateway import Event, ServerRequest
from omh.tui.keys import CHAR, ENTER, Key
from omh.tui.panels import SPINNER_FRAMES
from omh.tui.style import Span
from omh.tui.subagents import MAIN_ID, Roster
from omh.tui.theme import DEFAULT_THEME
from omh.tui.transcript import NoticeBlock
from omh.tui.width import line_width

STARTS = FRAMES[:2]  # both recorded agents started, neither finished
SPIN = SPINNER_FRAMES[0]  # a clock at 0 draws the spinner's first frame
STRIP = f"● main  {SPIN} find usages of  {SPIN} read the diff  ↓ agents"
DONE_STRIP = "● main  ○ find usages of  ✗ read the diff  ↓ agents"
BUSY_HINT = "/steer <text> steers"
IDLE_HINT = "? for shortcuts"


def relay(app: App, frames: list[tuple[str, dict[str, object]]] = FRAMES, session_id: str = SID) -> None:
    """Hermes' recorded subagent frames, as events of `session_id`."""
    for event_type, frame in frames:
        app._on_event(Event(event_type, session_id, frame))


def agent_event(app: App, kind: str, subagent_id: str = FIRST, **changes: object) -> None:
    app._on_event(Event(f"subagent.{kind}", SID, payload(subagent_id, **changes)))


def agent_app(*, busy: bool = True, frames: list[tuple[str, dict[str, object]]] = STARTS, **options: object) -> tuple[App, RecordingClient]:
    """An app at clock 0 whose live session has the recorded agents running."""
    options.setdefault("clock", lambda: 0.0)
    app, client = make_app(busy=busy, **options)
    relay(app, frames)
    return app, client


def mode_row(app: App) -> str:
    """The info line: the region's last row, where the strip is drawn wherever a slot has room."""
    screen = drawn(app)
    return screen[info_index(screen)]


def info_text(app: App) -> str:
    """The info line after the inset it starts with under the box."""
    row = mode_row(app)
    if not row.startswith(" " * BOX_INSET):
        raise AssertionError(f"the info line does not start {BOX_INSET} cells in: {row!r}")
    return row[BOX_INSET:]


def info_left(app: App) -> str:
    """The info line's left side, after its inset: its right side (model, context, effort) stands 3 cells or more
    away."""
    return re.split(r" {3,}", info_text(app))[0]


class BidiControlTests(unittest.TestCase):
    """Bidi embeddings and overrides in gateway text never reach the terminal, so what is drawn is what was measured."""

    BIDI = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"

    def test_the_strips_cells_match_the_screen_with_bidi_controls_in_the_goals(self) -> None:
        app, _client = make_app(busy=True, clock=lambda: 0.0, cols=120)
        agent_event(app, "start", FIRST, goal=f"\u202efind usages{self.BIDI} of old_api")
        second_goal = "read\u2067 the diff\u2069 for risks"
        agent_event(app, "start", SECOND, goal=second_goal)
        agent_event(app, "tool", SECOND, goal=second_goal, tool_name="read\u202e_file", tool_preview="\u2066a.py")
        row = mode_row(app)
        writes = "".join(app.terminal.writes)  # type: ignore[attr-defined]
        self.assertFalse(any(ch in writes for ch in self.BIDI))
        _line, hits = app._agent_strip(120)
        shown = [cells(row, hit.start, hit.end) for hit in hits]
        self.assertEqual(shown, ["● main", f"{SPIN} find usages of", f"{SPIN} read the diff"])
        self.assertEqual(app.roster.agents[SECOND].last_tool, "read_file")

    def test_an_approvals_command_row_shows_the_command_in_the_order_it_runs(self) -> None:
        app, _client = make_app()
        command = "echo safe \u202e~ fr- mr ;\u202c done"
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": command, "description": "\u2066rm\u2069"}))
        screen = drawn(app)
        writes = "".join(app.terminal.writes)  # type: ignore[attr-defined]
        self.assertFalse(any(ch in writes for ch in self.BIDI))
        self.assertTrue(any("echo safe ~ fr- mr ; done" in line for line in screen), screen)


class RosterFeedTests(unittest.TestCase):
    def test_every_subagent_event_of_the_live_session_is_folded(self) -> None:
        app, _client = make_app()
        relay(app)
        self.assertEqual(list(app.roster.agents), [FIRST, SECOND])
        first, second = app.roster.agents[FIRST], app.roster.agents[SECOND]
        self.assertEqual((first.status, first.tool_count, first.last_tool), ("completed", 6, "read_file"))
        self.assertEqual((second.status, second.duration), ("timeout", 30.0))
        self.assertEqual(len(first.log), 11)  # one thinking line, six tool calls, the four lines of the reply

    def test_the_fold_is_the_rosters_own(self) -> None:
        # Nothing between the event and `Roster.apply`: the app's roster is what the roster alone would hold.
        app, _client = make_app(clock=lambda: 7.0)
        relay(app)
        alone = Roster()
        for event_type, frame in FRAMES:
            alone.apply(event_type, frame, 7.0)
        self.assertEqual(app.roster.agents, alone.agents)

    def test_events_of_another_session_are_dropped(self) -> None:
        app, _client = make_app()
        relay(app, session_id="someone-else")
        relay(app, session_id="")
        app._on_event(Event(FRAMES[0][0], None, FRAMES[0][1]))
        self.assertEqual(app.roster.agents, {})

    def test_a_subagent_event_adds_nothing_to_the_transcript(self) -> None:
        app, _client = make_app()
        relay(app)
        self.assertEqual(app.transcript.blocks, [])

    def test_a_payload_that_is_not_a_subagents_never_raises_into_the_loop(self) -> None:
        app, _client = make_app(busy=True)
        junk: list[object] = [
            None, [], "text", 7, {}, {"subagent_id": 5}, {"subagent_id": ""}, {"subagent_id": "x" * 500},
            {"subagent_id": FIRST, "goal": ["not", "text"], "tool_count": "many", "model": {"a": 1}},
            {"subagent_id": FIRST, "status": 5, "duration_seconds": float("nan"), "summary": b"bytes"},
            {"subagent_id": FIRST, "tool_name": None, "tool_preview": {"deep": [1, 2]}, "text": 3.5},
        ]
        for kind in ("start", "thinking", "tool", "progress", "complete", "spawn_requested", "text", "", "unknown.kind"):
            for frame in junk:
                app._on_event(Event(f"subagent.{kind}", SID, frame))  # type: ignore[arg-type]
                drawn(app)  # and whatever it left behind still draws
        self.assertLessEqual(set(app.roster.agents), {FIRST})
        self.assertFalse([block for block in app.transcript.blocks if isinstance(block, NoticeBlock)])


class RosterClearTests(unittest.TestCase):
    def test_a_prompt_that_starts_a_turn_empties_the_roster(self) -> None:
        app, client = agent_app(busy=False)
        type_text(app, "the next task")
        self.assertEqual(len(app.roster.agents), 2)  # typing alone changes nothing
        app._on_input(Key(ENTER))
        self.assertEqual(client.named("prompt.submit"), [{"session_id": SID, "text": "the next task"}])
        self.assertEqual(app.roster.agents, {})

    def test_a_prompt_queued_behind_the_running_turn_keeps_the_agents_until_it_is_sent(self) -> None:
        app, client = agent_app(busy=True)
        type_text(app, "after this")
        app._on_input(Key(ENTER))
        self.assertEqual(client.named("prompt.submit"), [])
        self.assertEqual(len(app.roster.agents), 2)
        app._on_event(Event("message.complete", SID, {"text": "done"}))  # the turn ends: the queue drains
        self.assertEqual(client.named("prompt.submit"), [{"session_id": SID, "text": "after this", "queued": True}])
        self.assertEqual(app.roster.agents, {})

    def test_a_local_command_and_a_steer_start_no_turn_and_keep_the_agents(self) -> None:
        app, client = agent_app(busy=True)
        for typed in ("/queue", "/steer look at the tests too"):
            type_text(app, typed)
            app._on_input(Key(ENTER))
        self.assertEqual(client.named("session.steer"), [{"session_id": SID, "text": "look at the tests too"}])
        self.assertEqual(len(app.roster.agents), 2)

    def test_a_new_session_empties_the_roster(self) -> None:
        app, client = agent_app(busy=False)
        type_text(app, "/new")
        app._on_input(Key(ENTER))
        self.assertEqual(len(app.roster.agents), 2)  # still this session until Hermes opened the next
        answer(app, client, {"session_id": "live-2", "stored_session_id": "stored-2", "info": {}})
        self.assertEqual(app.roster.agents, {})

    def test_switching_sessions_empties_the_roster(self) -> None:
        app, client = agent_app(busy=False)
        type_text(app, "/sessions another-stored-id")
        app._on_input(Key(ENTER))
        answer(app, client, {"session_id": "live-3", "stored_session_id": "another-stored-id", "info": {}, "messages": []})
        self.assertEqual(app.roster.agents, {})

    def test_a_session_that_could_not_be_opened_keeps_the_agents(self) -> None:
        app, client = agent_app(busy=False)
        type_text(app, "/new")
        app._on_input(Key(ENTER))
        answer(app, client, error={"code": 5000, "message": "no room"})
        self.assertEqual(len(app.roster.agents), 2)

    def test_an_agent_that_reports_after_the_roster_was_emptied_comes_back(self) -> None:
        app, _client = agent_app(busy=False)
        type_text(app, "next")
        app._on_input(Key(ENTER))
        agent_event(app, "tool", SECOND, tool_name="read_file", tool_count=1)
        self.assertEqual(list(app.roster.agents), [SECOND])


class StripTests(unittest.TestCase):
    def test_the_strip_joins_the_info_line_with_the_first_agent_and_leaves_with_the_next_prompt(self) -> None:
        app, _client = make_app(busy=True, cols=120, clock=lambda: 0.0)
        self.assertEqual(info_left(app), BUSY_HINT)
        relay(app, STARTS[:1])
        self.assertEqual(info_left(app), f"● main  {SPIN} find usages of  ↓ agents · {BUSY_HINT}")
        relay(app, STARTS[1:])
        self.assertEqual(info_left(app), f"{STRIP} · {BUSY_HINT}")
        app.turn_pending = False
        type_text(app, "next")
        app._on_input(Key(ENTER))
        self.assertEqual(info_left(app), BUSY_HINT)

    def test_a_finished_turn_keeps_its_strip_with_each_agents_verdict(self) -> None:
        app, _client = agent_app(busy=False, frames=FRAMES, cols=120)
        self.assertEqual(info_left(app), f"{DONE_STRIP} · {IDLE_HINT}")

    def test_a_narrow_row_cuts_the_hint_and_keeps_the_strip_whole(self) -> None:
        app, _client = agent_app()
        self.assertEqual(info_text(app), f"{STRIP} · {BUSY_HINT}")  # 80 columns hold both
        narrow, _client = agent_app(cols=70)
        self.assertEqual(info_text(narrow), f"{STRIP} · /steer <text>…")

    def test_the_spinner_turns_with_the_clock(self) -> None:
        now = [0.0]
        app, _client = agent_app(clock=lambda: now[0])
        now[0] = 0.25  # two ticks of a tenth of a second on
        self.assertIn(f"{SPINNER_FRAMES[2]} find usages of", mode_row(app))

    def test_the_mode_label_comes_first_then_the_strip_then_the_hints(self) -> None:
        expected = {
            MODE_NORMAL: f"{STRIP} · {BUSY_HINT}",
            MODE_ACCEPT: f"⏵⏵ accept all on · {STRIP} · (⇧⇥ plan)",  # approvals Hermes turned off itself
            MODE_PLAN: f"⏸ plan mode on · {STRIP} · (⇧⇥ to cycle)",
        }
        for mode, row in expected.items():
            with self.subTest(mode=mode):
                app, _client = agent_app(cols=120)
                show_mode(app, mode)
                self.assertEqual(info_left(app), row)

    def test_the_right_side_stays_while_the_strip_leaves_it_room(self) -> None:
        app, _client = agent_app(cols=120)
        app.effort = "high"
        row = info_text(app)
        self.assertTrue(row.startswith(f"{STRIP} · {BUSY_HINT}"), row)
        self.assertTrue(row.endswith("effort high"), row)
        self.assertEqual(len(mode_row(app)), 120)  # after its inset, to the last cell

    def test_cut_short_the_right_side_goes_first_then_the_hint_then_the_strip_and_never_the_label(self) -> None:
        roster = Roster()
        for event_type, frame in STARTS:
            roster.apply(event_type, frame, 0.0)
        label = "⏵⏵ accept all on"
        right = ((Span("effort xhigh", DEFAULT_THEME.muted),),)
        hint = "(⇧⇥ to cycle)"
        seen = set()

        def row_at(width: int) -> str:
            strip = roster.strip_line(width - strip_column(MODE_ACCEPT, DEFAULT_THEME), DEFAULT_THEME, 0)[0]
            return line_text(
                info_line(mode=MODE_ACCEPT, busy=True, queued=0, keyboard_protocol=False, width=width,
                          theme=DEFAULT_THEME, strip=strip, right=right)
            )

        for width in range(10, 170):
            row = row_at(width)
            with self.subTest(width=width):
                self.assertLessEqual(len(row), width)
                # The label, whole wherever it fits, else its short words.
                self.assertTrue(row.startswith(label if width >= len(label) else "⏵⏵ accept"), row)
                whole = f"{label} · {STRIP}"
                if len(whole) <= width:
                    self.assertTrue(row.startswith(whole), row)  # the strip is whole before a cell goes to the rest
                    seen.add("strip")
                if "effort" in row:
                    self.assertTrue(row.endswith("effort xhigh"), row)
                    self.assertTrue(hint in row or row.startswith(label + " · ● 2 agents ↓") or "·   " not in row, row)
                    seen.add("effort")
                if hint in row:
                    seen.add("hint")
                    if len(f"{whole} · {hint}   effort xhigh") <= width:
                        self.assertTrue(row.endswith("effort xhigh"), row)  # the whole left side leaves it room
                if "2 agents ↓" in row:
                    seen.add("collapsed")
        self.assertEqual(seen, {"strip", "effort", "hint", "collapsed"})
        # The whole left side and the right side fit from 101 cells; at 100 the right side goes first and the hint
        # stays whole; at 85 the hint has gone and the strip is whole, and the room the hint left takes the right
        # side; at 62 the strip counts its agents, and the room that frees goes to the hint and the right side.
        full = f"{label} · {STRIP} · {hint}"
        rows = {width: row_at(width) for width in (101, 100, 85, 84, 62, 61)}
        self.assertEqual(rows, {
            101: f"{full}   effort xhigh",
            100: full,
            85: f"{label} · {STRIP}   effort xhigh",
            84: f"{label} · {STRIP}",
            62: f"{label} · ● 2 agents ↓ · {hint}   effort xhigh",
            61: f"{label} · ● 2 agents ↓ · {hint}",
        })

    def test_the_strip_starts_right_after_the_label_and_at_the_edge_without_one(self) -> None:
        self.assertEqual(strip_column(MODE_NORMAL, DEFAULT_THEME), 0)
        for mode in (MODE_ACCEPT, MODE_PLAN):
            with self.subTest(mode=mode):
                label = mode_label(mode, DEFAULT_THEME)
                self.assertEqual(strip_column(mode, DEFAULT_THEME), line_width(label) + 3)
        self.assertEqual(mode_label(MODE_NORMAL, DEFAULT_THEME), ())

    def test_each_entrys_cells_are_where_the_screen_shows_that_entry_in_every_mode(self) -> None:
        for mode in (MODE_NORMAL, MODE_ACCEPT, MODE_PLAN):
            for cols in (80, 120):
                with self.subTest(mode=mode, cols=cols):
                    app, _client = agent_app(cols=cols)
                    show_mode(app, mode)
                    row = mode_row(app)
                    _line, hits = app._agent_strip(cols)
                    self.assertEqual([hit.subagent_id for hit in hits], [MAIN_ID, FIRST, SECOND])
                    shown = [cells(row, hit.start, hit.end) for hit in hits]
                    self.assertEqual(shown, ["● main", f"{SPIN} find usages of", f"{SPIN} read the diff"])
                    # and the cells either side of an entry are the gap, not the entry
                    for hit in hits[1:]:
                        self.assertEqual(cells(row, hit.start - 1, hit.start), " ")
                        self.assertEqual(cells(row, hit.end, hit.end + 1), " ")

    def test_a_strip_too_wide_for_the_row_counts_the_agents_and_names_none(self) -> None:
        app, _client = agent_app(cols=40)
        self.assertTrue(info_text(app).startswith("● 2 agents ↓"), info_text(app))
        self.assertEqual(app._agent_strip(40)[1], ())

    def test_the_strip_is_on_the_info_line_wherever_the_agent_list_can_open(self) -> None:
        for rows in (6, 7, 9, 10):
            with self.subTest(rows=rows):
                app, _client = agent_app(rows=rows)
                self.assertTrue(info_text(app).startswith("● main"), drawn(app))
                self.assertTrue(app._list_available())
        for rows, cols in ((5, 80), (24, 19)):  # too small for a slot: no list, so no strip offering one
            with self.subTest(rows=rows, cols=cols):
                app, _client = agent_app(rows=rows, cols=cols)
                self.assertNotIn("● main", "\n".join(drawn(app)))
                self.assertFalse(app._list_available())

    def test_an_open_question_keeps_the_strip_on_the_info_line(self) -> None:
        from omh.tui.gateway import ServerRequest

        # 80 columns less the inset are one cell short of the strip whole beside the keys' short form.
        for cols, said in ((81, f"{STRIP} · ↑↓ · esc denies and stops"),
                           (80, "● 2 agents ↓ · ↑↓ pick · enter confirms · d or esc denies and stops")):
            with self.subTest(cols=cols):
                app, _client = agent_app(cols=cols)
                app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
                self.assertTrue(info_text(app).startswith(said), info_text(app))

    def test_typing_never_changes_the_strip(self) -> None:
        # The hint after it may change (a draft takes Esc from the interrupt); the strip does not.
        app, _client = agent_app()
        strip = info_text(app).partition(" · ")[0]
        self.assertTrue(strip.endswith("↓ agents"), strip)
        for char in "what are they doing":
            app._on_input(Key(CHAR, char))
        self.assertEqual(info_text(app).partition(" · ")[0], strip)


if __name__ == "__main__":
    unittest.main()


class OneAgentSurfaceTests(unittest.TestCase):
    """The roster (strip, list) is the one agent surface: the HUD's [Agents] block steps aside while it holds agents."""

    def hud(self, app: App) -> None:
        from test_tui_panels import agent_row, hud_payload, with_agents
        from omh.tui.panels import HudSnapshot

        payload = with_agents(hud_payload([{"text": "Ship it", "state": "active"}]), [agent_row(0), agent_row(1)])
        app.hud = HudSnapshot(app.stored_id, payload, 0.0)

    def slot(self, app: App) -> list[str]:
        return [line_text(line) for line in app._slot_lines(80, 30)]

    def test_the_agents_block_leaves_the_panel_while_the_roster_holds_agents_and_the_plan_stays(self) -> None:
        app, _client = agent_app()
        self.hud(app)
        shown = self.slot(app)
        self.assertFalse([row for row in shown if row.startswith("[Agents]")], shown)
        self.assertTrue([row for row in shown if row.startswith("[Plan]")], shown)
        app.roster.clear()
        self.assertTrue([row for row in self.slot(app) if row.startswith("[Agents]")])

    def test_agents_whose_goals_start_alike_show_where_they_part_on_the_strip(self) -> None:
        # Live smoke s27 read `1·Run the shell  2·Run the shell`: the words that told the two apart were lost.
        app, _client = make_app(busy=True, clock=lambda: 0.0, cols=120)
        agent_event(app, "start", FIRST, goal="Run the shell command `echo alpha` in the scratch directory")
        agent_event(app, "start", SECOND, goal="Run the shell command `echo beta` in the scratch directory")
        line, hits = app._agent_strip(120)
        self.assertEqual(line_text(line), f"● main  {SPIN} …alpha  {SPIN} …beta  ↓ agents")
        self.assertEqual(len(hits), 3)

    def test_agents_whose_goals_match_are_numbered_on_the_strip(self) -> None:
        app, _client = make_app(busy=True, clock=lambda: 0.0, cols=120)
        goal = "Run exactly the command below and report"
        agent_event(app, "start", FIRST, goal=goal)
        agent_event(app, "start", SECOND, goal=goal)
        agent_event(app, "start", "sa-2-other", goal="read the diff for risks")
        line, hits = app._agent_strip(120)
        text = line_text(line)
        self.assertIn(f"{SPIN} 1·Run exactly the", text)
        self.assertIn(f"{SPIN} 2·Run exactly the", text)
        self.assertIn(f"{SPIN} read the diff", text)  # a name that is its own stays as it is
        self.assertEqual(len(hits), 4)

    def test_idle_with_agents_still_running_the_strip_says_which_and_no_state_word_does(self) -> None:
        # The status line's `idle · 2 agents running` is gone with the status line: the strip marks each agent.
        app, _client = agent_app(busy=False, cols=120)
        self.assertEqual(info_left(app), f"{STRIP} · {IDLE_HINT}")
        relay(app, FRAMES)
        self.assertEqual(info_left(app), f"{DONE_STRIP} · {IDLE_HINT}")
        for state in ("idle", "running", "agents running"):
            self.assertNotIn(state, mode_row(app))
