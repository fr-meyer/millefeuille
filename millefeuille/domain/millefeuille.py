"""Offline Millefeuille contract models.

These models make the Zotero -> OpenKB evidence pipeline explicit without
performing live Zotero reads, PDF recovery, OCR calls, OpenKB writes, or release
actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
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


class SummaryGrain(_StringEnum):
    PAGE = "page"
    SECTION = "section"
    FIGURE_TABLE = "figure-table"
    FULL_PAPER = "full-paper"
    SCOPE_SPECIFIC = "scope-specific"


class SummaryScope(_StringEnum):
    CLASSIFICATION = "classification"
    LITERATURE_REVIEW = "literature-review"
    TECHNICAL = "technical"
    DOMAIN = "domain"
    QUICK_READ = "quick-read"
    GENERAL = "general"


class AcceptanceStatus(_StringEnum):
    PASS = "pass"
    NEEDS_REVIEW = "needs-review"


class AcceptanceCheckStatus(_StringEnum):
    PASSED = "passed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs-review"


class ClassificationMode(_StringEnum):
    SINGLE = "single"
    BATCH = "batch"
    REVIEW = "review"
    ADJUDICATE = "adjudicate"
    MULTI_AGENT = "multi-agent"


class ClassificationStatus(_StringEnum):
    CLASSIFIED = "classified"
    NEEDS_REVIEW = "needs-review"
    ADJUDICATION_REQUIRED = "adjudication-required"


class ClassificationActionOutcome(_StringEnum):
    NO_CHANGE = "no-change"
    CORRECTED = "corrected"
    ESCALATED = "escalated"
    CONFIRMED = "confirmed"
    TAXONOMY_CHANGE_REQUESTED = "taxonomy-change-requested"


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


TERMINAL_REVIEW_STATES = frozenset(
    {
        TagState.NEEDS_REVIEW,
        TagState.ERROR,
    }
)

ALLOWED_TAG_TRANSITIONS = frozenset(
    {
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
    }
)


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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StageRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("stage record must be an object")
        return cls(
            name=payload.get("name", ""),
            status=payload.get("status", ""),
            inputs=list(payload.get("inputs", [])),
            outputs=list(payload.get("outputs", [])),
            manual_gate_required=bool(payload.get("manual_gate_required", False)),
            gate=payload.get("gate"),
            notes=list(payload.get("notes", [])),
        )


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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StageManifest:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("stage manifest must be an object")
        stages = payload.get("stages")
        if not isinstance(stages, list):
            raise MillefeuilleContractError("stage manifest stages must be an array")
        manual_gates = payload.get("manual_gates", [])
        if not isinstance(manual_gates, list):
            raise MillefeuilleContractError(
                "stage manifest manual_gates must be an array"
            )
        return cls(
            run_id=str(payload.get("run_id", "")).strip(),
            mode=str(payload.get("mode", "")).strip(),
            stages=stages,
            manual_gates=manual_gates,
            schema_version=str(payload.get("schema_version", "")).strip(),
            source_type=str(payload.get("source_type", "")).strip(),
        )


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
            "provider_payload_disposition": (self.provider_payload_disposition.value),
            "warnings": list(self.warnings),
        }
        if self.requested_model is not None:
            payload["requested_model"] = self.requested_model
        return payload


@dataclass
class RouteEvidenceRecord:
    selected_route: RouteSelection | str
    source_pack: str
    attachment_identity: AttachmentEvidenceIdentity
    output_markdown_ref: str
    page_count: int
    extraction_routes: list[str]
    dual_extraction_complete: bool
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.selected_route = _coerce_enum(RouteSelection, self.selected_route)
        if self.page_count < 0:
            raise MillefeuilleContractError("page_count must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "selected_route": self.selected_route.value,
            "source_pack": self.source_pack,
            "attachment_identity": self.attachment_identity.to_dict(),
            "output_markdown_ref": self.output_markdown_ref,
            "page_count": self.page_count,
            "extraction_routes": list(self.extraction_routes),
            "dual_extraction_complete": self.dual_extraction_complete,
            "warnings": list(self.warnings),
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


@dataclass
class StructureEvidenceRecord:
    structure_backend: str
    selected_route: RouteSelection | str
    source_pack: str
    attachment_identity: AttachmentEvidenceIdentity
    source_markdown_ref: str
    page_count: int
    section_count: int
    table_count: int
    figure_count: int
    reference_count: int
    coverage: dict[str, Any]
    structure: dict[str, Any]
    outline_markdown_ref: str | None = None
    low_confidence_refs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.selected_route = _coerce_enum(RouteSelection, self.selected_route)
        for field_name in (
            "page_count",
            "section_count",
            "table_count",
            "figure_count",
            "reference_count",
        ):
            value = getattr(self, field_name)
            if value < 0:
                raise MillefeuilleContractError(f"{field_name} must be non-negative")
        if not isinstance(self.coverage, dict):
            raise MillefeuilleContractError("coverage must be an object")
        if not isinstance(self.structure, dict):
            raise MillefeuilleContractError("structure must be an object")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "structure_backend": self.structure_backend,
            "selected_route": self.selected_route.value,
            "source_pack": self.source_pack,
            "attachment_identity": self.attachment_identity.to_dict(),
            "source_markdown_ref": self.source_markdown_ref,
            "page_count": self.page_count,
            "section_count": self.section_count,
            "table_count": self.table_count,
            "figure_count": self.figure_count,
            "reference_count": self.reference_count,
            "coverage": dict(self.coverage),
            "structure": dict(self.structure),
            "low_confidence_refs": list(self.low_confidence_refs),
            "warnings": list(self.warnings),
        }
        if self.outline_markdown_ref is not None:
            payload["outline_markdown_ref"] = self.outline_markdown_ref
        return payload


@dataclass
class SummaryEntryRecord:
    summary_id: str
    grain: SummaryGrain | str
    scope: SummaryScope | str
    text_ref: str
    source_locators: list[str]
    depends_on: list[str] = field(default_factory=list)
    model_provenance_ref: str | None = None
    quality_warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.grain = _coerce_enum(SummaryGrain, self.grain)
        self.scope = _coerce_enum(SummaryScope, self.scope)
        if not self.summary_id.strip():
            raise MillefeuilleContractError("summary_id must not be empty")
        if not self.text_ref.strip():
            raise MillefeuilleContractError("text_ref must not be empty")
        for field_name in ("source_locators", "depends_on", "quality_warnings"):
            value = getattr(self, field_name)
            if not isinstance(value, list):
                raise MillefeuilleContractError(f"{field_name} must be an array")
            for entry in value:
                if not isinstance(entry, str) or not entry.strip():
                    raise MillefeuilleContractError(
                        f"{field_name} must contain non-empty strings"
                    )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SummaryEntryRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("summary entry must be an object")
        return cls(
            summary_id=str(payload.get("summary_id", "")).strip(),
            grain=str(payload.get("grain", "")).strip(),
            scope=str(payload.get("scope", "")).strip(),
            text_ref=str(payload.get("text_ref", "")).strip(),
            source_locators=list(payload.get("source_locators", [])),
            depends_on=list(payload.get("depends_on", [])),
            model_provenance_ref=payload.get("model_provenance_ref"),
            quality_warnings=list(payload.get("quality_warnings", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "summary_id": self.summary_id,
            "grain": self.grain.value,
            "scope": self.scope.value,
            "text_ref": self.text_ref,
            "source_locators": list(self.source_locators),
        }
        if self.depends_on:
            payload["depends_on"] = list(self.depends_on)
        if self.model_provenance_ref is not None:
            payload["model_provenance_ref"] = self.model_provenance_ref
        if self.quality_warnings:
            payload["quality_warnings"] = list(self.quality_warnings)
        return payload


@dataclass
class HierarchicalSummaryRecord:
    paper_id: str
    run_id: str
    summaries: list[SummaryEntryRecord | dict[str, Any]]
    taxonomy_context: dict[str, Any] | None = None
    schema_version: str = "millefeuille-hierarchical-summary/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-hierarchical-summary/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        if not self.paper_id.strip():
            raise MillefeuilleContractError("paper_id must not be empty")
        if not self.run_id.strip():
            raise MillefeuilleContractError("run_id must not be empty")
        if self.taxonomy_context is not None and not isinstance(
            self.taxonomy_context, dict
        ):
            raise MillefeuilleContractError(
                "taxonomy_context must be an object or null"
            )
        self.summaries = [
            summary
            if isinstance(summary, SummaryEntryRecord)
            else SummaryEntryRecord.from_dict(summary)
            for summary in self.summaries
        ]
        if not self.summaries:
            raise MillefeuilleContractError("summaries must not be empty")
        summary_ids = [summary.summary_id for summary in self.summaries]
        if len(summary_ids) != len(set(summary_ids)):
            raise MillefeuilleContractError("summary_id values must be unique")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> HierarchicalSummaryRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("hierarchical summary must be an object")
        summaries = payload.get("summaries")
        if not isinstance(summaries, list):
            raise MillefeuilleContractError("summaries must be an array")
        return cls(
            paper_id=str(payload.get("paper_id", "")).strip(),
            run_id=str(payload.get("run_id", "")).strip(),
            taxonomy_context=payload.get("taxonomy_context"),
            summaries=summaries,
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "taxonomy_context": (
                None if self.taxonomy_context is None else dict(self.taxonomy_context)
            ),
            "summaries": [summary.to_dict() for summary in self.summaries],
        }


@dataclass
class PaperCardRecord:
    paper_id: str
    identity: dict[str, Any]
    one_line_thesis: str
    primary_contribution: str
    evidence_refs: list[str]
    index_status: list[dict[str, Any]]
    model_provenance: dict[str, Any]
    problem_addressed: str | None = None
    method_or_approach: str | None = None
    data_modality_domain: str | None = None
    main_results: str | None = None
    limitations: str | None = None
    classification_clues: list[str] = field(default_factory=list)
    strongest_rejected_classification_path: str | None = None
    quality_warnings: list[str] = field(default_factory=list)
    zotero_lifecycle_tag_state: str | None = None
    schema_version: str = "millefeuille-paper-card/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-paper-card/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        if not self.paper_id.strip():
            raise MillefeuilleContractError("paper_id must not be empty")
        for field_name in ("one_line_thesis", "primary_contribution"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        if not isinstance(self.identity, dict):
            raise MillefeuilleContractError("identity must be an object")
        title = self.identity.get("title")
        if not isinstance(title, str) or not title.strip():
            raise MillefeuilleContractError("identity.title must be a non-empty string")
        if not isinstance(self.model_provenance, dict):
            raise MillefeuilleContractError("model_provenance must be an object")
        profile_id = self.model_provenance.get("profile_id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise MillefeuilleContractError(
                "model_provenance.profile_id must be a non-empty string"
            )
        if not isinstance(self.evidence_refs, list) or not self.evidence_refs:
            raise MillefeuilleContractError("evidence_refs must be a non-empty array")
        for ref in self.evidence_refs:
            if not isinstance(ref, str) or not ref.strip():
                raise MillefeuilleContractError(
                    "evidence_refs must contain non-empty strings"
                )
        if not isinstance(self.index_status, list):
            raise MillefeuilleContractError("index_status must be an array")
        for entry in self.index_status:
            if not isinstance(entry, dict):
                raise MillefeuilleContractError("index_status entries must be objects")
            lane = entry.get("lane")
            status = entry.get("status")
            if not isinstance(lane, str) or not lane.strip():
                raise MillefeuilleContractError(
                    "index_status.lane must be a non-empty string"
                )
            if not isinstance(status, str) or not status.strip():
                raise MillefeuilleContractError(
                    "index_status.status must be a non-empty string"
                )
        for field_name in ("classification_clues", "quality_warnings"):
            value = getattr(self, field_name)
            if not isinstance(value, list):
                raise MillefeuilleContractError(f"{field_name} must be an array")
            for entry in value:
                if not isinstance(entry, str) or not entry.strip():
                    raise MillefeuilleContractError(
                        f"{field_name} must contain non-empty strings"
                    )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PaperCardRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("paper card must be an object")
        evidence_refs = payload.get("evidence_refs")
        index_status = payload.get("index_status")
        if not isinstance(evidence_refs, list):
            raise MillefeuilleContractError("evidence_refs must be an array")
        if not isinstance(index_status, list):
            raise MillefeuilleContractError("index_status must be an array")
        return cls(
            paper_id=str(payload.get("paper_id", "")).strip(),
            identity=dict(payload.get("identity", {})),
            one_line_thesis=str(payload.get("one_line_thesis", "")).strip(),
            primary_contribution=str(payload.get("primary_contribution", "")).strip(),
            evidence_refs=list(evidence_refs),
            index_status=[dict(entry) for entry in index_status],
            model_provenance=dict(payload.get("model_provenance", {})),
            problem_addressed=payload.get("problem_addressed"),
            method_or_approach=payload.get("method_or_approach"),
            data_modality_domain=payload.get("data_modality_domain"),
            main_results=payload.get("main_results"),
            limitations=payload.get("limitations"),
            classification_clues=list(payload.get("classification_clues", [])),
            strongest_rejected_classification_path=payload.get(
                "strongest_rejected_classification_path"
            ),
            quality_warnings=list(payload.get("quality_warnings", [])),
            zotero_lifecycle_tag_state=payload.get("zotero_lifecycle_tag_state"),
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "paper_id": self.paper_id,
            "identity": dict(self.identity),
            "one_line_thesis": self.one_line_thesis,
            "primary_contribution": self.primary_contribution,
            "evidence_refs": list(self.evidence_refs),
            "index_status": [dict(entry) for entry in self.index_status],
            "model_provenance": dict(self.model_provenance),
        }
        optional_string_fields = {
            "problem_addressed": self.problem_addressed,
            "method_or_approach": self.method_or_approach,
            "data_modality_domain": self.data_modality_domain,
            "main_results": self.main_results,
            "limitations": self.limitations,
            "strongest_rejected_classification_path": (
                self.strongest_rejected_classification_path
            ),
            "zotero_lifecycle_tag_state": self.zotero_lifecycle_tag_state,
        }
        for field_name, value in optional_string_fields.items():
            if value is not None:
                payload[field_name] = value
        if self.classification_clues:
            payload["classification_clues"] = list(self.classification_clues)
        if self.quality_warnings:
            payload["quality_warnings"] = list(self.quality_warnings)
        return payload


INDEX_LANE_VALUES = frozenset({"openkb", "pageindex", "condb", "chatindex", "other"})
INDEX_STATUS_VALUES = frozenset(
    {
        "skipped",
        "previewed",
        "written",
        "failed",
        "needs-review",
    }
)


@dataclass
class IndexLaneRecord:
    lane: str
    status: str
    skip_reason: str | None = None
    target: dict[str, Any] | None = None
    chunking_profile: dict[str, Any] | None = None
    quality_warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.lane = self.lane.strip()
        self.status = self.status.strip()
        if not self.lane:
            raise MillefeuilleContractError("lane must be a non-empty string")
        if self.lane not in INDEX_LANE_VALUES:
            raise MillefeuilleContractError(
                f"lane must be one of {sorted(INDEX_LANE_VALUES)!r}"
            )
        if not self.status:
            raise MillefeuilleContractError("status must be a non-empty string")
        if self.status not in INDEX_STATUS_VALUES:
            raise MillefeuilleContractError(
                f"status must be one of {sorted(INDEX_STATUS_VALUES)!r}"
            )
        if self.status == "skipped":
            if not isinstance(self.skip_reason, str) or not self.skip_reason.strip():
                raise MillefeuilleContractError(
                    "skip_reason must be provided when lane status is skipped"
                )
            self.skip_reason = self.skip_reason.strip()
        elif self.skip_reason is not None:
            if not isinstance(self.skip_reason, str) or not self.skip_reason.strip():
                raise MillefeuilleContractError(
                    "skip_reason must be a non-empty string when provided"
                )
            self.skip_reason = self.skip_reason.strip()
        if self.target is not None and not isinstance(self.target, dict):
            raise MillefeuilleContractError("target must be an object or null")
        if self.chunking_profile is not None and not isinstance(
            self.chunking_profile, dict
        ):
            raise MillefeuilleContractError(
                "chunking_profile must be an object or null"
            )
        if not isinstance(self.quality_warnings, list):
            raise MillefeuilleContractError("quality_warnings must be an array")
        for entry in self.quality_warnings:
            if not isinstance(entry, str) or not entry.strip():
                raise MillefeuilleContractError(
                    "quality_warnings must contain non-empty strings"
                )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> IndexLaneRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("index lane must be an object")
        return cls(
            lane=str(payload.get("lane", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            skip_reason=payload.get("skip_reason"),
            target=payload.get("target"),
            chunking_profile=payload.get("chunking_profile"),
            quality_warnings=list(payload.get("quality_warnings", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "lane": self.lane,
            "status": self.status,
        }
        if self.skip_reason is not None:
            payload["skip_reason"] = self.skip_reason
        if self.target is not None:
            payload["target"] = dict(self.target)
        if self.chunking_profile is not None:
            payload["chunking_profile"] = dict(self.chunking_profile)
        if self.quality_warnings:
            payload["quality_warnings"] = list(self.quality_warnings)
        return payload


@dataclass
class RetrievalIndexRecord:
    paper_id: str
    run_id: str
    source_hash: str
    selected_fulltext_ref: str
    summary_ref: str
    paper_card_ref: str
    lanes: list[IndexLaneRecord | dict[str, Any]]
    duplicate_scan: dict[str, Any] | None = None
    collision_summary: dict[str, Any] | None = None
    quality_warnings: list[str] = field(default_factory=list)
    schema_version: str = "millefeuille-retrieval-index-status/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-retrieval-index-status/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        for field_name in (
            "paper_id",
            "run_id",
            "source_hash",
            "selected_fulltext_ref",
            "summary_ref",
            "paper_card_ref",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        if re.fullmatch(
            r"sha256(?:-aggregate)?:[0-9a-f]{64}",
            self.source_hash,
        ) is None:
            raise MillefeuilleContractError(
                "source_hash must use sha256:<hex> or "
                "sha256-aggregate:<hex> form"
            )
        if self.duplicate_scan is not None and not isinstance(
            self.duplicate_scan, dict
        ):
            raise MillefeuilleContractError("duplicate_scan must be an object or null")
        if self.collision_summary is not None and not isinstance(
            self.collision_summary, dict
        ):
            raise MillefeuilleContractError(
                "collision_summary must be an object or null"
            )
        if not isinstance(self.quality_warnings, list):
            raise MillefeuilleContractError("quality_warnings must be an array")
        for entry in self.quality_warnings:
            if not isinstance(entry, str) or not entry.strip():
                raise MillefeuilleContractError(
                    "quality_warnings must contain non-empty strings"
                )
        self.lanes = [
            lane
            if isinstance(lane, IndexLaneRecord)
            else IndexLaneRecord.from_dict(lane)
            for lane in self.lanes
        ]
        if not self.lanes:
            raise MillefeuilleContractError("lanes must not be empty")
        lane_names = [lane.lane for lane in self.lanes]
        if len(lane_names) != len(set(lane_names)):
            raise MillefeuilleContractError("lane values must be unique")
        required_lanes = {"openkb", "pageindex"}
        missing_lanes = required_lanes - set(lane_names)
        if missing_lanes:
            raise MillefeuilleContractError(
                f"lanes missing required entries: {sorted(missing_lanes)!r}"
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RetrievalIndexRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("retrieval index status must be an object")
        lanes = payload.get("lanes")
        if not isinstance(lanes, list):
            raise MillefeuilleContractError("lanes must be an array")
        return cls(
            paper_id=str(payload.get("paper_id", "")).strip(),
            run_id=str(payload.get("run_id", "")).strip(),
            source_hash=str(payload.get("source_hash", "")),
            selected_fulltext_ref=str(payload.get("selected_fulltext_ref", "")).strip(),
            summary_ref=str(payload.get("summary_ref", "")).strip(),
            paper_card_ref=str(payload.get("paper_card_ref", "")).strip(),
            lanes=lanes,
            duplicate_scan=payload.get("duplicate_scan"),
            collision_summary=payload.get("collision_summary"),
            quality_warnings=list(payload.get("quality_warnings", [])),
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "source_hash": self.source_hash,
            "selected_fulltext_ref": self.selected_fulltext_ref,
            "summary_ref": self.summary_ref,
            "paper_card_ref": self.paper_card_ref,
            "lanes": [lane.to_dict() for lane in self.lanes],
        }
        if self.duplicate_scan is not None:
            payload["duplicate_scan"] = dict(self.duplicate_scan)
        if self.collision_summary is not None:
            payload["collision_summary"] = dict(self.collision_summary)
        if self.quality_warnings:
            payload["quality_warnings"] = list(self.quality_warnings)
        return payload


@dataclass
class AcceptanceCheckRecord:
    name: str
    status: AcceptanceCheckStatus | str
    refs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise MillefeuilleContractError("acceptance check name must be non-empty")
        self.name = self.name.strip()
        self.status = _coerce_enum(AcceptanceCheckStatus, self.status)
        for field_name in ("refs", "notes"):
            value = getattr(self, field_name)
            if not isinstance(value, list):
                raise MillefeuilleContractError(f"{field_name} must be an array")
            for entry in value:
                if not isinstance(entry, str) or not entry.strip():
                    raise MillefeuilleContractError(
                        f"{field_name} must contain non-empty strings"
                    )
        if self.details is not None and not isinstance(self.details, dict):
            raise MillefeuilleContractError("details must be an object or null")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AcceptanceCheckRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("acceptance check must be an object")
        return cls(
            name=str(payload.get("name", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            refs=list(payload.get("refs", [])),
            notes=list(payload.get("notes", [])),
            details=payload.get("details"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "status": self.status.value,
            "refs": list(self.refs),
        }
        if self.notes:
            payload["notes"] = list(self.notes)
        if self.details is not None:
            payload["details"] = dict(self.details)
        return payload


@dataclass
class AcceptanceSummaryRecord:
    paper_id: str
    run_id: str
    source_hash: str
    source_pack_ref: str
    status: AcceptanceStatus | str
    counts: dict[str, int]
    checks: list[AcceptanceCheckRecord | dict[str, Any]]
    handoff: dict[str, Any] | None = None
    duplicate_scan: dict[str, Any] | None = None
    writeback_preview: dict[str, Any] | None = None
    review_reasons: list[str] = field(default_factory=list)
    schema_version: str = "openkb-millefeuille-acceptance-summary/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "openkb-millefeuille-acceptance-summary/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        for field_name in ("paper_id", "run_id", "source_hash", "source_pack_ref"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        self.status = _coerce_enum(AcceptanceStatus, self.status)
        if not isinstance(self.counts, dict):
            raise MillefeuilleContractError("counts must be an object")
        for key, value in self.counts.items():
            if not isinstance(key, str) or not key.strip():
                raise MillefeuilleContractError("counts keys must be non-empty")
            if not isinstance(value, int) or value < 0:
                raise MillefeuilleContractError("counts values must be non-negative")
        self.checks = [
            check
            if isinstance(check, AcceptanceCheckRecord)
            else AcceptanceCheckRecord.from_dict(check)
            for check in self.checks
        ]
        if not self.checks:
            raise MillefeuilleContractError("checks must not be empty")
        for field_name in ("review_reasons",):
            value = getattr(self, field_name)
            if not isinstance(value, list):
                raise MillefeuilleContractError(f"{field_name} must be an array")
            for entry in value:
                if not isinstance(entry, str) or not entry.strip():
                    raise MillefeuilleContractError(
                        f"{field_name} must contain non-empty strings"
                    )
        for field_name in ("handoff", "duplicate_scan", "writeback_preview"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, dict):
                raise MillefeuilleContractError(
                    f"{field_name} must be an object or null"
                )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AcceptanceSummaryRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("acceptance summary must be an object")
        checks = payload.get("checks")
        if not isinstance(checks, list):
            raise MillefeuilleContractError("checks must be an array")
        return cls(
            paper_id=str(payload.get("paper_id", "")).strip(),
            run_id=str(payload.get("run_id", "")).strip(),
            source_hash=str(payload.get("source_hash", "")).strip(),
            source_pack_ref=str(payload.get("source_pack_ref", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            counts=dict(payload.get("counts", {})),
            checks=checks,
            handoff=payload.get("handoff"),
            duplicate_scan=payload.get("duplicate_scan"),
            writeback_preview=payload.get("writeback_preview"),
            review_reasons=list(payload.get("review_reasons", [])),
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "source_hash": self.source_hash,
            "source_pack_ref": self.source_pack_ref,
            "status": self.status.value,
            "counts": dict(self.counts),
            "checks": [check.to_dict() for check in self.checks],
        }
        if self.handoff is not None:
            payload["handoff"] = dict(self.handoff)
        if self.duplicate_scan is not None:
            payload["duplicate_scan"] = dict(self.duplicate_scan)
        if self.writeback_preview is not None:
            payload["writeback_preview"] = dict(self.writeback_preview)
        if self.review_reasons:
            payload["review_reasons"] = list(self.review_reasons)
        return payload


@dataclass
class AcceptanceBatchRunRecord:
    paper_id: str
    run_id: str
    source_hash: str
    status: AcceptanceStatus | str
    summary_ref: str
    review_reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for field_name in (
            "paper_id",
            "run_id",
            "source_hash",
            "summary_ref",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        self.status = _coerce_enum(AcceptanceStatus, self.status)
        if not isinstance(self.review_reasons, list):
            raise MillefeuilleContractError("review_reasons must be an array")
        for reason in self.review_reasons:
            if not isinstance(reason, str) or not reason.strip():
                raise MillefeuilleContractError(
                    "review_reasons must contain non-empty strings"
                )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AcceptanceBatchRunRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("acceptance batch run must be an object")
        return cls(
            paper_id=str(payload.get("paper_id", "")).strip(),
            run_id=str(payload.get("run_id", "")).strip(),
            source_hash=str(payload.get("source_hash", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            summary_ref=str(payload.get("summary_ref", "")).strip(),
            review_reasons=list(payload.get("review_reasons", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "source_hash": self.source_hash,
            "status": self.status.value,
            "summary_ref": self.summary_ref,
        }
        if self.review_reasons:
            payload["review_reasons"] = list(self.review_reasons)
        return payload


@dataclass
class AcceptanceBatchSummaryRecord:
    batch_id: str
    status: AcceptanceStatus | str
    counts: dict[str, int]
    runs: list[AcceptanceBatchRunRecord | dict[str, Any]]
    schema_version: str = "millefeuille-acceptance-batch-summary/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-acceptance-batch-summary/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        if not isinstance(self.batch_id, str) or not self.batch_id.strip():
            raise MillefeuilleContractError("batch_id must be a non-empty string")
        self.status = _coerce_enum(AcceptanceStatus, self.status)
        if not isinstance(self.counts, dict):
            raise MillefeuilleContractError("counts must be an object")
        for key, value in self.counts.items():
            if not isinstance(key, str) or not key.strip():
                raise MillefeuilleContractError("counts keys must be non-empty")
            if not isinstance(value, int) or value < 0:
                raise MillefeuilleContractError("counts values must be non-negative")
        if not isinstance(self.runs, list):
            raise MillefeuilleContractError("runs must be an array")
        self.runs = [
            run
            if isinstance(run, AcceptanceBatchRunRecord)
            else AcceptanceBatchRunRecord.from_dict(run)
            for run in self.runs
        ]
        if not self.runs:
            raise MillefeuilleContractError("runs must not be empty")
        identities = [(run.paper_id, run.run_id) for run in self.runs]
        if len(identities) != len(set(identities)):
            raise MillefeuilleContractError(
                "acceptance batch runs must have unique paper_id/run_id pairs"
            )
        expected_counts = {
            "runs": len(self.runs),
            "passed": sum(run.status == AcceptanceStatus.PASS for run in self.runs),
            "needs_review": sum(
                run.status == AcceptanceStatus.NEEDS_REVIEW for run in self.runs
            ),
        }
        if self.counts != expected_counts:
            raise MillefeuilleContractError(
                f"acceptance batch counts drift: expected {expected_counts!r}"
            )
        expected_status = (
            AcceptanceStatus.PASS
            if expected_counts["needs_review"] == 0
            else AcceptanceStatus.NEEDS_REVIEW
        )
        if self.status != expected_status:
            raise MillefeuilleContractError(
                "acceptance batch status must reflect its run results"
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AcceptanceBatchSummaryRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(
                "acceptance batch summary must be an object"
            )
        runs = payload.get("runs")
        if not isinstance(runs, list):
            raise MillefeuilleContractError("runs must be an array")
        return cls(
            batch_id=str(payload.get("batch_id", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            counts=dict(payload.get("counts", {})),
            runs=runs,
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "status": self.status.value,
            "counts": dict(self.counts),
            "runs": [run.to_dict() for run in self.runs],
        }


@dataclass
class RejectedAlternativeRecord:
    path: str
    reason: str
    evidence_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for field_name in ("path", "reason"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        if not isinstance(self.evidence_refs, list):
            raise MillefeuilleContractError("evidence_refs must be an array")
        for ref in self.evidence_refs:
            if not isinstance(ref, str) or not ref.strip():
                raise MillefeuilleContractError(
                    "evidence_refs must contain non-empty strings"
                )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RejectedAlternativeRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("rejected alternative must be an object")
        return cls(
            path=str(payload.get("path", "")).strip(),
            reason=str(payload.get("reason", "")).strip(),
            evidence_refs=list(payload.get("evidence_refs", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "reason": self.reason,
        }
        if self.evidence_refs:
            payload["evidence_refs"] = list(self.evidence_refs)
        return payload


@dataclass
class ClassificationDecisionRecord:
    paper_id: str
    run_id: str
    source_hash: str
    taxonomy_version: str
    mode: ClassificationMode | str
    status: ClassificationStatus | str
    primary_path: str
    confidence: str
    evidence_refs: list[str]
    strongest_rejected_path: str | None = None
    rejected_alternatives: list[RejectedAlternativeRecord | dict[str, Any]] = field(
        default_factory=list
    )
    review_reasons: list[str] = field(default_factory=list)
    qa_flags: list[str] = field(default_factory=list)
    writeback_preview_ref: str | None = None
    schema_version: str = "millefeuille-classification-decision/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-classification-decision/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        for field_name in (
            "paper_id",
            "run_id",
            "source_hash",
            "taxonomy_version",
            "primary_path",
            "confidence",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        self.mode = _coerce_enum(ClassificationMode, self.mode)
        self.status = _coerce_enum(ClassificationStatus, self.status)
        for field_name in ("evidence_refs", "review_reasons", "qa_flags"):
            value = getattr(self, field_name)
            if not isinstance(value, list):
                raise MillefeuilleContractError(f"{field_name} must be an array")
            for entry in value:
                if not isinstance(entry, str) or not entry.strip():
                    raise MillefeuilleContractError(
                        f"{field_name} must contain non-empty strings"
                    )
        self.rejected_alternatives = [
            alt
            if isinstance(alt, RejectedAlternativeRecord)
            else RejectedAlternativeRecord.from_dict(alt)
            for alt in self.rejected_alternatives
        ]
        if self.writeback_preview_ref is not None and (
            not isinstance(self.writeback_preview_ref, str)
            or not self.writeback_preview_ref.strip()
        ):
            raise MillefeuilleContractError(
                "writeback_preview_ref must be a non-empty string when provided"
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ClassificationDecisionRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("classification decision must be an object")
        return cls(
            paper_id=str(payload.get("paper_id", "")).strip(),
            run_id=str(payload.get("run_id", "")).strip(),
            source_hash=str(payload.get("source_hash", "")).strip(),
            taxonomy_version=str(payload.get("taxonomy_version", "")).strip(),
            mode=str(payload.get("mode", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            primary_path=str(payload.get("primary_path", "")).strip(),
            confidence=str(payload.get("confidence", "")).strip(),
            evidence_refs=list(payload.get("evidence_refs", [])),
            strongest_rejected_path=payload.get("strongest_rejected_path"),
            rejected_alternatives=list(payload.get("rejected_alternatives", [])),
            review_reasons=list(payload.get("review_reasons", [])),
            qa_flags=list(payload.get("qa_flags", [])),
            writeback_preview_ref=payload.get("writeback_preview_ref"),
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "source_hash": self.source_hash,
            "taxonomy_version": self.taxonomy_version,
            "mode": self.mode.value,
            "status": self.status.value,
            "primary_path": self.primary_path,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
        }
        if self.strongest_rejected_path is not None:
            payload["strongest_rejected_path"] = self.strongest_rejected_path
        if self.rejected_alternatives:
            payload["rejected_alternatives"] = [
                alt.to_dict() for alt in self.rejected_alternatives
            ]
        if self.review_reasons:
            payload["review_reasons"] = list(self.review_reasons)
        if self.qa_flags:
            payload["qa_flags"] = list(self.qa_flags)
        if self.writeback_preview_ref is not None:
            payload["writeback_preview_ref"] = self.writeback_preview_ref
        return payload


@dataclass
class ClassificationActionRecord:
    action_id: str
    paper_id: str
    run_id: str
    source_hash: str
    taxonomy_version: str
    mode: ClassificationMode | str
    outcome: ClassificationActionOutcome | str
    status: ClassificationStatus | str
    summary: str
    prior_decision_ref: str
    final_decision_ref: str
    writeback_preview_ref: str
    evidence_refs: list[str]
    taxonomy_change_request_ref: str | None = None
    schema_version: str = "millefeuille-classification-action/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-classification-action/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        for field_name in (
            "action_id",
            "paper_id",
            "run_id",
            "source_hash",
            "taxonomy_version",
            "summary",
            "prior_decision_ref",
            "final_decision_ref",
            "writeback_preview_ref",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        self.mode = _coerce_enum(ClassificationMode, self.mode)
        if self.mode not in {
            ClassificationMode.REVIEW,
            ClassificationMode.ADJUDICATE,
        }:
            raise MillefeuilleContractError(
                "classification action mode must be review or adjudicate"
            )
        self.outcome = _coerce_enum(ClassificationActionOutcome, self.outcome)
        allowed_outcomes = {
            ClassificationMode.REVIEW: {
                ClassificationActionOutcome.NO_CHANGE,
                ClassificationActionOutcome.CORRECTED,
                ClassificationActionOutcome.ESCALATED,
            },
            ClassificationMode.ADJUDICATE: {
                ClassificationActionOutcome.CONFIRMED,
                ClassificationActionOutcome.CORRECTED,
                ClassificationActionOutcome.TAXONOMY_CHANGE_REQUESTED,
            },
        }
        if self.outcome not in allowed_outcomes[self.mode]:
            raise MillefeuilleContractError(
                f"outcome {self.outcome.value!r} is invalid for mode "
                f"{self.mode.value!r}"
            )
        self.status = _coerce_enum(ClassificationStatus, self.status)
        unresolved = self.outcome in {
            ClassificationActionOutcome.ESCALATED,
            ClassificationActionOutcome.TAXONOMY_CHANGE_REQUESTED,
        }
        expected_status = (
            ClassificationStatus.ADJUDICATION_REQUIRED
            if unresolved
            else ClassificationStatus.CLASSIFIED
        )
        if self.status != expected_status:
            raise MillefeuilleContractError(
                "classification action status must reflect its outcome"
            )
        if not isinstance(self.evidence_refs, list) or not self.evidence_refs:
            raise MillefeuilleContractError(
                "classification action evidence_refs must be a non-empty array"
            )
        for ref in self.evidence_refs:
            if not isinstance(ref, str) or not ref.strip():
                raise MillefeuilleContractError(
                    "classification action evidence_refs must contain "
                    "non-empty strings"
                )
        if self.taxonomy_change_request_ref is not None and (
            not isinstance(self.taxonomy_change_request_ref, str)
            or not self.taxonomy_change_request_ref.strip()
        ):
            raise MillefeuilleContractError(
                "taxonomy_change_request_ref must be a non-empty string when "
                "provided"
            )
        requires_taxonomy_request = (
            self.outcome == ClassificationActionOutcome.TAXONOMY_CHANGE_REQUESTED
        )
        if requires_taxonomy_request != (
            self.taxonomy_change_request_ref is not None
        ):
            raise MillefeuilleContractError(
                "taxonomy-change-requested actions require exactly one "
                "taxonomy_change_request_ref"
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ClassificationActionRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(
                "classification action must be an object"
            )
        return cls(
            action_id=str(payload.get("action_id", "")).strip(),
            paper_id=str(payload.get("paper_id", "")).strip(),
            run_id=str(payload.get("run_id", "")).strip(),
            source_hash=str(payload.get("source_hash", "")).strip(),
            taxonomy_version=str(payload.get("taxonomy_version", "")).strip(),
            mode=str(payload.get("mode", "")).strip(),
            outcome=str(payload.get("outcome", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            prior_decision_ref=str(payload.get("prior_decision_ref", "")).strip(),
            final_decision_ref=str(payload.get("final_decision_ref", "")).strip(),
            writeback_preview_ref=str(
                payload.get("writeback_preview_ref", "")
            ).strip(),
            evidence_refs=list(payload.get("evidence_refs", [])),
            taxonomy_change_request_ref=payload.get(
                "taxonomy_change_request_ref"
            ),
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "source_hash": self.source_hash,
            "taxonomy_version": self.taxonomy_version,
            "mode": self.mode.value,
            "outcome": self.outcome.value,
            "status": self.status.value,
            "summary": self.summary,
            "prior_decision_ref": self.prior_decision_ref,
            "final_decision_ref": self.final_decision_ref,
            "writeback_preview_ref": self.writeback_preview_ref,
            "evidence_refs": list(self.evidence_refs),
        }
        if self.taxonomy_change_request_ref is not None:
            payload["taxonomy_change_request_ref"] = (
                self.taxonomy_change_request_ref
            )
        return payload


@dataclass
class ClassificationBatchRunRecord:
    paper_id: str
    run_id: str
    source_hash: str
    taxonomy_version: str
    status: ClassificationStatus | str
    primary_path: str
    decision_ref: str
    writeback_preview_ref: str
    review_reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for field_name in (
            "paper_id",
            "run_id",
            "source_hash",
            "taxonomy_version",
            "primary_path",
            "decision_ref",
            "writeback_preview_ref",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        self.status = _coerce_enum(ClassificationStatus, self.status)
        if not isinstance(self.review_reasons, list):
            raise MillefeuilleContractError("review_reasons must be an array")
        for reason in self.review_reasons:
            if not isinstance(reason, str) or not reason.strip():
                raise MillefeuilleContractError(
                    "review_reasons must contain non-empty strings"
                )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ClassificationBatchRunRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(
                "classification batch run must be an object"
            )
        return cls(
            paper_id=str(payload.get("paper_id", "")).strip(),
            run_id=str(payload.get("run_id", "")).strip(),
            source_hash=str(payload.get("source_hash", "")).strip(),
            taxonomy_version=str(payload.get("taxonomy_version", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            primary_path=str(payload.get("primary_path", "")).strip(),
            decision_ref=str(payload.get("decision_ref", "")).strip(),
            writeback_preview_ref=str(
                payload.get("writeback_preview_ref", "")
            ).strip(),
            review_reasons=list(payload.get("review_reasons", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "source_hash": self.source_hash,
            "taxonomy_version": self.taxonomy_version,
            "status": self.status.value,
            "primary_path": self.primary_path,
            "decision_ref": self.decision_ref,
            "writeback_preview_ref": self.writeback_preview_ref,
        }
        if self.review_reasons:
            payload["review_reasons"] = list(self.review_reasons)
        return payload


@dataclass
class ClassificationBatchSummaryRecord:
    batch_id: str
    taxonomy_version: str
    status: ClassificationStatus | str
    counts: dict[str, int]
    routes: list[dict[str, Any]]
    runs: list[ClassificationBatchRunRecord | dict[str, Any]]
    schema_version: str = "millefeuille-classification-batch-summary/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-classification-batch-summary/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        for field_name in ("batch_id", "taxonomy_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        self.status = _coerce_enum(ClassificationStatus, self.status)
        if not isinstance(self.counts, dict):
            raise MillefeuilleContractError("counts must be an object")
        for key, value in self.counts.items():
            if not isinstance(key, str) or not key.strip():
                raise MillefeuilleContractError("counts keys must be non-empty")
            if not isinstance(value, int) or value < 0:
                raise MillefeuilleContractError("counts values must be non-negative")
        if not isinstance(self.runs, list):
            raise MillefeuilleContractError("runs must be an array")
        self.runs = [
            run
            if isinstance(run, ClassificationBatchRunRecord)
            else ClassificationBatchRunRecord.from_dict(run)
            for run in self.runs
        ]
        if not self.runs:
            raise MillefeuilleContractError("runs must not be empty")
        identities = [(run.paper_id, run.run_id) for run in self.runs]
        if len(identities) != len(set(identities)):
            raise MillefeuilleContractError(
                "classification batch runs must have unique paper_id/run_id pairs"
            )
        if any(run.taxonomy_version != self.taxonomy_version for run in self.runs):
            raise MillefeuilleContractError(
                "classification batch runs must use the locked taxonomy_version"
            )
        expected_counts = {
            "runs": len(self.runs),
            "classified": sum(
                run.status == ClassificationStatus.CLASSIFIED for run in self.runs
            ),
            "needs_review": sum(
                run.status == ClassificationStatus.NEEDS_REVIEW for run in self.runs
            ),
            "adjudication_required": sum(
                run.status == ClassificationStatus.ADJUDICATION_REQUIRED
                for run in self.runs
            ),
        }
        if self.counts != expected_counts:
            raise MillefeuilleContractError(
                f"classification batch counts drift: expected {expected_counts!r}"
            )
        expected_status = (
            ClassificationStatus.ADJUDICATION_REQUIRED
            if expected_counts["adjudication_required"]
            else ClassificationStatus.NEEDS_REVIEW
            if expected_counts["needs_review"]
            else ClassificationStatus.CLASSIFIED
        )
        if self.status != expected_status:
            raise MillefeuilleContractError(
                "classification batch status must reflect its run results"
            )
        self._validate_routes()

    def _validate_routes(self) -> None:
        if not isinstance(self.routes, list) or not self.routes:
            raise MillefeuilleContractError("routes must be a non-empty array")
        expected = {
            (
                run.paper_id,
                run.run_id,
                run.decision_ref,
                run.primary_path,
                run.status.value,
            )
            for run in self.runs
        }
        observed: set[tuple[str, str, str, str, str]] = set()
        route_paths: set[str] = set()
        for route in self.routes:
            if not isinstance(route, dict):
                raise MillefeuilleContractError("routes entries must be objects")
            primary_path = route.get("primary_path")
            count = route.get("count")
            route_runs = route.get("runs")
            if not isinstance(primary_path, str) or not primary_path.strip():
                raise MillefeuilleContractError(
                    "routes.primary_path must be a non-empty string"
                )
            if primary_path in route_paths:
                raise MillefeuilleContractError(
                    "classification batch routes must group each path once"
                )
            route_paths.add(primary_path)
            if not isinstance(route_runs, list) or not route_runs:
                raise MillefeuilleContractError(
                    "routes.runs must be a non-empty array"
                )
            if count != len(route_runs):
                raise MillefeuilleContractError(
                    "routes.count must equal the number of routed runs"
                )
            for run in route_runs:
                if not isinstance(run, dict):
                    raise MillefeuilleContractError(
                        "routes.runs entries must be objects"
                    )
                identity = (
                    run.get("paper_id"),
                    run.get("run_id"),
                    run.get("decision_ref"),
                    primary_path,
                    run.get("status"),
                )
                if not all(
                    isinstance(value, str) and value.strip() for value in identity
                ):
                    raise MillefeuilleContractError(
                        "routed run fields must be non-empty strings"
                    )
                if identity in observed:
                    raise MillefeuilleContractError(
                        "classification batch routes must not duplicate runs"
                    )
                observed.add(identity)
        if observed != expected:
            raise MillefeuilleContractError(
                "classification batch routes must cover every run exactly once"
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ClassificationBatchSummaryRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(
                "classification batch summary must be an object"
            )
        return cls(
            batch_id=str(payload.get("batch_id", "")).strip(),
            taxonomy_version=str(payload.get("taxonomy_version", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            counts=dict(payload.get("counts", {})),
            routes=list(payload.get("routes", [])),
            runs=list(payload.get("runs", [])),
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "taxonomy_version": self.taxonomy_version,
            "status": self.status.value,
            "counts": dict(self.counts),
            "routes": [dict(route) for route in self.routes],
            "runs": [run.to_dict() for run in self.runs],
        }


@dataclass
class ClassificationPlanRecord:
    run_id: str
    taxonomy_version: str
    mode: ClassificationMode | str
    papers: list[dict[str, Any]]
    default_profile: str | None = None
    schema_version: str = "millefeuille-classification-plan/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-classification-plan/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        for field_name in ("run_id", "taxonomy_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        self.mode = _coerce_enum(ClassificationMode, self.mode)
        if not isinstance(self.papers, list) or not self.papers:
            raise MillefeuilleContractError("papers must be a non-empty array")
        for paper in self.papers:
            if not isinstance(paper, dict):
                raise MillefeuilleContractError("papers entries must be objects")
            paper_id = paper.get("paper_id")
            decision_ref = paper.get("decision_ref")
            if not isinstance(paper_id, str) or not paper_id.strip():
                raise MillefeuilleContractError(
                    "papers.paper_id must be a non-empty string"
                )
            if not isinstance(decision_ref, str) or not decision_ref.strip():
                raise MillefeuilleContractError(
                    "papers.decision_ref must be a non-empty string"
                )
        if self.default_profile is not None and (
            not isinstance(self.default_profile, str)
            or not self.default_profile.strip()
        ):
            raise MillefeuilleContractError(
                "default_profile must be a non-empty string when provided"
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ClassificationPlanRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("classification plan must be an object")
        return cls(
            run_id=str(payload.get("run_id", "")).strip(),
            taxonomy_version=str(payload.get("taxonomy_version", "")).strip(),
            mode=str(payload.get("mode", "")).strip(),
            papers=list(payload.get("papers", [])),
            default_profile=payload.get("default_profile"),
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "taxonomy_version": self.taxonomy_version,
            "mode": self.mode.value,
            "papers": [dict(paper) for paper in self.papers],
        }
        if self.default_profile is not None:
            payload["default_profile"] = self.default_profile
        return payload


@dataclass
class ZoteroWritebackPlanRecord:
    paper_id: str
    run_id: str
    source_hash: str
    mode: str
    status: str
    add_tags: list[str] = field(default_factory=list)
    remove_tags: list[str] = field(default_factory=list)
    note_markdown_ref: str | None = None
    destination_collection: str | None = None
    classification_ref: str | None = None
    execution_notes: list[str] = field(default_factory=list)
    schema_version: str = "millefeuille-zotero-writeback-plan/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-zotero-writeback-plan/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        for field_name in ("paper_id", "run_id", "source_hash", "mode", "status"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string"
                )
        if self.mode not in {"none", "preview", "approved-live"}:
            raise MillefeuilleContractError("unsupported writeback mode")
        if self.status not in {"not-planned", "previewed", "written", "skipped"}:
            raise MillefeuilleContractError("unsupported writeback status")
        for field_name in ("add_tags", "remove_tags", "execution_notes"):
            value = getattr(self, field_name)
            if not isinstance(value, list):
                raise MillefeuilleContractError(f"{field_name} must be an array")
            for entry in value:
                if not isinstance(entry, str) or not entry.strip():
                    raise MillefeuilleContractError(
                        f"{field_name} must contain non-empty strings"
                    )
        for field_name in (
            "note_markdown_ref",
            "destination_collection",
            "classification_ref",
        ):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise MillefeuilleContractError(
                    f"{field_name} must be a non-empty string when provided"
                )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ZoteroWritebackPlanRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("writeback plan must be an object")
        return cls(
            paper_id=str(payload.get("paper_id", "")).strip(),
            run_id=str(payload.get("run_id", "")).strip(),
            source_hash=str(payload.get("source_hash", "")).strip(),
            mode=str(payload.get("mode", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            add_tags=list(payload.get("add_tags", [])),
            remove_tags=list(payload.get("remove_tags", [])),
            note_markdown_ref=payload.get("note_markdown_ref"),
            destination_collection=payload.get("destination_collection"),
            classification_ref=payload.get("classification_ref"),
            execution_notes=list(payload.get("execution_notes", [])),
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "source_hash": self.source_hash,
            "mode": self.mode,
            "status": self.status,
            "add_tags": list(self.add_tags),
            "remove_tags": list(self.remove_tags),
        }
        if self.note_markdown_ref is not None:
            payload["note_markdown_ref"] = self.note_markdown_ref
        if self.destination_collection is not None:
            payload["destination_collection"] = self.destination_collection
        if self.classification_ref is not None:
            payload["classification_ref"] = self.classification_ref
        if self.execution_notes:
            payload["execution_notes"] = list(self.execution_notes)
        return payload


@dataclass
class ReleaseCandidatePreflightRecord:
    current_version: str
    completed_stages: list[str]
    manual_gates_remaining: list[str]
    readiness: str
    validations: list[str] = field(default_factory=list)
    candidate_version: str | None = None
    schema_version: str = "millefeuille-release-candidate-preflight/v0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "millefeuille-release-candidate-preflight/v0.1":
            raise MillefeuilleContractError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        if (
            not isinstance(self.current_version, str)
            or not self.current_version.strip()
        ):
            raise MillefeuilleContractError(
                "current_version must be a non-empty string"
            )
        if self.candidate_version is not None and (
            not isinstance(self.candidate_version, str)
            or not self.candidate_version.strip()
        ):
            raise MillefeuilleContractError(
                "candidate_version must be a non-empty string when provided"
            )
        for field_name in ("completed_stages", "manual_gates_remaining", "validations"):
            value = getattr(self, field_name)
            if not isinstance(value, list):
                raise MillefeuilleContractError(f"{field_name} must be an array")
            for entry in value:
                if not isinstance(entry, str) or not entry.strip():
                    raise MillefeuilleContractError(
                        f"{field_name} must contain non-empty strings"
                    )
        if not isinstance(self.readiness, str) or not self.readiness.strip():
            raise MillefeuilleContractError("readiness must be a non-empty string")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ReleaseCandidatePreflightRecord:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(
                "release candidate preflight must be an object"
            )
        return cls(
            current_version=str(payload.get("current_version", "")).strip(),
            candidate_version=payload.get("candidate_version"),
            completed_stages=list(payload.get("completed_stages", [])),
            manual_gates_remaining=list(payload.get("manual_gates_remaining", [])),
            readiness=str(payload.get("readiness", "")).strip(),
            validations=list(payload.get("validations", [])),
            schema_version=str(payload.get("schema_version", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "current_version": self.current_version,
            "completed_stages": list(self.completed_stages),
            "manual_gates_remaining": list(self.manual_gates_remaining),
            "readiness": self.readiness,
            "validations": list(self.validations),
        }
        if self.candidate_version is not None:
            payload["candidate_version"] = self.candidate_version
        return payload
