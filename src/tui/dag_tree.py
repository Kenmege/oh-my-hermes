"""A dag run's live tree under the composer: one header row per node, what its child does now under it, folding.

The owner's ask of 2026-10-22 ("dag 돌면 프롬프트하단은 또 어떻게 뜸? 삼각형 및
탭으로 연관 하위 공백으로 영역 표시"): each node a header row, what that child
is doing indented under it, triangles that fold and unfold, blank rows
between areas.

The rows, top to bottom: the graph's header (`delegation_view.header_text`);
then each layer, a muted label row (`▾ layer 2 · 4 nodes · 2 running`), with
one blank row between two layers when either is open (shut layers stack);
under an open layer, one header row per node, four cells in: `▾` open or `▸`
shut (none on a node with nothing under it), its state glyph in the state's
colour (the running spinner in the running blue), its label in bold, and
`category · model:effort · status · ↓tokens` muted at the right edge (main's
own fields, `delegation_view._tree_fields`, `_status_text`, `_tokens_text`).
Under an open node, up to `DETAIL_ROWS` rows four cells further in, each led
by a muted `›`: its child's latest tool call (`terminal(gh pr checkout 1)`,
the name in its family's hue), then the newest other line the child sent: a
thinking line or its reply (`Agent.said`), or Hermes' progress line
(`Agent.progress`, the names of its last five calls). A progress line whose
last name is the call the first row already shows gives way to the thinking
or reply line, or, with none, drops that name (`_second_row`). A node
a repair or an extend added hangs under the node it came from, as the tree's
nesting edges say (`delegation_view._tree_structure`), led by `↻ repair N`
(`+ extend`). The graph carries no repair cycle (`dag_run` records one on
the node; the graph does not copy it), so N counts the repair edges under
that node.

A node's child is the agent whose child session the node names
(`session_tail`), else the newest agent whose goal's `[dag:<run>/<node>]`
marker binds it to the node (`Agent.dag_node`). The roster's other agents
(a helper the parent delegated beside the run; not a lane of this run the
graph no longer draws) close the tree as a group of their own, `other
agents`, each a row like a node's, so the keys reach them as the agent list
did.

Folding. Running, stuck and failed nodes open, and so does a node with one
of them under it (an extend round hangs under the done verification that
found the gap); done, waiting and handed-out ones stay shut. What the person folds or opens (`overrides`) wins. A tree
taller than `cap` rows first shuts the quiet layers (no node running, stuck
or failed), all-done ones first; then, in a layer with work moving, folds its
quiet nodes into one row at the layer's end (`▸ 12 more · 9 waiting · 3
done`); then shuts the open nodes the person did not open, bottom up, except
one with a running or failed node under it. What still does not fit is cut:
the last row says `… N more nodes · ↓ to browse`; while the tree has the
keys (`selected`) the rows scroll to the highlight instead, between
`… N more nodes above` and `… N more nodes below`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

from .panels import DAG_INDENT, SPINNER_FRAMES, dag_style
from .style import TRUECOLOR, Line, Span
from .subagents import (
    COMPLETED, FAILED_STATUSES, INTERRUPTED, LOG_PROGRESS, LOG_THINKING, LOG_TOOL, QUEUED, RUNNING, Agent, LogRow,
    Roster, short_title,
)
from .theme import Theme, kind_style
from .width import cell_width, truncate, truncate_line

OPEN = "▾ "
SHUT = "▸ "
LEAF = "  "
DETAIL_LEAD = "› "
STEP = 4  # cells a node's detail rows and the nodes nested under it sit right of its triangle
DETAIL_ROWS = 2
LAYER_KEY = "layer:"
OTHERS_KEY = "others:"  # the group of the roster's agents no node is bound to; `agent:` + its id for each of them
OTHERS = "other agents"
# An agent's status as a node's state, for a row of the `other agents` group.
AGENT_STATES = {RUNNING: "running", QUEUED: "waiting", INTERRUPTED: "interrupted", COMPLETED: "done",
                **{status: "failed" for status in FAILED_STATUSES}}
MORE_KEY = "more:"  # + a layer's label: the row that layer's quiet nodes fold into
OPEN_STATES = frozenset({"running", "stuck", "failed"})  # open by default; a layer holding one is never shut to fit
MIN_TITLE_CELLS = 12
FACT_SEPARATOR = " · "


@dataclass
class _Item:
    key: str  # a node's graph id, or `LAYER_KEY` + a layer's label
    depth: int  # 0 a layer, 1 a node of it, 2 and on a node nested under another
    parent: str = ""
    node: Mapping[str, Any] | None = None  # None for a layer
    state: str = ""  # the node's state (`delegation_view` reads it); "" for a layer
    label: str = ""  # a layer's label
    origin: str = ""  # `↻ repair 1` or `+ extend` for a node a repair or an extend added
    kids: list[_Item] = field(default_factory=list)  # a layer's nodes, or the nodes nested under a node
    agent: Agent | None = None
    details: tuple[LogRow, ...] = ()


@dataclass(frozen=True)
class TreeState:
    """What the keys need from the tree as last drawn."""

    order: tuple[str, ...]  # the rows the keys move over, top to bottom; a fold leaves its hidden rows out
    parent: Mapping[str, str]  # each row's parent row: a node's layer or node, a `more` row's layer
    expanded: Mapping[str, bool]
    foldable: frozenset[str]
    first: str  # where Down opens: the first running node, else the first node
    agents: Mapping[str, str] = field(default_factory=dict)  # a node's key -> its child's subagent id
    reveals: Mapping[str, str] = field(default_factory=dict)  # a `more` row -> the first node opening it shows


def _bound(by_id: Mapping[str, Mapping[str, Any]], roster: Roster) -> dict[str, Agent]:
    """Each node's agent: the one whose child session it names (`session_tail`), else the newest one its goal's
    `[dag:<run>/<node>]` marker bound to it."""
    from ..plugin_bundle.omh.delegation_graph import session_tail

    agents = list(roster.agents.values())
    by_tail = {session_tail(agent.child_session_id): agent for agent in agents if agent.child_session_id}
    by_node = {agent.dag_node: agent for agent in agents if agent.dag_node}
    bound: dict[str, Agent] = {}
    for node_id, node in by_id.items():
        tail = node.get("session_tail")
        agent = by_tail.get(tail) if isinstance(tail, str) and tail else None
        agent = agent or by_node.get(node_id)
        if agent is not None and node.get("kind") != "root":
            bound[node_id] = agent
    return bound


def _second_row(agent: Agent) -> LogRow | None:
    """The newest line besides a call: thinking or reply, or Hermes' progress line, never one that repeats the call
    the first row shows. Hermes joins a progress line's names with ", " (`delegate_tool_progress._flush`)."""
    progress, said, call = agent.progress, agent.said, agent.last_call
    if progress is None or (said is not None and not agent.progress_newest):
        return said or progress
    names = progress.text.split(", ")
    if call is None or names[-1] != call.text:
        return progress
    if said is not None:
        return said
    rest = ", ".join(names[:-1])
    return LogRow(LOG_PROGRESS, rest) if rest else None


