"""Let Hermes see how its own memory relates to OMH's approved records.

PR #672 built the comparison but left it CLI-only: `omh memory status` carried
it, and none of the nine registered tools did, so the model could not reach it
from chat. That is the half of the problem #672 did not close -- the store had
governance and no outlet.

The tool later grew a second job. OMH's memory blocks split into a tier that
renders every turn and a tier that is listed by label and read on request, and
the second tier only means anything if something can perform the read. That
something is this tool rather than a memory-provider tool schema: Hermes gates
provider tools behind toolset config (``memory_provider_tools_enabled`` in
``agent/memory_manager.py``), so a tier built on one would disappear for any
operator whose toolsets exclude memory, and ``agent/memory_provider.py`` names
tool-schema bloat as the reason only one external provider may run at all.

Two different redaction rules meet here, and they do not merge. OMH block values
are OMH's own content and are returned in full. Hermes memory entries are not
OMH's, are never returned, and appear only as counts, hashes, and similarity
scores -- the same boundary this file has always held. One action narrows that
rule rather than breaking it: ``demote`` returns, for the one entry OMH now
holds, the ``old_text`` Hermes' own memory tool needs to remove it -- the
entry itself when it is at most 60 characters, else the first window of at
most 60 characters no other entry contains, and the whole entry only when no
such window exists. That text is the model's own memory, already in its
system prompt every turn, so the prefix discloses nothing the reader did not
hold -- the reasoning ``demotion_reference_line`` already quotes an entry under.

Read-only with respect to Hermes: nothing here opens a Hermes file for writing.
Hermes owns ``~/.hermes/memories``, and the `memory` tool Hermes exposes to the
model is what edits it.

The one write is ``capture``, into OMH's own store. It is the model's path
into that store with no operator step: the store held zero records after two
months while capture and approve existed only as CLI verbs. ``capture`` calls
memory admission (``memory_admission``) in-process -- the same path the
``omh memory capture`` CLI reaches through its adapters -- and reports the
receipt state admission observed on disk, never one it did not.

``demote`` is the second write, also into OMH's own store only. Hermes memory
is a small character-capped file paid for on every turn, and the operator path
(`omh memory demote --stage`, review, then a person asking Hermes to edit)
never ran on the machine it was built for while that file sat nearly full.
The action reads the entry from the Hermes file itself -- never a paraphrase
the model supplies -- copies it into OMH under the guards the staging
workflow holds, and only when an approved, intact, durable user-global copy
exists tells the model to remove the original with Hermes' own `memory` tool.
Token similarity never authorizes that removal.
"""

from __future__ import annotations

from .. import runtime_paths

import json
from pathlib import Path

from ..degradation import safe_error_type as _safe_error_type
from ..hermes_memory import (
    HERMES_MEMORY_DELIMITER,
    HermesMemoryFile,
    build_hermes_memory_bridge,
    demotion_origin_ref,
    entry_digest,
    read_approved_records,
    read_hermes_memory,
)
from ..memory_admission import (
    DEMOTION_SUMMARY_MAX_CHARS,
    MODEL_CAPTURE_SOURCE,
    capture_project_memory_candidate,
    demotion_refusal_reason,
    held_durable_user_global_record,
)
from ..memory_store_io import read_project_memory_candidates
from ..host_observation import OBSERVATION_SCHEMA, attach_public_observation, observe_plugin_tool_call
from ..memory_blocks import read_memory_block, read_memory_blocks, select_memory_blocks
from ..memory_dreaming import read_dreaming_state, read_latest_consolidation
from ..project_identity import resolve_project_identity

MEMORY_ACTIONS = ("status", "blocks", "read", "consolidation", "capture", "demote")
CAPTURE_RECORD_TYPES = ("fact", "decision", "lesson", "procedure", "episode")
CAPTURE_SCOPES = ("project", "user")
CAPTURE_RETENTION_CLASSES = ("durable", "standard", "volatile")
# Admission stores at most this many summary characters.
CAPTURE_SUMMARY_MAX_CHARS = 500
CAPTURE_MAX_TAGS = 8
# A sha256 prefix shorter than this is not an identifier a brief prints.
DEMOTE_SHA_PREFIX_MIN_CHARS = 12
# The most of a Hermes entry `demote` returns: enough for Hermes' `old_text`.
DEMOTE_OLD_TEXT_MAX_CHARS = 60
# Hermes' memory tool names its two files by these `target` values.
HERMES_MEMORY_TARGETS = {"MEMORY.md": "memory", "USER.md": "user"}

