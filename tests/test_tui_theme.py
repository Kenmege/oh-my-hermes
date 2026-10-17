"""The OMH TUI's role-based theme over the shipped palettes."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path
import unittest

from omh.skin_pack import SKIN_THEMES, hex_to_rgb, skin_colors
from omh.tui.theme import DEFAULT_THEME, GLYPH_ROLES, TEXT_ROLES, build_theme, is_light, theme_named

# The backgrounds every palette is measured on: none reported (the palette's own status-bar background stands in
# for a dark terminal), pure white and black, and the near-white and near-black defaults common terminals ship.
LIGHT_BACKGROUNDS = ("#FFFFFF", "#F6F8FA", "#FDF6E3")
DARK_BACKGROUNDS = ("#000000", "#1E1E1E", "#002B36")
BAND_ROLES = ("ok_band", "error_band")


def luminance(colour: str) -> float:
    """WCAG 2 relative luminance, written out here rather than borrowed from the module under test."""
    rgb = hex_to_rgb(colour)
    assert rgb is not None, colour
    linear = []
    for value in rgb:
        channel = value / 255
        linear.append(channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4)
    red, green, blue = linear
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first: str, second: str) -> float:
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class ThemeTests(unittest.TestCase):
    def test_every_shipped_palette_paints_every_role_but_the_terminals_own_text(self) -> None:
        for skin in SKIN_THEMES:
            for background in (None, *LIGHT_BACKGROUNDS):
                theme = build_theme(skin, background)
                for field in fields(theme):
                    if field.name in ("name", "text", "user"):
                        continue
                    if background is None and field.name in BAND_ROLES:
                        continue  # no band is painted over a background nobody reported
                    with self.subTest(theme=skin.short_name, background=background, role=field.name):
                        style = getattr(theme, field.name)
                        self.assertTrue(style.fg or style.bg, "a role without any colour paints as plain text")

    def test_body_text_and_the_prompt_mark_are_the_terminals_default_foreground(self) -> None:
        # A light terminal showed near-white body text on white (1.08:1); the default foreground is readable on
        # whatever background the person chose.
        for skin in SKIN_THEMES:
            for background in (None, *LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                theme = build_theme(skin, background)
                with self.subTest(theme=skin.short_name, background=background):
                    self.assertIsNone(theme.text.fg)
                    self.assertIsNone(theme.user.fg)
                    self.assertTrue(theme.user.bold)

    def test_every_role_read_as_text_reaches_4_5_and_every_glyph_3_on_every_background(self) -> None:
        for skin in SKIN_THEMES:
            for background in (None, *LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                theme = build_theme(skin, background)
                surface = background or skin_colors(skin.skin_name)["status_bar_bg"]
                for role, minimum in [*((role, 4.5) for role in TEXT_ROLES), *((role, 3.0) for role in GLYPH_ROLES)]:
                    with self.subTest(theme=skin.short_name, background=background, role=role):
                        self.assertGreaterEqual(contrast(getattr(theme, role).fg, surface), minimum)

    def test_a_light_background_gets_light_diff_bands_under_the_default_foreground(self) -> None:
        for skin in SKIN_THEMES:
            for background in LIGHT_BACKGROUNDS:
                theme = build_theme(skin, background)
                for role in ("diff_add", "diff_add_word", "diff_remove", "diff_remove_word"):
                    with self.subTest(theme=skin.short_name, background=background, role=role):
                        style = getattr(theme, role)
                        self.assertIsNone(style.fg)  # the terminal's dark text
                        self.assertGreaterEqual(contrast(style.bg, "#000000"), 7.0)
        dark = build_theme(SKIN_THEMES[0], "#000000")
        self.assertEqual(dark.diff_add.bg, skin_colors(SKIN_THEMES[0].skin_name)["diff_added"])

    def test_a_dark_diff_band_carries_its_own_text_colour(self) -> None:
        # With no background reported the theme assumes dark; a light terminal's own dark text on the dark band
        # read 1.5:1, so the band says what colour its text is, as it did before the light-terminal work.
        for skin in SKIN_THEMES:
            for background in (None, *DARK_BACKGROUNDS):
                theme = build_theme(skin, background)
                for role in ("diff_add", "diff_remove"):
                    with self.subTest(theme=skin.short_name, background=background, role=role):
                        style = getattr(theme, role)
                        self.assertIsNotNone(style.fg)
                        self.assertGreaterEqual(contrast(style.fg, style.bg), 7.0)

    def test_light_is_read_from_the_background_itself(self) -> None:
        self.assertTrue(all(is_light(colour) for colour in LIGHT_BACKGROUNDS))
        self.assertFalse(any(is_light(colour) for colour in DARK_BACKGROUNDS))

    def test_roles_follow_the_palette(self) -> None:
        amber = theme_named("amber")
        self.assertEqual(amber.accent.fg, skin_colors("omh-amber")["ui_accent"])
        self.assertEqual(amber.diff_add.bg, skin_colors("omh-amber")["diff_added"])
        # A colour that clears the background is the palette's own; amber's comments (3.6:1) are lifted for reading.
        for role in ("syntax_keyword", "syntax_string", "syntax_number"):
            with self.subTest(role=role):
                self.assertEqual(getattr(amber, role).fg, skin_colors("omh-amber")[role])
        self.assertNotEqual(amber.syntax_comment.fg, skin_colors("omh-amber")["syntax_comment"])

    def test_keywords_and_comments_differ_from_text_even_where_the_colours_match(self) -> None:
        mono = theme_named("mono")
        self.assertNotEqual(mono.syntax_keyword, mono.text)
        self.assertNotEqual(mono.syntax_comment, mono.text)

    def test_hints_read_at_text_contrast_on_every_palette(self) -> None:
        # With no background reported, the status-bar background stands in for the dark terminal behind the mode row.
        for skin in SKIN_THEMES:
            theme = build_theme(skin)
            background = skin_colors(skin.skin_name).get("status_bar_bg") or theme.status.bg
            with self.subTest(theme=skin.short_name):
                self.assertGreaterEqual(contrast(theme.hint.fg, background), 4.5)
                self.assertGreaterEqual(contrast(theme.muted.fg, background), 4.5)
                self.assertGreaterEqual(contrast(theme.rule.fg, background), 3.0)

    def test_the_three_composer_rules_differ_on_every_palette(self) -> None:
        for skin in SKIN_THEMES:
            theme = build_theme(skin)
            rules = {"normal": theme.rule, "accept": theme.mode_accept, "plan": theme.mode_plan}
            with self.subTest(theme=skin.short_name):
                self.assertEqual(len(set(rules.values())), 3, rules)

    def test_a_palette_that_clears_the_background_keeps_its_own_colours(self) -> None:
        sky = theme_named("sky")
        self.assertEqual(sky.rule.fg, skin_colors("omh")["input_rule"])
        self.assertEqual(sky.accent.fg, skin_colors("omh")["ui_accent"])
        self.assertNotEqual(sky.muted.fg, skin_colors("omh")["banner_dim"])  # 3.5:1, lifted for text
        crimson = theme_named("crimson")
        self.assertNotEqual(crimson.rule.fg, skin_colors("omh-crimson")["input_rule"])
        self.assertNotEqual(theme_named("sky", "#FFFFFF").accent.fg, skin_colors("omh")["ui_accent"])  # 2.0:1 on white

    def test_names_resolve_by_short_or_skin_name_and_default_otherwise(self) -> None:
        self.assertEqual(theme_named("omh-crimson").name, "crimson")
        self.assertEqual(theme_named("crimson").name, "crimson")
        self.assertEqual(theme_named("not-a-theme"), DEFAULT_THEME)
        self.assertEqual(DEFAULT_THEME.name, "sky")


class StatusBarContrastTests(unittest.TestCase):
    """Words on the status line are measured against the status bar they sit on, not the terminal's background."""

    def line(self, background: str | None, skin: object, stage: str, yolo: bool, rows: int) -> list:
        from test_tui_modes import make_app
        from test_tui_panels import hud_payload
        from omh.tui.panels import HudSnapshot

        theme = build_theme(skin, background)  # type: ignore[arg-type]
        app, _client = make_app(theme=theme, rows=rows, cols=200)
        payload = hud_payload()
        payload["repeat"] = {"status": "observed", "consecutive": 4, "period": 2, "intercepted": 1, "stage": stage}
        payload["activity"].update(live=True, post_tool_call_observed=True, open_call_count=2, oldest_open_elapsed_seconds=12)
        payload["yolo"] = {"status": "observed", "enabled": yolo}
        app.hud = HudSnapshot(app.stored_id, payload, 0.0)
        app.approvals_off = app.plan_mode = True  # the mode chips, on a terminal too short for the mode row
        app.model, app.context, app.context_percent = "some/model", "ctx 96k/100k 96%", 96.0
        return [(span, theme.status.bg) for span in app._compose_status_line(200)]

    def test_every_coloured_word_on_the_status_line_reaches_4_5_against_the_bar(self) -> None:
        for skin in SKIN_THEMES:
            for background in (None, *LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                for stage, yolo, rows in (("watching", False, 24), ("blocking", True, 6), ("approval", True, 6)):
                    for span, bar in self.line(background, skin, stage, yolo, rows):
                        if not span.text.strip() or span.style.fg is None:
                            continue
                        minimum = 4.5 if any(char.isalnum() for char in span.text) else 3.0
                        with self.subTest(theme=skin.short_name, background=background, stage=stage, text=span.text):
                            self.assertEqual(span.style.bg, bar)
                            self.assertGreaterEqual(contrast(span.style.fg, bar), minimum)


class StatusBarTintTests(unittest.TestCase):
    """The audit's G8: on a light terminal the status line was the palette's dark bar, a band across a light screen."""

    def test_a_light_terminal_gets_a_light_bar_of_the_palettes_own_and_a_dark_or_unknown_one_keeps_it(self) -> None:
        tints = set()
        for skin in SKIN_THEMES:
            own = build_theme(skin, None).status.bg  # no background reported: the palette's bar
            assert own is not None
            self.assertFalse(is_light(own), skin.short_name)
            for background in DARK_BACKGROUNDS:
                with self.subTest(theme=skin.short_name, background=background):
                    self.assertEqual(build_theme(skin, background).status.bg, own)
            for background in LIGHT_BACKGROUNDS:
                with self.subTest(theme=skin.short_name, background=background):
                    bar = build_theme(skin, background).status.bg
                    assert bar is not None
                    self.assertTrue(is_light(bar), bar)
                    self.assertNotEqual(bar.upper(), background.upper())  # still a bar, not the background
                    if background == "#FFFFFF":
                        tints.add(bar)
        self.assertGreater(len(tints), 1)  # each palette tints its own bar, not one grey for all


class DimCarriesNoWordsTests(unittest.TestCase):
    """`dim` reaches only the 3:1 a glyph needs, so no span the TUI draws in it may hold words."""

    def test_no_span_written_out_in_dim_holds_a_letter_or_a_digit(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src" / "tui"
        found = []
        for path in sorted(source.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Span" and len(node.args) == 2):
                    continue
                style = node.args[1]
                if not (isinstance(style, ast.Attribute) and style.attr == "dim"):
                    continue
                text = node.args[0]
                parts = [text] if isinstance(text, ast.Constant) else text.values if isinstance(text, ast.JoinedStr) else []
                words = "".join(part.value for part in parts if isinstance(part, ast.Constant) and isinstance(part.value, str))
                if any(ch.isalnum() for ch in words):
                    found.append(f"{path.name}:{node.lineno} {words!r}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
