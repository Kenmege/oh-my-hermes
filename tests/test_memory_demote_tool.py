"""The model's own L1 -> L2 move: `omh_memory(action="demote")` and the brief that asks for it.

Demotion existed only as an operator path (`omh memory demote --stage`, review,
then a person asking Hermes to edit), and it never ran on the machine whose
MEMORY.md sat nearly full. These pin the model-driven path:

- the tool copies the entry exactly as Hermes holds it, under the staging
  workflow's guards, and only an approved, intact, durable user-global copy
  carries the native remove (`remove_from_hermes`); token similarity never does;
- entries are parsed the way Hermes parses them (full "\\n§\\n" delimiter, BOM
  stripped), so a fragment is never demoted in place of a whole entry;
- a nearly-full brief names entries by sha256 prefix only on disk, and the
  rendered section resolves short previews from the live Hermes file;
- a Hermes `remove` after the move retires the brief once headroom is back.

Every test that could remove something compares the Hermes file's raw bytes
before and after: OMH never writes it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()
from omh.paths import resolve_paths
from omh.plugin_bundle.omh.hermes_memory import HERMES_MEMORY_DELIMITER, HermesMemoryFile
from omh.plugin_bundle.omh.memory_admission import capture_project_memory_candidate
from omh.plugin_bundle.omh.memory_dreaming import read_latest_consolidation
from omh.plugin_bundle.omh.memory_provider import CONSOLIDATION_RENDER_BUDGET_CHARS
from omh.plugin_bundle.omh.tools.memory_tool import omh_memory_handler
from omh.profiles.setup import write_setup_profile
from omh.workflows.memory import reject_project_memory_candidate
from project_identity_fixture import seed_project_identity
from test_memory_prefetch_canonical import provider
from test_memory_provider_hermes_order import hermes_turn

FACT = "The operator deploys from the release branch every Friday afternoon, never from main."
NEGATION = "The operator never deploys from the release branch on Friday afternoon, only from main."
# Distinct vocabularies so no entry explains another and no duplicate cluster forms.
_TOPICS = (
    "alpha ledger reconciles invoices nightly through the batch scheduler on the reporting host",
    "bravo cluster rotates its build cache weekly and keeps three generations of artifacts around",
    "charlie dashboard renders latency percentiles from the collector with a five minute window",
    "delta mirror syncs package archives hourly from upstream and verifies every checksum",
    "echo pipeline compresses raw sensor frames before upload to the archive bucket overnight",
    "foxtrot gateway terminates inbound webhooks and queues them for the worker pool",
    "golf runbook says restart the indexer before reindexing large tenants on weekends",
    "hotel inventory service caches product images at the edge for twelve hours",
    "india scheduler pins long jobs to the dedicated pool during business hours only",
    "juliet notebook server autosaves drafts every minute into the shared research volume",
)


def entry(index: int) -> str:
    # About 200 characters each, so ten fill MEMORY.md past its headroom floor.
    return f"Fact {index}: the {_TOPICS[index]}, as agreed with the platform group in March 2026 and noted for onboarding and quarterly reviews."


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_memory(hermes_home: Path, label: str, *entries: str) -> Path:
    path = hermes_home / "memories" / label
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HERMES_MEMORY_DELIMITER.join(entries), encoding="utf-8")
    return path


def hermes_selects(entries: list[str], old_text: str) -> str | None:
    """The entry Hermes' remove would select for ``old_text``, or None when it refuses.

    Replicates `_find_unique_match` / `_substring_matches` in
    hermes-agent `tools/memory_tool_store.py` (not imported: that module pulls
    in the Hermes runtime): an entry equal to the stripped old_text wins
    outright; otherwise every entry containing it matches, and more than one
    distinct match is refused as ambiguous. The typography-folded fallback only
    runs when no byte-exact substring matched, which never applies to an
    old_text taken verbatim from an entry.
    """
    needle = old_text.strip()
    exact = [item for item in entries if item == needle]
    matches = exact or [item for item in entries if needle in item]
    if len(set(matches)) != 1:
        return None
    return matches[0]


class _Home(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        seed_project_identity(self.root)
        self.hermes = self.root / ".hermes"
        self.hermes.mkdir()
        self.store = self.root / ".omh"
        self.paths = resolve_paths(self.store, self.hermes)
        self.enterContext(patch.dict(os.environ, {"OMH_HOME": str(self.store), "HERMES_HOME": str(self.hermes)}))

    def demote(self, digest: str) -> dict:
        return json.loads(omh_memory_handler({"action": "demote", "entry_sha256": digest}, cwd=str(self.root)))

    def store_files(self) -> list[Path]:
        memory = self.store / "memory"
        return sorted(path for path in memory.rglob("*.json")) if memory.exists() else []

    def seed_record(self, summary: str, *, scope_kind: str = "user-global", retention: str = "durable", source_ref: str = "") -> str:
        payload = capture_project_memory_candidate(
            self.store,
            summary,
            scope_kind=scope_kind,
            scope_ref="demo" if scope_kind == "project" else None,
            retention_class=retention,
            source_ref=source_ref,
        )
        self.assertTrue(payload["auto_approved"], payload)
        return str(payload["record"]["record_id"])

    def assert_removal_is_safe(self, result: dict, entries: list[str], expected: str) -> None:
        """`old_text` reaches exactly the demoted entry under Hermes' own matching."""
        self.assertEqual(hermes_selects(entries, result["remove_from_hermes"]["old_text"]), expected)


