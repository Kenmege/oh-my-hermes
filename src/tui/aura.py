"""The aura a `dag-ulw` in the composer draws (owner request 2026-10-23, after Claude Code's `ultrathink`).

While the draft holds `dag-ulw`, `dag ulw` or `/dag-ulw` (any case, a whole
token: `dag-ulwx`, `undag ulw` and a path's `src/dag-ulw/` are none), the
token is drawn in a purple gradient that moves across its letters a step a
tick, and the composer's two rules take the same gradient at a rule's
contrast (`Theme.aura`, `Theme.aura_rule`). Nothing else moves: the rows,
the caret and the cells are the draft's own.

It moves only at the truecolor depth with motion on, and only while the
token is there, so the app ticks for it then and never otherwise
(`App._aura_live`). At 256 or 16 colours the gradient stands still; at the
`NO_COLOR` depth the token is bold and the rules are plain.
"""

from __future__ import annotations

from collections.abc import Sequence
import re

from .style import Line, Span, Style

# A whole `dag-ulw`, `dag ulw` or `/dag-ulw` token: nothing a word or a path goes on with on either side.
_KEYWORD = re.compile(r"(?<![\w./-])/?dag[- ]ulw(?![\w/-])", re.IGNORECASE)


def aura_ranges(text: str) -> list[tuple[int, int]]:
    """(start, end) str indexes of each `dag-ulw` token in `text`."""
    return [(found.start(), found.end()) for found in _KEYWORD.finditer(text)]


def aura_style(styles: Sequence[Style], offset: int, phase: int) -> Style:
    """The style of the token's cell `offset` cells in at `phase`: the gradient moves one step a phase."""
    return styles[(offset - phase) % len(styles)]


def aura_rule(width: int, styles: Sequence[Style], rule: str, phase: int) -> Line:
    """A composer rule `width` cells wide in the gradient, a step every few cells, moving one step a phase."""
    if width <= 0 or not styles:
        return ()
    step = max(width // len(styles), 1)
    spans: list[Span] = []
    for at in range(0, width, step):
        style = aura_style(styles, at // step, phase)
        text = rule * min(step, width - at)
        if spans and spans[-1].style == style:
            spans[-1] = Span(spans[-1].text + text, style)
        else:
            spans.append(Span(text, style))
    return tuple(spans)
