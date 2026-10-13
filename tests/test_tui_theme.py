"""The OMH TUI's role-based theme over the shipped palettes."""

from __future__ import annotations

from dataclasses import fields
import unittest

from omh.skin_pack import SKIN_THEMES, skin_colors
from omh.tui.theme import DEFAULT_THEME, build_theme, theme_named


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

    def test_names_resolve_by_short_or_skin_name_and_default_otherwise(self) -> None:
        self.assertEqual(theme_named("omh-crimson").name, "crimson")
        self.assertEqual(theme_named("crimson").name, "crimson")
        self.assertEqual(theme_named("not-a-theme"), DEFAULT_THEME)
        self.assertEqual(DEFAULT_THEME.name, "sky")


if __name__ == "__main__":
    unittest.main()
