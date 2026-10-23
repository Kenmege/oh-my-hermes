"""Approved memory-record fixtures shared by governance tests."""

from __future__ import annotations

from datetime import datetime, timezone

from _local_package import load_local_package

load_local_package()
from omh.plugin_bundle.omh import memory_governance as governance  # noqa: E402


NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
SAFE_SUMMARY = "Run deterministic release checks before deployment."


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _approved_artifact(
    *,
    schema_version: str = governance.PROJECT_MEMORY_RECORD_SCHEMA_VERSION,
    retention_class: str = "standard",
    record_type: str = "procedure",
    summary: str = SAFE_SUMMARY,
    admission_state: str = "approved_manual",
    revalidation: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    id_key = {
        governance.PROJECT_MEMORY_RECORD_SCHEMA_VERSION: "record_id",
        governance.MEMORY_SCOPE_SCHEMA_VERSION: "item_id",
        governance.MEMORY_BLOCK_SCHEMA_VERSION: "block_id",
    }[schema_version]
    artifact: dict[str, object] = {
        "schema_version": schema_version,
        id_key: "mem_fixture",
        "revision": 1,
        "record_type": record_type,
        "summary": summary,
        "scope": {"kind": "project", "ref": "default"},
        "source_class": "omh_local",
        "retention": governance.build_retention(
            retention_class,
            record_type=record_type,
            admitted_at=NOW,
        ),
    }
    if schema_version == governance.MEMORY_SCOPE_SCHEMA_VERSION:
        artifact["value"] = "release-check-policy"
    if schema_version == governance.MEMORY_BLOCK_SCHEMA_VERSION:
        artifact["value"] = "release-check-policy"
        artifact["label"] = "release-policy"
    if revalidation is not None:
        artifact["revalidation"] = revalidation
    identity = governance.stable_artifact_identity(artifact)
    payload_digest = governance.canonical_payload_digest(artifact)
    review = {
        "schema_version": governance.PROJECT_MEMORY_REVIEW_RECORD_SCHEMA_VERSION,
        "review_id": "review_fixture",
        "artifact_identity": identity,
        "decision": admission_state,
        "payload_digest": payload_digest,
        "policy_version": governance.MEMORY_GOVERNANCE_POLICY_VERSION,
        "classifier_version": governance.MEMORY_CLASSIFIER_VERSION,
    }
    artifact["admission"] = {
        "state": admission_state,
        "admitted_at": _iso(NOW),
        "review_id": "review_fixture",
        "artifact_identity": identity,
        "payload_digest": payload_digest,
        "policy_version": governance.MEMORY_GOVERNANCE_POLICY_VERSION,
        "classifier_version": governance.MEMORY_CLASSIFIER_VERSION,
    }
    return artifact, review


def _evaluate(
    artifact: dict[str, object],
    review: dict[str, object],
    *,
    now: datetime = NOW,
    **kwargs: object,
) -> dict[str, object]:
    return governance.evaluate_memory_replay(
        artifact,
        now=now,
        requested_scope={"kind": "project", "ref": "default"},
        review_resolver={"review_fixture": review},
        **kwargs,
    )
