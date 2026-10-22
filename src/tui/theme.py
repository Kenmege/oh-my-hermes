"""The OMH TUI's colours, by role, from the same palettes `omh theme` ships.

Every renderer asks for a role (`theme.muted`, `theme.tool`, `theme.diff_add`,
`theme.syntax_keyword`) instead of a colour, so a palette change is one lookup
and no renderer knows a hex value. The palettes are the shipped skin files
read through `skin_pack.skin_colors`; a key a palette lacks falls back to the
default palette's value, so a partial skin still paints every role. Keywords
are bold and comments italic as well as coloured, so the `mono` palette, whose
keyword and text colours nearly match, still tells them apart.

The terminal's background decides the rest. Body text (`text`, `user`, the
reply) and a tool call's name (`tool`, bold) have no colour of their own: they
are the terminal's default foreground, which is readable on whatever
background the person chose. Every other role is measured against that background
(`build_theme(skin, background)`: the colour the terminal reported, or the
palette's status-bar background, the dark stand-in, when it reported none).
A role a person reads as text reaches WCAG's 4.5:1 (`TEXT_ROLES`); a role
drawn only as a glyph or an edge (`dim`, the composer rule and prompt mark)
reaches the 3:1 a graphical mark needs. Each starts from the palette's own
colour and only moves toward the ink (the palette's `banner_text` on a dark
background, black on a light one) as far as that takes, so a palette that
already clears the background keeps its colour. On a light background the
diff bands are tints of the palette's word colours over the background, so
the default foreground stays readable on them; a dark diff band names its own
text colour (`banner_text`), since the dark it assumes may be a guess. `dim` is
for decoration glyphs; words go on `muted` or `hint`.

Hue is kept for what it says (owner's pick of 2026-10-22, the rail with kind
colours), so the transcript's chrome is neutral grey: a call's arguments on
`muted`, the dimmer duration and what it printed on `faint` (and `hint`), its
glyphs and the composer's rules on `dim` and `rule`, each lifted from a fixed
neutral grey (`NEUTRAL_GREYS`) toward white or black only as far as its floor
takes. State is hue: `accent`, a running call and everything that says a turn
or an agent is running, is the blue a running node takes in the delegation
view (`dag_edge_end`, lifted to 4.5:1), `ok` green and `error` red. The
palette's own accent is the banner title's alone (`brand`). A call's name is
drawn in its tool family's hue (`kinds`, in `KIND_FAMILIES` order, from
`KIND_HUES`), kept clear of every state hue, and a `/name` that resolves to a
command or skill takes the skill hue, bold (`slash`).

Outcomes sit on bands (`ok_band` behind what the person allowed,
`error_band` behind a denial or a failure): light tints of the palette's diff
colours on a light background, its dark diff colours on a dark one, and
none when no background was reported. What is drawn on a band takes
`on_band(theme, band)`, the roles measured against that band, so its words
reach 4.5:1 and its marks 3:1 on it.

The delegation view's edges step through a gradient (`dag_edges`, from the
top of the drawing to the bottom): the palette's `dag_edge_start`,
`dag_edge_mid` and `dag_edge_end` when it names them, else the violet, indigo
and blue of `DAG_EDGE_STOPS`. Each step is a graphical mark, so it is lifted to
3:1 like `dim`: toward black on a light background, toward the palette's light
ink on a dark one, never the other way. `dag_words` are the same steps lifted
to 4.5:1, for the words a box draws in its state's hue (its title and status).

The pager's footer sits on a bar of its own (`status`): the palette's
status-bar background on a dark terminal or one that reported none, and on
a light one a light tint of that colour (`LIGHT_STATUS_SHARE`), so the bar
belongs to a light screen instead of being a dark band across it. Its words
are measured against that bar instead of the terminal's background. The
info line under the composer and the sticky prompt over a running turn have
no bar: their words and marks are measured against the terminal's background
like any transcript row.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ..skin_pack import (
    SkinTheme,
    default_theme,
    hex_to_rgb,
    skin_colors,
    skin_logo,
    theme_for_name,
    theme_for_skin_name,
)
from .escapes import strip_escapes
from .style import Line, Span, Style

TEXT_CONTRAST = 4.5  # WCAG 2 body text
EDGE_CONTRAST = 3.0  # WCAG 2 non-text: a rule, an edge, a secondary mark
_LIFT_STEPS = 20
BLACK = "#000000"
WHITE = "#FFFFFF"
# The background a forced light theme is measured against when the terminal reported none that is light.
LIGHT_STAND_IN = WHITE
# How far a light background's diff tint moves from the word colour toward the background: the line band and the
# word band inside it.
LIGHT_BAND_SHARE = 0.82
LIGHT_WORD_BAND_SHARE = 0.62
# A light background's status bar: the palette's status-bar colour moved this far toward the background.
LIGHT_STATUS_SHARE = 0.85
# The delegation view's edge gradient, top to bottom, for a palette that names none of these tokens.
DAG_EDGE_STOPS = {"dag_edge_start": "#8B5CF6", "dag_edge_mid": "#6366F1", "dag_edge_end": "#3B82F6"}
DAG_EDGE_STEPS = 9  # an odd count, so the middle stop is a step of its own
# The roles a person reads as text: each reaches TEXT_CONTRAST against the background. `text`, `user` and `tool`
# are not here because they are the terminal's own foreground.
TEXT_ROLES = (
    "accent", "label", "ok", "warn", "error", "thinking", "heading", "code", "link", "quote",
    "syntax_keyword", "syntax_string", "syntax_number", "syntax_comment", "hint", "muted", "faint", "mode_accept",
    "mode_plan", "brand", "slash",
)
# The roles drawn only as a glyph or an edge: each reaches EDGE_CONTRAST.
GLYPH_ROLES = ("dim", "rule", "prompt")
# The transcript's neutral greys, (on a dark background, on a light one): a call's arguments (`muted`), the dimmer
# words under and beside it (`faint`, `hint`, thinking), and its glyphs and the composer's rules (`dim`, `rule`).
NEUTRAL_GREYS = {"muted": ("#9198A1", "#59636E"), "faint": ("#7D8590", "#6E7781"), "glyph": ("#545D68", "#8C959F")}
# The tool families a call's name is coloured by, and each family's hue (on a dark background, on a light one).
# Each keeps 23 degrees of OKLCH hue or more from the state hues (running blue, failed red, done green), the
# banner's turquoise and the delegation view's selected violet; tests/test_tui_theme.py holds each to 4.5:1.
KIND_FAMILIES = ("skill", "shell", "file", "web", "dag")
KIND_HUES = {
    "skill": ("#D58CF0", "#9E58B8"), "shell": ("#E3B341", "#996C00"), "file": ("#F8A87E", "#AB6239"),
    "web": ("#C5DE70", "#687C00"), "dag": ("#F778BA", "#BF4588"),
}


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
    menu: Style
    menu_current: Style
    hint: Style
    muted: Style
    mode_accept: Style
    mode_plan: Style
    ok_band: Style  # behind what the person allowed (an approval)
    error_band: Style  # behind a denial or a failure: a denied approval, a failed tool row and its lines, an error notice
    dag_edges: tuple[Style, ...]  # the delegation view's edges, top layer to bottom (`DAG_EDGE_STEPS` of them)
    dag_words: tuple[Style, ...]  # the same steps lifted to TEXT_CONTRAST: a box's title and status words
    faint: Style  # a call's duration and what it printed, dimmer than its arguments on `muted`
    brand: Style  # the banner's title, bold: the palette's accent, which no other role uses
    kinds: tuple[Style, ...]  # a call's name, bold, in its tool family's hue, in `KIND_FAMILIES` order
    slash: Style  # a `/name` that resolves to a command or skill, bold, in the composer and your prompt's rows


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
    return {**DAG_EDGE_STOPS, **base, **skin_colors(skin.skin_name)}


def _gradient(stops: list[str], steps: int) -> list[str]:
    """`steps` colours evenly through `stops`, the first stop first and the last stop last."""
    colours = []
    for step in range(steps):
        at = step / (steps - 1) * (len(stops) - 1)
        index = min(int(at), len(stops) - 2)
        colours.append(_mix(stops[index], stops[index + 1], at - index))
    return colours


def is_light(background: str) -> bool:
    """Whether dark text reads better than light text on `background` (`#rrggbb`)."""
    return contrast_ratio(background, BLACK) > contrast_ratio(background, WHITE)


def surface_for(skin: SkinTheme, background: str | None) -> str:
    """What the roles are measured against: `background` when it is a plain hex, else the palette's status-bar background."""
    return background if background and hex_to_rgb(background) else _palette(skin)["status_bar_bg"]


