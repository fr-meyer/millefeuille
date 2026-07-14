"""Fixture-first structure helpers for existing source packs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.millefeuille import (
    AttachmentEvidenceIdentity,
    MillefeuilleContractError,
    RouteSelection,
    StructureEvidenceRecord,
)
from millefeuille.domain.route_fixtures import (
    ROUTE_EVIDENCE_REF,
    ROUTE_MARKDOWN_REF,
    load_route_selection_sidecar,
)
from millefeuille.domain.source_packs import (
    load_source_pack_manifest,
    paper_id_for_zotero_item_key,
)

STRUCTURE_EVIDENCE_SCHEMA_VERSION = "millefeuille-structure-evidence/v0.1"
STRUCTURE_EVIDENCE_REF = Path("structure/structure.json")
STRUCTURE_OUTLINE_REF = Path("structure/outline.md")


@dataclass(frozen=True)
class StructureFixtureEvidence:
    item_key: str
    attachment_key: str
    canonical_filename: str
    structure_path: Path
    expected_sha256: str
    page_count: int
    selected_route: RouteSelection | str
    source_type: str = "zotero"
    schema_version: str = STRUCTURE_EVIDENCE_SCHEMA_VERSION
    outline_path: Path | None = None
    status: str = "ok"
    locators: int | None = None
    sections: int = 0
    tables: int = 0
    figures: int = 0
    references: int = 0
    warnings: list[str] = field(default_factory=list)
    zotero_version: int | None = None
    paper_id: str | None = None

    def __post_init__(self) -> None:
        if self.source_type != "zotero":
            raise MillefeuilleContractError("source_type must be 'zotero'")
        if self.page_count < 0:
            raise MillefeuilleContractError("page_count must be non-negative")
        for field_name in ("sections", "tables", "figures", "references"):
            if getattr(self, field_name) < 0:
                raise MillefeuilleContractError(
                    f"{field_name} must be non-negative"
                )
        if self.locators is not None and self.locators < 0:
            raise MillefeuilleContractError("locators must be non-negative")
        object.__setattr__(self, "structure_path", Path(self.structure_path))
        if self.outline_path is not None:
            object.__setattr__(self, "outline_path", Path(self.outline_path))
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
    ) -> StructureFixtureEvidence:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("structure evidence must be an object")
        schema_version = _required_string(
            payload.get("schema_version", STRUCTURE_EVIDENCE_SCHEMA_VERSION),
            "schema_version",
        )
        if schema_version != STRUCTURE_EVIDENCE_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                "unsupported structure evidence schema_version "
                f"{schema_version!r}"
            )
        structure_path = Path(
            _required_string(
                payload.get("structure_path") or payload.get("json_path"),
                "structure_path",
            )
        )
        if not structure_path.is_absolute() and base_dir is not None:
            structure_path = Path(base_dir) / structure_path
        outline_path = _optional_path(
            payload.get("outline_path") or payload.get("outline_markdown_path"),
            base_dir=base_dir,
            field_name="outline_path",
        )
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
            structure_path=structure_path,
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
            outline_path=outline_path,
            status=_required_string(payload.get("status", "ok"), "status"),
            locators=_optional_int(payload.get("locators"), "locators"),
            sections=_required_int(payload.get("sections", 0), "sections"),
            tables=_required_int(payload.get("tables", 0), "tables"),
            figures=_required_int(payload.get("figures", 0), "figures"),
            references=_required_int(payload.get("references", 0), "references"),
            warnings=_optional_string_list(payload.get("warnings"), "warnings"),
            zotero_version=_optional_int(
                payload.get("zotero_version"),
                "zotero_version",
            ),
            paper_id=_optional_string(payload.get("paper_id"), "paper_id"),
        )


@dataclass(frozen=True)
class StructureFixtureWriteResult:
    paper_id: str
    status: str
    source_pack_dir: Path
    evidence_path: Path
    outline_path: Path | None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "paper_id": self.paper_id,
            "status": self.status,
            "source_pack_dir": str(self.source_pack_dir),
            "evidence_path": str(self.evidence_path),
        }
        if self.outline_path is not None:
            result["outline_path"] = str(self.outline_path)
        return result


@dataclass(frozen=True)
class _PlannedStructureWrite:
    paper_id: str
    status: str
    source_pack_dir: Path
    evidence_output_path: Path
    outline_output_path: Path | None
    expected_payload: dict[str, Any]
    expected_outline: str | None


def load_structure_evidence_batch(
    path: str | Path,
) -> list[StructureFixtureEvidence]:
    evidence_path = Path(path)
    try:
        text = evidence_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read structure evidence {evidence_path}: {exc}"
        ) from exc

    if evidence_path.suffix == ".jsonl":
        records: list[StructureFixtureEvidence] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MillefeuilleContractError(
                    "structure evidence JSONL is not valid JSON at "
                    f"{evidence_path}:{line_number}"
                ) from exc
            records.append(
                StructureFixtureEvidence.from_dict(
                    payload,
                    base_dir=evidence_path.parent,
                )
            )
        return records

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"structure evidence is not valid JSON: {evidence_path}"
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
        StructureFixtureEvidence.from_dict(
            record,
            base_dir=evidence_path.parent,
        )
        for record in raw_records
    ]


def write_structures_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
) -> list[StructureFixtureWriteResult]:
    root = Path(source_pack_root)
    records = load_structure_evidence_batch(evidence_path)
    _reject_duplicate_records(records)
    planned = [_plan_structure(record, root) for record in records]
    return [_apply_planned_structure_write(plan) for plan in planned]


def load_structure_sidecar(path: str | Path) -> dict[str, Any]:
    payload = _load_json_object(path, "structure evidence")
    schema_version = _required_string(payload.get("schema_version"), "schema_version")
    if schema_version != STRUCTURE_EVIDENCE_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            f"unsupported structure evidence schema_version {schema_version!r}"
        )
    _normalize_source_hash(_required_string(payload.get("source_hash"), "source_hash"))
    return payload


def _plan_structure(
    evidence: StructureFixtureEvidence,
    source_pack_root: Path,
) -> _PlannedStructureWrite:
    source_pack_dir, source_hash = _resolve_source_pack_dir(
        source_pack_root=source_pack_root,
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        expected_sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
        paper_id=evidence.paper_id,
    )
    route_payload = load_route_selection_sidecar(source_pack_dir / ROUTE_EVIDENCE_REF)
    _validate_route_dependency(
        route_payload=route_payload,
        selected_route=evidence.selected_route,
        page_count=evidence.page_count,
        source_hash=source_hash,
        source_pack_dir=source_pack_dir,
    )
    selected_fulltext_path = source_pack_dir / ROUTE_MARKDOWN_REF
    if not selected_fulltext_path.is_file():
        raise MillefeuilleContractError(
            f"structure evidence requires selected fulltext: {selected_fulltext_path}"
        )

    structure_payload = _load_json_object(evidence.structure_path, "structure fixture")
    outline_text = _read_optional_outline(evidence.outline_path)
    locators = evidence.locators
    if locators is None:
        locators = _infer_locator_count(structure_payload)

    identity = AttachmentEvidenceIdentity(
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
    )
    record = StructureEvidenceRecord(
        structure_backend="fixture-structure",
        selected_route=evidence.selected_route,
        source_pack=f"source-packs/zotero/{source_pack_dir.name}",
        attachment_identity=identity,
        source_markdown_ref=ROUTE_MARKDOWN_REF.as_posix(),
        page_count=evidence.page_count,
        section_count=evidence.sections,
        table_count=evidence.tables,
        figure_count=evidence.figures,
        reference_count=evidence.references,
        coverage={
            "locators": locators,
            "pages": evidence.page_count,
            "source": "fixture",
        },
        structure=structure_payload,
        outline_markdown_ref=(
            STRUCTURE_OUTLINE_REF.as_posix() if outline_text is not None else None
        ),
        warnings=evidence.warnings,
    )
    payload = record.to_dict()
    payload.update(
        {
            "schema_version": STRUCTURE_EVIDENCE_SCHEMA_VERSION,
            "source_type": evidence.source_type,
            "source_hash": source_hash,
            "status": evidence.status,
            "route_evidence_ref": ROUTE_EVIDENCE_REF.as_posix(),
            "selected_fulltext_ref": ROUTE_MARKDOWN_REF.as_posix(),
            "output_structure_ref": STRUCTURE_EVIDENCE_REF.as_posix(),
        }
    )
    evidence_output_path = source_pack_dir / STRUCTURE_EVIDENCE_REF
    outline_output_path = (
        source_pack_dir / STRUCTURE_OUTLINE_REF if outline_text is not None else None
    )
    status = _existing_structure_status(
        evidence_output_path=evidence_output_path,
        outline_output_path=outline_output_path,
        expected_payload=payload,
        expected_outline=outline_text,
    )
    return _PlannedStructureWrite(
        paper_id=source_pack_dir.name,
        status=status or "created",
        source_pack_dir=source_pack_dir,
        evidence_output_path=evidence_output_path,
        outline_output_path=outline_output_path,
        expected_payload=payload,
        expected_outline=outline_text,
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


def _validate_route_dependency(
    *,
    route_payload: dict[str, Any],
    selected_route: RouteSelection,
    page_count: int,
    source_hash: str,
    source_pack_dir: Path,
) -> None:
    if route_payload["source_hash"] != source_hash:
        raise MillefeuilleContractError(
            f"route selection source_hash drift for {source_pack_dir}"
        )
    if route_payload.get("selected_route") != selected_route.value:
        raise MillefeuilleContractError(
            f"structure selected_route drift for {source_pack_dir}"
        )
    if route_payload.get("page_count") != page_count:
        raise MillefeuilleContractError(
            f"structure page_count drift for {source_pack_dir}"
        )


def _existing_structure_status(
    *,
    evidence_output_path: Path,
    outline_output_path: Path | None,
    expected_payload: dict[str, Any],
    expected_outline: str | None,
) -> str | None:
    evidence_exists = evidence_output_path.exists()
    default_outline_path = evidence_output_path.parent / STRUCTURE_OUTLINE_REF.name
    outline_exists = default_outline_path.exists()
    if not evidence_exists and not outline_exists:
        return None
    if not evidence_output_path.is_file():
        raise MillefeuilleContractError(
            f"existing structure fixture is incomplete: {evidence_output_path.parent}"
        )
    if expected_outline is not None and (
        outline_output_path is None or not outline_output_path.is_file()
    ):
        raise MillefeuilleContractError(
            f"existing structure fixture is incomplete: {evidence_output_path.parent}"
        )
    if expected_outline is None and outline_exists:
        raise MillefeuilleContractError(
            f"existing structure outline drift: {default_outline_path}"
        )
    existing_payload = load_structure_sidecar(evidence_output_path)
    if existing_payload != expected_payload:
        raise MillefeuilleContractError(
            f"existing structure evidence drift: {evidence_output_path}"
        )
    if expected_outline is not None:
        assert outline_output_path is not None
        try:
            existing_outline = outline_output_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not read existing structure outline {outline_output_path}: "
                f"{exc}"
            ) from exc
        if existing_outline != expected_outline:
            raise MillefeuilleContractError(
                f"existing structure outline drift: {outline_output_path}"
            )
    return "existing"


def _apply_planned_structure_write(
    planned: _PlannedStructureWrite,
) -> StructureFixtureWriteResult:
    if planned.status == "created":
        planned.evidence_output_path.parent.mkdir(parents=True, exist_ok=True)
        planned.evidence_output_path.write_text(
            json.dumps(planned.expected_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if (
            planned.outline_output_path is not None
            and planned.expected_outline is not None
        ):
            planned.outline_output_path.write_text(
                planned.expected_outline,
                encoding="utf-8",
            )
    return StructureFixtureWriteResult(
        paper_id=planned.paper_id,
        status=planned.status,
        source_pack_dir=planned.source_pack_dir,
        evidence_path=planned.evidence_output_path,
        outline_path=planned.outline_output_path,
    )


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


def _read_optional_outline(path: Path | None) -> str | None:
    if path is None:
        return None
    if not path.is_file():
        raise MillefeuilleContractError(
            f"structure outline markdown file not found: {path}"
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read structure outline markdown {path}: {exc}"
        ) from exc


def _infer_locator_count(payload: dict[str, Any]) -> int:
    for field_name in ("locators", "locator_count"):
        value = payload.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    count = 0
    for field_name in ("pages", "sections", "tables", "figures", "references"):
        value = payload.get(field_name)
        if isinstance(value, list):
            count += len(value)
    return count


def _reject_duplicate_records(records: list[StructureFixtureEvidence]) -> None:
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
            "duplicate structure evidence for handoff row(s): "
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


def _optional_path(
    value: Any,
    *,
    base_dir: str | Path | None,
    field_name: str,
) -> Path | None:
    raw_value = _optional_string(value, field_name)
    if raw_value is None:
        return None
    path = Path(raw_value)
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    return path


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
