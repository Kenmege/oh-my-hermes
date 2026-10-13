"""The OMH TUI's role-based theme over the shipped palettes."""

from __future__ import annotations

from dataclasses import fields
import unittest

from omh.skin_pack import SKIN_THEMES, hex_to_rgb, skin_colors
from omh.tui.theme import DEFAULT_THEME, build_theme, theme_named


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
    def test_every_shipped_palette_paints_every_role(self) -> None:
        for skin in SKIN_THEMES:
            theme = build_theme(skin)
            for field in fields(theme):
                if field.name == "name":
                    continue
                with self.subTest(theme=skin.short_name, role=field.name):
                    style = getattr(theme, field.name)
                    self.assertTrue(style.fg or style.bg, "a role without any colour paints as plain text")

    def test_roles_follow_the_palette(self) -> None:
        amber = theme_named("amber")
        self.assertEqual(amber.accent.fg, skin_colors("omh-amber")["ui_accent"])
        self.assertEqual(amber.diff_add.bg, skin_colors("omh-amber")["diff_added"])
        for role in ("syntax_keyword", "syntax_string", "syntax_number", "syntax_comment"):
            with self.subTest(role=role):
                self.assertEqual(getattr(amber, role).fg, skin_colors("omh-amber")[role])

    def test_keywords_and_comments_differ_from_text_even_where_the_colours_match(self) -> None:
        mono = theme_named("mono")
        self.assertNotEqual(mono.syntax_keyword, mono.text)
        self.assertNotEqual(mono.syntax_comment, mono.text)

    def test_hints_read_at_text_contrast_on_every_palette(self) -> None:
        # The status-bar background stands in for the dark terminal behind the mode row.
        for skin in SKIN_THEMES:
            theme = build_theme(skin)
            background = skin_colors(skin.skin_name).get("status_bar_bg") or theme.status.bg
            with self.subTest(theme=skin.short_name):
                self.assertGreaterEqual(contrast(theme.hint.fg, background), 4.5)
                self.assertGreaterEqual(contrast(theme.muted.fg, background), 3.0)
                self.assertGreaterEqual(contrast(theme.rule.fg, background), 3.0)

    def test_the_three_composer_rules_differ_on_every_palette(self) -> None:
        for skin in SKIN_THEMES:
            theme = build_theme(skin)
            rules = {"normal": theme.rule, "accept": theme.mode_accept, "plan": theme.mode_plan}
            with self.subTest(theme=skin.short_name):
                self.assertEqual(len(set(rules.values())), 3, rules)

    def test_a_palette_that_clears_the_bar_keeps_its_own_colours(self) -> None:
        sky = theme_named("sky")
        self.assertEqual(sky.rule.fg, skin_colors("omh")["input_rule"])
        self.assertEqual(sky.muted.fg, skin_colors("omh")["banner_dim"])
        crimson = theme_named("crimson")
        self.assertNotEqual(crimson.rule.fg, skin_colors("omh-crimson")["input_rule"])

    def test_names_resolve_by_short_or_skin_name_and_default_otherwise(self) -> None:
        self.assertEqual(theme_named("omh-crimson").name, "crimson")
        self.assertEqual(theme_named("crimson").name, "crimson")
        self.assertEqual(theme_named("not-a-theme"), DEFAULT_THEME)
        self.assertEqual(DEFAULT_THEME.name, "sky")


if __name__ == "__main__":
    unittest.main()
