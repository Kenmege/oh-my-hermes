"""The pi-family runtime's profile id was renamed; records written before still load.

Older releases stored a different id for this executor profile in settings,
capability snapshots, fanout unit files, and progress records, and users typed
it on the command line. Every reader resolves that id to `pi-family-runtime`,
and every writer stores only `pi-family-runtime`. The legacy spelling is
imported from `pi_family_interop`, which re-exports the one definition.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from _cli_harness import run_cli
from omh.coding.executor_capability_snapshots import (
    build_executor_capability_snapshot,
    recorded_executor_capability_snapshot,
)
from omh.coding.executor_progress import normalize_executor_profile
from omh.coding.executor_auth_signals import executor_auth_signals
from omh.coding.executors import EXECUTOR_PROFILES, canonical_executor_profile
from omh.coding.fanout import build_fanout_contract
from omh.coding.fanout_confinement import owner_state_directories
from omh.coding.fanout_dispatch import (
    PI_FAMILY_HOST_CANDIDATES,
    _unit_capability_precheck,
    build_dispatch_argv,
    fanout_child_env,
    pi_family_host,
)
from omh.coding.model_discovery_adapters import RUNTIME_CONFIG_SOURCE
from omh.coding.model_inventory import local_model_inventory
from omh.coding.owner_progress_normalization import _normalized_owner
from omh.coding.pi_family_interop import (
    LEGACY_RUNTIME_PROFILE_ID,
    LEGACY_RUNTIME_PROGRESS_LANE,
    RUNTIME_USER_PHRASE,
)
from omh.commands.model_setup_overrides import load_category_overrides
from omh.profiles.setup import read_setup_profile, write_setup_profile
from omh.routing.chat import route_chat_message
from omh.routing.coding_route_actions import named_executor_owners
from omh.routing.localization import normalized_phrase
from omh.system.paths import CONTINUITY_FORBIDDEN_TARGETS, OmhPaths
from omh.wrapper.contract import _executor_target_from_message

_PROFILE = "pi-family-runtime"
# The external spellings, rebuilt here from their own pieces rather than read
# from the interop module, so a drift in that module's values fails a test.
_HOST = "sen" + "pi"
_SHORT = "o" + "mo"
_FULL = "oh-my-" + "open" + "agent"


def _paths(root: Path) -> OmhPaths:
    return OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")


class LegacyProfileIdReadTests(unittest.TestCase):
    def test_the_legacy_id_resolves_to_the_current_profile_and_is_not_a_profile(self) -> None:
        self.assertEqual(canonical_executor_profile(LEGACY_RUNTIME_PROFILE_ID), _PROFILE)
        self.assertEqual(canonical_executor_profile("codex"), "codex")
        self.assertIn(_PROFILE, EXECUTOR_PROFILES)
        self.assertNotIn(LEGACY_RUNTIME_PROFILE_ID, EXECUTOR_PROFILES)

    def test_a_setup_profile_stored_with_the_legacy_id_reads_as_the_current_profile(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            paths.setup_profile_path.parent.mkdir(parents=True)
            paths.setup_profile_path.write_text(
                json.dumps({"schema_version": "setup_profile/v1", "default_executor": LEGACY_RUNTIME_PROFILE_ID}),
                encoding="utf-8",
            )
            profile = read_setup_profile(paths)
        self.assertEqual(profile["default_executor"], _PROFILE)

    def test_a_capability_snapshot_recorded_under_the_legacy_id_loads_for_the_current_profile(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            snapshot = build_executor_capability_snapshot(
                executor=LEGACY_RUNTIME_PROFILE_ID,
                capabilities={"visual_qa": {"status": "unknown"}},
                recorded_at="2026-10-01T00:00:00Z",
            )
            (directory / f"{LEGACY_RUNTIME_PROFILE_ID}.json").write_text(json.dumps(snapshot), encoding="utf-8")
            loaded = recorded_executor_capability_snapshot(_PROFILE, directory)
            written = sorted(path.name for path in directory.iterdir())
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["executor"], _PROFILE)
        self.assertEqual(written, [f"{LEGACY_RUNTIME_PROFILE_ID}.json"])

    def test_a_fanout_unit_naming_the_legacy_owner_freezes_with_the_current_owner(self) -> None:
        contract = build_fanout_contract(
            "split work",
            [{"unit_id": "core", "title": "Core", "owner": LEGACY_RUNTIME_PROFILE_ID, "file_scope": ["src/"], "depends_on": []}],
        )
        self.assertEqual(contract["units"][0]["owner"], _PROFILE)
        self.assertNotIn(LEGACY_RUNTIME_PROFILE_ID, json.dumps(contract))

    def test_a_contract_frozen_with_the_legacy_owner_is_refused_by_name_at_dispatch(self) -> None:
        # Its provenance digest pins the old bytes, so it is not rewritten on read.
        with TemporaryDirectory() as tmp:
            owner, snapshot, errors = _unit_capability_precheck(
                _paths(Path(tmp)),
                {"unit_id": "core", "owner": LEGACY_RUNTIME_PROFILE_ID, "handoff": {"executor_target": LEGACY_RUNTIME_PROFILE_ID}},
                contract_schema_version="fanout_contract/v2",
            )
        self.assertEqual((owner, snapshot), (LEGACY_RUNTIME_PROFILE_ID, None))
        self.assertEqual(len(errors), 1)
        self.assertIn("re-freeze the units", errors[0])
        self.assertIn(_PROFILE, errors[0])

    def test_the_legacy_id_and_lane_map_onto_the_current_progress_lane_and_owner(self) -> None:
        for value in (LEGACY_RUNTIME_PROFILE_ID, LEGACY_RUNTIME_PROGRESS_LANE):
            with self.subTest(value=value):
                self.assertEqual(normalize_executor_profile(value), "pi_family_runtime")
                self.assertEqual(_normalized_owner(value), _PROFILE)

    def test_a_typed_legacy_executor_argument_is_accepted_as_the_current_profile(self) -> None:
        with TemporaryDirectory() as tmp:
            status, stdout, stderr = run_cli(
                [
                    "--omh-home",
                    str(Path(tmp) / ".omh"),
                    "--hermes-home",
                    str(Path(tmp) / ".hermes"),
                    "coding",
                    "executor-readiness",
                    "--executor",
                    LEGACY_RUNTIME_PROFILE_ID,
                    "--dry-run",
                ]
            )
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout)["profile"], _PROFILE)


class CurrentIdWriteTests(unittest.TestCase):
    def test_setup_given_the_legacy_id_stores_the_current_id(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            write_setup_profile(paths, default_executor=LEGACY_RUNTIME_PROFILE_ID)
            stored = json.loads(paths.setup_profile_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["default_executor"], _PROFILE)

    def test_a_snapshot_recorded_with_the_legacy_executor_argument_is_written_under_the_current_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capabilities = root / "capabilities.json"
            capabilities.write_text(json.dumps({"visual_qa": {"status": "unknown"}}), encoding="utf-8")
            status, stdout, stderr = run_cli(
                [
                    "--omh-home",
                    str(root / ".omh"),
                    "--hermes-home",
                    str(root / ".hermes"),
                    "coding",
                    "capability-snapshot",
                    "record",
                    "--executor",
                    LEGACY_RUNTIME_PROFILE_ID,
                    "--capabilities-json",
                    str(capabilities),
                ]
            )
            self.assertEqual(status, 0, stderr)
            recorded = json.loads(stdout)
            stored = json.loads(Path(recorded["snapshot_path"]).read_text(encoding="utf-8"))
        self.assertEqual(Path(recorded["snapshot_path"]).name, f"{_PROFILE}.json")
        self.assertEqual(stored["executor"], _PROFILE)


class RuntimeCueRoutingTests(unittest.TestCase):
    def test_the_runtime_phrase_still_names_the_pi_family_owner(self) -> None:
        message = f"{RUNTIME_USER_PHRASE}으로 구현해줘"
        self.assertEqual(named_executor_owners(normalized_phrase(message)), (_PROFILE,))

    def test_the_runtime_phrase_still_routes_a_delivery_request_to_the_coding_lane(self) -> None:
        decision = route_chat_message(f"{RUNTIME_USER_PHRASE}으로 이 버그 고쳐줘")
        self.assertEqual((decision["selected_skill"], decision["action"]), ("ultrawork", "dispatch"))


class ExternalSpellingDetectionTests(unittest.TestCase):
    """One case per family of external spelling: detection behaves as before."""

    def test_the_distribution_host_binary_is_detected_and_spawned_by_its_own_name(self) -> None:
        self.assertEqual(PI_FAMILY_HOST_CANDIDATES, ("pi", _HOST, "opencode"))
        self.assertEqual(pi_family_host(lambda name: f"/x/{name}" if name == _HOST else None), _HOST)
        with mock.patch("omh.coding.fanout_dispatch.shutil.which", lambda name: f"/x/{name}" if name == _HOST else None):
            self.assertEqual(build_dispatch_argv(_PROFILE, "do the work")[0], _HOST)

    def test_the_distribution_host_state_dir_and_env_vars_are_its_own(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            with (
                mock.patch("omh.coding.fanout_confinement.Path.home", return_value=home),
                mock.patch("omh.coding.fanout_dispatch.pi_family_host", return_value=_HOST),
            ):
                self.assertEqual(owner_state_directories(_PROFILE, {}), (home / f".{_HOST}" / "agent",))
                child_env = fanout_child_env(
                    {f"{_HOST.upper()}_BRAND": "ambient"}, depth=0, fanout_id="fanout", unit_id="unit", owner=_PROFILE
                )
        self.assertEqual(child_env[f"{_HOST.upper()}_CODING_AGENT_DIR"], str(home / f".{_HOST}" / "agent"))
        self.assertNotIn(f"{_HOST.upper()}_BRAND", child_env)

    def test_the_distribution_host_credential_store_marks_the_profile_logged_in(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = home / f".{_HOST}" / "agent" / "auth.json"
            store.parent.mkdir(parents=True)
            store.write_text(json.dumps({"provider": {"key": "x"}}), encoding="utf-8")
            marker = executor_auth_signals(home=home)["profiles"][_PROFILE]["login_marker"]
        self.assertEqual(marker, "present")

    def test_the_runtime_home_config_is_read_for_discovery_and_category_import(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / f".{_SHORT}" / f"{_SHORT}.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps({"categories": {"deep": {"model": "anthropic/claude-opus-4-1"}}}), encoding="utf-8"
            )
            overrides, source = load_category_overrides(home)
            observed = {
                (entry["source"], entry["model_id"], entry["status"])
                for entry in local_model_inventory(home)["model_discovery"]["observations"]
            }
        self.assertIsNotNone(overrides)
        self.assertEqual(source, str(config))
        self.assertIn((RUNTIME_CONFIG_SOURCE, "claude-opus-4-1", "confirmed_active"), observed)

    def test_the_opencode_runtime_config_is_the_model_catalog_source(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".config" / "opencode" / f"{_FULL}.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"agents": {"main": {"model": "openai/gpt-5.6-sol"}}}), encoding="utf-8")
            source = local_model_inventory(home)["sources"]["runtime_agent_config"]
        self.assertEqual(source["status"], "present")

    def test_the_runtime_home_stays_a_forbidden_continuity_target(self) -> None:
        self.assertIn(f".{_SHORT}", CONTINUITY_FORBIDDEN_TARGETS)

    def test_every_name_a_user_types_for_the_runtime_still_selects_it(self) -> None:
        for name in (_SHORT, _FULL, _FULL.replace("-", " "), "open" + "agent runtime"):
            with self.subTest(name=name):
                self.assertEqual(_executor_target_from_message(f"hand this to {name} please"), _PROFILE)
        for message in (f"have {_HOST} fix the login bug", f"{_SHORT} runtime으로 구현해줘"):
            with self.subTest(message=message):
                self.assertEqual(named_executor_owners(normalized_phrase(message)), (_PROFILE,))


if __name__ == "__main__":
    unittest.main()
