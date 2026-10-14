"""The OMH TUI's colours, by role, from the same palettes `omh theme` ships.

Every renderer asks for a role (`theme.dim`, `theme.tool`, `theme.diff_add`,
`theme.syntax_keyword`) instead of a colour, so a palette change is one lookup
and no renderer knows a hex value. The palettes are the shipped skin files
read through `skin_pack.skin_colors`; a key a palette lacks falls back to the
default palette's value, so a partial skin still paints every role. Keywords
are bold and comments italic as well as coloured, so the `mono` palette, whose
keyword and text colours nearly match, still tells them apart.

Three roles are measured rather than looked up. Words a person is meant to
read off the chrome (`hint`: the mode row, the empty composer's placeholder)
reach WCAG's 4.5:1 text contrast against the palette's status-bar
background, a stand-in for the dark terminal behind the TUI; secondary facts
(`muted`) and the composer rule reach the 3:1 a graphical edge needs. Each
starts from the palette's own colour (`banner_dim`, `input_rule`) and only
moves toward `banner_text` as far as that takes, so a palette that already
clears the bar keeps its colour. `dim` stays for decoration glyphs.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..skin_pack import SkinTheme, default_theme, hex_to_rgb, skin_colors, theme_for_name, theme_for_skin_name
from .style import Style

TEXT_CONTRAST = 4.5  # WCAG 2 body text
EDGE_CONTRAST = 3.0  # WCAG 2 non-text: a rule, an edge, a secondary mark
_LIFT_STEPS = 20


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
    syntax_keyword: Style
    syntax_string: Style
    syntax_number: Style
    syntax_comment: Style
    status: Style
    status_dim: Style
    status_good: Style
    status_warn: Style
    status_bad: Style
    menu: Style
    menu_current: Style
    hint: Style
    muted: Style
    mode_accept: Style
    mode_plan: Style


def _channel(value: int) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def contrast_ratio(first: str, second: str) -> float:
    """WCAG 2 contrast between two `#rrggbb` colours, 1.0 to 21.0; 1.0 when either is not a plain hex."""
    rgbs = [hex_to_rgb(first), hex_to_rgb(second)]
    if rgbs[0] is None or rgbs[1] is None:
        return 1.0
    lighter, darker = sorted(
        (0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b) for r, g, b in rgbs), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _mix(start: str, end: str, share: float) -> str:
    one, two = hex_to_rgb(start), hex_to_rgb(end)
    if one is None or two is None:
        return start
    return "#" + "".join(f"{round(a + (b - a) * share):02X}" for a, b in zip(one, two, strict=True))


def _lifted(colour: str, toward: str, background: str, minimum: float) -> str:
    """`colour` moved toward `toward` in twentieths until it reaches `minimum` against `background`."""
    for step in range(_LIFT_STEPS + 1):
        candidate = _mix(colour, toward, step / _LIFT_STEPS)
        if contrast_ratio(candidate, background) >= minimum:
            return candidate
    return toward


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
        rule=Style(fg=_lifted(c["input_rule"], c["banner_text"], status_bg, EDGE_CONTRAST)),
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
        syntax_keyword=Style(fg=c["syntax_keyword"], bold=True),
        syntax_string=Style(fg=c["syntax_string"]),
        syntax_number=Style(fg=c["syntax_number"]),
        syntax_comment=Style(fg=c["syntax_comment"], italic=True),
        status=Style(fg=c["status_bar_text"], bg=status_bg),
        status_dim=Style(fg=c["status_bar_dim"], bg=status_bg),
        status_good=Style(fg=c["status_bar_good"], bg=status_bg),
        status_warn=Style(fg=c["status_bar_warn"], bg=status_bg),
        status_bad=Style(fg=c["status_bar_critical"], bg=status_bg),
        menu=Style(fg=c["banner_text"], bg=c["completion_menu_bg"]),
        menu_current=Style(fg=c["banner_text"], bg=c["completion_menu_current_bg"], bold=True),
        hint=Style(fg=_lifted(c["banner_dim"], c["banner_text"], status_bg, TEXT_CONTRAST)),
        muted=Style(fg=_lifted(c["banner_dim"], c["banner_text"], status_bg, EDGE_CONTRAST)),
        mode_accept=Style(fg=c["ui_warn"]),
        mode_plan=Style(fg=c["ui_ok"]),
    )


def theme_named(value: str) -> Theme:
    """The theme for a short name (`amber`) or skin name (`omh-amber`); the default for anything else."""
    skin = theme_for_name(value) or theme_for_skin_name(value) or default_theme()
    return build_theme(skin)


DEFAULT_THEME = build_theme(default_theme())