def _details(agent: Agent | None) -> tuple[LogRow, ...]:
    if agent is None:
        return ()
    return tuple(row for row in (agent.last_call, _second_row(agent)) if row is not None)[:DETAIL_ROWS]


def _agent_node(agent: Agent, now: float) -> dict[str, Any]:
    """An agent no node is bound to, as a node row draws one: its short title, model, status, time and tokens."""
    state = AGENT_STATES.get(agent.status, "unknown")
    return {"id": f"agent:{agent.subagent_id}", "kind": "lane", "title": short_title(agent.goal) or agent.name,
            "category": "", "model": agent.model or "?", "effort": "", "state": state, "status": state,
            "elapsed_seconds": agent.elapsed(now), "output_tokens": agent.output_tokens}


def _items(graph: Mapping[str, Any], roster: Roster, now: float) -> list[_Item]:
    from ..plugin_bundle.omh.delegation_view import _state, _tree_structure

    _root, groups, nested, by_id = _tree_structure(graph)
    agents = _bound(by_id, roster)
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    kinds = {(edge.get("from"), edge.get("to")): edge.get("kind") for edge in edges if isinstance(edge, Mapping)}

    def node_item(node_id: str, depth: int, parent: str, origin: str = "") -> _Item:
        agent = agents.get(node_id)
        item = _Item(node_id, depth, parent, by_id[node_id], _state(by_id[node_id]), origin=origin, agent=agent,
                     details=_details(agent))
        repairs = 0
        for kid in nested.get(node_id, []):
            kind = kinds.get((node_id, kid))
            repairs += kind == "repair"
            mark = f"↻ repair {repairs}" if kind == "repair" else "+ extend" if kind == "extend" else ""
            item.kids.append(node_item(kid, depth + 1, node_id, mark))
        return item

    layers = []
    for label, members in groups:
        key = f"{LAYER_KEY}{label}"
        layer = _Item(key, 0, label=label)
        layer.kids = [node_item(member, 1, key) for member in members]
        layers.append(layer)
    bound = {agent.subagent_id for agent in agents.values()}
    # A lane of this run the graph no longer draws (a superseded repair attempt) is not another agent.
    runs = {node_id.rpartition(":")[0] for node_id in by_id if node_id.startswith("d:")}
    others = [agent for agent in roster.agents.values()
              if agent.subagent_id not in bound and agent.dag_node.rpartition(":")[0] not in runs]
    if others:
        group = _Item(OTHERS_KEY, 0, label=OTHERS)
        for agent in others:
            node = _agent_node(agent, now)
            group.kids.append(
                _Item(node["id"], 1, OTHERS_KEY, node, node["state"], agent=agent, details=_details(agent))
            )
        layers.append(group)
    return layers


