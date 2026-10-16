"""Ctrl+R's history search stands where the composer was, as a picker does (owner complaint, P5).

The composer is not drawn while the search has the keys, the caret is at
the end of the query on the search's own row (so an IME pre-edit shows
where the text goes), the mode row says the search's keys, and Esc gives
back the draft exactly as it was: its text, its caret and its paste chips.
"""

from __future__ import annotations

import unittest

from _tui_lines import lines_text
from test_tui_modes import make_app, type_text
from omh.tui.history import SEARCH_HINT, SEARCH_LABEL, HistorySearch
from omh.tui.keys import CHAR, ENTER, ESCAPE, LEFT, Key, Paste
from omh.tui.theme import DEFAULT_THEME
from omh.tui.width import cell_width

CTRL_R = Key(CHAR, "r", ctrl=True)


def searching(draft: str = "half typed", history: tuple[str, ...] = ("ls -la", "git push")):
    app, client = make_app()
    app.composer.history = list(history)
    type_text(app, draft)
    app._on_input(Key(LEFT))
    app._on_input(CTRL_R)
    return app, client


def screen(app) -> list[str]:
    app._render()
    return app.terminal.screen.lines()


class SearchStandsWhereTheComposerWasTests(unittest.TestCase):
    def test_the_composer_and_its_rules_are_not_drawn_while_searching(self) -> None:
        app, _client = searching()
        rows = screen(app)
        self.assertFalse(any(row.startswith("› half typed") for row in rows), rows)
        self.assertFalse(any(row.startswith("────") for row in rows), rows)
        self.assertTrue(any(row.startswith(SEARCH_LABEL.rstrip()) for row in rows), rows)

    def test_the_caret_is_at_the_end_of_the_query_on_the_search_row(self) -> None:
        app, _client = searching()
        type_text(app, "gi")
        rows = screen(app)
        terminal = app.terminal
        row, col = terminal.screen.cursor  # type: ignore[attr-defined]
        self.assertTrue(rows[row].startswith(SEARCH_LABEL + "gi"), (row, rows))
        self.assertEqual(col, cell_width(SEARCH_LABEL + "gi"))
        self.assertTrue(terminal.screen.cursor_visible)  # type: ignore[attr-defined]

    def test_the_search_row_is_the_composer_row_and_the_composer_comes_back_on_it(self) -> None:
        app, _client = make_app()
        type_text(app, "half typed")
        before = screen(app)
        composer_row = next(index for index, row in enumerate(before) if row.startswith("› half typed"))
        app.composer.history = ["ls -la"]
        app._on_input(CTRL_R)
        during = screen(app)
        # The block the composer and its rules took: the query row is its last row, above the mode row.
        self.assertTrue(during[composer_row + 1].startswith(SEARCH_LABEL.rstrip()), during)
        app._on_input(Key(ESCAPE))
        after = screen(app)
        self.assertTrue(after[composer_row].startswith("› half typed"), after)

    def test_the_mode_row_says_the_search_keys(self) -> None:
        app, _client = searching()
        self.assertIn(SEARCH_HINT, "\n".join(screen(app)))

    def test_esc_gives_back_the_draft_with_its_caret_and_chips(self) -> None:
        app, _client = make_app()
        app.composer.handle(Paste("one\ntwo\nthree\nfour\nfive"))  # a chip
        type_text(app, " and more")
        for _ in range(3):
            app._on_input(Key(LEFT))
        kept = (app.composer.text, app.composer.caret, dict(app.composer.pastes))
        app.composer.history = ["ls -la"]
        app._on_input(CTRL_R)
        type_text(app, "ls")
        app._on_input(Key(ESCAPE))
        self.assertIsNone(app.search)
        self.assertEqual((app.composer.text, app.composer.caret, dict(app.composer.pastes)), kept)

    def test_enter_still_takes_the_match(self) -> None:
        app, client = searching()
        type_text(app, "ls")
        app._on_input(Key(ENTER))
        self.assertEqual(app.composer.text, "ls -la")
        self.assertEqual(client.calls, [])


class SearchRowsTests(unittest.TestCase):
    def test_one_row_is_the_query_row_and_its_caret(self) -> None:
        search = HistorySearch(["ls -la"], "")
        search.handle_key(Key(CHAR, "l"))
        rows = lines_text(search.lines(80, DEFAULT_THEME, 1))
        self.assertEqual(rows, [SEARCH_LABEL + "l  " + SEARCH_HINT])
        self.assertEqual(search.caret, (0, cell_width(SEARCH_LABEL + "l")))

    def test_without_keys_the_query_row_holds_only_the_query(self) -> None:
        search = HistorySearch(["ls -la"], "")
        search.handle_key(Key(CHAR, "l"))
        rows = lines_text(search.lines(80, DEFAULT_THEME, 4, keys=False))
        self.assertEqual([row.rstrip() for row in rows], ["› ls -la  1/1", SEARCH_LABEL + "l"])
        self.assertEqual(search.caret, (1, cell_width(SEARCH_LABEL + "l")))


if __name__ == "__main__":
    unittest.main()
