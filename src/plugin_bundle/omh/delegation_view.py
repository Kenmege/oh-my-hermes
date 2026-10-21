"""Draw a `delegation_graph/v1` as text: frontiers of boxes, or a compact tree when boxes do not fit.

Pure: no I/O, no clock, nothing imported from `omh`. The OMH TUI draws the
lines with its own styles (every piece of text carries a role), and `omh hud
--graph` joins them as text. A chat reply never carries this drawing
(`delegation_snapshot.py` writes its own short text).

The boxes follow oh-my-openagent's DAG view. A header names the task count,
the run and its state (``Running · Done 1/5``). Every node but the root is a
box of four lines: its title, its category and model, its status with its
glyph and duration, and its parents (``← Export API``) or ``Start node``. The
root is no box: what it dispatched starts a frontier, and its model is named
in the header. A node's frontier is the longest path of recorded edges (a
dispatch, a `depends_on`, a repair or an extend) from a node with none. The
nodes of one frontier sit side by side with a ``· Same frontier`` label
under them, and one connector (``│`` then ``▼``) joins one frontier to the
next, whatever its edges. So the drawing implies no edge: the edges are
exact in each box's parents line and in the ``Dependencies`` section under
the boxes, which lists every edge between two lanes. ``Node details`` then
gives a card to each running node: its whole title, category and model,
status, group and session.

Every box is one width: as wide as the widest node needs, narrowed to put
each frontier on one row (its category and model line is cut first), and a
frontier that does not fit even then wraps. Under a line or character
budget the boxes give up Node details, then Dependencies, then (where the
caller allows ``compact``) their category and model line; then the tree is
drawn, which nests a node under its dispatcher and draws no edge. So is a
graph whose edges loop, or one too narrow for a box. Widths are terminal
cells: a wide (CJK) character counts 2, and text is folded first so that
what is counted is what is painted. A duration is counted at
`DURATION_CELLS` whatever it reads, so a ticking clock never resizes a box.

Every part of a box carries its node's state as its role (``node:running``),
so a painter colours border, title and status by state; the drawing is
the same text without colour. `/dag` passes a `Focus`: each box then opens
with a fold marker, the highlighted one with ``>``, and its role is
``node:selected``.

Choosing a layout tries several drawings. The choice is kept per structure
(node ids, labels, states and edges, never elapsed time) and size, so a
redraw of the same graph with new durations lays it out once (`_DECISIONS`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final
import unicodedata

from .hud_text import elapsed_text

VIEW_LAYOUTS: Final = ("box", "compact", "tree", "header")
NODE_STATES: Final = (
    "root", "waiting", "dispatched", "running", "stuck", "done", "failed", "interrupted", "unknown",
)
UNKNOWN_FIELD: Final = "?"
ELAPSED_LIMIT: Final = 10**9  # seconds, about 31 years: a figure past it (or not finite) is not drawn
DURATION_CELLS: Final = 7  # a duration's width when a box is measured: `59m 59s`, the widest under 100 hours
DECISION_LIMIT: Final = 64

GAP: Final = 2  # cells between two boxes of a row
BOX_INNER_MIN: Final = 22  # a box's lines are cut down to this many cells, and no further: below it, the tree
BOX_INNER_MAX: Final = 30  # and never drawn wider: `unspecified-high · glm-5.3:low` whole
BOX_MIN_WIDTH: Final = BOX_INNER_MIN + 4
SECTION_INDENT: Final = "  "
TREE_WIDE_MIN: Final = 72  # below this a tree node takes two lines
TREE_TITLE_MAX: Final = 40
TREE_CATEGORY_MAX: Final = 12  # a category wider than this takes its cells from the model column first
TREE_MODEL_MAX: Final = 24
TREE_MODEL_MIN: Final = 8  # the model column gives way to a wide category down to this, then the title does
TREE_LEAD_ROOM: Final = 16  # cells a tree row keeps past its lead: the branch, the glyph and the start of a title

_L, _R, _U, _D = 1, 2, 4, 8


@dataclass(frozen=True)
class Charset:
    name: str
    lines: Mapping[int, str]
    arrow: str
    glyphs: Mapping[str, str]
    pending: str  # a box's glyph for a waiting node
    left: str  # before a box's parents
    right: str  # between the two ends of a dependency
    dot: str  # before a frontier's label
    repair: str  # before the title of a node added to repair another
    extend: str  # before the title of a node added to extend the run
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
    pending="○",
    left="←",
    right="→",
    dot="·",
    repair="↻",
    extend="+",
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
    pending=".",
    left="<-",
    right="->",
    dot="-",
    repair="~",
    extend="+",
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
    boxes: tuple[tuple[str, int, int], ...] = ()  # (node id, first line, last line) per box, in reading order

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


# ── the box layout ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Focus:
    """What `/dag` adds to the boxes: the highlighted node, the folded ones, and whether Node details are drawn.

    With a focus every box opens with its fold marker (``[-]``; ``[+]`` for a
    folded box, which is its title line alone) and the highlighted box has
    ``>`` before its title and the accent border. Without one (the
    transcript, `omh hud --graph`) no key acts on the drawing, so it draws
    neither.
    """

    node: str = ""
    folded: frozenset[str] = frozenset()
    details: bool = True


def _layers(ids: list[str], edges: list[tuple[str, str]]) -> dict[str, int] | None:
    incoming = {node_id: 0 for node_id in ids}
    successors: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for upper, lower in edges:
        incoming[lower] += 1
        successors[upper].append(lower)
    layer = {node_id: 0 for node_id in ids}
    ready = [node_id for node_id in ids if incoming[node_id] == 0]
    visited = 0
    while ready:
        current = ready.pop(0)
        visited += 1
        for lower in successors[current]:
            layer[lower] = max(layer[lower], layer[current] + 1)
            incoming[lower] -= 1
            if incoming[lower] == 0:
                ready.append(lower)
    return layer if visited == len(ids) else None


@dataclass(frozen=True)
class _Frontiers:
    lanes: Mapping[str, Mapping[str, Any]]  # every node but the root, by id
    rows: list[list[str]]  # the frontiers, first to last, each in drawing order
    layer: Mapping[str, int]
    parents: Mapping[str, list[str]]  # per lane, the lanes it has an edge from, in frontier and drawing order
    edges: list[tuple[str, str, str]]  # (from, to, kind) between lanes, in reading order
    origin: Mapping[str, str]  # a lane added mid-run: "repair" or "extend", from the kind of the edge into it


def _frontiers(graph: Mapping[str, Any]) -> _Frontiers | None:
    """The lanes by frontier: a lane's frontier is its longest path of recorded edges from a lane with none.

    The root is not a box: what it dispatched has no parent lane and reads
    ``Start node``, and its model is named in the header. None when there is
    no lane, or the edges between lanes loop.
    """
    lanes = {str(node["id"]): node for node in _nodes(graph) if node.get("kind") != "root"}
    if not lanes:
        return None
    ids = list(lanes)
    pairs = _edges(graph, set(ids))
    layer = _layers(ids, pairs)
    if layer is None:
        return None
    rows = [
        sorted((node_id for node_id in ids if layer[node_id] == index), key=lambda i: (_order(lanes[i]), i))
        for index in range(max(layer.values()) + 1)
    ]
    rank = {node_id: (layer[node_id], at) for row in rows for at, node_id in enumerate(row)}
    kinds: dict[tuple[str, str], str] = {}
    for edge in graph.get("edges") if isinstance(graph.get("edges"), list) else []:
        if isinstance(edge, Mapping):
            kinds.setdefault((str(edge.get("from")), str(edge.get("to"))), clean_text(edge.get("kind")))
    parents: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for upper, lower in pairs:
        parents[lower].append(upper)
    for listed in parents.values():
        listed.sort(key=rank.__getitem__)
    edges = sorted(((upper, lower, kinds.get((upper, lower), "")) for upper, lower in pairs),
                   key=lambda edge: (rank[edge[0]], rank[edge[1]]))
    origin: dict[str, str] = {}
    for _upper, lower, kind in edges:
        if kind == "repair" or (kind == "extend" and lower not in origin):
            origin[lower] = kind
    return _Frontiers(lanes, rows, layer, parents, edges, origin)


def _glyph(node: Mapping[str, Any], charset: Charset, spinner: str | None) -> str:
    state = _state(node)
    if spinner and state == "running" and charset is UNICODE and cells(spinner) == 1:
        return spinner
    return charset.glyphs.get(state, charset.glyphs["unknown"])


def _box_glyph(node: Mapping[str, Any], charset: Charset, spinner: str | None) -> str:
    """The tree's glyph, but a waiting node's is the hollow ``○`` a box draws for a node not handed out."""
    return charset.pending if _state(node) == "waiting" else _glyph(node, charset, spinner)


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _field_role(text: str, role: str) -> str:
    """A field with no record (`?`) is drawn as unknown, whatever field it is."""
    return "unknown" if text == UNKNOWN_FIELD else role


def _facts(node: Mapping[str, Any], charset: Charset) -> list[Segment]:
    """``quick · glm-5.3-flash:low``: the category, then the model and its effort; a field with no record yet is
    left out (``writing`` for a node no child has taken), and ``?`` stands for both only when neither is known."""
    known = [(text, role) for text, role in ((_category(node), "category"), (_model_text(node, ":"), "model"))
             if text != UNKNOWN_FIELD]
    if not known:
        return [Segment(UNKNOWN_FIELD, "unknown")]
    pieces = [Segment(known[0][0], known[0][1])]
    for text, role in known[1:]:
        pieces += [Segment(charset.separator, "model"), Segment(text, role)]
    return pieces


def _origin_mark(node_id: str, shape: _Frontiers, charset: Charset) -> str:
    """``↻ `` before a repair's title, ``+ `` before an extend's: where the node came from, in the colour of its state."""
    kind = shape.origin.get(node_id, "")
    return f"{charset.repair} " if kind == "repair" else f"{charset.extend} " if kind == "extend" else ""


def _box_natural(node_id: str, shape: _Frontiers, charset: Charset, focused: bool, compact: bool) -> int:
    """The inner width a box needs for its title, facts and status, whatever its duration reads; the parents line
    is cut to the box (the Dependencies section holds every edge whole)."""
    node = shape.lanes[node_id]
    lead = cells("[-] > ") if focused else 0
    status = cells(_box_glyph(node, charset, None)) + 1 + _status_cells(node)
    widths = [lead + cells(_origin_mark(node_id, shape, charset)) + cells(_title(node)), status]
    if not compact:
        widths.append(sum(cells(segment.text) for segment in _facts(node, charset)))
    return max(widths)


def _box_content(
    node_id: str, shape: _Frontiers, charset: Charset, inner: int, focus: Focus | None, compact: bool,
    spinner: str | None,
) -> list[tuple[Segment, ...]]:
    """A box's lines: its title; category and model (not in a compact box); its status; its parents."""
    node = shape.lanes[node_id]
    state = _state(node)
    glyph = _box_glyph(node, charset, spinner)
    folded = focus is not None and node_id in focus.folded
    selected = focus is not None and focus.node == node_id
    title = _title(node)
    named = "selected" if selected else state
    head: list[Segment] = []
    if focus is not None:
        head += [Segment("[+] " if folded else "[-] ", "fold")]
        if selected:
            head += [Segment(">", "node:selected"), Segment(" ", "")]
    if folded:
        head += [Segment(glyph, f"node:{state}"), Segment(" ", "")]
    head.append(Segment(_origin_mark(node_id, shape, charset), f"node-title:{named}"))
    head.append(Segment(title, "unknown" if title == UNKNOWN_FIELD else f"node-title:{named}"))
    lines = [head]
    if not folded:
        if not compact:
            lines.append(_facts(node, charset))
        lines.append([Segment(glyph, f"node:{state}"), Segment(" " + _capital(_status_text(node)), f"node:{state}")])
        parents = [_title(shape.lanes[upper]) for upper in shape.parents[node_id]]
        if parents:
            lines.append([Segment(f"{charset.left} ", "parents"), Segment(", ".join(parents), "parents")])
        else:
            lines.append([Segment("Start node", "parents")])
    return [_fit_pieces(line, inner, charset) for line in lines]


