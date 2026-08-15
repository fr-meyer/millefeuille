"""Deterministic, offline taxonomy registry governance.

The helpers in this module validate and derive JSON artifacts only.  They do
not generate taxonomy labels, select an active registry globally, mutate an
existing registry or batch lock, call a provider, or perform a live write.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
import hashlib
from itertools import islice
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import load_json_object_no_follow

TAXONOMY_REGISTRY_SCHEMA_VERSION = "millefeuille-taxonomy-registry/v0.1"
TAXONOMY_LOCK_SCHEMA_VERSION = "millefeuille-taxonomy-lock/v0.1"
TAXONOMY_CHANGE_PROPOSAL_SCHEMA_VERSION = "millefeuille-taxonomy-change-proposal/v0.1"
TAXONOMY_CHANGE_REVIEW_SCHEMA_VERSION = "millefeuille-taxonomy-change-review/v0.1"
TAXONOMY_APPLICATION_SCHEMA_VERSION = "millefeuille-taxonomy-application/v0.1"

REQUIRED_REVIEW_ROLES = frozenset({"taxonomy-owner", "qa-lead", "operations-lead"})
OPTIONAL_REVIEW_ROLES = frozenset({"subject-matter-reviewer"})

MAX_TAXONOMY_ENTRIES = 512
MAX_TAXONOMY_ENTRY_RULES = 32
MAX_TAXONOMY_AFFECTED_ENTRY_IDS = MAX_TAXONOMY_ENTRIES
MAX_TAXONOMY_EVIDENCE_REFS = 64
MAX_TAXONOMY_REVIEWS = len(REQUIRED_REVIEW_ROLES | OPTIONAL_REVIEW_ROLES)

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_ENTRY_ID = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}")
_CONTENT_IDENTITY = re.compile(r"sha256:[0-9a-f]{64}")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def canonical_content_identity(
    payload: Mapping[str, Any],
    *,
    identity_field: str = "content_identity",
) -> str:
    """Return the SHA-256 identity of canonical JSON excluding its identity."""

    canonical_payload = {
        key: deepcopy(value) for key, value in payload.items() if key != identity_field
    }
    try:
        canonical = json.dumps(
            canonical_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MillefeuilleContractError(
            "content-addressed artifact must contain canonical JSON values"
        ) from exc
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def seal_taxonomy_registry(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize an explicit registry draft and attach its content identity."""

    draft = _mapping_copy(payload, "taxonomy registry draft")
    if "content_identity" in draft:
        raise MillefeuilleContractError(
            "taxonomy registry draft must omit content_identity"
        )
    entries = draft.get("entries")
    if not isinstance(entries, list):
        raise MillefeuilleContractError("taxonomy registry entries must be an array")
    if len(entries) > MAX_TAXONOMY_ENTRIES:
        raise MillefeuilleContractError(
            "taxonomy registry entries must contain at most "
            f"{MAX_TAXONOMY_ENTRIES} entries"
        )
    canonical_entries: list[dict[str, Any]] = []
    for index, value in enumerate(entries):
        entry = _mapping_copy(value, f"taxonomy entry {index}")
        for field_name in (
            "include_when",
            "exclude_when",
            "boundary_notes",
        ):
            field_value = entry.get(field_name)
            if isinstance(field_value, list):
                entry[field_name] = _canonical_string_sequence(
                    field_value,
                    f"taxonomy entry {index} {field_name}",
                    maximum=MAX_TAXONOMY_ENTRY_RULES,
                )
        canonical_entries.append(entry)
    draft["entries"] = sorted(
        canonical_entries,
        key=lambda entry: str(entry.get("entry_id", "")),
    )
    draft["content_identity"] = canonical_content_identity(draft)
    return validate_taxonomy_registry(draft)


