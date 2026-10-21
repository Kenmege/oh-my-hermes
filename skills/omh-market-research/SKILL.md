---
name: "omh-market-research"
description: "[omh] Market sizing or customer/competitive analysis: investigate a scoped market with TAM/SAM/SOM sensitivity, attributed customer evidence, and comparable competitor/pricing profiles before a decision brief. Use when the user says: market-research, market sizing, tam sam som, bottom up market sizing, market size sensitivity, customer evidence analysis, jtbd evidence matrix, competitor pricing profiles."
license: MIT
author: "oh-my-hermes maintainers"
metadata:
  hermes:
    tags: [workflow, oh-my-hermes, research]
    category: research
    phase: market-analysis
    role: researcher
    quality_tier: source-gated
    related_skills: [omh-product-discovery-validation, omh-research-department, omh-feedback-triage, omh-data-analysis, omh-paper-learning, omh-research-brief]
---

# Market Research

Scope a market question, then analyze only the requested sizing, customer, or
competitive/pricing evidence. Required: decision/audience, buyer and geography,
period/currency/units/cutoff, source boundaries, budget and stop condition.
Output: source ledger, requested method worksheets, and decision-ready findings.

## Why This Exists

`market-research` turns a broad market question into bounded sizing, customer evidence, and comparable competitive analysis that a decision owner can inspect without mistaking secondary research for validation.

## First Steps

- Confirm the decision, buyer/segment, geography, period, currency/base year, units/denominator, source cutoff, budget, and stop condition before retrieval. Mark missing inputs unknown; ask the one that changes the analysis first.
- Read `references/findings-template.md` to open the ledger, then load only the needed methods: `references/market-sizing.md`, `references/customer-evidence.md`, or `references/competitive-pricing.md`. Do not manufacture empty analysis lanes for an out-of-scope question.

Load `references/full-contract.md` before analysis and
`references/procedure.md` for the ordered checks. These are working methods,
not a core OMH execution command. Keep unknowns unknown and use tools for math.
Market findings never establish customer validation or authorize investment.
Outreach, payments and scheduling need separate explicit approval.

## When to Use

Use when the task needs market-specific analysis of addressable demand, customer jobs and pains, or comparable competitor and pricing evidence, not merely a generic brief or source list.

Strong routing signals: `market-research`, `market sizing`, `tam sam som`, `bottom up market sizing`, `market size sensitivity`, `customer evidence analysis`, `jtbd evidence matrix`, `competitor pricing profiles`, `competitive pricing analysis`

## Catalog Metadata

Category: `research`
Phase: `market-analysis`
Hermes role: `researcher`
Quality tier: `source-gated`

## Completion Checklist

- The scope, units, source cutoff and stop reason are explicit, and every material finding links sources or labeled assumptions.
- Requested methods include source coverage, contrary evidence and confidence limits; unrequested lanes are marked not requested, not completed.
- Calculation receipts, unresolved retrieval gaps and customer sample limitations are attached; unknowns remain unknown.
- Decision implications name verification needs and an owner, never an automatic discovery, outreach, payment, scheduling or coding action.

## Recovery Notes

- If retrieval fails, record the source ID, attempted date, failure and uncovered period; seek an allowed alternative once within budget or leave the finding unresolved.
- If sources disagree, compare definitions, geography, units and dates before choosing; preserve both ranges when they cannot be reconciled.
- If customer evidence is absent, prepare a consent-aware evidence request and leave the JTBD hypothesis unvalidated, never generate participant answers.

## Workflow Lane

- Current lane: **Research and company ops** (`product-docs`, `source-finder`, `web-research`, `research`, `model-optimization`, `inference-serving`, `model-finetuning`, `research-brief`, `+22 more`) - research, signals, ops, and briefings.
- If intent belongs to another lane, hand back to `oh-my-hermes` or name the adjacent workflow.
- Shared product, routing, compatibility, and evidence rules: `skill_view(name='omh-routing', file_path='references/skill-common-rail.md')`.

## Runtime Evidence

Preferred harness for this skill: `business-research`.

```sh
omh runtime record --skill market-research --harness business-research --status started
```

Record observed delegation results; otherwise return `not_available` or `not_observed`.
Prepared OMH routing is not execution, review, CI, merge-readiness, or merge evidence.
- Treat wrapper memory/context summaries as advisory local context, not proof of opaque Hermes memory reads or changes.
Preserve workflow intent and stop conditions; verify before claiming completion.
Reply in the user's own words and the host's own voice: its SOUL.md persona owns reply language, tone, speech level, and sentence endings, progress updates included (where it sets no language, use the one the user wrote in), and OMH shapes structure and content only; OMH's record terms (surface, lane, wrapper, handoff, evidence boundary, not_observed) stay in records and tool calls, never in the sentence the user reads unless they ask about one; and when a stop condition or a decision the user owns ends the turn, offer the next action as a question rather than declaring what will not be done.

Use Hermes-native subagent/delegation features when available: native subagents -> Hermes delegation when available, otherwise sequential lanes.

Shared product, compatibility, topology, memory, harness, and execution rules: `skill_view(name='omh-routing', file_path='references/skill-common-rail.md')`. Load it when applicable; otherwise name an unavailable capability.