def _framed(content: list[tuple[Segment, ...]], inner: int, border: str, charset: Charset) -> list[list[Segment]]:
    """``content`` in a box ``inner + 4`` cells wide: a border, a space each side, the lines padded to ``inner``."""
    lines, rule = charset.lines, charset.lines[_L | _R] * (inner + 2)
    rows = [[Segment(lines[_R | _D] + rule + lines[_L | _D], border)]]
    for line in content:
        used = sum(cells(segment.text) for segment in line)
        rows.append([
            Segment(lines[_U | _D], border), Segment(" ", ""), *line, Segment(" " * (inner - used + 1), ""),
            Segment(lines[_U | _D], border),
        ])
    rows.append([Segment(lines[_R | _U] + rule + lines[_L | _U], border)])
    return rows


def _fit_pieces(pieces: Sequence[Segment], width: int, charset: Charset) -> tuple[Segment, ...]:
    """``pieces`` cut to ``width`` cells: the first that does not fit ends in the ellipsis (alone, when that is all
    the room left), so a cut line never ends on a separator."""
    kept: list[Segment] = []
    used = 0
    for piece in pieces:
        size = cells(piece.text)
        if used + size <= width:
            kept.append(piece)
            used += size
            continue
        if width - used >= cells(charset.ellipsis):
            kept.append(Segment(fit(piece.text, width - used, charset.ellipsis), piece.role, piece.level))
        break
    return _tidy(kept)


