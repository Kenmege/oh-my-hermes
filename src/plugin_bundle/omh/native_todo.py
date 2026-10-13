"""Hermes' native `todo_list`, projected into this session's OMH plan todo.

One checklist. Hermes ships `todo_list` as a core tool: its own description
asks for it on any task of three or more steps, Hermes re-injects it after
context compression, and the TUI renders it. `omh_todo` asked for the same
plan a second time, and on the owner's machine 8 of 17 sessions that used any
todo tool called both -- the same plan declared in both tools, in paraphrased
items rather than identical lists, and two lists on screen for one plan. So
the native list is the step tracker wherever the
presence rule (`native_tools`) says it is there, and `omh_todo` keeps what it
alone records: `plan_stage` gating, the completion dossier
(checkpoint/record/recall), an item's `blocked_reason` and the plan's
`deferred_reason`.

What keeps it ONE plan is that there is still one store. `post_tool_call`
hands every successful `todo_list` write to ``project_native_todo``, which
writes the full list Hermes returned into the session's own record --
``$OMH_HOME/runtime/todos/<session key>.json``, the file `omh_todo` writes --
with ``source: todo_list``. So the HUD, the TUI widget, the Desktop pane, the
plan-stage gate, the stall finding and the continuation drive all read one
record through `read_omh_todo` and need not know which tool wrote it. A
deferred `todo_list` changes nothing here: Hermes peels the tool_search
bridge before plugin hooks run, so the call arrives under its own name. An
absent one changes nothing either: no write arrives, and `omh_todo` is the
only writer, exactly as before.

A native status is a model's declaration, the same as an `omh_todo` state,
and it gains nothing by crossing over. ``completed`` becomes ``done`` through
`bind_declared_items`, the binding `omh_todo action=set` uses, so the stop
criterion judges it against the session's recorded commands exactly as it
judges an `omh_todo` done mark, and the record keeps the same claim boundary.
``cancelled`` becomes ``done`` carrying a ``blocked_reason``: the record's
existing spelling of a step closed without being worked, which the counts
report as skipped and the evidence check passes over.

What a native write may not do is take over a plan only `omh_todo` can
express. An open plan carrying any ``plan_stage`` is one: while it awaits
acceptance it is the plan-stage gate's own record, and once accepted it is
the plan `omh_team` binds its lanes to (``plan_stage=accepted`` and its
``check:`` items, `src/workflows/team.py`). An open code-story template is
another: a phase shape a native list cannot hold. While any of them stands,
the native write is left unprojected and the plan keeps its record. Every
other plan is replaced, as an `omh_todo action=set` replaces it: the native
write is a new declaration, so it carries no template and no
``deferred_reason`` (which lapses on any item change by design).

An item's ``blocked_reason`` is the one field carried, matched by item text
as `omh_todo` binds by text, because `todo_list` has no way to say it and the
next native write would otherwise drop it. It lasts while the item's status
stays where it was when the reason was recorded; a native status change --
resumed, completed, reopened -- ends it, so a resumed item is driven again
rather than silenced until it completes. The reason OMH writes for a
cancelled item is never carried: the native status decides it each write.

Writes arrive in order, so the projection keeps no ordering of its own.
Hermes never runs two `todo_list` calls at once: it is not in
``_PARALLEL_SAFE_TOOLS`` (``agent/tool_dispatch_helpers.py``, v2026.9.7 and
origin/main), so ``_batch_admission`` makes every call a sequential barrier,
and the sequential executor finishes one call -- its ``post_tool_call``
included, which the dispatcher waits on (``plugins_dispatch.py``
``_run_hook_callback_bounded``) -- before it starts the next. The one gap is
a hook outliving ``plugins.hook_callback_timeout`` (30 s by default) and
being abandoned; this projection's own lock wait is two seconds.

Where the projected items are drawn is each surface's call, read off
``source``: the modern TUI dock and the Desktop pane leave a todo_list-held
plan's items to Hermes' own todo panel and show only what it cannot
(`omh-status.mjs`, `desktop/plugin.js`).

This module imports nothing from ``omh`` (`tests/test_plugin_bundle_standalone.py`).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .native_tools import NATIVE_TODO_TOOL
from .todo_store import (
    MAX_TODO_DEPTH,
    MAX_TODO_ITEMS,
    MAX_TODO_TEXT_CHARS,
    TODO_STALE_SECONDS,
    TODO_PLAN_STAGES,
    _paired_with_prior,
    _remove_todo_file,
    build_todo_record,
    read_todo_record,
    strip_control_characters,
    todo_path,
    write_todo,
)
from .tools.todo_tool import bind_declared_items

# The record's `source` for a projected list. The tool's own name, so a reader
# of the file can tell which tool holds the steps without a second vocabulary.
NATIVE_TODO_SOURCE = NATIVE_TODO_TOOL
# The `blocked_reason` a cancelled native item carries. Written by OMH, not the
# model, and worded as what the record shows rather than why it happened.
NATIVE_CANCELLED_REASON = "cancelled in todo_list"
# Hermes' four statuses (`tools/todo_tool.py`, `VALID_STATUSES`) onto the
# record's three states. An unknown status reads as pending, which is what
# Hermes itself stores for one.
_NATIVE_STATES = {
    "pending": "pending",
    "in_progress": "active",
    "completed": "done",
    "cancelled": "done",
}
# Everything a projection can raise on the way to the record: the store's own
# refusals (`TodoValidationError` is a `ValueError`, `TodoStoreError` a
# `RuntimeError`), a home that cannot be written, and malformed host data.
_PROJECTION_FAILURES = (OSError, RuntimeError, ValueError, TypeError)
_TRUNCATION_MARK = "…"


def project_native_todo(
    *,
    tool_name: object,
    args: object,
    result: object,
    status: object,
    session_ref: str,
    omh_home: str,
    hermes_home: str,
) -> str:
    """Write a successful `todo_list` write into the session's plan record; returns what happened.

    One label per outcome, so a caller or a test can tell a projection from
    each way of declining one. Never raises: it runs inside ``post_tool_call``,
    which Hermes wraps in its own ``except Exception``, and a raise there
    would only take the other observers with it.

    Only a WRITE projects -- a call that sent ``todos`` and got the list back.
    ``todos`` may arrive as a JSON string, which Hermes parses and writes, so
    any sent value counts and the list is taken from the result. A read
    changes nothing, and re-stamping the record on one would read as progress
    to the stall finding. No session, no projection: a native list always
    belongs to one, and writing it to the home-wide file would show it to
    sessions that never declared it. An emptied native list removes the
    session's own record and nothing else -- never the home-wide one an
    operator wrote.
    """
    if str(tool_name or "") != NATIVE_TODO_TOOL:
        return "not_native_todo"
    if not session_ref:
        return "no_session"
    if str(status or "ok") != "ok":
        return "not_ok"
    if not isinstance(args, dict) or args.get("todos") is None:
        return "read_only"
    todos = _result_todos(result)
    if todos is None:
        return "no_list"
    home = Path(omh_home)
    try:
        prior = read_todo_record(home, session_ref) or {}
        if _held_by_omh_todo(prior):
            return "held_by_omh_todo"
        items = native_todo_items(todos, prior.get("items"))
        if not items:
            # The session's own file and nothing else: `clear_todo` would also
            # take the operator's unstamped home-wide record with it.
            return "cleared" if _remove_todo_file(home, todo_path(home, session_ref)) else "already_absent"
        bound, dropped = bind_declared_items(
            items, prior, session_ref, hermes_home=Path(hermes_home)
        )
        # A native write that leaves every item as stored is the list standing
        # still, whoever last wrote the record: nothing is restamped, and the
        # fields only `omh_todo` sets -- a live deferral among them -- stay.
        if bound == prior.get("items"):
            return "unchanged"
        record = build_todo_record(
            "", bound, source=NATIVE_TODO_SOURCE, session_ref=session_ref, dropped=dropped
        )
        _ = write_todo(home, record)
    except _PROJECTION_FAILURES:
        return "refused"
    return "projected"


def native_todo_items(todos: object, prior_items: object = ()) -> list[dict[str, Any]]:
    """The record items a native list projects to, in the order Hermes renders it.

    Each root in list order, each followed by its subtasks, the walk Hermes'
    own post-compression render takes; nesting becomes ``depth``, capped at the
    record's three levels. Text is bounded to the record's cap with a visible
    mark rather than refused, since the host already accepted it.

    Over the record's item cap, finished items leave first, oldest first, so
    every open item the cap can hold stays. The stop criterion reads open
    items, and a cut that dropped one would let a list with work left read as
    finished. More open items than the cap is a list the record cannot hold
    whole; it keeps the first, and the next native write brings the rest in as
    those close.
    """
    entries = [todo for todo in todos if isinstance(todo, dict)] if isinstance(todos, list) else []
    ids = {str(todo.get("id", "")) for todo in entries}
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for todo in entries:
        parent = str(todo.get("parent") or "")
        if parent and parent in ids and parent != str(todo.get("id", "")):
            children.setdefault(parent, []).append(todo)
        else:
            roots.append(todo)
    ordered: list[tuple[dict[str, Any], int]] = []
    seen: set[int] = set()
    stack = [(todo, 0) for todo in reversed(roots)]
    while stack:
        todo, depth = stack.pop()
        if id(todo) in seen:
            continue
        seen.add(id(todo))
        ordered.append((todo, depth))
        stack.extend((kid, depth + 1) for kid in reversed(children.get(str(todo.get("id", "")), [])))
    # Hermes breaks parent cycles before it returns a list; anything a walk
    # from the roots never reached is still an item, so it is kept at the top.
    ordered.extend((todo, 0) for todo in entries if id(todo) not in seen)
    items: list[dict[str, Any]] = []
    for todo, depth in ordered:
        text = strip_control_characters(todo.get("content", ""))
        if not text:
            continue
        if len(text) > MAX_TODO_TEXT_CHARS:
            text = text[: MAX_TODO_TEXT_CHARS - len(_TRUNCATION_MARK)] + _TRUNCATION_MARK
        status = str(todo.get("status", "")).strip().lower()
        item: dict[str, Any] = {"text": text, "state": _NATIVE_STATES.get(status, "pending")}
        if depth:
            item["depth"] = min(depth, MAX_TODO_DEPTH)
        if status == "cancelled":
            item["blocked_reason"] = NATIVE_CANCELLED_REASON
        items.append(item)
    for item, prior in _paired_with_prior(items, prior_items)[0]:
        reason = prior.get("blocked_reason") if prior else None
        if item.get("blocked_reason") or not isinstance(reason, str) or not reason.strip():
            continue
        if reason == NATIVE_CANCELLED_REASON or prior.get("state") != item["state"]:
            continue
        item["blocked_reason"] = reason
    while len(items) > MAX_TODO_ITEMS:
        finished = next((item for item in items if item["state"] == "done"), None)
        if finished is None:
            break
        items.remove(finished)
    return items[:MAX_TODO_ITEMS]


def _result_todos(result: object) -> list[Any] | None:
    """The full list a `todo_list` result carries, or None when it carries none."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    todos = result.get("todos") if isinstance(result, dict) else None
    return todos if isinstance(todos, list) else None


def _held_by_omh_todo(prior: dict[str, Any]) -> bool:
    """Whether the stored plan is one only `omh_todo` can express, and still open.

    A plan stage of either kind, or a template. Open and fresh is the test the
    plan-stage gate itself applies (`established`): a finished or stale plan
    holds nothing, so a native list replaces it like any other.
    """
    if prior.get("plan_stage") not in TODO_PLAN_STAGES and not prior.get("template"):
        return False
    items = prior.get("items")
    if not isinstance(items, list) or not any(
        isinstance(item, dict) and item.get("state") != "done" for item in items
    ):
        return False
    try:
        written = datetime.fromisoformat(str(prior.get("updated_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if written.tzinfo is None:
        written = written.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - written).total_seconds() <= TODO_STALE_SECONDS
