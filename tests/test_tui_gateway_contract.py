"""The OMH TUI's declared slice of Hermes' gateway protocol."""

from __future__ import annotations

import ast
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
        self.assertEqual(contract.CONFIG_SET_KEYS, {"yolo", "reasoning"})

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
            "another key": {**good, "key": "approvals.mode", "value": "off"},
            "the model key": {**good, "key": "model", "value": "x"},
            "no key": {"value": "on", "session_id": "live-1"},
        }
        for name, params in refused.items():
            with self.subTest(name):
                self.assertNotEqual(contract.call_problem("config.set", params), "")

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


if __name__ == "__main__":
    unittest.main()
