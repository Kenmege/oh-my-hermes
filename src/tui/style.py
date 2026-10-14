"""Styled text for the OMH TUI: a `Line` is a tuple of `Span`s, each with a `Style`.

Colours are `#rrggbb` strings, the same tokens `skin_pack.skin_colors` hands
out, and are painted as 24-bit SGR (38;2 / 48;2). A colour that is not a plain
hex token is skipped rather than guessed, so a bad palette entry shows default
colour instead of a wrong one.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..skin_pack import hex_to_rgb

CSI = "\x1b["
SGR_RESET = f"{CSI}0m"

_BOLD = "1"
_DIM = "2"
_ITALIC = "3"
_UNDERLINE = "4"
_REVERSE = "7"
_FOREGROUND_RGB = "38;2"
_BACKGROUND_RGB = "48;2"


def _colour_parameter(prefix: str, value: str | None) -> str:
    if value is None:
        return ""
    rgb = hex_to_rgb(value)
    if rgb is None:
        return ""
    red, green, blue = rgb
    return f"{prefix};{red};{green};{blue}"


@dataclass(frozen=True)
class Style:
    fg: str | None = None
    bg: str | None = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False

    def sgr(self) -> str:
        """The SGR sequence that turns this style on; "" for the default style."""
        parameters = [
            code
            for enabled, code in (
                (self.bold, _BOLD),
                (self.dim, _DIM),
                (self.italic, _ITALIC),
                (self.underline, _UNDERLINE),
                (self.reverse, _REVERSE),
            )
            if enabled
        ]
        for prefix, value in ((_FOREGROUND_RGB, self.fg), (_BACKGROUND_RGB, self.bg)):
            parameter = _colour_parameter(prefix, value)
            if parameter:
                parameters.append(parameter)
        if not parameters:
            return ""
        return f"{CSI}{';'.join(parameters)}m"


DEFAULT_STYLE = Style()


@dataclass(frozen=True)
class Span:
    text: str
    style: Style = DEFAULT_STYLE


Line = tuple[Span, ...]


def fill(style: Style) -> Span:
    """The span that ends a row whose rest takes `style`'s background, to the right edge (`screen.paint_line`)."""
    return Span("", style)


def is_fill(span: Span) -> bool:
    return not span.text and span.style.bg is not None
