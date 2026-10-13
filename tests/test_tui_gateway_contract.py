"""The OMH TUI's declared slice of Hermes' gateway protocol."""

from __future__ import annotations

import unittest

from omh.tui import gateway_contract as contract


class ServerRequestTreatmentTests(unittest.TestCase):
    def test_every_server_request_hermes_advertises_has_a_decline(self) -> None:
        # The 13 names `client.capabilities` returned from Hermes 6c61c3e27d.
        advertised = {
            "approval", "clarify", "display.install.sudo", "preview.act", "preview.read", "secret", "sudo",
            "terminal.read", "tour", "vault.code", "vault.save_login", "vault.unlock_prompt", "window.read",
        }
        self.assertEqual(set(contract.SERVER_REQUESTS), advertised)
        for method in advertised:
            with self.subTest(method=method):
                self.assertIsNotNone(contract.server_request_treatment(method).decline)

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
        self.assertIsNone(treatment.prompt)
        self.assertEqual(treatment.decline["error"]["code"], contract.METHOD_NOT_FOUND)


class ClientMethodTests(unittest.TestCase):
    def test_undeclared_params_names_only_the_extra_keys(self) -> None:
        self.assertEqual(contract.undeclared_params("session.create", {"cols": 80, "cwd": "/w"}), [])
        self.assertEqual(contract.undeclared_params("session.create", {"cols": 80, "title": "x"}), ["title"])

    def test_every_key_of_an_undeclared_method_is_undeclared(self) -> None:
        self.assertEqual(contract.undeclared_params("voice.toggle", {"on": True}), ["on"])

    def test_drift_errors_are_param_rejection_and_unknown_method_only(self) -> None:
        self.assertTrue(contract.is_drift_error({"code": 4000, "message": "extra fields"}))
        self.assertTrue(contract.is_drift_error({"code": -32601, "message": "unknown method"}))
        self.assertFalse(contract.is_drift_error({"code": 4090, "message": "lease"}))
        self.assertFalse(contract.is_drift_error("unknown method"))


class GatewayEnvironmentTests(unittest.TestCase):
    def test_attach_url_is_both_refused_and_stripped(self) -> None:
        self.assertIn(contract.ATTACH_ENV, contract.STRIPPED_GATEWAY_ENV)
        self.assertIn("HERMES_TUI_SIDECAR_URL", contract.STRIPPED_GATEWAY_ENV)


if __name__ == "__main__":
    unittest.main()
