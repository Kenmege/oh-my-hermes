"""Whether a native Hermes tool is present for a session: one rule, one wording.

A native tool is PRESENT when its name is in the session's tool list OR in
the tool_search catalog. Hermes defers a curated set of core tools behind the
tool_search bridge by default (`todo_list`, `cronjob_manage` and
`image_generate` among them, since the release tagged ``v2026.9.21``), and a
deferred tool is reachable through ``tool_call``: it is present, not
missing. Hermes also peels the bridge before plugin hooks run, so a deferred
tool's call reaches ``pre_tool_call`` / ``post_tool_call`` under its own
name, never as ``tool_call``.

Presence is never read off a Hermes version number. A version says what a
release shipped; it does not say what this session was given -- toolset
scope, the platform, a tool the person disabled, or a deferral list they
edited all change the answer on the same build.

The rule has two faces, and every OMH surface uses one of them rather than
restating it:

* ``native_tool_present`` -- for code that holds the two name sets.
* ``native_tool_presence_clause`` -- for text the MODEL applies: awareness
  lines, tool descriptions, skill bodies. Hermes hands a plugin hook neither
  set (``pre_llm_call`` carries no tool list), while the model sees its own
  tool list and can ask tool_search, so text states the rule in these words
  and the model evaluates it for the session it is in.

A surface that needs a native tool states what it does when the clause is
false, in the same sentence. The first user is the plan checklist: native
`todo_list` tracks steps when present, and `omh_todo` when it is not
(`native_todo`).

Stdlib only. This ships in the plugin bundle, which may not import ``omh``,
and the skill renderer imports it from here, so the wording is one string in
both places.
"""
from __future__ import annotations

from collections.abc import Iterable

NATIVE_TODO_TOOL = "todo_list"
TOOL_SEARCH_TOOL = "tool_search"


def native_tool_present(
    name: str, *, tool_list: Iterable[str] = (), tool_search_catalog: Iterable[str] = ()
) -> bool:
    """Whether ``name`` is in the session's tool list or its tool_search catalog."""
    return bool(name) and (name in set(tool_list) or name in set(tool_search_catalog))


def native_tool_presence_clause(name: str, *, code: bool = False) -> str:
    """The presence rule as a clause the model evaluates: ``<name> is in your tool list or tool_search finds it``.

    ``code`` wraps the named tool in backticks for markdown surfaces (skill
    bodies), which is the one shape every surface states the rule in:
    ``f"when {native_tool_presence_clause('clarify', code=True)}"`` reads
    "when `clarify` is in your tool list or tool_search finds it". Plain text
    surfaces (awareness lines, tool descriptions) leave the name bare, as the
    rest of their wording does.
    """
    tick = "`" if code else ""
    return f"{tick}{name}{tick} is in your tool list or {TOOL_SEARCH_TOOL} finds it"
