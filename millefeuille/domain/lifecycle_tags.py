"""Canonical lifecycle-tag registry and offline legacy-tag migration plans.

This module derives preview-only Zotero tag changes from verified local run
artifacts.  Existing Zotero tags are observations, never evidence of a stage.
No function in this module contacts Zotero or mutates a file.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
import unicodedata

from millefeuille.domain.artifacts import ArtifactIndex
from millefeuille.domain.millefeuille import (
    ALLOWED_TAG_TRANSITIONS,
    AcceptanceCheckStatus,
    AcceptanceStatus,
    AcceptanceSummaryRecord,
    ClassificationDecisionRecord,
    ClassificationPlanRecord,
    ClassificationStatus,
    MillefeuilleContractError,
    StageManifest,
    StageName,
    StageStatus,
    TagState,
)
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.taxonomy import validate_taxonomy_lock

LIFECYCLE_TAG_REGISTRY_SCHEMA_VERSION = "millefeuille-lifecycle-tag-registry/v0.1"
LIFECYCLE_TAG_MIGRATION_PLAN_SCHEMA_VERSION = (
    "millefeuille-lifecycle-tag-migration-plan/v0.1"
)
LIFECYCLE_TAG_REGISTRY_ID = "millefeuille-lifecycle"
LIFECYCLE_TAG_REGISTRY_VERSION = "0.1"
LIFECYCLE_TAG_JSON_MAX_BYTES = 1_048_576
LIFECYCLE_TAG_MAX_NESTING = 64
LIFECYCLE_TAG_MAX_NODES = 65_536

_DIGEST_RE = re.compile(r"sha256(?:-aggregate)?:[0-9a-f]{64}\Z")
_CONTENT_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+\-/]{0,255}\Z")
_SAFE_ARTIFACT_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,254}\Z")

_CANONICAL_TAG_ORDER = tuple(state.value for state in TagState)
CANONICAL_LIFECYCLE_TAGS = frozenset(_CANONICAL_TAG_ORDER)

_STAGE_TAGS = {
    StageName.DISCOVER.value: "millefeuille-previewed",
    StageName.HANDOFF.value: "millefeuille-handoff-exported",
    StageName.RECOVER.value: "millefeuille-source-verified",
    StageName.SOURCE_PACK.value: "millefeuille-source-packed",
    StageName.EXTRACT_NATIVE.value: "millefeuille-extracted-native",
    StageName.EXTRACT_OCR.value: "millefeuille-extracted-ocr",
    StageName.STRUCTURE.value: "millefeuille-structure-ready",
    StageName.SUMMARIZE.value: "millefeuille-summarized",
    StageName.CARD.value: "millefeuille-card-ready",
    StageName.OPENKB_ADD.value: "millefeuille-openkb-added",
}

_TRANSITIONS = tuple(
    sorted(
        (source.value, destination.value)
        for source, destination in ALLOWED_TAG_TRANSITIONS
    )
)

_LEGACY_POLICY = (
    {
        "match": "exact",
        "selector": "millefeuille-processed",
        "observed_meaning": "legacy-millefeuille-processing-reported-success",
        "canonical_mapping": None,
        "removal_policy": "preserve-history",
    },
    {
        "match": "prefix",
        "selector": "docai",
        "observed_meaning": "external-predecessor-outcome",
        "canonical_mapping": None,
        "removal_policy": "preserve-history",
    },
)

_STAGE_EVIDENCE = ("artifact_index", "stage_manifest")
_TAG_EVIDENCE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(_STAGE_TAGS.values(), _STAGE_EVIDENCE),
    "millefeuille-indexed": _STAGE_EVIDENCE,
    "millefeuille-acceptance-passed": (
        "acceptance_summary",
        "artifact_index",
        "stage_manifest",
    ),
    "millefeuille-ready-for-classification": ("acceptance_summary",),
    "millefeuille-classified": (
        "acceptance_summary",
        "classification_decision",
        "classification_plan",
        "taxonomy_lock",
    ),
    "millefeuille-needs-review": _STAGE_EVIDENCE,
    "millefeuille-error": _STAGE_EVIDENCE,
}

_EVIDENCE_SCHEMA_VERSIONS = {
    "stage_manifest": "millefeuille-stage-manifest/v0.1",
    "artifact_index": "millefeuille-artifact-index/v0.1",
    "acceptance_summary": "openkb-millefeuille-acceptance-summary/v0.1",
    "classification_plan": "millefeuille-classification-plan/v0.1",
    "classification_decision": "millefeuille-classification-decision/v0.1",
    "taxonomy_lock": "millefeuille-taxonomy-lock/v0.1",
}
_FIXED_EVIDENCE_REFS = {
    "stage_manifest": "stage-manifest.json",
    "artifact_index": "artifact-index.json",
    "acceptance_summary": "reports/acceptance-summary.json",
    "classification_plan": "classification/classification-plan.json",
    "taxonomy_lock": "taxonomy-lock.json",
}


def _registry_body() -> dict[str, Any]:
    tags: list[dict[str, Any]] = []
    for tag in _CANONICAL_TAG_ORDER:
        if tag == "millefeuille":
            kind = "selection"
            evidence_stage = None
        elif tag in {"millefeuille-needs-review", "millefeuille-error"}:
            kind = "exception"
            evidence_stage = None
        elif tag in {
            "millefeuille-acceptance-passed",
            "millefeuille-ready-for-classification",
        }:
            kind = "acceptance"
            evidence_stage = StageName.ACCEPTANCE.value
        elif tag == "millefeuille-classified":
            kind = "terminal-success"
            evidence_stage = StageName.CLASSIFY.value
        elif tag == "millefeuille-indexed":
            kind = "progress"
            evidence_stage = StageName.INDEX.value
        else:
            kind = "progress"
            evidence_stage = next(
                stage for stage, mapped in _STAGE_TAGS.items() if mapped == tag
            )
        tags.append(
            {
                "tag": tag,
                "kind": kind,
                "evidence_stage": evidence_stage,
            }
        )
    return {
        "schema_version": LIFECYCLE_TAG_REGISTRY_SCHEMA_VERSION,
        "registry_id": LIFECYCLE_TAG_REGISTRY_ID,
        "registry_version": LIFECYCLE_TAG_REGISTRY_VERSION,
        "authority": "local-artifact-evidence-not-zotero-tags",
        "tags": tags,
        "transitions": [
            {"from": source, "to": destination} for source, destination in _TRANSITIONS
        ],
        "exception_transition_policy": {
            "from": "any-canonical-state",
            "destinations": [
                "millefeuille-error",
                "millefeuille-needs-review",
            ],
        },
        "legacy_tags": [dict(value) for value in _LEGACY_POLICY],
        "terminal_success": {
            "tag": "millefeuille-classified",
            "requires_acceptance_pass": True,
            "requires_classification_decision": "classified",
            "requires_released_taxonomy_lock": True,
        },
        "write_policy": {
            "mode": "preview-only",
            "selection_removal": "terminal-success-only",
            "legacy_outcome_removal": "never-auto-propose",
            "historical_tags_preserved_by_default": True,
            "requires_current-zotero-version-recheck": True,
            "requires_separate_approved-live_writeback": True,
        },
    }


def canonical_lifecycle_tag_registry() -> dict[str, Any]:
    """Return the immutable v0.1 registry with its canonical identity."""

    payload = _registry_body()
    payload["content_identity"] = _content_identity(payload)
    return payload


def validate_lifecycle_tag_registry(payload: dict[str, Any]) -> None:
    """Validate the exact immutable registry for this runtime version."""

    if type(payload) is not dict:
        raise MillefeuilleContractError("lifecycle tag registry must be an object")
    _reject_unsafe_strings(payload, "lifecycle tag registry")
    expected = canonical_lifecycle_tag_registry()
    if payload != expected:
        raise MillefeuilleContractError(
            "lifecycle tag registry differs from the canonical v0.1 policy"
        )
    digest = payload.get("content_identity")
    if not isinstance(digest, str) or _CONTENT_DIGEST_RE.fullmatch(digest) is None:
        raise MillefeuilleContractError(
            "lifecycle tag registry content_identity is invalid"
        )
    if not hmac.compare_digest(digest, _content_identity(payload)):
        raise MillefeuilleContractError(
            "lifecycle tag registry content_identity mismatch"
        )


def load_lifecycle_tag_registry(path: str | Path) -> dict[str, Any]:
    payload = load_lifecycle_tag_json(path, "lifecycle tag registry")
    validate_lifecycle_tag_registry(payload)
    return payload


def load_lifecycle_tag_json(path: str | Path, label: str) -> dict[str, Any]:
    """Load one bounded, duplicate-free JSON object through a no-follow read."""

    target = Path(path)
    try:
        observed_size = target.stat(follow_symlinks=False).st_size
    except OSError as exc:
        raise MillefeuilleContractError(f"could not inspect {label}") from exc
    if observed_size > LIFECYCLE_TAG_JSON_MAX_BYTES:
        raise MillefeuilleContractError(
            f"{label} exceeds {LIFECYCLE_TAG_JSON_MAX_BYTES} bytes"
        )
    payload_bytes = read_bytes_no_follow(
        target,
        label,
        max_bytes=LIFECYCLE_TAG_JSON_MAX_BYTES,
    )
    if len(payload_bytes) > LIFECYCLE_TAG_JSON_MAX_BYTES:
        raise MillefeuilleContractError(
            f"{label} exceeds {LIFECYCLE_TAG_JSON_MAX_BYTES} bytes"
        )
    try:
        text = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MillefeuilleContractError(f"{label} is not valid UTF-8") from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_fields,
            parse_constant=_reject_non_finite_json_number,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise MillefeuilleContractError(f"{label} is not valid JSON") from exc
    if type(payload) is not dict:
        raise MillefeuilleContractError(f"{label} must be an object")
    _reject_unsafe_strings(payload, label)
    return payload


def build_lifecycle_tag_migration_plan(
    *,
    registry: dict[str, Any],
    plan_id: str,
    item_key: str,
    zotero_version: int,
    current_tags: Sequence[str],
    stage_manifest_payload: dict[str, Any],
    artifact_index_payload: dict[str, Any],
    acceptance_payload: dict[str, Any] | None = None,
    classification_plan_payload: dict[str, Any] | None = None,
    classification_decision_payload: dict[str, Any] | None = None,
    taxonomy_lock: dict[str, Any] | None = None,
    remove_selection_after_terminal_success: bool = False,
) -> dict[str, Any]:
    """Derive one no-effect migration plan from exact local evidence."""

    validate_lifecycle_tag_registry(registry)
    _safe_identity(plan_id, "plan_id")
    _safe_identity(item_key, "item_key")
    if isinstance(zotero_version, bool) or not isinstance(zotero_version, int):
        raise MillefeuilleContractError("zotero_version must be an integer")
    if zotero_version < 0 or zotero_version > 9_007_199_254_740_991:
        raise MillefeuilleContractError("zotero_version is outside the safe range")
    if type(remove_selection_after_terminal_success) is not bool:
        raise MillefeuilleContractError(
            "remove_selection_after_terminal_success must be a boolean"
        )

    normalized_tags = _canonical_current_tags(current_tags)
    for label, evidence_payload in (
        ("stage manifest", stage_manifest_payload),
        ("artifact index", artifact_index_payload),
        ("acceptance summary", acceptance_payload),
        ("classification plan", classification_plan_payload),
        ("classification decision", classification_decision_payload),
        ("taxonomy lock", taxonomy_lock),
    ):
        if evidence_payload is not None:
            _reject_unsafe_strings(evidence_payload, label)
    _require_exact_fields(
        stage_manifest_payload,
        {
            "schema_version",
            "run_id",
            "source_type",
            "mode",
            "manual_gates",
            "stages",
        },
        "stage manifest",
    )
    stage_manifest = StageManifest.from_dict(deepcopy(stage_manifest_payload))
    if stage_manifest.to_dict() != stage_manifest_payload:
        raise MillefeuilleContractError("stage manifest is not an exact wire record")
    _require_exact_fields(
        artifact_index_payload,
        {
            "schema_version",
            "paper_id",
            "run_id",
            "artifact_root",
            "source_pack",
            "source_identity",
            "stages",
            "artifacts",
            "indexes",
            "zotero_writeback",
        },
        "artifact index",
    )
    artifact_index = ArtifactIndex.from_dict(deepcopy(artifact_index_payload))
    if artifact_index.to_dict() != artifact_index_payload:
        raise MillefeuilleContractError("artifact index is not an exact wire record")
    stage_manifest_ref = _validate_stage_artifact_join(
        stage_manifest,
        artifact_index,
    )
    source_hash = str(artifact_index.source_pack["source_hash"])
    if _DIGEST_RE.fullmatch(source_hash) is None:
        raise MillefeuilleContractError("artifact index source_hash is invalid")
    observed_item_key = artifact_index.source_identity.get("zotero_item_key")
    if observed_item_key != item_key:
        raise MillefeuilleContractError(
            "item_key drifts from artifact index Zotero identity"
        )

    evidence = {
        "stage_manifest": _evidence_binding(
            stage_manifest_ref,
            stage_manifest_payload,
        ),
        "artifact_index": _evidence_binding(
            "artifact-index.json", artifact_index_payload
        ),
        "acceptance_summary": None,
        "classification_plan": None,
        "classification_decision": None,
        "taxonomy_lock": None,
    }
    stage_statuses = {
        stage.name.value: stage.status.value for stage in stage_manifest.stages
    }
    derived: dict[str, list[str]] = {}
    for stage, tag in _STAGE_TAGS.items():
        if stage_statuses.get(stage) == StageStatus.PASSED.value:
            derived[tag] = list(_TAG_EVIDENCE_REQUIREMENTS[tag])

    _derive_index_tag(artifact_index, stage_statuses, derived)

    acceptance_result = _validate_acceptance_evidence(
        acceptance_payload,
        stage_manifest=stage_manifest,
        artifact_index=artifact_index,
        stage_status=stage_statuses.get(StageName.ACCEPTANCE.value),
    )
    acceptance: AcceptanceSummaryRecord | None = None
    if acceptance_result is not None:
        acceptance, acceptance_ref = acceptance_result
        evidence["acceptance_summary"] = _evidence_binding(
            acceptance_ref,
            acceptance_payload,
        )
        if acceptance.status is AcceptanceStatus.PASS:
            derived["millefeuille-acceptance-passed"] = list(
                _TAG_EVIDENCE_REQUIREMENTS["millefeuille-acceptance-passed"]
            )
            derived["millefeuille-ready-for-classification"] = list(
                _TAG_EVIDENCE_REQUIREMENTS["millefeuille-ready-for-classification"]
            )

    classification = _validate_classification_evidence(
        classification_plan_payload=classification_plan_payload,
        classification_decision_payload=classification_decision_payload,
        taxonomy_lock=taxonomy_lock,
        stage_manifest=stage_manifest,
        artifact_index=artifact_index,
        stage_status=stage_statuses.get(StageName.CLASSIFY.value),
        acceptance=acceptance,
    )
    if classification is not None:
        plan_record, decision_record, plan_ref, decision_ref = classification
        if (
            classification_plan_payload is None
            or classification_decision_payload is None
            or taxonomy_lock is None
        ):
            raise MillefeuilleContractError(
                "classification evidence became incomplete during validation"
            )
        evidence["classification_plan"] = _evidence_binding(
            plan_ref,
            classification_plan_payload,
        )
        evidence["classification_decision"] = _evidence_binding(
            decision_ref,
            classification_decision_payload,
        )
        evidence["taxonomy_lock"] = {
            "artifact_ref": "taxonomy-lock.json",
            "schema_version": taxonomy_lock["schema_version"],
            "content_identity": taxonomy_lock["content_identity"],
            "lock_id": taxonomy_lock["lock_id"],
            "scope_type": taxonomy_lock["scope_type"],
            "scope_id": taxonomy_lock["scope_id"],
            "taxonomy_version": taxonomy_lock["taxonomy_version"],
        }
        if decision_record.status is ClassificationStatus.CLASSIFIED:
            derived["millefeuille-classified"] = list(
                _TAG_EVIDENCE_REQUIREMENTS["millefeuille-classified"]
            )

    if any(
        status == StageStatus.NEEDS_REVIEW.value for status in stage_statuses.values()
    ) or (
        acceptance is not None and acceptance.status is AcceptanceStatus.NEEDS_REVIEW
    ):
        derived["millefeuille-needs-review"] = list(
            _TAG_EVIDENCE_REQUIREMENTS["millefeuille-needs-review"]
        )
    if any(status == StageStatus.FAILED.value for status in stage_statuses.values()):
        derived["millefeuille-error"] = list(
            _TAG_EVIDENCE_REQUIREMENTS["millefeuille-error"]
        )

    proposed_adds = [
        {
            "tag": tag,
            "reason": "canonical-local-evidence-satisfied",
            "evidence": list(refs),
        }
        for tag in _CANONICAL_TAG_ORDER
        if tag in derived and tag not in normalized_tags
        for refs in (derived[tag],)
    ]
    proposed_removes: list[dict[str, Any]] = []
    if remove_selection_after_terminal_success:
        if "millefeuille-classified" not in derived:
            raise MillefeuilleContractError(
                "selection tag removal requires terminal classified evidence"
            )
        if "millefeuille" in normalized_tags:
            proposed_removes.append(
                {
                    "tag": "millefeuille",
                    "reason": "terminal-success-evidence-satisfied",
                    "preconditions": sorted(
                        [
                            "acceptance-pass-revalidated",
                            "classification-decision-revalidated",
                            "taxonomy-lock-revalidated",
                            "zotero-version-rechecked",
                            "separate-approved-live-writeback",
                        ]
                    ),
                }
            )

    removed = {entry["tag"] for entry in proposed_removes}
    preserved_tags = [tag for tag in normalized_tags if tag not in removed]
    observed_legacy = [
        _observed_legacy_tag(tag)
        for tag in normalized_tags
        if _is_legacy_outcome_tag(tag)
    ]
    unsubstantiated = [
        tag
        for tag in normalized_tags
        if tag in CANONICAL_LIFECYCLE_TAGS
        and tag != "millefeuille"
        and tag not in derived
    ]

    payload: dict[str, Any] = {
        "schema_version": LIFECYCLE_TAG_MIGRATION_PLAN_SCHEMA_VERSION,
        "plan_id": plan_id,
        "registry": {
            "registry_id": registry["registry_id"],
            "registry_version": registry["registry_version"],
            "content_identity": registry["content_identity"],
        },
        "item": {
            "item_key": item_key,
            "zotero_version": zotero_version,
            "current_tags": normalized_tags,
        },
        "run": {
            "paper_id": artifact_index.paper_id,
            "run_id": artifact_index.run_id,
            "source_hash": source_hash,
        },
        "evidence": evidence,
        "derived_lifecycle_tags": [
            {"tag": tag, "evidence": list(derived[tag])}
            for tag in _CANONICAL_TAG_ORDER
            if tag in derived
        ],
        "observed_legacy_tags": observed_legacy,
        "unsubstantiated_lifecycle_tags": unsubstantiated,
        "proposed_adds": proposed_adds,
        "proposed_removes": proposed_removes,
        "preserved_tags": preserved_tags,
        "writeback": {
            "mode": "preview",
            "status": "not-executed",
            "expected_zotero_version": zotero_version,
            "external_effects_performed": False,
            "requires_mf160_executor": True,
            "requires_separate_approval": True,
        },
        "policy_assertions": {
            "legacy_tags_used_as_evidence": False,
            "historical_legacy_tags_auto_removed": False,
            "zotero_write_performed": False,
            "docai_pageindex_implies_indexed": False,
            "millefeuille_processed_implies_lifecycle_success": False,
        },
    }
    payload["content_identity"] = _content_identity(payload)
    _validate_lifecycle_tag_migration_plan_structure(payload)
    return payload


def load_lifecycle_tag_migration_plan(
    path: str | Path,
    *,
    stage_manifest_payload: dict[str, Any],
    artifact_index_payload: dict[str, Any],
    acceptance_payload: dict[str, Any] | None = None,
    classification_plan_payload: dict[str, Any] | None = None,
    classification_decision_payload: dict[str, Any] | None = None,
    taxonomy_lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = load_lifecycle_tag_json(path, "lifecycle tag migration plan")
    validate_lifecycle_tag_migration_plan(
        payload,
        stage_manifest_payload=stage_manifest_payload,
        artifact_index_payload=artifact_index_payload,
        acceptance_payload=acceptance_payload,
        classification_plan_payload=classification_plan_payload,
        classification_decision_payload=classification_decision_payload,
        taxonomy_lock=taxonomy_lock,
    )
    return payload


def validate_lifecycle_tag_migration_plan(
    payload: dict[str, Any],
    *,
    stage_manifest_payload: dict[str, Any],
    artifact_index_payload: dict[str, Any],
    acceptance_payload: dict[str, Any] | None = None,
    classification_plan_payload: dict[str, Any] | None = None,
    classification_decision_payload: dict[str, Any] | None = None,
    taxonomy_lock: dict[str, Any] | None = None,
) -> None:
    """Validate a plan by rebuilding it from the supplied evidence bundle."""

    _validate_lifecycle_tag_migration_plan_structure(payload)
    item = payload["item"]
    expected = build_lifecycle_tag_migration_plan(
        registry=canonical_lifecycle_tag_registry(),
        plan_id=payload["plan_id"],
        item_key=item["item_key"],
        zotero_version=item["zotero_version"],
        current_tags=item["current_tags"],
        stage_manifest_payload=stage_manifest_payload,
        artifact_index_payload=artifact_index_payload,
        acceptance_payload=acceptance_payload,
        classification_plan_payload=classification_plan_payload,
        classification_decision_payload=classification_decision_payload,
        taxonomy_lock=taxonomy_lock,
        remove_selection_after_terminal_success=bool(payload["proposed_removes"]),
    )
    if payload != expected:
        raise MillefeuilleContractError(
            "lifecycle tag migration plan drifts from rederived evidence"
        )


def _validate_lifecycle_tag_migration_plan_structure(payload: dict[str, Any]) -> None:
    """Validate strict shape, deterministic ordering, and content identity."""

    if type(payload) is not dict:
        raise MillefeuilleContractError(
            "lifecycle tag migration plan must be an object"
        )
    _reject_unsafe_strings(payload, "lifecycle tag migration plan")
    _require_exact_fields(
        payload,
        {
            "schema_version",
            "plan_id",
            "registry",
            "item",
            "run",
            "evidence",
            "derived_lifecycle_tags",
            "observed_legacy_tags",
            "unsubstantiated_lifecycle_tags",
            "proposed_adds",
            "proposed_removes",
            "preserved_tags",
            "writeback",
            "policy_assertions",
            "content_identity",
        },
        "lifecycle tag migration plan",
    )
    if payload.get("schema_version") != LIFECYCLE_TAG_MIGRATION_PLAN_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "lifecycle tag migration plan schema_version is unsupported"
        )
    _safe_identity(payload.get("plan_id"), "plan_id")
    registry = _required_object(payload.get("registry"), "registry")
    _require_exact_fields(
        registry,
        {"registry_id", "registry_version", "content_identity"},
        "registry",
    )
    canonical_registry = canonical_lifecycle_tag_registry()
    if registry != {
        "registry_id": canonical_registry["registry_id"],
        "registry_version": canonical_registry["registry_version"],
        "content_identity": canonical_registry["content_identity"],
    }:
        raise MillefeuilleContractError("migration plan registry binding is invalid")

    item = _required_object(payload.get("item"), "item")
    _require_exact_fields(item, {"item_key", "zotero_version", "current_tags"}, "item")
    _safe_identity(item.get("item_key"), "item.item_key")
    if (
        isinstance(item.get("zotero_version"), bool)
        or not isinstance(item.get("zotero_version"), int)
        or item["zotero_version"] < 0
        or item["zotero_version"] > 9_007_199_254_740_991
    ):
        raise MillefeuilleContractError("item.zotero_version is invalid")
    if item.get("current_tags") != _canonical_current_tags(item.get("current_tags")):
        raise MillefeuilleContractError("item.current_tags is not canonical")

    run = _required_object(payload.get("run"), "run")
    _require_exact_fields(run, {"paper_id", "run_id", "source_hash"}, "run")
    _safe_identity(run.get("paper_id"), "run.paper_id")
    _safe_identity(run.get("run_id"), "run.run_id")
    if (
        not isinstance(run.get("source_hash"), str)
        or _DIGEST_RE.fullmatch(run["source_hash"]) is None
    ):
        raise MillefeuilleContractError("run.source_hash is invalid")

    evidence = _required_object(payload.get("evidence"), "evidence")
    _require_exact_fields(
        evidence,
        {
            "stage_manifest",
            "artifact_index",
            "acceptance_summary",
            "classification_plan",
            "classification_decision",
            "taxonomy_lock",
        },
        "evidence",
    )
    for name, binding in evidence.items():
        if binding is None:
            continue
        binding = _required_object(binding, f"evidence.{name}")
        required = {"artifact_ref", "schema_version", "content_identity"}
        if name == "taxonomy_lock":
            required |= {
                "lock_id",
                "scope_type",
                "scope_id",
                "taxonomy_version",
            }
        _require_exact_fields(binding, required, f"evidence.{name}")
        _safe_artifact_ref(binding.get("artifact_ref"), f"evidence.{name}.artifact_ref")
        if name in _FIXED_EVIDENCE_REFS and (
            binding.get("artifact_ref") != _FIXED_EVIDENCE_REFS[name]
        ):
            raise MillefeuilleContractError(
                f"evidence.{name}.artifact_ref is not canonical"
            )
        if binding.get("schema_version") != _EVIDENCE_SCHEMA_VERSIONS[name]:
            raise MillefeuilleContractError(
                f"evidence.{name}.schema_version is unsupported"
            )
        if not isinstance(binding.get("content_identity"), str) or (
            _CONTENT_DIGEST_RE.fullmatch(binding["content_identity"]) is None
        ):
            raise MillefeuilleContractError(
                f"evidence.{name}.content_identity is invalid"
            )
        if name == "taxonomy_lock" and (
            binding.get("scope_type") != "single-run"
            or binding.get("scope_id") != run["run_id"]
        ):
            raise MillefeuilleContractError(
                "evidence.taxonomy_lock must bind this exact single run"
            )
        if name == "taxonomy_lock":
            _safe_identity(binding.get("lock_id"), "evidence.taxonomy_lock.lock_id")
            _safe_identity(
                binding.get("taxonomy_version"),
                "evidence.taxonomy_lock.taxonomy_version",
            )

    derived = _tag_evidence_entries(
        payload.get("derived_lifecycle_tags"),
        "derived_lifecycle_tags",
        allow_reasons=False,
    )
    derived_tags = [entry["tag"] for entry in derived]
    if derived_tags != [tag for tag in _CANONICAL_TAG_ORDER if tag in derived_tags]:
        raise MillefeuilleContractError("derived lifecycle tags are not canonical")
    for entry in derived:
        expected_refs = list(_TAG_EVIDENCE_REQUIREMENTS[entry["tag"]])
        if entry["evidence"] != expected_refs:
            raise MillefeuilleContractError(
                "derived lifecycle tag evidence mapping drift"
            )
        if any(evidence[reference] is None for reference in expected_refs):
            raise MillefeuilleContractError(
                "derived lifecycle tag references missing evidence"
            )
    additions = _tag_evidence_entries(
        payload.get("proposed_adds"), "proposed_adds", allow_reasons=True
    )
    expected_additions = [
        {
            "tag": entry["tag"],
            "reason": "canonical-local-evidence-satisfied",
            "evidence": entry["evidence"],
        }
        for entry in derived
        if entry["tag"] not in item["current_tags"]
    ]
    if additions != expected_additions:
        raise MillefeuilleContractError(
            "proposed additions drift from exact derived/current state"
        )

    removals = payload.get("proposed_removes")
    if not isinstance(removals, list) or len(removals) > 1:
        raise MillefeuilleContractError("proposed_removes is invalid")
    for removal in removals:
        removal = _required_object(removal, "proposed_removes entry")
        _require_exact_fields(
            removal,
            {"tag", "reason", "preconditions"},
            "proposed_removes entry",
        )
        if removal.get("tag") != "millefeuille":
            raise MillefeuilleContractError(
                "only the selection tag can be proposed for automatic migration removal"
            )
        if "millefeuille-classified" not in derived_tags:
            raise MillefeuilleContractError(
                "selection removal lacks terminal success evidence"
            )
        if "millefeuille" not in item["current_tags"]:
            raise MillefeuilleContractError(
                "selection removal requires the exact current selection tag"
            )
        if removal.get("reason") != "terminal-success-evidence-satisfied":
            raise MillefeuilleContractError("selection removal reason is invalid")
        expected_preconditions = sorted(
            [
                "acceptance-pass-revalidated",
                "classification-decision-revalidated",
                "taxonomy-lock-revalidated",
                "zotero-version-rechecked",
                "separate-approved-live-writeback",
            ]
        )
        if removal.get("preconditions") != expected_preconditions:
            raise MillefeuilleContractError("selection removal preconditions drift")

    observed_legacy = payload.get("observed_legacy_tags")
    if not isinstance(observed_legacy, list):
        raise MillefeuilleContractError("observed_legacy_tags must be an array")
    expected_legacy = [
        _observed_legacy_tag(tag)
        for tag in item["current_tags"]
        if _is_legacy_outcome_tag(tag)
    ]
    if observed_legacy != expected_legacy:
        raise MillefeuilleContractError("observed legacy-tag policy drift")
    unsubstantiated = _canonical_string_list(
        payload.get("unsubstantiated_lifecycle_tags"),
        "unsubstantiated_lifecycle_tags",
    )
    if not set(unsubstantiated).issubset(CANONICAL_LIFECYCLE_TAGS):
        raise MillefeuilleContractError("unsubstantiated lifecycle tag is unknown")
    expected_unsubstantiated = [
        tag
        for tag in item["current_tags"]
        if tag in CANONICAL_LIFECYCLE_TAGS
        and tag != "millefeuille"
        and tag not in derived_tags
    ]
    if unsubstantiated != expected_unsubstantiated:
        raise MillefeuilleContractError(
            "unsubstantiated lifecycle tags drift from exact evidence"
        )
    preserved = _canonical_current_tags(payload.get("preserved_tags"))
    removal_tags = {entry["tag"] for entry in removals}
    if preserved != [tag for tag in item["current_tags"] if tag not in removal_tags]:
        raise MillefeuilleContractError("preserved_tags drift from exact current state")

    writeback = _required_object(payload.get("writeback"), "writeback")
    if writeback != {
        "mode": "preview",
        "status": "not-executed",
        "expected_zotero_version": item["zotero_version"],
        "external_effects_performed": False,
        "requires_mf160_executor": True,
        "requires_separate_approval": True,
    }:
        raise MillefeuilleContractError("migration plan writeback boundary drift")
    assertions = _required_object(payload.get("policy_assertions"), "policy_assertions")
    if assertions != {
        "legacy_tags_used_as_evidence": False,
        "historical_legacy_tags_auto_removed": False,
        "zotero_write_performed": False,
        "docai_pageindex_implies_indexed": False,
        "millefeuille_processed_implies_lifecycle_success": False,
    }:
        raise MillefeuilleContractError("migration policy assertions drift")
    digest = payload.get("content_identity")
    if not isinstance(digest, str) or _CONTENT_DIGEST_RE.fullmatch(digest) is None:
        raise MillefeuilleContractError("migration plan content_identity is invalid")
    if not hmac.compare_digest(digest, _content_identity(payload)):
        raise MillefeuilleContractError("migration plan content_identity mismatch")


def _validate_stage_artifact_join(
    manifest: StageManifest,
    artifact_index: ArtifactIndex,
) -> str:
    if manifest.run_id != artifact_index.run_id:
        raise MillefeuilleContractError(
            "stage manifest and artifact index run_id drift"
        )
    manifest_ref = _require_indexed_artifact(
        artifact_index,
        name="stage_manifest",
        kind="stage-manifest",
        stage=StageName.DISCOVER.value,
        private_content=False,
        expected_ref=_FIXED_EVIDENCE_REFS["stage_manifest"],
    )
    stage_map: dict[str, str] = {}
    for stage in manifest.stages:
        name = stage.name.value
        if name in stage_map:
            raise MillefeuilleContractError("stage manifest contains duplicate stages")
        stage_map[name] = stage.status.value
    artifact_map = {
        name: str(record["status"]) for name, record in artifact_index.stages.items()
    }
    if stage_map != artifact_map:
        raise MillefeuilleContractError(
            "stage manifest and artifact index stage status maps drift"
        )
    for name, record in artifact_index.stages.items():
        _require_exact_fields(
            record,
            {"status", "manifest_ref"},
            f"artifact index stage {name}",
        )
        if record.get("manifest_ref") != manifest_ref:
            raise MillefeuilleContractError("artifact index stage manifest_ref drift")
    return manifest_ref


def _derive_index_tag(
    artifact_index: ArtifactIndex,
    stage_statuses: dict[str, str],
    derived: dict[str, list[str]],
) -> None:
    if stage_statuses.get(StageName.INDEX.value) != StageStatus.PASSED.value:
        return
    if not artifact_index.indexes:
        raise MillefeuilleContractError(
            "passed index stage requires explicit lane evidence"
        )
    for lane in artifact_index.indexes:
        status = lane["status"]
        if status == "written":
            if not isinstance(lane.get("result_ref"), str) or not lane["result_ref"]:
                raise MillefeuilleContractError(
                    "written index lane requires result_ref"
                )
            _safe_artifact_ref(lane["result_ref"], "index result_ref")
        elif status == "skipped":
            if not isinstance(lane.get("skip_reason"), str) or not lane["skip_reason"]:
                raise MillefeuilleContractError(
                    "skipped index lane requires skip_reason"
                )
        else:
            raise MillefeuilleContractError(
                "passed index stage contains a non-final lane"
            )
    derived["millefeuille-indexed"] = list(
        _TAG_EVIDENCE_REQUIREMENTS["millefeuille-indexed"]
    )


def _validate_acceptance_evidence(
    payload: dict[str, Any] | None,
    *,
    stage_manifest: StageManifest,
    artifact_index: ArtifactIndex,
    stage_status: str | None,
) -> tuple[AcceptanceSummaryRecord, str] | None:
    if payload is None:
        if (
            stage_status == StageStatus.PASSED.value
            or "acceptance_summary" in artifact_index.artifacts
        ):
            raise MillefeuilleContractError(
                "passed acceptance stage requires acceptance summary evidence"
            )
        return None
    acceptance_ref = _require_indexed_artifact(
        artifact_index,
        name="acceptance_summary",
        kind="acceptance-summary",
        stage=StageName.ACCEPTANCE.value,
        private_content=False,
        expected_ref=_FIXED_EVIDENCE_REFS["acceptance_summary"],
    )
    _require_stage_output(
        stage_manifest,
        stage=StageName.ACCEPTANCE,
        artifact_ref=acceptance_ref,
    )
    record = AcceptanceSummaryRecord.from_dict(deepcopy(payload))
    if record.to_dict() != payload:
        raise MillefeuilleContractError(
            "acceptance summary is not an exact wire record"
        )
    _require_run_identity(
        paper_id=record.paper_id,
        run_id=record.run_id,
        source_hash=record.source_hash,
        artifact_index=artifact_index,
        label="acceptance summary",
    )
    expected_stage = (
        StageStatus.PASSED.value
        if record.status is AcceptanceStatus.PASS
        else StageStatus.NEEDS_REVIEW.value
    )
    if stage_status != expected_stage:
        raise MillefeuilleContractError("acceptance summary and stage status drift")
    if record.status is AcceptanceStatus.PASS and any(
        check.status is AcceptanceCheckStatus.NEEDS_REVIEW for check in record.checks
    ):
        raise MillefeuilleContractError(
            "passing acceptance summary cannot contain needs-review checks"
        )
    return record, acceptance_ref


def _validate_classification_evidence(
    *,
    classification_plan_payload: dict[str, Any] | None,
    classification_decision_payload: dict[str, Any] | None,
    taxonomy_lock: dict[str, Any] | None,
    stage_manifest: StageManifest,
    artifact_index: ArtifactIndex,
    stage_status: str | None,
    acceptance: AcceptanceSummaryRecord | None,
) -> (
    tuple[
        ClassificationPlanRecord,
        ClassificationDecisionRecord,
        str,
        str,
    ]
    | None
):
    supplied = (
        classification_plan_payload is not None,
        classification_decision_payload is not None,
        taxonomy_lock is not None,
    )
    if not any(supplied):
        if stage_status == StageStatus.PASSED.value or any(
            name in artifact_index.artifacts
            for name in ("classification_plan", "classification_decision")
        ):
            raise MillefeuilleContractError(
                "passed classify stage requires plan, decision, and taxonomy lock"
            )
        return None
    if not all(supplied):
        raise MillefeuilleContractError(
            "classification evidence requires plan, decision, and taxonomy lock "
            "together"
        )
    if (
        classification_plan_payload is None
        or classification_decision_payload is None
        or taxonomy_lock is None
    ):
        raise MillefeuilleContractError("classification evidence is incomplete")
    plan_ref = _require_indexed_artifact(
        artifact_index,
        name="classification_plan",
        kind="classification-plan",
        stage=StageName.CLASSIFY.value,
        private_content=False,
        expected_ref=_FIXED_EVIDENCE_REFS["classification_plan"],
    )
    decision_ref = _require_indexed_artifact(
        artifact_index,
        name="classification_decision",
        kind="classification-decision",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    _require_stage_output(
        stage_manifest,
        stage=StageName.CLASSIFY,
        artifact_ref=plan_ref,
    )
    _require_stage_output(
        stage_manifest,
        stage=StageName.CLASSIFY,
        artifact_ref=decision_ref,
    )
    plan = ClassificationPlanRecord.from_dict(deepcopy(classification_plan_payload))
    decision = ClassificationDecisionRecord.from_dict(
        deepcopy(classification_decision_payload)
    )
    if plan.to_dict() != classification_plan_payload:
        raise MillefeuilleContractError(
            "classification plan is not an exact wire record"
        )
    if decision.to_dict() != classification_decision_payload:
        raise MillefeuilleContractError(
            "classification decision is not an exact wire record"
        )
    paper_entry = plan.papers[0]
    required_paper_fields = {
        "paper_id",
        "decision_ref",
        "decision_markdown_ref",
        "status",
        "writeback_preview_ref",
    }
    if set(paper_entry) not in (
        required_paper_fields,
        required_paper_fields | {"action_ref"},
    ):
        raise MillefeuilleContractError(
            "classification plan paper fields are not canonical"
        )
    for field_name in (
        "decision_ref",
        "decision_markdown_ref",
        "writeback_preview_ref",
        "action_ref",
    ):
        if field_name in paper_entry:
            _safe_artifact_ref(
                paper_entry[field_name],
                f"classification plan {field_name}",
            )
    if paper_entry.get("status") != decision.status.value:
        raise MillefeuilleContractError("classification plan and decision status drift")
    if plan.run_id != artifact_index.run_id:
        raise MillefeuilleContractError("classification plan run_id drift")
    if (
        len(plan.papers) != 1
        or plan.papers[0].get("paper_id") != artifact_index.paper_id
    ):
        raise MillefeuilleContractError(
            "classification plan must bind exactly the migration paper"
        )
    if plan.papers[0].get("decision_ref") != decision_ref:
        raise MillefeuilleContractError(
            "classification plan decision_ref drifts from artifact index"
        )
    _require_run_identity(
        paper_id=decision.paper_id,
        run_id=decision.run_id,
        source_hash=decision.source_hash,
        artifact_index=artifact_index,
        label="classification decision",
    )
    validated_lock = deepcopy(taxonomy_lock)
    validate_taxonomy_lock(validated_lock)
    if validated_lock["scope_type"] != "single-run" or (
        validated_lock["scope_id"] != artifact_index.run_id
    ):
        raise MillefeuilleContractError("taxonomy lock must bind this exact single run")
    taxonomy_version = validated_lock["taxonomy_version"]
    if plan.taxonomy_version != taxonomy_version or decision.taxonomy_version != (
        taxonomy_version
    ):
        raise MillefeuilleContractError(
            "classification evidence and taxonomy lock version drift"
        )
    if plan.mode is not decision.mode:
        raise MillefeuilleContractError("classification plan and decision mode drift")
    expected_stage = (
        StageStatus.PASSED.value
        if decision.status is ClassificationStatus.CLASSIFIED
        else StageStatus.NEEDS_REVIEW.value
    )
    if stage_status != expected_stage:
        raise MillefeuilleContractError(
            "classification decision and stage status drift"
        )
    if decision.status is ClassificationStatus.CLASSIFIED and (
        acceptance is None or acceptance.status is not AcceptanceStatus.PASS
    ):
        raise MillefeuilleContractError(
            "classified tag requires passing acceptance evidence"
        )
    return plan, decision, plan_ref, decision_ref


def _require_indexed_artifact(
    artifact_index: ArtifactIndex,
    *,
    name: str,
    kind: str,
    stage: str,
    private_content: bool,
    expected_ref: str | None = None,
) -> str:
    record = artifact_index.artifacts.get(name)
    if type(record) is not dict:
        raise MillefeuilleContractError(
            f"artifact index is missing canonical {name} evidence"
        )
    _require_exact_fields(
        record,
        {"kind", "ref", "format", "stage", "private_content"},
        f"artifact index {name}",
    )
    if (
        record.get("kind") != kind
        or record.get("format") != "json"
        or record.get("stage") != stage
        or record.get("private_content") is not private_content
    ):
        raise MillefeuilleContractError(f"artifact index {name} metadata drift")
    artifact_ref = record.get("ref")
    if not isinstance(artifact_ref, str):
        raise MillefeuilleContractError(f"artifact index {name}.ref is invalid")
    _safe_artifact_ref(artifact_ref, f"artifact index {name}.ref")
    if expected_ref is not None and artifact_ref != expected_ref:
        raise MillefeuilleContractError(f"artifact index {name}.ref drift")
    return artifact_ref


def _require_stage_output(
    stage_manifest: StageManifest,
    *,
    stage: StageName,
    artifact_ref: str,
) -> None:
    record = next(
        (candidate for candidate in stage_manifest.stages if candidate.name is stage),
        None,
    )
    if record is None or artifact_ref not in record.outputs:
        raise MillefeuilleContractError(
            f"{stage.value} stage outputs do not bind indexed evidence"
        )


def _require_run_identity(
    *,
    paper_id: str,
    run_id: str,
    source_hash: str,
    artifact_index: ArtifactIndex,
    label: str,
) -> None:
    if (
        paper_id != artifact_index.paper_id
        or run_id != artifact_index.run_id
        or source_hash != artifact_index.source_pack["source_hash"]
    ):
        raise MillefeuilleContractError(f"{label} paper/run/source identity drift")


def _evidence_binding(artifact_ref: str, payload: dict[str, Any]) -> dict[str, str]:
    _safe_artifact_ref(artifact_ref, "evidence artifact_ref")
    schema_version = payload.get("schema_version")
    _required_text(schema_version, "evidence schema_version")
    return {
        "artifact_ref": artifact_ref,
        "schema_version": schema_version,
        "content_identity": _canonical_payload_identity(payload),
    }


def _canonical_payload_identity(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _content_identity(payload: dict[str, Any]) -> str:
    body = deepcopy(payload)
    body.pop("content_identity", None)
    return _canonical_payload_identity(body)


def _canonical_json_bytes(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise MillefeuilleContractError(
            "lifecycle tag data cannot be canonicalized"
        ) from exc


def _canonical_current_tags(values: object) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise MillefeuilleContractError("current tags must be an array")
    normalized: list[str] = []
    for value in values:
        text = _required_text(value, "current tag", maximum=128)
        normalized.append(text)
    if len(normalized) > 256:
        raise MillefeuilleContractError("current tags exceed the bounded limit")
    if len(normalized) != len(set(normalized)):
        raise MillefeuilleContractError("current tags must be unique")
    return sorted(normalized)


def _is_legacy_outcome_tag(tag: str) -> bool:
    return tag == "millefeuille-processed" or tag.startswith("docai")


def _observed_legacy_tag(tag: str) -> dict[str, Any]:
    if tag == "millefeuille-processed":
        policy = _LEGACY_POLICY[0]
        policy_id = "legacy-millefeuille-processed"
    elif tag.startswith("docai"):
        policy = _LEGACY_POLICY[1]
        policy_id = "legacy-docai-family"
    else:
        raise MillefeuilleContractError("legacy observation tag is unsupported")
    return {
        "tag": tag,
        "policy_id": policy_id,
        "observed_meaning": policy["observed_meaning"],
        "canonical_mapping": None,
        "removal_policy": "preserve-history",
    }


def _tag_evidence_entries(
    value: object,
    label: str,
    *,
    allow_reasons: bool,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise MillefeuilleContractError(f"{label} must be an array")
    if len(value) > len(CANONICAL_LIFECYCLE_TAGS):
        raise MillefeuilleContractError(f"{label} exceeds the bounded limit")
    entries: list[dict[str, Any]] = []
    for raw in value:
        entry = _required_object(raw, f"{label} entry")
        required = {"tag", "evidence"}
        if allow_reasons:
            required.add("reason")
        _require_exact_fields(entry, required, f"{label} entry")
        tag = _required_text(entry.get("tag"), f"{label}.tag", maximum=128)
        if tag not in CANONICAL_LIFECYCLE_TAGS or tag == "millefeuille":
            raise MillefeuilleContractError(f"{label} contains an invalid tag")
        refs = _canonical_string_list(entry.get("evidence"), f"{label}.evidence")
        if not refs:
            raise MillefeuilleContractError(f"{label}.evidence must not be empty")
        if allow_reasons and entry.get("reason") != (
            "canonical-local-evidence-satisfied"
        ):
            raise MillefeuilleContractError(f"{label}.reason is invalid")
        entries.append(entry)
    tags = [entry["tag"] for entry in entries]
    if len(tags) != len(set(tags)):
        raise MillefeuilleContractError(f"{label} tags must be unique")
    return entries


def _canonical_string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise MillefeuilleContractError(f"{label} must be an array")
    if len(value) > 256:
        raise MillefeuilleContractError(f"{label} exceeds the bounded limit")
    values = [_required_text(entry, label, maximum=256) for entry in value]
    if values != sorted(set(values)):
        raise MillefeuilleContractError(f"{label} must be sorted and unique")
    return values


def _safe_artifact_ref(value: object, label: str) -> None:
    text = _required_text(value, label, maximum=512)
    if "\\" in text or "%" in text or ":" in text:
        raise MillefeuilleContractError(
            f"{label} must be a plain repository-relative path"
        )
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise MillefeuilleContractError(f"{label} must be a safe relative path")
    if path.as_posix() != text or any(
        _SAFE_ARTIFACT_SEGMENT_RE.fullmatch(part) is None for part in path.parts
    ):
        raise MillefeuilleContractError(f"{label} must be normalized")


def _safe_identity(value: object, label: str) -> None:
    text = _required_text(value, label, maximum=256)
    if _SAFE_ID_RE.fullmatch(text) is None:
        raise MillefeuilleContractError(f"{label} is not a safe identity")


def _required_text(value: object, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MillefeuilleContractError(f"{label} must be an unpadded string")
    if len(value) > maximum:
        raise MillefeuilleContractError(f"{label} exceeds the length limit")
    if unicodedata.normalize("NFC", value) != value:
        raise MillefeuilleContractError(f"{label} must use NFC Unicode")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise MillefeuilleContractError(f"{label} contains a control character")
    return value


def _required_object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise MillefeuilleContractError(f"{label} must be an object")
    return value


def _require_exact_fields(
    value: dict[str, Any],
    expected: set[str],
    label: str,
) -> None:
    if set(value) != expected:
        raise MillefeuilleContractError(f"{label} fields are not exact")


def _reject_unsafe_strings(value: object, label: str) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    visited_containers: set[int] = set()
    visited_nodes = 0
    while stack:
        current, depth = stack.pop()
        visited_nodes += 1
        if visited_nodes > LIFECYCLE_TAG_MAX_NODES:
            raise MillefeuilleContractError(
                f"{label} exceeds the structured-data node limit"
            )
        if isinstance(current, str):
            _required_text(current, label)
            continue
        if not isinstance(current, dict | list | tuple):
            continue
        if depth >= LIFECYCLE_TAG_MAX_NESTING:
            raise MillefeuilleContractError(
                f"{label} exceeds the structured-data nesting limit"
            )
        object_id = id(current)
        if object_id in visited_containers:
            raise MillefeuilleContractError(
                f"{label} contains a cyclic or aliased container"
            )
        visited_containers.add(object_id)
        if isinstance(current, dict):
            for key, nested in current.items():
                _required_text(key, f"{label} field name", maximum=128)
                stack.append((nested, depth + 1))
        else:
            stack.extend((nested, depth + 1) for nested in current)


def _object_without_duplicate_fields(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name, value in pairs:
        if field_name in payload:
            raise MillefeuilleContractError(
                "lifecycle tag JSON contains duplicate fields"
            )
        payload[field_name] = value
    return payload


def _reject_non_finite_json_number(value: str) -> None:
    del value
    raise MillefeuilleContractError("lifecycle tag JSON contains a non-finite number")
