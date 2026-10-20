"""One conversation's delegated work as a graph: `delegation_graph/v1`, nodes, edges and layers.

The structure comes from records, never from timing guesses:

* the delegation ledger (`delegation_ledger.py`): which child each
  `delegate_task` call started, under which parent and in which turn;
* an `omh_team/v1` record for the conversation: parts, their titles and
  their recorded `depends_on`;
* for the opt-in fanout lane, the frozen `fanout_contract/v2` and its roster
  (passed in, already verified, by the HUD reader).

Facts about a child are read from Hermes' own `state.db`, read-only: the model
its session row recorded (or the one model its usage rows observed), its
reasoning effort, whether it has made a model call, and the goal Hermes stored
for its task. Nothing reads current config.

Every field without evidence is ``?``:

* **category** is the route OMH recorded for the parent session when the child
  started, and only while the child's observed model matches that route.
  Otherwise ``inherit`` when the child's observed model equals its parent's
  recorded model, otherwise ``?``. The mixture-chain projection the HUD rows
  use is not consulted.
* **model** is what Hermes recorded for the child, never the configured route.
* **state** comes from `subagent_stop`, a recorded model call, or the team's
  own lifecycle. A child whose session closed for compaction without a stop
  this ledger could join is ``unknown``, never done and never running.

An edge is either a dispatch (a parent started this child) or a recorded
`depends_on`. Dispatch waves of one parent (the children one turn started)
are grouped as ``wave N · HH:MM``; that is order, not dependency, and no edge
is drawn for it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Final

from .delegation_ledger import child_title, ledger_key, read_ledger, title_from_goal
from .subagent_graph import _roster_progress_fields as roster_progress_fields

GRAPH_SCHEMA_VERSION: Final = "delegation_graph/v1"
GRAPH_NODE_LIMIT: Final = 48
GRAPH_BYTE_LIMIT: Final = 24_576
GRAPH_LINGER_SECONDS: Final = 15 * 60
CHILD_STALE_SECONDS: Final = 30 * 60
TEAM_FILE_LIMIT: Final = 16
TEAM_RECORD_BYTES: Final = 262_144
TITLE_LIMIT: Final = 120
OPENER_CHARS: Final = 400
COMPACTION_END_REASON: Final = "compression"
GRAPH_CLAIM_BOUNDARY: Final = (
    "Delegation structure and states as Hermes' hooks, state.db and OMH's own records reported them. A category is "
    "OMH's recorded route, shown only where the child's observed model matches it. Not execution, verification, "
    "review, CI or merge evidence."
)
_FAILED_STOPS: Final = frozenset({"failed", "error", "timeout"})
_FANOUT_SUCCESS: Final = frozenset({"completed", "already_completed", "dry_run_planned"})


def inactive_graph(reason: str, *, scope: str = "session") -> dict[str, Any]:
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "status": "inactive",
        "reason": reason,
        "scope": scope,
        "source": "",
        "name": "",
        "nodes": [],
        "edges": [],
        "layers": [],
        "hidden_nodes": 0,
        "claim_boundary": GRAPH_CLAIM_BOUNDARY,
    }


def graph_fingerprint(graph: Mapping[str, Any]) -> str:
    """What a reader would see, minus the clock: structure, states and labels, never elapsed time."""
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    keys = ("id", "kind", "title", "category", "model", "effort", "state", "status", "waits_on", "group")
    shape = {
        "nodes": [{key: node.get(key) for key in keys} for node in nodes if isinstance(node, Mapping)],
        "edges": [[edge.get("from"), edge.get("to"), edge.get("kind")] for edge in edges if isinstance(edge, Mapping)],
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _text(value: object, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.strip() if ch.isprintable())[:limit]


def same_model(observed: str, recorded: str) -> bool:
    """One model named twice: equal ignoring case, or equal once a provider path is dropped."""
    left, right = observed.strip().casefold(), recorded.strip().casefold()
    if not left or not right:
        return False
    return left == right or left.rsplit("/", 1)[-1] == right.rsplit("/", 1)[-1]


# ── state.db, read-only ──────────────────────────────────────────────────


def _connect(hermes_home: Path) -> sqlite3.Connection | None:
    state_db = Path(hermes_home) / "state.db"
    if not state_db.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=0.25)
    except sqlite3.Error:
        return None


def _effort(config: Mapping[str, Any]) -> str:
    reasoning = config.get("reasoning_config")
    if isinstance(reasoning, Mapping) and reasoning.get("enabled"):
        return _text(reasoning.get("effort"), 40)
    return ""


def state_facts(
    hermes_home: Path, session_ids: Sequence[str], delegation_ids: Sequence[str]
) -> dict[str, Any]:
    """What Hermes recorded about these sessions and delegations. Each read is on its own: one that fails is absent."""
    facts: dict[str, Any] = {"read": False, "sessions": {}, "usage": {}, "openers": {}, "delegations": {}}
    ids = [item for item in dict.fromkeys(session_ids) if item][:160]
    connection = _connect(hermes_home)
    if connection is None:
        return facts
    try:
        marks = ",".join("?" for _ in ids)
        if ids:
            try:
                columns = {str(row[1]) for row in connection.execute('PRAGMA table_info("sessions")')}
                ended = "ended_at" if "ended_at" in columns else "NULL"
                reason = "end_reason" if "end_reason" in columns else "NULL"
                for row in connection.execute(
                    f"SELECT id, model, model_config, {ended}, {reason} FROM sessions WHERE id IN ({marks})", ids
                ):
                    try:
                        config = json.loads(row[2]) if isinstance(row[2], str) else {}
                    except ValueError:
                        config = {}
                    config = config if isinstance(config, dict) else {}
                    facts["sessions"][str(row[0])] = {
                        "model": _text(row[1]),
                        "effort": _effort(config),
                        "ended_at": row[3] if isinstance(row[3], (int, float)) else None,
                        "end_reason": _text(row[4], 40),
                    }
                facts["read"] = True
            except sqlite3.Error:
                pass
            try:
                for row in connection.execute(
                    "SELECT session_id, COUNT(*), GROUP_CONCAT(DISTINCT NULLIF(model, '')), MAX(last_seen) "
                    f"FROM session_model_usage WHERE session_id IN ({marks}) GROUP BY session_id",
                    ids,
                ):
                    models = [part for part in str(row[2] or "").split(",") if part]
                    facts["usage"][str(row[0])] = {
                        "rows": int(row[1] or 0),
                        "model": _text(models[0]) if len(models) == 1 else "",
                        "last_seen": row[3] if isinstance(row[3], (int, float)) else None,
                    }
            except sqlite3.Error:
                pass
            try:
                for row in connection.execute(
                    f"SELECT session_id, substr(content, 1, {OPENER_CHARS}) FROM messages WHERE rowid IN ("
                    f"SELECT MIN(rowid) FROM messages WHERE session_id IN ({marks}) AND role = 'user' "
                    "GROUP BY session_id)",
                    ids,
                ):
                    facts["openers"][str(row[0])] = title_from_goal(row[1])
            except sqlite3.Error:
                pass
        delegations = [item for item in dict.fromkeys(delegation_ids) if item][:32]
        if delegations:
            try:
                for row in connection.execute(
                    "SELECT delegation_id, task_json FROM async_delegations WHERE delegation_id IN ("
                    + ",".join("?" for _ in delegations) + ")",
                    delegations,
                ):
                    try:
                        task = json.loads(row[1]) if isinstance(row[1], str) else {}
                    except ValueError:
                        task = {}
                    if not isinstance(task, dict):
                        continue
                    goals = task.get("goals") if isinstance(task.get("goals"), list) else []
                    facts["delegations"][str(row[0])] = {
                        "goals": [title_from_goal(goal) for goal in goals[:64]],
                        "model": _text(task.get("model")),
                    }
            except sqlite3.Error:
                pass
    finally:
        connection.close()
    return facts


# ── omh_team, read-only ──────────────────────────────────────────────────


def team_unit_state(unit: Mapping[str, Any]) -> str:
    """Hand-mirror of `omh.workflows.team.unit_state` (the bundle cannot import it); a parity test holds them."""
    if unit.get("blocked"):
        return "blocked"
    attempts = unit.get("attempts") or []
    attempt = attempts[-1] if attempts else None
    if not isinstance(attempt, Mapping):
        return "waiting"
    check = attempt.get("check")
    if isinstance(check, Mapping) and check.get("outcome") == "passed":
        return "accepted"
    if attempt.get("returned_at"):
        return "awaiting_check"
    if attempt.get("n", 1) > 1:
        return "repairing"
    if attempt.get("dispatch"):
        return "dispatched"
    return "prepared"


def _team_record(omh_home: Path, session_id: str) -> tuple[dict[str, Any], float] | None:
    """The conversation's most recently written `omh_team/v1` record, or None."""
    directory = Path(omh_home) / "runtime" / "teams" / ledger_key(session_id)
    try:
        paths = sorted(
            (path for path in directory.iterdir() if path.suffix == ".json" and not path.name.startswith(".")
             and not path.is_symlink()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:TEAM_FILE_LIMIT]
    except OSError:
        return None
    for path in paths:
        try:
            if path.stat().st_size > TEAM_RECORD_BYTES:
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            mtime = path.stat().st_mtime
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            continue
        if not isinstance(raw, dict) or raw.get("schema_version") != "omh_team/v1":
            continue
        units = raw.get("units")
        if not isinstance(units, list) or not units:
            continue
        ids = [unit.get("unit_id") for unit in units if isinstance(unit, dict)]
        if len(ids) != len(units) or not all(isinstance(item, str) and item for item in ids):
            continue
        if not all(
            isinstance(unit.get("depends_on"), list) and all(dep in ids for dep in unit["depends_on"])
            and isinstance(unit.get("attempts", []), list)
            for unit in units
        ):
            continue
        return raw, mtime
    return None


_TEAM_VIEW: Final = {
    "waiting": ("waiting", "waiting"),
    "prepared": ("waiting", "prepared"),
    "dispatched": ("dispatched", "dispatched"),
    "awaiting_check": ("waiting", "awaiting check"),
    "repairing": ("running", "repairing"),
    "accepted": ("done", "accepted"),
    "blocked": ("failed", "blocked"),
}


# ── the builder ──────────────────────────────────────────────────────────


def _clock(moment: float) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(moment))
    except (OverflowError, OSError, ValueError):
        return "--:--"


