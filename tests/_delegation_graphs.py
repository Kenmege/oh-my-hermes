"""Delegation graphs the view, panel and snapshot tests draw: the owner's mock-up and its variants."""

from __future__ import annotations


def root(model: str = "claude-opus-5-5", effort: str = "high") -> dict:
    return {"id": "root", "kind": "root", "title": "main", "model": model, "effort": effort, "state": "root",
            "status": "", "order": 0, "group": ""}


def lane(node_id: str, title: str, category: str, model: str, effort: str, state: str, *, elapsed=None,
         order: int = 0, group: str = "", waits: int = 0, status: str = "") -> dict:
    return {"id": node_id, "kind": "lane", "title": title, "category": category, "model": model, "effort": effort,
            "state": state, "status": status or state, "elapsed_seconds": elapsed, "order": order, "group": group,
            "waits_on": waits}


def graph(nodes: list[dict], edges: list[tuple[str, str, str]], *, name: str = "delegation",
          source: str = "native") -> dict:
    return {"schema_version": "delegation_graph/v1", "status": "active", "scope": "session", "source": source,
            "name": name, "hidden_nodes": 0, "nodes": nodes,
            "edges": [{"from": upper, "to": lower, "kind": kind} for upper, lower, kind in edges]}


def owner_mock() -> dict:
    """The owner's mock-up: three lanes from main, and a verification that depends on all three."""
    return graph(
        [
            root(),
            lane("a", "Refresh HUD docs", "writing", "claude-opus-5-5", "medium", "running", elapsed=130, order=1),
            lane("b", "Polish HUD CLI", "quick", "glm-5.3-flash", "low", "done", elapsed=62, order=2),
            lane("c", "Update HUD tests", "deep", "claude-opus-5-5", "high", "running", elapsed=128, order=3),
            lane("v", "Integration verify", "inherit", "claude-opus-5-5", "", "waiting", order=4, waits=3),
        ],
        [("root", "a", "dispatch"), ("root", "b", "dispatch"), ("root", "c", "dispatch"),
         ("a", "v", "depends_on"), ("b", "v", "depends_on"), ("c", "v", "depends_on")],
        name="ulw-work hud-graph",
    )


def parallel(count: int) -> dict:
    lanes = [lane(f"l{index}", f"Lane {index} migrate", "quick", "glm-5.3-flash", "low", "running",
                  elapsed=60 + index, order=index + 1) for index in range(count)]
    return graph([root(), *lanes], [("root", item["id"], "dispatch") for item in lanes])


def nested() -> dict:
    """main -> an orchestrator -> a sub-orchestrator -> two leaves, beside a lane with a Korean title."""
    return graph(
        [
            root(),
            lane("o", "Port the auth module", "architect", "claude-opus-5-5", "xhigh", "running", elapsed=400, order=1),
            lane("k", "인증 모듈 문서 정리", "writing", "claude-opus-5-5", "medium", "running", elapsed=355, order=2),
            lane("o1", "Port token store", "inherit", "claude-opus-5-5", "xhigh", "done", elapsed=131, order=3),
            lane("o2", "Sub-orchestrate refresh", "inherit", "claude-opus-5-5", "xhigh", "running", elapsed=182,
                 order=4),
            lane("o21", "Refresh tokens", "?", "?", "", "dispatched", order=5),
            lane("o22", "Rotate keys", "?", "?", "", "dispatched", order=6),
        ],
        [("root", "o", "dispatch"), ("root", "k", "dispatch"), ("o", "o1", "dispatch"), ("o", "o2", "dispatch"),
         ("o2", "o21", "dispatch"), ("o2", "o22", "dispatch")],
    )