OMH_MEMORY_SCHEMA = {
    "name": "omh_memory",
    "description": (
        "Read and add to OMH's durable memory. Call action='capture' yourself, without asking, when "
        "the user states something worth knowing in a later session: a lasting preference, a decision, "
        "a fact about their setup or project, or a lesson learned. One fact per call, as one or two "
        "sentences with absolute dates (never 'yesterday' or 'next week'). Never capture secrets, raw "
        "logs, transcripts, or task progress. Safe captures are remembered at once; the result's status "
        "says remembered, already_remembered, pending_review, or refused. Call 'status' first only when "
        "unsure what is already held. 'status' compares Hermes' built-in memory (MEMORY.md, USER.md) "
        "with OMH's approved records; 'blocks' lists blocks with admission and replay state; 'read' "
        "returns a replay-eligible block's value; 'consolidation' reads the latest reminder without "
        "evaluating it. 'demote' copies one Hermes entry (entry_sha256) into OMH; only if status is "
        "demoted or already_covered, remove it with Hermes' memory tool per remove_from_hermes. Hermes "
        "entries are otherwise only counted and hashed. After capture or demote, tell the user in one "
        "short line what was kept; display_line is suggested wording."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(MEMORY_ACTIONS),
                "description": "Defaults to 'status'. 'capture' and 'demote' write OMH memory; the others only read.",
            },
            "label": {
                "type": "string",
                "description": "Block label, required by the 'read' action.",
            },
            "summary": {
                "type": "string",
                "description": "capture: the fact itself, self-contained, at most 500 characters.",
            },
            "record_type": {
                "type": "string",
                "enum": list(CAPTURE_RECORD_TYPES),
                "description": "capture: defaults to 'fact'.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "capture: up to 8 short topic tags.",
            },
            "scope": {
                "type": "string",
                "enum": list(CAPTURE_SCOPES),
                "description": "capture: defaults to 'project' inside an identified repository.",
            },
            "retention_class": {
                "type": "string",
                "enum": list(CAPTURE_RETENTION_CLASSES),
                "description": "capture: defaults to 'durable'; 'volatile' expires within a week.",
            },
            "entry_sha256": {
                "type": "string",
                "description": "demote: the entry's sha256 or a unique 12+ hex prefix.",
            },
            "observation": OBSERVATION_SCHEMA,
        },
    },
}


def omh_memory_handler(args: dict, **kwargs) -> str:
    observation = observe_plugin_tool_call("omh_memory", args, kwargs)
    action = str((args or {}).get("action", "") or "status").strip().lower()
    if action not in MEMORY_ACTIONS:
        payload, backend = _unknown_action(action), "bundle_memory"
    elif action == "blocks":
        payload, backend = _blocks(), "bundle_memory"
    elif action == "read":
        payload, backend = _read_block(str((args or {}).get("label", "") or "")), "bundle_memory"
    elif action == "consolidation":
        payload, backend = _consolidation()
    elif action == "capture":
        payload, backend = _capture(args or {}, kwargs), "bundle_memory"
    elif action == "demote":
        payload, backend = _demote(args or {}), "bundle_memory"
    else:
        payload, backend = _memory_bridge()
    payload["plugin_tool"] = "omh_memory"
    payload["action"] = action
    payload["source_backend"] = backend
    return json.dumps(attach_public_observation(payload, observation), sort_keys=True)


def _memory_bridge() -> tuple[dict[str, object], str]:
    """Compare the two stores using the bundle's own reader.

    This used to delegate to `omh.memory` and fall back when the import failed.
    The Hermes process cannot import that package -- it lives in its own
    environment -- so the fallback was the *only* path taken on the host this
    tool exists for, and the tool answered "package_absent" every time. Measured
    live before the fix: the model called it, got nothing, and shelled out to
    `omh memory status` instead. The reader is vendored here now, which is what
    every other working bundle surface already does.
    """
    try:
        return (
            build_hermes_memory_bridge(_home("OMH_HOME", "~/.omh"), _home("HERMES_HOME", "~/.hermes")),
            "bundle_memory",
        )
    except Exception as exc:
        # A read failure must stay distinguishable from an empty comparison, or
        # an unreadable memory file reads as a memory with nothing in it.
        return _unavailable(_safe_error_type(type(exc).__name__)), "bundle_memory_error"