def lifted_colour(colour: str, surface: str, minimum: float, ink: str) -> str:
    """`colour` moved toward `ink` only as far as reaching `minimum` against `surface` takes."""
    return _lifted(colour, ink, surface, minimum)


def build_theme(skin: SkinTheme, background: str | None = None) -> Theme:
    """Map one shipped palette onto the TUI's roles, measured against `background` (see the module docstring)."""
    c = _palette(skin)
    surface = surface_for(skin, background)
    reported = surface == background  # the terminal said what its background is
    light = is_light(surface)
    ink = BLACK if light else c["banner_text"]
    status_bg = _mix(c["status_bar_bg"], surface, LIGHT_STATUS_SHARE) if reported and light else c["status_bar_bg"]

    def read(key: str) -> str:
        return _lifted(c[key], ink, surface, TEXT_CONTRAST)

    side = 1 if light else 0

    def grey(key: str, minimum: float) -> str:
        return _lifted(NEUTRAL_GREYS[key][side], BLACK if light else WHITE, surface, minimum)

    def hue(family: str) -> str:
        return _lifted(KIND_HUES[family][side], ink, surface, TEXT_CONTRAST)

    def mark(key: str) -> str:
        return _lifted(c[key], ink, surface, EDGE_CONTRAST)

    status_ink = BLACK if is_light(status_bg) else c["banner_text"]

    def on_bar(key: str, minimum: float = TEXT_CONTRAST) -> Style:
        return Style(fg=_lifted(c[key], status_ink, status_bg, minimum), bg=status_bg)

    if not reported:
        ok_band = error_band = Style()
    elif light:
        ok_band = Style(bg=_mix(c["diff_added_word"], surface, LIGHT_BAND_SHARE))
        error_band = Style(bg=_mix(c["diff_removed_word"], surface, LIGHT_BAND_SHARE))
    else:
        ok_band, error_band = Style(bg=c["diff_added"]), Style(bg=c["diff_removed"])
    if light:
        diff_add = Style(bg=_mix(c["diff_added_word"], surface, LIGHT_BAND_SHARE))
        diff_add_word = Style(bg=_mix(c["diff_added_word"], surface, LIGHT_WORD_BAND_SHARE), bold=True)
        diff_remove = Style(bg=_mix(c["diff_removed_word"], surface, LIGHT_BAND_SHARE))
        diff_remove_word = Style(bg=_mix(c["diff_removed_word"], surface, LIGHT_WORD_BAND_SHARE), bold=True)
    else:
        diff_add = Style(fg=c["banner_text"], bg=c["diff_added"])
        diff_add_word = Style(fg=c["diff_added_word"], bg=c["diff_added"])
        diff_remove = Style(fg=c["banner_text"], bg=c["diff_removed"])
        diff_remove_word = Style(fg=c["diff_removed_word"], bg=c["diff_removed"])
    return Theme(
        name=skin.short_name,
        text=Style(),
        dim=Style(fg=grey("glyph", EDGE_CONTRAST)),
        accent=Style(fg=read("dag_edge_end")),
        label=Style(fg=read("ui_label")),
        ok=Style(fg=read("ui_ok")),
        warn=Style(fg=read("ui_warn")),
        error=Style(fg=read("ui_error")),
        tool=Style(bold=True),
        thinking=Style(fg=grey("faint", TEXT_CONTRAST), italic=True),
        rule=Style(fg=grey("glyph", EDGE_CONTRAST)),
        prompt=Style(fg=mark("prompt"), bold=True),
        user=Style(bold=True),
        heading=Style(fg=read("banner_title"), bold=True),
        code=Style(fg=read("syntax_string")),
        link=Style(fg=read("ui_label"), underline=True),
        quote=Style(fg=read("syntax_comment"), italic=True),
        diff_add=diff_add,
        diff_add_word=diff_add_word,
        diff_remove=diff_remove,
        diff_remove_word=diff_remove_word,
        syntax_keyword=Style(fg=read("syntax_keyword"), bold=True),
        syntax_string=Style(fg=read("syntax_string")),
        syntax_number=Style(fg=read("syntax_number")),
        syntax_comment=Style(fg=read("syntax_comment"), italic=True),
        status=on_bar("status_bar_text"),
        menu=Style(fg=c["banner_text"], bg=c["completion_menu_bg"]),
        menu_current=Style(fg=c["banner_text"], bg=c["completion_menu_current_bg"], bold=True),
        hint=Style(fg=grey("faint", TEXT_CONTRAST)),
        muted=Style(fg=grey("muted", TEXT_CONTRAST)),
        mode_accept=Style(fg=read("ui_warn")),
        mode_plan=Style(fg=read("ui_ok")),
        ok_band=ok_band,
        error_band=error_band,
        dag_edges=tuple(
            Style(fg=_lifted(colour, ink, surface, EDGE_CONTRAST))
            for colour in _gradient([c[key] for key in DAG_EDGE_STOPS], DAG_EDGE_STEPS)
        ),
        dag_words=tuple(
            Style(fg=_lifted(colour, ink, surface, TEXT_CONTRAST))
            for colour in _gradient([c[key] for key in DAG_EDGE_STOPS], DAG_EDGE_STEPS)
        ),
        faint=Style(fg=grey("faint", TEXT_CONTRAST)),
        brand=Style(fg=read("ui_accent"), bold=True),
        kinds=tuple(Style(fg=hue(family), bold=True) for family in KIND_FAMILIES),
        slash=Style(fg=hue("skill"), bold=True),
    )


