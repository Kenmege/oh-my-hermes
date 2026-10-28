"""`/resume` is `/sessions`: the menu offers it under its own name, and every form of it runs as /sessions' does.

Measured on a real TUI over Hermes' shared gateway (2026-10-28): `/res` highlighted Hermes' `/compress` (a name
that only contains it), `/resu` offered nothing and its Enter sent it to Hermes, which resolved it to its own
classic-only /resume; and `/sessions <title>` and `/resume <title>` were both refused `4001 not_found`, as the shared
gateway resumes ids only.
"""

from __future__ import annotations

import unittest

from test_tui_catalog import CATALOG, catalog_app
from test_tui_modes import SID, answer, make_app, notices, type_text
from omh.tui import slash
from omh.tui.app import RESUME_REFUSED, SESSION_LIST_LIMIT
from omh.tui.keys import ENTER, Key

# Hermes' own names next to the alias, as its catalog lists them.
HERMES_CATALOG = {**CATALOG, "pairs": [
    *CATALOG["pairs"],
    ["/compress", "Compress conversation context"],
    ["/egress", "Show Docker egress proxy status"],
    ["/resume", "Resume a previously-named session"],
]}
LISTED = {"sessions": [
    {"id": "live-other", "title": "", "started_at": 0, "message_count": 2},
    {"id": "stored-9", "title": "Release notes for 4.0.3", "started_at": 0, "message_count": 31},
    {"id": "stored-3", "title": "Release notes for 4.0.3", "started_at": 0, "message_count": 4},
]}
NOT_FOUND = {"code": RESUME_REFUSED, "message": "not_found"}


class MenuTests(unittest.TestCase):
    def test_an_alias_is_offered_under_its_own_name_when_the_commands_name_does_not_match(self) -> None:
        pairs = slash.catalog_pairs(HERMES_CATALOG)
        sessions = slash.parse_slash("/sessions").command
        self.assertEqual(slash.completions("resu", pairs), [slash.Completion("resume", sessions.summary)])
        names = [completion.name for completion in slash.completions("res", pairs)]
        self.assertLess(names.index("resume"), names.index("compress"))  # the TUI's own first, as for every name
        self.assertEqual([completion.name for completion in slash.completions("ses", pairs)][0], "sessions")
        self.assertIn("queue", [completion.name for completion in slash.completions("q", pairs)])  # its name matches
        self.assertNotIn("resume", [completion.name for completion in slash.completions("", pairs)])
        self.assertTrue({"resume", "switch", "exit"} <= slash.known_names(pairs))  # drawn as /sessions is

    def test_resume_completes_and_runs_as_sessions_does(self) -> None:
        for typed, completed in (("/resu", "/resume "), ("/sess", "/sessions ")):
            with self.subTest(typed=typed):
                app, client = catalog_app(HERMES_CATALOG)
                type_text(app, typed)
                self.assertEqual(app._menu_rows()[0][0], completed.strip())
                app._on_input(Key(ENTER))
                self.assertEqual(app.composer.text, completed)
                self.assertEqual(client.calls, [])
                app._on_input(Key(ENTER))
                self.assertEqual(client.calls, [("session.list", {"limit": SESSION_LIST_LIMIT})])


class TitleTests(unittest.TestCase):
    def switch(self, typed: str) -> tuple[list[tuple[str, dict]], list[str]]:
        app, client = make_app()
        type_text(app, typed)
        app._on_input(Key(ENTER))
        answer(app, client, error=NOT_FOUND)
        answer(app, client, LISTED)
        return client.calls, notices(app)

    def test_a_title_the_gateway_refuses_is_resumed_by_the_id_listed_under_it(self) -> None:
        for name in ("sessions", "resume"):
            with self.subTest(name=name):
                calls, _said = self.switch(f"/{name} Release notes for 4.0.3")
                self.assertEqual(calls, [
                    ("session.resume", {"session_id": "Release notes for 4.0.3", "cols": 80}),
                    ("session.list", {"limit": SESSION_LIST_LIMIT}),
                    ("session.resume", {"session_id": "stored-9", "cols": 80}),  # the most recently active
                ])

    def test_a_title_nothing_is_listed_under_says_the_first_refusal(self) -> None:
        calls, said = self.switch("/resume No such title")
        self.assertEqual([method for method, _params in calls], ["session.resume", "session.list"])
        self.assertIn("Could not switch to No such title (4001): not_found. Still in ", said[-1])

    def test_a_picked_row_or_another_refusal_is_said_without_a_listing(self) -> None:
        app, client = make_app()
        app._switch_session("stored-9")  # what a pick runs: an id the person did not type
        answer(app, client, error=NOT_FOUND)
        type_text(app, "/resume stored-3")
        app._on_input(Key(ENTER))
        answer(app, client, error={"code": 4090, "message": "the session is open in another process"})
        self.assertEqual([method for method, _params in client.calls], ["session.resume", "session.resume"])
        self.assertEqual(len([text for text in notices(app) if text.startswith("Could not switch")]), 2)
        self.assertEqual(app.sid, SID)


if __name__ == "__main__":
    unittest.main()