def validate_taxonomy_registry(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one immutable taxonomy registry version."""

    registry = _mapping_copy(payload, "taxonomy registry")
    _require_exact_fields(
        registry,
        required={
            "schema_version",
            "registry_id",
            "taxonomy_version",
            "previous_version",
            "previous_content_identity",
            "status",
            "governing_basis",
            "owner_id",
            "entries",
            "content_identity",
        },
        label="taxonomy registry",
    )
    _require_const(
        registry["schema_version"],
        TAXONOMY_REGISTRY_SCHEMA_VERSION,
        "taxonomy registry schema_version",
    )
    _require_safe_id(registry["registry_id"], "taxonomy registry registry_id")
    _require_safe_id(registry["taxonomy_version"], "taxonomy registry taxonomy_version")
    previous_version = registry["previous_version"]
    if previous_version is not None:
        _require_safe_id(previous_version, "taxonomy registry previous_version")
        if previous_version == registry["taxonomy_version"]:
            raise MillefeuilleContractError(
                "taxonomy registry previous_version must differ from taxonomy_version"
            )
    previous_content_identity = registry["previous_content_identity"]
    if previous_version is None:
        if previous_content_identity is not None:
            raise MillefeuilleContractError(
                "root taxonomy registry previous_content_identity must be null"
            )
    else:
        if previous_content_identity is None:
            raise MillefeuilleContractError(
                "non-root taxonomy registry requires previous_content_identity"
            )
        _require_identity(
            previous_content_identity,
            "taxonomy registry previous_content_identity",
        )
    if registry["status"] not in {"draft", "released", "deprecated", "archived"}:
        raise MillefeuilleContractError("taxonomy registry status is unsupported")
    _require_string(registry["governing_basis"], "taxonomy registry governing_basis")
    _require_string(registry["owner_id"], "taxonomy registry owner_id")

    raw_entries = registry["entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise MillefeuilleContractError(
            "taxonomy registry entries must be a non-empty array"
        )
    if len(raw_entries) > MAX_TAXONOMY_ENTRIES:
        raise MillefeuilleContractError(
            "taxonomy registry entries must contain at most "
            f"{MAX_TAXONOMY_ENTRIES} entries"
        )
    entries = [
        _validate_taxonomy_entry(value, index=index)
        for index, value in enumerate(raw_entries)
    ]
    entry_ids = [entry["entry_id"] for entry in entries]
    if entry_ids != sorted(entry_ids):
        raise MillefeuilleContractError(
            "taxonomy registry entries must be sorted by entry_id"
        )
    if len(entry_ids) != len(set(entry_ids)):
        raise MillefeuilleContractError(
            "taxonomy registry entries must have unique entry_id values"
        )
    by_id = {entry["entry_id"]: entry for entry in entries}
    sibling_labels: set[tuple[str | None, str]] = set()
    for entry in entries:
        parent_id = entry["parent_id"]
        if entry["level"] == 1:
            if parent_id is not None:
                raise MillefeuilleContractError(
                    "level 1 taxonomy entries must have null parent_id"
                )
        else:
            if parent_id not in by_id or by_id[parent_id]["level"] != 1:
                raise MillefeuilleContractError(
                    "level 2 taxonomy entries must reference an existing level 1 parent"
                )
            if entry["status"] == "active" and by_id[parent_id]["status"] != "active":
                raise MillefeuilleContractError(
                    "active level 2 taxonomy entries require an active parent"
                )
        sibling_key = (parent_id, entry["label"].casefold())
        if sibling_key in sibling_labels:
            raise MillefeuilleContractError(
                "taxonomy registry sibling labels must be unique ignoring case"
            )
        sibling_labels.add(sibling_key)

        replacement_id = entry["replacement_id"]
        if entry["status"] == "active" and replacement_id is not None:
            raise MillefeuilleContractError(
                "active taxonomy entries cannot declare replacement_id"
            )
        if replacement_id is not None:
            replacement = by_id.get(replacement_id)
            if replacement is None or replacement["status"] != "active":
                raise MillefeuilleContractError(
                    "taxonomy replacement_id must reference an active entry"
                )
            if replacement["level"] != entry["level"]:
                raise MillefeuilleContractError(
                    "taxonomy replacement_id must preserve the entry level"
                )

    if registry["status"] == "released":
        active_l1 = {
            entry["entry_id"]
            for entry in entries
            if entry["level"] == 1 and entry["status"] == "active"
        }
        covered_l1 = {
            entry["parent_id"]
            for entry in entries
            if entry["level"] == 2 and entry["status"] == "active"
        }
        if not active_l1 or active_l1 - covered_l1:
            raise MillefeuilleContractError(
                "released taxonomy registries require an active level 2 entry "
                "under every active level 1 entry"
            )

    _require_content_identity(registry, "taxonomy registry")
    registry["entries"] = entries
    return registry


def load_taxonomy_registry(path: str | Path) -> dict[str, Any]:
    """Load and validate a registry without following symbolic links."""

    return validate_taxonomy_registry(
        load_json_object_no_follow(path, "taxonomy registry")
    )


def create_taxonomy_lock(
    registry: Mapping[str, Any],
    *,
    lock_id: str,
    scope_type: str,
    scope_id: str,
    locked_by: str,
    locked_at: str,
) -> dict[str, Any]:
    """Create a self-contained immutable snapshot for a batch or run."""

    validated_registry = validate_taxonomy_registry(registry)
    if validated_registry["status"] != "released":
        raise MillefeuilleContractError(
            "only a released taxonomy registry can be locked"
        )
    payload: dict[str, Any] = {
        "schema_version": TAXONOMY_LOCK_SCHEMA_VERSION,
        "lock_id": lock_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "locked_by": locked_by,
        "locked_at": locked_at,
        "registry_id": validated_registry["registry_id"],
        "taxonomy_version": validated_registry["taxonomy_version"],
        "registry_content_identity": validated_registry["content_identity"],
        "registry_snapshot": deepcopy(validated_registry),
    }
    payload["content_identity"] = canonical_content_identity(payload)
    return validate_taxonomy_lock(payload)


def validate_taxonomy_lock(
    payload: Mapping[str, Any],
    *,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a lock and, optionally, bind it to an external registry file."""

    lock = _mapping_copy(payload, "taxonomy lock")
    _require_exact_fields(
        lock,
        required={
            "schema_version",
            "lock_id",
            "scope_type",
            "scope_id",
            "locked_by",
            "locked_at",
            "registry_id",
            "taxonomy_version",
            "registry_content_identity",
            "registry_snapshot",
            "content_identity",
        },
        label="taxonomy lock",
    )
    _require_const(
        lock["schema_version"],
        TAXONOMY_LOCK_SCHEMA_VERSION,
        "taxonomy lock schema_version",
    )
    _require_safe_id(lock["lock_id"], "taxonomy lock lock_id")
    if lock["scope_type"] not in {"batch", "pilot", "single-run"}:
        raise MillefeuilleContractError("taxonomy lock scope_type is unsupported")
    _require_safe_id(lock["scope_id"], "taxonomy lock scope_id")
    _require_string(lock["locked_by"], "taxonomy lock locked_by")
    _require_timestamp(lock["locked_at"], "taxonomy lock locked_at")
    _require_safe_id(lock["registry_id"], "taxonomy lock registry_id")
    _require_safe_id(lock["taxonomy_version"], "taxonomy lock taxonomy_version")
    _require_identity(
        lock["registry_content_identity"],
        "taxonomy lock registry_content_identity",
    )
    snapshot = validate_taxonomy_registry(lock["registry_snapshot"])
    if snapshot["status"] != "released":
        raise MillefeuilleContractError(
            "taxonomy lock registry snapshot must be released"
        )
    expected_binding = (
        snapshot["registry_id"],
        snapshot["taxonomy_version"],
        snapshot["content_identity"],
    )
    actual_binding = (
        lock["registry_id"],
        lock["taxonomy_version"],
        lock["registry_content_identity"],
    )
    if actual_binding != expected_binding:
        raise MillefeuilleContractError(
            "taxonomy lock registry snapshot identity drift"
        )
    if registry is not None:
        current = validate_taxonomy_registry(registry)
        current_binding = (
            current["registry_id"],
            current["taxonomy_version"],
            current["content_identity"],
        )
        if current_binding != expected_binding:
            raise MillefeuilleContractError(
                "taxonomy lock does not match the supplied registry"
            )
    _require_content_identity(lock, "taxonomy lock")
    lock["registry_snapshot"] = snapshot
    return lock


def load_taxonomy_lock(
    path: str | Path,
    *,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return validate_taxonomy_lock(
        load_json_object_no_follow(path, "taxonomy lock"), registry=registry
    )


def create_taxonomy_change_proposal(
    *,
    base_registry: Mapping[str, Any],
    candidate_registry: Mapping[str, Any],
    change_id: str,
    operation: str,
    affected_entry_ids: Sequence[str],
    reason: str,
    evidence_refs: Sequence[str],
    impact_risk: str,
    impact_summary: str,
    estimated_affected_records: int | None,
    historical_reclassification: str,
    migration_instructions: str,
    requested_by: str,
    requested_at: str,
    rollback_source_registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a proposal that binds one explicit candidate to one exact base."""

    base = validate_taxonomy_registry(base_registry)
    candidate = validate_taxonomy_registry(candidate_registry)
    rollback_source = (
        None
        if rollback_source_registry is None
        else validate_taxonomy_registry(rollback_source_registry)
    )
    if rollback_source is not None and rollback_source["status"] == "draft":
        raise MillefeuilleContractError(
            "rollback source registry must have been released"
        )
    payload: dict[str, Any] = {
        "schema_version": TAXONOMY_CHANGE_PROPOSAL_SCHEMA_VERSION,
        "change_id": change_id,
        "operation": operation,
        "base_registry": _registry_binding(base),
        "candidate_registry": deepcopy(candidate),
        "affected_entry_ids": _canonical_string_sequence(
            affected_entry_ids,
            "taxonomy change proposal affected_entry_ids",
            safe_entry_ids=True,
            maximum=MAX_TAXONOMY_AFFECTED_ENTRY_IDS,
        ),
        "reason": reason,
        "evidence_refs": _canonical_string_sequence(
            evidence_refs,
            "taxonomy change proposal evidence_refs",
            maximum=MAX_TAXONOMY_EVIDENCE_REFS,
        ),
        "impact": {
            "risk": impact_risk,
            "summary": impact_summary,
            "estimated_affected_records": estimated_affected_records,
        },
        "migration": {
            "historical_reclassification": historical_reclassification,
            "instructions": migration_instructions,
            "active_batch_policy": "preserve-locked-version",
            "rollback_source": (
                None if rollback_source is None else _registry_binding(rollback_source)
            ),
        },
        "requested_by": requested_by,
        "requested_at": requested_at,
    }
    payload["content_identity"] = canonical_content_identity(payload)
    validated = validate_taxonomy_change_proposal(payload, base_registry=base)
    if operation == "rollback" and rollback_source is not None:
        _validate_rollback_candidate(
            base=base,
            candidate=validated["candidate_registry"],
            source=rollback_source,
        )
    return validated


def validate_taxonomy_change_proposal(
    payload: Mapping[str, Any],
    *,
    base_registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a governed proposal and its exact, released candidate."""

    proposal = _mapping_copy(payload, "taxonomy change proposal")
    _require_exact_fields(
        proposal,
        required={
            "schema_version",
            "change_id",
            "operation",
            "base_registry",
            "candidate_registry",
            "affected_entry_ids",
            "reason",
            "evidence_refs",
            "impact",
            "migration",
            "requested_by",
            "requested_at",
            "content_identity",
        },
        label="taxonomy change proposal",
    )
    _require_const(
        proposal["schema_version"],
        TAXONOMY_CHANGE_PROPOSAL_SCHEMA_VERSION,
        "taxonomy change proposal schema_version",
    )
    _require_safe_id(proposal["change_id"], "taxonomy change proposal change_id")
    operations = {
        "add",
        "clarify",
        "rename",
        "deprecate",
        "split",
        "merge",
        "mixed",
        "rollback",
    }
    if proposal["operation"] not in operations:
        raise MillefeuilleContractError(
            "taxonomy change proposal operation is unsupported"
        )
    base_binding = _validate_registry_binding(
        proposal["base_registry"], "taxonomy change proposal base_registry"
    )
    validated_base: dict[str, Any] | None = None
    if base_registry is not None:
        validated_base = validate_taxonomy_registry(base_registry)
        if validated_base["status"] != "released":
            raise MillefeuilleContractError(
                "taxonomy change base registry must be released"
            )
        if _registry_binding(validated_base) != base_binding:
            raise MillefeuilleContractError(
                "taxonomy change proposal base registry identity drift"
            )
    candidate = validate_taxonomy_registry(proposal["candidate_registry"])
    if candidate["status"] != "released":
        raise MillefeuilleContractError(
            "taxonomy change candidate_registry must be released"
        )
    if candidate["registry_id"] != base_binding["registry_id"]:
        raise MillefeuilleContractError("taxonomy change candidate registry_id drift")
    if candidate["taxonomy_version"] == base_binding["taxonomy_version"]:
        raise MillefeuilleContractError(
            "taxonomy change candidate must use a new taxonomy_version"
        )
    if candidate["previous_version"] != base_binding["taxonomy_version"]:
        raise MillefeuilleContractError(
            "taxonomy change candidate previous_version must equal the base version"
        )
    if candidate["previous_content_identity"] != base_binding["content_identity"]:
        raise MillefeuilleContractError(
            "taxonomy change candidate must bind the exact base content identity"
        )
    affected_entry_ids = _require_sorted_unique_strings(
        proposal["affected_entry_ids"],
        "taxonomy change proposal affected_entry_ids",
        minimum=1,
        safe_entry_ids=True,
        maximum=MAX_TAXONOMY_AFFECTED_ENTRY_IDS,
    )
    _require_string(proposal["reason"], "taxonomy change proposal reason")
    evidence_refs = _require_sorted_unique_strings(
        proposal["evidence_refs"],
        "taxonomy change proposal evidence_refs",
        minimum=1,
        maximum=MAX_TAXONOMY_EVIDENCE_REFS,
    )
    impact = _mapping_copy(proposal["impact"], "taxonomy change proposal impact")
    _require_exact_fields(
        impact,
        required={"risk", "summary", "estimated_affected_records"},
        label="taxonomy change proposal impact",
    )
    if impact["risk"] not in {"low", "medium", "high"}:
        raise MillefeuilleContractError(
            "taxonomy change proposal impact risk is unsupported"
        )
    _require_string(impact["summary"], "taxonomy change proposal impact summary")
    estimated = impact["estimated_affected_records"]
    if estimated is not None and (
        isinstance(estimated, bool) or not isinstance(estimated, int) or estimated < 0
    ):
        raise MillefeuilleContractError(
            "taxonomy change estimated_affected_records must be null or a "
            "non-negative integer"
        )
    migration = _mapping_copy(
        proposal["migration"], "taxonomy change proposal migration"
    )
    _require_exact_fields(
        migration,
        required={
            "historical_reclassification",
            "instructions",
            "active_batch_policy",
            "rollback_source",
        },
        label="taxonomy change proposal migration",
    )
    if migration["historical_reclassification"] not in {
        "none",
        "review",
        "required",
    }:
        raise MillefeuilleContractError(
            "taxonomy change historical_reclassification is unsupported"
        )
    _require_string(migration["instructions"], "taxonomy change migration instructions")
    _require_const(
        migration["active_batch_policy"],
        "preserve-locked-version",
        "taxonomy change active_batch_policy",
    )
    rollback_source_value = migration["rollback_source"]
    if proposal["operation"] == "rollback":
        if rollback_source_value is None:
            raise MillefeuilleContractError(
                "rollback proposals require a rollback_source registry binding"
            )
        rollback_source = _validate_registry_binding(
            rollback_source_value,
            "taxonomy change proposal rollback_source",
        )
        if rollback_source["registry_id"] != base_binding["registry_id"]:
            raise MillefeuilleContractError("rollback source registry_id drift")
    elif rollback_source_value is not None:
        raise MillefeuilleContractError(
            "non-rollback proposals cannot declare rollback_source"
        )
    _require_string(proposal["requested_by"], "taxonomy change proposal requested_by")
    _require_timestamp(
        proposal["requested_at"], "taxonomy change proposal requested_at"
    )
    _require_content_identity(proposal, "taxonomy change proposal")

    if validated_base is not None:
        _validate_registry_transition(
            base=validated_base,
            candidate=candidate,
            affected_entry_ids=affected_entry_ids,
            operation=str(proposal["operation"]),
        )

    proposal["base_registry"] = base_binding
    proposal["candidate_registry"] = candidate
    proposal["affected_entry_ids"] = affected_entry_ids
    proposal["evidence_refs"] = evidence_refs
    proposal["impact"] = impact
    proposal["migration"] = migration
    return proposal


def load_taxonomy_change_proposal(
    path: str | Path,
    *,
    base_registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return validate_taxonomy_change_proposal(
        load_json_object_no_follow(path, "taxonomy change proposal"),
        base_registry=base_registry,
    )


def create_taxonomy_change_review(
    proposal: Mapping[str, Any],
    *,
    base_registry: Mapping[str, Any],
    review_id: str,
    role: str,
    decision: str,
    reviewer_id: str,
    reviewed_at: str,
    notes: str,
) -> dict[str, Any]:
    """Create an immutable review bound to the proposal and candidate hashes."""

    validated_proposal = validate_taxonomy_change_proposal(
        proposal, base_registry=base_registry
    )
    payload: dict[str, Any] = {
        "schema_version": TAXONOMY_CHANGE_REVIEW_SCHEMA_VERSION,
        "review_id": review_id,
        "proposal_content_identity": validated_proposal["content_identity"],
        "candidate_content_identity": validated_proposal["candidate_registry"][
            "content_identity"
        ],
        "role": role,
        "decision": decision,
        "reviewer_id": reviewer_id,
        "reviewed_at": reviewed_at,
        "notes": notes,
    }
    payload["content_identity"] = canonical_content_identity(payload)
    return validate_taxonomy_change_review(payload, proposal=validated_proposal)


def validate_taxonomy_change_review(
    payload: Mapping[str, Any],
    *,
    proposal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    review = _mapping_copy(payload, "taxonomy change review")
    _require_exact_fields(
        review,
        required={
            "schema_version",
            "review_id",
            "proposal_content_identity",
            "candidate_content_identity",
            "role",
            "decision",
            "reviewer_id",
            "reviewed_at",
            "notes",
            "content_identity",
        },
        label="taxonomy change review",
    )
    _require_const(
        review["schema_version"],
        TAXONOMY_CHANGE_REVIEW_SCHEMA_VERSION,
        "taxonomy change review schema_version",
    )
    _require_safe_id(review["review_id"], "taxonomy change review review_id")
    _require_identity(
        review["proposal_content_identity"],
        "taxonomy change review proposal_content_identity",
    )
    _require_identity(
        review["candidate_content_identity"],
        "taxonomy change review candidate_content_identity",
    )
    if review["role"] not in REQUIRED_REVIEW_ROLES | OPTIONAL_REVIEW_ROLES:
        raise MillefeuilleContractError("taxonomy change review role is unsupported")
    if review["decision"] not in {"approve", "reject"}:
        raise MillefeuilleContractError(
            "taxonomy change review decision is unsupported"
        )
    _require_string(review["reviewer_id"], "taxonomy change review reviewer_id")
    _require_timestamp(review["reviewed_at"], "taxonomy change review reviewed_at")
    _require_string(review["notes"], "taxonomy change review notes")
    _require_content_identity(review, "taxonomy change review")
    if proposal is not None:
        validated_proposal = validate_taxonomy_change_proposal(proposal)
        expected = (
            validated_proposal["content_identity"],
            validated_proposal["candidate_registry"]["content_identity"],
        )
        actual = (
            review["proposal_content_identity"],
            review["candidate_content_identity"],
        )
        if actual != expected:
            raise MillefeuilleContractError(
                "taxonomy change review proposal or candidate identity drift"
            )
        if review["reviewed_at"] < validated_proposal["requested_at"]:
            raise MillefeuilleContractError(
                "taxonomy change review cannot predate its proposal"
            )
        if (
            review["role"] in REQUIRED_REVIEW_ROLES
            and review["reviewer_id"] == validated_proposal["requested_by"]
        ):
            raise MillefeuilleContractError(
                "required taxonomy reviewers must be independent from the requester"
            )
    return review


def load_taxonomy_change_review(
    path: str | Path,
    *,
    proposal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return validate_taxonomy_change_review(
        load_json_object_no_follow(path, "taxonomy change review"),
        proposal=proposal,
    )


def apply_taxonomy_change(
    *,
    base_registry: Mapping[str, Any],
    proposal: Mapping[str, Any],
    reviews: Iterable[Mapping[str, Any]],
    application_id: str,
    applied_by: str,
    applied_at: str,
    rollback_source_registry: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authorize one candidate and return it with an immutable application record."""

    base = validate_taxonomy_registry(base_registry)
    validated_proposal = validate_taxonomy_change_proposal(proposal, base_registry=base)
    candidate = validated_proposal["candidate_registry"]
    review_inputs = list(islice(iter(reviews), MAX_TAXONOMY_REVIEWS + 1))
    if len(review_inputs) > MAX_TAXONOMY_REVIEWS:
        raise MillefeuilleContractError(
            "taxonomy change application accepts at most "
            f"{MAX_TAXONOMY_REVIEWS} governed reviews"
        )
    validated_reviews = [
        validate_taxonomy_change_review(review, proposal=validated_proposal)
        for review in review_inputs
    ]
    _require_approved_reviews(
        validated_reviews,
        requester_id=validated_proposal["requested_by"],
    )

    validated_applied_at = _require_timestamp(
        applied_at, "taxonomy application applied_at"
    )
    if validated_applied_at < validated_proposal["requested_at"] or any(
        validated_applied_at < review["reviewed_at"] for review in validated_reviews
    ):
        raise MillefeuilleContractError(
            "taxonomy application cannot predate its proposal or reviews"
        )

    operation = validated_proposal["operation"]
    if operation == "rollback":
        if rollback_source_registry is None:
            raise MillefeuilleContractError(
                "rollback application requires the exact rollback source registry"
            )
        source = validate_taxonomy_registry(rollback_source_registry)
        if source["status"] == "draft":
            raise MillefeuilleContractError(
                "rollback source registry must have been released"
            )
        source_binding = validated_proposal["migration"]["rollback_source"]
        if _registry_binding(source) != source_binding:
            raise MillefeuilleContractError("rollback source registry identity drift")
        _validate_rollback_candidate(base=base, candidate=candidate, source=source)
    elif rollback_source_registry is not None:
        raise MillefeuilleContractError(
            "non-rollback application cannot supply rollback_source_registry"
        )

    _require_safe_id(application_id, "taxonomy application application_id")
    _require_string(applied_by, "taxonomy application applied_by")
    governance_actor_ids = {
        validated_proposal["requested_by"],
        *(review["reviewer_id"] for review in validated_reviews),
    }
    if applied_by in governance_actor_ids:
        raise MillefeuilleContractError(
            "taxonomy application applied_by must be independent from the requester "
            "and every reviewer"
        )
    record: dict[str, Any] = {
        "schema_version": TAXONOMY_APPLICATION_SCHEMA_VERSION,
        "application_id": application_id,
        "change_id": validated_proposal["change_id"],
        "operation": "rollback" if operation == "rollback" else "apply",
        "status": "rolled-back" if operation == "rollback" else "applied",
        "proposal_content_identity": validated_proposal["content_identity"],
        "base_registry": _registry_binding(base),
        "result_registry": _registry_binding(candidate),
        "review_content_identities": sorted(
            review["content_identity"] for review in validated_reviews
        ),
        "active_batch_policy": "preserve-locked-version",
        "applied_by": applied_by,
        "applied_at": validated_applied_at,
    }
    record["content_identity"] = canonical_content_identity(record)
    return deepcopy(candidate), validate_taxonomy_application_record(record)


def rollback_taxonomy_change(
    *,
    base_registry: Mapping[str, Any],
    rollback_source_registry: Mapping[str, Any],
    proposal: Mapping[str, Any],
    reviews: Iterable[Mapping[str, Any]],
    application_id: str,
    applied_by: str,
    applied_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply an approved rollback as a new forward registry version."""

    validated = validate_taxonomy_change_proposal(proposal, base_registry=base_registry)
    if validated["operation"] != "rollback":
        raise MillefeuilleContractError(
            "rollback requires a taxonomy proposal with operation 'rollback'"
        )
    return apply_taxonomy_change(
        base_registry=base_registry,
        proposal=validated,
        reviews=reviews,
        application_id=application_id,
        applied_by=applied_by,
        applied_at=applied_at,
        rollback_source_registry=rollback_source_registry,
    )


def validate_taxonomy_application_record(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    record = _mapping_copy(payload, "taxonomy application record")
    _require_exact_fields(
        record,
        required={
            "schema_version",
            "application_id",
            "change_id",
            "operation",
            "status",
            "proposal_content_identity",
            "base_registry",
            "result_registry",
            "review_content_identities",
            "active_batch_policy",
            "applied_by",
            "applied_at",
            "content_identity",
        },
        label="taxonomy application record",
    )
    _require_const(
        record["schema_version"],
        TAXONOMY_APPLICATION_SCHEMA_VERSION,
        "taxonomy application schema_version",
    )
    _require_safe_id(record["application_id"], "taxonomy application_id")
    _require_safe_id(record["change_id"], "taxonomy application change_id")
    expected_status = {"apply": "applied", "rollback": "rolled-back"}
    if record["operation"] not in expected_status:
        raise MillefeuilleContractError("taxonomy application operation is unsupported")
    if record["status"] != expected_status[record["operation"]]:
        raise MillefeuilleContractError("taxonomy application status drift")
    _require_identity(
        record["proposal_content_identity"],
        "taxonomy application proposal_content_identity",
    )
    base = _validate_registry_binding(
        record["base_registry"], "taxonomy application base_registry"
    )
    result = _validate_registry_binding(
        record["result_registry"], "taxonomy application result_registry"
    )
    if base["registry_id"] != result["registry_id"]:
        raise MillefeuilleContractError("taxonomy application registry_id drift")
    if base["taxonomy_version"] == result["taxonomy_version"]:
        raise MillefeuilleContractError(
            "taxonomy application must produce a new taxonomy_version"
        )
    reviews = _require_sorted_unique_strings(
        record["review_content_identities"],
        "taxonomy application review_content_identities",
        minimum=len(REQUIRED_REVIEW_ROLES),
        identities=True,
        maximum=MAX_TAXONOMY_REVIEWS,
    )
    _require_const(
        record["active_batch_policy"],
        "preserve-locked-version",
        "taxonomy application active_batch_policy",
    )
    _require_string(record["applied_by"], "taxonomy application applied_by")
    _require_timestamp(record["applied_at"], "taxonomy application applied_at")
    _require_content_identity(record, "taxonomy application record")
    record["base_registry"] = base
    record["result_registry"] = result
    record["review_content_identities"] = reviews
    return record


def _validate_taxonomy_entry(value: Any, *, index: int) -> dict[str, Any]:
    entry = _mapping_copy(value, f"taxonomy entry {index}")
    _require_exact_fields(
        entry,
        required={
            "entry_id",
            "level",
            "parent_id",
            "label",
            "definition",
            "include_when",
            "exclude_when",
            "boundary_notes",
            "status",
            "replacement_id",
        },
        label=f"taxonomy entry {index}",
    )
    _require_entry_id(entry["entry_id"], f"taxonomy entry {index} entry_id")
    if isinstance(entry["level"], bool) or entry["level"] not in {1, 2}:
        raise MillefeuilleContractError(f"taxonomy entry {index} level must be 1 or 2")
    if entry["parent_id"] is not None:
        _require_entry_id(entry["parent_id"], f"taxonomy entry {index} parent_id")
    _require_string(entry["label"], f"taxonomy entry {index} label")
    _require_string(entry["definition"], f"taxonomy entry {index} definition")
    for field_name, minimum in (
        ("include_when", 1),
        ("exclude_when", 1),
        ("boundary_notes", 0),
    ):
        entry[field_name] = _require_sorted_unique_strings(
            entry[field_name],
            f"taxonomy entry {index} {field_name}",
            minimum=minimum,
            maximum=MAX_TAXONOMY_ENTRY_RULES,
        )
    if entry["status"] not in {"active", "deprecated"}:
        raise MillefeuilleContractError(f"taxonomy entry {index} status is unsupported")
    if entry["replacement_id"] is not None:
        _require_entry_id(
            entry["replacement_id"],
            f"taxonomy entry {index} replacement_id",
        )
        if entry["replacement_id"] == entry["entry_id"]:
            raise MillefeuilleContractError(
                f"taxonomy entry {index} cannot replace itself"
            )
    return entry


def _validate_registry_transition(
    *,
    base: Mapping[str, Any],
    candidate: Mapping[str, Any],
    affected_entry_ids: Sequence[str],
    operation: str,
) -> None:
    immutable_fields = (
        ("registry_id",)
        if operation == "rollback"
        else ("registry_id", "governing_basis", "owner_id")
    )
    for field_name in immutable_fields:
        if base[field_name] != candidate[field_name]:
            raise MillefeuilleContractError(
                f"taxonomy change cannot mutate registry {field_name}"
            )
    base_entries = {entry["entry_id"]: entry for entry in base["entries"]}
    candidate_entries = {entry["entry_id"]: entry for entry in candidate["entries"]}
    missing = sorted(set(base_entries) - set(candidate_entries))
    if missing:
        raise MillefeuilleContractError(
            "taxonomy change cannot delete stable entry IDs; deprecate them instead"
        )
    for entry_id, base_entry in base_entries.items():
        candidate_entry = candidate_entries[entry_id]
        for field_name in ("level", "parent_id"):
            if base_entry[field_name] != candidate_entry[field_name]:
                raise MillefeuilleContractError(
                    "taxonomy change cannot reassign a stable entry ID to a "
                    "different level or parent"
                )
    actual_affected = sorted(
        entry_id
        for entry_id in candidate_entries
        if entry_id not in base_entries
        or candidate_entries[entry_id] != base_entries[entry_id]
    )
    if actual_affected != list(affected_entry_ids):
        raise MillefeuilleContractError(
            "taxonomy change affected_entry_ids do not match the exact registry diff"
        )
    if not actual_affected:
        raise MillefeuilleContractError("taxonomy change candidate is a no-op")
    _validate_operation_semantics(
        operation=operation,
        base_entries=base_entries,
        candidate_entries=candidate_entries,
    )


def _validate_operation_semantics(
    *,
    operation: str,
    base_entries: Mapping[str, Mapping[str, Any]],
    candidate_entries: Mapping[str, Mapping[str, Any]],
) -> None:
    """Bind each specific operation name to an enforceable registry diff shape."""

    if operation in {"mixed", "rollback"}:
        return
    added_ids = set(candidate_entries) - set(base_entries)
    changed_existing = {
        entry_id: _changed_entry_fields(
            base_entries[entry_id], candidate_entries[entry_id]
        )
        for entry_id in set(base_entries) & set(candidate_entries)
        if base_entries[entry_id] != candidate_entries[entry_id]
    }

    def is_deprecation_only(entry_id: str) -> bool:
        before = base_entries[entry_id]
        after = candidate_entries[entry_id]
        fields = changed_existing[entry_id]
        return (
            before["status"] == "active"
            and after["status"] == "deprecated"
            and fields <= {"status", "replacement_id"}
        )

    if operation == "add":
        valid = bool(added_ids) and not changed_existing
    elif operation == "rename":
        valid = (
            not added_ids
            and bool(changed_existing)
            and all(fields == {"label"} for fields in changed_existing.values())
        )
    elif operation == "clarify":
        clarification_fields = {
            "definition",
            "include_when",
            "exclude_when",
            "boundary_notes",
        }
        valid = (
            not added_ids
            and bool(changed_existing)
            and all(
                bool(fields) and fields <= clarification_fields
                for fields in changed_existing.values()
            )
        )
    elif operation == "deprecate":
        valid = (
            not added_ids
            and bool(changed_existing)
            and all(is_deprecation_only(entry_id) for entry_id in changed_existing)
        )
    elif operation == "split":
        valid = (
            len(added_ids) >= 2
            and bool(changed_existing)
            and all(is_deprecation_only(entry_id) for entry_id in changed_existing)
        )
    elif operation == "merge":
        valid = len(changed_existing) >= 2 and all(
            is_deprecation_only(entry_id) for entry_id in changed_existing
        )
        if valid:
            replacement_ids = {
                candidate_entries[entry_id]["replacement_id"]
                for entry_id in changed_existing
            }
            valid = (
                len(replacement_ids) == 1
                and None not in replacement_ids
                and added_ids <= replacement_ids
            )
    else:  # The public validator rejects unknown operations before this point.
        return
    if not valid:
        raise MillefeuilleContractError(
            f"taxonomy change operation {operation} does not match the exact "
            "registry diff"
        )


def _changed_entry_fields(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> set[str]:
    return {
        field_name for field_name in before if before[field_name] != after[field_name]
    }


def _validate_rollback_candidate(
    *,
    base: Mapping[str, Any],
    candidate: Mapping[str, Any],
    source: Mapping[str, Any],
) -> None:
    if (
        candidate["registry_id"] != source["registry_id"]
        or base["registry_id"] != source["registry_id"]
    ):
        raise MillefeuilleContractError("rollback candidate registry_id drift")
    if (
        base["previous_version"] != source["taxonomy_version"]
        or base["previous_content_identity"] != source["content_identity"]
    ):
        raise MillefeuilleContractError(
            "rollback source is not the exact content-addressed predecessor of the base"
        )
    for field_name in ("governing_basis", "owner_id"):
        if candidate[field_name] != source[field_name]:
            raise MillefeuilleContractError(
                "rollback candidate must restore the exact source registry content"
            )

    base_entries = {entry["entry_id"]: entry for entry in base["entries"]}
    source_entries = {entry["entry_id"]: entry for entry in source["entries"]}
    candidate_entries = {entry["entry_id"]: entry for entry in candidate["entries"]}
    source_only_ids = set(source_entries) - set(base_entries)
    if source_only_ids:
        raise MillefeuilleContractError(
            "rollback source is not an ancestor of the base; stable source entry "
            "IDs are missing from the base"
        )
    for entry_id, source_entry in source_entries.items():
        base_entry = base_entries[entry_id]
        if any(
            source_entry[field_name] != base_entry[field_name]
            for field_name in ("level", "parent_id")
        ):
            raise MillefeuilleContractError(
                "rollback source is not an ancestor of the base; stable source "
                "entry structure drifted"
            )

    expected_entry_ids = set(base_entries) | set(source_entries)
    candidate_entry_ids = set(candidate_entries)
    if candidate_entry_ids != expected_entry_ids:
        raise MillefeuilleContractError(
            "rollback candidate entry IDs must equal the base/source union"
        )
    for entry_id, source_entry in source_entries.items():
        if candidate_entries[entry_id] != source_entry:
            raise MillefeuilleContractError(
                "rollback candidate must restore every source entry exactly"
            )
    for entry_id in set(base_entries) - set(source_entries):
        base_entry = base_entries[entry_id]
        candidate_entry = candidate_entries[entry_id]
        expected_entry = deepcopy(base_entry)
        if expected_entry["status"] == "active":
            expected_entry["status"] = "deprecated"
        if candidate_entry != expected_entry:
            raise MillefeuilleContractError(
                "rollback candidate must retain every later base-only entry exactly, "
                "apart from the sole permitted active-to-deprecated status change"
            )
    if candidate["taxonomy_version"] == source["taxonomy_version"]:
        raise MillefeuilleContractError(
            "rollback must publish a new forward taxonomy_version"
        )


def _require_approved_reviews(
    reviews: Sequence[Mapping[str, Any]],
    *,
    requester_id: str,
) -> None:
    if not reviews:
        raise MillefeuilleContractError(
            "taxonomy change application requires governed reviews"
        )
    roles = [str(review["role"]) for review in reviews]
    reviewers = [str(review["reviewer_id"]) for review in reviews]
    review_ids = [str(review["review_id"]) for review in reviews]
    if len(roles) != len(set(roles)):
        raise MillefeuilleContractError(
            "taxonomy change application cannot reuse a review role"
        )
    if len(reviewers) != len(set(reviewers)):
        raise MillefeuilleContractError(
            "taxonomy change application requires distinct reviewers"
        )
    if len(review_ids) != len(set(review_ids)):
        raise MillefeuilleContractError(
            "taxonomy change application review_id values must be unique"
        )
    if any(
        review["role"] in REQUIRED_REVIEW_ROLES
        and review["reviewer_id"] == requester_id
        for review in reviews
    ):
        raise MillefeuilleContractError(
            "required taxonomy reviewers must be independent from the requester"
        )
    if any(review["decision"] != "approve" for review in reviews):
        raise MillefeuilleContractError(
            "taxonomy change application is blocked by a non-approval review"
        )
    missing = REQUIRED_REVIEW_ROLES - set(roles)
    if missing:
        raise MillefeuilleContractError(
            "taxonomy change application is missing required review roles: "
            + ", ".join(sorted(missing))
        )


def _registry_binding(registry: Mapping[str, Any]) -> dict[str, str]:
    return {
        "registry_id": str(registry["registry_id"]),
        "taxonomy_version": str(registry["taxonomy_version"]),
        "content_identity": str(registry["content_identity"]),
    }


def _validate_registry_binding(value: Any, label: str) -> dict[str, str]:
    binding = _mapping_copy(value, label)
    _require_exact_fields(
        binding,
        required={"registry_id", "taxonomy_version", "content_identity"},
        label=label,
    )
    _require_safe_id(binding["registry_id"], f"{label} registry_id")
    _require_safe_id(binding["taxonomy_version"], f"{label} taxonomy_version")
    _require_identity(binding["content_identity"], f"{label} content_identity")
    return {
        "registry_id": binding["registry_id"],
        "taxonomy_version": binding["taxonomy_version"],
        "content_identity": binding["content_identity"],
    }


def _mapping_copy(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MillefeuilleContractError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise MillefeuilleContractError(f"{label} keys must be strings")
    return deepcopy(dict(value))


def _require_exact_fields(
    payload: Mapping[str, Any],
    *,
    required: set[str],
    label: str,
) -> None:
    missing = sorted(required - set(payload))
    unexpected = sorted(set(payload) - required)
    if missing:
        raise MillefeuilleContractError(
            f"{label} is missing required fields: " + ", ".join(missing)
        )
    if unexpected:
        raise MillefeuilleContractError(
            f"{label} has unsupported fields: " + ", ".join(unexpected)
        )


def _require_const(value: Any, expected: str, label: str) -> None:
    if value != expected:
        raise MillefeuilleContractError(f"unsupported {label}")


def _require_string(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value != unicodedata.normalize("NFC", value)
        or any(unicodedata.category(character) == "Cc" for character in value)
        or len(value) > 4096
    ):
        raise MillefeuilleContractError(
            f"{label} must be a non-empty, unpadded string without controls"
        )
    return value


def _require_safe_id(value: Any, label: str) -> str:
    normalized = _require_string(value, label)
    if _SAFE_ID.fullmatch(normalized) is None:
        raise MillefeuilleContractError(f"{label} must be a stable safe identifier")
    return normalized


def _require_entry_id(value: Any, label: str) -> str:
    normalized = _require_string(value, label)
    if _SAFE_ENTRY_ID.fullmatch(normalized) is None:
        raise MillefeuilleContractError(f"{label} must be a stable entry identifier")
    return normalized


def _require_identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or _CONTENT_IDENTITY.fullmatch(value) is None:
        raise MillefeuilleContractError(f"{label} must use sha256:<64 lowercase hex>")
    return value


def _require_content_identity(payload: Mapping[str, Any], label: str) -> None:
    actual = _require_identity(payload.get("content_identity"), f"{label} identity")
    expected = canonical_content_identity(payload)
    if actual != expected:
        raise MillefeuilleContractError(f"{label} content identity drift")


def _require_timestamp(value: Any, label: str) -> str:
    normalized = _require_string(value, label)
    if _UTC_TIMESTAMP.fullmatch(normalized) is None:
        raise MillefeuilleContractError(
            f"{label} must be an RFC 3339 UTC timestamp ending in Z"
        )
    try:
        datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise MillefeuilleContractError(
            f"{label} is not a valid UTC timestamp"
        ) from exc
    return normalized


def _require_sorted_unique_strings(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
    safe_entry_ids: bool = False,
    identities: bool = False,
) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise MillefeuilleContractError(
            f"{label} must be an array with at least {minimum} entries"
        )
    if len(value) > maximum:
        raise MillefeuilleContractError(
            f"{label} must contain at most {maximum} entries"
        )
    normalized: list[str] = []
    for entry in value:
        if safe_entry_ids:
            normalized.append(_require_entry_id(entry, f"{label} entry"))
        elif identities:
            normalized.append(_require_identity(entry, f"{label} entry"))
        else:
            normalized.append(_require_string(entry, f"{label} entry"))
    if normalized != sorted(normalized):
        raise MillefeuilleContractError(f"{label} must be sorted")
    if len(normalized) != len(set(normalized)):
        raise MillefeuilleContractError(f"{label} must not contain duplicates")
    return normalized


def _canonical_string_sequence(
    value: Any,
    label: str,
    *,
    safe_entry_ids: bool = False,
    maximum: int,
) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise MillefeuilleContractError(f"{label} must be an array")
    if len(value) > maximum:
        raise MillefeuilleContractError(
            f"{label} must contain at most {maximum} entries"
        )
    normalized = [
        (
            _require_entry_id(entry, f"{label} entry")
            if safe_entry_ids
            else _require_string(entry, f"{label} entry")
        )
        for entry in value
    ]
    return sorted(normalized)