class DemoteToolTests(_Home):
    def test_demote_stores_the_exact_entry_and_hands_back_the_native_remove(self) -> None:
        path = write_memory(self.hermes, "MEMORY.md", FACT, entry(0))
        before = path.read_bytes()
        result = self.demote(sha(FACT)[:12])
        self.assertEqual(result["status"], "demoted", result)
        self.assertEqual((result["action"], result["source_backend"]), ("demote", "bundle_memory"))
        record = json.loads((self.store / "memory" / "records" / f"{result['record_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(record["summary"], FACT)
        self.assertEqual(record["scope"]["kind"], "user-global")
        self.assertEqual(record["retention"]["class"], "durable")
        self.assertEqual(record["source"], "hermes_demotion")
        self.assertEqual(record["source_ref"], f"hermes:MEMORY.md#{sha(FACT)[:16]}")
        remove = result["remove_from_hermes"]
        self.assertEqual(remove["target"], "memory")
        self.assertLessEqual(len(remove["old_text"]), 60)
        self.assert_removal_is_safe(result, [FACT, entry(0)], FACT)
        self.assertTrue(result["display_line"].startswith("📦 OMH moved to long-term memory: "))
        self.assertIn("Confirm", result["next_action"])
        self.assertEqual(path.read_bytes(), before)

        again = self.demote(sha(FACT))
        self.assertEqual(again["status"], "already_covered", again)
        self.assertEqual(again["record_id"], result["record_id"])
        self.assertEqual(again["remove_from_hermes"], remove)
        # Nothing moved on the second call, so the suggested wording says so.
        self.assertTrue(again["display_line"].startswith("📦 OMH already holds: "))
        self.assertEqual(path.read_bytes(), before)

    def test_user_md_maps_to_the_user_target(self) -> None:
        write_memory(self.hermes, "USER.md", FACT)
        result = self.demote(sha(FACT)[:16])
        self.assertEqual(result["status"], "demoted", result)
        self.assertEqual(result["remove_from_hermes"]["target"], "user")
        self.assertEqual(result["file"], "USER.md")

    def test_sensitive_and_overlong_entries_are_refused_before_anything_is_written(self) -> None:
        sensitive = "The staging password is kept in the team vault under ops."
        overlong = ("The garden notes describe watering the tomato beds at dawn. " * 5).strip()
        path = write_memory(self.hermes, "MEMORY.md", sensitive, overlong)
        before = path.read_bytes()
        for text, reason in ((sensitive, "redacted_cannot_demote"), (overlong, "summary_bound_exceeded")):
            with self.subTest(reason=reason):
                result = self.demote(sha(text)[:12])
                self.assertEqual((result["status"], result["reason"]), ("refused", reason), result)
                self.assertNotIn("remove_from_hermes", result)
                self.assertNotIn("display_line", result)
        self.assertEqual(self.store_files(), [])
        self.assertEqual(path.read_bytes(), before)

    def test_pending_review_keeps_the_native_entry_and_a_rejection_stands(self) -> None:
        write_setup_profile(self.paths, [], memory_mode="review-first")
        write_memory(self.hermes, "MEMORY.md", FACT)
        result = self.demote(sha(FACT)[:12])
        self.assertEqual(result["status"], "pending_review", result)
        self.assertNotIn("remove_from_hermes", result)
        self.assertNotIn("display_line", result)
        again = self.demote(sha(FACT)[:12])
        self.assertEqual((again["status"], again["candidate_id"]), ("pending_review", result["candidate_id"]))

        reject_project_memory_candidate(self.paths, result["candidate_id"])
        rejected = self.demote(sha(FACT)[:12])
        self.assertEqual((rejected["status"], rejected["reason"]), ("refused", "previously_rejected"), rejected)

    def test_a_rejection_outranks_an_exact_durable_copy(self) -> None:
        write_setup_profile(self.paths, [], memory_mode="review-first")
        write_memory(self.hermes, "MEMORY.md", FACT)
        reject_project_memory_candidate(self.paths, self.demote(sha(FACT)[:12])["candidate_id"])
        write_setup_profile(self.paths, [], memory_mode="auto-safe")
        self.seed_record(FACT)
        result = self.demote(sha(FACT)[:12])
        self.assertEqual((result["status"], result["reason"]), ("refused", "previously_rejected"), result)
        self.assertNotIn("remove_from_hermes", result)

    def _similar_record_case(self, scope_kind: str, retention: str) -> None:
        # A negation shares nearly every token; it is captured past, not trusted.
        path = write_memory(self.hermes, "USER.md", FACT)
        before = path.read_bytes()
        negation = self.seed_record(NEGATION, scope_kind=scope_kind, retention=retention)
        result = self.demote(sha(FACT)[:12])
        self.assertEqual(result["status"], "demoted", result)
        self.assertNotEqual(result["record_id"], negation)
        record = json.loads((self.store / "memory" / "records" / f"{result['record_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(record["summary"], FACT)
        self.assertEqual(path.read_bytes(), before)

    def test_a_similar_project_volatile_record_never_authorizes_removal(self) -> None:
        # The reproduced data-loss case from review.
        self._similar_record_case("project", "volatile")

    def test_a_similar_durable_user_global_record_never_authorizes_removal(self) -> None:
        self._similar_record_case("user-global", "durable")

    def test_an_exact_project_scoped_copy_does_not_count_as_held(self) -> None:
        write_memory(self.hermes, "MEMORY.md", FACT)
        project_record = self.seed_record(FACT, scope_kind="project")
        result = self.demote(sha(FACT)[:12])
        self.assertEqual(result["status"], "demoted", result)
        self.assertNotEqual(result["record_id"], project_record)

    def test_an_exact_volatile_copy_refuses_rather_than_removing(self) -> None:
        path = write_memory(self.hermes, "MEMORY.md", FACT)
        before = path.read_bytes()
        volatile = self.seed_record(FACT, retention="volatile")
        result = self.demote(sha(FACT)[:12])
        self.assertEqual((result["status"], result["reason"], result["record_id"]), ("refused", "held_record_not_removable", volatile), result)
        self.assertNotIn("remove_from_hermes", result)
        self.assertEqual(path.read_bytes(), before)

    def test_an_altered_copy_under_the_origin_ref_is_not_intact_on_every_call(self) -> None:
        write_memory(self.hermes, "MEMORY.md", FACT)
        self.seed_record(FACT + " Altered.", source_ref=f"hermes:MEMORY.md#{sha(FACT)[:16]}")
        for _attempt in range(2):
            result = self.demote(sha(FACT)[:12])
            self.assertEqual((result["status"], result["reason"]), ("refused", "content_not_intact"), result)
            self.assertNotIn("remove_from_hermes", result)

    def test_a_bare_section_sign_stays_inside_the_entry_it_belongs_to(self) -> None:
        contract = "Contract: see § 4.2 for renewal terms; renews June 1"
        path = write_memory(self.hermes, "MEMORY.md", contract, "Other fact here")
        before = path.read_bytes()
        fragment = self.demote(sha("Contract: see")[:12])
        self.assertEqual((fragment["status"], fragment["reason"]), ("refused", "entry_not_found"), fragment)
        result = self.demote(sha(contract)[:12])
        self.assertEqual(result["status"], "demoted", result)
        self.assert_removal_is_safe(result, [contract, "Other fact here"], contract)
        self.assertEqual(path.read_bytes(), before)

    def test_a_byte_order_mark_does_not_hide_the_first_entry(self) -> None:
        path = self.hermes / "memories" / "MEMORY.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xef\xbb\xbf" + FACT.encode("utf-8"))
        result = self.demote(sha(FACT)[:12])
        self.assertEqual(result["status"], "demoted", result)

    def test_a_reading_that_disagrees_with_hermes_parsing_is_refused(self) -> None:
        path = write_memory(self.hermes, "MEMORY.md", FACT)
        fragment = FACT[:30]
        drifted = HermesMemoryFile("MEMORY.md", path, True, len(fragment), 2200, (fragment,), 0.0)
        with patch("omh.plugin_bundle.omh.tools.memory_tool.read_hermes_memory", return_value=(drifted,)):
            result = self.demote(sha(fragment)[:12])
        self.assertEqual((result["status"], result["reason"]), ("refused", "entry_not_whole"), result)
        self.assertEqual(self.store_files(), [])

    def test_missing_duplicated_and_malformed_identifiers_are_refused(self) -> None:
        # The same text in both files has one digest: no prefix separates them.
        write_memory(self.hermes, "MEMORY.md", FACT)
        write_memory(self.hermes, "USER.md", FACT)
        cases = (
            ("0" * 12, "entry_not_found"),
            (sha(FACT)[:12], "entry_in_both_files"),
            (sha(FACT), "entry_in_both_files"),
            ("abc", "entry_sha256_invalid"),
            ("z" * 12, "entry_sha256_invalid"),
        )
        for digest, reason in cases:
            with self.subTest(reason=reason, digest=digest):
                result = self.demote(digest)
                self.assertEqual((result["status"], result["reason"]), ("refused", reason), result)
                self.assertNotIn("remove_from_hermes", result)
                self.assertNotIn("longer", result["next_action"])
        self.assertEqual(self.store_files(), [])

    def test_a_case_folded_copy_never_authorizes_removal(self) -> None:
        path = write_memory(self.hermes, "MEMORY.md", FACT)
        before = path.read_bytes()
        folded = self.seed_record(FACT.lower())
        result = self.demote(sha(FACT)[:12])
        self.assertEqual((result["status"], result["reason"], result["record_id"]), ("refused", "held_record_not_removable", folded), result)
        self.assertNotIn("remove_from_hermes", result)
        self.assertEqual(path.read_bytes(), before)

    def test_a_store_fault_is_an_error_without_the_native_remove(self) -> None:
        write_memory(self.hermes, "MEMORY.md", FACT)
        with patch("omh.plugin_bundle.omh.tools.memory_tool.capture_project_memory_candidate", side_effect=OSError("disk")):
            result = self.demote(sha(FACT)[:12])
        self.assertEqual((result["status"], result["reason"]), ("error", "OSError"), result)
        self.assertNotIn("remove_from_hermes", result)
        self.assertNotIn("display_line", result)

    def test_old_text_selects_only_this_entry_when_a_sibling_shares_its_opening(self) -> None:
        sibling = FACT[:70] + " except during the December freeze window."
        write_memory(self.hermes, "MEMORY.md", FACT, sibling)
        result = self.demote(sha(FACT)[:12])
        self.assertLessEqual(len(result["remove_from_hermes"]["old_text"]), 60)
        self.assert_removal_is_safe(result, [FACT, sibling], FACT)

    def test_an_entry_inside_a_sibling_falls_back_to_its_whole_text(self) -> None:
        # Every 60-char window of FACT is inside the sibling, so only the whole
        # entry -- an exact match, which Hermes ranks first -- selects it.
        sibling = FACT + " Confirmed by the release manager."
        write_memory(self.hermes, "MEMORY.md", FACT, sibling)
        result = self.demote(sha(FACT)[:12])
        self.assertEqual(result["remove_from_hermes"]["old_text"], FACT)
        self.assert_removal_is_safe(result, [FACT, sibling], FACT)

    def test_display_line_rides_a_remembered_capture_and_not_a_refusal(self) -> None:
        remembered = json.loads(omh_memory_handler({"action": "capture", "summary": FACT, "scope": "user"}, cwd=str(self.root)))
        self.assertEqual(remembered["status"], "remembered", remembered)
        self.assertTrue(remembered["display_line"].startswith("📌 OMH remembered: "))
        self.assertLessEqual(len(remembered["display_line"]), len("📌 OMH remembered: ") + 81)
        refused = json.loads(omh_memory_handler({"action": "capture", "summary": ""}, cwd=str(self.root)))
        self.assertEqual(refused["status"], "refused")
        self.assertNotIn("display_line", refused)


class DemotionBriefTests(_Home):
    ENTRIES = tuple(entry(index) for index in range(len(_TOPICS)))

    def setUp(self) -> None:
        super().setUp()
        self.memory_file = write_memory(self.hermes, "MEMORY.md", *self.ENTRIES)

    def test_a_nearly_full_brief_serves_demote_rows_with_live_previews(self) -> None:
        live = provider(self.root, cwd=self.root)
        pack = hermes_turn(live, 1, "hello")
        self.assertIn("<memory_consolidation", pack)
        self.assertEqual(pack.count("<demote "), 3, pack)
        self.assertNotIn("<covered", pack)
        self.assertIn('omh_memory(action="demote"', pack)
        self.assertNotIn("removed directly", pack)
        brief = read_latest_consolidation(self.store)
        rows = brief["demotion_rows"]
        self.assertEqual([row["kind"] for row in rows], ["demote"] * 3)
        for row in rows:
            text = next(item for item in self.ENTRIES if sha(item).startswith(row["sha12"]))
            preview = " ".join(text.split())[:40]
            self.assertIn(f'<demote file="MEMORY.md" entry="{row["sha12"]}" chars="{len(text)}">{preview}…</demote>', pack)
        section = pack[pack.index("<memory_consolidation") :]
        self.assertLessEqual(len(section), CONSOLIDATION_RENDER_BUDGET_CHARS)
        # Entry text never reaches disk: neither the 0644 brief nor the journal.
        for name in ("consolidation.json", "consolidation.jsonl"):
            on_disk = (self.store / "memory" / name).read_text(encoding="utf-8")
            for text in self.ENTRIES:
                self.assertNotIn(text[:30], on_disk, name)
                self.assertNotIn(text[-30:], on_disk, name)
        journal = (self.store / "memory" / "consolidation.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("sha12", journal)

    def test_a_section_whose_rows_all_vanished_falls_back_to_the_trim_instruction(self) -> None:
        live = provider(self.root, cwd=self.root)
        rows = read_latest_consolidation(self.store)["demotion_rows"]
        gone = {item for item in self.ENTRIES if any(sha(item).startswith(row["sha12"]) for row in rows)}
        write_memory(self.hermes, "MEMORY.md", *(item for item in self.ENTRIES if item not in gone))
        pack = hermes_turn(live, 1, "hello")
        section = pack[pack.index("<memory_consolidation") :]
        self.assertNotIn("<demote", section)
        self.assertNotIn("demote row", section)
        self.assertIn("trim or merge", section)

    def test_an_entry_also_in_user_md_is_not_offered(self) -> None:
        largest = max(self.ENTRIES, key=len)
        write_memory(self.hermes, "USER.md", largest)
        provider(self.root, cwd=self.root)
        rows = read_latest_consolidation(self.store)["demotion_rows"]
        self.assertEqual(len(rows), 3)
        self.assertNotIn(sha(largest)[:12], [row["sha12"] for row in rows])

    def test_a_row_whose_entry_is_gone_is_dropped_at_render(self) -> None:
        live = provider(self.root, cwd=self.root)
        rows = read_latest_consolidation(self.store)["demotion_rows"]
        gone = next(item for item in self.ENTRIES if sha(item).startswith(rows[0]["sha12"]))
        write_memory(self.hermes, "MEMORY.md", *(item for item in self.ENTRIES if item != gone))
        pack = hermes_turn(live, 1, "hello")
        self.assertNotIn(rows[0]["sha12"], pack)
        self.assertIn(rows[1]["sha12"], pack)

    def test_a_brief_without_headroom_pressure_carries_no_demote_rows(self) -> None:
        write_memory(self.hermes, "MEMORY.md", self.ENTRIES[0])
        live = provider(self.root, cwd=self.root)
        live.on_pre_compress([{"role": "user", "content": "x"}])
        brief = read_latest_consolidation(self.store)
        self.assertTrue(brief["due"])
        self.assertFalse(any(reason.startswith("headroom_below_floor") for reason in brief["reasons"]))
        self.assertEqual(brief["demotion_rows"], [])
        self.assertFalse(any("demote" in item for item in brief["requested_of_executor"]))
        pack = hermes_turn(live, 1, "hello")
        self.assertNotIn("<demote", pack)

    def test_headroom_pressure_with_nothing_demotable_keeps_the_trim_instruction(self) -> None:
        overlong = tuple(("Entry %d keeps a long running note about the garden tomato beds at dawn. " % i) * 4 for i in range(7))
        write_memory(self.hermes, "MEMORY.md", *(item.strip() for item in overlong))
        provider(self.root, cwd=self.root)
        brief = read_latest_consolidation(self.store)
        self.assertTrue(any(reason.startswith("headroom_below_floor") for reason in brief["reasons"]), brief["reasons"])
        self.assertEqual(brief["demotion_rows"], [])
        self.assertTrue(any("trim or merge" in item for item in brief["requested_of_executor"]))
        self.assertFalse(any("demote" in item for item in brief["requested_of_executor"]))

    def test_a_row_fault_costs_the_rows_not_the_brief(self) -> None:
        with patch("omh.plugin_bundle.omh.memory_provider.read_project_memory_candidates", side_effect=KeyError("x")):
            live = provider(self.root, cwd=self.root)
        brief = read_latest_consolidation(self.store)
        self.assertTrue(brief["due"])
        self.assertEqual(brief["demotion_rows"], [])
        self.assertIn({"op": "demotion_rows", "path": "MEMORY.md", "error": "KeyError"}, live._write_failures)

    def test_a_remove_after_demotion_retires_the_brief_once_headroom_recovers(self) -> None:
        live = provider(self.root, cwd=self.root)
        rows = read_latest_consolidation(self.store)["demotion_rows"]
        remaining = list(self.ENTRIES)
        for row in rows:
            result = self.demote(row["sha12"])
            self.assertEqual(result["status"], "demoted", result)
            # What Hermes' own memory tool does with remove_from_hermes.
            removed = hermes_selects(remaining, result["remove_from_hermes"]["old_text"])
            self.assertIsNotNone(removed)
            remaining.remove(removed)
            write_memory(self.hermes, "MEMORY.md", *remaining)
            live.on_memory_write("remove", "memory", "", {"old_text": result["remove_from_hermes"]["old_text"], "previous_content": removed})
            journal = (self.store / "memory" / "write_journal.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertIs(json.loads(journal[-1])["removed_entry_demoted"], True)
        self.assertEqual(len(remaining), len(self.ENTRIES) - 3)
        self.assertFalse(read_latest_consolidation(self.store)["due"])
        self.assertNotIn("<memory_consolidation", hermes_turn(live, 1, "hello"))

    def test_removing_an_entry_omh_does_not_hold_is_journalled_as_such(self) -> None:
        live = provider(self.root, cwd=self.root)
        live.on_memory_write("remove", "memory", "", {"previous_content": self.ENTRIES[0]})
        journal = (self.store / "memory" / "write_journal.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertIs(json.loads(journal[-1])["removed_entry_demoted"], False)
        live.on_memory_write("remove", "memory", "", {})
        self.assertNotIn("removed_entry_demoted", json.loads((self.store / "memory" / "write_journal.jsonl").read_text(encoding="utf-8").splitlines()[-1]))


if __name__ == "__main__":
    unittest.main()