def _walk(items: Sequence[_Item]) -> list[_Item]:
    found: list[_Item] = []
    for item in items:
        found += [item, *_walk(item.kids)]
    return found


def _foldable(item: _Item) -> bool:
    return bool(item.kids or item.details)


def _active(item: _Item) -> bool:
    """Whether a running, stuck or failed node is the item or under it."""
    return any(each.state in OPEN_STATES for each in _walk([item]))


def _quiet(layer: _Item) -> list[_Item]:
    """A layer's nodes with nothing running or failed in them: what folds into its `more` row."""
    return [item for item in layer.kids if not _active(item)]


def _specs(layers: Sequence[_Item], expanded: Mapping[str, bool]) -> list[tuple[str, _Item | None, Any]]:
    """The rows as (kind, item, detail) before they are drawn: `header`, `blank`, `layer`, `node`, `detail` (its log
    row), and `more` (its layer's folded nodes)."""
    specs: list[tuple[str, _Item | None, Any]] = [("header", None, None)]

    def node_specs(item: _Item) -> None:
        specs.append(("node", item, None))
        if expanded.get(item.key):
            specs.extend(("detail", item, row) for row in item.details)
            for kid in item.kids:
                node_specs(kid)

    labelled = len(layers) > 1 or any(layer.label for layer in layers)  # one unnamed group draws no label row
    for at, layer in enumerate(layers):
        if at and (expanded.get(layer.key) or expanded.get(layers[at - 1].key)):  # shut layers stack as one list
            specs.append(("blank", None, None))
        if labelled:
            specs.append(("layer", layer, None))
        if expanded.get(layer.key) or not labelled:
            folded = [] if expanded.get(MORE_KEY + layer.label, True) else _quiet(layer)
            for item in layer.kids:
                if item not in folded:
                    node_specs(item)
            if folded:
                specs.append(("more", layer, folded))
    return specs


def _fitted(layers: Sequence[_Item], overrides: Mapping[str, bool], cap: int) -> dict[str, bool]:
    """Which rows are open: by state, then the person's folds, then shut to fit `cap` rows (the module says how)."""
    every = _walk(layers)
    # Open by default: a layer, a running, stuck or failed node, and one with such a node under it (an extend round
    # hangs under the verification that found the gap, done itself).
    expanded = {
        item.key: _foldable(item) and overrides.get(item.key, item.node is None or _active(item))
        for item in every
    }
    expanded.update({MORE_KEY + layer.label: overrides.get(MORE_KEY + layer.label, True) for layer in layers})

    def fits() -> bool:
        return len(_specs(layers, expanded)) <= cap

    if fits():
        return expanded
    labelled = len(layers) > 1 or any(layer.label for layer in layers)
    quiet = [layer for layer in layers if labelled and layer.key not in overrides and expanded[layer.key]
             and not _active(layer)]
    quiet.sort(key=lambda layer: not all(item.state == "done" for item in _walk(layer.kids)))  # all-done ones first
    for layer in quiet:
        expanded[layer.key] = False
        if fits():
            return expanded
    for layer in layers:
        key = MORE_KEY + layer.label
        if key not in overrides and (expanded[layer.key] or not labelled) and _active(layer) and len(_quiet(layer)) > 1:
            expanded[key] = False
            if fits():
                return expanded
    for item in reversed([item for item in every if item.node is not None]):
        # A node over a running repair stays open: shutting it would hide work that moves.
        if item.key not in overrides and expanded[item.key] and not any(_active(kid) for kid in item.kids):
            expanded[item.key] = False
            if fits():
                return expanded
    return expanded


