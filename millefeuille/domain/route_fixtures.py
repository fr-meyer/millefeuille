"""Fixture-first route-selection helpers for existing source packs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.extraction_fixtures import (
    NATIVE_EVIDENCE_REF,
    OCR_EVIDENCE_REF,
    load_native_extraction_sidecar,
    load_ocr_extraction_sidecar,
)
from millefeuille.domain.millefeuille import (
    AttachmentEvidenceIdentity,
    MillefeuilleContractError,
    RouteEvidenceRecord,
    RouteSelection,
)
from millefeuille.domain.source_packs import (
    load_source_pack_manifest,
    paper_id_for_zotero_item_key,
)

ROUTE_SELECTION_EVIDENCE_SCHEMA_VERSION = (
    "millefeuille-route-selection-evidence/v0.1"
)
ROUTE_MARKDOWN_REF = Path("selected/fulltext.md")
ROUTE_EVIDENCE_REF = Path("selected/route.json")


@dataclass(frozen=True)
class RouteSelectionFixtureEvidence:
    item_key: str
    attachment_key: str
    canonical_filename: str
    markdown_path: Path
    expected_sha256: str
    page_count: int
    selected_route: RouteSelection | str
    source_type: str = "zotero"
    schema_version: str = ROUTE_SELECTION_EVIDENCE_SCHEMA_VERSION
    reason: str | None = None
    character_count: int | None = None
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
        object.__setattr__(self, "markdown_path", Path(self.markdown_path))
        object.__setattr__(
            self,
            "expected_sha256",
            _normalize_sha256(self.expected_sha256),
        )
        object.__setattr__(
            self,
            "selected_route",
            RouteSelection(self.selected_route),
        )

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: str | Path | None = None,
    ) -> RouteSelectionFixtureEvidence:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(
                "route selection evidence must be an object"
            )
        schema_version = _required_string(
            payload.get("schema_version", ROUTE_SELECTION_EVIDENCE_SCHEMA_VERSION),
            "schema_version",
        )
        if schema_version != ROUTE_SELECTION_EVIDENCE_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                "unsupported route selection evidence schema_version "
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
            selected_route=_required_string(
                payload.get("selected_route"),
                "selected_route",
            ),
            source_type=_required_string(
                payload.get("source_type", "zotero"),
                "source_type",
            ),
            schema_version=schema_version,
            reason=_optional_string(payload.get("reason"), "reason"),
            character_count=_optional_int(
                payload.get("character_count"),
                "character_count",
            ),
            warnings=_optional_string_list(payload.get("warnings"), "warnings"),
            zotero_version=_optional_int(
                payload.get("zotero_version"),
                "zotero_version",
            ),
            paper_id=_optional_string(payload.get("paper_id"), "paper_id"),
        )


@dataclass(frozen=True)
class RouteFixtureWriteResult:
    paper_id: str
    status: str
    source_pack_dir: Path
    evidence_path: Path
    markdown_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "status": self.status,
            "source_pack_dir": str(self.source_pack_dir),
            "evidence_path": str(self.evidence_path),
            "markdown_path": str(self.markdown_path),
        }


@dataclass(frozen=True)
class _PlannedRouteWrite:
    paper_id: str
    status: str
    source_pack_dir: Path
    evidence_output_path: Path
    markdown_output_path: Path
    expected_payload: dict[str, Any]
    expected_markdown: str


def load_route_selection_evidence_batch(
    path: str | Path,
) -> list[RouteSelectionFixtureEvidence]:
    evidence_path = Path(path)
    try:
        text = evidence_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read route selection evidence {evidence_path}: {exc}"
        ) from exc

    if evidence_path.suffix == ".jsonl":
        records: list[RouteSelectionFixtureEvidence] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MillefeuilleContractError(
                    "route selection evidence JSONL is not valid JSON at "
                    f"{evidence_path}:{line_number}"
                ) from exc
            records.append(
                RouteSelectionFixtureEvidence.from_dict(
                    payload,
                    base_dir=evidence_path.parent,
                )
            )
        return records

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"route selection evidence is not valid JSON: {evidence_path}"
        ) from exc
    if isinstance(payload, list):
        raw_records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("evidence"), list):
        raw_records = payload["evidence"]
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        raw_records = payload["records"]
    else:
        raw_records = [payload]
    return [
        RouteSelectionFixtureEvidence.from_dict(
            record,
            base_dir=evidence_path.parent,
        )
        for record in raw_records
    ]


def write_route_selections_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
) -> list[RouteFixtureWriteResult]:
    root = Path(source_pack_root)
    records = load_route_selection_evidence_batch(evidence_path)
    _reject_duplicate_records(records)
    planned = [_plan_route_selection(record, root) for record in records]
    return [_apply_planned_route_write(plan) for plan in planned]


def load_route_selection_sidecar(path: str | Path) -> dict[str, Any]:
    sidecar_path = Path(path)
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read route selection evidence {sidecar_path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"route selection evidence is not valid JSON: {sidecar_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError("route selection evidence must be an object")
    schema_version = _required_string(payload.get("schema_version"), "schema_version")
    if schema_version != ROUTE_SELECTION_EVIDENCE_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            f"unsupported route selection evidence schema_version {schema_version!r}"
        )
    _normalize_source_hash(_required_string(payload.get("source_hash"), "source_hash"))
    return payload


def _plan_route_selection(
    evidence: RouteSelectionFixtureEvidence,
    source_pack_root: Path,
) -> _PlannedRouteWrite:
    source_pack_dir, source_hash = _resolve_source_pack_dir(
        source_pack_root=source_pack_root,
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        expected_sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
        paper_id=evidence.paper_id,
    )
    native_payload = _maybe_load_sidecar(source_pack_dir / NATIVE_EVIDENCE_REF)
    ocr_payload = _maybe_load_sidecar(source_pack_dir / OCR_EVIDENCE_REF, ocr=True)
    extraction_routes = _validate_route_dependencies(
        selected_route=evidence.selected_route,
        native_payload=native_payload,
        ocr_payload=ocr_payload,
        source_pack_dir=source_pack_dir,
    )
    markdown_text = _read_markdown_text(evidence.markdown_path)
    character_count = _character_count(markdown_text, evidence.character_count)
    identity = AttachmentEvidenceIdentity(
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
    )
    record = RouteEvidenceRecord(
        selected_route=evidence.selected_route,
        source_pack=f"source-packs/zotero/{source_pack_dir.name}",
        attachment_identity=identity,
        output_markdown_ref=ROUTE_MARKDOWN_REF.as_posix(),
        page_count=evidence.page_count,
        extraction_routes=extraction_routes,
        dual_extraction_complete=(
            native_payload is not None and ocr_payload is not None
        ),
        reason=evidence.reason,
        warnings=evidence.warnings,
    )
    payload = record.to_dict()
    payload.update(
        {
            "schema_version": ROUTE_SELECTION_EVIDENCE_SCHEMA_VERSION,
            "source_type": evidence.source_type,
            "source_hash": source_hash,
            "character_count": character_count,
        }
    )
    evidence_output_path = source_pack_dir / ROUTE_EVIDENCE_REF
    markdown_output_path = source_pack_dir / ROUTE_MARKDOWN_REF
    status = _existing_route_status(
        evidence_output_path=evidence_output_path,
        markdown_output_path=markdown_output_path,
        expected_payload=payload,
        expected_markdown=markdown_text,
    )
    return _PlannedRouteWrite(
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


def _validate_route_dependencies(
    *,
    selected_route: RouteSelection,
    native_payload: dict[str, Any] | None,
    ocr_payload: dict[str, Any] | None,
    source_pack_dir: Path,
) -> list[str]:
    if selected_route == RouteSelection.NATIVE and native_payload is None:
        raise MillefeuilleContractError(
            f"route selection requires native extraction evidence for {source_pack_dir}"
        )
    if selected_route == RouteSelection.OCR and ocr_payload is None:
        raise MillefeuilleContractError(
            f"route selection requires OCR extraction evidence for {source_pack_dir}"
        )
    if (
        selected_route == RouteSelection.MERGED_DUAL
        and (native_payload is None or ocr_payload is None)
    ):
        raise MillefeuilleContractError(
            "route selection merged-dual requires both native and OCR "
            f"extraction evidence for {source_pack_dir}"
        )

    routes: list[str] = []
    if native_payload is not None:
        routes.append("native")
    if ocr_payload is not None:
        routes.append(_required_string(ocr_payload.get("provider"), "provider"))
    return routes


def _existing_route_status(
    *,
    evidence_output_path: Path,
    markdown_output_path: Path,
    expected_payload: dict[str, Any],
    expected_markdown: str,
) -> str | None:
    evidence_exists = evidence_output_path.exists()
    markdown_exists = markdown_output_path.exists()
    if not evidence_exists and not markdown_exists:
        return None
    if not evidence_output_path.is_file() or not markdown_output_path.is_file():
        raise MillefeuilleContractError(
            "existing route selection fixture is incomplete: "
            f"{evidence_output_path.parent}"
        )
    existing_payload = load_route_selection_sidecar(evidence_output_path)
    if existing_payload != expected_payload:
        raise MillefeuilleContractError(
            f"existing route selection evidence drift: {evidence_output_path}"
        )
    try:
        existing_markdown = markdown_output_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            "could not read existing route selection markdown "
            f"{markdown_output_path}: {exc}"
        ) from exc
    if existing_markdown != expected_markdown:
        raise MillefeuilleContractError(
            f"existing route selection markdown drift: {markdown_output_path}"
        )
    return "existing"


def _apply_planned_route_write(
    planned: _PlannedRouteWrite,
) -> RouteFixtureWriteResult:
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
    return RouteFixtureWriteResult(
        paper_id=planned.paper_id,
        status=planned.status,
        source_pack_dir=planned.source_pack_dir,
        evidence_path=planned.evidence_output_path,
        markdown_path=planned.markdown_output_path,
    )


def _maybe_load_sidecar(
    path: Path,
    *,
    ocr: bool = False,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    loader = load_ocr_extraction_sidecar if ocr else load_native_extraction_sidecar
    return loader(path)


def _read_markdown_text(path: Path) -> str:
    if not path.is_file():
        raise MillefeuilleContractError(
            f"route selection markdown file not found: {path}"
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read route selection markdown {path}: {exc}"
        ) from exc


def _character_count(text: str, expected: int | None) -> int:
    actual = len(text)
    if expected is not None and expected != actual:
        raise MillefeuilleContractError(
            f"character_count mismatch: expected {expected}, got {actual}"
        )
    return actual


def _reject_duplicate_records(
    records: list[RouteSelectionFixtureEvidence],
) -> None:
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
            "duplicate route selection evidence for handoff row(s): "
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