def _child_state(
    child: Mapping[str, Any], facts: Mapping[str, Any], now: float
) -> tuple[str, str, float | None]:
    """(state, status word, elapsed seconds) for one native child."""
    session = child["child_session_id"]
    usage = facts["usage"].get(session, {})
    row = facts["sessions"].get(session)
    stop = child.get("stop")
    started = float(child["started_at"])
    if isinstance(stop, Mapping):
        status = _text(stop.get("status"), 40) or "unknown"
        duration = stop.get("duration_ms")
        elapsed = duration / 1000.0 if isinstance(duration, int) else max(0.0, float(stop["observed_at"]) - started)
        if status == "completed":
            # Hermes reports a provider refusal as a completed child with no model call at all.
            if facts["read"] and row is not None and not usage.get("rows"):
                return "failed", "no model call", elapsed
            return "done", "done", elapsed
        if status in _FAILED_STOPS:
            return "failed", status, elapsed
        if status == "interrupted":
            return "interrupted", "interrupted", elapsed
        return "unknown", status, elapsed
    if row is not None and row.get("end_reason") == COMPACTION_END_REASON:
        return "unknown", "compacted", None
    if row is not None and row.get("ended_at") is not None:
        return "unknown", "ended", None
    last = max([started] + [value for value in (usage.get("last_seen"),) if isinstance(value, (int, float))])
    if now - last > CHILD_STALE_SECONDS:
        return "unknown", "no news", None
    if usage.get("rows"):
        return "running", "running", max(0.0, now - started)
    return "dispatched", "dispatched", None


