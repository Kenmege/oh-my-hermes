"""What a slash command prints stays in the transcript, under an echo of the command.

The pager opens only when the person asks for one (`/history`, `/keys`,
`/logs`, Ctrl+O); a command's output, however long, is the transcript's: the
echo `› /name arg`, its output under `⎿`, the first rows of a long one and a
count of the rest, which `/history` shows in full. Driven through the
recording client of `test_tui_modes`, answering each call by hand.
"""

from __future__ import annotations

import unittest

from test_tui_modes import SID, answer, drawn, make_app, type_text
from omh.tui.app import App
from omh.tui.gateway import Response
from omh.tui.keys import ENTER, TAB, Key
from omh.tui.transcript import NOTICE_INFO, NOTICE_WARN, OUTPUT_PREVIEW_ROWS, CommandBlock, NoticeBlock, UserBlock


def send(app: App, text: str) -> None:
    type_text(app, text)
    app._on_input(Key(ENTER))


def last(app: App) -> object:
    return app.transcript.blocks[-1]


class CommandEchoTests(unittest.TestCase):
    def test_a_hermes_command_is_echoed_at_once_and_its_output_lands_under_it(self) -> None:
        app, client = make_app()
        send(app, "/usage")
        self.assertEqual(client.calls, [("slash.exec", {"command": "usage", "session_id": SID})])
        block = last(app)
        self.assertIsInstance(block, CommandBlock)
        self.assertEqual((block.command, block.parts, block.done), ("/usage", [], False))
        answer(app, client, {"output": "Session cost: $0.01"})
        self.assertEqual((block.parts, block.done), ([("Session cost: $0.01", NOTICE_INFO)], True))
        screen = drawn(app)
        self.assertIn("› /usage", screen)
        self.assertIn("  ⎿ Session cost: $0.01", screen)

    def test_long_output_stays_in_the_transcript_and_no_pager_opens(self) -> None:
        app, client = make_app(rows=24, cols=80)
        send(app, "/context")
        long = "\n".join(f"context row {number}" for number in range(40))
        answer(app, client, {"output": long})
        self.assertIsNone(app.pager)
        self.assertEqual(last(app).parts, [(long, NOTICE_INFO)])  # all of it held, for /history
        screen = drawn(app)
        self.assertIn(f"    context row {OUTPUT_PREVIEW_ROWS - 1}", screen)
        self.assertNotIn(f"    context row {OUTPUT_PREVIEW_ROWS}", screen)
        self.assertIn(f"    … +{40 - OUTPUT_PREVIEW_ROWS} rows · /history shows all", screen)

    def test_a_command_dispatch_owns_answers_under_the_same_echo_and_closes_it_only_then(self) -> None:
        app, client = make_app()
        send(app, "/undo")
        app._on_response(Response(1, None, {"code": 4018, "message": "not a slash.exec command"}))
        self.assertEqual(client.calls[-1], ("command.dispatch", {"name": "undo", "arg": "", "session_id": SID}))
        self.assertFalse(last(app).done)  # its fallback is still out
        answer(app, client, {"type": "exec", "output": "Undid the last turn."})
        self.assertEqual((last(app).command, last(app).parts, last(app).done), ("/undo", [("Undid the last turn.", NOTICE_INFO)], True))

    def test_a_local_commands_notice_is_its_output_and_the_echo_closes_with_the_command(self) -> None:
        app, client = make_app()
        send(app, "/theme nope")
        self.assertEqual(client.calls, [])
        block = last(app)
        self.assertTrue(block.done)
        self.assertEqual(block.command, "/theme nope")
        self.assertEqual(block.parts[0][1], NOTICE_WARN)
        self.assertIn("No theme named nope", block.parts[0][0])

    def test_help_prints_whole(self) -> None:
        app, _client = make_app(rows=24, cols=200)
        send(app, "/help")
        block = last(app)
        self.assertTrue(block.whole)
        self.assertIsNone(app.pager)
        self.assertGreater(len(block.parts[0][0].split("\n")), OUTPUT_PREVIEW_ROWS)

    def test_output_answered_after_a_prompt_repeats_the_echo(self) -> None:
        app, client = make_app()
        send(app, "/usage")
        send(app, "and now a prompt")
        self.assertIsInstance(last(app), UserBlock)
        app._on_response(Response(1, {"output": "Session cost: $0.01"}, None))
        block = last(app)
        self.assertIsInstance(block, CommandBlock)
        self.assertEqual((block.command, block.parts, block.done), ("/usage", [("Session cost: $0.01", NOTICE_INFO)], True))
        echoes = [entry for entry in app.transcript.blocks if isinstance(entry, CommandBlock)]
        self.assertEqual([entry.command for entry in echoes], ["/usage", "/usage"])

    def test_two_commands_out_at_once_each_print_under_their_own_echo(self) -> None:
        # Hermes' slash.exec runs a CLI worker and can take seconds, so a second command often goes before the first
        # one answers; the answer finds its command through the call that asked, whichever order they come in.
        for order in ((1, 2), (2, 1)):
            with self.subTest(order=order):
                app, client = make_app()
                send(app, "/usage")
                send(app, "/skills list")
                said = {1: "USAGE: 12k tokens today", 2: "SKILLS: omh-plan, omh-review"}
                for call in order:
                    app._on_response(Response(call, {"output": said[call]}, None))
                under = {part: block.command for block in app.transcript.blocks if isinstance(block, CommandBlock)
                         for part, _level in block.parts}
                self.assertEqual(under, {said[1]: "/usage", said[2]: "/skills list"})
                echoes = [block for block in app.transcript.blocks if isinstance(block, CommandBlock)]
                self.assertEqual([block.command for block in echoes], ["/usage", "/skills list"])  # no repeated echo
                self.assertTrue(all(block.done for block in echoes))

    def test_output_answered_after_a_switch_repeats_the_echo_in_the_session_now_live(self) -> None:
        app, client = make_app()
        send(app, "/usage")
        send(app, "/new")
        answer(app, client, {"session_id": "live-2", "stored_session_id": "stored-2", "info": {}})
        app._on_response(Response(1, {"output": "Session cost: $0.01"}, None))
        echoes = [block for block in app.transcript.blocks if isinstance(block, CommandBlock)]
        self.assertEqual([(block.command, block.parts) for block in echoes][-1:],
                         [("/usage", [("Session cost: $0.01", NOTICE_INFO)])])  # not lost in the old session's echo

    def test_a_notice_from_outside_a_command_is_not_its_output(self) -> None:
        app, _client = make_app()
        send(app, "/usage")  # still waiting for Hermes
        app._on_error_event("The gateway reported an error.")  # between turns: a notice of its own
        echo = next(block for block in app.transcript.blocks if isinstance(block, CommandBlock))
        self.assertEqual(echo.parts, [])
        self.assertIsInstance(last(app), NoticeBlock)

    def test_a_prompt_typed_in_plan_mode_is_no_command_and_is_not_echoed(self) -> None:
        app, client = make_app()
        app.plan_mode = True
        send(app, "fix the flaky test")
        self.assertFalse([entry for entry in app.transcript.blocks if isinstance(entry, CommandBlock)])
        self.assertEqual(client.calls[0][0], "slash.exec")

    def test_the_pager_opens_only_when_asked_for(self) -> None:
        app, _client = make_app()
        app.transcript.add_notice("an earlier notice")
        send(app, "/history")
        self.assertIsNotNone(app.pager)
        self.assertEqual(last(app).command, "/history")
        self.assertTrue(last(app).done)

    def test_completion_still_fills_the_composer_without_running_anything(self) -> None:
        app, client = make_app()
        type_text(app, "/usa")
        app._on_input(Key(TAB))
        self.assertEqual(client.calls, [])
        self.assertFalse([entry for entry in app.transcript.blocks if isinstance(entry, (CommandBlock, NoticeBlock))])