def _blocks() -> dict[str, object]:
    """Review-readable block status, never values or ineligible descriptions."""
    home = _home("OMH_HOME", "~/.omh")
    blocks = read_memory_blocks(home)
    selection = select_memory_blocks(blocks, omh_home=home)
    rows = []
    for block in blocks:
        row = block.to_summary()
        row["replay"] = _public_replay(selection.evaluations[block.block_id])
        rows.append(row)
    return {
        "schema_version": "omh_memory_block_listing/v2",
        "blocks": rows,
        "block_count": len(rows),
        "next_action": "Use action='read' only for a replay-eligible block label.",
        "claim_boundary": "Listings are review-readable metadata, not evidence Hermes read, wrote, or used memory.",
    }


def _read_block(label: str) -> dict[str, object]:
    """One block's value, or a stated miss.

    A missing block returns ``found: false`` rather than an empty value, so an
    absent block is never mistaken for a block that has nothing to say.
    """
    if not label:
        return {
            "schema_version": "omh_memory_block_read/v1",
            "found": False,
            "reason": "label_required",
            "next_action": "Call action='blocks' to list available labels.",
        }
    home = _home("OMH_HOME", "~/.omh")
    blocks = read_memory_blocks(home)
    selection = select_memory_blocks(blocks, omh_home=home)
    for block in blocks:
        if block.label != label:
            continue
        replay = _public_replay(selection.evaluations[block.block_id])
        if not replay["eligible"]:
            return {
                "schema_version": "omh_memory_block_read/v2",
                "found": True,
                "block_id": block.block_id,
                "revision": block.revision,
                "replay": replay,
                "next_action": "Review or reactivate this block; ineligible blocks never return values.",
            }
        return {
            "schema_version": "omh_memory_block_read/v2",
            "found": True,
            "block": block.to_dict(),
            "replay": replay,
            "claim_boundary": "A replay-eligible value is prepared OMH context, not observed Hermes use or mutation.",
        }
    return {
        "schema_version": "omh_memory_block_read/v1",
        "found": False,
        "reason": "unknown_label",
        "label": label,
        "next_action": "Call action='blocks' to list available labels.",
    }


def _public_replay(evaluation: dict[str, object]) -> dict[str, object]:
    return {
        "eligible": bool(evaluation.get("eligible")),
        "reason_code": str(evaluation.get("reason_code", "ineligible")),
        "admission_state": evaluation.get("admission_state"),
        "source_class": evaluation.get("source_class"),
        "retention_class": evaluation.get("retention_class"),
    }


def _consolidation() -> tuple[dict[str, object], str]:
    """Read the latest reminder and counters without entering a provider session.

    Like `omh memory dream` without `--evaluate`, this is a status query, not
    a scheduler trigger. Keep a recorded due brief visible even when its
    reasons are suppressed for the next evaluation. No brief means unknown,
    not a fresh evaluation that found nothing due.
    """
    omh_home = _home("OMH_HOME", "~/.omh")
    try:
        payload = dict(read_latest_consolidation(omh_home) or {})
        payload["state"] = read_dreaming_state(omh_home)
        payload["evaluated"] = False
        return payload, "bundle_memory"
    except Exception as exc:
        return _unavailable(_safe_error_type(type(exc).__name__)), "bundle_memory_error"


