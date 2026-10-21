# Market Research

Load this on-demand contract after the compact `market-research` body identifies the
workflow. The main body owns routing signals, shared rules, the runtime
operation, delegation fallback, and completion/recovery pointers; this file
holds the workflow-specific detail without copying those shared rails.

## Why This Exists

`market-research` turns a broad market question into bounded sizing, customer evidence, and comparable competitive analysis that a decision owner can inspect without mistaking secondary research for validation.

## First Steps

- Confirm the decision, buyer/segment, geography, period, currency/base year, units/denominator, source cutoff, budget, and stop condition before retrieval. Mark missing inputs unknown; ask the one that changes the analysis first.
- Read `references/findings-template.md` to open the ledger, then load only the needed methods: `references/market-sizing.md`, `references/customer-evidence.md`, or `references/competitive-pricing.md`. Do not manufacture empty analysis lanes for an out-of-scope question.

## Do Not Use When

- A kill, pivot, or persevere gate before a PRD is needed; use `omh-product-discovery-validation`.
- A recurring source inbox with Scout, Analyst, and Briefer operations is needed; use `omh-research-department`.
- Supplied tickets need clustering and investigation priorities; use `omh-feedback-triage`.
- A supplied spreadsheet needs cleaning, descriptive statistics, or anomaly analysis; use `omh-data-analysis`.
- A supplied academic PDF needs section-by-section explanation; use `omh-paper-learning`.
- A general business question needs an audience-ready synthesis rather than a domain model; use `omh-research-brief`.

## Examples

Good example:

- Prompt: Estimate TAM SAM SOM for US independent dental clinics with sourced counts and sensitivity, not a guessed capture percentage.
- Expected behavior: Fix scope and units, build a source/assumption ledger, calculate addressable scenarios with tools, and expose confidence limits and missing inputs.
- Why: The task needs an addressable-market model, not only a narrative brief.

Bad example:

- Prompt: Approve our startup for investment because a large total market proves demand.
- Expected behavior: Keep the size estimate separate from a discovery decision; require target-customer evidence and the decision owner's gate.
- Why: Market scale is not customer validation or permission to build.

## Completion Checklist

- The scope, units, source cutoff and stop reason are explicit, and every material finding links sources or labeled assumptions.
- Requested methods include source coverage, contrary evidence and confidence limits; unrequested lanes are marked not requested, not completed.
- Calculation receipts, unresolved retrieval gaps and customer sample limitations are attached; unknowns remain unknown.
- Decision implications name verification needs and an owner, never an automatic discovery, outreach, payment, scheduling or coding action.

## Recovery Notes

- If retrieval fails, record the source ID, attempted date, failure and uncovered period; seek an allowed alternative once within budget or leave the finding unresolved.
- If sources disagree, compare definitions, geography, units and dates before choosing; preserve both ranges when they cannot be reconciled.
- If customer evidence is absent, prepare a consent-aware evidence request and leave the JTBD hypothesis unvalidated, never generate participant answers.

## When to Use

Use when the task needs market-specific analysis of addressable demand, customer jobs and pains, or comparable competitor and pricing evidence, not merely a generic brief or source list.

Routing signals: `market-research`, `market sizing`, `tam sam som`, `bottom up market sizing`, `market size sensitivity`, `customer evidence analysis`, `jtbd evidence matrix`, `competitor pricing profiles`, `competitive pricing analysis`

## Workflow Contract

Category: `research`
Phase: `market-analysis`
Hermes role: `researcher`
Quality tier: `source-gated`
Reasoning demand: `standard`

Quality bar:

- Require source IDs, dates/cutoffs, observed/vendor-asserted/inferred labels, assumptions and units on material claims.
- Keep failed retrieval, contrary evidence, sample bias, scope mismatch and confidence limits visible; a citation alone does not verify a claim.
- Load method and output templates on demand; calculations need real tool receipts, and absent evidence cannot be synthesized into certainty.

Handoff policy:

Keep scoping, source-backed analysis, calculations, and uncertainty reporting in Hermes. Use `omh-research-brief` for the audience's final synthesis and `omh-research-department` for recurring operations. Product investment, kill/pivot/persevere, and customer-validation gates remain owned by `omh-product-discovery-validation`; market findings are not customer validation or permission to build.

Required inputs:

- decision and audience
- buyer segment and geography
- period currency units and source cutoff
- available evidence and source boundary
- research budget and stop condition

Expected outputs:

- market scope and source ledger
- TAM SAM SOM model and sensitivity
- customer evidence and JTBD matrix
- comparable competitor and pricing profiles
- decision-ready market findings

Artifact expectations:

- prepared market analysis with source IDs, assumptions, failed retrieval gaps, and tool calculation receipts when captured

Safety rules:

- Never invent figures or quotes, customer participants, market shares, traffic/revenue estimates, source IDs, or tool results. Label unsupported inputs unknown rather than borrowing a generic industry ratio.
- Outreach, participant recruitment, payments, and scheduling each require separate explicit approval; research approval is not permission to contact people, buy reports, run surveys, or create jobs.
- Keep contact details, raw recordings, private transcripts, and sensitive customer data outside durable artifacts; use consented bounded summaries and pseudonymous IDs with access-controlled source pointers.
- A prepared analysis is not retrieval, interviews, willingness to pay, customer validation, product-market fit, or an approved investment decision. No external tool, paid dataset, or MCP integration is mandatory.

Detailed procedure steps: `references/procedure.md`.
