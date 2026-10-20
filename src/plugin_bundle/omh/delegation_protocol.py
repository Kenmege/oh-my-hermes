"""The delegation protocol a delegated agent follows, owned once by the bundle.

Three rules: echo the goal back before using tools, run one bounded
verification pass, and cap re-review at two rounds. The control plane
(`omh.coding.unit_prompt_protocol`) re-exports them from here, so fanout unit
prompts and `omh coding composition-guide` read these same objects. The plugin
reads them directly, to put them in the system prompt of every Hermes
`delegate_task` child (#2174). One definition, no copy. Stdlib only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

GOAL_ECHO_PROTOCOL: Final[str] = (
    "Before using tools, restate the overall goal, your deliverable, and the numbered criteria. "
    "If they conflict with your brief, stop and report the conflict instead of guessing."
)

VERIFICATION_STOP_PROTOCOL: Final[str] = (
    "After finishing work, run one full verification pass; verification is never skipped. "
    "A failed check blocks only a stated criterion; report other observations. Once criteria pass, "
    "STOP verification, including after unrelated edits. After two failed fix-and-verify cycles, "
    "commit what passes and report the failed criterion and output."
)

REVIEW_ROLE_PROTOCOL: Final[str] = (
    "Review discipline: a finding blocks only when it violates a stated success criterion of the reviewed "
    "work; list every other finding as non-blocking. Cap re-review at two rounds — after that, report the "
    "remaining criterion-cited blockers rather than starting another round."
)

# A system prompt section, not a `pre_tool_call` `modify` of `goal` and
# `tasks[].goal`. Hermes renders every plugin section into each agent's
# system prompt, a `delegate_task` child's included
# (`agent/system_prompt.py`, `build_system_prompt_parts`), so the section
# reaches every child of a batch, a nested child, and the reviewer that
# `agent/review_engine.py` spawns by calling `delegate_task` itself, with no
# tool call for `pre_tool_call` to see. A `modify` would also rewrite the
# goal that the parent's display, the `subagent_start` hook and the child's
# first user turn all show.
DELEGATION_PROTOCOL_SECTION_ID: Final[str] = "omh.delegation-protocol"

DELEGATION_PROTOCOL_SECTION: Final[str] = "\n".join(
    (
        "OMH delegation protocol for this delegated task:",
        f"- {GOAL_ECHO_PROTOCOL}",
        f"- {VERIFICATION_STOP_PROTOCOL}",
        f"- {REVIEW_ROLE_PROTOCOL}",
    )
)

# Hermes builds a `delegate_task` child with this platform
# (`tools/delegate_tool.py`, `_build_child_agent`). Nothing else in Hermes
# sets it at d923a5eb48 (v0.22.2) or 8b9ac5773e.
_CHILD_PLATFORM: Final[str] = "subagent"


def delegation_protocol_section(session_info: object) -> str:
    """The protocol for a `delegate_task` child's system prompt, and "" for any other session.

    Hermes hands each section the agent's own platform in `session_info`
    (`_plugin_session_info`). It skips an empty section without charging the
    budget that all plugins' sections share, so the parent's prompt does not
    change.
    """
    platform = session_info.get("platform") if isinstance(session_info, Mapping) else None
    return DELEGATION_PROTOCOL_SECTION if platform == _CHILD_PLATFORM else ""
