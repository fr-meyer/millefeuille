"""Fixture-first extraction helpers for existing source packs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.millefeuille import (
    AttachmentEvidenceIdentity,
    MillefeuilleContractError,
    NativeExtractionEvidenceRecord,
    OCREvidenceRecord,
    ProviderPayloadDisposition,
)
from millefeuille.domain.source_packs import (
    load_source_pack_manifest,
    paper_id_for_zotero_item_key,
)

NATIVE_EXTRACTION_EVIDENCE_SCHEMA_VERSION = (
    "millefeuille-native-extraction-evidence/v0.1"
)
OCR_EXTRACTION_EVIDENCE_SCHEMA_VERSION = (
    "millefeuille-ocr-extraction-evidence/v0.1"
)
NATIVE_MARKDOWN_REF = Path("extractions/native/fulltext.md")
NATIVE_EVIDENCE_REF = Path("extractions/native/evidence.json")
OCR_MARKDOWN_REF = Path("extractions/mistral-ocr/fulltext.md")
OCR_EVIDENCE_REF = Path("extractions/mistral-ocr/evidence.json")


@dataclass(frozen=True)
class NativeExtractionFixtureEvidence:
    item_key: str
    attachment_key: str
    canonical_filename: str
    markdown_path: Path
    expected_sha256: str
    page_count: int
    source_type: str = "zotero"
    schema_version: str = NATIVE_EXTRACTION_EVIDENCE_SCHEMA_VERSION
    tool: str = "native-fixture"
    status: str = "ok"
    character_count: int | None = None
    empty_pages: int | None = None
    warnings: list[str] = field(default_factory=list)
    zotero_version: int | None = None
    paper_id: str | None = None

    def __post_init__(self) -> None:
        if self.source_type != "zotero":
            raise MillefeuilleContractError("source_type must be 'zotero'")
        if self.page_count < 0:
            raise MillefeuilleContractError("page_count must be non-negative")
        if self.character_count is not None and self.character_count < 0:
            raise MillefeuilleContractError(
                "character_count must be non-negative"
            )
        if self.empty_pages is not None and self.empty_pages < 0:
            raise MillefeuilleContractError("empty_pages must be non-negative")
        object.__setattr__(
            self,
            "markdown_path",
            Path(self.markdown_path),
        )
        object.__setattr__(
            self,
            "expected_sha256",
            _normalize_sha256(self.expected_sha256),
        )

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: str | Path | None = None,
    ) -> NativeExtractionFixtureEvidence:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(
                "native extraction evidence must be an object"
            )
        schema_version = _required_string(
            payload.get("schema_version", NATIVE_EXTRACTION_EVIDENCE_SCHEMA_VERSION),
            "schema_version",
        )
        if schema_version != NATIVE_EXTRACTION_EVIDENCE_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                "unsupported native extraction evidence schema_version "
                f"{schema_version!r}"
            )
        markdown_path = Path(
            _required_string(
                payload.get("markdown_path") or payload.get("fulltext_path"),
                "markdown_path",
            )
        )
        if not markdown_path.is_absolute() and base_dir is not None:
            markdown_path = Path(base_dir) / markdown_path
        return cls(
            item_key=_required_string(payload.get("item_key"), "item_key"),
            attachment_key=_required_string(
                payload.get("attachment_key"),
                "attachment_key",
            ),
            canonical_filename=_required_string(
                payload.get("canonical_filename"),
                "canonical_filename",
            ),
            markdown_path=markdown_path,
            expected_sha256=_required_string(
                payload.get("expected_sha256") or payload.get("sha256"),
                "expected_sha256",
            ),
            page_count=_required_int(payload.get("page_count"), "page_count"),
            source_type=_required_string(
                payload.get("source_type", "zotero"),
                "source_type",
            ),
            schema_version=schema_version,
            tool=_required_string(payload.get("tool", "native-fixture"), "tool"),
            status=_required_string(payload.get("status", "ok"), "status"),
            character_count=_optional_int(
                payload.get("character_count"),
                "character_count",
            ),
            empty_pages=_optional_int(payload.get("empty_pages"), "empty_pages"),
            warnings=_optional_string_list(payload.get("warnings"), "warnings"),
            zotero_version=_optional_int(
                payload.get("zotero_version"),
                "zotero_version",
            ),
            paper_id=_optional_string(payload.get("paper_id"), "paper_id"),
        )


@dataclass(frozen=True)
class OCRExtractionFixtureEvidence:
    item_key: str
    attachment_key: str
    canonical_filename: str
    markdown_path: Path
    expected_sha256: str
    page_count: int
    provider_version: str
    source_type: str = "zotero"
    schema_version: str = OCR_EXTRACTION_EVIDENCE_SCHEMA_VERSION
    provider: str = "mistral-ocr"
    requested_model: str | None = None
    status: str = "ok"
    character_count: int | None = None
    confidence_scores_granularity: str | None = None
    table_format: str | None = None
    include_blocks: bool | None = None
    image_policy: str | None = None
    provider_payload_disposition: ProviderPayloadDisposition | str = (
        ProviderPayloadDisposition.DISCARDED
    )
    provider_upload_deleted: bool | None = None
    raw_provider_json_retained_private: bool | None = None
    word_confidence_scores: int | None = None
    low_word_confidence_under_0_80: int | None = None
    low_word_confidence_under_0_95: int | None = None
    tables: int | None = None
    image_regions_metadata_only: int | None = None
    warnings: list[str] = field(default_factory=list)
    zotero_version: int | None = None
    paper_id: str | None = None

    def __post_init__(self) -> None:
        if self.source_type != "zotero":
            raise MillefeuilleContractError("source_type must be 'zotero'")
        if self.page_count < 0:
            raise MillefeuilleContractError("page_count must be non-negative")
        if self.character_count is not None and self.character_count < 0:
            raise MillefeuilleContractError(
                "character_count must be non-negative"
            )
        object.__setattr__(
            self,
            "markdown_path",
            Path(self.markdown_path),
        )
        object.__setattr__(
            self,
            "expected_sha256",
            _normalize_sha256(self.expected_sha256),
        )
        try:
            disposition = ProviderPayloadDisposition(
                self.provider_payload_disposition
            )
        except ValueError as exc:
            raise MillefeuilleContractError(
                "provider_payload_disposition must be a supported value"
            ) from exc
        object.__setattr__(
            self,
            "provider_payload_disposition",
            disposition,
        )

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: str | Path | None = None,
    ) -> OCRExtractionFixtureEvidence:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("OCR extraction evidence must be an object")
        schema_version = _required_string(
            payload.get("schema_version", OCR_EXTRACTION_EVIDENCE_SCHEMA_VERSION),
            "schema_version",
        )
        if schema_version != OCR_EXTRACTION_EVIDENCE_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                "unsupported OCR extraction evidence schema_version "
                f"{schema_version!r}"
            )
        markdown_path = Path(
            _required_string(
                payload.get("markdown_path") or payload.get("fulltext_path"),
                "markdown_path",
            )
        )
        if not markdown_path.is_absolute() and base_dir is not None:
            markdown_path = Path(base_dir) / markdown_path
        return cls(
            item_key=_required_string(payload.get("item_key"), "item_key"),
            attachment_key=_required_string(
                payload.get("attachment_key"),
                "attachment_key",
            ),
            canonical_filename=_required_string(
                payload.get("canonical_filename"),
                "canonical_filename",
            ),
            markdown_path=markdown_path,
            expected_sha256=_required_string(
                payload.get("expected_sha256") or payload.get("sha256"),
                "expected_sha256",
            ),
            page_count=_required_int(payload.get("page_count"), "page_count"),
            provider_version=_required_string(
                payload.get("provider_version") or payload.get("provider_model"),
                "provider_version",
            ),
            source_type=_required_string(
                payload.get("source_type", "zotero"),
                "source_type",
            ),
            schema_version=schema_version,
            provider=_required_string(
                payload.get("provider", "mistral-ocr"),
                "provider",
            ),
            requested_model=_optional_string(
                payload.get("requested_model"),
                "requested_model",
            ),
            status=_required_string(payload.get("status", "ok"), "status"),
            character_count=_optional_int(
                payload.get("character_count"),
                "character_count",
            ),
            confidence_scores_granularity=_optional_string(
                payload.get("confidence_scores_granularity"),
                "confidence_scores_granularity",
            ),
            table_format=_optional_string(
                payload.get("table_format"),
                "table_format",
            ),
            include_blocks=_optional_bool(
                payload.get("include_blocks"),
                "include_blocks",
            ),
            image_policy=_optional_string(
                payload.get("image_policy"),
                "image_policy",
            ),
            provider_payload_disposition=_required_string(
                payload.get(
                    "provider_payload_disposition",
                    ProviderPayloadDisposition.DISCARDED.value,
                ),
                "provider_payload_disposition",
            ),
            provider_upload_deleted=_optional_bool(
                payload.get("provider_upload_deleted"),
                "provider_upload_deleted",
            ),
            raw_provider_json_retained_private=_optional_bool(
                payload.get("raw_provider_json_retained_private"),
                "raw_provider_json_retained_private",
            ),
            word_confidence_scores=_optional_int(
                payload.get("word_confidence_scores"),
                "word_confidence_scores",
            ),
            low_word_confidence_under_0_80=_optional_int(
                payload.get("low_word_confidence_under_0_80"),
                "low_word_confidence_under_0_80",
            ),
            low_word_confidence_under_0_95=_optional_int(
                payload.get("low_word_confidence_under_0_95"),
                "low_word_confidence_under_0_95",
            ),
            tables=_optional_int(payload.get("tables"), "tables"),
            image_regions_metadata_only=_optional_int(
                payload.get("image_regions_metadata_only"),
                "image_regions_metadata_only",
            ),
            warnings=_optional_string_list(payload.get("warnings"), "warnings"),
            zotero_version=_optional_int(
                payload.get("zotero_version"),
                "zotero_version",
            ),
            paper_id=_optional_string(payload.get("paper_id"), "paper_id"),
        )


@dataclass(frozen=True)
class ExtractionFixtureWriteResult:
    stage: str
    paper_id: str
    status: str
    source_pack_dir: Path
    evidence_path: Path
    markdown_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "paper_id": self.paper_id,
            "status": self.status,
            "source_pack_dir": str(self.source_pack_dir),
            "evidence_path": str(self.evidence_path),
            "markdown_path": str(self.markdown_path),
        }


@dataclass(frozen=True)
class _PlannedExtractionWrite:
    stage: str
    paper_id: str
    status: str
    source_pack_dir: Path
    evidence_output_path: Path
    markdown_output_path: Path
    expected_payload: dict[str, Any]
    expected_markdown: str


def load_native_extraction_evidence_batch(
    path: str | Path,
) -> list[NativeExtractionFixtureEvidence]:
    return _load_fixture_batch(
        path,
        kind="native extraction evidence",
        loader=NativeExtractionFixtureEvidence.from_dict,
    )


def load_ocr_extraction_evidence_batch(
    path: str | Path,
) -> list[OCRExtractionFixtureEvidence]:
    return _load_fixture_batch(
        path,
        kind="OCR extraction evidence",
        loader=OCRExtractionFixtureEvidence.from_dict,
    )


def write_native_extractions_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
) -> list[ExtractionFixtureWriteResult]:
    root = Path(source_pack_root)
    records = load_native_extraction_evidence_batch(evidence_path)
    _reject_duplicate_records(records, kind="native extraction")
    planned = [_plan_native_extraction(record, root) for record in records]
    return [_apply_planned_write(plan) for plan in planned]


def write_ocr_extractions_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
) -> list[ExtractionFixtureWriteResult]:
    root = Path(source_pack_root)
    records = load_ocr_extraction_evidence_batch(evidence_path)
    _reject_duplicate_records(records, kind="OCR extraction")
    planned = [_plan_ocr_extraction(record, root) for record in records]
    return [_apply_planned_write(plan) for plan in planned]


def load_native_extraction_sidecar(path: str | Path) -> dict[str, Any]:
    payload = _load_json_object(path, "native extraction evidence")
    schema_version = _required_string(payload.get("schema_version"), "schema_version")
    if schema_version != NATIVE_EXTRACTION_EVIDENCE_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            f"unsupported native extraction evidence schema_version {schema_version!r}"
        )
    _normalize_source_hash(_required_string(payload.get("source_hash"), "source_hash"))
    return payload


def load_ocr_extraction_sidecar(path: str | Path) -> dict[str, Any]:
    payload = _load_json_object(path, "OCR extraction evidence")
    schema_version = _required_string(payload.get("schema_version"), "schema_version")
    if schema_version != OCR_EXTRACTION_EVIDENCE_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            f"unsupported OCR extraction evidence schema_version {schema_version!r}"
        )
    _normalize_source_hash(_required_string(payload.get("source_hash"), "source_hash"))
    return payload


def _plan_native_extraction(
    evidence: NativeExtractionFixtureEvidence,
    source_pack_root: Path,
) -> _PlannedExtractionWrite:
    source_pack_dir, source_hash = _resolve_source_pack_dir(
        source_pack_root=source_pack_root,
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        expected_sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
        paper_id=evidence.paper_id,
    )
    markdown_text = _read_markdown_text(
        evidence.markdown_path,
        kind="native extraction markdown",
    )
    character_count = _character_count(
        text=markdown_text,
        expected=evidence.character_count,
    )
    identity = _attachment_identity(
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        expected_sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
    )
    record = NativeExtractionEvidenceRecord(
        tool=evidence.tool,
        source_pack=f"source-packs/zotero/{source_pack_dir.name}",
        attachment_identity=identity,
        output_markdown_ref=NATIVE_MARKDOWN_REF.as_posix(),
        page_count=evidence.page_count,
        warnings=evidence.warnings,
    )
    payload = record.to_dict()
    payload.update(
        {
            "schema_version": NATIVE_EXTRACTION_EVIDENCE_SCHEMA_VERSION,
            "source_type": evidence.source_type,
            "source_hash": source_hash,
            "status": evidence.status,
            "character_count": character_count,
        }
    )
    if evidence.empty_pages is not None:
        payload["empty_pages"] = evidence.empty_pages
    evidence_output_path = source_pack_dir / NATIVE_EVIDENCE_REF
    markdown_output_path = source_pack_dir / NATIVE_MARKDOWN_REF
    status = _existing_extraction_status(
        evidence_output_path=evidence_output_path,
        markdown_output_path=markdown_output_path,
        expected_payload=payload,
        expected_markdown=markdown_text,
        stage="native extraction",
    )
    return _PlannedExtractionWrite(
        stage="extract-native",
        paper_id=source_pack_dir.name,
        status=status or "created",
        source_pack_dir=source_pack_dir,
        evidence_output_path=evidence_output_path,
        markdown_output_path=markdown_output_path,
        expected_payload=payload,
        expected_markdown=markdown_text,
    )


def _plan_ocr_extraction(
    evidence: OCRExtractionFixtureEvidence,
    source_pack_root: Path,
) -> _PlannedExtractionWrite:
    source_pack_dir, source_hash = _resolve_source_pack_dir(
        source_pack_root=source_pack_root,
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        expected_sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
        paper_id=evidence.paper_id,
    )
    markdown_text = _read_markdown_text(
        evidence.markdown_path,
        kind="OCR extraction markdown",
    )
    character_count = _character_count(
        text=markdown_text,
        expected=evidence.character_count,
    )
    identity = _attachment_identity(
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        expected_sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
    )
    record = OCREvidenceRecord(
        provider=evidence.provider,
        provider_version=evidence.provider_version,
        source_pack=f"source-packs/zotero/{source_pack_dir.name}",
        attachment_identity=identity,
        output_markdown_ref=OCR_MARKDOWN_REF.as_posix(),
        page_count=evidence.page_count,
        provider_payload_disposition=evidence.provider_payload_disposition,
        requested_model=evidence.requested_model,
        warnings=evidence.warnings,
    )
    payload = record.to_dict()
    payload.update(
        {
            "schema_version": OCR_EXTRACTION_EVIDENCE_SCHEMA_VERSION,
            "source_type": evidence.source_type,
            "source_hash": source_hash,
            "status": evidence.status,
            "character_count": character_count,
        }
    )
    for field_name in (
        "confidence_scores_granularity",
        "table_format",
        "include_blocks",
        "image_policy",
        "provider_upload_deleted",
        "raw_provider_json_retained_private",
        "word_confidence_scores",
        "low_word_confidence_under_0_80",
        "low_word_confidence_under_0_95",
        "tables",
        "image_regions_metadata_only",
    ):
        value = getattr(evidence, field_name)
        if value is not None:
            payload[field_name] = value
    evidence_output_path = source_pack_dir / OCR_EVIDENCE_REF
    markdown_output_path = source_pack_dir / OCR_MARKDOWN_REF
    status = _existing_extraction_status(
        evidence_output_path=evidence_output_path,
        markdown_output_path=markdown_output_path,
        expected_payload=payload,
        expected_markdown=markdown_text,
        stage="OCR extraction",
    )
    return _PlannedExtractionWrite(
        stage="extract-ocr",
        paper_id=source_pack_dir.name,
        status=status or "created",
        source_pack_dir=source_pack_dir,
        evidence_output_path=evidence_output_path,
        markdown_output_path=markdown_output_path,
        expected_payload=payload,
        expected_markdown=markdown_text,
    )


def _resolve_source_pack_dir(
    *,
    source_pack_root: Path,
    item_key: str,
    attachment_key: str,
    canonical_filename: str,
    expected_sha256: str,
    zotero_version: int | None,
    paper_id: str | None,
) -> tuple[Path, str]:
    resolved_paper_id = paper_id or paper_id_for_zotero_item_key(item_key)
    source_pack_dir = source_pack_root / "zotero" / resolved_paper_id
    manifest_path = source_pack_dir / "manifest.json"
    manifest = load_source_pack_manifest(manifest_path)
    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        raise MillefeuilleContractError(
            f"source-pack manifest identity must be an object: {manifest_path}"
        )
    if identity.get("zotero_item_key") != item_key:
        raise MillefeuilleContractError(
            f"source-pack item_key drift for {resolved_paper_id}"
        )
    if identity.get("zotero_attachment_key") != attachment_key:
        raise MillefeuilleContractError(
            f"source-pack attachment_key drift for {resolved_paper_id}"
        )
    if identity.get("canonical_filename") != canonical_filename:
        raise MillefeuilleContractError(
            f"source-pack canonical_filename drift for {resolved_paper_id}"
        )
    if zotero_version is not None and identity.get("zotero_version") not in (
        None,
        zotero_version,
    ):
        raise MillefeuilleContractError(
            f"source-pack zotero_version drift for {resolved_paper_id}"
        )
    source_hash = _normalize_source_hash(
        _required_string(manifest.get("source_hash"), "source_hash")
    )
    if source_hash != f"sha256:{expected_sha256}":
        raise MillefeuilleContractError(
            f"source-pack source hash drift for {resolved_paper_id}"
        )
    return source_pack_dir, source_hash


def _existing_extraction_status(
    *,
    evidence_output_path: Path,
    markdown_output_path: Path,
    expected_payload: dict[str, Any],
    expected_markdown: str,
    stage: str,
) -> str | None:
    evidence_exists = evidence_output_path.exists()
    markdown_exists = markdown_output_path.exists()
    if not evidence_exists and not markdown_exists:
        return None
    if not evidence_output_path.is_file() or not markdown_output_path.is_file():
        raise MillefeuilleContractError(
            f"existing {stage} fixture is incomplete: {evidence_output_path.parent}"
        )
    existing_payload = _load_json_object(evidence_output_path, stage)
    if existing_payload != expected_payload:
        raise MillefeuilleContractError(
            f"existing {stage} evidence drift: {evidence_output_path}"
        )
    try:
        existing_markdown = markdown_output_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read existing {stage} markdown {markdown_output_path}: {exc}"
        ) from exc
    if existing_markdown != expected_markdown:
        raise MillefeuilleContractError(
            f"existing {stage} markdown drift: {markdown_output_path}"
        )
    return "existing"


def _apply_planned_write(
    planned: _PlannedExtractionWrite,
) -> ExtractionFixtureWriteResult:
    if planned.status == "created":
        planned.markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
        planned.evidence_output_path.parent.mkdir(parents=True, exist_ok=True)
        planned.markdown_output_path.write_text(
            planned.expected_markdown,
            encoding="utf-8",
        )
        planned.evidence_output_path.write_text(
            json.dumps(planned.expected_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return ExtractionFixtureWriteResult(
        stage=planned.stage,
        paper_id=planned.paper_id,
        status=planned.status,
        source_pack_dir=planned.source_pack_dir,
        evidence_path=planned.evidence_output_path,
        markdown_path=planned.markdown_output_path,
    )


def _load_fixture_batch(
    path: str | Path,
    *,
    kind: str,
    loader: Any,
) -> list[Any]:
    evidence_path = Path(path)
    try:
        text = evidence_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read {kind} {evidence_path}: {exc}"
        ) from exc

    if evidence_path.suffix == ".jsonl":
        records: list[Any] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MillefeuilleContractError(
                    f"{kind} JSONL is not valid JSON at {evidence_path}:{line_number}"
                ) from exc
            records.append(loader(payload, base_dir=evidence_path.parent))
        return records

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"{kind} is not valid JSON: {evidence_path}"
        ) from exc
    if isinstance(payload, list):
        raw_records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("evidence"), list):
        raw_records = payload["evidence"]
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        raw_records = payload["records"]
    else:
        raw_records = [payload]
    return [loader(record, base_dir=evidence_path.parent) for record in raw_records]


def _load_json_object(path: str | Path, kind: str) -> dict[str, Any]:
    object_path = Path(path)
    try:
        payload = json.loads(object_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read {kind} {object_path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"{kind} is not valid JSON: {object_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError(f"{kind} must be an object")
    return payload


def _attachment_identity(
    *,
    item_key: str,
    attachment_key: str,
    canonical_filename: str,
    expected_sha256: str,
    zotero_version: int | None,
) -> AttachmentEvidenceIdentity:
    return AttachmentEvidenceIdentity(
        item_key=item_key,
        attachment_key=attachment_key,
        canonical_filename=canonical_filename,
        sha256=expected_sha256,
        zotero_version=zotero_version,
    )


def _read_markdown_text(path: Path, *, kind: str) -> str:
    if not path.is_file():
        raise MillefeuilleContractError(f"{kind} file not found: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read {kind} {path}: {exc}"
        ) from exc


def _character_count(*, text: str, expected: int | None) -> int:
    actual = len(text)
    if expected is not None and expected != actual:
        raise MillefeuilleContractError(
            f"character_count mismatch: expected {expected}, got {actual}"
        )
    return actual


def _reject_duplicate_records(records: list[Any], *, kind: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for record in records:
        key = f"{record.item_key}:{record.attachment_key}"
        if key in seen:
            duplicates.append(key)
            continue
        seen.add(key)
    if duplicates:
        raise MillefeuilleContractError(
            f"duplicate {kind} evidence for handoff row(s): "
            + ", ".join(sorted(set(duplicates)))
        )


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MillefeuilleContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MillefeuilleContractError(f"{field_name} must be a string")
    stripped = value.strip()
    return stripped or None


def _required_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MillefeuilleContractError(f"{field_name} must be an integer")
    return value


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _required_int(value, field_name)


def _optional_bool(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise MillefeuilleContractError(f"{field_name} must be a boolean")
    return value


def _optional_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MillefeuilleContractError(f"{field_name} must be a list")
    result: list[str] = []
    for entry in value:
        result.append(_required_string(entry, field_name))
    return result


def _normalize_sha256(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("sha256:"):
        stripped = stripped.split(":", 1)[1]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", stripped):
        raise MillefeuilleContractError("expected_sha256 must be a SHA-256 hex digest")
    return stripped.lower()


def _normalize_source_hash(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("sha256:"):
        raise MillefeuilleContractError("source_hash must use the sha256:<hex> form")
    _normalize_sha256(stripped)
    return stripped.lower()