def _observed_model(session: str, facts: Mapping[str, Any]) -> str:
    row = facts["sessions"].get(session) or {}
    return row.get("model") or facts["usage"].get(session, {}).get("model", "")


def _category(route: Mapping[str, Any], observed: str, parent_model: str) -> tuple[str, str]:
    """(category, source): the recorded route where the observed model matches it, else inherit by equality, else ?."""
    if route.get("category") and route.get("origin") not in ("cleared", "exhausted_to_inherit"):
        if observed and (same_model(observed, str(route.get("wire_model", "")))
                         or same_model(observed, str(route.get("alias", "")))):
            return str(route["category"]), "route_provenance"
        return "?", "route_model_mismatch" if observed else "model_not_observed"
    if observed and parent_model and same_model(observed, parent_model):
        return "inherit", "parent_model_equality"
    return "?", "no_route_record"


def build_delegation_graph(
    omh_home: Path,
    hermes_home: Path,
    session_id: str,
    *,
    now: float | None = None,
    root_model: str = "",
    titles: Callable[[str], str] = child_title,
    fanout: Mapping[str, Any] | None = None,
    scope: str = "session",
) -> dict[str, Any]:
    """The graph of one conversation's delegated work, or of the fanout passed in when it has none.

    Never raises: an unreadable record or database leaves the graph smaller or
    inactive, and a field it cannot evidence ``?``.
    """
    moment = time.time() if now is None else now
    try:
        graph = _build(omh_home, hermes_home, session_id, moment, root_model, titles, scope)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        graph = inactive_graph(f"unreadable:{type(error).__name__}", scope=scope)
    if graph["status"] != "active" and fanout:
        try:
            graph = fanout_graph(fanout)
        except (ValueError, TypeError, KeyError) as error:
            graph = inactive_graph(f"unreadable_fanout:{type(error).__name__}", scope="global")
    return _bounded(graph)