def _capture(args: dict, kwargs: dict) -> dict[str, object]:
    """Capture one memory through memory admission and map its answer."""
    summary = " ".join(str(args.get("summary", "") or "").split())
    record_type = str(args.get("record_type", "") or "fact").strip().lower()
    retention = str(args.get("retention_class", "") or "durable").strip().lower()
    tags = args.get("tags") or []
    if not summary:
        return _capture_result("refused", reason="summary_required", next_action="Retry with a one- or two-sentence summary.")
    if "\x00" in summary or any(isinstance(tag, str) and "\x00" in tag for tag in (tags if isinstance(tags, list) else [])):
        # Refused rather than stored: the CLI path can never carry one (a NUL
        # cannot travel in an argv), so neither does this one.
        return _capture_result("refused", reason="control_character", next_action="Remove the NUL character and retry.")
    if len(summary) > CAPTURE_SUMMARY_MAX_CHARS:
        return _capture_result("refused", reason="summary_too_long", next_action=f"Restate the fact in at most {CAPTURE_SUMMARY_MAX_CHARS} characters.")
    if record_type not in CAPTURE_RECORD_TYPES:
        return _capture_result("refused", reason="unsupported_record_type", next_action=f"Use one of: {', '.join(CAPTURE_RECORD_TYPES)}.")
    if retention not in CAPTURE_RETENTION_CLASSES:
        return _capture_result("refused", reason="unsupported_retention_class", next_action=f"Use one of: {', '.join(CAPTURE_RETENTION_CLASSES)}.")
    if not isinstance(tags, list) or len(tags) > CAPTURE_MAX_TAGS or not all(isinstance(tag, str) for tag in tags):
        return _capture_result("refused", reason="invalid_tags", next_action=f"Pass at most {CAPTURE_MAX_TAGS} tags as a list of strings.")
    cwd = _session_cwd(kwargs)
    identity = resolve_project_identity(cwd) if cwd is not None else None
    in_project = identity is not None and identity.state == "resolved"
    scope = str(args.get("scope", "") or ("project" if in_project else "user")).strip().lower()
    if scope not in CAPTURE_SCOPES:
        return _capture_result("refused", reason="unsupported_scope", next_action="Use scope 'project' or 'user'.")
    if scope == "project" and not in_project:
        return _capture_result(
            "refused",
            reason="project_scope_unresolved",
            next_action="This session is not inside a repository OMH can identify; retry with scope='user'.",
        )
    try:
        omh_home = Path(_home("OMH_HOME", "~/.omh"))
    except ValueError as exc:  # RuntimeBindingError is a ValueError
        return _capture_result("error", reason=_safe_error_type(type(exc).__name__), next_action="Nothing was saved; this session's OMH home could not be resolved.")
    try:
        payload = capture_project_memory_candidate(
            omh_home,
            summary,
            record_type=record_type,
            retention_class=retention,
            # The model's own loop has no reviewer who would act on a duplicate
            # candidate, so a duplicate names the existing record and writes nothing.
            on_duplicate="skip",
            source=MODEL_CAPTURE_SOURCE,
            # `user` maps to the user-global scope: it needs no acting-principal
            # context, and recall delivers it beside the project scope.
            scope_kind="project" if scope == "project" else "user-global",
            scope_ref=identity.identity if scope == "project" and identity is not None else None,
            tags=list(tags),
        )
    except Exception as exc:
        # In-process now, so a store fault would otherwise raise into Hermes'
        # tool dispatch. It is an error result naming the exception class --
        # never a status that says something was saved.
        return _capture_result(
            "error",
            reason=_safe_error_type(type(exc).__name__),
            next_action="Nothing is confirmed saved. Tell the user the capture failed; call action='status' to see what is held.",
        )
    return _map_capture_payload(payload)


def _map_capture_payload(payload: dict) -> dict[str, object]:
    """Translate an admission result into the tool's closed status set."""
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    record = payload.get("record") if isinstance(payload.get("record"), dict) else {}
    receipt = payload.get("receipt_state")
    fields: dict[str, object] = {
        "receipt_state": str(receipt) if receipt else None,
        "candidate_id": str(candidate.get("candidate_id", "") or "") or None,
        "record_id": str(record.get("record_id", "") or "") or None,
        "admission_state": _admission_state(record),
    }
    if not payload.get("captured"):
        reason = str(payload.get("reason", "") or "not_captured")
        if reason == "duplicate":
            duplicate_of = str(payload.get("duplicate_of", "") or "")
            return _capture_result(
                "already_remembered",
                **{**fields, "record_id": duplicate_of or None},
                duplicate_of=duplicate_of,
                next_action="Nothing new was saved; the same fact is already held.",
            )
        if payload.get("instruction_cue"):
            # A vocabulary item from admission's closed cue list, never the
            # user's text, so the model sees which words made it an order.
            fields["instruction_cue"] = str(payload["instruction_cue"])
        return _capture_result(
            "refused",
            **fields,
            reason=reason,
            # A refusal that names its own fix (an instruction-shaped summary)
            # passes it through, so the model learns how to restate it.
            next_action=(
                "Memory is turned off in OMH settings; nothing was saved."
                if reason == "project_memory_disabled"
                else str(payload.get("next_action", "") or "") or "Nothing was saved. Do not retry the same text."
            ),
        )
    if payload.get("auto_approved"):
        return _capture_result(
            "remembered",
            **fields,
            display_line=_display_line("📌 OMH remembered: ", str(candidate.get("summary", "") or ""), 80),
            next_action="Saved. Later sessions receive it when it fits their recall budget.",
        )
    return _capture_result(
        "pending_review",
        **{**fields, "admission_state": "pending_review"},
        review_reason=str(payload.get("review_reason", "") or "unknown"),
        next_action=(
            "Saved as a candidate that waits for operator review (`omh memory review`). For "
            "relative_time_phrase, capture the fact again with an absolute date instead."
        ),
    )


