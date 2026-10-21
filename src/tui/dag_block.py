"""A dag-ulw run's graph printed into the transcript at the moments its plan takes or changes shape.

The live state stays in the panel; this is what the scrollback keeps (owner
decision 2026-10-21). When an `omh_dag` call completes, the transcript gets
one block holding the run's graph, drawn by `delegation_view`'s public entry
with the box layout, which steps down to the tree where boxes do not fit the
width:

* at ``define``: the plan as it starts, its first layer handed out;
* at ``repair`` and ``extend``: the graph with the added nodes marked ``+``,
  each hanging from where it came from;
* once at the run's end (done, out of repair cycles, or cancelled): the
  final states. The app prints it when the turn ends and only if the run is
  still over then, so an end the same turn's extend moved on prints nothing.

Never on ``ready``, ``settle`` or ``status`` (a settle that ends the run is
the end moment, not a settle), never twice for one moment, and never on a
messenger: a chat reply carries its three moments and no boxes.

`topology_lines` is the other half of a run's picture: `/dag` closes the
whole graph with the run's critical path and bottlenecks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any

from .style import Line, Span
from .theme import Theme
from .width import truncate_line

DAG_TOOL = "omh_dag"
END = "end"  # the title an end moment opens with
ADDED_MARK = "+ "
END_STATES = ("done", "stopped", "cancelled")
INDENT = "  "


@dataclass
class DagBlock:
    title: str  # what the moment is: "plan", "repair · +1 node", "extend · +3 nodes", "end · done"
    graph: Mapping[str, Any]
    done: bool = True


def moment(payload: Mapping[str, Any], ended: set[tuple[str, int]]) -> tuple[str, str, list[str]] | None:
    """(title, run, added node ids) when a completed tool call is a print moment, else None.

    ``ended`` holds the run ends already printed, keyed by run and node count,
    so a run's end prints once, and again only after an extend round reopened
    it.
    """
    if str(payload.get("name") or "") != DAG_TOOL:
        return None
    raw = payload.get("result")
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(result, Mapping) or result.get("status") != "ok" or not isinstance(result.get("run"), str):
        return None
    run, action, settled = result["run"], result.get("action"), result.get("settled")
    added = settled.get(f"{action}_nodes") if isinstance(settled, Mapping) else None
    if action == "define" and not result.get("reused"):
        return "plan", run, []
    if action in ("repair", "extend") and isinstance(added, list) and added:
        count = len(added)
        return f"{action} · +{count} node{'' if count == 1 else 's'}", run, [str(node) for node in added]
    state = result.get("state")
    nodes = result.get("nodes") if isinstance(result.get("nodes"), list) else []
    # The end is the settle (or cancel) that ended the run; a later status or ready reading it says nothing new.
    if action in ("settle", "cancel") and state in END_STATES and (run, len(nodes)) not in ended:
        ended.add((run, len(nodes)))
        return f"{END} · {state}", run, []
    return None


def marked(graph: Mapping[str, Any], run: str, added: list[str]) -> dict[str, Any]:
    """A copy of ``graph`` whose nodes ``added`` to ``run`` open their titles with `ADDED_MARK`."""
    ids = {f"d:{run}:{node}" for node in added}
    nodes = [
        {**node, "title": ADDED_MARK + str(node.get("title") or "")} if node.get("id") in ids else node
        for node in graph.get("nodes") or []
        if isinstance(node, Mapping)
    ]
    return {**graph, "nodes": nodes}


def read_graph(omh_home: str | None, hermes_home: str | None, session: str) -> Mapping[str, Any] | None:
    """The session's active delegation graph, read the way the HUD panel reads it, or None."""
    from ..plugin_bundle.omh.runtime_reader import read_omh_hud

    if not session:
        return None
    payload = read_omh_hud(omh_home, hermes_home, session_ref=session, session_scoped=True,
                           tui_identity_expected=True)
    graph = payload.get("delegation_graph") if isinstance(payload, Mapping) else None
    return graph if isinstance(graph, Mapping) and graph.get("status") == "active" else None


def rows(block: DagBlock, width: int, theme: Theme) -> list[Line]:
    """The block's rows: its moment, then the graph as boxes, or the tree where boxes do not fit ``width``."""
    from ..plugin_bundle.omh.delegation_view import render_delegation_view
    from .panels import dag_line

    view = render_delegation_view(block.graph, width=max(8, width - len(INDENT)), layout="box")
    lines = [truncate_line((Span(f"{INDENT}dag-ulw {block.title}", theme.muted),), width)]
    lines += [truncate_line((Span(INDENT), *dag_line(row, view.levels, theme)), width) for row in view.lines]
    return lines


def topology_lines(topology: Mapping[str, Any], width: int, theme: Theme) -> list[Line]:
    """`/dag`'s closing lines for a dag-ulw run: its critical path and the nodes the most others wait on."""
    lines: list[Line] = []
    path = [str(label) for label in topology.get("critical_path") or []]
    if path:
        lines.append(truncate_line((Span("critical path: ", theme.muted), Span(" → ".join(path), theme.text)), width))
    neck = [f"{label} (blocks {count})" for label, count in topology.get("bottlenecks") or []]
    if neck:
        lines.append(truncate_line((Span("bottlenecks: ", theme.muted), Span(" · ".join(neck), theme.text)), width))
    return lines
