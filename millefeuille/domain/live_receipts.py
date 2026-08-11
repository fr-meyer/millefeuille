"""Fail-closed approval receipts for bounded live Millefeuille operations.

This module validates authorization metadata only.  It never reads credentials,
calls providers, mutates external systems, or promotes a preview into a live run.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import read_bytes_no_follow

APPROVED_LIVE_RECEIPT_SCHEMA_VERSION = "millefeuille-approved-live-receipt/v0.1"
APPROVED_LIVE_AUDIT_SCHEMA_VERSION = "millefeuille-approved-live-audit/v0.1"
APPROVED_LIVE_RECEIPT_MAX_BYTES = 65_536
APPROVED_LIVE_RECEIPT_MAX_VALIDITY = timedelta(hours=24)

_JSON_SAFE_INTEGER_MAX = (1 << 53) - 1
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "receipt_id",
        "run_id",
        "approval",
        "expires_at",
        "scope",
        "integrity",
    }
)
_APPROVAL_FIELDS = frozenset({"approver_id", "approved_at"})
_SCOPE_FIELDS = frozenset(
    {
        "operations",
        "targets",
        "max_items",
        "selector",
        "output_root",
        "source_pack_root",
        "provider",
        "limits",
        "disposal_policy",
        "stop_conditions",
    }
)
_TARGET_FIELDS = frozenset({"kind", "id"})
_SELECTOR_FIELDS = frozenset({"kind", "value"})
_PROVIDER_FIELDS = frozenset({"provider_id", "model_id"})
_LIMIT_FIELDS = frozenset({"max_provider_calls", "max_cost_usd_micros"})
_DISPOSAL_FIELDS = frozenset({"pdfs", "provider_payloads", "temporary_files"})
_INTEGRITY_FIELDS = frozenset({"algorithm", "content_digest"})
_AUDIT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "receipt_id",
        "receipt_digest",
        "request_digest",
        "run_id",
        "approval",
        "expires_at",
        "scope",
        "selected_item_count",
        "evaluated_at",
        "secret_material_persisted",
    }
)
_SELECTOR_KINDS = frozenset(
    {
        "zotero-tag",
        "zotero-query",
        "paper-id",
        "zotero-item-key",
        "source-pack",
        "batch-manifest",
        "doi",
        "title",
        "slug",
    }
)
_PDF_DISPOSITIONS = frozenset(
    {
        "delete-after-verification",
        "delete-after-run",
        "retain-until-expiry",
        "not-applicable",
    }
)
_PROVIDER_PAYLOAD_DISPOSITIONS = frozenset(
    {"never-persist", "delete-after-run", "not-applicable"}
)
_PROVIDER_OPERATION_PREFIXES = ("model.", "ocr.")
_TEMPORARY_FILE_DISPOSITIONS = frozenset(
    {"delete-after-run", "delete-on-failure", "not-applicable"}
)
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}\Z")
_SAFE_OPERATION_RE = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9][a-z0-9-]*)*\Z")
_SAFE_KIND_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_SAFE_STOP_CONDITION_RE = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9-]+)*\Z")
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
    re.compile(
        r"https?://[^/\s:@]+:[^/\s@]+@",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:[?&]|\b)(?:access_token|api_key|password|refresh_token|secret|token)="
        r"[^\s&]+",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class ApprovalIdentity:
    """The human or policy identity and time asserted by a receipt."""

    approver_id: str
    approved_at: str

    def __post_init__(self) -> None:
        _safe_identity(self.approver_id, "approval.approver_id")
        _parse_utc_timestamp(self.approved_at, "approval.approved_at")

    @classmethod
    def from_dict(cls, payload: object) -> ApprovalIdentity:
        value = _require_object(payload, "approval")
        _require_exact_fields(value, _APPROVAL_FIELDS, "approval")
        return cls(
            approver_id=_required_string(
                value.get("approver_id"),
                "approval.approver_id",
                maximum=256,
            ),
            approved_at=_required_string(
                value.get("approved_at"),
                "approval.approved_at",
                maximum=20,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "approver_id": self.approver_id,
            "approved_at": self.approved_at,
        }


@dataclass(frozen=True, order=True)
class LiveTarget:
    """One exact external or local live-operation target."""

    kind: str
    id: str

    def __post_init__(self) -> None:
        _safe_kind(self.kind, "target.kind")
        _safe_scope_value(self.id, "target.id", maximum=256)

    @classmethod
    def from_dict(cls, payload: object, index: int) -> LiveTarget:
        value = _require_object(payload, f"targets[{index}]")
        _require_exact_fields(value, _TARGET_FIELDS, f"targets[{index}]")
        return cls(
            kind=_required_string(value.get("kind"), f"targets[{index}].kind"),
            id=_required_string(
                value.get("id"),
                f"targets[{index}].id",
                maximum=256,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id}


@dataclass(frozen=True)
class LiveSelector:
    """One exact source selector used to choose the live work set."""

    kind: str
    value: str

    def __post_init__(self) -> None:
        if self.kind not in _SELECTOR_KINDS:
            raise MillefeuilleContractError("selector.kind is unsupported")
        _safe_scope_value(self.value, "selector.value", maximum=512)

    @classmethod
    def from_dict(cls, payload: object) -> LiveSelector:
        value = _require_object(payload, "selector")
        _require_exact_fields(value, _SELECTOR_FIELDS, "selector")
        return cls(
            kind=_required_string(value.get("kind"), "selector.kind"),
            value=_required_string(
                value.get("value"),
                "selector.value",
                maximum=512,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value}


@dataclass(frozen=True)
class LiveProvider:
    """Exact provider/model identity for a receipt that permits provider calls."""

    provider_id: str
    model_id: str

    def __post_init__(self) -> None:
        _safe_scope_value(self.provider_id, "provider.provider_id", maximum=128)
        _safe_scope_value(self.model_id, "provider.model_id", maximum=256)

    @classmethod
    def from_dict(cls, payload: object) -> LiveProvider:
        value = _require_object(payload, "provider")
        _require_exact_fields(value, _PROVIDER_FIELDS, "provider")
        return cls(
            provider_id=_required_string(
                value.get("provider_id"),
                "provider.provider_id",
                maximum=128,
            ),
            model_id=_required_string(
                value.get("model_id"),
                "provider.model_id",
                maximum=256,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {"provider_id": self.provider_id, "model_id": self.model_id}


@dataclass(frozen=True)
class LiveDisposalPolicy:
    """Required retention/disposal controls for sensitive live-run material."""

    pdfs: str
    provider_payloads: str
    temporary_files: str

    def __post_init__(self) -> None:
        if self.pdfs not in _PDF_DISPOSITIONS:
            raise MillefeuilleContractError("disposal_policy.pdfs is unsupported")
        if self.provider_payloads not in _PROVIDER_PAYLOAD_DISPOSITIONS:
            raise MillefeuilleContractError(
                "disposal_policy.provider_payloads is unsupported"
            )
        if self.temporary_files not in _TEMPORARY_FILE_DISPOSITIONS:
            raise MillefeuilleContractError(
                "disposal_policy.temporary_files is unsupported"
            )

    @classmethod
    def from_dict(cls, payload: object) -> LiveDisposalPolicy:
        value = _require_object(payload, "disposal_policy")
        _require_exact_fields(value, _DISPOSAL_FIELDS, "disposal_policy")
        return cls(
            pdfs=_required_string(value.get("pdfs"), "disposal_policy.pdfs"),
            provider_payloads=_required_string(
                value.get("provider_payloads"),
                "disposal_policy.provider_payloads",
            ),
            temporary_files=_required_string(
                value.get("temporary_files"),
                "disposal_policy.temporary_files",
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "pdfs": self.pdfs,
            "provider_payloads": self.provider_payloads,
            "temporary_files": self.temporary_files,
        }


@dataclass(frozen=True)
class ApprovedLiveScope:
    """The complete, exact scope authorized by one receipt."""

    operations: tuple[str, ...]
    targets: tuple[LiveTarget, ...]
    max_items: int
    selector: LiveSelector
    output_root: str
    source_pack_root: str
    provider: LiveProvider | None
    max_provider_calls: int
    max_cost_usd_micros: int
    disposal_policy: LiveDisposalPolicy
    stop_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_operations(self.operations)
        _validate_targets(self.targets)
        _required_integer(self.max_items, "scope.max_items", minimum=1)
        _absolute_root(self.output_root, "scope.output_root")
        _absolute_root(self.source_pack_root, "scope.source_pack_root")
        _required_integer(
            self.max_provider_calls,
            "scope.limits.max_provider_calls",
            minimum=0,
        )
        _required_integer(
            self.max_cost_usd_micros,
            "scope.limits.max_cost_usd_micros",
            minimum=0,
        )
        _validate_stop_conditions(self.stop_conditions)
        if _operations_require_provider(self.operations) and self.provider is None:
            raise MillefeuilleContractError(
                "model and OCR operations require an exact provider and model"
            )
        if self.provider is None:
            if self.max_provider_calls != 0 or self.max_cost_usd_micros != 0:
                raise MillefeuilleContractError(
                    "provider limits must be zero when scope.provider is null"
                )
            if self.disposal_policy.provider_payloads != "not-applicable":
                raise MillefeuilleContractError(
                    "provider payload disposal must be not-applicable without a "
                    "provider"
                )
        else:
            if self.max_provider_calls < 1:
                raise MillefeuilleContractError(
                    "max_provider_calls must be positive when a provider is bound"
                )
            if self.disposal_policy.provider_payloads == "not-applicable":
                raise MillefeuilleContractError(
                    "provider payload disposal is required when a provider is bound"
                )

    @classmethod
    def from_dict(cls, payload: object) -> ApprovedLiveScope:
        value = _require_object(payload, "scope")
        _require_exact_fields(value, _SCOPE_FIELDS, "scope")
        operations = _string_tuple(value.get("operations"), "scope.operations")
        raw_targets = _require_sequence(value.get("targets"), "scope.targets")
        targets = tuple(
            LiveTarget.from_dict(target, index)
            for index, target in enumerate(raw_targets)
        )
        limits = _require_object(value.get("limits"), "scope.limits")
        _require_exact_fields(limits, _LIMIT_FIELDS, "scope.limits")
        provider_payload = value.get("provider")
        provider = (
            None
            if provider_payload is None
            else LiveProvider.from_dict(provider_payload)
        )
        return cls(
            operations=operations,
            targets=targets,
            max_items=_required_integer(
                value.get("max_items"),
                "scope.max_items",
                minimum=1,
            ),
            selector=LiveSelector.from_dict(value.get("selector")),
            output_root=_required_string(
                value.get("output_root"),
                "scope.output_root",
                maximum=4096,
            ),
            source_pack_root=_required_string(
                value.get("source_pack_root"),
                "scope.source_pack_root",
                maximum=4096,
            ),
            provider=provider,
            max_provider_calls=_required_integer(
                limits.get("max_provider_calls"),
                "scope.limits.max_provider_calls",
                minimum=0,
            ),
            max_cost_usd_micros=_required_integer(
                limits.get("max_cost_usd_micros"),
                "scope.limits.max_cost_usd_micros",
                minimum=0,
            ),
            disposal_policy=LiveDisposalPolicy.from_dict(value.get("disposal_policy")),
            stop_conditions=_string_tuple(
                value.get("stop_conditions"),
                "scope.stop_conditions",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operations": list(self.operations),
            "targets": [target.to_dict() for target in self.targets],
            "max_items": self.max_items,
            "selector": self.selector.to_dict(),
            "output_root": self.output_root,
            "source_pack_root": self.source_pack_root,
            "provider": self.provider.to_dict() if self.provider else None,
            "limits": {
                "max_provider_calls": self.max_provider_calls,
                "max_cost_usd_micros": self.max_cost_usd_micros,
            },
            "disposal_policy": self.disposal_policy.to_dict(),
            "stop_conditions": list(self.stop_conditions),
        }


@dataclass(frozen=True)
class ApprovedLiveReceipt:
    """Validated approval receipt with exact content identity."""

    receipt_id: str
    run_id: str
    approval: ApprovalIdentity
    expires_at: str
    scope: ApprovedLiveScope
    content_digest: str

    def __post_init__(self) -> None:
        _safe_identity(self.receipt_id, "receipt_id")
        _safe_identity(self.run_id, "run_id")
        approved_at = _parse_utc_timestamp(
            self.approval.approved_at,
            "approval.approved_at",
        )
        expires_at = _parse_utc_timestamp(self.expires_at, "expires_at")
        if expires_at <= approved_at:
            raise MillefeuilleContractError(
                "expires_at must be later than approval.approved_at"
            )
        if expires_at - approved_at > APPROVED_LIVE_RECEIPT_MAX_VALIDITY:
            raise MillefeuilleContractError(
                "approved-live receipt validity must not exceed 24 hours"
            )
        if _DIGEST_RE.fullmatch(self.content_digest) is None:
            raise MillefeuilleContractError(
                "integrity.content_digest must be a sha256 digest"
            )
        expected_digest = compute_approved_live_receipt_digest(self.to_dict())
        if not hmac.compare_digest(self.content_digest, expected_digest):
            raise MillefeuilleContractError(
                "approved-live receipt content_digest mismatch"
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ApprovedLiveReceipt:
        _reject_secret_like(payload, "approved-live receipt")
        _require_exact_fields(payload, _RECEIPT_FIELDS, "approved-live receipt")
        if payload.get("schema_version") != APPROVED_LIVE_RECEIPT_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                "approved-live receipt schema_version is unsupported"
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
        expected_digest = compute_approved_live_receipt_digest(payload)
        if not hmac.compare_digest(supplied_digest, expected_digest):
            raise MillefeuilleContractError(
                "approved-live receipt content_digest mismatch"
            )
        return cls(
            receipt_id=_required_string(
                payload.get("receipt_id"),
                "receipt_id",
                maximum=256,
            ),
            run_id=_required_string(payload.get("run_id"), "run_id", maximum=256),
            approval=ApprovalIdentity.from_dict(payload.get("approval")),
            expires_at=_required_string(
                payload.get("expires_at"),
                "expires_at",
                maximum=20,
            ),
            scope=ApprovedLiveScope.from_dict(payload.get("scope")),
            content_digest=supplied_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
            "receipt_id": self.receipt_id,
            "run_id": self.run_id,
            "approval": self.approval.to_dict(),
            "expires_at": self.expires_at,
            "scope": self.scope.to_dict(),
            "integrity": {
                "algorithm": "sha256",
                "content_digest": self.content_digest,
            },
        }


@dataclass(frozen=True)
class ApprovedLiveRequest:
    """Independently derived controls for one proposed live execution."""

    run_id: str
    operations: tuple[str, ...]
    targets: tuple[LiveTarget, ...]
    item_cap: int
    selected_item_count: int
    selector: LiveSelector
    output_root: str
    source_pack_root: str
    provider: LiveProvider | None
    provider_call_limit: int
    cost_limit_usd_micros: int
    disposal_policy: LiveDisposalPolicy
    stop_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        _safe_identity(self.run_id, "request.run_id")
        _validate_operations(self.operations)
        _validate_targets(self.targets)
        _required_integer(self.item_cap, "request.item_cap", minimum=1)
        _required_integer(
            self.selected_item_count,
            "request.selected_item_count",
            minimum=0,
        )
        if self.selected_item_count > self.item_cap:
            raise MillefeuilleContractError(
                "request selected_item_count exceeds request item_cap"
            )
        _absolute_root(self.output_root, "request.output_root")
        _absolute_root(self.source_pack_root, "request.source_pack_root")
        _required_integer(
            self.provider_call_limit,
            "request.provider_call_limit",
            minimum=0,
        )
        _required_integer(
            self.cost_limit_usd_micros,
            "request.cost_limit_usd_micros",
            minimum=0,
        )
        _validate_stop_conditions(self.stop_conditions)
        if _operations_require_provider(self.operations) and self.provider is None:
            raise MillefeuilleContractError(
                "request model and OCR operations require an exact provider and model"
            )
        if self.provider is None and (
            self.provider_call_limit != 0 or self.cost_limit_usd_micros != 0
        ):
            raise MillefeuilleContractError(
                "request provider limits must be zero without a provider"
            )
        if self.provider is not None and self.provider_call_limit < 1:
            raise MillefeuilleContractError(
                "request provider_call_limit must be positive with a provider"
            )
        _reject_secret_like(self.to_dict(), "approved-live request")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "operations": list(self.operations),
            "targets": [target.to_dict() for target in self.targets],
            "item_cap": self.item_cap,
            "selected_item_count": self.selected_item_count,
            "selector": self.selector.to_dict(),
            "output_root": self.output_root,
            "source_pack_root": self.source_pack_root,
            "provider": self.provider.to_dict() if self.provider else None,
            "provider_call_limit": self.provider_call_limit,
            "cost_limit_usd_micros": self.cost_limit_usd_micros,
            "disposal_policy": self.disposal_policy.to_dict(),
            "stop_conditions": list(self.stop_conditions),
        }


@dataclass(frozen=True)
class ReceiptReplayState:
    """Previously consumed receipt identities from a durable audit ledger."""

    receipt_ids: frozenset[str] = frozenset()
    content_digests: frozenset[str] = frozenset()

    @classmethod
    def from_audit_records(
        cls,
        records: Iterable[dict[str, Any]],
    ) -> ReceiptReplayState:
        receipt_ids: set[str] = set()
        content_digests: set[str] = set()
        for record in records:
            _reject_secret_like(record, "approved-live audit record")
            _require_exact_fields(record, _AUDIT_FIELDS, "approved-live audit record")
            if record.get("schema_version") != APPROVED_LIVE_AUDIT_SCHEMA_VERSION:
                raise MillefeuilleContractError(
                    "approved-live audit schema_version is unsupported"
                )
            status = record.get("status")
            if status not in {"validated", "consumed"}:
                raise MillefeuilleContractError(
                    "approved-live audit status is unsupported"
                )
            receipt_id = _required_string(
                record.get("receipt_id"),
                "audit.receipt_id",
                maximum=256,
            )
            digest = _required_string(
                record.get("receipt_digest"),
                "audit.receipt_digest",
                maximum=71,
            )
            if _DIGEST_RE.fullmatch(digest) is None:
                raise MillefeuilleContractError(
                    "audit.receipt_digest must be a sha256 digest"
                )
            request_digest = _required_string(
                record.get("request_digest"),
                "audit.request_digest",
                maximum=71,
            )
            if _DIGEST_RE.fullmatch(request_digest) is None:
                raise MillefeuilleContractError(
                    "audit.request_digest must be a sha256 digest"
                )
            _safe_identity(receipt_id, "audit.receipt_id")
            run_id = _required_string(
                record.get("run_id"),
                "audit.run_id",
                maximum=256,
            )
            _safe_identity(run_id, "audit.run_id")
            approval = ApprovalIdentity.from_dict(record.get("approval"))
            approved_at = _parse_utc_timestamp(
                approval.approved_at,
                "audit.approval.approved_at",
            )
            expires_at_text = _required_string(
                record.get("expires_at"),
                "audit.expires_at",
                maximum=20,
            )
            expires_at = _parse_utc_timestamp(expires_at_text, "audit.expires_at")
            if expires_at <= approved_at or (
                expires_at - approved_at > APPROVED_LIVE_RECEIPT_MAX_VALIDITY
            ):
                raise MillefeuilleContractError(
                    "approved-live audit has an invalid approval window"
                )
            scope = ApprovedLiveScope.from_dict(record.get("scope"))
            selected_item_count = _required_integer(
                record.get("selected_item_count"),
                "audit.selected_item_count",
                minimum=0,
            )
            if selected_item_count > scope.max_items:
                raise MillefeuilleContractError(
                    "approved-live audit selected_item_count exceeds max_items"
                )
            evaluated_at = _parse_utc_timestamp(
                _required_string(
                    record.get("evaluated_at"),
                    "audit.evaluated_at",
                    maximum=20,
                ),
                "audit.evaluated_at",
            )
            if evaluated_at < approved_at or evaluated_at >= expires_at:
                raise MillefeuilleContractError(
                    "approved-live audit evaluation is outside the approval window"
                )
            if record.get("secret_material_persisted") is not False:
                raise MillefeuilleContractError(
                    "approved-live audit must not persist secret material"
                )
            expected_receipt_digest = compute_approved_live_receipt_digest(
                {
                    "schema_version": APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
                    "receipt_id": receipt_id,
                    "run_id": run_id,
                    "approval": approval.to_dict(),
                    "expires_at": expires_at_text,
                    "scope": scope.to_dict(),
                }
            )
            if not hmac.compare_digest(digest, expected_receipt_digest):
                raise MillefeuilleContractError(
                    "approved-live audit receipt_digest mismatch"
                )
            expected_request_digest = _digest_json_object(
                _request_payload_from_scope(
                    run_id=run_id,
                    scope=scope,
                    selected_item_count=selected_item_count,
                )
            )
            if not hmac.compare_digest(request_digest, expected_request_digest):
                raise MillefeuilleContractError(
                    "approved-live audit request_digest mismatch"
                )
            if status == "consumed":
                receipt_ids.add(receipt_id)
                content_digests.add(digest)
        return cls(frozenset(receipt_ids), frozenset(content_digests))


def load_approved_live_receipt(path: str | Path) -> ApprovedLiveReceipt:
    """Securely load and validate one bounded approved-live receipt."""

    target = Path(path)
    payload_bytes = read_bytes_no_follow(
        target,
        "approved-live receipt",
        max_bytes=APPROVED_LIVE_RECEIPT_MAX_BYTES,
    )
    if len(payload_bytes) > APPROVED_LIVE_RECEIPT_MAX_BYTES:
        raise MillefeuilleContractError("approved-live receipt exceeds 65536 bytes")
    try:
        text = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MillefeuilleContractError(
            "approved-live receipt is not valid UTF-8"
        ) from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_fields,
            parse_constant=_reject_non_finite_json_number,
        )
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            "approved-live receipt is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError("approved-live receipt must be an object")
    return ApprovedLiveReceipt.from_dict(payload)


def compute_approved_live_receipt_digest(payload: dict[str, Any]) -> str:
    """Return the canonical SHA-256 identity for a receipt body.

    The ``integrity`` member is excluded, so callers can calculate the digest
    before adding it.  Receipt fields contain no floating-point values.
    """

    body = dict(payload)
    body.pop("integrity", None)
    try:
        canonical = json.dumps(
            body,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MillefeuilleContractError(
            "approved-live receipt cannot be canonicalized"
        ) from exc
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def validate_approved_live_receipt(
    receipt: ApprovedLiveReceipt,
    request: ApprovedLiveRequest,
    *,
    now: datetime | None = None,
    replay_state: ReceiptReplayState | None = None,
) -> None:
    """Authorize only an exact, unexpired, unconsumed request scope."""

    evaluation_time = _normalize_now(now)
    approved_at = _parse_utc_timestamp(
        receipt.approval.approved_at,
        "approval.approved_at",
    )
    expires_at = _parse_utc_timestamp(receipt.expires_at, "expires_at")
    if evaluation_time < approved_at:
        raise MillefeuilleContractError("approved-live receipt is not active yet")
    if evaluation_time >= expires_at:
        raise MillefeuilleContractError("approved-live receipt has expired")

    replay = replay_state or ReceiptReplayState()
    if receipt.receipt_id in replay.receipt_ids:
        raise MillefeuilleContractError(
            "approved-live receipt has already been consumed"
        )
    if receipt.content_digest in replay.content_digests:
        raise MillefeuilleContractError(
            "approved-live receipt content has already been consumed"
        )

    _require_equal(receipt.run_id, request.run_id, "run_id")
    _require_equal(receipt.scope.operations, request.operations, "operations")
    _require_equal(receipt.scope.targets, request.targets, "targets")
    _require_equal(receipt.scope.max_items, request.item_cap, "max_items")
    if request.selected_item_count > receipt.scope.max_items:
        raise MillefeuilleContractError(
            "approved-live request exceeds receipt max_items"
        )
    _require_equal(receipt.scope.selector, request.selector, "selector")
    _require_root_equal(
        receipt.scope.output_root,
        request.output_root,
        "output_root",
    )
    _require_root_equal(
        receipt.scope.source_pack_root,
        request.source_pack_root,
        "source_pack_root",
    )
    _require_equal(receipt.scope.provider, request.provider, "provider")
    _require_equal(
        receipt.scope.max_provider_calls,
        request.provider_call_limit,
        "limits.max_provider_calls",
    )
    _require_equal(
        receipt.scope.max_cost_usd_micros,
        request.cost_limit_usd_micros,
        "limits.max_cost_usd_micros",
    )
    _require_equal(
        receipt.scope.disposal_policy,
        request.disposal_policy,
        "disposal_policy",
    )
    _require_equal(
        receipt.scope.stop_conditions,
        request.stop_conditions,
        "stop_conditions",
    )


def build_approved_live_audit_record(
    receipt: ApprovedLiveReceipt,
    request: ApprovedLiveRequest,
    *,
    evaluated_at: datetime | None = None,
    status: str = "validated",
    replay_state: ReceiptReplayState | None = None,
) -> dict[str, Any]:
    """Return a strict allowlisted audit object without credential material."""

    if status not in {"validated", "consumed"}:
        raise MillefeuilleContractError("approved-live audit status is unsupported")
    evaluation_time = _normalize_now(evaluated_at)
    validate_approved_live_receipt(
        receipt,
        request,
        now=evaluation_time,
        replay_state=replay_state,
    )
    request_payload = _canonical_request_digest_payload(request.to_dict())
    request_digest = _digest_json_object(request_payload)
    record: dict[str, Any] = {
        "schema_version": APPROVED_LIVE_AUDIT_SCHEMA_VERSION,
        "status": status,
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.content_digest,
        "request_digest": request_digest,
        "run_id": receipt.run_id,
        "approval": receipt.approval.to_dict(),
        "expires_at": receipt.expires_at,
        "scope": receipt.scope.to_dict(),
        "selected_item_count": request.selected_item_count,
        "evaluated_at": _format_utc_timestamp(evaluation_time),
        "secret_material_persisted": False,
    }
    _require_exact_fields(record, _AUDIT_FIELDS, "approved-live audit record")
    _reject_secret_like(record, "approved-live audit record")
    return record


def _digest_json_object(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _request_payload_from_scope(
    *,
    run_id: str,
    scope: ApprovedLiveScope,
    selected_item_count: int,
) -> dict[str, Any]:
    return _canonical_request_digest_payload(
        {
            "run_id": run_id,
            "operations": list(scope.operations),
            "targets": [target.to_dict() for target in scope.targets],
            "item_cap": scope.max_items,
            "selected_item_count": selected_item_count,
            "selector": scope.selector.to_dict(),
            "output_root": scope.output_root,
            "source_pack_root": scope.source_pack_root,
            "provider": scope.provider.to_dict() if scope.provider else None,
            "provider_call_limit": scope.max_provider_calls,
            "cost_limit_usd_micros": scope.max_cost_usd_micros,
            "disposal_policy": scope.disposal_policy.to_dict(),
            "stop_conditions": list(scope.stop_conditions),
        }
    )


def _canonical_request_digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    canonical = dict(payload)
    canonical["output_root"] = _root_identity(canonical["output_root"])
    canonical["source_pack_root"] = _root_identity(canonical["source_pack_root"])
    return canonical


def _object_without_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for name, value in pairs:
        if name in payload:
            raise MillefeuilleContractError(
                "approved-live receipt contains duplicate fields"
            )
        payload[name] = value
    return payload


def _reject_non_finite_json_number(value: str) -> None:
    del value
    raise MillefeuilleContractError(
        "approved-live receipt contains a non-finite number"
    )


def _required_string(value: object, field_name: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value:
        raise MillefeuilleContractError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise MillefeuilleContractError(
            f"{field_name} must not have leading or trailing whitespace"
        )
    if len(value) > maximum:
        raise MillefeuilleContractError(f"{field_name} is too long")
    if "\x00" in value:
        raise MillefeuilleContractError(f"{field_name} must not contain NUL")
    return value


def _required_integer(value: object, field_name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MillefeuilleContractError(f"{field_name} must be an integer")
    if value < minimum or value > _JSON_SAFE_INTEGER_MAX:
        raise MillefeuilleContractError(f"{field_name} is outside the allowed range")
    return value


def _require_object(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MillefeuilleContractError(f"{field_name} must be an object")
    return value


def _require_sequence(value: object, field_name: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise MillefeuilleContractError(f"{field_name} must be an array")
    if not value:
        raise MillefeuilleContractError(f"{field_name} must not be empty")
    if len(value) > 256:
        raise MillefeuilleContractError(f"{field_name} has too many entries")
    return value


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    values = _require_sequence(value, field_name)
    return tuple(
        _required_string(item, f"{field_name}[{index}]", maximum=256)
        for index, item in enumerate(values)
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


def _safe_identity(value: str, field_name: str) -> None:
    text = _required_string(value, field_name, maximum=256)
    if _SAFE_ID_RE.fullmatch(text) is None or _is_overbroad(text):
        raise MillefeuilleContractError(f"{field_name} is not a safe exact identity")


def _safe_kind(value: str, field_name: str) -> None:
    text = _required_string(value, field_name, maximum=64)
    if _SAFE_KIND_RE.fullmatch(text) is None or _is_overbroad(text):
        raise MillefeuilleContractError(f"{field_name} is not a safe exact kind")


def _safe_scope_value(value: str, field_name: str, *, maximum: int) -> None:
    text = _required_string(value, field_name, maximum=maximum)
    if _is_overbroad(text):
        raise MillefeuilleContractError(f"{field_name} must bind one exact value")
    _reject_secret_like(text, field_name)


def _is_overbroad(value: str) -> bool:
    normalized = value.casefold()
    return normalized in {"*", "all", "any", "everything"} or "*" in value


def _validate_operations(operations: tuple[str, ...]) -> None:
    if not operations:
        raise MillefeuilleContractError("scope.operations must not be empty")
    if len(operations) > 32:
        raise MillefeuilleContractError("scope.operations has too many entries")
    if tuple(sorted(set(operations))) != operations:
        raise MillefeuilleContractError("scope.operations must be sorted and unique")
    for operation in operations:
        if (
            _SAFE_OPERATION_RE.fullmatch(operation) is None
            or _is_overbroad(operation)
            or any(
                segment in {"all", "any", "everything"}
                for segment in re.split(r"[.-]", operation)
            )
        ):
            raise MillefeuilleContractError(
                "scope.operations contains an unsafe or over-broad operation"
            )


def _operations_require_provider(operations: tuple[str, ...]) -> bool:
    return any(
        operation.startswith(_PROVIDER_OPERATION_PREFIXES) for operation in operations
    )


def _validate_targets(targets: tuple[LiveTarget, ...]) -> None:
    if not targets:
        raise MillefeuilleContractError("scope.targets must not be empty")
    if len(targets) > 256:
        raise MillefeuilleContractError("scope.targets has too many entries")
    if tuple(sorted(set(targets))) != targets:
        raise MillefeuilleContractError("scope.targets must be sorted and unique")


def _validate_stop_conditions(stop_conditions: tuple[str, ...]) -> None:
    if not stop_conditions:
        raise MillefeuilleContractError("scope.stop_conditions must not be empty")
    if len(stop_conditions) > 32:
        raise MillefeuilleContractError("scope.stop_conditions has too many entries")
    if tuple(sorted(set(stop_conditions))) != stop_conditions:
        raise MillefeuilleContractError(
            "scope.stop_conditions must be sorted and unique"
        )
    for condition in stop_conditions:
        if _SAFE_STOP_CONDITION_RE.fullmatch(condition) is None:
            raise MillefeuilleContractError(
                "scope.stop_conditions contains an unsafe condition"
            )


def _absolute_root(value: str, field_name: str) -> str:
    text = _required_string(value, field_name, maximum=4096)
    path = Path(text)
    if not path.is_absolute():
        raise MillefeuilleContractError(f"{field_name} must be an absolute path")
    if any(part in {".", ".."} for part in path.parts):
        raise MillefeuilleContractError(
            f"{field_name} must not contain traversal components"
        )
    if os.path.normpath(text) != text:
        raise MillefeuilleContractError(f"{field_name} must be normalized")
    _reject_secret_like(text, field_name)
    return text


def _root_identity(value: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(value)))


def _parse_utc_timestamp(value: str, field_name: str) -> datetime:
    text = _required_string(value, field_name, maximum=20)
    if _TIMESTAMP_RE.fullmatch(text) is None:
        raise MillefeuilleContractError(
            f"{field_name} must be a canonical UTC timestamp ending in Z"
        )
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise MillefeuilleContractError(
            f"{field_name} is not a valid timestamp"
        ) from exc


def _normalize_now(value: datetime | None) -> datetime:
    now = datetime.now(UTC) if value is None else value
    if now.tzinfo is None or now.utcoffset() is None:
        raise MillefeuilleContractError(
            "approval evaluation time must be timezone-aware"
        )
    return now.astimezone(UTC)


def _format_utc_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_equal(actual: object, expected: object, field_name: str) -> None:
    if actual != expected:
        raise MillefeuilleContractError(f"approved-live receipt {field_name} drift")


def _require_root_equal(actual: str, expected: str, field_name: str) -> None:
    if _root_identity(actual) != _root_identity(expected):
        raise MillefeuilleContractError(f"approved-live receipt {field_name} drift")


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