def _admission_state(record: dict) -> str | None:
    admission = record.get("admission")
    state = admission.get("state") if isinstance(admission, dict) else None
    return str(state) if state else None


def _capture_result(status: str, **fields: object) -> dict[str, object]:
    return {
        "schema_version": "omh_memory_capture/v1",
        "status": status,
        "receipt_state": None,
        "record_id": None,
        "candidate_id": None,
        "admission_state": None,
        **fields,
        "claim_boundary": (
            "receipt_state is what OMH memory admission observed on local disk; it is not "
            "evidence that a later session recalled or used the memory, and Hermes memory is unchanged."
        ),
    }


def _demote(args: dict) -> dict[str, object]:
    """Move one Hermes entry into OMH; say whether its original may now go."""
    wanted = str(args.get("entry_sha256", "") or "").strip().lower()
    if not DEMOTE_SHA_PREFIX_MIN_CHARS <= len(wanted) <= 64 or any(c not in "0123456789abcdef" for c in wanted):
        return _demote_result(
            "refused",
            reason="entry_sha256_invalid",
            next_action=f"Pass the entry's sha256 or a prefix of at least {DEMOTE_SHA_PREFIX_MIN_CHARS} hex characters.",
        )
    try:
        return _demote_entry(wanted, Path(_home("OMH_HOME", "~/.omh")), Path(_home("HERMES_HOME", "~/.hermes")))
    except Exception as exc:
        # In-process inside Hermes' tool dispatch: a fault is an error result,
        # never `demoted`, so the model is never told to remove the original.
        return _demote_result(
            "error",
            reason=_safe_error_type(type(exc).__name__),
            next_action="Keep the Hermes entry; nothing is confirmed moved.",
        )


