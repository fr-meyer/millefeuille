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
                raise MillefeuilleContractError(
                    f"{field_name} must be non-negative"
                )
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
            raise MillefeuilleContractError(
                "hierarchical summary must be an object"
            )
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
                None
                if self.taxonomy_context is None
                else dict(self.taxonomy_context)
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
            raise MillefeuilleContractError(
                "evidence_refs must be a non-empty array"
            )
        for ref in self.evidence_refs:
            if not isinstance(ref, str) or not ref.strip():
                raise MillefeuilleContractError(
                    "evidence_refs must contain non-empty strings"
                )
        if not isinstance(self.index_status, list):
            raise MillefeuilleContractError("index_status must be an array")
        for entry in self.index_status:
            if not isinstance(entry, dict):
                raise MillefeuilleContractError(
                    "index_status entries must be objects"
                )
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
            primary_contribution=str(
                payload.get("primary_contribution", "")
            ).strip(),
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
