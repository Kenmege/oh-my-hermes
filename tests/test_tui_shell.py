"""`!` shell mode: a line the person sends that starts with `!` is their own shell command, run by Hermes' `shell.exec`.

The composer shows the mode while the line starts with `!` (the label
"! shell (not sent to the model)" and the rules in their own glyph and
colour); sending it echoes `› !cmd` and shows what the command printed and
its exit code, or Hermes' refusal. It never becomes a prompt. During a turn
it waits in the queue, as a slash command waits in Claude Code, and runs
when the turn ends. Answers are the recorded Hermes 6c61c3e27d frames in
`tests/fixtures/tui_gateway/parity.jsonl`.
"""

from __future__ import annotations

import unittest

from _tui_lines import line_text
from test_tui_gateway_contract import recorded_rows
from test_tui_modes import SID, answer, drawn, make_app, notices, type_text
from omh.tui import gateway_contract as contract
from omh.tui.app import App
from omh.tui.frame import MODE_SHELL, MODE_RULE_GLYPHS, rule_line
from omh.tui.gateway import Event, Response
from omh.tui.keys import ENTER, Key
from omh.tui.modes import SHELL_LABEL
from omh.tui.theme import DEFAULT_THEME
from omh.tui.transcript import NOTICE_ERROR, NOTICE_INFO, NOTICE_WARN, CommandBlock


def recorded(segment: str) -> tuple[dict, dict]:
    """The recorded call's params and the answer frame (`result` or `error`)."""
    rows = recorded_rows("parity", segment)
    call = next(row["frame"] for row in rows if row["dir"] == "out")
    reply = next(row["frame"] for row in rows if row["dir"] == "in")
    return call["params"], {key: reply[key] for key in ("result", "error") if key in reply}


def send(app: App, text: str) -> None:
    type_text(app, text)
    app._on_input(Key(ENTER))


def reply(app: App, client: object, frame: dict) -> None:
    answer(app, client, frame.get("result"), frame.get("error"))  # type: ignore[arg-type]


class ShellModeTests(unittest.TestCase):
    def test_a_leading_bang_shows_the_shell_mode_on_the_frame_and_the_mode_row(self) -> None:
        app, _client = make_app()
        type_text(app, "!ls")
        screen = drawn(app)
        self.assertTrue(any(SHELL_LABEL in line for line in screen), screen)
        rule = line_text(rule_line(80, DEFAULT_THEME, MODE_SHELL))
        self.assertIn(rule, screen)  # both edges of the composer frame
        self.assertNotEqual(MODE_RULE_GLYPHS[MODE_SHELL], MODE_RULE_GLYPHS["normal"])  # not only a colour

    def test_the_recorded_command_prints_stdout_and_stderr_under_its_echo(self) -> None:
        params, frame = recorded("shell_ok")
        app, client = make_app()
        send(app, "!" + params["command"])
        self.assertEqual(client.calls, [("shell.exec", {"command": params["command"]})])
        block = app.transcript.blocks[-1]
        self.assertIsInstance(block, CommandBlock)
        self.assertEqual(block.command, "!echo hello; echo oops >&2")
        reply(app, client, frame)
        self.assertEqual((block.parts, block.done), ([("hello", NOTICE_INFO), ("oops", NOTICE_INFO)], True))
        self.assertNotIn("prompt.submit", [method for method, _params in client.calls])

    def test_a_non_zero_exit_is_said_after_what_it_printed(self) -> None:
        params, frame = recorded("shell_exit")
        app, client = make_app()
        send(app, "!" + params["command"])
        reply(app, client, frame)
        self.assertEqual(app.transcript.blocks[-1].parts, [("partial", NOTICE_INFO), ("exit 3", NOTICE_ERROR)])

    def test_hermes_refusing_a_dangerous_command_is_said_in_its_words(self) -> None:
        params, frame = recorded("shell_blocked")
        app, client = make_app()
        send(app, "!" + params["command"])
        reply(app, client, frame)
        (part,) = app.transcript.blocks[-1].parts
        self.assertEqual(part, ("Blocked: recursive delete. Use the agent for dangerous commands.", NOTICE_WARN))

    def test_a_command_that_prints_nothing_says_its_exit_code(self) -> None:
        app, client = make_app()
        send(app, "!true")
        answer(app, client, {"stdout": "", "stderr": "", "code": 0})
        self.assertEqual(app.transcript.blocks[-1].parts, [("exit 0", NOTICE_INFO)])

    def test_a_bang_alone_says_how_and_sends_nothing(self) -> None:
        app, client = make_app()
        send(app, "!  ")
        self.assertEqual(client.calls, [])
        self.assertIn("!<command>", notices(app, NOTICE_WARN)[-1])

    def test_plan_mode_does_not_wrap_a_shell_line(self) -> None:
        app, client = make_app()
        app.plan_mode = True
        send(app, "!ls")
        self.assertEqual(client.calls, [("shell.exec", {"command": "ls"})])


class ShellDuringATurnTests(unittest.TestCase):
    def test_it_waits_for_the_turn_and_the_prompt_behind_it_waits_for_its_answer(self) -> None:
        app, client = make_app(busy=True)
        send(app, "!ls")
        send(app, "next prompt")
        self.assertEqual(client.calls, [])
        self.assertEqual([text for text, _shown, _typed in app.queued], ["!ls", "next prompt"])
        app._on_event(Event("message.complete", SID, {"text": "done"}))
        self.assertEqual(client.calls, [("shell.exec", {"command": "ls"})])
        app._on_response(Response(1, {"stdout": "a.py\n", "stderr": "", "code": 0}, None))
        self.assertEqual(client.calls[-1], ("prompt.submit", {"session_id": SID, "text": "next prompt", "queued": True}))

    def test_ctrl_enter_never_steers_a_shell_line(self) -> None:
        app, client = make_app(busy=True)
        type_text(app, "!ls")
        app._on_input(Key(ENTER, ctrl=True))
        self.assertEqual(client.calls, [])
        self.assertEqual([text for text, _shown, _typed in app.queued], ["!ls"])


class ContractTests(unittest.TestCase):
    def test_shell_exec_carries_the_command_only(self) -> None:
        # ShellExecParams in Hermes 6c61c3e27d's contracts/tools_commands.py: command, profile.
        self.assertEqual(contract.CLIENT_METHODS["shell.exec"], {"command"})
        self.assertIn("command", contract.call_problem("shell.exec", {"command": ""}))
        self.assertEqual(contract.call_problem("shell.exec", recorded("shell_ok")[0]), "")


if __name__ == "__main__":
    unittest.main()