def _build(
    omh_home: Path,
    hermes_home: Path,
    session_id: str,
    now: float,
    root_model: str,
    titles: Callable[[str], str],
    scope: str,
) -> dict[str, Any]:
    if not session_id:
        return inactive_graph("no_session", scope=scope)
    ledger = read_ledger(Path(omh_home), session_id)
    team = _team_record(Path(omh_home), session_id)
    children = list(ledger["children"]) if ledger else []
    if not children and team is None:
        return inactive_graph("no_delegation", scope=scope)
    sessions = [session_id] + [child["child_session_id"] for child in children]
    if team is not None:
        for unit in team[0]["units"]:
            for attempt in unit.get("attempts") or []:
                dispatch = attempt.get("dispatch") if isinstance(attempt, dict) else None
                if isinstance(dispatch, dict) and isinstance(dispatch.get("child_session_id"), str):
                    sessions.append(dispatch["child_session_id"])
    delegations = [child.get("delegation_id", "") for child in children]
    facts = state_facts(Path(hermes_home), sessions, delegations)
    root_row = facts["sessions"].get(session_id) or {}
    root = {
        "id": "root",
        "kind": "root",
        "title": "main",
        "model": root_row.get("model") or _text(root_model) or "?",
        "effort": root_row.get("effort", ""),
        "state": "root",
        "status": "",
        "order": 0,
        "group": "",
    }
    nodes: list[dict[str, Any]] = [root]
    edges: list[dict[str, str]] = []
    node_of_subagent: dict[str, str] = {}
    node_of_session: dict[str, str] = {}
    model_of_node: dict[str, str] = {"root": root["model"] if root["model"] != "?" else ""}

    team_bound: dict[str, str] = {}
    if team is not None:
        record, _mtime = team
        for unit in record["units"]:
            attempts = unit.get("attempts") or []
            attempt = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
            dispatch = attempt.get("dispatch") if isinstance(attempt.get("dispatch"), dict) else {}
            if isinstance(dispatch.get("child_session_id"), str):
                team_bound[dispatch["child_session_id"]] = str(unit["unit_id"])

    by_session = {child["child_session_id"]: child for child in children}
    ordered = sorted(children, key=lambda child: child["started_at"])
    waves: dict[tuple[str, str], int] = {}
    wave_start: dict[tuple[str, str], float] = {}
    for child in ordered:
        if child["parent_session_id"] != session_id or child["child_session_id"] in team_bound:
            continue
        key = (child["parent_session_id"], child["parent_turn_id"] or child.get("call_id", ""))
        if key not in waves:
            waves[key] = len(waves) + 1
            wave_start[key] = child["started_at"]

    def facts_for(child: Mapping[str, Any], parent_model: str) -> dict[str, Any]:
        session = child["child_session_id"]
        observed = _observed_model(session, facts)
        delegation = facts["delegations"].get(child.get("delegation_id", ""), {})
        model = observed or delegation.get("model", "")
        category, source = _category(child.get("route") or {}, observed, parent_model)
        state, status, elapsed = _child_state(child, facts, now)
        return {
            "model": model or "?",
            "effort": (facts["sessions"].get(session) or {}).get("effort", ""),
            "category": category,
            "category_source": source,
            "state": state,
            "status": status,
            "elapsed_seconds": elapsed,
        }

    def title_for(child: Mapping[str, Any]) -> str:
        title = titles(child["subagent_id"])
        index = child.get("task_index")
        delegation = facts["delegations"].get(child.get("delegation_id", ""), {})
        goals = delegation.get("goals", [])
        if not title and isinstance(index, int) and 0 <= index < len(goals):
            title = goals[index]
        return title or facts["openers"].get(child["child_session_id"], "") or "?"

    order = 1
    if team is not None:
        record, _mtime = team
        units = record["units"]
        states = {str(unit["unit_id"]): team_unit_state(unit) for unit in units}
        depth: dict[str, int] = {}

        def stage(unit_id: str, seen: frozenset[str] = frozenset()) -> int:
            if unit_id in depth:
                return depth[unit_id]
            unit = next(item for item in units if item["unit_id"] == unit_id)
            parents = [dep for dep in unit["depends_on"] if dep not in seen]
            depth[unit_id] = 1 + max([stage(dep, seen | {unit_id}) for dep in parents] or [0])
            return depth[unit_id]

        for unit in units:
            unit_id = str(unit["unit_id"])
            node_id = f"t:{unit_id}"
            state, status = _TEAM_VIEW.get(states[unit_id], ("unknown", states[unit_id]))
            node: dict[str, Any] = {
                "id": node_id, "kind": "lane", "title": _text(unit.get("title"), TITLE_LIMIT) or "?",
                "category": "?", "category_source": "not_dispatched", "model": "?", "effort": "",
                "state": state, "status": status, "elapsed_seconds": None, "order": order,
                "group": f"stage {stage(unit_id)}", "waits_on": 0, "team_unit": unit_id,
            }
            order += 1
            unmet = [dep for dep in unit["depends_on"] if states.get(dep) != "accepted"]
            if states[unit_id] in ("waiting", "prepared") and unmet:
                node["waits_on"] = len(unmet)
            child_session = next((session for session, bound in team_bound.items() if bound == unit_id), "")
            child = by_session.get(child_session)
            if child is not None:
                derived = facts_for(child, model_of_node["root"])
                node.update({key: derived[key] for key in ("model", "effort", "category", "category_source")})
                if states[unit_id] == "dispatched":
                    node.update(state=derived["state"], status=derived["status"],
                                elapsed_seconds=derived["elapsed_seconds"])
                elif derived["elapsed_seconds"] is not None:
                    node["elapsed_seconds"] = derived["elapsed_seconds"]
                node_of_subagent[child["subagent_id"]] = node_id
                model_of_node[node_id] = derived["model"] if derived["model"] != "?" else ""
            if child_session:
                node_of_session[child_session] = node_id
            nodes.append(node)
            if unit["depends_on"]:
                edges.extend({"from": f"t:{dep}", "to": node_id, "kind": "depends_on"} for dep in unit["depends_on"])
            else:
                edges.append({"from": "root", "to": node_id, "kind": "dispatch"})

    for child in ordered:
        if child["child_session_id"] in team_bound:
            continue
        node_id = f"s:{child['subagent_id']}"
        node_of_subagent[child["subagent_id"]] = node_id
        node_of_session[child["child_session_id"]] = node_id
    for child in ordered:
        if child["child_session_id"] in team_bound:
            continue
        node_id = node_of_subagent[child["subagent_id"]]
        parent_id = node_of_subagent.get(child["parent_subagent_id"], "") if child["parent_subagent_id"] else ""
        if not parent_id:
            parent_id = node_of_session.get(child["parent_session_id"], "")
        if not parent_id:
            parent_id = "root" if child["parent_session_id"] == session_id else ""
        if not parent_id:
            continue  # a child whose parent this graph does not hold is not drawn hanging from the wrong node
        derived = facts_for(child, model_of_node.get(parent_id, ""))
        key = (child["parent_session_id"], child["parent_turn_id"] or child.get("call_id", ""))
        group = f"wave {waves[key]} · {_clock(wave_start[key])}" if parent_id == "root" and key in waves else ""
        nodes.append({
            "id": node_id, "kind": "lane", "title": _text(title_for(child), TITLE_LIMIT) or "?",
            **derived, "order": order, "group": group, "waits_on": 0,
        })
        model_of_node[node_id] = derived["model"] if derived["model"] != "?" else ""
        order += 1
        edges.append({"from": parent_id, "to": node_id, "kind": "dispatch"})

    lanes = [node for node in nodes if node["kind"] != "root"]
    if not lanes:
        return inactive_graph("no_delegation", scope=scope)
    settled = all(node["state"] in ("done", "failed", "interrupted", "unknown") for node in lanes)
    newest = max([child["started_at"] for child in children]
                 + [float(child["stop"]["observed_at"]) for child in children if isinstance(child.get("stop"), dict)]
                 + ([team[1]] if team is not None else []))
    if settled and now - newest > GRAPH_LINGER_SECONDS:
        return inactive_graph("settled", scope=scope)
    name = f"team {_text(team[0].get('team_id'), 48)}" if team is not None and team[0].get("team_id") else "delegation"
    return _finish(name, nodes, edges, scope)


