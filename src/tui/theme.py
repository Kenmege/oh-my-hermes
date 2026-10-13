"""The OMH TUI's colours, by role, from the same palettes `omh theme` ships.

Every renderer asks for a role (`theme.dim`, `theme.tool`, `theme.diff_add`)
instead of a colour, so a palette change is one lookup and no renderer knows a
hex value. The palettes are the shipped skin files read through
`skin_pack.skin_colors`; a key a palette lacks falls back to the default
palette's value, so a partial skin still paints every role.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..skin_pack import SkinTheme, default_theme, skin_colors, theme_for_name, theme_for_skin_name
from .style import Style


@dataclass(frozen=True)
class Theme:
    name: str
    text: Style
    dim: Style
    accent: Style
    label: Style
    ok: Style
    warn: Style
    error: Style
    tool: Style
    thinking: Style
    rule: Style
    prompt: Style
    user: Style
    heading: Style
    code: Style
    link: Style
    quote: Style
    diff_add: Style
    diff_add_word: Style
    diff_remove: Style
    diff_remove_word: Style
    status: Style
    status_dim: Style
    status_good: Style
    status_warn: Style
    status_bad: Style
    menu: Style
    menu_current: Style


def _palette(skin: SkinTheme) -> dict[str, str]:
    base = skin_colors(default_theme().skin_name)
    return {**base, **skin_colors(skin.skin_name)}


def build_theme(skin: SkinTheme) -> Theme:
    """Map one shipped palette onto the TUI's roles."""
    c = _palette(skin)
    status_bg = c["status_bar_bg"]
    return Theme(
        name=skin.short_name,
        text=Style(fg=c["banner_text"]),
        dim=Style(fg=c["banner_dim"]),
        accent=Style(fg=c["ui_accent"]),
        label=Style(fg=c["ui_label"]),
        ok=Style(fg=c["ui_ok"]),
        warn=Style(fg=c["ui_warn"]),
        error=Style(fg=c["ui_error"]),
        tool=Style(fg=c["ui_tool"]),
        thinking=Style(fg=c["ui_thinking"], italic=True),
        rule=Style(fg=c["input_rule"]),
        prompt=Style(fg=c["prompt"], bold=True),
        user=Style(fg=c["session_label"], bold=True),
        heading=Style(fg=c["banner_title"], bold=True),
        code=Style(fg=c["syntax_string"]),
        link=Style(fg=c["ui_label"], underline=True),
        quote=Style(fg=c["syntax_comment"], italic=True),
        diff_add=Style(bg=c["diff_added"]),
        diff_add_word=Style(fg=c["diff_added_word"], bg=c["diff_added"]),
        diff_remove=Style(bg=c["diff_removed"]),
        diff_remove_word=Style(fg=c["diff_removed_word"], bg=c["diff_removed"]),
        status=Style(fg=c["status_bar_text"], bg=status_bg),
        status_dim=Style(fg=c["status_bar_dim"], bg=status_bg),
        status_good=Style(fg=c["status_bar_good"], bg=status_bg),
        status_warn=Style(fg=c["status_bar_warn"], bg=status_bg),
        status_bad=Style(fg=c["status_bar_critical"], bg=status_bg),
        menu=Style(fg=c["banner_text"], bg=c["completion_menu_bg"]),
        menu_current=Style(fg=c["banner_text"], bg=c["completion_menu_current_bg"], bold=True),
    )


def theme_named(value: str) -> Theme:
    """The theme for a short name (`amber`) or skin name (`omh-amber`); the default for anything else."""
    skin = theme_for_name(value) or theme_for_skin_name(value) or default_theme()
    return build_theme(skin)


DEFAULT_THEME = build_theme(default_theme())