def logo_lines(skin: SkinTheme, background: str | None = None) -> list[Line]:
    """The skin's block logo (`skin_pack.skin_logo`), each row in its own colour, measured against `background`.

    A row keeps the skin's gradient colour where it reaches the 3:1 a
    graphical mark needs and moves toward the ink only as far as that takes,
    as every glyph role does; a row the skin gives no colour takes
    `banner_title`. Escape sequences and control characters are taken out of
    the text, so a skin file never writes to the terminal.
    """
    c = _palette(skin)
    surface = surface_for(skin, background)
    ink = BLACK if is_light(surface) else c["banner_text"]
    lines: list[Line] = []
    for text, colour, bold in skin_logo(skin.skin_name):
        shown = "".join(char for char in strip_escapes(text) if char.isprintable())
        fg = _lifted(colour if hex_to_rgb(colour) else c["banner_title"], ink, surface, EDGE_CONTRAST)
        lines.append((Span(shown, Style(fg=fg, bold=bold)),))
    return lines


@lru_cache(maxsize=32)
def on_band(theme: Theme, band: Style) -> Theme:
    """`theme`'s roles measured against `band`'s background, for the words and marks drawn on that band."""
    skin = theme_for_name(theme.name) or default_theme()
    return build_theme(skin, band.bg)


def theme_named(value: str, background: str | None = None) -> Theme:
    """The theme for a short name (`amber`) or skin name (`omh-amber`), the default for anything else, against `background`."""
    skin = theme_for_name(value) or theme_for_skin_name(value) or default_theme()
    return build_theme(skin, background)


DEFAULT_THEME = build_theme(default_theme())
