"""The OMH TUI's declared slice of Hermes' gateway protocol."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from omh.tui import gateway_contract as contract


class ServerRequestTreatmentTests(unittest.TestCase):
    def test_every_server_request_hermes_advertises_has_a_treatment(self) -> None:
        # The 13 names `client.capabilities` returned from Hermes 6c61c3e27d.
        advertised = {
            "approval", "clarify", "display.install.sudo", "preview.act", "preview.read", "secret", "sudo",
            "terminal.read", "tour", "vault.code", "vault.save_login", "vault.unlock_prompt", "window.read",
        }
        self.assertEqual(set(contract.SERVER_REQUESTS), advertised)

    def test_approval_is_declined_with_an_explicit_deny(self) -> None:
        # An error answer withdraws an approval instead of denying it.
        decline = contract.server_request_treatment("approval").decline
        self.assertEqual(decline, {"result": {"choice": "deny"}})

    def test_clarify_decline_carries_no_answers(self) -> None:
        self.assertEqual(contract.server_request_treatment("clarify").decline, {"result": {}})

    def test_secret_prompts_are_masked(self) -> None:
        for method in ("sudo", "secret"):
            with self.subTest(method=method):
                self.assertTrue(contract.server_request_treatment(method).masked)
        self.assertFalse(contract.server_request_treatment("approval").masked)

    def test_an_unknown_request_is_answered_unsupported_not_ignored(self) -> None:
        treatment = contract.server_request_treatment("future.request")
        self.assertFalse(treatment.shown)
        self.assertEqual(treatment.decline["error"]["code"], contract.METHOD_NOT_FOUND)


class ClientMethodTests(unittest.TestCase):
    def test_undeclared_params_names_only_the_extra_keys(self) -> None:
        self.assertEqual(contract.undeclared_params("session.create", {"cols": 80, "cwd": "/w"}), [])
        self.assertEqual(contract.undeclared_params("session.create", {"cols": 80, "title": "x"}), ["title"])

    def test_every_declared_method_is_one_the_tui_calls(self) -> None:
        # CLIENT_METHODS is "the methods the TUI calls": each name appears as a literal in a TUI module.
        modules = [path for path in Path(contract.__file__).parent.glob("*.py") if path.name != "gateway_contract.py"]
        literals = {
            node.value
            for path in modules
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertEqual(sorted(set(contract.CLIENT_METHODS) - literals), [])

    def test_the_two_calls_this_phase_adds_carry_exactly_what_hermes_names(self) -> None:
        # Read from Hermes 6c61c3e27d's registry (tui_gateway/contracts/): ConfigSetParams is key, value,
        # session_id, scope, confirm_expensive_model, profile; SessionCorrectionParams is session_id, text, profile.
        self.assertEqual(contract.CLIENT_METHODS["config.set"], {"key", "value", "session_id"})
        self.assertEqual(contract.CLIENT_METHODS["session.steer"], {"session_id", "text"})
        self.assertEqual(set(contract.CONFIG_SET_VALUES), {"yolo", "reasoning"})
        self.assertEqual(contract.CONFIG_SET_VALUES["yolo"], {"on", "off"})
        # hermes_constants.VALID_REASONING_EFFORTS in 6c61c3e27d, plus none (parse_reasoning_effort: thinking off).
        self.assertEqual(
            contract.REASONING_LEVELS, ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
        )
        self.assertEqual(contract.CONFIG_SET_VALUES["reasoning"], set(contract.REASONING_LEVELS))

    def test_a_config_set_is_sendable_only_for_one_session_with_an_explicit_value(self) -> None:
        good = {"key": "yolo", "value": "on", "session_id": "live-1"}
        self.assertEqual(contract.call_problem("config.set", good), "")
        self.assertEqual(contract.call_problem("config.set", {**good, "key": "reasoning", "value": "high"}), "")
        refused = {
            "a scope": {**good, "scope": "global"},
            "a session scope too": {**good, "scope": "session"},
            "no session id": {"key": "yolo", "value": "on"},
            "an empty session id": {**good, "session_id": ""},
            "a null session id": {**good, "session_id": None},
            "no value (a toggle)": {"key": "yolo", "session_id": "live-1"},
            "an empty value": {**good, "value": ""},
            "a bool value": {**good, "value": True},
            "a yolo word Hermes also reads": {**good, "value": "1"},
            "an effort as a yolo value": {**good, "value": "high"},
            "another key": {**good, "key": "approvals.mode", "value": "off"},
            "the model key": {**good, "key": "model", "value": "x"},
            "no key": {"value": "on", "session_id": "live-1"},
        }
        for name, params in refused.items():
            with self.subTest(name):
                self.assertNotEqual(contract.call_problem("config.set", params), "")

    def test_a_reasoning_config_set_takes_effort_levels_only(self) -> None:
        # Under the same key Hermes reads these as display settings and writes them to config.yaml.
        effort = {"key": "reasoning", "session_id": "live-1"}
        for level in contract.REASONING_LEVELS:
            with self.subTest(level=level):
                self.assertEqual(contract.call_problem("config.set", {**effort, "value": level}), "")
        for word in ("show", "hide", "on", "off", "full", "all", "clamp", "collapse", "short", "HIGH", "", "hgih"):
            with self.subTest(word=word):
                self.assertNotEqual(contract.call_problem("config.set", {**effort, "value": word}), "")
        self.assertNotEqual(contract.call_problem("config.set", {"key": "reasoning", "value": "high"}), "")

    def test_call_problem_names_undeclared_keys_for_every_method(self) -> None:
        self.assertEqual(contract.call_problem("session.create", {"cols": 80, "cwd": "/w"}), "")
        self.assertIn("title", contract.call_problem("session.create", {"cols": 80, "title": "x"}))
        self.assertIn("scope", contract.call_problem("config.set", {"key": "yolo", "scope": "global"}))
        self.assertEqual(contract.call_problem("session.steer", {"session_id": "live-1", "text": "x"}), "")

    def test_every_key_of_an_undeclared_method_is_undeclared(self) -> None:
        self.assertEqual(contract.undeclared_params("voice.toggle", {"on": True}), ["on"])

    def test_drift_errors_are_param_rejection_and_unknown_method_only(self) -> None:
        self.assertTrue(contract.is_drift_error({"code": 4000, "message": "extra fields"}))
        self.assertTrue(contract.is_drift_error({"code": -32601, "message": "unknown method"}))
        self.assertFalse(contract.is_drift_error({"code": 4090, "message": "lease"}))
        self.assertFalse(contract.is_drift_error("unknown method"))


TUI_DIR = Path(contract.__file__).parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tui_gateway"


# `panels` reads OMH's own HUD snapshot (`read_omh_hud`), never a gateway frame: its `yolo` and `queued` are OMH's.
NOT_GATEWAY_READERS = frozenset({"gateway_contract.py", "panels.py"})


def tui_modules() -> dict[str, ast.Module]:
    """Every TUI module that reads what the gateway sends, parsed."""
    return {
        path.name: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(TUI_DIR.glob("*.py"))
        if path.name not in NOT_GATEWAY_READERS
    }


def recorded_events(name: str) -> list[tuple[str, dict]]:
    """(type, payload) of every event a recorded fixture holds, whichever of the two row shapes it uses."""
    lines = (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()[1:]
    events = []
    for row in map(json.loads, lines):
        params = row.get("frame", {}).get("params", {}) if "frame" in row else row
        if row.get("dir", "in") == "in" and isinstance(params.get("type"), str):
            events.append((params["type"], params.get("payload", {})))
    return events


class InboundTests(unittest.TestCase):
    """The names, fields and values the TUI keys on in what Hermes sends, declared once."""

    def test_the_declared_shapes_are_the_ones_hermes_names(self) -> None:
        # Read from Hermes 6c61c3e27d's registry (tui_gateway/contracts/): the six event(...) calls for
        # SubagentEventPayload in events.py, CorrectionStatus in sessions.py, SubagentTailResult's fields in
        # profiles_vault_complete_foreign_subagents.py, and SessionLiveInfo's yolo and reasoning_effort in common.py.
        self.assertEqual(
            contract.SUBAGENT_EVENTS,
            ("subagent.spawn_requested", "subagent.start", "subagent.progress", "subagent.thinking", "subagent.tool",
             "subagent.complete"),
        )
        self.assertTrue(all(name.startswith(contract.SUBAGENT_EVENT_PREFIX) for name in contract.SUBAGENT_EVENTS))
        self.assertEqual(contract.STEER_STATUSES, {"queued", "redirected", "rejected"})
        self.assertLessEqual({contract.STEER_QUEUED, contract.STEER_REJECTED}, contract.STEER_STATUSES)
        tail_result = {"subagent_id", "available", "text", "truncated"}
        self.assertLessEqual({contract.TAIL_AVAILABLE, contract.TAIL_TEXT, contract.TAIL_TRUNCATED}, tail_result)
        self.assertEqual((contract.SESSION_INFO_YOLO, contract.SESSION_INFO_REASONING_EFFORT), ("yolo", "reasoning_effort"))

    def test_recorded_frames_carry_the_declared_shapes(self) -> None:
        relayed = recorded_events("subagent_relay")
        self.assertTrue(relayed)
        for event_type, _payload in relayed:
            with self.subTest(event_type=event_type):
                self.assertIn(event_type, contract.SUBAGENT_EVENTS)
        infos = [payload for name in ("session", "approval") for kind, payload in recorded_events(name) if kind == "session.info"]
        self.assertTrue(infos)
        for payload in infos:
            self.assertIsInstance(payload[contract.SESSION_INFO_YOLO], bool)
            self.assertIsInstance(payload[contract.SESSION_INFO_REASONING_EFFORT], str)

    def test_the_roster_folds_exactly_the_declared_subagent_events(self) -> None:
        from omh.tui.subagents import Roster

        self.assertEqual(set(Roster()._handlers), set(contract.SUBAGENT_EVENTS))

    def test_the_tui_reads_these_shapes_through_the_contract_only(self) -> None:
        fields = {
            contract.SESSION_INFO_YOLO, contract.SESSION_INFO_REASONING_EFFORT, contract.TAIL_AVAILABLE, contract.TAIL_TRUNCATED,
        }
        for name, module in tui_modules().items():
            for node in ast.walk(module):
                with self.subTest(module=name, line=getattr(node, "lineno", 0)):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        # A subagent name written out is a method the TUI calls, never an event it reads.
                        if node.value.startswith(contract.SUBAGENT_EVENT_PREFIX):
                            self.assertIn(node.value, contract.CLIENT_METHODS)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                        literal = node.args[0] if node.args and isinstance(node.args[0], ast.Constant) else None
                        self.assertNotIn(getattr(literal, "value", None), fields)
                    if isinstance(node, ast.Compare):
                        compared = [part for part in (node.left, *node.comparators) if isinstance(part, ast.Constant)]
                        self.assertFalse({part.value for part in compared} & contract.STEER_STATUSES)
        (on_tail,) = [
            node for node in ast.walk(tui_modules()["app.py"]) if isinstance(node, ast.FunctionDef) and node.name == "_on_tail"
        ]
        literal_gets = [
            node.args[0].value
            for node in ast.walk(on_tail)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and node.args and isinstance(node.args[0], ast.Constant)
        ]
        self.assertEqual(literal_gets, [])  # `text` too: it is the tail's own field, read by its declared name


if __name__ == "__main__":
    unittest.main()
