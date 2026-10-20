"""Delegated work in a chat reply: once when a run starts, a count line while it runs, once when it ends.

OMH sends no message. Hermes calls `transform_llm_output` once per turn with
the reply it is about to deliver (`agent/turn_finalizer.py`), and a messaging
gateway delivers a transformed reply on purpose, editing the streamed message
to include what a plugin appended (`gateway/run_turn.py`). On a chat platform
(`MESSENGER_PLATFORMS`) this appends to that reply at three moments of a run,
the conversation's lanes that no end post has closed yet:

* **start**, once: a code block, ``DAG · <name> · <N> tasks · <W> layers``,
  then one line per layer listing its tasks' titles, at most `MOMENT_LINES`
  of them and then ``+N more``;
* **during**, no graph: on a turn where the counts changed since the last
  post, one plain line, ``DAG <name> · <done>/<total> done · <running>
  running``, and the failures with the first one's title when there are any;
* **end**, once, on the first reply after every lane has settled: a short
  block that puts each lane under Done, Failed or Stopped (and Working, only
  while one still runs), names the first failure, and gives the totals.

A run whose first reply finds it already settled (a synchronous call) gets the
end post alone. A chat reply never carries the box drawing: the OMH TUI and
`omh hud --graph` draw that.

Nothing is appended:

* with ``OMH_SUBAGENT_GRAPH=off`` in Hermes' environment, the switch that
  already turns the HUD's graphs off;
* on a delegated child's own turn, or with no active graph;
* when the graph's fingerprint (structure, states and labels, never elapsed
  time) equals the one the last post recorded, or a running run's counts did
  not change;
* unless the post can be recorded in the delegation ledger, so a ledger that
  cannot be written never turns into a post on every reply, and a restart
  does not post it again;
* when the reply ends inside an open code fence.

When the reply and a start or end block together pass the platform's
single-message cap (`MESSAGE_CAPS`, measured the way Hermes' adapter measures
it), the count line goes alone in its place. When even that does not fit,
nothing is appended: the skip is recorded in the ledger (`note_skipped`) and
the moment stays due, so a later reply that fits carries it. The reply's own
text is never changed: the post follows it (`appended`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import os
import time
from typing import Any, Final

from . import runtime_paths
from .delegation_graph import GRAPH_LINGER_SECONDS, build_delegation_graph, graph_fingerprint
from .delegation_ledger import note_posted, note_skipped, posted_record, root_session
from .delegation_view import UNICODE, cells, clean_text, fit

MESSENGER_PLATFORMS: Final = frozenset({"discord", "slack", "telegram", "mattermost", "matrix"})
FENCE: Final = "```"
GRAPH_SWITCH: Final = "OMH_SUBAGENT_GRAPH"
MOMENT_LINES: Final = 8  # a start block's layer lines; past them, `+N more`
LINE_CELLS: Final = 72  # one listing line; titles that do not fit are counted as `+N`
LABEL_CELLS: Final = 28  # one title in a listing
STATUS_CELLS: Final = 20
# One message's cap on each platform and the unit its Hermes adapter counts, at v0.22.2 (d923a5eb48) and on main
# (8b9ac5773e) alike, in plugins/platforms/<platform>/adapter.py: Discord's and Slack's MAX_MESSAGE_LENGTH (2,000;
# 39,000), Telegram's 4,096 counted by `utf16_len`, Mattermost's MAX_POST_LENGTH (4,000), and Matrix's
# DEFAULT_MAX_MESSAGE_LENGTH (45,000 // 3 UTF-8 bytes), which MATRIX_MAX_MESSAGE_LENGTH can lower to 500.
MESSAGE_CAPS: Final = {
    "discord": (2_000, "chars"),
    "slack": (39_000, "chars"),
    "telegram": (4_096, "utf16"),
    "mattermost": (4_000, "chars"),
    "matrix": (15_000, "utf8"),
}
MATRIX_CAP_ENV: Final = "MATRIX_MAX_MESSAGE_LENGTH"
# The share of a cap the reply and post may fill: the rest is left for the adapter's own formatting (Telegram's
# escapes, Matrix's HTML body), which adds to the text before it is measured.
CAP_SHARE: Final = 0.9
SKIPPED_CAP: Final = "graph_skipped_cap"
SKIPPED_OPEN_FENCE: Final = "graph_skipped_open_fence"
STOPPED_STATES: Final = frozenset({"interrupted", "unknown"})
WORKING_STATES: Final = frozenset({"running", "stuck", "dispatched", "waiting"})
# The end post's collapse, in the order it lists them, with the glyph state each takes; Working is listed only while
# a lane still runs.
COLLAPSE: Final = (
    ("Done", "done", frozenset({"done"})),
    ("Failed", "failed", frozenset({"failed"})),
    ("Stopped", "interrupted", STOPPED_STATES),
    ("Working", "running", WORKING_STATES),
)


def appended(reply: str, post: str) -> str:
    """The message a reply becomes with the post after it; the reply's own text, trailing space included, unchanged."""
    return f"{reply}\n\n{post}"


