"""Keep compatibility and recovery guidance inside observed support boundaries."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CompatibilityDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = " ".join((ROOT / "docs/INSTALLATION.md").read_text(encoding="utf-8").split())

    def test_loader_selection_does_not_prefer_leftover_legacy_python(self):
        for phrase in ("--print-runtime-command", "leftover legacy venv", "hermes_runtime_command_failed", "hermes_python_override_missing", "registration evidence only"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_optional_browser_support_is_not_claimed_restored(self):
        for phrase in ("host_browser_identity_unsupported", "_BROWSER_CONTROL_PRINCIPAL", "_BROWSER_CONTROL_TRANSPORT_FAMILY", "optional browser execution remains unsupported", "PM-only browser-skill promotion is not covered"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_ci_helper_is_not_hosted_matrix_evidence(self):
        for phrase in ("local helper result is not a hosted CI result", "Fresh PM provisioning", "native Windows", "branch-rule enforcement"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_pointer_rollback_does_not_claim_shared_resource_restoration(self):
        for phrase in ("restore_required", "rollback.status", "restore_failed", "shared-resource restoration", "Do not delete the retained candidate", "only restoration until it succeeds"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)
        self.assertNotIn("there is no half-updated state in between", self.text)
        self.assertNotIn("The previous generation, active again |", self.text)

    def test_incidental_profile_directories_are_not_configured_profiles(self):
        for phrase in ("logs/cache alone", "SOUL.md", "profile.yaml", "tombstoned", "symlink refusal"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)


if __name__ == "__main__":
    unittest.main()