def fanout_graph(record: Mapping[str, Any]) -> dict[str, Any]:
    """A verified local fanout as a graph: its units, their titles and owners, the recorded dependencies.

    ``record`` carries the frozen contract and the roster the HUD reader
    already checked (`subagent_graph_contract`); this only reshapes them.
    """
    contract = record.get("contract") or {}
    roster = record.get("roster") or {}
    fanout_id = _text(contract.get("fanout_id"), 64)
    units = [unit for unit in contract.get("units", []) if isinstance(unit, Mapping)][:GRAPH_NODE_LIMIT]
    statuses = {
        str(unit.get("unit_id")): unit
        for unit in roster.get("units", []) if isinstance(unit, Mapping)
    }
    ids = {str(unit.get("unit_id")) for unit in units}
    nodes: list[dict[str, Any]] = [{
        "id": "root", "kind": "root", "title": f"fanout {fanout_id}" if fanout_id else "fanout",
        "model": "", "effort": "", "state": "root", "status": "", "order": 0, "group": "",
    }]
    edges: list[dict[str, str]] = []
    done = {
        unit_id for unit_id, entry in statuses.items() if entry.get("status") in _FANOUT_SUCCESS
    }
    for order, unit in enumerate(units, start=1):
        unit_id = str(unit.get("unit_id"))
        entry = statuses.get(unit_id, {})
        status = _text(entry.get("status"), 40) or "unknown"
        depends = [dep for dep in unit.get("depends_on", []) if isinstance(dep, str) and dep in ids]
        if status == "running":
            state = "running"
        elif status in _FANOUT_SUCCESS:
            state = "done"
        elif status == "prepared_not_observed":
            state, status = "waiting", "prepared"
        elif status == "unknown":
            state = "unknown"
        else:
            state = "failed"
        unmet = [dep for dep in depends if dep not in done]
        stuck = roster_progress_fields(entry) if state == "running" else {}
        elapsed = None
        if stuck:
            # `running` only says a process was spawned; a unit whose work stopped moving is never drawn running.
            state = "stuck"
            status = f"stuck: {_text(stuck.get('state_reason'), 60) or stuck['unit_state']}"
            elapsed = stuck.get("stalled_for_seconds")
        nodes.append({
            "id": f"f:{unit_id}", "kind": "lane", "title": _text(unit.get("title"), TITLE_LIMIT) or unit_id,
            "category": _text(unit.get("owner"), 40) or "?", "category_source": "fanout_owner",
            "model": _text(entry.get("model")) or "?", "effort": _text(entry.get("reasoning_effort"), 40),
            "state": state, "status": status, "elapsed_seconds": elapsed, "order": order,
            "group": "", "waits_on": len(unmet) if state == "waiting" else 0,
        })
        if depends:
            edges.extend({"from": f"f:{dep}", "to": f"f:{unit_id}", "kind": "depends_on"} for dep in depends)
        else:
            edges.append({"from": "root", "to": f"f:{unit_id}", "kind": "dispatch"})
    if len(nodes) < 2:
        return inactive_graph("empty_fanout", scope="global")
    return _finish(f"fanout {fanout_id}" if fanout_id else "fanout", nodes, edges, "global", "fanout")


