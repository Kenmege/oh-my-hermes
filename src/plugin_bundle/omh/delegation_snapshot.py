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
* **end**, once: a short block that puts each lane under Done, Failed or
  Stopped (and Working, only while one is in flight), names the lane that
  failed first, and gives the totals. It goes on the first reply after
  every lane has settled, or, for a run a start opened, once nothing has
  been in flight and nothing has changed for `STALL_SECONDS` (a team part
  waiting on work that will not come). Such a ``stalled`` end lists the
  parts not moving under Stopped with their status, and leaves them out of
  the closed lanes, so a part that moves again opens a run of its own.

A run whose first reply finds it already settled (a synchronous call) gets the
end post alone. A chat reply never carries the box drawing: the OMH TUI and
`omh hud --graph` draw that.

Nothing is appended:

* with ``OMH_SUBAGENT_GRAPH=off`` in Hermes' environment, the switch that
  already turns the HUD's graphs off;
* on a delegated child's own turn, with no active graph, or for a run no
  start opened that is not moving;
* when the graph's fingerprint (structure, states and labels, never elapsed
  time) equals the one the last post recorded, unless time alone has
  brought a stalled end, or a running run's counts did not change;
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
from .delegation_graph import GRAPH_LINGER_SECONDS, SETTLED_STATES, build_delegation_graph, graph_fingerprint
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
KNOWN_STATES: Final = frozenset({"done", "failed", "interrupted", "unknown", "running", "stuck", "dispatched", "waiting"})
# A run with nothing in flight that has not changed for this long is stalled: its end post goes out with what settled.
STALL_SECONDS: Final = GRAPH_LINGER_SECONDS
# The end post's groups, in the order it lists them, with the glyph each takes (`collapse` puts a lane in one).
COLLAPSE: Final = (("Done", "done"), ("Failed", "failed"), ("Stopped", "interrupted"), ("Working", "running"))


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
    """The node's state, or ``unknown`` for one the graph does not define."""
    state = node.get("state")
    return state if isinstance(state, str) and state in KNOWN_STATES else "unknown"


def collapse(node: Mapping[str, Any]) -> str:
    """The lane's group in an end post.

    Working is a lane in flight: running, stuck, or dispatched to a child
    Hermes recorded (`session_tail`), which settles on its own. Stopped is one
    that is not moving: interrupted, `unknown`, or a team part waiting or sent
    with no child the ledger holds.
    """
    state = _state(node)
    if state in ("done", "failed"):
        return state.capitalize()
    if state in ("running", "stuck") or (state == "dispatched" and node.get("session_tail")):
        return "Working"
    return "Stopped"


def _label(node: Mapping[str, Any], *, with_status: bool = False) -> str:
    """A lane's title, and, when asked, its status word: always for a Stopped lane, which says why it is not moving,
    and otherwise where the word says more than the state (``no model call``)."""
    title = fit(clean_text(node.get("title")) or "?", LABEL_CELLS)
    status = fit(clean_text(node.get("status")), STATUS_CELLS)
    shown = with_status and status and (status != _state(node) or collapse(node) == "Stopped")
    return f"{title} ({status})" if shown else title


def _ended_at(node: Mapping[str, Any]) -> float:
    value = node.get("ended_at")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float("inf")


def tally(lanes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The counts a count line and an end post give, and the title of the lane that failed first (by its stop time,
    then by drawing order)."""
    groups = [collapse(node) for node in lanes]
    failed = sorted((node for node in lanes if _state(node) == "failed"), key=_ended_at)
    return {
        "total": len(lanes),
        "done": groups.count("Done"),
        "running": sum(1 for node in lanes if _state(node) == "running"),
        "failed": len(failed),
        "stopped": groups.count("Stopped"),
        "working": groups.count("Working"),
        "settled": all(_state(node) in SETTLED_STATES for node in lanes),
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
    """The run's end: each lane under Done, Failed, Stopped or Working (`collapse`), the first failure, the totals.

    The header says ``ended`` when every lane settled, ``stalled`` when some
    are not moving and none is in flight, and ``so far`` while one is.
    """
    counts = tally(lanes)
    sep = UNICODE.separator
    verdict = "ended" if counts["settled"] else ("so far" if counts["working"] else "stalled")
    lines = [sep.join(["DAG", graph_name(graph), verdict])]
    width = max(len(word) for word, _glyph in COLLAPSE)
    for word, glyph_state in COLLAPSE:
        members = [node for node in lanes if collapse(node) == word]
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
    name = graph_name(graph)
    closed = list(posted.get("closed") or [])
    lanes = [node for node in lanes_of(graph) if lane_key(name, str(node["id"])) not in set(closed)]
    if not lanes:
        return ""
    counts = tally(lanes)
    changed_at = graph.get("changed_at")
    # Nothing in flight and no start, stop or team write for `STALL_SECONDS`: a team part that waits on a part that
    # will not come (or was sent with no child the ledger holds) would otherwise keep its run from ever ending.
    stalled = (not counts["working"] and isinstance(changed_at, (int, float))
               and moment - float(changed_at) > STALL_SECONDS)
    fingerprint = graph_fingerprint(graph)
    if fingerprint == posted.get("fingerprint") and not (run_open and stalled):
        return ""  # the graph the last post showed; only time can still bring a stalled end
    if counts["settled"] or (run_open and stalled):
        moment_name, block = "end", end_block(graph, lanes)
    elif stalled:
        return ""  # a run no start opened that is not moving is not news
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
        # A lane that settled is closed; one left not moving at a stalled end stays, so it opens a run if it moves.
        closed += [lane_key(name, str(node["id"])) for node in lanes if _state(node) in SETTLED_STATES]
    if not note_posted(
        omh_home, session, fingerprint, moment_name=moment_name, phase="closed" if moment_name == "end" else "open",
        counts=counts_key(counts), closed=closed, now=moment,
    ):
        return ""
    return post