def _tidy(pieces: Sequence[Segment]) -> tuple[Segment, ...]:
    """One line: neighbouring pieces of one role and level joined, nothing painted after its last character."""
    joined: list[Segment] = []
    for piece in pieces:
        if not piece.text:
            continue
        if joined and (joined[-1].role, joined[-1].level) == (piece.role, piece.level):
            joined[-1] = Segment(joined[-1].text + piece.text, piece.role, piece.level)
        else:
            joined.append(piece)
    while joined and not joined[-1].text.strip():
        joined.pop()
    if joined:
        joined[-1] = Segment(joined[-1].text.rstrip(), joined[-1].role, joined[-1].level)
    return tuple(joined)


def _run_state(lanes: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """The run's word and the state it is painted as: running while a lane is out, else waiting, else settled."""
    states = {_state(node) for node in lanes}
    if states & {"running", "stuck", "dispatched"}:
        return "Running", "running"
    if "waiting" in states:
        return "Waiting", "waiting"
    if "failed" in states:
        return "Failed", "failed"
    return ("Completed", "done") if states <= {"done"} else ("Ended", "interrupted")


def _box_header(graph: Mapping[str, Any], shape: _Frontiers, width: int, charset: Charset) -> list[tuple[Segment, ...]]:
    """``OMH / DAG · 5 tasks``; the run's name, ``1/1`` and main's model; ``Running · Done 1/5``."""
    sep = charset.separator
    lanes = list(shape.lanes.values())
    hidden = graph.get("hidden_nodes")
    hidden = hidden if isinstance(hidden, int) and not isinstance(hidden, bool) and hidden > 0 else 0
    total = len(lanes) + hidden
    first = [Segment(f"OMH / DAG{sep}{total} task{'' if total == 1 else 's'}", "header")]
    second = [Segment(clean_text(graph.get("name")) or "delegation", "title"), Segment(f"{sep}1/1", "header")]
    root = next((node for node in _nodes(graph) if node.get("kind") == "root"), None)
    model = _model_text(root, ":") if root is not None and clean_text(root.get("model")) else UNKNOWN_FIELD
    if model != UNKNOWN_FIELD:
        second += [Segment(f"{sep}main ", "header"), Segment(model, "model")]
    word, state = _run_state(lanes)
    done = sum(1 for node in lanes if _state(node) == "done")
    third = [Segment(word, f"node:{state}"), Segment(f"{sep}Done {done}/{len(lanes)}", "header")]
    for alarm in ("failed", "stuck"):
        count = sum(1 for node in lanes if _state(node) == alarm)
        if count:
            third += [Segment(sep, "header"), Segment(f"{count} {alarm}", f"node:{alarm}")]
    if hidden:
        third.append(Segment(f"{sep}+{hidden} more node{'' if hidden == 1 else 's'}", "more"))
    return [_fit_pieces(line, width, charset) for line in (first, second, third)]


def _dependencies(shape: _Frontiers, width: int, charset: Charset) -> list[tuple[Segment, ...]]:
    """Every edge between lanes as text, ``Export API → Export button``, its kind named unless it is a
    `depends_on`: the boxes draw one connector per frontier gap, so this is where the edges are exact."""
    lines: list[tuple[Segment, ...]] = [(), (Segment("Dependencies", "header"),)]
    for upper, lower, kind in shape.edges:
        pieces = [
            Segment(SECTION_INDENT, ""), Segment(_title(shape.lanes[upper]), "dependency"),
            Segment(f" {charset.right} ", "connector"), Segment(_title(shape.lanes[lower]), "dependency"),
        ]
        if kind and kind != "depends_on":
            pieces.append(Segment(f" ({kind})", "parents"))
        lines.append(_fit_pieces(pieces, width, charset))
    return lines


def _wrap(text: str, width: int, limit: int, charset: Charset) -> list[str]:
    """``text`` in lines of at most ``width`` cells, broken between words; past ``limit`` lines the last is cut."""
    lines: list[str] = []
    for word in text.split(" "):
        if lines and cells(f"{lines[-1]} {word}") <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    if len(lines) > limit:
        lines = [*lines[:limit - 1], " ".join(lines[limit - 1:])]
    return [fit(line, width, charset.ellipsis) for line in lines]


def _details(
    shape: _Frontiers, order: Sequence[str], width: int, charset: Charset, focus: Focus | None, spinner: str | None
) -> list[tuple[Segment, ...]]:
    """One card per running or stuck node, and the highlighted one: its whole title, category and model, status,
    and the group and session the graph names for it. The graph holds no reply text and no turn or tool counts,
    so a card shows none."""
    shown = [
        node_id for node_id in order
        if _state(shape.lanes[node_id]) in ("running", "stuck") or (focus is not None and focus.node == node_id)
    ]
    if not shown:
        return []
    lines: list[tuple[Segment, ...]] = [(), (Segment("Node details", "header"),)]
    deeper = SECTION_INDENT * 2
    for node_id in shown:
        node = shape.lanes[node_id]
        state = _state(node)
        glyph = _box_glyph(node, charset, spinner)
        lead = SECTION_INDENT + glyph + " "
        title = _title(node)
        for at, text in enumerate(_wrap(title, max(1, width - cells(lead)), 3, charset)):
            pieces = [Segment(SECTION_INDENT, ""), Segment(glyph, f"node:{state}"), Segment(" ", "")] if at == 0 else [
                Segment(" " * cells(lead), "")]
            role = "unknown" if title == UNKNOWN_FIELD else f"node-title:{state}"
            lines.append(_fit_pieces([*pieces, Segment(text, role)], width, charset))
        lines.append(_fit_pieces([Segment(deeper, ""), *_facts(node, charset)], width, charset))
        where = [Segment(_capital(_status_text(node)), f"node:{state}")]
        group = _group_label(node.get("group"), charset)
        if group:
            where.append(Segment(f"{charset.separator}{group}", "more"))
        tail = clean_text(node.get("session_tail"))
        if tail:
            where.append(Segment(f"{charset.separator}session {tail}", "more"))
        lines.append(_fit_pieces([Segment(deeper, ""), *where], width, charset))
    return lines


@dataclass(frozen=True)
class _Boxes:
    lines: list[tuple[Segment, ...]]
    boxes: tuple[tuple[str, int, int], ...]


def _box_view(
    graph: Mapping[str, Any], width: int, charset: Charset, *, compact: bool = False, sections: int = 2,
    focus: Focus | None = None, spinner: str | None = None,
) -> _Boxes | None:
    """The header, the frontiers of boxes, then (``sections`` 1 and up) Dependencies and (2) Node details.

    Every box is one width: as wide as the widest lane needs, up to
    `BOX_INNER_MAX`, and narrower where that puts a frontier on one row,
    down to `BOX_INNER_MIN` (the category and model line is cut first). A
    frontier that does not fit one row even then wraps, at that same width.
    Where one box does not fit `BOX_INNER_MIN`, None, and the tree is drawn.
    """
    shape = _frontiers(graph)
    if shape is None:
        return None
    natural = max(_box_natural(node_id, shape, charset, focus is not None, compact) for node_id in shape.lanes)
    one_row = [(width - (len(row) - 1) * GAP) // len(row) - 4 for row in shape.rows]
    inner = min([max(natural, BOX_INNER_MIN), BOX_INNER_MAX, width - 4,
                 *(fit for fit in one_row if fit >= BOX_INNER_MIN)])
    if inner < BOX_INNER_MIN:
        return None
    box_width = inner + 4
    per_row = max(1, (width + GAP) // (box_width + GAP))
    widest = min(per_row, max(len(row) for row in shape.rows))
    span = widest * box_width + (widest - 1) * GAP
    connector = (span - box_width) // 2 + box_width // 2  # under a lone box's middle, and every row is centred
    lines = [*_box_header(graph, shape, width, charset), ()]
    boxes: list[tuple[str, int, int]] = []
    order: list[str] = []
    for index, frontier in enumerate(shape.rows):
        # A frontier is centred; one that wraps keeps the column its first row starts at, so it reads as one grid.
        offset = (span - min(per_row, len(frontier)) * (box_width + GAP) + GAP) // 2
        for start in range(0, len(frontier), per_row):
            row = frontier[start:start + per_row]
            drawn = [
                _framed(_box_content(node_id, shape, charset, inner, focus, compact, spinner), inner,
                        "node:selected" if focus is not None and focus.node == node_id
                        else f"node:{_state(shape.lanes[node_id])}", charset)
                for node_id in row
            ]
            top = len(lines)
            for node_id, box in zip(row, drawn):
                boxes.append((node_id, top, top + len(box) - 1))
                order.append(node_id)
            for at in range(max(len(box) for box in drawn)):
                pieces = [Segment(" " * offset, "")]
                for column, box in enumerate(drawn):
                    pieces += [Segment(" " * GAP, "")] if column else []
                    pieces += box[at] if at < len(box) else [Segment(" " * box_width, "")]
                lines.append(_tidy(pieces))
        if len(frontier) > 1:
            lines.append(_tidy([Segment(" " * offset, ""), Segment(f"{charset.dot} Same frontier", "frontier")]))
        if index < len(shape.rows) - 1:
            # Dim, and blue where it leads into a frontier with a node running.
            below = {_state(shape.lanes[node_id]) for node_id in shape.rows[index + 1]}
            role = "connector:running" if "running" in below else "connector"
            for mark in (charset.lines[_U | _D], charset.arrow):
                lines.append(_tidy([Segment(" " * connector, ""), Segment(mark, role)]))
    if sections >= 1 and shape.edges:
        lines += _dependencies(shape, width, charset)
    if sections >= 2 and (focus is None or focus.details):
        lines += _details(shape, order, width, charset, focus, spinner)
    if any(cells("".join(segment.text for segment in line)) > width for line in lines):
        return None
    return _Boxes(lines, tuple(boxes))


def _group_label(label: object, charset: Charset) -> str:
    """A group label in this charset: the builder writes ``wave 1 · 14:02``; ASCII draws ``wave 1 - 14:02``."""
    return clean_text(label).replace(UNICODE.separator, charset.separator)


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
        if not isinstance(edge, Mapping) or edge.get("kind") != "dispatch":
            continue
        upper, lower = edge.get("from"), edge.get("to")
        if upper in by_id and lower in by_id and upper != root_id and lower not in parent_of and upper != lower:
            parent_of[str(lower)] = str(upper)
    # A dispatch parent chain that loops is not drawn as nesting.
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
    fields = {node: _tree_fields(by_id[node]) for node in shown}
    title_width = min(TREE_TITLE_MAX, max([cells(_title(by_id[n])) for n in shown] or [12]))
    # The category column is as wide as the widest category: `unspecified-high` cut to `unspecified…` reads as
    # another category. What it takes past TREE_CATEGORY_MAX comes from the model column first.
    category_width = max([cells(category) for category, _model in fields.values()] or [1])
    model_width = min(TREE_MODEL_MAX, max([cells(model) for _category, model in fields.values()] or [1]))
    deepest = max([_depth(n, nested) for n in shown] or [0])
    base = (cells(charset.tee) if labelled else 0) + cells(charset.tee)
    # Where the title column ends, the same for every depth, so the columns after it line up.
    title_end = base + cells(charset.bar) * deepest + 2 + title_width
    room_after = width - title_end - category_width - model_width - 6
    widened = max(0, category_width - TREE_CATEGORY_MAX)
    if room_after < 12 and widened:
        given = min(12 - room_after, widened, max(0, model_width - TREE_MODEL_MIN))
        model_width -= given
        room_after += given
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
            category, model = _tree_fields(node)
            category = fit(category, category_width, charset.ellipsis)
            model = fit(model, model_width, charset.ellipsis)
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
            (category, model), status = _tree_fields(node), _status_text(node)
            # The status is what a reader came for: the model gives way first when the line is full.
            model_room = width - cells(indent) - cells(category) - cells(status) - 2 * cells(sep)
            model = fit(model, max(cells(charset.ellipsis) + 3, model_room), charset.ellipsis) if model else ""
            facts = [*lead_segments(shown_under), Segment("  ", "edge", depth)]
            for text, role in ((category, "category"), (model, "model")):
                if text:  # a field with no record yet is left out with its separator
                    facts += [Segment(text, _field_role(text, role)), Segment(sep, "edge", depth)]
            facts.append(Segment(status, f"status:{state}"))
            lines.append(_cut_line(facts, width, charset))
        kids = [kid for kid in nested.get(node_id, []) if kid not in hidden]
        for at, kid in enumerate(kids):
            node_lines(kid, under, at == len(kids) - 1, depth + 1)

    # A cut that hid nodes of the last group drawn, and nothing else, is counted under that group; one that hid
    # anything else (a whole group, or part of an earlier one) is counted at the root, after the last group.
    under_last = False
    if hidden and labelled and visible_groups:
        last_members = next(members for label, members in groups if label == visible_groups[-1][0])
        under_last = hidden <= _descendants(last_members, nested)
    for group_at, (label, members) in enumerate(visible_groups):
        group_last = group_at == len(visible_groups) - 1
        cut_here = bool(hidden) and group_last and (under_last or not labelled)
        if labelled:
            label_last = group_last and not (hidden and not under_last)
            prefix = charset.end if label_last else charset.tee
            text = fit(_group_label(label, charset) or "lanes", width - cells(prefix), charset.ellipsis)
            lines.append((Segment(prefix, "edge"), Segment(text, "group")))
            lead = charset.gap if label_last else charset.bar
        else:
            lead = ""
        for at, member in enumerate(members):
            node_lines(member, lead, at == len(members) - 1 and not cut_here, 0)
    if hidden:
        done = sum(1 for node in hidden if _state(by_id[node]) == "done")
        tail = f" ({done} done)" if done else ""
        text = f"+{len(hidden)} more node{'' if len(hidden) == 1 else 's'}{tail}"
        lead = [Segment(charset.gap, "edge")] if under_last else []
        room = width - cells(charset.end) - (cells(charset.gap) if under_last else 0)
        lines.append((*lead, Segment(charset.end, "edge"), Segment(fit(text, room, charset.ellipsis), "more")))
    return lines


def _descendants(members: Sequence[str], nested: Mapping[str, list[str]]) -> set[str]:
    """``members`` and every node nested under them."""
    found: set[str] = set()
    pending = list(members)
    while pending:
        node = pending.pop()
        if node not in found:
            found.add(node)
            pending += nested.get(node, [])
    return found


def _tree_fields(node: Mapping[str, Any]) -> tuple[str, str]:
    """The tree's category and model columns: a field with no record yet is blank, and ``?`` stands in the category
    column only when neither is known, as in a box."""
    category, model = _category(node), _model_text(node, ":")
    if category == UNKNOWN_FIELD and model == UNKNOWN_FIELD:
        return UNKNOWN_FIELD, ""
    return ("" if category == UNKNOWN_FIELD else category), ("" if model == UNKNOWN_FIELD else model)


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
    focus: Focus | None = None,
) -> View:
    """The best drawing of ``graph`` that fits: boxes with their Dependencies and Node details, else boxes with fewer
    sections, else (with ``compact``) three-line boxes, else the tree, else the header alone.

    ``layout`` narrows the choice: ``box`` tries the boxes, ``compact`` only
    three-line ones (title, status, parents), and ``tree`` only the tree; each
    still falls back to the tree and the header. ``focus`` is `/dag`'s
    highlight, folds and details switch. ``max_chars`` counts the joined text,
    newlines included. Never raises on a malformed graph: what cannot be read
    is left out, and what cannot be laid out is drawn as the tree. A graph
    with no nodes draws nothing.
    """
    chosen = CHARSETS.get(charset, UNICODE)
    safe_width = max(8, int(width)) if isinstance(width, int) and not isinstance(width, bool) else 80
    if not isinstance(graph, Mapping) or not _nodes(graph):
        return View((), "header")
    header = _header_lines(graph, safe_width, chosen)
    key = (_structure(graph), safe_width, max_lines, max_chars, chosen.name, layout, spinner, compact, focus)

    def fits(lines: Sequence[tuple[Segment, ...]]) -> bool:
        if max_lines is not None and len(lines) > max_lines:
            return False
        if max_chars is not None:
            size = sum(len("".join(segment.text for segment in line)) for line in lines) + max(0, len(lines) - 1)
            if size > max_chars:
                return False
        return True

    def boxes(compact_boxes: bool, sections: int) -> View | None:
        drawn = _box_view(graph, safe_width, chosen, compact=compact_boxes, sections=sections, focus=focus,
                          spinner=spinner)
        if drawn is None or not fits(drawn.lines):
            return None
        return View(tuple(drawn.lines), "compact" if compact_boxes else "box", boxes=drawn.boxes)

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
    view, decision = _choose(graph, safe_width, chosen, header, layout, compact, boxes, tree, fits)
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
    boxes: Any,
    tree: Any,
    fits: Any,
) -> tuple[View, tuple[str, Any]]:
    """The drawing `render_delegation_view` returns, and the decision that redraws it.

    Under a budget the boxes give up Node details first, then Dependencies
    (each box still names its parents), then, where the caller allows it,
    their category and model line.
    """
    tiers = [(False, sections) for sections in (2, 1, 0)] if layout in ("auto", "box") else []
    if layout == "compact" or (layout == "auto" and compact):
        tiers.append((True, 0))
    if width >= BOX_MIN_WIDTH:
        for compact_boxes, sections in tiers:
            drawn = boxes(compact_boxes, sections)
            if drawn is not None:
                return drawn, ("compact" if compact_boxes else "box", sections)
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