def _demote_entry(wanted: str, omh_home: Path, hermes_home: Path) -> dict[str, object]:
    matches: list[tuple[HermesMemoryFile, str]] = []
    for reading in read_hermes_memory(hermes_home):
        # An entry repeated verbatim in one file is one entry to Hermes too:
        # its store de-duplicates on load and its remove treats copies as one.
        for entry in dict.fromkeys(reading.entries):
            if entry_digest(entry).startswith(wanted):
                matches.append((reading, entry))
    if not matches:
        return _demote_result("refused", reason="entry_not_found", next_action="Nothing changed. The entry may already be gone.")
    if len({entry for _reading, entry in matches}) == 1 and len(matches) > 1:
        # The same text in MEMORY.md and USER.md has one digest, so no prefix
        # can tell the two apart, and removing one would leave the other.
        return _demote_result(
            "refused",
            reason="entry_in_both_files",
            files=[reading.label for reading, _entry in matches],
            next_action="Keep both; this entry is in MEMORY.md and USER.md. Remove one copy with Hermes' memory tool first if it should move.",
        )
    if len(matches) > 1:
        return _demote_result("refused", reason="entry_ambiguous", next_action="Retry with a longer entry_sha256 prefix.")
    reading, entry = matches[0]
    label = reading.label
    fields: dict[str, object] = {"file": label, "entry_sha256": entry_digest(entry), "chars": len(entry)}
    hermes_entries = _hermes_parsed_entries(reading.path)
    if entry not in hermes_entries:
        # Removing a fragment's old_text removes the whole Hermes entry around
        # it, so only an entry Hermes itself would parse may be moved.
        return _demote_result("refused", **fields, reason="entry_not_whole", next_action="Keep the Hermes entry; it is not one whole Hermes entry.")
    siblings = tuple(dict.fromkeys(hermes_entries))
    origin_ref = demotion_origin_ref(label, entry)
    # A standing decision on exactly this entry comes before any notion of
    # "already held": a rejection is never overridden by a matching record.
    for candidate in read_project_memory_candidates(omh_home):
        if str(candidate.get("source_ref", "")) != origin_ref:
            continue
        status = str(candidate.get("status", ""))
        candidate_id = str(candidate.get("candidate_id", "")) or None
        if status == "rejected":
            return _demote_result(
                "refused", **fields, reason="previously_rejected", candidate_id=candidate_id,
                next_action="A reviewer rejected this entry; keep it in Hermes.",
            )
        if status in ("pending_review", "blocked_review_required"):
            return _demote_result(
                "pending_review",
                **fields,
                candidate_id=candidate_id,
                next_action="A copy already waits for review; keep the Hermes entry until it is approved.",
            )
        if status != "approved":
            return _demote_result(
                "refused", **fields, reason="candidate_status_unknown", candidate_status=status,
                candidate_id=candidate_id, next_action="Keep the Hermes entry.",
            )
    for record in read_approved_records(omh_home):
        if str(record.get("source_ref", "")) != origin_ref:
            continue
        if str(record.get("summary", "")) != entry:
            return _demote_result(
                "refused",
                **fields,
                reason="content_not_intact",
                record_id=str(record.get("record_id", "")) or None,
                next_action="Keep the Hermes entry; the OMH copy of it differs.",
            )
        return _demoted("already_covered", reading, entry, siblings, fields, str(record.get("record_id", "")))
    # Similarity never authorizes a removal: a negation of the same fact shares
    # nearly every token. Only an exact durable user-global copy does; a merely
    # similar record falls through to a capture of the entry itself.
    held = held_durable_user_global_record(omh_home, entry)
    if held:
        return _demoted("already_covered", reading, entry, siblings, fields, held)
    refusal = demotion_refusal_reason(entry)
    if refusal:
        # Checked before capture: a redacted or truncated copy is never written.
        return _demote_result(
            "refused",
            **fields,
            reason=refusal,
            next_action=(
                f"Keep the Hermes entry; split it into entries of at most {DEMOTION_SUMMARY_MAX_CHARS} characters to move it."
                if refusal == "summary_bound_exceeded"
                else "Keep the Hermes entry; OMH would store it redacted."
            ),
        )
    payload = capture_project_memory_candidate(
        omh_home,
        entry,
        record_type="fact",
        on_duplicate="skip",
        source="hermes_demotion",
        source_ref=origin_ref,
        # Hermes memory belongs to one Hermes home, not to a project.
        scope_kind="user-global",
        retention_class="durable",
        tags=["hermes-demotion"],
    )
    if not payload.get("captured"):
        if payload.get("reason") == "duplicate":
            # The exact durable user-global case returned above, so the record
            # admission matched (by normalized summary) differs in case or
            # spacing, is not durable, or is not replay-eligible: it is not an
            # exact copy that comes back the way the Hermes entry would.
            return _demote_result(
                "refused",
                **fields,
                reason="held_record_not_removable",
                record_id=str(payload.get("duplicate_of", "") or "") or None,
                next_action="Keep the Hermes entry; OMH holds only an inexact, non-durable, or non-recallable copy of it.",
            )
        return _demote_result(
            "refused",
            **fields,
            reason=str(payload.get("reason", "") or "not_captured"),
            next_action="Keep the Hermes entry; nothing was moved.",
        )
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    record = payload.get("record") if isinstance(payload.get("record"), dict) else {}
    fields["candidate_id"] = str(candidate.get("candidate_id", "") or "") or None
    if str(candidate.get("summary", "") or record.get("summary", "")) != entry:
        # Not retired here: retiring needs the review workflow, which this
        # process cannot import. Every later call refuses through the
        # source_ref check above, so the Hermes entry is never removed on it.
        return _demote_result("refused", **fields, reason="content_not_intact", next_action="Keep the Hermes entry; OMH stored an altered copy.")
    if not payload.get("auto_approved"):
        return _demote_result(
            "pending_review",
            **fields,
            review_reason=str(payload.get("review_reason", "") or "unknown"),
            next_action="Saved for review; keep the Hermes entry until it is approved.",
        )
    return _demoted("demoted", reading, entry, siblings, fields, str(record.get("record_id", "") or ""))


