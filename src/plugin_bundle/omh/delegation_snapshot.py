"""The delegation view as a code block at the end of a chat reply, on a turn where the graph changed.

OMH sends no message. Hermes calls `transform_llm_output` once per turn with
the reply it is about to deliver (`agent/turn_finalizer.py`), and a messaging
gateway delivers a transformed reply on purpose, editing the streamed message
to include what a plugin appended (`gateway/run_turn.py`). This appends a
snapshot of the conversation's delegation graph there, and nothing else:

* only on a chat platform that renders a fenced block (`MESSENGER_PLATFORMS`);
  the OMH TUI draws the same graph live in its own panel, and the CLI and the
  desktop app get nothing;
* only when the graph differs from the last snapshot a reply carried, by a
  fingerprint of its structure, states and labels (never elapsed time), kept
  in the delegation ledger so a restart does not post it again;
* only when that fingerprint can be recorded, so a ledger that cannot be
  written never turns into a block on every reply;
* never with ``OMH_SUBAGENT_GRAPH=off`` in Hermes' environment, the switch that
  already turns the HUD's graph off.

The reply's own text is never changed: the block follows it. Width and size
are chosen for a phone: `MESSENGER_WIDTH` cells per line, so a box border
never wraps in a ~42-column code block, and at most `MESSENGER_MAX_CHARS`
characters with the fences, under Discord's 2,000.
"""

from __future__ import annotations

import os
import time
from typing import Final

from . import runtime_paths
from .delegation_graph import build_delegation_graph, graph_fingerprint
from .delegation_ledger import note_posted, posted_fingerprint, root_session
from .delegation_view import render_delegation_view

MESSENGER_PLATFORMS: Final = frozenset({"discord", "slack", "telegram", "mattermost", "matrix"})
MESSENGER_WIDTH: Final = 40
MESSENGER_MAX_CHARS: Final = 1_800
MESSENGER_MAX_LINES: Final = 42
FENCE: Final = "```"
GRAPH_SWITCH: Final = "OMH_SUBAGENT_GRAPH"


def messenger_snapshot(
    *, session_id: object, platform: object, model: object = "", now: float | None = None
) -> str:
    """The fenced block to append to this turn's reply, or "" when there is nothing new to show here."""
    if not isinstance(platform, str) or platform.strip().lower() not in MESSENGER_PLATFORMS:
        return ""
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
        return ""  # a host that cannot name this profile's homes gets no block, and the reply goes out as it is
    if root_session(session, hermes_home) != session:
        return ""  # a delegated child's own turn: its parent's reply carries the graph
    graph = build_delegation_graph(
        omh_home, hermes_home, session, now=moment, root_model=model if isinstance(model, str) else ""
    )
    if graph.get("status") != "active":
        return ""
    fingerprint = graph_fingerprint(graph)
    if fingerprint == posted_fingerprint(omh_home, session):
        return ""
    budget = MESSENGER_MAX_CHARS - 2 * (len(FENCE) + 1)
    view = render_delegation_view(
        graph, width=MESSENGER_WIDTH, max_chars=budget, max_lines=MESSENGER_MAX_LINES, charset="unicode"
    )
    if not view.lines or not note_posted(omh_home, session, fingerprint, now=moment):
        return ""
    return f"{FENCE}\n{view.text()}\n{FENCE}"