def _figures(node: Mapping[str, Any]) -> list[str]:
    """The header's figures, fullest first: `category · model:effort · status · ↓tokens`, then shorter forms."""
    from ..plugin_bundle.omh.delegation_view import UNICODE, _status_text, _tokens_text, _tree_fields

    category, model = _tree_fields(node)
    status, tokens = _status_text(node), _tokens_text(node, UNICODE)
    forms = [[category, model, status, tokens], [category, model, status], [model, status], [status]]
    return [FACT_SEPARATOR.join(part for part in form if part) for form in forms]


def _node_row(item: _Item, expanded: bool, width: int, theme: Theme, frame: int) -> list[Span]:
    from ..plugin_bundle.omh.delegation_view import UNICODE

    node = item.node or {}
    glyph = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)] if item.state == "running" else UNICODE.glyphs.get(
        item.state, UNICODE.glyphs["unknown"])
    lead = " " * (len(DAG_INDENT) + 2 + STEP * (item.depth - 1))
    triangle = (OPEN if expanded else SHUT) if _foldable(item) else LEAF
    origin = f"{item.origin}{FACT_SEPARATOR}" if item.origin else ""
    fixed = cell_width(f"{lead}{triangle}{glyph} {origin}")
    figure, room = "", width - fixed
    for figure in [*_figures(node), ""]:  # ends on "", which always stays
        room = width - fixed - (cell_width(f"  {figure}") if figure else 0)
        if room >= MIN_TITLE_CELLS:
            break
    title = truncate(str(node.get("title") or "untitled"), max(room, 1))
    pad = " " * max(room - cell_width(title), 0)
    return [
        Span(lead), Span(triangle, theme.muted), Span(glyph, dag_style(f"glyph:{item.state}", theme)), Span(" "),
        Span(origin, theme.muted), Span(title, dag_style("title", theme)),
        Span(f"{pad}  {figure}" if figure else "", theme.muted),
    ]


def _detail_row(item: _Item, row: LogRow, theme: Theme) -> list[Span]:
    lead = [Span(" " * (len(DAG_INDENT) + 2 + STEP * item.depth)), Span(DETAIL_LEAD, theme.muted)]
    if row.kind == LOG_TOOL:
        args = f"({row.detail})" if row.detail else ""
        return [*lead, Span(row.text, replace(kind_style(row.text, theme), bold=False)), Span(args, theme.muted)]
    style = theme.thinking if row.kind == LOG_THINKING else theme.muted if row.kind == LOG_PROGRESS else theme.text
    return [*lead, Span(row.text, style)]


def _layer_row(item: _Item, expanded: bool, theme: Theme) -> list[Span]:
    counts: dict[str, int] = {}
    for kid in item.kids:
        word = "running" if kid.state in ("running", "stuck") else kid.state
        word = word if word in ("running", "failed", "done") else "waiting"
        counts[word] = counts.get(word, 0) + 1
    noun = "agent" if item.key == OTHERS_KEY else "node"
    said = [f"{len(item.kids)} {noun}{'' if len(item.kids) == 1 else 's'}"]
    said += [f"{counts[word]} {word}" for word in ("running", "failed", "waiting", "done") if counts.get(word)]
    return [Span(DAG_INDENT), Span(OPEN if expanded else SHUT, theme.muted),
            Span(FACT_SEPARATOR.join([item.label or "lanes", *said]), theme.muted)]


def _more_row(folded: Sequence[_Item], theme: Theme) -> list[Span]:
    waiting = sum(1 for item in folded if item.state != "done")
    said = [f"{len(folded)} more", *([f"{waiting} waiting"] if waiting else []),
            *([f"{len(folded) - waiting} done"] if len(folded) - waiting else [])]
    return [Span(DAG_INDENT + "  "), Span(SHUT, theme.muted), Span(FACT_SEPARATOR.join(said), theme.muted)]


def _covers(kind: str, item: _Item | None, expanded: Mapping[str, bool], detail: Any = None) -> int:
    """How many nodes a row stands for: a node's header itself and, shut, what is nested under it; a shut layer's
    nodes; a `more` row's."""
    if kind == "more":
        return len(_walk(detail))
    if item is None or kind == "detail":
        return 0
    if kind == "layer":
        return 0 if expanded.get(item.key) else len(_walk(item.kids))
    return 1 if expanded.get(item.key) else len(_walk([item]))