def _hermes_parsed_entries(path: Path) -> list[str]:
    """The file split exactly as Hermes' store splits it (``_parse_entries``)."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    return [part.strip() for part in raw.split(HERMES_MEMORY_DELIMITER) if part.strip()]


def _demoted(
    status: str,
    reading: HermesMemoryFile,
    entry: str,
    siblings: tuple[str, ...],
    fields: dict[str, object],
    record_id: str,
) -> dict[str, object]:
    """OMH holds the entry: hand the model the native remove that frees the space."""
    target = HERMES_MEMORY_TARGETS.get(reading.label, "memory")
    prefix = "📦 OMH moved to long-term memory: " if status == "demoted" else "📦 OMH already holds: "
    return _demote_result(
        status,
        **fields,
        record_id=record_id or None,
        remove_from_hermes={"target": target, "old_text": _unique_old_text(entry, siblings)},
        display_line=_display_line(prefix, entry, DEMOTE_OLD_TEXT_MAX_CHARS),
        next_action=(
            f"Remove the entry with Hermes' memory tool: action='remove', target='{target}', old_text from "
            "remove_from_hermes. Confirm the entry Hermes reports removed is this one."
        ),
    )


def _unique_old_text(entry: str, siblings: tuple[str, ...]) -> str:
    """The shortest-reach `old_text` that selects only this entry in Hermes.

    Hermes strips `old_text`, prefers a whole-entry exact match, and refuses a
    substring two distinct entries contain. A short entry is its own exact
    match; a longer one gets the first window of at most 60 characters no
    other entry contains. An entry wholly inside a sibling has no such window,
    so it is returned whole: correct beats short.
    """
    if len(entry) <= DEMOTE_OLD_TEXT_MAX_CHARS:
        return entry
    others = [sibling for sibling in siblings if sibling != entry]
    for start in range(len(entry) - DEMOTE_OLD_TEXT_MAX_CHARS + 1):
        window = entry[start : start + DEMOTE_OLD_TEXT_MAX_CHARS].strip()
        if window and not any(window in other for other in others):
            return window
    return entry


def _display_line(prefix: str, text: str, limit: int) -> str:
    """Suggested user-facing wording; the model relays it in its own voice."""
    flat = " ".join(text.split())
    return prefix + (flat if len(flat) <= limit else flat[:limit] + "…")


def _demote_result(status: str, **fields: object) -> dict[str, object]:
    return {
        "schema_version": "omh_memory_demote/v1",
        "status": status,
        **fields,
        "claim_boundary": (
            "OMH wrote only its own store. Hermes memory is unchanged until Hermes' own memory tool "
            "removes the entry; this result is not evidence that it did."
        ),
    }


def _session_cwd(kwargs: dict) -> Path | None:
    """A host-supplied cwd, else Hermes' logical working directory, else None."""
    supplied = kwargs.get("cwd")
    try:
        if supplied:
            return runtime_paths.expand_path(supplied)
        return runtime_paths.runtime_cwd()
    except (OSError, ValueError):
        return None


def _unknown_action(action: str) -> dict[str, object]:
    return {
        "schema_version": "omh_memory_unknown_action/v1",
        "status": "unavailable",
        "reason": "unknown_action",
        "requested_action": action,
        "supported_actions": list(MEMORY_ACTIONS),
        "next_action": f"Retry with one of: {', '.join(MEMORY_ACTIONS)}.",
        "claim_boundary": "No memory view was produced. This is not evidence about memory state.",
    }


def _home(variable: str, default: str) -> str:
    return str(runtime_paths.default_omh_home() if variable == "OMH_HOME" else runtime_paths.default_hermes_home())


def _unavailable(reason: str) -> dict[str, object]:
    return {
        "schema_version": "omh_memory_bridge_unavailable/v1",
        "status": "unavailable",
        "reason": reason,
        "next_action": "Run `omh memory status` locally, or `omh doctor` if OMH may not be installed.",
        "claim_boundary": (
            "No memory comparison was produced. This is not evidence that Hermes memory is empty, "
            "in sync, or readable."
        ),
    }


__all__ = ["MEMORY_ACTIONS", "OMH_MEMORY_SCHEMA", "omh_memory_handler", "read_memory_block"]