def _layers(nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, str]]) -> list[list[str]]:
    ids = [str(node["id"]) for node in nodes]
    incoming = {node_id: 0 for node_id in ids}
    successors: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in edges:
        if edge["from"] in successors and edge["to"] in incoming:
            successors[edge["from"]].append(edge["to"])
            incoming[edge["to"]] += 1
    layer = {node_id: 0 for node_id in ids}
    ready = [node_id for node_id in ids if incoming[node_id] == 0]
    while ready:
        current = ready.pop(0)
        for lower in successors[current]:
            layer[lower] = max(layer[lower], layer[current] + 1)
            incoming[lower] -= 1
            if incoming[lower] == 0:
                ready.append(lower)
    if any(count for count in incoming.values()):
        return []
    depth = max(layer.values(), default=-1) + 1
    return [[node_id for node_id in ids if layer[node_id] == index] for index in range(depth)]


def _finish(
    name: str, nodes: list[dict[str, Any]], edges: list[dict[str, str]], scope: str, source: str = "native"
) -> dict[str, Any]:
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "status": "active",
        "reason": "",
        "scope": scope,
        "source": source,
        "name": name,
        "nodes": nodes,
        "edges": edges,
        "layers": _layers(nodes, edges),
        "hidden_nodes": 0,
        "claim_boundary": GRAPH_CLAIM_BOUNDARY,
    }