def _more(count: int, where: str, theme: Theme, depth: str) -> Line:
    text = f"… {count} more node{'' if count == 1 else 's'}{where}"
    return (Span(DAG_INDENT), Span(text, replace(theme.muted, depth=depth)))


def tree_lines(
    graph: Mapping[str, Any],
    roster: Roster,
    width: int,
    cap: int,
    theme: Theme,
    frame: int,
    overrides: Mapping[str, bool],
    selected: str | None,
    depth: str = TRUECOLOR,
    now: float = 0.0,
) -> tuple[list[Line], TreeState]:
    """The tree in `cap` rows at most, none wider than `width`, painted at `depth` as the graph is everywhere
    (`style.color_depth`), and what the keys need from it; `selected` (a key of the state's `order`) is one
    `menu_current` bar across the row while the tree has the keys, else None."""
    from ..plugin_bundle.omh.delegation_view import header_text

    layers = _items(graph, roster, now)
    expanded = _fitted(layers, overrides, cap)
    rows: list[tuple[Line, str, int]] = []  # (line, the key it selects or "", the nodes it stands for)
    for kind, item, detail in _specs(layers, expanded):
        if kind == "header":
            spans = [Span(DAG_INDENT), Span(header_text(graph), dag_style("header", theme))]
        elif kind == "blank" or item is None:
            spans = []
        elif kind == "layer":
            spans = _layer_row(item, expanded[item.key], theme)
        elif kind == "node":
            spans = _node_row(item, expanded[item.key], width, theme, frame)
        elif kind == "more":
            spans = _more_row(detail, theme)
        else:
            spans = _detail_row(item, detail, theme)
        key = item.key if item is not None and kind in ("layer", "node") else ""
        key = MORE_KEY + item.label if kind == "more" and item is not None else key
        line: Line = tuple(spans)
        if key and key == selected:  # one bar across the row, as the agent list highlights its row
            text = "".join(span.text for span in spans)
            line = (Span(text + " " * max(width - cell_width(text), 0), theme.menu_current),)
        line = tuple(span if span.style.depth == depth else Span(span.text, replace(span.style, depth=depth))
                     for span in line)
        rows.append((truncate_line(line, width), key, _covers(kind, item, expanded, detail)))
    every = _walk(layers)
    order = tuple(key for _line, key, _count in rows if key)
    shown = [item for item in every if item.node is not None and item.key in order]
    running = [item.key for item in shown if item.state == "running"]
    state = TreeState(
        order=order,
        parent={item.key: item.parent for item in every} | {MORE_KEY + layer.label: layer.key for layer in layers},
        expanded=expanded,
        foldable=frozenset(item.key for item in every if _foldable(item))
        | {MORE_KEY + layer.label for layer in layers if len(_quiet(layer)) > 1},
        first=(running or [item.key for item in shown] or list(order) or [""])[0],
        agents={item.key: item.agent.subagent_id for item in every if item.agent is not None},
        reveals={MORE_KEY + layer.label: _quiet(layer)[0].key for layer in layers if _quiet(layer)},
    )
    return _window(rows, cap, selected, partial(_more, theme=theme, depth=depth)), state


def _window(rows: list[tuple[Line, str, int]], cap: int, selected: str | None, more: Callable[..., Line]) -> list[Line]:
    """`rows` in `cap` rows: all of them when they fit; else, without the keys, the first ones and a last row counting
    what is cut; with them, the rows around the highlight between counts of what is above and below."""
    if len(rows) <= cap:
        return [line for line, _key, _count in rows]
    if cap < 2:
        return [line for line, _key, _count in rows[:cap]]

    def counted(cut: Sequence[tuple[Line, str, int]]) -> int:
        return sum(count for _line, _key, count in cut)

    if selected is None:
        return [line for line, *_rest in rows[: cap - 1]] + [more(counted(rows[cap - 1 :]), " · ↓ to browse")]
    index = next((at for at, (_line, key, _count) in enumerate(rows) if key == selected), 0)
    body = cap - 2
    start = min(max(index - body // 2, 0), len(rows) - body)
    if start == 0:
        return [line for line, *_rest in rows[: cap - 1]] + [more(counted(rows[cap - 1 :]), " below")]
    if start + body >= len(rows):
        tail = rows[len(rows) - cap + 1 :]
        return [more(counted(rows[: len(rows) - cap + 1]), " above")] + [line for line, *_rest in tail]
    return (
        [more(counted(rows[:start]), " above")]
        + [line for line, *_rest in rows[start : start + body]]
        + [more(counted(rows[start + body :]), " below")]
    )