def message_cap(platform: str) -> tuple[int, str]:
    """(cap, unit) for one platform's single message, Matrix's lowered by its environment setting as Hermes does."""
    cap, unit = MESSAGE_CAPS[platform]
    if platform == "matrix":
        try:
            cap = max(500, min(int(os.environ.get(MATRIX_CAP_ENV, "") or cap), cap))
        except ValueError:
            pass
    return cap, unit


def measured(text: str, unit: str) -> int:
    if unit == "utf16":
        return len(text.encode("utf-16-le")) // 2
    if unit == "utf8":
        return len(text.encode("utf-8"))
    return len(text)


def ends_in_open_fence(reply: str) -> bool:
    """Whether the reply leaves a code fence open, so a block after it would close the reply's own block instead."""
    return sum(1 for line in reply.splitlines() if line.lstrip().startswith(("```", "~~~"))) % 2 == 1


# ── what a post says ─────────────────────────────────────────────────────


def lane_key(name: str, node_id: str) -> str:
    """A lane's key in the ledger's closed list.

    A native child's node id names its subagent, which no other child shares,
    so it keeps its key when a team record later renames the graph. A team
    part's or fanout unit's id is unique only within its team or fanout, so the
    graph's name is part of its key.
    """
    scope = "" if node_id.startswith("s:") else name
    return hashlib.sha256(f"{scope}\0{node_id}".encode("utf-8")).hexdigest()[:16]


def graph_name(graph: Mapping[str, Any]) -> str:
    return clean_text(graph.get("name")) or "delegation"


