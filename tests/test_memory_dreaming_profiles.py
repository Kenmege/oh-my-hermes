"""Dreaming state belongs to one Hermes profile, and the profile chooses its mode (#2087).

On a Hermes host running `gateway.multiplex_profiles: true`, every profile's
OMH plugin can bind the same store. The counters and the brief lived at
`memory/dreaming.json` and `memory/consolidation.json` in that store while the
headroom reason read the calling profile's own MEMORY.md, so one nearly-full
profile raised a brief that every other profile was served on every turn, and
each profile's turns moved every other profile's interval. Observed: 657 of
716 user turns in one profile carried a `<memory_consolidation>` block, most
raised by another profile.

Both files now sit under `memory/profiles/<profile_slot(hermes_home)>/`, and
`plugins.entries.omh.settings.dreaming_mode` in a profile's own config.yaml
turns its brief off without editing a managed file. The isolation cases run
the provider's real `initialize`, turn and compaction hooks against two Hermes
homes sharing one OMH home. The routed upgrade cases stub
`runtime_paths.profile_is_routed`, which only a Hermes host answers True.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.paths import resolve_paths  # noqa: E402
from omh.maintenance.doctor import _memory_consolidation_check  # noqa: E402
from omh.plugin_bundle.omh import runtime_paths  # noqa: E402
from omh.plugin_bundle.omh.memory_blocks import approve_memory_block, build_memory_block, write_memory_block  # noqa: E402
from omh.plugin_bundle.omh.memory_dreaming import (  # noqa: E402
    DEFAULT_TURN_INTERVAL,
    DREAMING_HANDOFF_SCHEMA_VERSION,
    configured_dreaming_mode,
    consolidation_path,
    dreaming_state_path,
    empty_dreaming_state,
    read_dreaming_state,
    read_latest_consolidation,
    record_turn,
    write_dreaming_state,
)
from omh.plugin_bundle.omh.memory_provider import OmhMemoryProvider  # noqa: E402
from omh.plugin_bundle.omh.tools.memory_tool import omh_memory_handler  # noqa: E402
from omh.workflows.memory_migration import build_memory_migration_inventory  # noqa: E402
from omh.wrapper.contract import build_chat_interaction_payload  # noqa: E402

HERMES_DELIMITER = "\n§\n"  # Hermes ENTRY_DELIMITER (tools/memory_tool_store.py)
BRIEF = "<memory_consolidation"


def write_hermes_memory(hermes_home: Path, *entries: str) -> None:
    path = hermes_home / "memories" / "MEMORY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HERMES_DELIMITER.join(entries), encoding="utf-8")


def write_profile_config(hermes_home: Path, body: str) -> None:
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(body, encoding="utf-8")


def dreaming_mode_config(value: str) -> str:
    return f"memory:\n  provider: omh\nplugins:\n  entries:\n    omh:\n      settings:\n        dreaming_mode: {value}\n"


def write_legacy(store: Path, name: str, payload: dict) -> Path:
    path = store / "memory" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def due_brief(*reasons: str, trigger: str = "turn") -> dict:
    return {
        "schema_version": DREAMING_HANDOFF_SCHEMA_VERSION,
        "due": True,
        "trigger": trigger,
        "raised_at": "2026-10-14T00:00:00Z",
        "reasons": list(reasons),
        "requested_of_executor": [],
    }


class _SharedStore(unittest.TestCase):
    """Two Hermes profiles, A and B, both binding one OMH store."""

    def setUp(self) -> None:
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.store = self.root / "shared-omh"
        self.store.mkdir()
        self.profile_a = self.root / "hermes" / "profiles" / "a"
        self.profile_b = self.root / "hermes" / "profiles" / "b"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def start(self, hermes_home: Path, session_id: str, **kwargs: object) -> OmhMemoryProvider:
        live = OmhMemoryProvider(self.store)
        live.initialize(
            session_id,
            **{"hermes_home": str(hermes_home), "platform": "cli", "agent_context": "primary", "cwd": str(self.workspace), **kwargs},
        )
        return live

    def turns(self, live: OmhMemoryProvider, count: int, start: int = 1) -> str:
        pack = ""
        for turn in range(start, start + count):
            live.on_turn_start(turn, "keep going")
            pack = live.prefetch("keep going")
        return pack


class ProfileIsolationTests(_SharedStore):
    def test_a_full_profile_raises_a_brief_the_other_profile_is_never_served(self) -> None:
        # A's MEMORY.md is below its headroom floor; B's has room.
        write_hermes_memory(self.profile_a, "x" * 2100)
        write_hermes_memory(self.profile_b, "one short fact")
        live_a = self.start(self.profile_a, "session-a")
        brief_a = read_latest_consolidation(self.store, self.profile_a)
        self.assertTrue(brief_a and brief_a["due"])
        self.assertTrue(any(reason.startswith("headroom_below_floor") for reason in brief_a["reasons"]))
        self.assertIn(BRIEF, self.turns(live_a, 1))

        live_b = self.start(self.profile_b, "session-b")
        self.assertNotIn(BRIEF, live_b.prefetch(""))
        self.assertNotIn(BRIEF, self.turns(live_b, DEFAULT_TURN_INTERVAL - 1))
        self.assertIsNone(read_latest_consolidation(self.store, self.profile_b))
        self.assertFalse(consolidation_path(self.store, self.profile_b).exists())
        # B's own evaluation reads B's MEMORY.md, never A's.
        handoff_b = live_b.consolidation_due()
        self.assertFalse(any(reason.startswith("headroom_below_floor") for reason in handoff_b["reasons"]))
        self.assertNotEqual(consolidation_path(self.store, self.profile_a), consolidation_path(self.store, self.profile_b))

    def test_every_surface_that_reads_the_brief_reads_only_its_own_profile(self) -> None:
        write_hermes_memory(self.profile_a, "x" * 2100)
        write_hermes_memory(self.profile_b, "one short fact")
        self.start(self.profile_a, "session-a")
        self.assertTrue(read_latest_consolidation(self.store, self.profile_a)["due"])

        # The chat card notice, doctor, and the omh_memory status action.
        for profile, expect_brief in ((self.profile_a, True), (self.profile_b, False)):
            with self.subTest(profile=profile.name):
                paths = resolve_paths(self.store, profile)
                payload = build_chat_interaction_payload("hello there", source="discord", paths=paths)
                self.assertEqual("memory_consolidation_notice" in payload, expect_brief)
                check = _memory_consolidation_check(paths)
                self.assertEqual(check.message != "No memory consolidation is pending", expect_brief)
                with patch.dict(os.environ, {"OMH_HOME": str(self.store), "HERMES_HOME": str(profile)}):
                    status = json.loads(omh_memory_handler({"action": "consolidation"}))
                self.assertEqual(status.get("due") is True, expect_brief)

    def test_turn_and_compaction_counters_count_only_their_own_profile(self) -> None:
        write_hermes_memory(self.profile_a, "one short fact")
        write_hermes_memory(self.profile_b, "another short fact")
        live_a = self.start(self.profile_a, "session-a")
        live_b = self.start(self.profile_b, "session-b")
        # Interleaved, the two profiles reach the interval together; each
        # alone is one short of it.
        for turn in range(1, DEFAULT_TURN_INTERVAL):
            live_a.on_turn_start(turn, "a")
            live_b.on_turn_start(turn, "b")
        self.assertEqual(read_dreaming_state(self.store, self.profile_a)["turns_since_consolidation"], DEFAULT_TURN_INTERVAL - 1)
        self.assertEqual(read_dreaming_state(self.store, self.profile_b)["turns_since_consolidation"], DEFAULT_TURN_INTERVAL - 1)
        self.assertIsNone(read_latest_consolidation(self.store, self.profile_a))
        self.assertIsNone(read_latest_consolidation(self.store, self.profile_b))

        # A compaction in A raises A's brief and leaves B's counters alone.
        live_a.on_pre_compress([{"role": "user", "content": "hi"}])
        self.assertEqual(read_latest_consolidation(self.store, self.profile_a)["trigger"], "compaction")
        self.assertIsNone(read_latest_consolidation(self.store, self.profile_b))
        self.assertFalse(read_dreaming_state(self.store, self.profile_b)["compaction_pending"])
        self.assertEqual(read_dreaming_state(self.store, self.profile_b)["turns_since_consolidation"], DEFAULT_TURN_INTERVAL - 1)
        live_b.on_turn_start(DEFAULT_TURN_INTERVAL, "b")
        self.assertNotIn("compaction", read_latest_consolidation(self.store, self.profile_b)["trigger"])
        self.assertIn(f"turn_interval_reached:{DEFAULT_TURN_INTERVAL}/{DEFAULT_TURN_INTERVAL}", read_latest_consolidation(self.store, self.profile_b)["reasons"])


class DreamingModeSettingTests(_SharedStore):
    def test_the_mode_is_read_from_the_profile_config(self) -> None:
        cases = (
            ("bare off, a YAML false", dreaming_mode_config("off"), "off"),
            ("what hermes config set stores for off", dreaming_mode_config("false"), "off"),
            ("quoted off", dreaming_mode_config('"off"'), "off"),
            ("no", dreaming_mode_config("no"), "off"),
            ("reminder", dreaming_mode_config("reminder"), "reminder"),
            ("legacy config subtree", "plugins:\n  entries:\n    omh:\n      config:\n        dreaming_mode: off\n", "off"),
            ("blank", dreaming_mode_config(""), "reminder"),
            ("unknown word", dreaming_mode_config("sometimes"), "reminder"),
            ("unset", "plugins:\n  entries:\n    omh:\n      settings:\n        omh_home: ~/.omh\n", "reminder"),
            ("another plugin's setting", "plugins:\n  entries:\n    other:\n      settings:\n        dreaming_mode: off\n", "reminder"),
            ("tab-indented, unreadable", "plugins:\n\tentries:\n\t\tomh:\n\t\t\tsettings:\n\t\t\t\tdreaming_mode: off\n", "reminder"),
        )
        for label, body, expected in cases:
            with self.subTest(case=label):
                home = self.root / "modes" / label.replace(" ", "-").replace(",", "")
                write_profile_config(home, body)
                self.assertEqual(configured_dreaming_mode(home), expected)
        self.assertEqual(configured_dreaming_mode(self.root / "no-config-here"), "reminder")

    def test_off_writes_and_serves_no_brief_for_that_profile_only(self) -> None:
        write_profile_config(self.profile_a, dreaming_mode_config("off"))
        write_hermes_memory(self.profile_a, "x" * 2100)
        write_hermes_memory(self.profile_b, "x" * 2100)
        live_a = self.start(self.profile_a, "session-a")
        pack = self.turns(live_a, DEFAULT_TURN_INTERVAL)
        live_a.on_pre_compress([{"role": "user", "content": "hi"}])
        live_a.on_session_end()
        self.assertNotIn(BRIEF, pack)
        self.assertFalse(consolidation_path(self.store, self.profile_a).exists())
        self.assertEqual(live_a.consolidation_due()["reasons"], [])
        self.assertEqual(live_a.consolidation_due()["mode"], "off")
        # B on the same store chose nothing, so it keeps the default.
        live_b = self.start(self.profile_b, "session-b")
        self.assertTrue(read_latest_consolidation(self.store, self.profile_b)["due"])
        self.assertIn(BRIEF, self.turns(live_b, 1))

    def test_off_raises_no_replay_reminder_either(self) -> None:
        now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
        stale = approve_memory_block(build_memory_block("stale-only", "STALE_ONLY", revalidation={"deadline": "2026-07-30T11:59:59Z"}))
        write_memory_block(self.store, stale)
        write_profile_config(self.profile_a, dreaming_mode_config("off"))
        self.assertEqual(OmhMemoryProvider(self.store, hermes_home=self.profile_a).consolidation_due(now=now)["reasons"], [])
        self.assertFalse(consolidation_path(self.store, self.profile_a).exists())
        # The control: the same store and block under the default mode.
        reasons = OmhMemoryProvider(self.store, hermes_home=self.profile_b).consolidation_due(now=now)["reasons"]
        self.assertEqual(reasons, ["stale_review_required:1"])

    def test_a_brief_written_before_off_is_not_served_or_reported(self) -> None:
        path = consolidation_path(self.store, self.profile_a)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(due_brief("turn_interval_reached:5/5")), encoding="utf-8")
        self.assertTrue(read_latest_consolidation(self.store, self.profile_a)["due"])
        write_profile_config(self.profile_a, dreaming_mode_config("off"))
        self.assertIsNone(read_latest_consolidation(self.store, self.profile_a))
        live = self.start(self.profile_a, "session-a")
        self.assertNotIn(BRIEF, live.prefetch(""))
        paths = resolve_paths(self.store, self.profile_a)
        self.assertEqual(_memory_consolidation_check(paths).message, "No memory consolidation is pending")
        self.assertNotIn("memory_consolidation_notice", build_chat_interaction_payload("hello there", source="discord", paths=paths))

    def test_omh_memory_dream_reports_the_profile_mode(self) -> None:
        write_profile_config(self.profile_a, dreaming_mode_config("off"))
        for profile, expected in ((self.profile_a, "off"), (self.profile_b, "reminder")):
            with self.subTest(profile=profile.name):
                status, stdout, stderr = run_cli(["--omh-home", str(self.store), "--hermes-home", str(profile), "memory", "dream"])
                self.assertEqual(status, 0, stderr)
                self.assertEqual(json.loads(stdout)["mode"], expected)


class LegacyStateUpgradeTests(_SharedStore):
    """What happens to the home-wide files an earlier release left in the store."""

    def test_a_single_profile_host_keeps_its_pending_brief_and_counters(self) -> None:
        legacy_brief = write_legacy(self.store, "consolidation.json", due_brief("context_compaction_observed", trigger="compaction"))
        state = empty_dreaming_state()
        for _ in range(3):
            state = record_turn(state)
        legacy_state = write_legacy(self.store, "dreaming.json", state)
        write_hermes_memory(self.profile_a, "one short fact")
        live = self.start(self.profile_a, "session-a")
        self.assertFalse(legacy_brief.exists())
        self.assertFalse(legacy_state.exists())
        self.assertTrue(dreaming_state_path(self.store, self.profile_a).is_file())
        # The pack `initialize` rendered carries the brief the profile had before the upgrade.
        self.assertIn("<reason>context_compaction_observed</reason>", live._pack)
        # The adopted counters were honoured: the session-start recovery saw
        # three unconsolidated turns and raised its own brief over them.
        self.assertIn("session_ending_with_unconsolidated_turns:3", read_latest_consolidation(self.store, self.profile_a)["reasons"])

    def test_adoption_never_replaces_the_profiles_own_state(self) -> None:
        own = record_turn(empty_dreaming_state())
        write_dreaming_state(self.store, self.profile_a, own)
        legacy = empty_dreaming_state()
        for _ in range(4):
            legacy = record_turn(legacy)
        legacy_state = write_legacy(self.store, "dreaming.json", legacy)
        write_hermes_memory(self.profile_a, "one short fact")
        self.start(self.profile_a, "session-a")
        self.assertTrue(legacy_state.exists())
        self.assertIn("session_ending_with_unconsolidated_turns:1", read_latest_consolidation(self.store, self.profile_a)["reasons"])

    def test_a_routed_profile_leaves_the_shared_files_unread(self) -> None:
        legacy_brief = write_legacy(self.store, "consolidation.json", due_brief("headroom_below_floor:12<=300"))
        legacy_state = write_legacy(self.store, "dreaming.json", record_turn(empty_dreaming_state()))
        write_hermes_memory(self.profile_b, "one short fact")
        with patch.object(runtime_paths, "profile_is_routed", return_value=True):
            live = self.start(self.profile_b, "session-b")
        self.assertTrue(legacy_brief.exists())
        self.assertTrue(legacy_state.exists())
        self.assertNotIn(BRIEF, live._pack)
        self.assertNotIn(BRIEF, self.turns(live, 1))
        self.assertIsNone(read_latest_consolidation(self.store, self.profile_b))

    def test_the_migration_inventory_lists_every_brief_in_the_home(self) -> None:
        for profile in (self.profile_a, self.profile_b):
            path = consolidation_path(self.store, profile)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(due_brief("turn_interval_reached:5/5")), encoding="utf-8")
        write_legacy(self.store, "consolidation.json", due_brief("turn_interval_reached:5/5"))
        inventory = build_memory_migration_inventory(resolve_paths(self.store, self.profile_a))
        self.assertEqual(inventory["counts"]["source_bucket"].get("provider_consolidation_journal"), 3)

    def test_an_unattributable_scope_or_a_background_run_claims_nothing(self) -> None:
        legacy_brief = write_legacy(self.store, "consolidation.json", due_brief("turn_interval_reached:5/5"))
        with patch.object(runtime_paths, "profile_is_routed", side_effect=runtime_paths.UnattributableSessionError("no scope")):
            self.start(self.profile_a, "session-a")
        self.assertTrue(legacy_brief.exists())
        self.start(self.profile_b, "session-cron", agent_context="cron")
        self.assertTrue(legacy_brief.exists())


if __name__ == "__main__":
    unittest.main()
