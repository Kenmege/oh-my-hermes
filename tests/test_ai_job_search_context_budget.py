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
        # 13157 -> 13241 and the digest re-derived: repro-audit adds its own
        # 84-character entry, which test_skill_context_cost pins.
        self.assertEqual(_digest(legacy), 'cea493d3fac9e07b6052a8196b90a74e4b74b7354df825d30914078e1264aa33')
        self.assertEqual(len('\n'.join(legacy)), 13241)
        delta = len('\n'.join(lines)) - 13241
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
            # Then the promo postmortem's visual-qa quality-bar rule grows that
            # sibling row: 522328 -> 523199.
            # 523199 -> 523243 and the digest re-derived: the ru trigger pack,
            # after its review, moves sibling rows' trigger previews and `+N more`
            # counts (itemized at FULL_CAPABILITY_SKILL_SECTION_CHAR_LIMIT); the
            # career row is unchanged.
            # 523243 -> 526233 and the digest re-derived: repro-audit adds one
            # row to each producer (+2990 full, +929 standalone).
            (skill_capabilities, 526233, FULL_ROW_DELTA_CAP,
             'b4dde327c355ec019ae372125b00b280e1cd39a59291ca3ebf28b9ffa5115393',
             release.FULL_CAPABILITY_SKILL_SECTION_CHAR_LIMIT),
            (standalone_skill_capability_items, 142629, STANDALONE_ROW_DELTA_CAP,
             'a130544228a3583763e8b987f2a2e07983015ff8f6b832cdc72c230f5f069019',
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
        # 32993 -> 33096 and the digest re-derived: repro-audit adds its
        # trigger row and its researcher membership (+103).
        self.assertEqual(_digest(legacy), 'f40d6d4ac1b0d0b7e4489815f21c382c9669136e9c89582c22bce7c8a6a1d0e2')
        baseline = len(''.join(legacy).encode())
        self.assertEqual(baseline, 33096)
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
            # repro-audit's own lane membership makes it +27 -> +26.
            restored = ''.join(body.replace('`+27 more`', '`+26 more`')
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
        # Then the promo postmortem in content-operator, design-quality-gate,
        # show-reel, and visual-qa.
        # Re-derived for the ru trigger pack: the 49 bodies it covers gain
        # Cyrillic phrases on their routing-signal line; repeated bytes do not move.
        # Re-derived again for repro-audit: its own body joins the legacy set
        # (and again for its review's confirmation-wording fix).
        self.assertEqual(_digest(legacy_hashes), '055021f66e10653f53baa63fa5bd7e01dd84882116802e1f7c32cdcf104a79f3')
        legacy = _skill_context_cost_profile('full', legacy_templates, refs)
        current = _skill_context_cost_profile('full', templates, refs)
        # Main's search guidance adds 305; repro-audit's mandatory rails 1,943.
        self.assertEqual(legacy['repeated']['bytes'], 126593)
        delta = current['repeated']['bytes'] - legacy['repeated']['bytes']
        self.assertEqual(delta, len(sections['Workflow Lane']) + len(sections['Runtime Evidence']))
        self.assertLessEqual(delta, REPEATED_DELTA_CAP)
        # 126,000 -> 127,943: repro-audit's 1,943 rails, bounded by its own test.
        self.assertLess(current['repeated']['bytes'] - REPEATED_DELTA_CAP, 127_943)
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
