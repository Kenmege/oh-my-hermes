"""The block logo a start shows above the banner.

The rows are the `banner_logo` of the shipped skin (read through the
package, as `skin_colors` reads the palette), its Rich markup taken off and
each row painted in the skin's gradient colour, lifted to the 3:1 a
graphical mark needs on the terminal's background. It shows from
`LOGO_MIN_ROWS` rows and as many columns as the logo is wide, goes to
scrollback with the banner, comes back with a full redraw only on top of the
whole session it opened, and is not shown again for `/new` or a switch.
"""

from __future__ import annotations

import unittest
from unittest import mock

from _tui_lines import line_text
from test_tui_app import FakePoller, ScriptedTerminal
from test_tui_modes import SID, RecordingClient
from test_tui_theme import DARK_BACKGROUNDS, LIGHT_BACKGROUNDS, contrast
from omh.skin_pack import SKIN_THEMES, default_theme, logo_from_text, skin_colors, skin_logo
from omh.tui.app import BANNER_BRAND, LOGO_MIN_ROWS, App, banner_logo
from omh.tui.gateway import Response
from omh.tui.theme import logo_lines
from omh.tui.width import line_width

SKY_GRADIENT = ["#B8F4FF", "#7FDBFF", "#40D6DE", "#00CED1", "#0FA3AF", "#0B7E8A"]
LOGO_WIDTH = 100


class StartClient(RecordingClient):
    def call(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        return {"session_id": SID, "stored_session_id": "stored-1", "info": {"model": "m", "cwd": "/p"}}


def started(rows: int, cols: int) -> tuple[App, list[str]]:
    """A launched app after its first frame, and everything on its terminal, scrollback first."""
    app = App(ScriptedTerminal(rows, cols), StartClient(), {}, poller=FakePoller())  # type: ignore[arg-type]
    app.startup()
    return app, shown(app)


def shown(app: App) -> list[str]:
    app._render()
    screen = app.terminal.screen  # type: ignore[attr-defined]
    return screen.history() + screen.lines()


def logo_text() -> list[str]:
    return [line_text(row) for row in logo_lines(default_theme())]


class ReaderTests(unittest.TestCase):
    def test_every_shipped_skin_has_six_rows_a_hundred_cells_wide_with_the_markup_off(self) -> None:
        for skin in SKIN_THEMES:
            with self.subTest(skin=skin.short_name):
                rows = skin_logo(skin.skin_name)
                self.assertEqual(len(rows), 6)
                self.assertEqual({line_width(row) for row in logo_lines(skin)}, {LOGO_WIDTH})
                for text, colour, _bold in rows:
                    self.assertNotIn("[", text)
                    self.assertNotIn("]", text)
                    self.assertTrue(colour.startswith("#"))
        self.assertEqual([colour for _text, colour, _bold in skin_logo("omh")], SKY_GRADIENT)
        self.assertEqual([bold for _text, _colour, bold in skin_logo("omh")], [True, True, False, False, False, False])

    def test_a_row_without_a_tag_or_a_colour_keeps_its_text_and_the_block_ends_at_the_next_key(self) -> None:
        document = "name: x\nbanner_logo: |-\n  plain row\n  [bold]bold row[/]\n  [#12AB34] lead[/]\ncolors:\n  a: \"#000000\"\n"
        self.assertEqual(
            logo_from_text(document), [("plain row", "", False), ("bold row", "", True), (" lead", "#12AB34", False)]
        )


class PaintTests(unittest.TestCase):
    def test_every_row_reaches_three_to_one_on_every_background(self) -> None:
        for skin in SKIN_THEMES:
            for background in (None, *LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                surface = background or skin_colors(skin.skin_name)["status_bar_bg"]
                for number, row in enumerate(logo_lines(skin, background)):
                    with self.subTest(skin=skin.short_name, background=background, row=number):
                        self.assertGreaterEqual(contrast(row[0].style.fg, surface), 3.0)

    def test_the_skins_colour_stays_wherever_it_already_reaches_three_to_one(self) -> None:
        # Sky's last row is 3.43:1 on its dark stand-in: a mark keeps it; lifting to text contrast would not.
        for skin in SKIN_THEMES:
            for background in (None, *LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                surface = background or skin_colors(skin.skin_name)["status_bar_bg"]
                painted = [row[0].style.fg for row in logo_lines(skin, background)]
                for number, (_text, colour, _bold) in enumerate(skin_logo(skin.skin_name)):
                    if contrast(colour, surface) >= 3.0:
                        with self.subTest(skin=skin.short_name, background=background, row=number):
                            self.assertEqual(painted[number], colour)
        self.assertEqual([row[0].style.fg for row in logo_lines(default_theme())], SKY_GRADIENT)
        self.assertEqual(len({row[0].style.fg for row in logo_lines(default_theme(), "#FFFFFF")}), 6)  # still a gradient

    def test_no_escape_sequence_or_control_character_from_the_skin_reaches_the_terminal(self) -> None:
        planted = [("a\x1b[31mb\x07c\x1b]0;title\x07d\u009b2Je", "#123456", False)]
        with mock.patch("omh.tui.theme.skin_logo", return_value=planted):
            (row,) = logo_lines(default_theme())
        self.assertEqual(line_text(row), "abcde")


class StartTests(unittest.TestCase):
    def test_a_start_wide_and_tall_enough_shows_the_logo_above_the_banner(self) -> None:
        self.assertEqual(LOGO_MIN_ROWS, 24)
        _app, screen = started(LOGO_MIN_ROWS, LOGO_WIDTH)
        logo = logo_text()
        self.assertEqual(screen[: len(logo)], logo)
        self.assertEqual(screen[len(logo)], "")
        self.assertTrue(screen[len(logo) + 1].startswith(BANNER_BRAND))

    def test_a_terminal_narrower_than_the_logo_or_shorter_than_the_minimum_gets_the_banner_alone(self) -> None:
        for rows, cols in ((LOGO_MIN_ROWS, LOGO_WIDTH - 1), (LOGO_MIN_ROWS - 1, 160)):
            with self.subTest(rows=rows, cols=cols):
                _app, screen = started(rows, cols)
                self.assertTrue(screen[0].startswith(BANNER_BRAND), screen[:3])
                self.assertEqual(banner_logo(default_theme(), None, cols, rows), [])

    def test_a_full_redraw_shows_it_again_on_top_of_the_whole_session_only(self) -> None:
        app, _screen = started(30, 120)
        app.transcript.add_notice("one")
        app._full_redraw()
        self.assertEqual(shown(app)[:6], logo_text())
        app.transcript.add_notice("two")
        with mock.patch("omh.tui.app.REDRAW_TAIL_ROWS", 2):  # the redraw leaves "one" out
            app._full_redraw()
            screen = shown(app)
        self.assertTrue(screen[0].startswith(BANNER_BRAND), screen[:3])

    def test_a_new_session_does_not_repeat_it(self) -> None:
        app, _screen = started(30, 120)
        app._new_session("")
        calls = app.client.calls  # type: ignore[attr-defined]
        self.assertEqual(calls[-1][0], "session.create")
        app._on_response(Response(len(calls), {"session_id": "live-2", "stored_session_id": "s2"}, None))
        app._full_redraw()
        screen = shown(app)
        self.assertTrue(screen[0].startswith(BANNER_BRAND), screen[:3])
        self.assertNotIn(logo_text()[0], screen)


if __name__ == "__main__":
    unittest.main()
