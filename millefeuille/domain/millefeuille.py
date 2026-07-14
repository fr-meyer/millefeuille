"""Offline Millefeuille contract models.

These models make the Zotero -> OpenKB evidence pipeline explicit without
performing live Zotero reads, PDF recovery, OCR calls, OpenKB writes, or release
actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MillefeuilleContractError(ValueError):
    """Raised when a Millefeuille contract object is internally inconsistent."""


class _StringEnum(StrEnum):
    @classmethod
    def values(cls) -> set[str]:
        return {member.value for member in cls}


class RunMode(_StringEnum):
    PREVIEW = "preview"
    READ_ONLY_LIVE = "read-only-live"
    APPROVED_LIVE = "approved-live"


class ManualGate(_StringEnum):
    GITHUB_PUBLICATION = "github_publication"
    ZOTERO_READ = "zotero_read"
    ZOTERO_WRITE = "zotero_write"
    PDF_RECOVERY = "pdf_recovery"
    OCR_PROVIDER_CALL = "ocr_provider_call"
    MODEL_PROVIDER_CALL = "model_provider_call"
    WORKER_AGENT_EXECUTION = "worker_agent_execution"
    SOURCE_PACK_WRITE = "source_pack_write"
    OPENKB_WRITE = "openkb_write"
    INDEX_WRITE = "index_write"
    STABLE_BRANCH_PROMOTION = "stable_branch_promotion"
    RELEASE_TAG = "release_tag"
    PACKAGE_PUBLICATION = "package_publication"


class StageName(_StringEnum):
    DISCOVER = "discover"
    HANDOFF = "handoff"
    RECOVER = "recover"
    SOURCE_PACK = "source-pack"
    EXTRACT_NATIVE = "extract-native"
    EXTRACT_OCR = "extract-ocr"
    ROUTE = "route"
    STRUCTURE = "structure"
    SUMMARIZE = "summarize"
    CARD = "card"
    OPENKB_ADD = "openkb-add"
    INDEX = "index"
    ACCEPTANCE = "acceptance"
    CLASSIFY = "classify"
    WRITEBACK = "writeback"
    RELEASE = "release"


class StageStatus(_StringEnum):
    NOT_STARTED = "not-started"
    SKIPPED = "skipped"
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs-review"
    MANUAL_GATE = "manual-gate"


class RouteSelection(_StringEnum):
    NATIVE = "native"
    OCR = "ocr"
    MERGED_DUAL = "merged-dual"


class ProviderPayloadDisposition(_StringEnum):
    DISCARDED = "discarded"
    QUARANTINED = "quarantined"
    RETAINED_OUTSIDE_COMMITTED_FILES = "retained-outside-committed-files"


class TagState(_StringEnum):
    SELECTED = "millefeuille"
    PREVIEWED = "millefeuille-previewed"
    HANDOFF_EXPORTED = "millefeuille-handoff-exported"
    SOURCE_VERIFIED = "millefeuille-source-verified"
    SOURCE_PACKED = "millefeuille-source-packed"
    EXTRACTED_NATIVE = "millefeuille-extracted-native"
    EXTRACTED_OCR = "millefeuille-extracted-ocr"
    STRUCTURE_READY = "millefeuille-structure-ready"
    SUMMARIZED = "millefeuille-summarized"
    CARD_READY = "millefeuille-card-ready"
    OPENKB_ADDED = "millefeuille-openkb-added"
    INDEXED = "millefeuille-indexed"
    ACCEPTANCE_PASSED = "millefeuille-acceptance-passed"
    READY_FOR_CLASSIFICATION = "millefeuille-ready-for-classification"
    CLASSIFIED = "millefeuille-classified"
    NEEDS_REVIEW = "millefeuille-needs-review"
    ERROR = "millefeuille-error"


TERMINAL_REVIEW_STATES = frozenset({
    TagState.NEEDS_REVIEW,
    TagState.ERROR,
})

ALLOWED_TAG_TRANSITIONS = frozenset({
    (TagState.SELECTED, TagState.PREVIEWED),
    (TagState.PREVIEWED, TagState.HANDOFF_EXPORTED),
    (TagState.HANDOFF_EXPORTED, TagState.SOURCE_VERIFIED),
    (TagState.SOURCE_VERIFIED, TagState.SOURCE_PACKED),
    (TagState.SOURCE_PACKED, TagState.EXTRACTED_NATIVE),
    (TagState.SOURCE_PACKED, TagState.EXTRACTED_OCR),
    (TagState.EXTRACTED_NATIVE, TagState.OPENKB_ADDED),
    (TagState.EXTRACTED_OCR, TagState.OPENKB_ADDED),
    (TagState.EXTRACTED_NATIVE, TagState.STRUCTURE_READY),
    (TagState.EXTRACTED_OCR, TagState.STRUCTURE_READY),
    (TagState.STRUCTURE_READY, TagState.SUMMARIZED),
    (TagState.SUMMARIZED, TagState.CARD_READY),
    (TagState.CARD_READY, TagState.OPENKB_ADDED),
    (TagState.CARD_READY, TagState.INDEXED),
    (TagState.OPENKB_ADDED, TagState.ACCEPTANCE_PASSED),
    (TagState.OPENKB_ADDED, TagState.INDEXED),
    (TagState.INDEXED, TagState.ACCEPTANCE_PASSED),
    (TagState.ACCEPTANCE_PASSED, TagState.READY_FOR_CLASSIFICATION),
    (TagState.READY_FOR_CLASSIFICATION, TagState.CLASSIFIED),
})


STAGE_MANUAL_GATES: dict[StageName, ManualGate | None] = {
    StageName.DISCOVER: ManualGate.ZOTERO_READ,
    StageName.HANDOFF: None,
    StageName.RECOVER: ManualGate.PDF_RECOVERY,
    StageName.SOURCE_PACK: ManualGate.SOURCE_PACK_WRITE,
    StageName.EXTRACT_NATIVE: None,
    StageName.EXTRACT_OCR: ManualGate.OCR_PROVIDER_CALL,
    StageName.ROUTE: None,
    StageName.STRUCTURE: None,
    StageName.SUMMARIZE: ManualGate.MODEL_PROVIDER_CALL,
    StageName.CARD: ManualGate.MODEL_PROVIDER_CALL,
    StageName.OPENKB_ADD: ManualGate.OPENKB_WRITE,
    StageName.INDEX: ManualGate.INDEX_WRITE,
    StageName.ACCEPTANCE: None,
    StageName.CLASSIFY: ManualGate.MODEL_PROVIDER_CALL,
    StageName.WRITEBACK: ManualGate.ZOTERO_WRITE,
    StageName.RELEASE: ManualGate.RELEASE_TAG,
}


def _coerce_enum(enum_cls: type[_StringEnum], value: str | _StringEnum) -> _StringEnum:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value))
    except ValueError as exc:
        raise MillefeuilleContractError(
            f"{enum_cls.__name__}: unsupported value {value!r}"
        ) from exc


def can_transition_tag(current: TagState | str, next_state: TagState | str) -> bool:
    """Return whether a tag-state transition is allowed by the offline contract."""
    current_state = _coerce_enum(TagState, current)
    destination = _coerce_enum(TagState, next_state)
    if destination in TERMINAL_REVIEW_STATES:
        return True
    return (current_state, destination) in ALLOWED_TAG_TRANSITIONS


@dataclass
class StageRecord:
    name: StageName | str
    status: StageStatus | str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    manual_gate_required: bool = False
    gate: ManualGate | str | None = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = _coerce_enum(StageName, self.name)
        self.status = _coerce_enum(StageStatus, self.status)
        if self.gate is not None:
            self.gate = _coerce_enum(ManualGate, self.gate)
        if self.manual_gate_required and self.gate is None:
            raise MillefeuilleContractError(
                "manual_gate_required stages must name a gate"
            )
        if self.status == StageStatus.MANUAL_GATE and not self.manual_gate_required:
            raise MillefeuilleContractError(
                "manual-gate status requires manual_gate_required=true"
            )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name.value,
            "status": self.status.value,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "manual_gate_required": self.manual_gate_required,
        }
        if self.gate is not None:
            result["gate"] = self.gate.value
        if self.notes:
            result["notes"] = list(self.notes)
        return result


@dataclass
class StageManifest:
    run_id: str
    mode: RunMode | str
    stages: list[StageRecord]
    manual_gates: list[ManualGate | str] = field(default_factory=list)
    schema_version: str = "millefeuille-stage-manifest/v0.1"
    source_type: str = "zotero"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-stage-manifest/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        if self.source_type != "zotero":
            raise MillefeuilleContractError(
                f"unsupported source_type {self.source_type!r}"
            )
        if not self.run_id.strip():
            raise MillefeuilleContractError("run_id must not be empty")
        self.mode = _coerce_enum(RunMode, self.mode)
        self.stages = [
            stage if isinstance(stage, StageRecord) else StageRecord(**stage)
            for stage in self.stages
        ]
        self.manual_gates = [
            _coerce_enum(ManualGate, gate) for gate in self.manual_gates
        ]
        known_gates = set(self.manual_gates)
        missing_gates = {
            stage.gate
            for stage in self.stages
            if stage.manual_gate_required and stage.gate not in known_gates
        }
        if missing_gates:
            missing = sorted(gate.value for gate in missing_gates if gate is not None)
            raise MillefeuilleContractError(
                f"manual_gates missing stage gate(s): {missing!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "source_type": self.source_type,
            "mode": self.mode.value,
            "manual_gates": [gate.value for gate in self.manual_gates],
            "stages": [stage.to_dict() for stage in self.stages],
        }


@dataclass
class AttachmentEvidenceIdentity:
    item_key: str
    attachment_key: str
    canonical_filename: str
    sha256: str
    zotero_version: int | None = None

    def __post_init__(self) -> None:
        if len(self.sha256) != 64:
            raise MillefeuilleContractError("attachment sha256 must be 64 chars")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "item_key": self.item_key,
            "attachment_key": self.attachment_key,
            "canonical_filename": self.canonical_filename,
            "sha256": self.sha256,
        }
        if self.zotero_version is not None:
            result["zotero_version"] = self.zotero_version
        return result


@dataclass
class NativeExtractionEvidenceRecord:
    tool: str
    source_pack: str
    attachment_identity: AttachmentEvidenceIdentity
    output_markdown_ref: str
    page_count: int
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.page_count < 0:
            raise MillefeuilleContractError("page_count must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "source_pack": self.source_pack,
            "attachment_identity": self.attachment_identity.to_dict(),
            "output_markdown_ref": self.output_markdown_ref,
            "page_count": self.page_count,
            "warnings": list(self.warnings),
        }


@dataclass
class OCREvidenceRecord:
    provider: str
    provider_version: str
    source_pack: str
    attachment_identity: AttachmentEvidenceIdentity
    output_markdown_ref: str
    page_count: int
    provider_payload_disposition: ProviderPayloadDisposition | str
    requested_model: str | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.provider_payload_disposition = _coerce_enum(
            ProviderPayloadDisposition,
            self.provider_payload_disposition,
        )
        if self.page_count < 0:
            raise MillefeuilleContractError("page_count must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "provider": self.provider,
            "provider_version": self.provider_version,
            "source_pack": self.source_pack,
            "attachment_identity": self.attachment_identity.to_dict(),
            "output_markdown_ref": self.output_markdown_ref,
            "page_count": self.page_count,
            "provider_payload_disposition": (
                self.provider_payload_disposition.value
            ),
            "warnings": list(self.warnings),
        }
        if self.requested_model is not None:
            payload["requested_model"] = self.requested_model
        return payload
