"""Draw a `delegation_graph/v1` as text: a layered box DAG, or a compact tree when boxes do not fit.

Pure: no I/O, no clock, nothing imported from `omh`. The OMH TUI draws the
lines with its own styles (every piece of text carries a role), and `omh hud
--graph` joins them as text. A chat reply never carries this drawing
(`delegation_snapshot.py` writes its own short text).

The box layout draws an edge only where the graph records one: a dispatch from
a parent to a child, a `depends_on`, or a dag node added mid-run hanging from
where it came from (a repair or an extend). Nodes are layered by their longest
path from the root, so every edge it draws goes one layer down. It draws
three shapes and nothing else, because each is unambiguous when read:

* a fan-out or fan-in, where one bus joins every upper node to every lower
  node of the band and that band is the only one between the two layers
  (the bus says "all of these to all of those", which is then true);
* several fan-outs side by side, one per parent, each kept under its own
  parent so no two buses touch;
* a layer too wide for one row, wrapped into more rows that a trunk on the
  left feeds (and, when the layer feeds a fan-in, a trunk on the right
  collects). Dispatch waves of one parent also start a new row each.

Anything else (an edge that skips a layer, a band that is not all-to-all
within a component, buses that would cross) is drawn as the compact tree,
which draws no edge but nests a node under the one that dispatched it, or
under the one a repair or an extend added it for. So is a graph whose boxes
do not fit the width, the line budget or the character budget. Widths are
terminal cells: a wide (CJK) character counts 2, and text is folded first so
that what is counted is what is painted.

A box holds four lines: its title, category, model and effort, and status.
Where four do not fit the line budget and the caller allows it (``compact``),
the same layout is tried with two-line boxes first, title then category and
model, before the tree: the glyph and, in the TUI, the border colour say the
state. A box is as wide as its widest line, with a duration counted at
`DURATION_CELLS` whatever it reads, so a ticking clock never resizes a box.

Choosing a layout tries many widths. The choice is kept per structure (node
ids, labels, states and edges, never elapsed time) and size, so a redraw of
the same graph with new durations lays it out once (`_DECISIONS`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Final
import unicodedata

from .dag_core import layer_of
from .hud_text import elapsed_text

VIEW_LAYOUTS: Final = ("box", "compact", "tree", "header")
NODE_STATES: Final = (
    "root", "waiting", "dispatched", "running", "stuck", "done", "failed", "interrupted", "unknown",
)
UNKNOWN_FIELD: Final = "?"
# The edges the tree draws as nesting: a child under its dispatcher, an added dag node under where it came from.
NESTING_KINDS: Final = frozenset({"dispatch", "repair", "extend"})
ELAPSED_LIMIT: Final = 10**9  # seconds, about 31 years: a figure past it (or not finite) is not drawn
DURATION_CELLS: Final = 7  # a duration's width when a box is measured: `59m 59s`, the widest under 100 hours
DECISION_LIMIT: Final = 64

GAP: Final = 2  # cells between two boxes of a row
GUTTER: Final = 3  # cells a trunk takes beside wrapped rows; the trunk runs in the middle one
INNER_MIN: Final = 14
INNER_COMFORT: Final = 16
INNER_MAX: Final = 26
ROOT_INNER_MAX: Final = 30
TREE_WIDE_MIN: Final = 72  # below this a tree node takes two lines
# Below this many cells boxes are not drawn at all. The owner's mock-up as one column of boxes with both
# trunks takes 39 cells; narrower, a box's title keeps only a few characters and the tree reads better.
BOX_MIN_WIDTH: Final = 36
TREE_TITLE_MAX: Final = 40
TREE_CATEGORY_MAX: Final = 12
TREE_MODEL_MAX: Final = 24
TREE_LEAD_ROOM: Final = 16  # cells a tree row keeps past its lead: the branch, the glyph and the start of a title
LABEL_MIN: Final = 6

_L, _R, _U, _D = 1, 2, 4, 8


@dataclass(frozen=True)
class Charset:
    name: str
    lines: Mapping[int, str]
    arrow: str
    glyphs: Mapping[str, str]
    separator: str
    ellipsis: str
    tee: str
    end: str
    bar: str
    gap: str


UNICODE: Final = Charset(
    name="unicode",
    lines={
        _L | _R: "─", _U | _D: "│", _R | _D: "┌", _L | _D: "┐", _R | _U: "└", _L | _U: "┘",
        _L | _R | _D: "┬", _L | _R | _U: "┴", _U | _D | _R: "├", _U | _D | _L: "┤", _L | _R | _U | _D: "┼",
        _L: "─", _R: "─", _U: "│", _D: "│",
    },
    arrow="▼",
    glyphs={
        "root": "◆", "waiting": "·", "dispatched": "◌", "running": "●", "stuck": "!", "done": "✓",
        "failed": "✗", "interrupted": "▪", "unknown": "?",
    },
    separator=" · ",
    ellipsis="…",
    tee="├─ ",
    end="└─ ",
    bar="│  ",
    gap="   ",
)
ASCII: Final = Charset(
    name="ascii",
    lines={
        _L | _R: "-", _U | _D: "|", _L: "-", _R: "-", _U: "|", _D: "|",
        **{
            bits: "+"
            for bits in (
                _R | _D, _L | _D, _R | _U, _L | _U, _L | _R | _D, _L | _R | _U, _U | _D | _R, _U | _D | _L,
                _L | _R | _U | _D,
            )
        },
    },
    arrow="v",
    glyphs={
        "root": "#", "waiting": ".", "dispatched": "o", "running": "*", "stuck": "!", "done": "+",
        "failed": "x", "interrupted": "-", "unknown": "?",
    },
    separator=" - ",
    ellipsis="...",
    tee="+- ",
    end="`- ",
    bar="|  ",
    gap="   ",
)
CHARSETS: Final = {UNICODE.name: UNICODE, ASCII.name: ASCII}


@dataclass(frozen=True)
class Segment:
    """Text and what it is. ``level`` places an edge in the drawing (its band, or its depth in the tree), so a
    painter can colour edges by how far down they are; every other role leaves it 0."""

    text: str
    role: str
    level: int = 0


@dataclass(frozen=True)
class View:
    lines: tuple[tuple[Segment, ...], ...]
    layout: str
    hidden_nodes: int = 0
    levels: int = 1  # how many edge levels the drawing has: one more than its deepest edge's level

    def text_lines(self) -> list[str]:
        return ["".join(segment.text for segment in line) for line in self.lines]

    def text(self) -> str:
        return "\n".join(self.text_lines())


# ── text measuring ───────────────────────────────────────────────────────

_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Mn", "Me", "Mc", "Zl", "Zp"})
_BIDI = frozenset(map(chr, (*range(0x202A, 0x202F), *range(0x2066, 0x206A))))


def _droppable(ch: str) -> bool:
    code = ord(ch)
    if ch in _BIDI or 0x1F3FB <= code <= 0x1F3FF or 0x1F1E6 <= code <= 0x1F1FF:
        return True
    if 0x1160 <= code <= 0x11FF or 0xD7B0 <= code <= 0xD7FF or 0xFE00 <= code <= 0xFE0F:
        return True
    return unicodedata.category(ch) in _DROPPED_CATEGORIES


def clean_text(value: object) -> str:
    """One line of paintable text: NFC, whitespace collapsed, every zero-width or control code point dropped.

    What is left is measured one code point at a time (`cells`), which is how
    a terminal paints it: no cluster survives that a terminal would draw as
    one glyph over several code points.
    """
    return _clean(value) if isinstance(value, str) else ""


@lru_cache(maxsize=4096)
def _clean(value: str) -> str:
    text = unicodedata.normalize("NFC", value)
    kept = "".join(" " if ch.isspace() else ch for ch in text if ch.isspace() or not _droppable(ch))
    return " ".join(kept.split())


@lru_cache(maxsize=4096)
def char_cells(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def cells(text: str) -> int:
    return len(text) if text.isascii() else sum(char_cells(ch) for ch in text)


def fit(text: str, width: int, ellipsis: str = "…") -> str:
    """``text`` cut to at most ``width`` cells, ending in ``ellipsis`` when it was cut."""
    if width <= 0:
        return ""
    if cells(text) <= width:
        return text
    room = width - cells(ellipsis)
    if room <= 0:
        return ellipsis[:width]
    kept, used = [], 0
    for ch in text:
        size = char_cells(ch)
        if used + size > room:
            break
        kept.append(ch)
        used += size
    return "".join(kept).rstrip() + ellipsis


def short_model(model: object) -> str:
    """The model as a box names it: provider path dropped, a leading ``claude-`` dropped (``opus-5-5``)."""
    text = clean_text(model)
    if not text or text == UNKNOWN_FIELD:
        return UNKNOWN_FIELD
    text = text.rsplit("/", 1)[-1]
    return text[len("claude-"):] if text.startswith("claude-") and len(text) > len("claude-") else text


# ── node text ────────────────────────────────────────────────────────────


def _state(node: Mapping[str, Any]) -> str:
    state = node.get("state")
    return state if state in NODE_STATES else "unknown"


def _model_text(node: Mapping[str, Any], separator: str) -> str:
    """``opus-5-5 · high`` in a box; the tree passes ``:`` so its facts line keeps one separator per field."""
    model = short_model(node.get("model"))
    effort = clean_text(node.get("effort"))
    return f"{model}{separator}{effort}" if effort and model != UNKNOWN_FIELD else model


def _status_parts(node: Mapping[str, Any]) -> tuple[str, float | None]:
    """The status word and the duration drawn after it, if one is."""
    waits = node.get("waits_on")
    if isinstance(waits, int) and not isinstance(waits, bool) and waits > 0:
        return f"waits on {waits} lane{'' if waits == 1 else 's'}", None
    word = clean_text(node.get("status")) or _state(node)
    elapsed = node.get("elapsed_seconds")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and 0 <= elapsed < ELAPSED_LIMIT:
        if _state(node) in ("running", "stuck", "done", "failed", "interrupted"):
            return word, float(elapsed)
    return word, None


def _status_text(node: Mapping[str, Any]) -> str:
    """The status word and its duration: how long it ran, or, for a stuck node, how long it has been stuck and why
    (``stuck 6m 40s: same error``), so a stall is never read as running time."""
    word, elapsed = _status_parts(node)
    if elapsed is None:
        return word
    if _state(node) == "stuck":
        reason = _stuck_reason(word)
        return f"stuck {elapsed_text(elapsed)}" + (f": {reason}" if reason else "")
    return f"{word} {elapsed_text(elapsed)}"


def _stuck_reason(word: str) -> str:
    return word.removeprefix("stuck").lstrip(":").strip()


def _status_cells(node: Mapping[str, Any]) -> int:
    """The status line's width for measuring a box: its duration counted at `DURATION_CELLS`."""
    word, elapsed = _status_parts(node)
    if elapsed is None:
        return cells(word)
    if _state(node) == "stuck":
        reason = _stuck_reason(word)
        return cells("stuck ") + DURATION_CELLS + (2 + cells(reason) if reason else 0)
    return cells(word) + 1 + DURATION_CELLS


