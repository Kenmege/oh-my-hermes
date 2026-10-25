"""Public installation/recovery claims must match lifecycle safety boundaries."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallationLifecycleDocumentationTests(unittest.TestCase):
    def setUp(self):
        self.installation = (ROOT / "docs/INSTALLATION.md").read_text(encoding="utf-8")
        self.agents = (ROOT / "INSTALL_FOR_AGENTS.md").read_text(encoding="utf-8")

    def both_contain(self, *phrases):
        for name, text in (("installation", self.installation), ("agents", self.agents)):
            normalized = " ".join(text.split())
            for phrase in phrases:
                with self.subTest(surface=name, phrase=phrase):
                    self.assertIn(phrase, normalized)

    def test_shared_store_cleanup_is_not_profile_local_deletion(self):
        self.both_contain("profile-local detach", "shared store", "shared command", "store_removal", "kept_paths")

    def test_unknown_reference_inventory_fails_closed(self):
        self.both_contain("unreadable_reference_inventory", "nonzero", "persisted", "transient process environment")

    def test_registration_only_does_not_disable_plugin_or_prove_desktop_state(self):
        self.both_contain("does not disable the plugin", "Desktop mounting must be observed separately")
        self.assertNotIn("after `omh uninstall --registration-only` the file is still copied but the backend is not mounted", " ".join(self.installation.split()))

    def test_primary_skill_store_and_profile_runtime_store_are_distinct(self):
        self.both_contain("primary skill store A", "runtime store B", "real path", "basename")

    def test_partial_update_is_one_final_result_not_early_success(self):
        self.both_contain("exactly one final JSON document", "hermes_profiles", "status: partial", "failures", "exit_code", "later healthy profiles")

    def test_explicit_source_is_still_content_only(self):
        self.both_contain("--source", "--from-skills-dir", "workflow-content-only")

    def test_binding_and_command_ownership_are_explicit(self):
        self.both_contain("implicit default", "custom store", "--keep-command", "generations", "--omh-home")

    def test_publication_is_not_windows_runtime_evidence(self):
        self.both_contain("Publication is not runtime verification", "Windows CI")
        for text in (self.installation, self.agents):
            self.assertNotIn("pending the first distribution release", text)
            self.assertNotIn("prepared but not yet published", text)
            self.assertNotIn("public as of v1.0.6", text)

    def test_existing_recovery_anchors_remain(self):
        for heading in ("## Runtime state in Hermes profiles (operator reference)", "## Uninstall", "### What a full uninstall takes back out of `config.yaml`", "### Package-manager command removal"):
            self.assertIn(heading, self.installation)


if __name__ == "__main__":
    unittest.main()
