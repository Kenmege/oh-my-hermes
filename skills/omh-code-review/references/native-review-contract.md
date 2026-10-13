# Review Contracts on Hermes

Hermes handles two parts of a review itself when its tools are present: it
validates a delegated reviewer's return against a schema, and its review lane
decides what happens to a kanban card. `code-review` states each rule once and
reads the host's record rather than arguing the rule in prose. Presence is a
fact about this session: a tool is present when it is in your tool list or
tool_search finds it. Never infer it from a Hermes version.

## 1. A reviewer dispatched with `delegate_task`

When `delegate_task` is in your tool list or tool_search finds it, give the
reviewer task the findings schema from `review-dispatch.md` as its
`output_schema`, exactly as that file prints it.

What the host then does, so you do not do it a second time:

- It tells the reviewer the contract before the work starts.
- It validates the reviewer's final answer against the schema.
- On a mismatch it asks the reviewer once more, quoting what failed validation,
  and validates that second answer. There is no third.

What comes back on the task's result:

| Field | Meaning | What you do |
| --- | --- | --- |
| `schema_valid: true` | The final answer parsed and matched | Read the findings under the report rules in `review-dispatch.md` |
| `schema_valid: false` | It did not match; `schema_retries` set means the correction turn was already spent | The review is `not_observed`; `schema_errors` says what failed |
| no `schema_valid` field | No schema reached the task | The return was never checked; treat it as an unstructured report |

Three readings that go wrong:

- **Reading `status`.** A contract failure arrives as a failed task on some host
  builds and as a completed one carrying the raw, unvalidated text on others.
  `schema_valid` means the same thing on both, so it is the only field that
  answers whether the reviewer returned the contract.
- **Mining the raw text for a verdict.** When `schema_valid` is false the
  summary may still hold leads, and each is a lead you check yourself. No
  verdict was observed. Do not add a correction turn of your own: the host
  already spent the one the contract allows. Re-dispatch with something changed,
  or write the findings yourself, and say which you did.
- **Reading `schema_valid: true` as a verdict on the code.** It is a shape
  check. A host that cannot load its JSON Schema validator accepts any answer
  that parses as JSON, so confirm `verdict` and each finding's fields are there
  before you use them.

A schema the host rejects as malformed fails the whole call before any reviewer
starts. Nothing was reviewed: fix the schema and dispatch again.

## 2. Inside a kanban review worker

You are in one when the dispatcher opened this session on a review-lane card
and loaded Hermes' bundled `sdlc-review` for it. That skill owns the card's
transition. `code-review` supplies the findings the transition cites and
decides nothing about the card.

| `code-review` result | What happens to the card | Tool |
| --- | --- | --- |
| `no_ship` | Changes are requested: the `P0`/`P1` findings go in the comment, each with its `path`, `line_range`, and how it reproduces | `kanban_comment`, then `kanban_request_changes` |
| `ship` | Nothing yet: it is one input to an approval `sdlc-review` decides against what the card asked for | `kanban_complete`, when that approval is reached |
| `not_observed` | Nothing: the review is not done. Finish it, or escalate only when what is missing is a person's decision or an outside prerequisite | `kanban_block`, for the escalation only |

- Call a transition only when it is in your tool list or tool_search finds it.
  Outside a kanban review worker `code-review` never moves a card.
- `ship` alone never completes a card, and a review that did not finish never
  becomes `kanban_complete`.
- `sdlc-review` sets the lead lens by review round: the artifact read cold, then
  the work run for real, then the original contract audited. Lead with that
  lens. `review-lenses.md` adds passes under it and never replaces it.
- Do not edit the work under review. A reviewer that fixes what it found has no
  independent review left to give.

## Boundary

A schema-valid return is a checked shape, not a verified claim. A kanban
transition is the host's record that a decision was made, not evidence that the
decision was right.