def _title(node: Mapping[str, Any]) -> str:
    return clean_text(node.get("title")) or UNKNOWN_FIELD


def _category(node: Mapping[str, Any]) -> str:
    return clean_text(node.get("category")) or UNKNOWN_FIELD


def header_parts(graph: Mapping[str, Any], charset: Charset = UNICODE) -> tuple[str, str]:
    """``DAG · <name> · N nodes · E edges`` and the state counts, which follow it on one line when they fit."""
    nodes = _nodes(graph)
    edges = _edges(graph, {str(node["id"]) for node in nodes})
    sep = charset.separator
    counts: dict[str, int] = {}
    for node in nodes:
        state = _state(node)
        if state != "root":
            counts[state] = counts.get(state, 0) + 1
    order = ("running", "stuck", "failed", "dispatched", "waiting", "interrupted", "unknown", "done")
    tally = [f"{counts[state]} {state}" for state in order if counts.get(state)]
    hidden = graph.get("hidden_nodes")
    if isinstance(hidden, int) and not isinstance(hidden, bool) and hidden > 0:
        tally.append(f"+{hidden} more nodes")
    name = clean_text(graph.get("name")) or "delegation"
    node_word = "node" if len(nodes) == 1 else "nodes"
    edge_word = "edge" if len(edges) == 1 else "edges"
    return sep.join(["DAG", name, f"{len(nodes)} {node_word}", f"{len(edges)} {edge_word}"]), sep.join(tally)


def header_text(graph: Mapping[str, Any], charset: Charset = UNICODE) -> str:
    title, tally = header_parts(graph, charset)
    return f"{title}{charset.separator}{tally}" if tally else title


def _header_lines(graph: Mapping[str, Any], width: int, charset: Charset) -> tuple[tuple[Segment, ...], ...]:
    title, tally = header_parts(graph, charset)
    joined = f"{title}{charset.separator}{tally}" if tally else title
    if cells(joined) <= width or not tally:
        return ((Segment(fit(joined, width, charset.ellipsis), "header"),),)
    return (
        (Segment(fit(title, width, charset.ellipsis), "header"),),
        (Segment(fit(tally, width, charset.ellipsis), "header"),),
    )