def lanes_of(graph: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The graph's lanes (every node but the root), in drawing order."""
    raw = graph.get("nodes")
    lanes = [
        node for node in (raw if isinstance(raw, list) else [])
        if isinstance(node, Mapping) and node.get("kind") != "root" and isinstance(node.get("id"), str)
    ]
    return sorted(lanes, key=lambda node: node.get("order") if isinstance(node.get("order"), int) else 0)


def _state(node: Mapping[str, Any]) -> str:
    """The node's state, or ``unknown`` for one no collapse group names."""
    state = node.get("state")
    return state if isinstance(state, str) and any(state in states for _word, _glyph, states in COLLAPSE) else "unknown"


def _label(node: Mapping[str, Any], *, with_status: bool = False) -> str:
    """A lane's title, and, when asked, its status word where that says more than its state (``no model call``)."""
    title = fit(clean_text(node.get("title")) or "?", LABEL_CELLS)
    status = fit(clean_text(node.get("status")), STATUS_CELLS)
    return f"{title} ({status})" if with_status and status and status != _state(node) else title


def tally(lanes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The counts a count line and an end post give, and the first failed lane's title."""
    states = [_state(node) for node in lanes]
    failed = [node for node in lanes if _state(node) == "failed"]
    return {
        "total": len(lanes),
        "done": states.count("done"),
        "running": states.count("running"),
        "failed": len(failed),
        "stopped": sum(1 for state in states if state in STOPPED_STATES),
        "working": sum(1 for state in states if state in WORKING_STATES),
        "first_failed": _label(failed[0]) if failed else "",
        "first_failure": _label(failed[0], with_status=True) if failed else "",
    }


def counts_key(counts: Mapping[str, Any]) -> str:
    return "/".join(str(counts[key]) for key in ("done", "total", "running", "failed", "first_failed"))


def count_line(name: str, counts: Mapping[str, Any]) -> str:
    """``DAG <name> · <done>/<total> done · <running> running`` and, when any failed, how many and the first."""
    parts = [f"DAG {name}", f"{counts['done']}/{counts['total']} done", f"{counts['running']} running"]
    if counts["failed"]:
        parts.append(f"{counts['failed']} failed ({counts['first_failed']})")
    return " · ".join(parts)


def _listing(lead: str, labels: Sequence[str]) -> str:
    """``lead`` and the labels joined by `` · `` within `LINE_CELLS`; the ones that do not fit are counted (``+2``)."""
    shown = lead
    for index, label in enumerate(labels):
        piece = label if index == 0 else f" · {label}"
        left = len(labels) - index - 1
        if index and cells(shown + piece) + (cells(f" +{left}") if left else 0) > LINE_CELLS:
            return f"{shown} +{len(labels) - index}"
        shown += piece
    return shown


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _fenced(lines: Sequence[str]) -> str:
    return "\n".join([FENCE, *lines, FENCE])


def start_block(graph: Mapping[str, Any], lanes: Sequence[Mapping[str, Any]]) -> str:
    """The run's start: its size, then one line per layer listing its tasks, at most `MOMENT_LINES` lines."""
    by_id = {str(node["id"]): node for node in lanes}
    layers = [
        [by_id[node_id] for node_id in layer if node_id in by_id]
        for layer in (graph.get("layers") if isinstance(graph.get("layers"), list) else [])
        if isinstance(layer, list)
    ]
    layers = [layer for layer in layers if layer]
    placed = {str(node["id"]) for layer in layers for node in layer}
    unplaced = [node for node in lanes if str(node["id"]) not in placed]
    if unplaced:  # a graph with no layering (a recorded cycle) lists those lanes on one line
        layers.append(unplaced)
    sep = UNICODE.separator
    lines = [sep.join(["DAG", graph_name(graph), _plural(len(lanes), "task"), _plural(len(layers), "layer")])]
    for number, layer in enumerate(layers[:MOMENT_LINES], start=1):
        lines.append(_listing(f"{number}  ", [_label(node) for node in layer]))
    hidden = sum(len(layer) for layer in layers[MOMENT_LINES:])
    if hidden:
        lines.append(f"+{hidden} more")
    return _fenced(lines)


def end_block(graph: Mapping[str, Any], lanes: Sequence[Mapping[str, Any]]) -> str:
    """The run's end: each lane under Done, Failed or Stopped (Working while one runs), the first failure, totals."""
    counts = tally(lanes)
    sep = UNICODE.separator
    lines = [sep.join(["DAG", graph_name(graph), "ended" if not counts["working"] else "so far"])]
    width = max(len(word) for word, _glyph, _states in COLLAPSE)
    for word, glyph_state, states in COLLAPSE:
        members = [node for node in lanes if _state(node) in states]
        if members:
            lead = f"{UNICODE.glyphs[glyph_state]} {word.ljust(width)}  "
            lines.append(_listing(lead, [_label(node, with_status=word != "Done") for node in members]))
    if counts["first_failure"]:
        lines.append(f"first failure: {counts['first_failure']}")
    totals = [_plural(counts["total"], "task"), f"{counts['done']} done", f"{counts['failed']} failed",
              f"{counts['stopped']} stopped"]
    if counts["working"]:
        totals.append(f"{counts['working']} working")
    lines.append(sep.join(totals))
    return _fenced(lines)


# ── when a post is due ───────────────────────────────────────────────────


def messenger_snapshot(
    *, session_id: object, platform: object, model: object = "", reply: str = "", now: float | None = None
) -> str:
    """What to append to ``reply`` at this moment of the conversation's run, or "" when nothing is due or fits."""
    if not isinstance(platform, str) or platform.strip().lower() not in MESSENGER_PLATFORMS:
        return ""
    cap, unit = message_cap(platform.strip().lower())
    if os.environ.get(GRAPH_SWITCH, "").strip() == "off":
        return ""  # the same switch that turns the HUD's graphs off
    if not isinstance(session_id, str) or not session_id.strip():
        return ""
    session = session_id.strip()
    moment = time.time() if now is None else now
    try:
        omh_home = runtime_paths.plugin_home(None)
        hermes_home = runtime_paths.plugin_home(None, hermes=True)
    except (runtime_paths.RuntimeBindingError, OSError, RuntimeError):
        return ""  # a host that cannot name this profile's homes gets no post, and the reply goes out as it is
    if root_session(session, hermes_home) != session:
        return ""  # a delegated child's own turn: its parent's reply carries the run
    posted = posted_record(omh_home, session)
    run_open = posted.get("phase") == "open"
    graph = build_delegation_graph(
        omh_home, hermes_home, session, now=moment, root_model=model if isinstance(model, str) else "",
        # A run a start post opened gets its end post however long it took to settle; any other settled graph
        # stops being news after the usual linger.
        linger=None if run_open else GRAPH_LINGER_SECONDS,
    )
    if graph.get("status") != "active":
        return ""
    fingerprint = graph_fingerprint(graph)
    if fingerprint == posted.get("fingerprint"):
        return ""
    name = graph_name(graph)
    closed = list(posted.get("closed") or [])
    lanes = [node for node in lanes_of(graph) if lane_key(name, str(node["id"])) not in set(closed)]
    if not lanes:
        return ""
    counts = tally(lanes)
    if not counts["working"]:
        moment_name, block = "end", end_block(graph, lanes)
    elif not run_open:
        moment_name, block = "start", start_block(graph, lanes)
    elif counts_key(counts) != posted.get("counts"):
        moment_name, block = "count", ""
    else:
        return ""
    if ends_in_open_fence(reply):
        note_skipped(omh_home, session, fingerprint, SKIPPED_OPEN_FENCE, now=moment)
        return ""
    line = count_line(name, counts)
    if block and measured(appended(reply, block), unit) <= cap * CAP_SHARE:
        post = block
    elif measured(appended(reply, line), unit) <= cap * CAP_SHARE:
        post = line
    else:
        note_skipped(omh_home, session, fingerprint, SKIPPED_CAP, now=moment)
        return ""
    if moment_name == "end":
        closed += [lane_key(name, str(node["id"])) for node in lanes]
    if not note_posted(
        omh_home, session, fingerprint, moment_name=moment_name, phase="closed" if moment_name == "end" else "open",
        counts=counts_key(counts), closed=closed, now=moment,
    ):
        return ""
    return post