_KEEP_ORDER: Final = {
    "running": 0, "stuck": 0, "failed": 1, "dispatched": 2, "waiting": 3, "interrupted": 4, "unknown": 5, "done": 6,
}


def _bounded(graph: dict[str, Any]) -> dict[str, Any]:
    """At most `GRAPH_NODE_LIMIT` nodes and `GRAPH_BYTE_LIMIT` bytes; what is left out is counted, edges kept whole."""
    nodes = graph.get("nodes") or []
    if graph.get("status") != "active":
        return graph

    def size(candidate: dict[str, Any]) -> int:
        return len(json.dumps(candidate, sort_keys=True, default=str).encode("utf-8"))

    keep = len(nodes)
    while keep > 1 and (keep > GRAPH_NODE_LIMIT or size(_cut(graph, keep)) > GRAPH_BYTE_LIMIT):
        keep -= 1
    return _cut(graph, keep) if keep < len(nodes) else graph


def _cut(graph: dict[str, Any], keep: int) -> dict[str, Any]:
    nodes = graph["nodes"]
    if keep >= len(nodes):
        return graph
    root = [node for node in nodes if node.get("kind") == "root"]
    lanes = sorted(
        (node for node in nodes if node.get("kind") != "root"),
        key=lambda node: (_KEEP_ORDER.get(node.get("state", ""), 5), -int(node.get("order") or 0)),
    )
    kept_ids = {node["id"] for node in root + lanes[: max(0, keep - len(root))]}
    # A kept node's parents are kept too, so no kept edge hangs from nothing.
    changed = True
    while changed:
        changed = False
        for edge in graph["edges"]:
            if edge["to"] in kept_ids and edge["from"] not in kept_ids:
                kept_ids.add(edge["from"])
                changed = True
    kept = [node for node in nodes if node["id"] in kept_ids]
    edges = [edge for edge in graph["edges"] if edge["from"] in kept_ids and edge["to"] in kept_ids]
    return {
        **graph,
        "nodes": kept,
        "edges": edges,
        "layers": _layers(kept, edges),
        "hidden_nodes": len(nodes) - len(kept),
    }