def _nodes(graph: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = graph.get("nodes")
    seen: set[str] = set()
    nodes: list[Mapping[str, Any]] = []
    for node in raw if isinstance(raw, list) else []:
        if not isinstance(node, Mapping):
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id or node_id in seen:
            continue
        seen.add(node_id)
        nodes.append(node)
    return nodes


def _edges(graph: Mapping[str, Any], ids: set[str]) -> list[tuple[str, str]]:
    raw = graph.get("edges")
    seen: set[tuple[str, str]] = set()
    edges: list[tuple[str, str]] = []
    for edge in raw if isinstance(raw, list) else []:
        if not isinstance(edge, Mapping):
            continue
        upper, lower = edge.get("from"), edge.get("to")
        if not isinstance(upper, str) or not isinstance(lower, str) or upper not in ids or lower not in ids:
            continue
        if upper == lower or (upper, lower) in seen:
            continue
        seen.add((upper, lower))
        edges.append((upper, lower))
    return edges


def _order(node: Mapping[str, Any]) -> int:
    value = node.get("order")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# ── the canvas ───────────────────────────────────────────────────────────


@dataclass
class _Canvas:
    charset: Charset
    lines: dict[tuple[int, int], int] = field(default_factory=dict)
    line_roles: dict[tuple[int, int], str] = field(default_factory=dict)
    text: dict[tuple[int, int], tuple[str, str]] = field(default_factory=dict)
    arrows: dict[tuple[int, int], int] = field(default_factory=dict)
    joins: dict[tuple[int, int], int] = field(default_factory=dict)  # arrows drawn into a box's top border
    levels: dict[tuple[int, int], int] = field(default_factory=dict)
    level: int = 0  # the band the connectors drawn now belong to

    def hline(self, y: int, x1: int, x2: int, role: str = "edge") -> None:
        left, right = min(x1, x2), max(x1, x2)
        for x in range(left, right + 1):
            bits = (_L if x > left else 0) | (_R if x < right else 0)
            self._add(x, y, bits, role)

    def vline(self, x: int, y1: int, y2: int, role: str = "edge") -> None:
        top, bottom = min(y1, y2), max(y1, y2)
        for y in range(top, bottom + 1):
            bits = (_U if y > top else 0) | (_D if y < bottom else 0)
            self._add(x, y, bits, role)

    def mark(self, x: int, y: int, bits: int, role: str = "edge") -> None:
        self._add(x, y, bits, role)

    def arrow(self, x: int, y: int) -> None:
        key = (y, x)
        if key in self.text or key in self.lines or key in self.arrows:
            raise _Collision
        self.arrows[key] = self.level

    def join(self, x: int, y: int, level: int) -> None:
        """An arrow where an edge enters a two-line box: drawn in the box's top border, a line saving a row."""
        key = (y, x)
        if self.lines.get(key) != _L | _R or key in self.joins:
            raise _Collision
        self.joins[key] = level

    def _add(self, x: int, y: int, bits: int, role: str) -> None:
        key = (y, x)
        if key in self.text or key in self.arrows:
            raise _Collision
        self.lines[key] = self.lines.get(key, 0) | bits
        self.line_roles.setdefault(key, role)
        if role == "edge":
            self.levels.setdefault(key, self.level)

    def put(self, x: int, y: int, text: str, role: str) -> None:
        for ch in text:
            key = (y, x)
            if key in self.lines or key in self.text or key in self.arrows:
                raise _Collision
            self.text[key] = (ch, role)
            if char_cells(ch) == 2:
                if (y, x + 1) in self.lines or (y, x + 1) in self.text or (y, x + 1) in self.arrows:
                    raise _Collision
                self.text[(y, x + 1)] = ("", role)
                x += 2
            else:
                x += 1

    def box(self, x: int, y: int, inner: int, content: Sequence[Sequence[tuple[str, str]]], role: str) -> None:
        height = len(content) + 2
        right = x + inner + 3
        self.hline(y, x, right, role)
        self.hline(y + height - 1, x, right, role)
        self.vline(x, y, y + height - 1, role)
        self.vline(right, y, y + height - 1, role)
        for row, pieces in enumerate(content):
            column = x + 2
            for piece, piece_role in pieces:
                self.put(column, y + 1 + row, piece, piece_role)
                column += cells(piece)

    def render(self) -> list[tuple[Segment, ...]]:
        keys = set(self.lines) | set(self.text) | set(self.arrows)
        if not keys:
            return []
        height = max(y for y, _ in keys) + 1
        width = max(x for _, x in keys) + 1
        rendered: list[tuple[Segment, ...]] = []
        for y in range(height):
            segments: list[Segment] = []
            run, run_key = "", ("", 0)
            for x in range(width):
                key = (y, x)
                level = 0
                if key in self.joins:
                    ch, role, level = self.charset.arrow, "edge", self.joins[key]
                elif key in self.text:
                    ch, role = self.text[key]
                elif key in self.lines:
                    ch, role = self.charset.lines.get(self.lines[key], " "), self.line_roles[key]
                    level = self.levels.get(key, 0)
                elif key in self.arrows:
                    ch, role, level = self.charset.arrow, "edge", self.arrows[key]
                else:
                    ch, role = " ", ""
                if (role, level) != run_key and run:
                    segments.append(Segment(run, *run_key))
                    run = ""
                run_key = (role, level)
                run += ch
            if run:
                segments.append(Segment(run, *run_key))
            while segments and not segments[-1].text.strip():
                segments.pop()
            if segments:
                last = segments[-1]
                segments[-1] = Segment(last.text.rstrip(), last.role, last.level)
            rendered.append(tuple(segments))
        return rendered


class _Collision(Exception):
    """Two drawn things wanted one cell: the box layout gives up and the tree is drawn instead."""


# ── the box layout ───────────────────────────────────────────────────────


@dataclass
class _Band:
    kind: str  # "none", "single", "forest"
    uppers: list[str]
    lowers: list[str]
    children: dict[str, list[str]]


def _layers(ids: list[str], edges: list[tuple[str, str]]) -> dict[str, int] | None:
    parents: dict[str, list[str]] = {}
    for upper, lower in edges:
        parents.setdefault(lower, []).append(upper)
    layer, cyclic = layer_of(ids, parents)
    return None if cyclic else {node_id: layer[node_id] for node_id in ids}


def _bands(
    rows: list[list[str]], edges: list[tuple[str, str]], layer: Mapping[str, int]
) -> list[_Band] | None:
    bands: list[_Band] = []
    for index in range(len(rows) - 1):
        band_edges = [(u, d) for u, d in edges if layer[u] == index]
        if not band_edges:
            bands.append(_Band("none", [], [], {}))
            continue
        parent = {node: node for pair in band_edges for node in pair}

        def find(node: str) -> str:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for upper, lower in band_edges:
            parent[find(upper)] = find(lower)
        groups: dict[str, tuple[set[str], set[str], int]] = {}
        for upper, lower in band_edges:
            uppers, lowers, count = groups.get(find(upper), (set(), set(), 0))
            uppers.add(upper)
            lowers.add(lower)
            groups[find(upper)] = (uppers, lowers, count + 1)
        if any(count != len(uppers) * len(lowers) for uppers, lowers, count in groups.values()):
            return None
        position = {node: at for at, node in enumerate(rows[index])}
        lower_position = {node: at for at, node in enumerate(rows[index + 1])}
        uppers_all = sorted({u for u, _ in band_edges}, key=position.__getitem__)
        lowers_all = sorted({d for _, d in band_edges}, key=lower_position.__getitem__)
        children = {
            upper: sorted([d for u, d in band_edges if u == upper], key=lower_position.__getitem__)
            for upper in uppers_all
        }
        if len(groups) == 1:
            bands.append(_Band("single", uppers_all, lowers_all, children))
        elif all(len(uppers) == 1 for uppers, _lowers, _count in groups.values()):
            bands.append(_Band("forest", uppers_all, lowers_all, children))
        else:
            return None
    return bands


@dataclass
class _Placement:
    x: dict[str, int]
    width: dict[str, int]
    inner: dict[str, int]
    rows: list[list[list[str]]]  # per layer, its rows
    trunk_left: dict[int, int]  # layer -> x of the left trunk feeding its rows
    trunk_right: dict[int, int]  # layer -> x of the right trunk collecting its rows
    labels: dict[tuple[int, int], str]  # (layer, row) -> wave label


def _glyph(node: Mapping[str, Any], charset: Charset, spinner: str | None) -> str:
    state = _state(node)
    if spinner and state == "running" and charset is UNICODE and cells(spinner) == 1:
        return spinner
    return charset.glyphs.get(state, charset.glyphs["unknown"])


def _box_title(node: Mapping[str, Any], charset: Charset) -> str:
    title = _title(node)
    return f"{title}{charset.separator}orchestrator" if node.get("orchestrates") else title


def _content(
    node: Mapping[str, Any], charset: Charset, inner: int, spinner: str | None = None, compact: bool = False
) -> list[list[tuple[str, str]]]:
    state = _state(node)
    glyph = _glyph(node, charset, spinner)
    title = fit(_box_title(node, charset), max(1, inner - cells(glyph) - 1), charset.ellipsis)
    first = [(glyph, f"glyph:{state}"), (" " + title, _field_role(title, "title"))]
    model = fit(_model_text(node, charset.separator), inner, charset.ellipsis)
    if node.get("kind") == "root":
        if compact:
            return [_compact_root(node, charset, inner, glyph)]
        # A root that names no model at all (an operator's fanout) is one line; an unknown one shows "?".
        return [first, [(model, _field_role(model, "model"))]] if clean_text(node.get("model")) else [first]
    category = fit(_category(node), inner, charset.ellipsis)
    if compact:
        return [first, _compact_facts(node, charset, inner)]
    return [
        first,
        [(category, _field_role(category, "category"))],
        [(model, _field_role(model, "model"))],
        [(fit(_status_text(node), inner, charset.ellipsis), f"status:{state}")],
    ]


def _compact_root(node: Mapping[str, Any], charset: Charset, inner: int, glyph: str) -> list[tuple[str, str]]:
    """A two-line drawing's root on one line: its title, then its model and effort where they fit."""
    sep = charset.separator
    title = fit(_title(node), max(1, inner - cells(glyph) - 1), charset.ellipsis)
    line = [(glyph, "glyph:root"), (" " + title, _field_role(title, "title"))]
    room = inner - cells(glyph) - 1 - cells(title) - cells(sep)
    if clean_text(node.get("model")) and room >= cells(charset.ellipsis) + 1:
        model = fit(_model_text(node, sep), room, charset.ellipsis)
        line += [(sep, "model"), (model, _field_role(model, "model"))]
    return line


def _compact_facts(node: Mapping[str, Any], charset: Charset, inner: int) -> list[tuple[str, str]]:
    """A two-line box's second line: category, then the model, with its effort only where that fits."""
    sep = charset.separator
    category = fit(_category(node), inner, charset.ellipsis)
    facts = [(category, _field_role(category, "category"))]
    room = inner - cells(category) - cells(sep)
    with_effort = _model_text(node, sep)
    model = with_effort if cells(with_effort) <= room else short_model(node.get("model"))
    if room >= cells(charset.ellipsis) + 1:
        model = fit(model, room, charset.ellipsis)
        facts += [(sep, "model"), (model, _field_role(model, "model"))]
    return facts


def _field_role(text: str, role: str) -> str:
    """A field with no record (`?`) is drawn as unknown, whatever field it is."""
    return "unknown" if text == UNKNOWN_FIELD else role


def _natural_inner(node: Mapping[str, Any], charset: Charset, compact: bool = False) -> int:
    glyph = charset.glyphs.get(_state(node), "?")
    model = cells(_model_text(node, charset.separator))
    if compact and node.get("kind") == "root":
        title = cells(glyph) + 1 + cells(_title(node))
        return title + (cells(charset.separator) + model if clean_text(node.get("model")) else 0)
    widths = [cells(glyph) + 1 + cells(_box_title(node, charset))]
    if node.get("kind") != "root" or clean_text(node.get("model")):
        widths.append(model)
    if node.get("kind") != "root":
        if compact:
            widths.append(cells(_category(node)) + cells(charset.separator) + model)
        else:
            widths += [cells(_category(node)), _status_cells(node)]
    return max(widths)


def _box_view(
    graph: Mapping[str, Any], width: int, charset: Charset, lane_inner: int, spinner: str | None = None,
    compact: bool = False,
) -> tuple[list[tuple[Segment, ...]], int] | None:
    nodes = _nodes(graph)
    if not nodes:
        return None
    by_id = {str(node["id"]): node for node in nodes}
    ids = list(by_id)
    edges = _edges(graph, set(ids))
    layer = _layers(ids, edges)
    if layer is None or any(layer[d] != layer[u] + 1 for u, d in edges):
        return None
    depth = max(layer.values()) + 1
    predecessors: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for upper, lower in edges:
        predecessors[lower].append(upper)
    rows: list[list[str]] = [[] for _ in range(depth)]
    rows[0] = sorted((i for i in ids if layer[i] == 0), key=lambda i: (_order(by_id[i]), i))
    for index in range(1, depth):
        position = {node: at for at, node in enumerate(rows[index - 1])}
        members = [i for i in ids if layer[i] == index]
        rows[index] = sorted(
            members,
            key=lambda i: (min(position[u] for u in predecessors[i]), _order(by_id[i]), i),
        )
    bands = _bands(rows, edges, layer)
    if bands is None:
        return None
    orchestrating = {upper for upper, _lower in edges}
    by_id = {
        node_id: {**node, "orchestrates": node.get("kind") == "root" and node_id in orchestrating}
        for node_id, node in by_id.items()
    }
    inner = {
        node_id: min(
            ROOT_INNER_MAX if by_id[node_id].get("kind") == "root" else lane_inner,
            max(INNER_MIN, _natural_inner(by_id[node_id], charset, compact)),
        )
        for node_id in ids
    }
    lanes = [node_id for node_id in ids if by_id[node_id].get("kind") != "root"]
    lane_width = max((inner[node_id] for node_id in lanes), default=lane_inner)
    for node_id in lanes:
        inner[node_id] = lane_width  # every lane box of one drawing is one size
    box_width = {node_id: inner[node_id] + 4 for node_id in ids}
    placement = _place(rows, bands, by_id, box_width, inner, width)
    if placement is None:
        return None
    canvas = _Canvas(charset)
    try:
        _draw(canvas, placement, bands, by_id, charset, spinner, compact)
    except _Collision:
        return None
    lines = canvas.render()
    if any(cells("".join(segment.text for segment in line)) > width for line in lines):
        return None
    return lines, max(1, len(bands))


def _place(
    rows: list[list[str]],
    bands: list[_Band],
    by_id: Mapping[str, Mapping[str, Any]],
    box_width: Mapping[str, int],
    inner: Mapping[str, int],
    width: int,
) -> _Placement | None:
    depth = len(rows)
    required = dict(box_width)
    for index in range(depth - 2, -1, -1):
        band = bands[index]
        if band.kind == "forest":
            for upper in band.uppers:
                kids = band.children[upper]
                span = sum(required[kid] for kid in kids) + GAP * (len(kids) - 1)
                required[upper] = max(required[upper], span)
    x: dict[str, int] = {}
    slot: dict[str, int] = {}
    layer_rows: list[list[list[str]]] = [[rows[0]]]
    trunk_left: dict[int, int] = {}
    trunk_right: dict[int, int] = {}
    labels: dict[tuple[int, int], str] = {}
    cursor = 0
    for node in rows[0]:
        slot[node] = cursor
        x[node] = cursor + (required[node] - box_width[node]) // 2
        cursor += required[node] + GAP
    for index, band in enumerate(bands):
        lowers = rows[index + 1]
        if band.kind == "forest":
            for upper in band.uppers:
                kids = band.children[upper]
                span = sum(required[kid] for kid in kids) + GAP * (len(kids) - 1)
                start = slot[upper] + (required[upper] - span) // 2
                for kid in kids:
                    slot[kid] = start
                    x[kid] = start + (required[kid] - box_width[kid]) // 2
                    start += required[kid] + GAP
            layer_rows.append([lowers])
            continue
        if band.kind != "single":
            return None
        centers = [x[u] + box_width[u] // 2 for u in band.uppers]
        middle = (min(centers) + max(centers)) // 2
        below = bands[index + 1] if index + 1 < len(bands) else _Band("none", [], [], {})
        can_wrap = below.kind in ("none", "single")
        groups = _wave_groups(band, lowers, by_id)
        total = sum(required[node] for node in lowers) + GAP * (len(lowers) - 1)
        if total <= width and not (groups and can_wrap):
            start = middle - total // 2
            for node in lowers:
                slot[node] = start
                x[node] = start + (required[node] - box_width[node]) // 2
                start += required[node] + GAP
            layer_rows.append([lowers])
            continue
        if not can_wrap:
            return None
        collects = below.kind == "single"
        room = width - GUTTER - (GUTTER if collects else 0)
        size = max(box_width[node] for node in lowers)
        per_row = (room + GAP) // (size + GAP)
        if per_row < 1:
            return None
        wrapped: list[list[str]] = []
        for label, members in groups or [("", lowers)]:
            for start_at in range(0, len(members), per_row):
                wrapped.append(members[start_at:start_at + per_row])
                if label and start_at == 0:
                    labels[(index + 1, len(wrapped) - 1)] = label
        row_width = max(len(row) * (size + GAP) - GAP for row in wrapped)
        # The boxes, not the gutters, are centred under the parent, so a one-box row sits straight below it.
        block_start = middle - row_width // 2 - GUTTER
        trunk_left[index + 1] = block_start + 1
        if collects:
            trunk_right[index + 1] = block_start + GUTTER + row_width + 1
        for row in wrapped:
            start = block_start + GUTTER
            for node in row:
                slot[node] = start
                x[node] = start + (size - box_width[node]) // 2
                start += size + GAP
        layer_rows.append(wrapped)
    shift = -min(
        [x[node] for node in x] + list(trunk_left.values()) + list(trunk_right.values())
    )
    x = {node: value + shift for node, value in x.items()}
    trunk_left = {key: value + shift for key, value in trunk_left.items()}
    trunk_right = {key: value + shift for key, value in trunk_right.items()}
    right_edge = max(
        [x[node] + box_width[node] for node in x] + [value + 1 for value in trunk_right.values()]
    )
    if right_edge > width:
        return None
    return _Placement(dict(x), dict(box_width), dict(inner), layer_rows, trunk_left, trunk_right, labels)


def _wave_groups(
    band: _Band, lowers: list[str], by_id: Mapping[str, Mapping[str, Any]]
) -> list[tuple[str, list[str]]]:
    """Dispatch waves of one root, in order, when there is more than one: each starts its own row."""
    if len(band.uppers) != 1 or by_id[band.uppers[0]].get("kind") != "root":
        return []
    groups: list[tuple[str, list[str]]] = []
    for node in lowers:
        label = clean_text(by_id[node].get("group"))
        if not label.startswith("wave "):
            return []
        if groups and groups[-1][0] == label:
            groups[-1][1].append(node)
        else:
            groups.append((label, [node]))
    labels = [label for label, _members in groups]
    return groups if len(groups) > 1 and len(set(labels)) == len(labels) else []


def _draw(
    canvas: _Canvas,
    placement: _Placement,
    bands: list[_Band],
    by_id: Mapping[str, Mapping[str, Any]],
    charset: Charset,
    spinner: str | None = None,
    compact: bool = False,
) -> None:
    """Rows top to bottom; between two rows, the connector rows the bands above and below need.

    A trunk is drawn as one vertical line from the junction where it last met
    a bus to the next one, so it passes every box row and label line between
    them in its gutter, where nothing else is drawn.
    """
    x, box_width = placement.x, placement.width
    content = {node: _content(by_id[node], charset, placement.inner[node], spinner, compact) for node in x}
    height = {node: len(content[node]) + 2 for node in x}
    center = {node: x[node] + box_width[node] // 2 for node in x}
    bottom: dict[str, int] = {}
    joins: list[tuple[int, int, int]] = []  # two-line boxes take their arrows into their top border
    step = 1 if compact else 2  # rows from a bus to the boxes it feeds
    y = 0
    previous_row: list[str] = []
    left_open: tuple[int, int] | None = None  # the left trunk feeding this layer: (x, y of its last junction)
    right_open: tuple[int, int] | None = None  # the right trunk collecting this layer: (x, y of its last junction)
    for index, layer_rows in enumerate(placement.rows):
        wrapped = len(layer_rows) > 1
        for row_index, row in enumerate(layer_rows):
            if index > 0 and row_index == 0:
                band = bands[index - 1]
                canvas.level = index - 1
                uppers = [node for node in previous_row if node in band.uppers]
                blocked = [center[node] for node in uppers] + ([right_open[0]] if right_open else [])
                label = placement.labels.get((index, 0))
                if label:
                    y = _label(canvas, placement.trunk_left[index], y, label, blocked)
                bus = y
                for upper in uppers:
                    canvas.vline(center[upper], bottom[upper], bus)
                lowers = [node for node in row if node in band.lowers]
                if band.kind == "single":
                    points = [center[node] for node in uppers + lowers]
                    if right_open is not None:
                        canvas.vline(right_open[0], right_open[1], bus)
                        points.append(right_open[0])
                    if wrapped:
                        points.append(placement.trunk_left[index])
                    canvas.hline(bus, min(points), max(points))
                else:
                    for upper in uppers:
                        kids = [center[kid] for kid in band.children[upper] if kid in row]
                        canvas.hline(bus, min([center[upper], *kids]), max([center[upper], *kids]))
                right_open = None
                for node in lowers:
                    canvas.mark(center[node], bus, _D)
                    if compact:
                        joins.append((center[node], bus + 1, canvas.level))
                    else:
                        canvas.arrow(center[node], bus + 1)
                if wrapped:
                    canvas.mark(placement.trunk_left[index], bus, _D)
                    left_open = (placement.trunk_left[index], bus)
                y = bus + step
            elif row_index > 0:
                below = bands[index] if index < len(bands) else None
                if below is not None and below.kind == "single" and index in placement.trunk_right:
                    uppers = [node for node in previous_row if node in below.uppers]
                    canvas.level = index  # the right trunk collects for the band below this layer
                    if uppers:
                        trunk = placement.trunk_right[index]
                        for upper in uppers:
                            canvas.vline(center[upper], bottom[upper], y)
                        canvas.hline(y, min(center[node] for node in uppers), trunk)
                        if right_open is not None:
                            canvas.vline(trunk, right_open[1], y)
                        canvas.mark(trunk, y, _D)
                        right_open = (trunk, y)
                        y += 1
                trunk = placement.trunk_left[index]
                canvas.level = index - 1  # the left trunk feeds this layer from the band above
                label = placement.labels.get((index, row_index))
                if label:
                    y = _label(canvas, trunk, y, label, [right_open[0]] if right_open else [])
                if left_open is None:
                    raise _Collision
                canvas.vline(trunk, left_open[1], y)
                canvas.hline(y, trunk, max(center[node] for node in row))
                for node in row:
                    canvas.mark(center[node], y, _D)
                    if compact:
                        joins.append((center[node], y + 1, canvas.level))
                    else:
                        canvas.arrow(center[node], y + 1)
                if row_index < len(layer_rows) - 1:
                    canvas.mark(trunk, y, _D)
                    left_open = (trunk, y)
                else:
                    left_open = None
                y += step
            for node in row:
                state = _state(by_id[node])
                inner = placement.inner[node]
                canvas.box(x[node], y, inner, content[node], "border" if state == "root" else f"border:{state}")
                bottom[node] = y + height[node] - 1
            for column, row_y, level in joins:
                canvas.join(column, row_y, level)
            joins.clear()
            y += max(height[node] for node in row)
            previous_row = row
    if right_open is not None:
        raise _Collision


def _group_label(label: object, charset: Charset) -> str:
    """A group label in this charset: the builder writes ``wave 1 · 14:02``; ASCII draws ``wave 1 - 14:02``."""
    return clean_text(label).replace(UNICODE.separator, charset.separator)


def _label(canvas: _Canvas, trunk: int, y: int, label: str, blocked: list[int]) -> int:
    """A wave's label on its own line, right of the trunk and left of any line that crosses it."""
    start = trunk + 2
    limit = min([column for column in blocked if column > start] or [start + 64]) - 1
    text = fit(_group_label(label, canvas.charset), limit - start, canvas.charset.ellipsis)
    if cells(text) >= LABEL_MIN:
        canvas.put(start, y, text, "group")
        return y + 1
    return y


# ── the tree ─────────────────────────────────────────────────────────────


def _tree_structure(graph: Mapping[str, Any]) -> tuple[
    Mapping[str, Any] | None, list[tuple[str, list[str]]], dict[str, list[str]], dict[str, Mapping[str, Any]]
]:
    nodes = _nodes(graph)
    by_id = {str(node["id"]): node for node in nodes}
    root = next((node for node in nodes if node.get("kind") == "root"), None)
    root_id = str(root["id"]) if root else ""
    raw_edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    nested: dict[str, list[str]] = {}
    parent_of: dict[str, str] = {}
    for edge in raw_edges:
        if not isinstance(edge, Mapping) or edge.get("kind") not in NESTING_KINDS:
            continue
        upper, lower = edge.get("from"), edge.get("to")
        if upper in by_id and lower in by_id and upper != root_id and lower not in parent_of and upper != lower:
            parent_of[str(lower)] = str(upper)
    # A parent chain that loops is not drawn as nesting.
    for lower in list(parent_of):
        seen, current = {lower}, parent_of.get(lower)
        while current is not None:
            if current in seen:
                parent_of.pop(lower, None)
                break
            seen.add(current)
            current = parent_of.get(current)
    ordered = sorted((i for i in by_id if i != root_id), key=lambda i: (_order(by_id[i]), i))
    for lower in ordered:
        if lower in parent_of:
            nested.setdefault(parent_of[lower], []).append(lower)
    groups: list[tuple[str, list[str]]] = []
    for node_id in ordered:
        if node_id in parent_of:
            continue
        label = clean_text(by_id[node_id].get("group"))
        for group_label, members in groups:
            if group_label == label:
                members.append(node_id)
                break
        else:
            groups.append((label, [node_id]))
    return root, groups, nested, by_id


def _tree_lines(
    graph: Mapping[str, Any],
    width: int,
    charset: Charset,
    hidden: set[str],
    spinner: str | None,
) -> list[tuple[Segment, ...]]:
    root, groups, nested, by_id = _tree_structure(graph)
    sep = charset.separator
    lines: list[tuple[Segment, ...]] = []
    if root is not None:
        model = f"{sep}{_model_text(root, ':')}" if clean_text(root.get("model")) else ""
        text = fit(f"{charset.glyphs['root']} {_title(root)}{model}", width, charset.ellipsis)
        lines.append((Segment(text, "title"),))
    wide = width >= TREE_WIDE_MIN
    visible_groups = [(label, [m for m in members if m not in hidden]) for label, members in groups]
    visible_groups = [(label, members) for label, members in visible_groups if members]
    labelled = len(visible_groups) > 1 or any(label for label, _members in visible_groups)
    shown = [node for node in by_id if node not in hidden and (root is None or node != str(root["id"]))]
    title_width = min(TREE_TITLE_MAX, max([cells(_title(by_id[n])) for n in shown] or [12]))
    category_width = min(TREE_CATEGORY_MAX, max([cells(_category(by_id[n])) for n in shown] or [1]))
    model_width = min(TREE_MODEL_MAX, max([cells(_model_text(by_id[n], ":")) for n in shown] or [1]))
    deepest = max([_depth(n, nested) for n in shown] or [0])
    base = (cells(charset.tee) if labelled else 0) + cells(charset.tee)
    # Where the title column ends, the same for every depth, so the columns after it line up.
    title_end = base + cells(charset.bar) * deepest + 2 + title_width
    room_after = width - title_end - category_width - model_width - 6
    if room_after < 12:
        title_end -= 12 - room_after
    step, offset = len(charset.bar), 1 if labelled else 0
    deepest_shown = max(2, (width - TREE_LEAD_ROOM) // step)

    def clamped(lead: str, depth: int) -> str:
        """A lead too deep for the width: its first columns, then the depth as a number in place of the rest."""
        if len(lead) // step <= deepest_shown:
            return lead
        return lead[: (deepest_shown - 1) * step] + f"{min(depth, 10 ** (step - 1) - 1):>{step - 1}} "

    def lead_segments(lead: str) -> list[Segment]:
        """One segment per column of ``lead``: a bar keeps the level of the depth it continues all the way down."""
        return [Segment(lead[at:at + step], "edge", max(0, at // step - offset)) for at in range(0, len(lead), step)]

    def node_lines(node_id: str, lead: str, last: bool, depth: int) -> None:
        node = by_id[node_id]
        state = _state(node)
        glyph = _glyph(node, charset, spinner)
        branch = charset.end if last else charset.tee
        under = lead + (charset.gap if last else charset.bar)
        lead = clamped(lead, depth)
        prefix = lead + branch
        if wide:
            title_room = max(8, title_end - cells(prefix) - cells(glyph) - 1)
            title = fit(_title(node), title_room, charset.ellipsis)
            pad = " " * (title_room - cells(title))
            category = fit(_category(node), category_width, charset.ellipsis)
            model = fit(_model_text(node, ":"), model_width, charset.ellipsis)
            pieces = [
                *lead_segments(lead),
                Segment(branch, "edge", depth),
                Segment(glyph, f"glyph:{state}"),
                Segment(f" {title}{pad}  ", _field_role(title, "title")),
                Segment(_pad(category, category_width) + "  ", _field_role(category, "category")),
                Segment(_pad(model, model_width) + "  ", _field_role(model, "model")),
                Segment(_status_text(node), f"status:{state}"),
            ]
            lines.append(_cut_line(pieces, width, charset))
        else:
            title = fit(_title(node), width - cells(prefix) - cells(glyph) - 1, charset.ellipsis)
            lines.append((
                *lead_segments(lead), Segment(branch, "edge", depth), Segment(glyph, f"glyph:{state}"),
                Segment(f" {title}", _field_role(title, "title")),
            ))
            shown_under = clamped(under, depth)
            indent = shown_under + "  "
            category, status = _category(node), _status_text(node)
            # The status is what a reader came for: the model gives way first when the line is full.
            model_room = width - cells(indent) - cells(category) - cells(status) - 2 * cells(sep)
            model = fit(_model_text(node, ":"), max(cells(charset.ellipsis) + 3, model_room), charset.ellipsis)
            facts = [
                *lead_segments(shown_under),
                Segment("  ", "edge", depth),
                Segment(category, _field_role(category, "category")),
                Segment(sep, "edge", depth),
                Segment(model, _field_role(model, "model")),
                Segment(sep, "edge", depth),
                Segment(status, f"status:{state}"),
            ]
            lines.append(_cut_line(facts, width, charset))
        kids = [kid for kid in nested.get(node_id, []) if kid not in hidden]
        for at, kid in enumerate(kids):
            node_lines(kid, under, at == len(kids) - 1, depth + 1)

    for group_at, (label, members) in enumerate(visible_groups):
        group_last = group_at == len(visible_groups) - 1
        if labelled:
            prefix = charset.end if group_last else charset.tee
            text = fit(_group_label(label, charset) or "lanes", width - cells(prefix), charset.ellipsis)
            lines.append((Segment(prefix, "edge"), Segment(text, "group")))
            lead = charset.gap if group_last else charset.bar
        else:
            lead = ""
        for at, member in enumerate(members):
            node_lines(member, lead, at == len(members) - 1 and not (group_last and hidden), 0)
    if hidden:
        done = sum(1 for node in hidden if _state(by_id[node]) == "done")
        tail = f" ({done} done)" if done else ""
        text = f"+{len(hidden)} more node{'' if len(hidden) == 1 else 's'}{tail}"
        lines.append((Segment(charset.end, "edge"), Segment(fit(text, width - cells(charset.end), charset.ellipsis), "more")))
    return lines


def _depth(node_id: str, nested: Mapping[str, list[str]]) -> int:
    parents = {kid: upper for upper, kids in nested.items() for kid in kids}
    depth, current, seen = 0, node_id, {node_id}
    while current in parents and parents[current] not in seen:
        current = parents[current]
        seen.add(current)
        depth += 1
    return depth


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - cells(text))


def _cut_line(pieces: list[Segment], width: int, charset: Charset) -> tuple[Segment, ...]:
    kept: list[Segment] = []
    used = 0
    for piece in pieces:
        size = cells(piece.text)
        if used + size <= width:
            kept.append(piece)
            used += size
            continue
        room = width - used
        if room > cells(charset.ellipsis):
            kept.append(Segment(fit(piece.text, room, charset.ellipsis), piece.role, piece.level))
        break
    while kept and not kept[-1].text.strip():
        kept.pop()
    if kept:
        kept[-1] = Segment(kept[-1].text.rstrip(), kept[-1].role, kept[-1].level)
    return tuple(kept)


_HIDE_ORDER: Final = {
    "done": 0, "unknown": 1, "waiting": 2, "dispatched": 3, "interrupted": 4, "failed": 5, "running": 6,
    "stuck": 6,
}


def _hide_candidates(graph: Mapping[str, Any]) -> tuple[list[str], dict[str, list[str]]]:
    """Nodes in the order a cap drops them (settled first, oldest first; running and failed last), and the nesting."""
    _root, _groups, nested, by_id = _tree_structure(graph)
    candidates = [node_id for node_id, node in by_id.items() if node.get("kind") != "root"]
    ordered = sorted(
        candidates,
        key=lambda node_id: (_HIDE_ORDER.get(_state(by_id[node_id]), 1), _order(by_id[node_id]), node_id),
    )
    return ordered, nested


# ── entry point ──────────────────────────────────────────────────────────


def render_delegation_view(
    graph: Mapping[str, Any] | None,
    *,
    width: int,
    max_lines: int | None = None,
    max_chars: int | None = None,
    charset: str = "unicode",
    layout: str = "auto",
    spinner: str | None = None,
    compact: bool = False,
) -> View:
    """The best drawing of ``graph`` that fits: boxes, else (with ``compact``) two-line boxes, else the tree, else
    the header alone.

    ``layout`` narrows the choice: ``box`` tries four-line boxes, ``compact``
    two-line ones, and ``tree`` only the tree; each still falls back to the
    tree and the header. ``max_chars`` counts the joined text, newlines
    included. Never raises on a malformed graph: what cannot be read is left
    out, and what cannot be laid out is drawn as the tree. A graph with no nodes
    draws nothing.
    """
    chosen = CHARSETS.get(charset, UNICODE)
    safe_width = max(8, int(width)) if isinstance(width, int) and not isinstance(width, bool) else 80
    if not isinstance(graph, Mapping) or not _nodes(graph):
        return View((), "header")
    header = _header_lines(graph, safe_width, chosen)
    key = (_structure(graph), safe_width, max_lines, max_chars, chosen.name, layout, spinner, compact)

    def fits(lines: Sequence[tuple[Segment, ...]]) -> bool:
        if max_lines is not None and len(lines) > max_lines:
            return False
        if max_chars is not None:
            size = sum(len("".join(segment.text for segment in line)) for line in lines) + max(0, len(lines) - 1)
            if size > max_chars:
                return False
        return True

    def boxes(compact_boxes: bool, lane_inner: int) -> View | None:
        drawn = _box_view(graph, safe_width, chosen, lane_inner, spinner, compact_boxes)
        if drawn is None or not fits((*header, *drawn[0])):
            return None
        return View((*header, *drawn[0]), "compact" if compact_boxes else "box", levels=drawn[1])

    def tree(hidden: set[str]) -> View | None:
        body = _tree_lines(graph, safe_width, chosen, hidden, spinner)
        if not fits((*header, *body)):
            return None
        levels = 1 + max((segment.level for line in body for segment in line if segment.role == "edge"), default=0)
        return View((*header, *body), "tree", len(hidden), levels)

    decided = _DECISIONS.get(key)
    if decided is not None:
        kind, detail = decided
        replayed = boxes(kind == "compact", detail) if kind in ("box", "compact") else tree(set(detail)) if (
            kind == "tree") else None
        if replayed is not None:
            return replayed
    view, decision = _choose(graph, safe_width, chosen, header, layout, compact, max_lines, boxes, tree, fits)
    if len(_DECISIONS) >= DECISION_LIMIT:
        _DECISIONS.pop(next(iter(_DECISIONS)))
    _DECISIONS[key] = decision
    return view


_DECISIONS: dict[tuple[Any, ...], tuple[str, Any]] = {}


def _choose(
    graph: Mapping[str, Any],
    width: int,
    charset: Charset,
    header: tuple[tuple[Segment, ...], ...],
    layout: str,
    compact: bool,
    max_lines: int | None,
    boxes: Any,
    tree: Any,
    fits: Any,
) -> tuple[View, tuple[str, Any]]:
    """The drawing `render_delegation_view` returns, and the decision that redraws it."""
    tiers = {"auto": (False, True) if compact else (False,), "box": (False,), "compact": (True,)}.get(layout, ())
    if width >= BOX_MIN_WIDTH:
        for compact_boxes in tiers:
            if max_lines is not None and len(header) + _fewest_box_lines(graph, compact_boxes) > max_lines:
                continue  # even unwrapped, these boxes are taller than the budget
            # The fewest lines wins, then the widest boxes: three lanes cut to fit one row read better than the same
            # three wrapped into three. Boxes narrower than INNER_COMFORT are a last resort. A width past the widest
            # box's own draws what that width draws, so the search starts there.
            # Narrower boxes never take more lines (more fit a row) and never fit worse, so the narrowest width of a
            # floor draws the fewest lines any width can, and the widest width that draws as few is found by halving.
            kind = "compact" if compact_boxes else "box"
            top = min(INNER_MAX, max(_widest_lane(graph, charset, compact_boxes), INNER_COMFORT))
            for floor, ceiling in ((INNER_COMFORT, top), (INNER_MIN, INNER_COMFORT - 1)):
                narrowest = boxes(compact_boxes, floor)
                if narrowest is None:
                    continue
                best, low, high = (narrowest, floor), floor + 1, ceiling
                while low <= high:
                    middle = (low + high) // 2
                    drawn = boxes(compact_boxes, middle)
                    if drawn is not None and len(drawn.lines) == len(narrowest.lines):
                        best, low = (drawn, middle), middle + 1
                    else:
                        high = middle - 1
                return best[0], (kind, best[1])
    order = _hide_order(graph)
    whole = tree(set())
    if whole is not None:
        return whole, ("tree", ())
    # Past the first (which adds the `+N more` line), each node hidden takes lines away, so the fewest hidden that
    # fit are found by halving, not one at a time.
    low, high, found = 1, len(order), None
    while low <= high:
        middle = (low + high) // 2
        drawn = tree(set(order[:middle]))
        if drawn is not None:
            found, high = (drawn, middle), middle - 1
        else:
            low = middle + 1
    if found is not None:
        return found[0], ("tree", tuple(sorted(order[:found[1]])))
    candidates = order
    if fits(header):
        return View(header, "header", len(candidates)), ("header", None)
    single = ((Segment(fit(header_text(graph, charset), width, charset.ellipsis), "header"),),)
    return View(single if fits(single) else (), "header", len(candidates)), ("header", None)


def _hide_order(graph: Mapping[str, Any]) -> list[str]:
    """Every node in the order a line budget hides them: a parent only after all of its children."""
    candidates, nested = _hide_candidates(graph)
    hidden: list[str] = []
    gone: set[str] = set()
    while True:
        remaining = [node for node in candidates if node not in gone and all(kid in gone for kid in nested.get(node, []))]
        if not remaining:
            return hidden
        hidden.append(remaining[0])
        gone.add(remaining[0])


def _fewest_box_lines(graph: Mapping[str, Any], compact: bool) -> int:
    """The lines boxes take with no layer wrapped: the root's box, then per layer a bus, an arrow and a box."""
    nodes = _nodes(graph)
    ids = [str(node["id"]) for node in nodes]
    layer = _layers(ids, _edges(graph, set(ids)))
    if layer is None:
        return 0  # no layering: `_box_view` says why it cannot draw
    depth = max(layer.values()) + 1
    root = next((node for node in nodes if node.get("kind") == "root"), None)
    if compact:
        return 3 + (depth - 1) * (1 + 4)  # a one-line root; per layer a bus, then boxes with the arrow in the border
    root_lines = 2 + (2 if root is None or clean_text(root.get("model")) else 1)
    return root_lines + (depth - 1) * (2 + 2 + 4)


def _widest_lane(graph: Mapping[str, Any], charset: Charset, compact: bool) -> int:
    """The widest lane box's natural inner width: every lane box of a drawing is that wide, or `lane_inner`."""
    return max(
        [max(INNER_MIN, _natural_inner(node, charset, compact)) for node in _nodes(graph) if node.get("kind") != "root"]
        or [INNER_MIN]
    )


def _structure(graph: Mapping[str, Any]) -> tuple[Any, ...]:
    """What a layout depends on: every node's labels and state, the edges, the name; never a duration's value."""
    nodes = _nodes(graph)
    shape = tuple(
        (
            str(node.get("id")), str(node.get("kind")), clean_text(node.get("title")), clean_text(node.get("category")),
            clean_text(node.get("model")), clean_text(node.get("effort")), _state(node), _status_parts(node)[0],
            _status_parts(node)[1] is not None, clean_text(node.get("group")), _order(node),
        )
        for node in nodes
    )
    edges = tuple(_edges(graph, {str(node["id"]) for node in nodes}))
    return shape, edges, clean_text(graph.get("name")), str(graph.get("hidden_nodes"))
