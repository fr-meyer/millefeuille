"""Offline source-pack intake helpers.

These helpers create source packs only from explicit local recovered-PDF
evidence. They do not read Zotero, call OCR/model providers, write OpenKB or
index lanes, or mutate Zotero state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.models import OpenKBHandoffRow

SOURCE_PACK_ARTIFACT_ROOT = "source-pack"
DEFAULT_SOURCE_PACK_ROOT = "/srv/openkb/source-packs"
SOURCE_PACK_MANIFEST_SCHEMA_VERSION = "millefeuille-source-pack-manifest/v0.1"
RECOVERED_PDF_EVIDENCE_SCHEMA_VERSION = "millefeuille-recovered-pdf-evidence/v0.1"
SOURCE_PACK_SOURCE_REF = "source.pdf"


@dataclass(frozen=True)
class RecoveredPdfEvidence:
    """Verified local recovered-PDF evidence for fixture-first intake."""

    item_key: str
    attachment_key: str
    canonical_filename: str
    recovered_pdf_path: Path
    expected_sha256: str
    source_type: str = "zotero"
    schema_version: str = RECOVERED_PDF_EVIDENCE_SCHEMA_VERSION
    item_title: str | None = None
    citation_key: str | None = None
    discovered_at: str | None = None
    verification_strength: str | None = None
    recovery: dict[str, Any] | None = None
    openkb_policy_hints: dict[str, Any] | None = None
    content_type: str | None = "application/pdf"
    file_size_bytes: int | None = None
    zotero_version: int | None = None
    paper_id: str | None = None

    def __post_init__(self) -> None:
        if self.source_type != "zotero":
            raise MillefeuilleContractError("source_type must be 'zotero'")
        object.__setattr__(
            self,
            "expected_sha256",
            _normalize_sha256(self.expected_sha256),
        )
        object.__setattr__(
            self,
            "recovered_pdf_path",
            Path(self.recovered_pdf_path),
        )

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: str | Path | None = None,
    ) -> RecoveredPdfEvidence:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("recovered PDF evidence must be an object")

        schema_version = _optional_string(
            payload.get("schema_version"),
            "schema_version",
            default=RECOVERED_PDF_EVIDENCE_SCHEMA_VERSION,
        )
        if schema_version != RECOVERED_PDF_EVIDENCE_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                f"unsupported recovered PDF evidence schema_version {schema_version!r}"
            )

        source_type = _required_string(
            payload.get("source_type", "zotero"),
            "source_type",
        )
        if source_type != "zotero":
            raise MillefeuilleContractError("source_type must be 'zotero'")

        raw_path = _required_string(
            payload.get("recovered_pdf_path") or payload.get("source_path"),
            "recovered_pdf_path",
        )
        recovered_path = Path(raw_path)
        if not recovered_path.is_absolute() and base_dir is not None:
            recovered_path = Path(base_dir) / recovered_path

        expected_sha256 = _required_string(
            payload.get("expected_sha256") or payload.get("sha256"),
            "expected_sha256",
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
            recovered_pdf_path=recovered_path,
            expected_sha256=_normalize_sha256(expected_sha256),
            source_type=source_type,
            schema_version=schema_version,
            item_title=_optional_string(payload.get("item_title"), "item_title"),
            citation_key=_optional_string(payload.get("citation_key"), "citation_key"),
            discovered_at=_optional_string(
                payload.get("discovered_at"),
                "discovered_at",
            ),
            verification_strength=_optional_string(
                payload.get("verification_strength"),
                "verification_strength",
            ),
            recovery=_optional_mapping(payload.get("recovery"), "recovery"),
            openkb_policy_hints=_optional_mapping(
                payload.get("openkb_policy_hints"),
                "openkb_policy_hints",
            ),
            content_type=_optional_string(
                payload.get("content_type"),
                "content_type",
                default="application/pdf",
            ),
            file_size_bytes=_optional_int(
                payload.get("file_size_bytes"),
                "file_size_bytes",
            ),
            zotero_version=_optional_int(
                payload.get("zotero_version"),
                "zotero_version",
            ),
            paper_id=_optional_string(payload.get("paper_id"), "paper_id"),
        )


@dataclass(frozen=True)
class SourcePackIntakeResult:
    paper_id: str
    status: str
    source_pack_dir: Path
    manifest_path: Path
    source_path: Path
    source_hash: str
    manifest: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "status": self.status,
            "source_pack_dir": str(self.source_pack_dir),
            "manifest_path": str(self.manifest_path),
            "source_path": str(self.source_path),
            "source_hash": self.source_hash,
            "manifest": dict(self.manifest),
        }


def load_recovered_pdf_evidence(path: str | Path) -> RecoveredPdfEvidence:
    evidence_path = Path(path)
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read recovered PDF evidence {evidence_path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"recovered PDF evidence is not valid JSON: {evidence_path}"
        ) from exc
    return RecoveredPdfEvidence.from_dict(payload, base_dir=evidence_path.parent)


def load_recovered_pdf_evidence_batch(
    path: str | Path,
) -> list[RecoveredPdfEvidence]:
    """Load one or more recovered-PDF evidence records from JSON or JSONL."""
    evidence_path = Path(path)
    try:
        text = evidence_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read recovered PDF evidence {evidence_path}: {exc}"
        ) from exc

    if evidence_path.suffix == ".jsonl":
        records: list[RecoveredPdfEvidence] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MillefeuilleContractError(
                    "recovered PDF evidence JSONL is not valid JSON "
                    f"at {evidence_path}:{line_number}"
                ) from exc
            records.append(
                RecoveredPdfEvidence.from_dict(
                    payload,
                    base_dir=evidence_path.parent,
                )
            )
        return records

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"recovered PDF evidence is not valid JSON: {evidence_path}"
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
        RecoveredPdfEvidence.from_dict(record, base_dir=evidence_path.parent)
        for record in raw_records
    ]


def verify_recovered_pdf_evidence(evidence: RecoveredPdfEvidence) -> str:
    """Verify local recovered bytes against evidence and return source hash."""
    source_path = evidence.recovered_pdf_path
    if not source_path.is_file():
        raise MillefeuilleContractError(
            f"recovered PDF evidence file not found: {source_path}"
        )

    actual_sha256 = sha256_file(source_path)
    if actual_sha256 != evidence.expected_sha256:
        raise MillefeuilleContractError(
            "recovered PDF hash mismatch: "
            f"expected {evidence.expected_sha256}, got {actual_sha256}"
        )

    byte_size = source_path.stat().st_size
    if (
        evidence.file_size_bytes is not None
        and evidence.file_size_bytes != byte_size
    ):
        raise MillefeuilleContractError(
            "recovered PDF size mismatch: "
            f"expected {evidence.file_size_bytes}, got {byte_size}"
        )
    return f"sha256:{actual_sha256}"


def write_source_packs_from_handoff_evidence(
    *,
    handoff_rows: list[OpenKBHandoffRow],
    evidence_path: str | Path,
    source_pack_root: str | Path,
    created_at: str | None = None,
) -> list[SourcePackIntakeResult]:
    """Write source packs for selected handoff rows from local fixture evidence.

    This is the dry-run/staged-run bridge: it only consumes already-built
    handoff rows and explicit local recovered-PDF evidence. It preflights every
    selected PDF row before creating any source-pack files.
    """
    root = Path(source_pack_root)
    if not str(root).strip():
        raise MillefeuilleContractError("source_pack_root cannot be empty")

    rows = [row for row in handoff_rows if row.is_pdf]
    if not rows:
        return []
    _reject_multi_pdf_handoff_groups(rows)

    evidence_records = load_recovered_pdf_evidence_batch(evidence_path)
    evidence_by_key = _evidence_by_handoff_key(evidence_records)

    planned: list[RecoveredPdfEvidence] = []
    missing: list[str] = []
    for row in rows:
        key = _handoff_key(row.item_key, row.attachment_key)
        evidence = evidence_by_key.get(key)
        if evidence is None:
            missing.append(key)
            continue
        _validate_evidence_matches_handoff_row(evidence, row)
        verify_recovered_pdf_evidence(evidence)
        planned.append(evidence)

    if missing:
        raise MillefeuilleContractError(
            "source-pack intake missing recovered PDF evidence for "
            f"handoff rows: {', '.join(sorted(missing))}"
        )

    return [
        write_source_pack_from_recovered_pdf(
            evidence=evidence,
            source_pack_root=root,
            created_at=created_at,
        )
        for evidence in planned
    ]


def write_source_pack_from_recovered_pdf(
    *,
    evidence: RecoveredPdfEvidence,
    source_pack_root: str | Path,
    created_at: str | None = None,
) -> SourcePackIntakeResult:
    """Create a source pack from verified local recovered-PDF evidence."""
    root = Path(source_pack_root)
    if not str(root).strip():
        raise MillefeuilleContractError("source_pack_root cannot be empty")

    source_path = evidence.recovered_pdf_path
    source_hash = verify_recovered_pdf_evidence(evidence)
    byte_size = source_path.stat().st_size

    paper_id = evidence.paper_id or paper_id_for_zotero_item_key(evidence.item_key)
    source_pack_dir = root / "zotero" / paper_id
    manifest_path = source_pack_dir / "manifest.json"
    target_source_path = source_pack_dir / SOURCE_PACK_SOURCE_REF
    timestamp = created_at or datetime.now(UTC).isoformat()
    manifest = build_source_pack_manifest(
        evidence=evidence,
        paper_id=paper_id,
        source_hash=source_hash,
        byte_size=byte_size,
        created_at=timestamp,
    )

    existing_status = _existing_source_pack_status(
        source_pack_dir=source_pack_dir,
        manifest_path=manifest_path,
        target_source_path=target_source_path,
        expected_manifest=manifest,
        expected_source_hash=source_hash,
    )
    if existing_status is not None:
        existing_manifest = load_source_pack_manifest(manifest_path)
        return SourcePackIntakeResult(
            paper_id=paper_id,
            status=existing_status,
            source_pack_dir=source_pack_dir,
            manifest_path=manifest_path,
            source_path=target_source_path,
            source_hash=source_hash,
            manifest=existing_manifest,
        )

    _write_new_source_pack(
        source_pack_dir=source_pack_dir,
        manifest_path=manifest_path,
        target_source_path=target_source_path,
        source_path=source_path,
        manifest=manifest,
    )
    return SourcePackIntakeResult(
        paper_id=paper_id,
        status="created",
        source_pack_dir=source_pack_dir,
        manifest_path=manifest_path,
        source_path=target_source_path,
        source_hash=source_hash,
        manifest=manifest,
    )


def build_source_pack_manifest(
    *,
    evidence: RecoveredPdfEvidence,
    paper_id: str,
    source_hash: str,
    byte_size: int,
    created_at: str,
) -> dict[str, Any]:
    source_sha256 = source_hash.split(":", 1)[1]
    manifest: dict[str, Any] = {
        "schema_version": SOURCE_PACK_MANIFEST_SCHEMA_VERSION,
        "paper_id": paper_id,
        "source_type": evidence.source_type,
        "source_hash": source_hash,
        "created_at": created_at,
        "source": {
            "kind": "recovered-pdf",
            "ref": SOURCE_PACK_SOURCE_REF,
            "format": "pdf",
            "byte_size": byte_size,
            "sha256": source_sha256,
        },
        "identity": {
            "zotero_item_key": evidence.item_key,
            "zotero_attachment_key": evidence.attachment_key,
            "canonical_filename": evidence.canonical_filename,
        },
        "verification": {
            "status": "verified",
            "method": "sha256",
            "expected_sha256": evidence.expected_sha256,
            "actual_sha256": source_sha256,
        },
        "provenance": {
            "intake_method": "fixture-recovered-pdf",
            "evidence_schema_version": evidence.schema_version,
        },
    }
    if evidence.item_title is not None:
        manifest["identity"]["item_title"] = evidence.item_title
    if evidence.citation_key is not None:
        manifest["identity"]["citation_key"] = evidence.citation_key
    if evidence.content_type is not None:
        manifest["source"]["content_type"] = evidence.content_type
    if evidence.discovered_at is not None:
        manifest["provenance"]["discovered_at"] = evidence.discovered_at
    if evidence.verification_strength is not None:
        manifest["provenance"]["verification_strength"] = (
            evidence.verification_strength
        )
    if evidence.recovery:
        manifest["provenance"]["recovery"] = dict(evidence.recovery)
    if evidence.openkb_policy_hints:
        manifest["provenance"]["openkb_policy_hints"] = dict(
            evidence.openkb_policy_hints
        )
    if evidence.file_size_bytes is not None:
        manifest["identity"]["file_size_bytes"] = evidence.file_size_bytes
    if evidence.zotero_version is not None:
        manifest["identity"]["zotero_version"] = evidence.zotero_version
    return manifest


def load_source_pack_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read source-pack manifest {manifest_path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"source-pack manifest is not valid JSON: {manifest_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError("source-pack manifest must be an object")
    source_hash = _normalize_source_hash(
        _required_string(payload.get("source_hash"), "source_hash")
    )
    payload["source_hash"] = source_hash
    source = payload.get("source")
    if isinstance(source, dict) and source.get("sha256") is not None:
        source_sha256 = _normalize_sha256(
            _required_string(source.get("sha256"), "source.sha256")
        )
        if source_hash != f"sha256:{source_sha256}":
            raise MillefeuilleContractError(
                "source-pack manifest source_hash does not match source.sha256"
            )
    return payload


def load_source_pack_manifest_source_hash(path: str | Path) -> str:
    manifest = load_source_pack_manifest(path)
    return str(manifest["source_hash"])


def paper_id_for_zotero_item_key(item_key: str) -> str:
    raw = f"zotero-{item_key}"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    return slug or "zotero-item"


def _evidence_by_handoff_key(
    evidence_records: list[RecoveredPdfEvidence],
) -> dict[str, RecoveredPdfEvidence]:
    evidence_by_key: dict[str, RecoveredPdfEvidence] = {}
    for evidence in evidence_records:
        key = _handoff_key(evidence.item_key, evidence.attachment_key)
        if key in evidence_by_key:
            raise MillefeuilleContractError(
                f"duplicate recovered PDF evidence for handoff row {key}"
            )
        evidence_by_key[key] = evidence
    return evidence_by_key


def _reject_multi_pdf_handoff_groups(rows: list[OpenKBHandoffRow]) -> None:
    grouped_rows: dict[str, list[OpenKBHandoffRow]] = {}
    for row in rows:
        grouped_rows.setdefault(row.item_key, []).append(row)

    conflicts: list[str] = []
    for item_key, item_rows in grouped_rows.items():
        if len(item_rows) <= 1:
            continue
        paper_id = paper_id_for_zotero_item_key(item_key)
        attachment_keys = ", ".join(
            sorted(row.attachment_key for row in item_rows)
        )
        conflicts.append(
            f"{item_key} -> {paper_id} [{attachment_keys}]"
        )

    if conflicts:
        raise MillefeuilleContractError(
            "source-pack intake currently supports exactly one PDF handoff row "
            "per Zotero item/source pack; refusing multi-PDF groups: "
            + "; ".join(sorted(conflicts))
        )


def _handoff_key(item_key: str, attachment_key: str) -> str:
    return f"{item_key}:{attachment_key}"


def _validate_evidence_matches_handoff_row(
    evidence: RecoveredPdfEvidence,
    row: OpenKBHandoffRow,
) -> None:
    if evidence.source_type != row.source_type:
        raise MillefeuilleContractError(
            "source-pack evidence source_type does not match handoff row "
            f"for {_handoff_key(row.item_key, row.attachment_key)}"
        )
    if evidence.item_key != row.item_key:
        raise MillefeuilleContractError("source-pack evidence item_key drift")
    if evidence.attachment_key != row.attachment_key:
        raise MillefeuilleContractError(
            "source-pack evidence attachment_key drift"
        )
    if evidence.canonical_filename != row.canonical_filename:
        raise MillefeuilleContractError(
            "source-pack evidence canonical_filename does not match handoff "
            f"row for {_handoff_key(row.item_key, row.attachment_key)}"
        )
    if not row.sha256:
        raise MillefeuilleContractError(
            "source-pack intake requires handoff row sha256 for "
            f"{_handoff_key(row.item_key, row.attachment_key)}"
        )
    try:
        row_sha256 = _normalize_sha256(row.sha256)
    except MillefeuilleContractError as exc:
        raise MillefeuilleContractError(
            "source-pack intake requires a valid handoff row sha256 for "
            f"{_handoff_key(row.item_key, row.attachment_key)}"
        ) from exc
    if evidence.expected_sha256 != row_sha256:
        raise MillefeuilleContractError(
            "source-pack evidence sha256 does not match handoff row for "
            f"{_handoff_key(row.item_key, row.attachment_key)}"
        )
    if (
        row.file_size_bytes is not None
        and evidence.file_size_bytes != row.file_size_bytes
    ):
        raise MillefeuilleContractError(
            "source-pack evidence file_size_bytes does not match "
            f"handoff row for {_handoff_key(row.item_key, row.attachment_key)}"
        )
    if (
        row.content_type is not None
        and evidence.content_type is not None
        and evidence.content_type != row.content_type
    ):
        raise MillefeuilleContractError(
            "source-pack evidence content_type does not match handoff row "
            f"for {_handoff_key(row.item_key, row.attachment_key)}"
        )
    if (
        row.zotero_version is not None
        and evidence.zotero_version is not None
        and evidence.zotero_version != row.zotero_version
    ):
        raise MillefeuilleContractError(
            "source-pack evidence zotero_version does not match handoff row "
            f"for {_handoff_key(row.item_key, row.attachment_key)}"
        )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new_source_pack(
    *,
    source_pack_dir: Path,
    manifest_path: Path,
    target_source_path: Path,
    source_path: Path,
    manifest: dict[str, Any],
) -> None:
    source_pack_dir.mkdir(parents=True, exist_ok=True)
    for dirname in (
        "pages",
        "extractions/native",
        "extractions/mistral-ocr",
        "selected",
        "structure",
        "analyses/millefeuille",
    ):
        (source_pack_dir / dirname).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target_source_path)
    _write_json(manifest_path, manifest)


def _existing_source_pack_status(
    *,
    source_pack_dir: Path,
    manifest_path: Path,
    target_source_path: Path,
    expected_manifest: dict[str, Any],
    expected_source_hash: str,
) -> str | None:
    if not source_pack_dir.exists():
        return None
    if not source_pack_dir.is_dir():
        raise MillefeuilleContractError(
            f"source-pack path exists but is not a directory: {source_pack_dir}"
        )
    if not manifest_path.exists() and not target_source_path.exists():
        if any(source_pack_dir.iterdir()):
            raise MillefeuilleContractError(
                "source pack already exists without manifest/source evidence: "
                f"{source_pack_dir}"
            )
        return None
    if not manifest_path.is_file() or not target_source_path.is_file():
        raise MillefeuilleContractError(
            f"source pack already exists in an incomplete state: {source_pack_dir}"
        )

    existing_manifest = load_source_pack_manifest(manifest_path)
    actual_target_hash = f"sha256:{sha256_file(target_source_path)}"
    if actual_target_hash != expected_source_hash:
        raise MillefeuilleContractError(
            "existing source pack source hash drift: "
            f"expected {expected_source_hash}, got {actual_target_hash}"
        )

    comparable_existing = dict(existing_manifest)
    comparable_expected = dict(expected_manifest)
    comparable_existing.pop("created_at", None)
    comparable_expected.pop("created_at", None)
    if comparable_existing != comparable_expected:
        raise MillefeuilleContractError(
            f"existing source pack manifest does not match evidence: {manifest_path}"
        )
    return "existing"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MillefeuilleContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_string(
    value: Any,
    field_name: str,
    *,
    default: str | None = None,
) -> str | None:
    if value is None:
        return default
    if not isinstance(value, str):
        raise MillefeuilleContractError(f"{field_name} must be a string")
    stripped = value.strip()
    if not stripped:
        return default
    return stripped


def _optional_mapping(value: Any, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MillefeuilleContractError(f"{field_name} must be an object")
    return dict(value)


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise MillefeuilleContractError(f"{field_name} must be an integer")
    return value


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
