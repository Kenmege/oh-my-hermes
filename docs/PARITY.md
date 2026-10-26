# OMH Parity Matrix

This document tracks the gap between common oh-my agent runtime capability
patterns and OMH's Hermes-native implementation. It is intentionally not a
clone list. The goal is to keep OMH competitive while preserving its core
boundary: Hermes owns conversation and workflow narration, selected executors
own observed execution, and OMH owns deterministic local contracts.

Use the live verifier for the current local install:

```sh
omh probe --parity
omh probe --parity --json
omh probe --roadmap
```

The JSON payload includes `omh_parity_matrix/v1`. `--parity` also includes
`omh_capability_gap_roadmap/v1`, which separates real product/setup gaps from
host or wrapper evidence gaps. This matters because a missing runtime
observation is usually not a missing OMH feature.

## Search Basis

The comparison is based on public oh-my agent runtime patterns across:

- skill and plugin installation surfaces
- native plugin/HUD/status context
- specialist role or profile systems
- bounded evidence probe tools
- team, swarm, worker, and worktree orchestration
- MCP/tool bridge setup
- loop/autopilot delivery flows
- doctor, update, uninstall, and release smoke commands

OMH should absorb the useful capability shape, not copy another project's
implementation or claim runtime behavior it did not observe.

## Current Matrix

| Capability axis | OMH status | OMH surface | Missing or intentionally delegated |
| --- | --- | --- | --- |
| Skill and plugin distribution | Available | Tap-compatible `skills/*/SKILL.md`, `omh setup`, managed `~/.hermes/plugins/omh` bridge, `omh plugin observe-host`, and plugin tool/hook self-observation when a host supplies bounded observation metadata. | Live plugin load/use must still be recorded or host-supplied; local install smoke is not runtime evidence. |
| Specialist role/profile system | Available | Skill catalog metadata, operating models, optional visible profile packs, wrapper role narration, plugin `omh_role`, and `[omh-role:name]` context injection. | Observed role execution still requires wrapper or runtime evidence; role context is not a hidden live agent. |
| Bounded evidence probe | Available | Plugin `omh_gather_evidence` runs shell-free allowlisted local probes such as doctor, harness validation, docs checks, unittest, compileall, and whitespace checks. | It is not a general shell, executor dispatch, PR review, CI, merge, or live Hermes plugin-load signal. |
| Team, swarm, and worker protocol | Available | `ultrawork` coordinated lanes, `omh runtime team-readiness`, runtime handoff payloads, worker-protocol guidance, wrapper sessions, and runtime observations. | Live worker launch and pane/session management still require the selected host runtime to act and record observations. |
| Worktree and project-session isolation | Available | `worktree_session_isolation/v1` plans in coding handoffs, wrapper Prepare worktree actions, `omh worktree list/bind`, executor-session status cards, loop queue metadata, and runtime observations for worktree creation. | OMH defers worktree creation to native Hermes tooling (Kanban worktree-per-task, Desktop Projects) or manual `git worktree add`, except that the opt-in `omh coding fanout dispatch` creates one isolated worktree per unit it spawns; it observes the resulting worktree, and binding recipes show how wrappers can open or attach host sessions but do not auto-launch executors or claim runtime evidence. |
| HUD, status, and session observability | Available | `omh hud`, plugin `omh_interact`/`omh_recommend`/`omh_probe`/`omh_hud`/`omh_memory`/`omh_status`, wrapper sessions, runtime runs, roadmap cards, and status cards. | Live host HUD rendering depends on Hermes/plugin support; plugin chat/session output is metadata/status, not execution evidence. |
| MCP and tool bridge | Available | `omh setup --with-mcp --mcp-host ...`, `omh mcp manifest`, `omh mcp config-recipe`, `omh mcp serve`, `omh mcp observe-host`, and `omh probe` preference/server/runtime/host-session/host-config separation. | OMH can write supported local host config entries, but it does not independently inspect live host sessions; the host or wrapper must record load/session evidence. |
| Loop and autopilot workflow | Available | `loop`, `ultrawork`, `ralplan`, loop queue ticks, verification tiers, and failure-mode status cards. | Scheduling, connector I/O, worktree creation, and subagent execution remain prepared or delegated until observed. |
| Doctor, update, uninstall, and release smoke | Available | `omh setup`, `omh doctor`, `omh update`, `omh uninstall`, `omh release checklist`, `omh release product-readiness`, `omh release install-smoke`, `omh release hermes-smoke`, `omh demo route-hint-alignment`, `omh demo context-brief-coverage`, `omh demo routing-precision`, `omh demo localized-chat-copy`, `omh demo router-fast-path`, `omh demo common-request-coverage`, and `omh demo hermes-ux-quality`. | Product readiness rolls skill content, use-case readiness, parity contracts, route-hint alignment, context-brief coverage, routing precision, localized chat copy, router fast-path quality, common request coverage, popular plugin-family coverage, Hermes UX quality, and checklist shape into one local contract card. Installer smoke can run live in an isolated temp HOME; live Hermes profile smoke still needs an explicit target Hermes profile or operator confirmation before mutation. |

