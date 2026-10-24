"""Setup ends with a next action, not a control-plane tour."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()
from omh.commands import setup
from omh.commands.language import LANGUAGE_CODES, tr


class FriendlySetupOutputTests(unittest.TestCase):
    def payload(self):
        return {
            "steps": {"install": {"skills": ["a"]}, "profile": {"default_executor": "choose"}},
            "hermes_native": {"discovery_status": "config_registered_reload_required"},
            "operator_summary": {"status": "configured", "command_path": {"found": True, "path": "/bin/omh"}},
        }

    def render(self, payload, language="en"):
        output = io.StringIO()
        with redirect_stdout(output), patch.object(setup, "_use_color", return_value=False):
            setup._print_setup_summary(payload, language=language)
        return output.getvalue()

    def test_success_has_one_clear_launch_action_in_every_locale(self):
        for language in LANGUAGE_CODES:
            with self.subTest(language=language):
                output = self.render(self.payload(), language)
                self.assertIn("`omh`", output)
                self.assertLessEqual(len(output.splitlines()), 12)
                self.assertNotIn("omh model-chains interview", output)
                self.assertNotIn("--default-executor", output)

    def test_preview_and_skipped_connection_do_not_offer_a_ready_launch(self):
        for status, discovery, dry_run in (
            ("dry_run", "dry_run_not_observed", True),
            ("skills_only", "not_registered_skip_apply", False),
        ):
            with self.subTest(status=status):
                payload = self.payload()
                payload["dry_run"] = dry_run
                payload["operator_summary"]["status"] = status
                payload["hermes_native"]["discovery_status"] = discovery
                output = self.render(payload)
                self.assertNotIn("Run `omh` to", output)
                self.assertIn("omh setup", output) if not dry_run else self.assertIn("--dry-run", output)

    def test_missing_command_and_profile_failures_keep_actionable_details(self):
        payload = self.payload()
        payload["operator_summary"]["command_path"] = {"found": False}
        payload["hermes_profiles"] = [{"profile": "work", "status": "failed", "error": "config write refused"}]
        output = self.render(payload)
        self.assertIn("PATH", output)
        self.assertIn("config write refused", output)
        self.assertIn("work", output)

    def test_optional_question_copy_is_short_and_does_not_claim_execution(self):
        for language in LANGUAGE_CODES:
            for key in ("maestro_delegation_note", "provider_entitlements_note", "model_chains_interview_note"):
                with self.subTest(language=language, key=key):
                    self.assertLessEqual(len(tr(language, key)), 270)
        self.assertIn("No CLI", tr("en", "maestro_delegation_note"))
        self.assertIn("not checked", tr("en", "provider_entitlements_note"))


if __name__ == "__main__":
    unittest.main()