class DetailsViewTests(unittest.TestCase):
    def test_ctrl_o_opens_the_whole_transcript_with_details_at_its_end(self) -> None:
        from omh.tui.keys import CHAR
        from omh.tui.pager import HistoryPager

        app, client = make_app(rows=12, cols=80)
        for number in range(30):
            app.transcript.add_notice(f"notice {number}")
        app._on_input(Key(CHAR, "o", ctrl=True))
        self.assertIsInstance(app.pager, HistoryPager)
        screen = drawn(app)
        self.assertIn("· notice 29", screen)  # the last page, not the first
        self.assertNotIn("· notice 0", screen)
        self.assertEqual(client.calls, [])

    def test_ctrl_o_leaves_an_open_question_on_screen(self) -> None:
        from omh.tui.gateway import ServerRequest
        from omh.tui.keys import CHAR

        app, _client = make_app()
        app.transcript.add_notice("something to show")  # so a view would have rows to open on
        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
        app._on_input(Key(CHAR, "o", ctrl=True))
        self.assertIsNone(app.pager)
        self.assertIsNotNone(app.slot)


class DocumentedOutputTests(unittest.TestCase):
    def test_installation_names_the_rows_the_code_shows(self) -> None:
        from pathlib import Path

        prose = " ".join((Path(__file__).resolve().parents[1] / "docs/INSTALLATION.md").read_text(encoding="utf-8").split())
        self.assertIn(f"a long output shows its first {OUTPUT_PREVIEW_ROWS} rows and a count of the rest", prose)
        self.assertIn("Nothing a command prints opens a pager", prose)


if __name__ == "__main__":
    unittest.main()