## Absorbed Capability Shapes

The rows above track capability axes. This section tracks something narrower:
specific capability *shapes* seen in other oh-my agent runtimes that OMH decided
were worth having, and the OMH-native contract that now carries each one. The
rule from Search Basis applies literally here. OMH absorbs the shape and writes
its own contract for it; it does not copy another project's implementation, and
it makes no claim about how any other project behaves at runtime, because it has
not observed one.

| Absorbed shape | OMH-native contract | What the contract owns | What it still does not prove |
| --- | --- | --- | --- |
| Typed unit results instead of an exit code standing in for readiness | `fanout_unit_result/v1` | A per-unit result payload with an explicit schema version, changed paths, and check rows that each name `reported_by`, `observed_by`, and `observation_source`. Schema validity is shape, not truth. | An executor-reported passing check is reported, not observed. Only dispatcher-observed events advance the lifecycle ladder, and no result payload can promote itself to integration eligibility. |
| Descriptive executor capability metadata attached to a dispatch profile | `executor_capability_snapshot/v1` | The same observation-backed snapshot used by owner fit now also carries edit-format handling, persistent evaluation, tool re-entry, and batching facts. `host_observed` rows require bounded scope, evidence reference, and observation time; unresearched cells project as `unknown`. | Descriptive fields do not independently rank or auto-select. Only accepted-plan requirements interpreted by `coding_owner_fit/v1` affect recommendation, and a capability snapshot is never evidence that an executor performed the delegated work. |
| A live roster projected from recorded events rather than a stateful hub | Journal projection over `runtime_observation/v1` events, surfaced by `omh coding fanout status` | A deterministic read-only roster: per-unit lifecycle state, owner profile, branch and worktree path, last-event age, and evidence-reference counts, derived only from events already on the journal. | It writes no state and emits no events, and it offers no revive, steer, or kill action. Absence of an event means nothing was recorded, which is not the same as nothing having happened. |

## Acceptance Criteria

- `omh probe` remains a compact capability summary by default.
- `omh probe --parity` prints a human-readable parity section.
- `omh probe --parity --json` includes `parity_matrix.schema_version` equal to
  `omh_parity_matrix/v1`.
- `omh probe --roadmap --json` includes
  `capability_gap_roadmap.schema_version` equal to
  `omh_capability_gap_roadmap/v1`.
- The roadmap distinguishes baseline product setup gaps from evidence gaps
  such as missing wrapper metadata, MCP host-session observations, or plugin
  runtime observations.
- Roadmap `next_actions` keeps executable backend commands in `command` and
  Hermes/operator guidance in `operator_instruction`, so wrappers do not treat
  prose instructions as shell commands.
- Team/swarm worker protocol is `available` as a readiness contract, wrapper
  action set, runtime template set, and `runtime_observation/v1` ledger. Worker
  launch, pane/session management, worker results, review, CI, and merge still
  require observed host/runtime records before they are claimed. Worktree
  isolation is `available` only for explicit local Git worktree creation,
  `omh_worktree_observation/v1` records, and `worktree_executor_binding/v1`
  wrapper recipes; executor/session dispatch still needs separate runtime
  evidence. The MCP bridge axis is `available` only for the local stdio server
  and allowlisted tools; host-specific load/session use must still be recorded
  through `omh_mcp_host_session/v1` before it is claimed.
- Specialist roles are `available` only as prompt context, marker validation,
  and profile guidance. They are not hidden runtime agents.
- Plugin runtime observation is `available` only when a host or wrapper records,
  or an invoked plugin tool/hook self-records,
  `omh_plugin_host_observation/v1` with an evidence reference. It is not coding
  dispatch, implementation, review, CI, merge, or proof of unrecorded tool/hook
  calls. `native_integration_claim_ready` is narrower than historical runtime
  observation and requires an active plugin event.
- Bounded evidence probes are `available` only as explicit allowlisted local
  command results. They are not executor dispatch, implementation, review, CI,
  merge, or plugin-load evidence.
- The matrix never claims hidden worker launch, automatic worktree creation,
  unrecorded MCP host load, plugin runtime load, executor execution, review, CI,
  or merge evidence.

## Non-Goals

- No hidden tmux team launcher.
- No automatic Git worktree creator outside the opt-in
  `omh coding fanout dispatch`, which creates one isolated worktree per unit.
- No executor launch from the worktree creator or binder.
- No arbitrary MCP shell, connector runner, or auto-enabled host config.
- No Discord/Slack transport implementation.
- No Hermes core patch.
- No network calls, LLM calls, or executor dispatch from the parity verifier.
- No arbitrary shell or connector command runner from the plugin bridge.
