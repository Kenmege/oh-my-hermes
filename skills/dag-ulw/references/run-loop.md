# dag-ulw run loop

## The calls, in order

1. `omh_dag action=define run=<id> name=<short name> goal=<criteria> nodes=[...]`. A refusal names what to fix:
   a cycle, an unknown or self dependency, two nodes writing one path with no edge between them, more than
   48 nodes, an empty graph. Lint is advice: missing prompt markers, no verification node, a producer the
   verification node does not depend on, every node `deep`, more than one `ultrabrain`.
2. The result carries `calls`. Each call is one category: run its `route` with `omh_delegate_route`
   (`set`, or `fallback` after a provider error), then one `delegate_task` with its `tasks` unchanged. The
   goal starts with `[dag:<run>/<node>]`; that marker is how OMH ties the child to its node.
3. `calls` never exceeds the free background slots (`slots.free`): a call past them would run blocking.
   `held_for_slots` lists ready nodes left for the next turn.
4. End the turn with one status line. Each completion re-enters as a new turn.
5. On a completion, read the node's handoff file, then `omh_dag action=settle node=<id> outcome=<...>`.
   `accepted` is refused while the handoff file is missing or when Hermes reported the child failed.
   The result carries the next `calls`; dispatch them in the same turn.
6. `omh_dag action=status` at any time; it survives compaction.

## Goal loop

Verify, repair, extend, verify. The verification node runs the real command. When it, or any node, fails:

- read why, then `omh_dag action=repair node=<failed id> nodes=[...]`. Repair nodes may depend only on
  accepted nodes or on each other. The failed node counts as done once its latest repair group is all
  accepted.
- a node gets at most two repair cycles. After that the run state is `stopped`: report what is still
  open, and do not repair again.

When every node passed (`state: done`), check each node against its SCOPE and the goal criteria. When
work is missing:

- `omh_dag action=extend node=<the node whose check found the gap> nodes=[...]` adds the missing work to
  the same run, with its own verification node. New nodes may depend on the run's accepted nodes and on
  each other; the whole run passes the same refusals as define (cycles, unknown or self edges, a write
  scope shared with any node of the run without an edge, 48 nodes in all).
- a run gets at most two extend rounds (`extend_rounds_left`). After that, report what is still open.

To stop early, `omh_dag action=cancel`; children already running finish on their own unless you stop
them with `delegate_task action=stop`.

## Messengers

On Discord, Slack, Telegram, Mattermost and Matrix every completion turn is a bot post.

- Keep `delegation.independent_completions` off (Hermes' default): one completion per call, not per node.
- Dispatch one call per layer and category, as `ready` groups them.
- On an intermediate completion turn, reply with the run's `status_line` only. After a repair or an
  extend it names the added nodes once (`+1 added (repair: Docs body)`); copy it as it is.
- OMH appends the start block, a count line when counts change, and the end block itself; never paste
  the graph into a reply.

In the TUI a completion per node is fine; the panel and `/dag` draw the graph.

## External coding owner

Only when the person already chose an external coding CLI (Codex or Claude Code) for this work:

- `omh_dag action=export owner=<codex|claude-code>` turns the waiting coding nodes (those with a
  write scope) into a `fanout_contract/v2` units file and a goal file, and returns the two
  `omh coding fanout` commands to run.
- It refuses when no such choice is recorded, and when a coding node still waits on a node that has
  not run; settle those first.
- Settle each exported node from the fanout result, writing its handoff file from that result.

Without that choice every node runs on `delegate_task`.
