"""Offline, fail-closed operator preflight for bounded Millefeuille work.

The preflight inspects only a strict local packet, optional MF-100 approval
receipt metadata, and the presence of explicitly named environment variables.
It never returns, serializes, logs, or hashes credential values and performs no
provider, Zotero, OpenKB, index, PDF, or source-pack operation.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from typing import Any
import unicodedata

from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    ApprovedLiveRequest,
    ApprovedLiveScope,
    LiveDisposalPolicy,
    LiveProvider,
    LiveSelector,
    LiveTarget,
    ReceiptReplayState,
    validate_approved_live_receipt,
    validate_approved_live_receipt_for_no_effect,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError, RunMode
from millefeuille.domain.secure_io import read_bytes_no_follow

OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION = "millefeuille-operator-preflight-packet/v0.1"
OPERATOR_PREFLIGHT_RESULT_SCHEMA_VERSION = "millefeuille-operator-preflight-result/v0.1"
OPERATOR_PREFLIGHT_PACKET_MAX_BYTES = 65_536
OPERATOR_PREFLIGHT_RESULT_MAX_BYTES = 65_536
PREFLIGHT_SCOPE_TARGET_KIND = "preflight-scope"

_JSON_SAFE_INTEGER_MAX = (1 << 53) - 1
_PACKET_FIELDS = frozenset(
    {
        "schema_version",
        "packet_id",
        "mode",
        "source",
        "run_id",
        "roots",
        "operations",
        "targets",
        "destinations",
        "provider",
        "limits",
        "disposal_policy",
        "stop_conditions",
        "rollback_actions",
        "acceptance_status",
        "credential_requirements",
        "approval_receipt",
        "integrity",
    }
)
_SOURCE_FIELDS = frozenset({"adapter", "selector", "item_cap", "resolved_item_count"})
_SELECTOR_FIELDS = frozenset({"kind", "value"})
_ROOT_FIELDS = frozenset({"artifact_root", "source_pack_root"})
_TARGET_FIELDS = frozenset({"kind", "id"})
_PROVIDER_FIELDS = frozenset({"provider_id", "model_id", "profile_id"})
_LIMIT_FIELDS = frozenset({"max_provider_calls", "max_cost_usd_micros"})
_DISPOSAL_FIELDS = frozenset({"pdfs", "provider_payloads", "temporary_files"})
_CREDENTIAL_FIELDS = frozenset({"type", "reference"})
_APPROVAL_RECEIPT_FIELDS = frozenset({"receipt_id", "content_digest"})
_INTEGRITY_FIELDS = frozenset({"algorithm", "content_digest"})
_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "packet_id",
        "packet_digest",
        "mode",
        "decision",
        "external_effects_performed",
        "credential_readiness",
        "approval_receipt",
        "checks",
        "blockers",
        "secret_material_persisted",
        "integrity",
    }
)
_CREDENTIAL_READINESS_FIELDS = frozenset({"type", "reference", "state"})
_CHECK_FIELDS = frozenset({"id", "status"})
_APPROVAL_VALIDATION_FIELDS = frozenset({"receipt_id", "content_digest", "status"})

_SOURCE_ADAPTERS = frozenset({"local-fixture", "openkb", "source-pack", "zotero"})
_SELECTORS_BY_ADAPTER = {
    "local-fixture": frozenset(
        {"batch-manifest", "paper-id", "source-pack", "zotero-item-key"}
    ),
    "openkb": frozenset({"doi", "paper-id", "slug", "title"}),
    "source-pack": frozenset(
        {
            "batch-manifest",
            "doi",
            "paper-id",
            "slug",
            "source-pack",
            "title",
            "zotero-item-key",
        }
    ),
    "zotero": frozenset({"paper-id", "zotero-item-key", "zotero-query", "zotero-tag"}),
}
_CREDENTIAL_TYPES = frozenset(
    {
        "index-write",
        "model-oauth",
        "ocr-provider",
        "openkb-write",
        "zotero-library-id",
        "zotero-read",
        "zotero-write",
    }
)
_CREDENTIAL_REFERENCES_BY_TYPE = {
    "index-write": frozenset(
        {"PAGEINDEX_API_KEY", "MILLEFEUILLE_CREDENTIAL_INDEX_WRITE"}
    ),
    "model-oauth": frozenset(
        {"OPENCLAW_CODEX_OAUTH_READY", "MILLEFEUILLE_CREDENTIAL_MODEL_OAUTH"}
    ),
    "ocr-provider": frozenset(
        {
            "MISTRAL_API_KEY",
            "PAGEINDEX_API_KEY",
            "MILLEFEUILLE_CREDENTIAL_OCR_PROVIDER",
        }
    ),
    "openkb-write": frozenset(
        {"OPENKB_API_KEY", "MILLEFEUILLE_CREDENTIAL_OPENKB_WRITE"}
    ),
    "zotero-library-id": frozenset(
        {"ZOTERO_LIBRARY_ID", "MILLEFEUILLE_CREDENTIAL_ZOTERO_LIBRARY_ID"}
    ),
    "zotero-read": frozenset(
        {"ZOTERO_READ_KEY", "MILLEFEUILLE_CREDENTIAL_ZOTERO_READ"}
    ),
    "zotero-write": frozenset(
        {"ZOTERO_WRITE_KEY", "MILLEFEUILLE_CREDENTIAL_ZOTERO_WRITE"}
    ),
}
_READ_ONLY_OPERATIONS = frozenset(
    {
        "index.read",
        "source-pack.read",
        "status.inspect",
        "zotero.discover",
        "zotero.handoff-preview",
    }
)
_OPERATION_DESTINATION_KINDS = {
    "classification.adjudicate": frozenset({"artifact-root"}),
    "classification.review": frozenset({"artifact-root"}),
    "index.read": frozenset({"index", "paper-id"}),
    "index.write": frozenset({"index"}),
    "model.card": frozenset({"artifact-root"}),
    "model.classify": frozenset({"artifact-root"}),
    "model.execute": frozenset({"artifact-root"}),
    "model.summarize": frozenset({"artifact-root"}),
    "ocr.execute": frozenset({"artifact-root"}),
    "openkb.write": frozenset({"openkb-collection"}),
    "source-pack.read": frozenset({"paper-id", "source-pack", "source-pack-root"}),
    "source-pack.write": frozenset({"source-pack-root"}),
    "stage.acceptance": frozenset({"artifact-root"}),
    "stage.extract-native": frozenset({"artifact-root"}),
    "stage.route": frozenset({"artifact-root"}),
    "stage.structure": frozenset({"artifact-root"}),
    "status.inspect": frozenset(
        {
            "artifact-root",
            "index",
            "paper-id",
            "source-pack",
            "source-pack-root",
            "zotero-item-key",
        }
    ),
    "zotero.discover": frozenset(
        {"paper-id", "zotero-item-key", "zotero-query", "zotero-tag"}
    ),
    "zotero.handoff-export": frozenset({"artifact-root"}),
    "zotero.handoff-preview": frozenset(
        {"paper-id", "zotero-item-key", "zotero-query", "zotero-tag"}
    ),
    "zotero.recover-pdf": frozenset({"artifact-root"}),
    "zotero.writeback": frozenset({"zotero-item"}),
}
SUPPORTED_OPERATOR_OPERATIONS = frozenset(_OPERATION_DESTINATION_KINDS)
_OPERATION_CREDENTIAL_TYPES = {
    "index.write": frozenset({"index-write"}),
    "model.card": frozenset({"model-oauth"}),
    "model.classify": frozenset({"model-oauth"}),
    "model.execute": frozenset({"model-oauth"}),
    "model.summarize": frozenset({"model-oauth"}),
    "ocr.execute": frozenset({"ocr-provider"}),
    "openkb.write": frozenset({"openkb-write"}),
    "zotero.discover": frozenset({"zotero-library-id", "zotero-read"}),
    "zotero.handoff-export": frozenset({"zotero-library-id", "zotero-read"}),
    "zotero.handoff-preview": frozenset({"zotero-library-id", "zotero-read"}),
    "zotero.recover-pdf": frozenset({"zotero-library-id", "zotero-read"}),
    "zotero.writeback": frozenset({"zotero-library-id", "zotero-read", "zotero-write"}),
}
_PROVIDER_OPERATIONS = frozenset(
    {
        "model.card",
        "model.classify",
        "model.execute",
        "model.summarize",
        "ocr.execute",
    }
)
_CLASSIFICATION_OPERATIONS = frozenset(
    {
        "classification.adjudicate",
        "classification.review",
        "model.classify",
    }
)
_ACCEPTANCE_REQUIRED_OPERATIONS = _CLASSIFICATION_OPERATIONS | frozenset(
    {"zotero.writeback"}
)
_DECISIONS = frozenset(
    {
        "approved-scope-validated-execution-unsupported",
        "blocked",
        "ready-for-preview",
        "ready-for-read-only-live",
    }
)
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}\Z")
_SAFE_CODE_RE = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9][a-z0-9-]*)*\Z")
_ENV_REFERENCE_RE = re.compile(r"[A-Z][A-Z0-9_]{1,127}\Z")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "credential_value",
        "headers",
        "oauth_token",
        "password",
        "payload",
        "pdf_bytes",
        "private_key",
        "provider_payload",
        "raw_response",
        "refresh_token",
        "secret",
        "token",
        "value_bytes",
    }
)
_FORBIDDEN_VALUE_MARKERS = (
    re.compile(r"Authorization:\s*(?:Bearer|Basic)", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}", re.IGNORECASE),
    re.compile(r"data:application/pdf", re.IGNORECASE),
    re.compile(r"%PDF-"),
    re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"npm_[A-Za-z0-9]{20,}"),
    re.compile(r"dop_v1_[A-Fa-f0-9]{32,}"),
    re.compile(r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\."
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),
    re.compile(r"https?://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
    re.compile(
        r"(?:[?&]|\b)(?:access_token|api_key|password|refresh_token|secret|token)="
        r"[^\s&]+",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class OperatorSource:
    adapter: str
    selector: LiveSelector
    item_cap: int
    resolved_item_count: int

    def __post_init__(self) -> None:
        if type(self.selector) is not LiveSelector:
            raise MillefeuilleContractError(
                "source.selector must be an exact LiveSelector"
            )
        _required_string(self.selector.kind, "source.selector.kind", maximum=64)
        _safe_exact_value(
            self.selector.value,
            "source.selector.value",
            maximum=512,
        )
        if self.adapter not in _SOURCE_ADAPTERS:
            raise MillefeuilleContractError("source.adapter is unsupported")
        if self.selector.kind not in _SELECTORS_BY_ADAPTER[self.adapter]:
            raise MillefeuilleContractError(
                "source.selector.kind is incompatible with source.adapter"
            )
        _required_integer(self.item_cap, "source.item_cap", minimum=1)
        _required_integer(
            self.resolved_item_count,
            "source.resolved_item_count",
            minimum=0,
        )
        if self.resolved_item_count > self.item_cap:
            raise MillefeuilleContractError(
                "source.resolved_item_count exceeds source.item_cap"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "selector": self.selector.to_dict(),
            "item_cap": self.item_cap,
            "resolved_item_count": self.resolved_item_count,
        }


@dataclass(frozen=True)
class OperatorProvider:
    provider_id: str
    model_id: str
    profile_id: str

    def __post_init__(self) -> None:
        _safe_exact_value(self.provider_id, "provider.provider_id", maximum=128)
        _safe_exact_value(self.model_id, "provider.model_id", maximum=256)
        _safe_exact_value(self.profile_id, "provider.profile_id", maximum=256)

    def to_live_provider(self) -> LiveProvider:
        return LiveProvider(self.provider_id, self.model_id)

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "profile_id": self.profile_id,
        }


@dataclass(frozen=True, order=True)
class CredentialRequirement:
    credential_type: str
    reference: str

    def __post_init__(self) -> None:
        if self.credential_type not in _CREDENTIAL_TYPES:
            raise MillefeuilleContractError(
                "credential_requirements.type is unsupported"
            )
        if _ENV_REFERENCE_RE.fullmatch(self.reference) is None:
            raise MillefeuilleContractError(
                "credential_requirements.reference must be an exact environment name"
            )
        if self.reference not in _CREDENTIAL_REFERENCES_BY_TYPE[self.credential_type]:
            raise MillefeuilleContractError(
                "credential_requirements.reference is not allowlisted for its type"
            )

    def to_dict(self) -> dict[str, str]:
        return {"type": self.credential_type, "reference": self.reference}


@dataclass(frozen=True)
class ApprovalReceiptIdentity:
    receipt_id: str
    content_digest: str

    def __post_init__(self) -> None:
        _safe_identity(self.receipt_id, "approval_receipt.receipt_id")
        if _DIGEST_RE.fullmatch(self.content_digest) is None:
            raise MillefeuilleContractError(
                "approval_receipt.content_digest must be a sha256 digest"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "receipt_id": self.receipt_id,
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True)
class OperatorPreflightPacket:
    """One content-addressed operator request, not an execution capability."""

    packet_id: str
    mode: str
    source: OperatorSource
    run_id: str
    artifact_root: str
    source_pack_root: str
    operations: tuple[str, ...]
    targets: tuple[LiveTarget, ...]
    destinations: tuple[LiveTarget, ...]
    provider: OperatorProvider | None
    max_provider_calls: int
    max_cost_usd_micros: int
    disposal_policy: LiveDisposalPolicy
    stop_conditions: tuple[str, ...]
    rollback_actions: tuple[str, ...]
    acceptance_status: str
    credential_requirements: tuple[CredentialRequirement, ...]
    approval_receipt: ApprovalReceiptIdentity | None
    content_digest: str

    def __post_init__(self) -> None:
        _safe_identity(self.packet_id, "packet_id")
        _safe_identity(self.run_id, "run_id")
        if self.mode not in RunMode.values():
            raise MillefeuilleContractError("operator preflight mode is unsupported")

        if type(self.source) is not OperatorSource:
            raise MillefeuilleContractError("source must be an exact OperatorSource")
        if self.provider is not None and type(self.provider) is not OperatorProvider:
            raise MillefeuilleContractError(
                "provider must be null or an exact OperatorProvider"
            )
        if type(self.disposal_policy) is not LiveDisposalPolicy:
            raise MillefeuilleContractError(
                "disposal_policy must be an exact LiveDisposalPolicy"
            )
        _require_exact_tuple(self.operations, str, "operations")
        _require_exact_tuple(self.targets, LiveTarget, "targets")
        _require_exact_tuple(self.destinations, LiveTarget, "destinations")
        _require_exact_tuple(self.stop_conditions, str, "stop_conditions")
        _require_exact_tuple(self.rollback_actions, str, "rollback_actions")
        _require_exact_tuple(
            self.credential_requirements,
            CredentialRequirement,
            "credential_requirements",
        )
        if (
            self.approval_receipt is not None
            and type(self.approval_receipt) is not ApprovalReceiptIdentity
        ):
            raise MillefeuilleContractError(
                "approval_receipt must be null or an exact identity"
            )

        _safe_exact_value(self.artifact_root, "roots.artifact_root", maximum=4096)
        _safe_exact_value(
            self.source_pack_root,
            "roots.source_pack_root",
            maximum=4096,
        )
        for target in (*self.targets, *self.destinations):
            _validate_code(target.kind, "target.kind")
            _safe_exact_value(target.id, "target.id", maximum=256)

        _validate_codes(self.operations, "operations")
        if not self.destinations:
            raise MillefeuilleContractError("destinations must not be empty")
        if tuple(sorted(set(self.destinations))) != self.destinations:
            raise MillefeuilleContractError("destinations must be sorted and unique")
        if not set(self.destinations).issubset(self.targets):
            raise MillefeuilleContractError(
                "every destination must also appear in exact targets"
            )
        if any(
            destination.kind == PREFLIGHT_SCOPE_TARGET_KIND
            for destination in self.destinations
        ):
            raise MillefeuilleContractError(
                "preflight-scope identity cannot be an execution destination"
            )
        _validate_supported_operation_scope(self)
        self.to_live_scope()

        _validate_codes(self.stop_conditions, "stop_conditions")
        _validate_codes(self.rollback_actions, "rollback_actions")
        if self.acceptance_status not in {"not-applicable", "pass"}:
            raise MillefeuilleContractError("acceptance_status is unsupported")
        if (
            _ACCEPTANCE_REQUIRED_OPERATIONS.intersection(self.operations)
            and self.acceptance_status != "pass"
        ):
            raise MillefeuilleContractError(
                "classification and writeback operations require acceptance_status pass"
            )
        if self.mode == RunMode.READ_ONLY_LIVE.value and any(
            operation not in _READ_ONLY_OPERATIONS for operation in self.operations
        ):
            raise MillefeuilleContractError(
                "read-only-live permits only exact read-only operations"
            )

        if tuple(sorted(set(self.credential_requirements))) != (
            self.credential_requirements
        ):
            raise MillefeuilleContractError(
                "credential_requirements must be sorted and unique"
            )
        credential_types = [
            requirement.credential_type for requirement in self.credential_requirements
        ]
        if len(credential_types) != len(set(credential_types)):
            raise MillefeuilleContractError(
                "credential_requirements must bind one reference per type"
            )
        expected_credentials = _required_credential_types(self)
        if set(credential_types) != expected_credentials:
            raise MillefeuilleContractError(
                "credential requirements drift from requested mode and scope"
            )

        if self.mode == RunMode.APPROVED_LIVE.value:
            if self.approval_receipt is None:
                raise MillefeuilleContractError(
                    "approved-live requires approval_receipt identity"
                )
            scope_targets = tuple(
                target
                for target in self.targets
                if target.kind == PREFLIGHT_SCOPE_TARGET_KIND
            )
            if len(scope_targets) != 1:
                raise MillefeuilleContractError(
                    "approved-live requires one preflight-scope target"
                )
            expected_context = compute_operator_authorization_context_digest(
                self.to_dict()
            )
            if not hmac.compare_digest(scope_targets[0].id, expected_context):
                raise MillefeuilleContractError(
                    "preflight-scope authorization context digest mismatch"
                )
        elif self.approval_receipt is not None:
            raise MillefeuilleContractError(
                "approval_receipt cannot promote preview or read-only-live"
            )
        elif any(target.kind == PREFLIGHT_SCOPE_TARGET_KIND for target in self.targets):
            raise MillefeuilleContractError(
                "preflight-scope target is approved-live only"
            )

        _reject_secret_like(self.to_dict(), "operator preflight packet")
        if _DIGEST_RE.fullmatch(self.content_digest) is None:
            raise MillefeuilleContractError(
                "integrity.content_digest must be a sha256 digest"
            )
        expected_digest = compute_operator_preflight_packet_digest(self.to_dict())
        if not hmac.compare_digest(self.content_digest, expected_digest):
            raise MillefeuilleContractError(
                "operator preflight packet content_digest mismatch"
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> OperatorPreflightPacket:
        _reject_secret_like(payload, "operator preflight packet")
        _require_exact_fields(payload, _PACKET_FIELDS, "operator preflight packet")
        if payload.get("schema_version") != OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                "operator preflight packet schema_version is unsupported"
            )
        integrity = _require_object(payload.get("integrity"), "integrity")
        _require_exact_fields(integrity, _INTEGRITY_FIELDS, "integrity")
        if integrity.get("algorithm") != "sha256":
            raise MillefeuilleContractError("integrity.algorithm must be sha256")
        supplied_digest = _required_string(
            integrity.get("content_digest"),
            "integrity.content_digest",
            maximum=71,
        )
        expected_digest = compute_operator_preflight_packet_digest(payload)
        if not hmac.compare_digest(supplied_digest, expected_digest):
            raise MillefeuilleContractError(
                "operator preflight packet content_digest mismatch"
            )

        source_value = _require_object(payload.get("source"), "source")
        _require_exact_fields(source_value, _SOURCE_FIELDS, "source")
        selector_value = _require_object(source_value.get("selector"), "selector")
        _require_exact_fields(selector_value, _SELECTOR_FIELDS, "selector")
        source = OperatorSource(
            adapter=_required_string(
                source_value.get("adapter"), "source.adapter", maximum=64
            ),
            selector=LiveSelector(
                kind=_required_string(
                    selector_value.get("kind"), "source.selector.kind", maximum=64
                ),
                value=_required_string(
                    selector_value.get("value"),
                    "source.selector.value",
                    maximum=512,
                ),
            ),
            item_cap=_required_integer(
                source_value.get("item_cap"), "source.item_cap", minimum=1
            ),
            resolved_item_count=_required_integer(
                source_value.get("resolved_item_count"),
                "source.resolved_item_count",
                minimum=0,
            ),
        )

        roots = _require_object(payload.get("roots"), "roots")
        _require_exact_fields(roots, _ROOT_FIELDS, "roots")
        provider_value = payload.get("provider")
        provider: OperatorProvider | None
        if provider_value is None:
            provider = None
        else:
            provider_object = _require_object(provider_value, "provider")
            _require_exact_fields(provider_object, _PROVIDER_FIELDS, "provider")
            provider = OperatorProvider(
                provider_id=_required_string(
                    provider_object.get("provider_id"),
                    "provider.provider_id",
                    maximum=128,
                ),
                model_id=_required_string(
                    provider_object.get("model_id"),
                    "provider.model_id",
                    maximum=256,
                ),
                profile_id=_required_string(
                    provider_object.get("profile_id"),
                    "provider.profile_id",
                    maximum=256,
                ),
            )

        limits = _require_object(payload.get("limits"), "limits")
        _require_exact_fields(limits, _LIMIT_FIELDS, "limits")
        disposal = _require_object(payload.get("disposal_policy"), "disposal_policy")
        _require_exact_fields(disposal, _DISPOSAL_FIELDS, "disposal_policy")

        approval_value = payload.get("approval_receipt")
        approval_receipt: ApprovalReceiptIdentity | None
        if approval_value is None:
            approval_receipt = None
        else:
            approval_object = _require_object(approval_value, "approval_receipt")
            _require_exact_fields(
                approval_object,
                _APPROVAL_RECEIPT_FIELDS,
                "approval_receipt",
            )
            approval_receipt = ApprovalReceiptIdentity(
                receipt_id=_required_string(
                    approval_object.get("receipt_id"),
                    "approval_receipt.receipt_id",
                    maximum=256,
                ),
                content_digest=_required_string(
                    approval_object.get("content_digest"),
                    "approval_receipt.content_digest",
                    maximum=71,
                ),
            )

        credentials = tuple(
            _credential_requirement_from_object(item, index)
            for index, item in enumerate(
                _require_sequence(
                    payload.get("credential_requirements"),
                    "credential_requirements",
                    allow_empty=True,
                )
            )
        )
        return cls(
            packet_id=_required_string(
                payload.get("packet_id"), "packet_id", maximum=256
            ),
            mode=_required_string(payload.get("mode"), "mode", maximum=32),
            source=source,
            run_id=_required_string(payload.get("run_id"), "run_id", maximum=256),
            artifact_root=_required_string(
                roots.get("artifact_root"), "roots.artifact_root", maximum=4096
            ),
            source_pack_root=_required_string(
                roots.get("source_pack_root"),
                "roots.source_pack_root",
                maximum=4096,
            ),
            operations=_string_tuple(payload.get("operations"), "operations"),
            targets=_target_tuple(payload.get("targets"), "targets"),
            destinations=_target_tuple(payload.get("destinations"), "destinations"),
            provider=provider,
            max_provider_calls=_required_integer(
                limits.get("max_provider_calls"),
                "limits.max_provider_calls",
                minimum=0,
            ),
            max_cost_usd_micros=_required_integer(
                limits.get("max_cost_usd_micros"),
                "limits.max_cost_usd_micros",
                minimum=0,
            ),
            disposal_policy=LiveDisposalPolicy(
                pdfs=_required_string(
                    disposal.get("pdfs"), "disposal_policy.pdfs", maximum=64
                ),
                provider_payloads=_required_string(
                    disposal.get("provider_payloads"),
                    "disposal_policy.provider_payloads",
                    maximum=64,
                ),
                temporary_files=_required_string(
                    disposal.get("temporary_files"),
                    "disposal_policy.temporary_files",
                    maximum=64,
                ),
            ),
            stop_conditions=_string_tuple(
                payload.get("stop_conditions"), "stop_conditions"
            ),
            rollback_actions=_string_tuple(
                payload.get("rollback_actions"), "rollback_actions"
            ),
            acceptance_status=_required_string(
                payload.get("acceptance_status"),
                "acceptance_status",
                maximum=32,
            ),
            credential_requirements=credentials,
            approval_receipt=approval_receipt,
            content_digest=supplied_digest,
        )

    def to_live_scope(self) -> ApprovedLiveScope:
        return ApprovedLiveScope(
            operations=self.operations,
            targets=self.targets,
            max_items=self.source.item_cap,
            selector=self.source.selector,
            output_root=self.artifact_root,
            source_pack_root=self.source_pack_root,
            provider=(
                self.provider.to_live_provider() if self.provider is not None else None
            ),
            max_provider_calls=self.max_provider_calls,
            max_cost_usd_micros=self.max_cost_usd_micros,
            disposal_policy=self.disposal_policy,
            stop_conditions=self.stop_conditions,
        )

    def to_approved_live_request(self) -> ApprovedLiveRequest:
        return ApprovedLiveRequest(
            run_id=self.run_id,
            operations=self.operations,
            targets=self.targets,
            item_cap=self.source.item_cap,
            selected_item_count=self.source.resolved_item_count,
            selector=self.source.selector,
            output_root=self.artifact_root,
            source_pack_root=self.source_pack_root,
            provider=(
                self.provider.to_live_provider() if self.provider is not None else None
            ),
            provider_call_limit=self.max_provider_calls,
            cost_limit_usd_micros=self.max_cost_usd_micros,
            disposal_policy=self.disposal_policy,
            stop_conditions=self.stop_conditions,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION,
            "packet_id": self.packet_id,
            "mode": self.mode,
            "source": self.source.to_dict(),
            "run_id": self.run_id,
            "roots": {
                "artifact_root": self.artifact_root,
                "source_pack_root": self.source_pack_root,
            },
            "operations": list(self.operations),
            "targets": [target.to_dict() for target in self.targets],
            "destinations": [target.to_dict() for target in self.destinations],
            "provider": self.provider.to_dict() if self.provider else None,
            "limits": {
                "max_provider_calls": self.max_provider_calls,
                "max_cost_usd_micros": self.max_cost_usd_micros,
            },
            "disposal_policy": self.disposal_policy.to_dict(),
            "stop_conditions": list(self.stop_conditions),
            "rollback_actions": list(self.rollback_actions),
            "acceptance_status": self.acceptance_status,
            "credential_requirements": [
                requirement.to_dict() for requirement in self.credential_requirements
            ],
            "approval_receipt": (
                self.approval_receipt.to_dict() if self.approval_receipt else None
            ),
            "integrity": {
                "algorithm": "sha256",
                "content_digest": self.content_digest,
            },
        }


@dataclass(frozen=True, order=True)
class CredentialReadiness:
    credential_type: str
    reference: str
    state: str

    def __post_init__(self) -> None:
        CredentialRequirement(self.credential_type, self.reference)
        if self.state not in {"missing", "present"}:
            raise MillefeuilleContractError("credential readiness state is invalid")

    def to_dict(self) -> dict[str, str]:
        return {
            "type": self.credential_type,
            "reference": self.reference,
            "state": self.state,
        }


@dataclass(frozen=True, order=True)
class PreflightCheck:
    check_id: str
    status: str

    def __post_init__(self) -> None:
        _validate_code(self.check_id, "checks.id")
        if self.status not in {"failed", "not-applicable", "passed"}:
            raise MillefeuilleContractError("checks.status is invalid")

    def to_dict(self) -> dict[str, str]:
        return {"id": self.check_id, "status": self.status}


@dataclass(frozen=True)
class ApprovalReceiptValidation:
    receipt_id: str
    content_digest: str
    status: str = "validated"

    def __post_init__(self) -> None:
        ApprovalReceiptIdentity(self.receipt_id, self.content_digest)
        if self.status != "validated":
            raise MillefeuilleContractError(
                "approval receipt validation status is invalid"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "receipt_id": self.receipt_id,
            "content_digest": self.content_digest,
            "status": self.status,
        }


@dataclass(frozen=True)
class OperatorPreflightResult:
    """Content-addressed, allowlisted outcome of one no-effect preflight."""

    packet_id: str
    packet_digest: str
    mode: str
    decision: str
    credential_readiness: tuple[CredentialReadiness, ...]
    approval_receipt: ApprovalReceiptValidation | None
    checks: tuple[PreflightCheck, ...]
    blockers: tuple[str, ...]
    content_digest: str

    def __post_init__(self) -> None:
        _safe_identity(self.packet_id, "result.packet_id")
        if _DIGEST_RE.fullmatch(self.packet_digest) is None:
            raise MillefeuilleContractError(
                "result.packet_digest must be a sha256 digest"
            )
        if self.mode not in RunMode.values():
            raise MillefeuilleContractError("result.mode is unsupported")
        if self.decision not in _DECISIONS:
            raise MillefeuilleContractError("result.decision is unsupported")
        _require_exact_tuple(
            self.credential_readiness,
            CredentialReadiness,
            "result.credential_readiness",
        )
        _require_exact_tuple(self.checks, PreflightCheck, "result.checks")
        _require_exact_tuple(self.blockers, str, "result.blockers")
        if (
            self.approval_receipt is not None
            and type(self.approval_receipt) is not ApprovalReceiptValidation
        ):
            raise MillefeuilleContractError(
                "result.approval_receipt must be null or an exact validation"
            )
        if tuple(sorted(set(self.credential_readiness))) != (self.credential_readiness):
            raise MillefeuilleContractError(
                "result credential readiness must be sorted and unique"
            )
        if tuple(sorted(self.checks, key=lambda check: check.check_id)) != self.checks:
            raise MillefeuilleContractError("result checks must be sorted")
        if len({check.check_id for check in self.checks}) != len(self.checks):
            raise MillefeuilleContractError("result checks must be unique")
        if tuple(sorted(set(self.blockers))) != self.blockers:
            raise MillefeuilleContractError("result blockers must be sorted and unique")
        for blocker in self.blockers:
            _validate_code(blocker, "result.blockers")

        checks_by_id = {check.check_id: check.status for check in self.checks}
        if set(checks_by_id) != {
            "approval-receipt",
            "credential-readiness",
            "mode-binding",
            "no-external-effect",
            "packet-integrity",
            "scope-validation",
        }:
            raise MillefeuilleContractError("result checks are incomplete")
        if any(
            checks_by_id[check_id] != "passed"
            for check_id in {
                "mode-binding",
                "no-external-effect",
                "packet-integrity",
                "scope-validation",
            }
        ):
            raise MillefeuilleContractError("result invariant checks must have passed")
        has_missing_credential = any(
            readiness.state == "missing" for readiness in self.credential_readiness
        )
        if checks_by_id["credential-readiness"] != (
            "failed" if has_missing_credential else "passed"
        ):
            raise MillefeuilleContractError("result credential-readiness check drift")
        expected_missing_blockers = {
            f"credential-missing.{readiness.credential_type}"
            for readiness in self.credential_readiness
            if readiness.state == "missing"
        }
        actual_missing_blockers = {
            blocker
            for blocker in self.blockers
            if blocker.startswith("credential-missing.")
        }
        if actual_missing_blockers != expected_missing_blockers:
            raise MillefeuilleContractError("result credential blockers drift")
        self._validate_decision_semantics(checks_by_id, has_missing_credential)

        if _DIGEST_RE.fullmatch(self.content_digest) is None:
            raise MillefeuilleContractError(
                "result integrity.content_digest must be a sha256 digest"
            )
        expected_digest = compute_operator_preflight_result_digest(self.to_dict())
        if not hmac.compare_digest(self.content_digest, expected_digest):
            raise MillefeuilleContractError(
                "operator preflight result content_digest mismatch"
            )

    def _validate_decision_semantics(
        self,
        checks_by_id: dict[str, str],
        has_missing_credential: bool,
    ) -> None:
        approval_status = checks_by_id["approval-receipt"]
        expected_blockers = {
            f"credential-missing.{readiness.credential_type}"
            for readiness in self.credential_readiness
            if readiness.state == "missing"
        }
        if self.mode == RunMode.APPROVED_LIVE.value:
            if self.approval_receipt is None or approval_status != "passed":
                raise MillefeuilleContractError(
                    "approved-live result requires a validated approval receipt"
                )
            if has_missing_credential:
                expected_decision = "blocked"
                unsupported_expected = False
            else:
                expected_decision = "approved-scope-validated-execution-unsupported"
                unsupported_expected = True
                expected_blockers.add("external-execution-unsupported")
            if ("external-execution-unsupported" in self.blockers) != (
                unsupported_expected
            ):
                raise MillefeuilleContractError(
                    "result unsupported-execution blocker drift"
                )
        else:
            if self.approval_receipt is not None or approval_status != "not-applicable":
                raise MillefeuilleContractError(
                    "non-approved result cannot contain approval validation"
                )
            if "external-execution-unsupported" in self.blockers:
                raise MillefeuilleContractError(
                    "non-approved result has an invalid live blocker"
                )
            if has_missing_credential:
                expected_decision = "blocked"
            elif self.mode == RunMode.READ_ONLY_LIVE.value:
                expected_decision = "ready-for-read-only-live"
            else:
                expected_decision = "ready-for-preview"
        if self.decision != expected_decision:
            raise MillefeuilleContractError("result decision drift")
        if set(self.blockers) != expected_blockers:
            raise MillefeuilleContractError("result blockers drift")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> OperatorPreflightResult:
        _reject_secret_like(payload, "operator preflight result")
        _require_exact_fields(payload, _RESULT_FIELDS, "operator preflight result")
        if payload.get("schema_version") != OPERATOR_PREFLIGHT_RESULT_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                "operator preflight result schema_version is unsupported"
            )
        if payload.get("external_effects_performed") is not False:
            raise MillefeuilleContractError(
                "operator preflight result cannot claim an external effect"
            )
        if payload.get("secret_material_persisted") is not False:
            raise MillefeuilleContractError(
                "operator preflight result cannot persist secret material"
            )
        integrity = _require_object(payload.get("integrity"), "result.integrity")
        _require_exact_fields(integrity, _INTEGRITY_FIELDS, "result.integrity")
        if integrity.get("algorithm") != "sha256":
            raise MillefeuilleContractError("result.integrity.algorithm must be sha256")
        supplied_digest = _required_string(
            integrity.get("content_digest"),
            "result.integrity.content_digest",
            maximum=71,
        )
        expected_digest = compute_operator_preflight_result_digest(payload)
        if not hmac.compare_digest(supplied_digest, expected_digest):
            raise MillefeuilleContractError(
                "operator preflight result content_digest mismatch"
            )

        readiness = tuple(
            _credential_readiness_from_object(item, index)
            for index, item in enumerate(
                _require_sequence(
                    payload.get("credential_readiness"),
                    "result.credential_readiness",
                    allow_empty=True,
                )
            )
        )
        checks = tuple(
            _preflight_check_from_object(item, index)
            for index, item in enumerate(
                _require_sequence(payload.get("checks"), "result.checks")
            )
        )
        approval_value = payload.get("approval_receipt")
        approval_receipt = (
            None
            if approval_value is None
            else _approval_validation_from_object(approval_value)
        )
        return cls(
            packet_id=_required_string(
                payload.get("packet_id"), "result.packet_id", maximum=256
            ),
            packet_digest=_required_string(
                payload.get("packet_digest"),
                "result.packet_digest",
                maximum=71,
            ),
            mode=_required_string(payload.get("mode"), "result.mode", maximum=32),
            decision=_required_string(
                payload.get("decision"), "result.decision", maximum=64
            ),
            credential_readiness=readiness,
            approval_receipt=approval_receipt,
            checks=checks,
            blockers=_string_tuple(
                payload.get("blockers"),
                "result.blockers",
                allow_empty=True,
            ),
            content_digest=supplied_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPERATOR_PREFLIGHT_RESULT_SCHEMA_VERSION,
            "packet_id": self.packet_id,
            "packet_digest": self.packet_digest,
            "mode": self.mode,
            "decision": self.decision,
            "external_effects_performed": False,
            "credential_readiness": [
                readiness.to_dict() for readiness in self.credential_readiness
            ],
            "approval_receipt": (
                self.approval_receipt.to_dict() if self.approval_receipt else None
            ),
            "checks": [check.to_dict() for check in self.checks],
            "blockers": list(self.blockers),
            "secret_material_persisted": False,
            "integrity": {
                "algorithm": "sha256",
                "content_digest": self.content_digest,
            },
        }


def load_operator_preflight_packet(path: str | Path) -> OperatorPreflightPacket:
    """Load one strict packet through the stable bounded-file reader."""

    payload = _load_strict_json_object(
        path,
        label="operator preflight packet",
        max_bytes=OPERATOR_PREFLIGHT_PACKET_MAX_BYTES,
    )
    return OperatorPreflightPacket.from_dict(payload)


def load_operator_preflight_result(path: str | Path) -> OperatorPreflightResult:
    """Load and verify one bounded sanitized preflight result."""

    payload = _load_strict_json_object(
        path,
        label="operator preflight result",
        max_bytes=OPERATOR_PREFLIGHT_RESULT_MAX_BYTES,
    )
    return OperatorPreflightResult.from_dict(payload)


def compute_operator_preflight_packet_digest(payload: dict[str, Any]) -> str:
    """Return canonical content identity without credential values."""

    _reject_secret_like(payload, "operator preflight packet")
    body = dict(payload)
    body.pop("integrity", None)
    return _digest_canonical_object(body, "operator preflight packet")


def compute_operator_authorization_context_digest(
    payload: dict[str, Any],
) -> str:
    """Bind controls MF-100 does not otherwise represent without a cycle."""

    _reject_secret_like(payload, "operator preflight authorization context")
    body = dict(payload)
    body.pop("integrity", None)
    body.pop("approval_receipt", None)
    targets = body.get("targets")
    if isinstance(targets, list):
        body["targets"] = [
            target
            for target in targets
            if not (
                isinstance(target, dict)
                and target.get("kind") == PREFLIGHT_SCOPE_TARGET_KIND
            )
        ]
    return _digest_canonical_object(
        body,
        "operator preflight authorization context",
    )


def compute_operator_preflight_result_digest(payload: dict[str, Any]) -> str:
    """Return canonical identity for one sanitized no-effect result."""

    _reject_secret_like(payload, "operator preflight result")
    body = dict(payload)
    body.pop("integrity", None)
    return _digest_canonical_object(body, "operator preflight result")


def compute_operator_root_target_id(root: str) -> str:
    """Return a bounded target ID for one validated platform root identity."""

    text = _required_string(root, "operator root target", maximum=4096)
    path = Path(text)
    if not path.is_absolute():
        raise MillefeuilleContractError("operator root target must be an absolute path")
    if any(part in {".", ".."} for part in path.parts):
        raise MillefeuilleContractError(
            "operator root target must not contain traversal components"
        )
    if os.path.normpath(text) != text:
        raise MillefeuilleContractError("operator root target must be normalized")
    _reject_secret_like(text, "operator root target")
    identity = _root_identity(text).encode("utf-8")
    return "sha256:" + hashlib.sha256(identity).hexdigest()


def validate_operator_preflight_result(
    result: OperatorPreflightResult,
    packet: OperatorPreflightPacket,
) -> None:
    """Recompute and bind every result identity to its source packet."""

    if type(result) is not OperatorPreflightResult:
        raise MillefeuilleContractError(
            "result must be an exact OperatorPreflightResult"
        )
    if type(packet) is not OperatorPreflightPacket:
        raise MillefeuilleContractError(
            "packet must be an exact OperatorPreflightPacket"
        )
    # Reconstruct through strict wire models so mutations or forged subclasses
    # cannot bypass either model's content identity.
    verified_packet = OperatorPreflightPacket.from_dict(packet.to_dict())
    verified_result = OperatorPreflightResult.from_dict(result.to_dict())
    if not hmac.compare_digest(verified_result.packet_id, verified_packet.packet_id):
        raise MillefeuilleContractError("operator preflight result packet_id drift")
    if not hmac.compare_digest(
        verified_result.packet_digest,
        verified_packet.content_digest,
    ):
        raise MillefeuilleContractError("operator preflight result packet_digest drift")
    if verified_result.mode != verified_packet.mode:
        raise MillefeuilleContractError("operator preflight result mode drift")
    expected_credential_scope = tuple(
        (requirement.credential_type, requirement.reference)
        for requirement in verified_packet.credential_requirements
    )
    actual_credential_scope = tuple(
        (readiness.credential_type, readiness.reference)
        for readiness in verified_result.credential_readiness
    )
    if actual_credential_scope != expected_credential_scope:
        raise MillefeuilleContractError(
            "operator preflight result credential readiness scope drift"
        )

    expected_approval = verified_packet.approval_receipt
    actual_approval = verified_result.approval_receipt
    if (expected_approval is None) != (actual_approval is None):
        raise MillefeuilleContractError(
            "operator preflight result approval receipt identity drift"
        )
    if (
        expected_approval is not None
        and actual_approval is not None
        and (
            not hmac.compare_digest(
                actual_approval.receipt_id,
                expected_approval.receipt_id,
            )
            or not hmac.compare_digest(
                actual_approval.content_digest,
                expected_approval.content_digest,
            )
        )
    ):
        raise MillefeuilleContractError(
            "operator preflight result approval receipt identity drift"
        )


def _digest_canonical_object(payload: dict[str, Any], label: str) -> str:
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MillefeuilleContractError(f"{label} cannot be canonicalized") from exc
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def evaluate_operator_preflight(
    packet: OperatorPreflightPacket,
    *,
    explicit_mode: str,
    approval_receipt: ApprovedLiveReceipt | None = None,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
    replay_state: ReceiptReplayState | None = None,
) -> OperatorPreflightResult:
    """Validate readiness without executing or persisting any live effect."""

    if type(packet) is not OperatorPreflightPacket:
        raise MillefeuilleContractError(
            "packet must be an exact OperatorPreflightPacket"
        )
    packet = OperatorPreflightPacket.from_dict(packet.to_dict())
    if explicit_mode not in RunMode.values():
        raise MillefeuilleContractError("explicit operator mode is unsupported")
    if packet.mode != explicit_mode:
        raise MillefeuilleContractError(
            "operator preflight mode drift; a packet cannot promote execution mode"
        )

    approval_validation: ApprovalReceiptValidation | None = None
    if packet.mode == RunMode.APPROVED_LIVE.value:
        if approval_receipt is None:
            raise MillefeuilleContractError(
                "approved-live operator preflight requires an approval receipt"
            )
        if type(approval_receipt) is not ApprovedLiveReceipt:
            raise MillefeuilleContractError(
                "approval receipt must be an exact ApprovedLiveReceipt"
            )
        approval_receipt = ApprovedLiveReceipt.from_dict(approval_receipt.to_dict())
        assert packet.approval_receipt is not None
        if not hmac.compare_digest(
            packet.approval_receipt.receipt_id,
            approval_receipt.receipt_id,
        ):
            raise MillefeuilleContractError("approval receipt identity drift")
        if not hmac.compare_digest(
            packet.approval_receipt.content_digest,
            approval_receipt.content_digest,
        ):
            raise MillefeuilleContractError("approval receipt identity drift")
        request = packet.to_approved_live_request()
        if replay_state is None:
            validate_approved_live_receipt_for_no_effect(
                approval_receipt,
                request,
                now=now,
            )
        else:
            validate_approved_live_receipt(
                approval_receipt,
                request,
                now=now,
                replay_state=replay_state,
            )
        approval_validation = ApprovalReceiptValidation(
            receipt_id=approval_receipt.receipt_id,
            content_digest=approval_receipt.content_digest,
        )
    elif approval_receipt is not None:
        raise MillefeuilleContractError(
            "an approval receipt cannot promote preview or read-only-live"
        )

    env = os.environ if environment is None else environment
    credential_readiness = tuple(
        sorted(
            CredentialReadiness(
                credential_type=requirement.credential_type,
                reference=requirement.reference,
                state=(
                    "present"
                    if _credential_reference_is_present(env, requirement.reference)
                    else "missing"
                ),
            )
            for requirement in packet.credential_requirements
        )
    )
    missing_types = tuple(
        readiness.credential_type
        for readiness in credential_readiness
        if readiness.state == "missing"
    )
    blockers = [
        f"credential-missing.{credential_type}" for credential_type in missing_types
    ]
    checks = [
        PreflightCheck(
            "approval-receipt",
            "passed" if approval_validation else "not-applicable",
        ),
        PreflightCheck(
            "credential-readiness",
            "failed" if missing_types else "passed",
        ),
        PreflightCheck("mode-binding", "passed"),
        PreflightCheck("no-external-effect", "passed"),
        PreflightCheck("packet-integrity", "passed"),
        PreflightCheck("scope-validation", "passed"),
    ]

    if missing_types:
        decision = "blocked"
    elif packet.mode == RunMode.APPROVED_LIVE.value:
        decision = "approved-scope-validated-execution-unsupported"
        blockers.append("external-execution-unsupported")
    elif packet.mode == RunMode.READ_ONLY_LIVE.value:
        decision = "ready-for-read-only-live"
    else:
        decision = "ready-for-preview"

    result_payload: dict[str, Any] = {
        "schema_version": OPERATOR_PREFLIGHT_RESULT_SCHEMA_VERSION,
        "packet_id": packet.packet_id,
        "packet_digest": packet.content_digest,
        "mode": packet.mode,
        "decision": decision,
        "external_effects_performed": False,
        "credential_readiness": [
            readiness.to_dict() for readiness in credential_readiness
        ],
        "approval_receipt": (
            approval_validation.to_dict() if approval_validation else None
        ),
        "checks": [
            check.to_dict()
            for check in sorted(checks, key=lambda check: check.check_id)
        ],
        "blockers": sorted(blockers),
        "secret_material_persisted": False,
    }
    result_payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_operator_preflight_result_digest(result_payload),
    }
    result = OperatorPreflightResult.from_dict(result_payload)
    validate_operator_preflight_result(result, packet)
    return result


def render_operator_preflight_markdown(result: OperatorPreflightResult) -> str:
    """Render one deterministic, credential-value-free operator view."""

    lines = [
        "# Millefeuille Operator Preflight",
        "",
        f"- Packet: `{result.packet_id}`",
        f"- Packet digest: `{result.packet_digest}`",
        f"- Mode: `{result.mode}`",
        f"- Decision: `{result.decision}`",
        f"- Result digest: `{result.content_digest}`",
        "- External effects performed: `false`",
        "",
        "## Credential Readiness",
    ]
    if result.credential_readiness:
        for readiness in result.credential_readiness:
            lines.append(
                f"- `{readiness.credential_type}` via `{readiness.reference}`: "
                f"`{readiness.state}`"
            )
    else:
        lines.append("- `[none required]`")
    lines.extend(["", "## Checks"])
    for check in result.checks:
        lines.append(f"- `{check.check_id}`: `{check.status}`")
    lines.extend(["", "## Blockers"])
    if result.blockers:
        for blocker in result.blockers:
            lines.append(f"- `{blocker}`")
    else:
        lines.append("- `[none]`")
    if result.approval_receipt is not None:
        lines.extend(
            [
                "",
                "## Approval Receipt",
                f"- Receipt: `{result.approval_receipt.receipt_id}`",
                f"- Digest: `{result.approval_receipt.content_digest}`",
                f"- Status: `{result.approval_receipt.status}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _validate_supported_operation_scope(packet: OperatorPreflightPacket) -> None:
    if any(
        operation not in _OPERATION_DESTINATION_KINDS for operation in packet.operations
    ):
        raise MillefeuilleContractError(
            "operations contains an unsupported operation for this contract version"
        )

    for operation in packet.operations:
        allowed_kinds = _OPERATION_DESTINATION_KINDS[operation]
        if not any(
            destination.kind in allowed_kinds for destination in packet.destinations
        ):
            raise MillefeuilleContractError(
                "an operation is missing its required exact destination role"
            )
    for destination in packet.destinations:
        if not any(
            destination.kind in _OPERATION_DESTINATION_KINDS[operation]
            for operation in packet.operations
        ):
            raise MillefeuilleContractError(
                "a destination role is not authorized by any requested operation"
            )
        if (
            destination.kind == "artifact-root"
            and destination.id != compute_operator_root_target_id(packet.artifact_root)
        ):
            raise MillefeuilleContractError(
                "artifact-root destination identity drifts from roots.artifact_root"
            )
        if (
            destination.kind == "source-pack-root"
            and destination.id
            != compute_operator_root_target_id(packet.source_pack_root)
        ):
            raise MillefeuilleContractError(
                "source-pack-root destination identity drifts from "
                "roots.source_pack_root"
            )

    requires_provider = bool(_PROVIDER_OPERATIONS.intersection(packet.operations))
    if requires_provider != (packet.provider is not None):
        raise MillefeuilleContractError(
            "provider binding must exactly match supported provider operations"
        )


def _required_credential_types(packet: OperatorPreflightPacket) -> set[str]:
    required: set[str] = set()
    if packet.source.adapter == "zotero":
        required.update({"zotero-library-id", "zotero-read"})
    for operation in packet.operations:
        required.update(_OPERATION_CREDENTIAL_TYPES.get(operation, frozenset()))
    return required


def _root_identity(value: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(value)))


def _credential_reference_is_present(
    environment: Mapping[str, str],
    reference: str,
) -> bool:
    value = environment.get(reference)
    return isinstance(value, str) and bool(value.strip())


def _credential_requirement_from_object(
    payload: object,
    index: int,
) -> CredentialRequirement:
    value = _require_object(payload, f"credential_requirements[{index}]")
    _require_exact_fields(
        value,
        _CREDENTIAL_FIELDS,
        f"credential_requirements[{index}]",
    )
    return CredentialRequirement(
        credential_type=_required_string(
            value.get("type"), f"credential_requirements[{index}].type", maximum=64
        ),
        reference=_required_string(
            value.get("reference"),
            f"credential_requirements[{index}].reference",
            maximum=128,
        ),
    )


def _credential_readiness_from_object(
    payload: object,
    index: int,
) -> CredentialReadiness:
    label = f"result.credential_readiness[{index}]"
    value = _require_object(payload, label)
    _require_exact_fields(value, _CREDENTIAL_READINESS_FIELDS, label)
    return CredentialReadiness(
        credential_type=_required_string(
            value.get("type"), f"{label}.type", maximum=64
        ),
        reference=_required_string(
            value.get("reference"), f"{label}.reference", maximum=128
        ),
        state=_required_string(value.get("state"), f"{label}.state", maximum=16),
    )


def _preflight_check_from_object(payload: object, index: int) -> PreflightCheck:
    label = f"result.checks[{index}]"
    value = _require_object(payload, label)
    _require_exact_fields(value, _CHECK_FIELDS, label)
    return PreflightCheck(
        check_id=_required_string(value.get("id"), f"{label}.id", maximum=64),
        status=_required_string(value.get("status"), f"{label}.status", maximum=32),
    )


def _approval_validation_from_object(
    payload: object,
) -> ApprovalReceiptValidation:
    value = _require_object(payload, "result.approval_receipt")
    _require_exact_fields(
        value,
        _APPROVAL_VALIDATION_FIELDS,
        "result.approval_receipt",
    )
    return ApprovalReceiptValidation(
        receipt_id=_required_string(
            value.get("receipt_id"),
            "result.approval_receipt.receipt_id",
            maximum=256,
        ),
        content_digest=_required_string(
            value.get("content_digest"),
            "result.approval_receipt.content_digest",
            maximum=71,
        ),
        status=_required_string(
            value.get("status"),
            "result.approval_receipt.status",
            maximum=16,
        ),
    )


def _target_tuple(payload: object, label: str) -> tuple[LiveTarget, ...]:
    values = _require_sequence(payload, label)
    targets: list[LiveTarget] = []
    for index, item in enumerate(values):
        value = _require_object(item, f"{label}[{index}]")
        _require_exact_fields(value, _TARGET_FIELDS, f"{label}[{index}]")
        targets.append(
            LiveTarget(
                kind=_required_string(
                    value.get("kind"), f"{label}[{index}].kind", maximum=64
                ),
                id=_required_string(
                    value.get("id"), f"{label}[{index}].id", maximum=256
                ),
            )
        )
    return tuple(targets)


def _string_tuple(
    payload: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    return tuple(
        _required_string(value, f"{label}[{index}]", maximum=256)
        for index, value in enumerate(
            _require_sequence(payload, label, allow_empty=allow_empty)
        )
    )


def _validate_codes(values: tuple[str, ...], label: str) -> None:
    if not values:
        raise MillefeuilleContractError(f"{label} must not be empty")
    if len(values) > 32:
        raise MillefeuilleContractError(f"{label} has too many entries")
    if tuple(sorted(set(values))) != values:
        raise MillefeuilleContractError(f"{label} must be sorted and unique")
    for value in values:
        _validate_code(value, label)


def _validate_code(value: str, label: str) -> None:
    text = _required_string(value, label, maximum=256)
    if (
        _SAFE_CODE_RE.fullmatch(text) is None
        or _is_overbroad(text)
        or any(
            segment in {"all", "any", "everything"}
            for segment in re.split(r"[.-]", text)
        )
    ):
        raise MillefeuilleContractError(
            f"{label} contains an unsafe or over-broad code"
        )


def _safe_identity(value: str, label: str) -> None:
    text = _required_string(value, label, maximum=256)
    if _SAFE_ID_RE.fullmatch(text) is None or _is_overbroad(text):
        raise MillefeuilleContractError(f"{label} is not a safe exact identity")


def _safe_exact_value(value: str, label: str, *, maximum: int) -> None:
    text = _required_string(value, label, maximum=maximum)
    if _is_overbroad(text):
        raise MillefeuilleContractError(f"{label} must bind one exact value")
    _reject_secret_like(text, label)


def _required_string(value: object, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MillefeuilleContractError(f"{label} must be a non-empty unpadded string")
    if len(value) > maximum:
        raise MillefeuilleContractError(f"{label} exceeds maximum length")
    if unicodedata.normalize("NFC", value) != value:
        raise MillefeuilleContractError(f"{label} must use NFC Unicode")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise MillefeuilleContractError(
            f"{label} must not contain control or format characters"
        )
    return value


def _required_integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MillefeuilleContractError(f"{label} must be an integer")
    if value < minimum or value > _JSON_SAFE_INTEGER_MAX:
        raise MillefeuilleContractError(f"{label} is outside the allowed range")
    return value


def _require_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MillefeuilleContractError(f"{label} must be an object")
    return value


def _require_sequence(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> list[Any]:
    if not isinstance(value, list):
        raise MillefeuilleContractError(f"{label} must be an array")
    if not allow_empty and not value:
        raise MillefeuilleContractError(f"{label} must not be empty")
    if len(value) > 256:
        raise MillefeuilleContractError(f"{label} has too many entries")
    return value


def _require_exact_tuple(
    value: object,
    item_type: type[object],
    label: str,
) -> None:
    if type(value) is not tuple:
        raise MillefeuilleContractError(f"{label} must be an exact tuple")
    if any(type(item) is not item_type for item in value):
        raise MillefeuilleContractError(
            f"{label} contains an invalid direct-construction value"
        )


def _require_exact_fields(
    payload: dict[str, Any],
    expected: Collection[str],
    label: str,
) -> None:
    actual = set(payload)
    if actual - set(expected):
        raise MillefeuilleContractError(f"{label} contains unknown fields")
    missing = sorted(set(expected) - actual)
    if missing:
        raise MillefeuilleContractError(
            f"{label} is missing required fields: {', '.join(missing)}"
        )


def _is_overbroad(value: str) -> bool:
    normalized = value.casefold()
    return normalized in {"*", "all", "any", "everything"} or "*" in value


def _load_strict_json_object(
    path: str | Path,
    *,
    label: str,
    max_bytes: int,
) -> dict[str, Any]:
    payload_bytes = read_bytes_no_follow(
        Path(path),
        label,
        max_bytes=max_bytes,
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
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError(f"{label} must be an object")
    return payload


def _object_without_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name, value in pairs:
        if field_name in payload:
            raise MillefeuilleContractError(
                "operator preflight JSON contains duplicate fields"
            )
        payload[field_name] = value
    return payload


def _reject_non_finite_json_number(value: str) -> None:
    del value
    raise MillefeuilleContractError(
        "operator preflight JSON contains a non-finite number"
    )


def _reject_secret_like(value: object, label: str) -> None:
    if isinstance(value, dict):
        for field_name, nested in value.items():
            if field_name.casefold() in _FORBIDDEN_FIELD_NAMES:
                raise MillefeuilleContractError(
                    f"{label} contains a forbidden secret-bearing field"
                )
            _reject_secret_like(nested, label)
        return
    if isinstance(value, list | tuple):
        for nested in value:
            _reject_secret_like(nested, label)
        return
    if isinstance(value, str) and any(
        marker.search(value) for marker in _FORBIDDEN_VALUE_MARKERS
    ):
        raise MillefeuilleContractError(
            f"{label} contains credential, secret, or private payload material"
        )
