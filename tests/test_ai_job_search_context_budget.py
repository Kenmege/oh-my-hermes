"""Measured marginal cost of the complete career feature, not general budget slack.

Baseline: 245d34d7135ba0fba18afbf7f4151d5c4f6a5f37 (integration main).
Legacy body hashes match main's producer-derived digest fixture after restoring
only the research lane cardinality; integration-1/merged-measurements.json
records the combined repeated-byte measurement. The index pin retains its
original 85673e100 measurement; the capability and registry pins are re-derived
for the ru trigger pack's sibling-row triggers. Hashes pin existing
content, not just its size, so removing a safeguard cannot finance this feature.
"""
from __future__ import annotations

import hashlib
import json
import unittest

from _local_package import load_local_package

load_local_package()
from omh.capabilities.skills import skill_capabilities
from omh.maintenance import release
from omh.plugin_bundle.omh.tools.capability_tool import standalone_skill_capability_items
from omh.skills.context_cost import _skill_context_cost_profile, _split_sections
from omh.skills.packaging import builtin_skill_reference_templates, builtin_skill_templates
from omh.skills.skill_index import full_profile_skill_index_lines


# No optional career duplication was found in the aggregate failures: registry
# growth is the intact retrieval vocabulary + role membership; repeated growth
# is only the mandatory Workflow Lane and Runtime Evidence sections. Do not
# rename headings, truncate triggers, hide the skill or remove these rails.
FEATURE = 'ai-job-search'
INDEX_DELTA_CAP = 86  # One 85-character line and its joining newline.
FULL_ROW_DELTA_CAP = 3072  # One 3070-character JSON row + comma/space.
STANDALONE_ROW_DELTA_CAP = 935  # One 933-character row + comma/space.
REGISTRY_DELTA_CAP = 244  # 227-character trigger row + 17-character role entry.
REPEATED_DELTA_CAP = 1943  # Workflow Lane 497 + Runtime Evidence 1446.


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class AiJobSearchContextBudgetTests(unittest.TestCase):
    def test_index_growth_is_only_the_new_visible_entry(self) -> None:
        lines = full_profile_skill_index_lines()
        added = [line for line in lines if line.startswith('    - omh-ai-job-search: ')]
        self.assertEqual(len(added), 1)
        legacy = [line for line in lines if line not in added]
        self.assertEqual(_digest(legacy), '08c0a6fc44b47bde948aee6ff427eb2e7312bffb627deeb31b32304747d754df')
        self.assertEqual(len('\n'.join(legacy)), 13157)
        delta = len('\n'.join(lines)) - 13157
        self.assertGreater(delta, 0)
        self.assertLessEqual(delta, INDEX_DELTA_CAP)
        # A catalog ratchet is exact; do not carry the old row's unused slack.
        self.assertEqual(release.SKILL_INDEX_CHAR_LIMIT, len('\n'.join(lines)))

    def test_capability_growth_is_one_bounded_row_without_sibling_edits(self) -> None:
        for producer, base, cap, expected_hash, limit in (
            # 522307 -> 522331 and the sibling digest re-derived: the pi-family
            # runtime rename moved four sibling rows by +6 each, and the
            # executor-runtime-readiness row's trigger count went +35 -> +34
            # when its runtime names became scoring-only (same length).
            # 522331 -> 522328 and the digest re-derived again for the
            # model-setup row refresh (-3); no other row moved.
            # 522328 -> 522372 and the digest re-derived: the ru trigger pack, after
            # its review, moves sibling rows' trigger previews and `+N more`
            # counts (itemized at FULL_CAPABILITY_SKILL_SECTION_CHAR_LIMIT); the
            # career row is unchanged.
            (skill_capabilities, 522372, FULL_ROW_DELTA_CAP,
             '29f587db13a801a914c73f909131354d31313bf8ce1437356e0b55568824f4b9',
             release.FULL_CAPABILITY_SKILL_SECTION_CHAR_LIMIT),
            (standalone_skill_capability_items, 141700, STANDALONE_ROW_DELTA_CAP,
             'a108c8d1b94c4b2ea75326b4daf340f65086472306a307682896117dc1236e20',
             release.STANDALONE_CAPABILITY_SKILL_SECTION_CHAR_LIMIT),
        ):
            with self.subTest(producer=producer.__name__):
                items = producer()
                rows = {str(row['id']): row for row in items}
                self.assertEqual(len(items), len(rows))
                self.assertIn(FEATURE, rows)
                added = rows.pop(FEATURE)
                self.assertEqual(_digest(rows), expected_hash)
                cost = len(json.dumps(added, sort_keys=True, ensure_ascii=False)) + 2
                self.assertLessEqual(cost, cap)
                measured = len(json.dumps(items, sort_keys=True, ensure_ascii=False))
                self.assertEqual(measured - base, cost)
                self.assertEqual(limit, measured)

    def test_registry_growth_preserves_all_existing_rows_and_trigger_coverage(self) -> None:
        registry = next(t.content for t in builtin_skill_reference_templates()
                        if t.skill_name == 'oh-my-hermes'
                        and t.relative_path == 'references/workflow-registry.md')
        lines = registry.splitlines(keepends=True)
        added = [line for line in lines if line.startswith('- `ai-job-search`:')]
        self.assertEqual(len(added), 1)
        self.assertEqual(len(added[0].encode()), 227)
        # Remove only the new row and the explicit operator membership token.
        legacy = [line.replace(', `ai-job-search`', '') for line in lines if line not in added]
        # Re-derived for the ru trigger pack: `running-work-board` gains
        # `что сейчас делается` and `dag-ulw` three Cyrillic triggers (+123 bytes).
        # The pack review replaces `что сейчас делается` with `что делают
        # агенты` (-4 bytes).
        self.assertEqual(_digest(legacy), '71240381ad2738a6e954085a6c1a6c0fd32c1503a90fd4fb575c370a803abd98')
        baseline = len(''.join(legacy).encode())
        self.assertEqual(baseline, 32993)
        self.assertLess(baseline, 33_100)  # The existing content keeps its old gate.
        self.assertLessEqual(len(registry.encode()) - baseline, REGISTRY_DELTA_CAP)

    def test_repeated_growth_is_only_mandatory_rails_and_existing_bodies_survive(self) -> None:
        templates = builtin_skill_templates()
        refs = builtin_skill_reference_templates()
        feature = [t for t in templates if t.name == FEATURE]
        self.assertEqual(len(feature), 1)
        sections = dict(_split_sections(feature[0].content))
        self.assertEqual(len(sections['Workflow Lane']), 497)
        self.assertEqual(len(sections['Runtime Evidence']), 1446)
        self.assertLessEqual(len(feature[0].content.encode()), 5735)
        legacy_templates = [t for t in templates if t.name != FEATURE]
        legacy_hashes = {}
        for template in legacy_templates:
            parts = _split_sections(template.content)
            # The renderer changes only research lane cardinality +25 -> +26.
            # Reconstruct that one measured, same-length membership change.
            # The vi trigger rewording also moved five lane bodies, which is
            # why this digest is no longer the clean-base one.
            restored = ''.join(body.replace('`+26 more`', '`+25 more`')
                               if heading == 'Workflow Lane' and '**Research and company ops**' in body
                               else body for heading, body in parts)
            legacy_hashes[template.name] = hashlib.sha256(restored.encode()).hexdigest()
        # Re-derived from both sources: the vi rewordings above plus
        # model-setup's nine Kimi K3 -> Kimi K3.1 Pro mentions on main.
        # Re-derived from both main sources above plus the bounded dag-ulw
        # baseline-verification guidance, then the pi-family runtime rename in
        # model-setup, maestro, ultrawork, and executor-runtime-readiness.
        # Re-derived again for the model-setup body refresh (retired provider
        # and role-slot wording); only model-setup moves versus main.
        # Re-derived for the ru trigger pack: the 49 bodies it covers gain
        # Cyrillic phrases on their routing-signal line; repeated bytes do not move.
        self.assertEqual(_digest(legacy_hashes), '3e33f8f5b26782a6bd9dc054ee30a69a54655cad08e2a2f7c0589feeba5ae189')
        legacy = _skill_context_cost_profile('full', legacy_templates, refs)
        current = _skill_context_cost_profile('full', templates, refs)
        self.assertEqual(legacy['repeated']['bytes'], 124650)  # Main's search guidance adds 305.
        delta = current['repeated']['bytes'] - legacy['repeated']['bytes']
        self.assertEqual(delta, len(sections['Workflow Lane']) + len(sections['Runtime Evidence']))
        self.assertLessEqual(delta, REPEATED_DELTA_CAP)
        self.assertLess(current['repeated']['bytes'] - REPEATED_DELTA_CAP, 126_000)
        feature_refs = [t for t in refs if t.skill_name == FEATURE]
        self.assertEqual(len(feature_refs), 6)
        self.assertLessEqual(sum(len(t.content.encode()) for t in feature_refs), 19397)
        self.assertLessEqual(max(len(t.content.encode()) for t in feature_refs), 4039)

    def test_hard_per_item_schema_and_injection_limits_do_not_move(self) -> None:
        # Career growth moves only the three catalog-total ratchets. Later
        # schema changes keep their own measured marginal caps; parked waits
        # are covered by test_per_turn_budgets, not funded from career slack.
        from omh.maintenance.drift import budget_metrics
        expected = {
            'skill_index_line_max_chars': 100,
            'plugin_tool_schema_chars': 69186,  # #2277 prepare fields, then parked DAG waits (+429).
            'pre_llm_call_context_chars_max': 5786,  # The pi-family runtime rename (+6).
            'pre_llm_call_context_fallback_chars_max': 7222,  # The same rename (+6).
            'awareness_primer_markdown_chars': 3520,
            'awareness_primer_context_chars': 1440,
            'awareness_workflow_context_chars_max': 1500,
            'role_context_chars_max': 2600,
            'full_capability_skill_item_chars_max': 9000,
            'standalone_capability_skill_item_chars_max': 2200,
            'full_profile_skill_body_chars': 1500000,
            'full_profile_skill_body_repeated_chars': 140000,
            'skill_density_filler_hits': 0,
        }
        metrics = {m.name: m for m in budget_metrics()}
        for name, limit in expected.items():
            with self.subTest(metric=name):
                self.assertEqual(metrics[name].limit, limit)
                self.assertLessEqual(metrics[name].live(), limit)


if __name__ == '__main__':
    unittest.main()
