"""Fixture-first summary helpers for verified source packs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.millefeuille import (
    HierarchicalSummaryRecord,
    MillefeuilleContractError,
)
from millefeuille.domain.secure_io import load_json_object_no_follow
from millefeuille.domain.source_packs import (
    load_source_pack_manifest,
    paper_id_for_zotero_item_key,
)
from millefeuille.domain.structure_fixtures import (
    STRUCTURE_EVIDENCE_REF,
    load_structure_sidecar,
)

SUMMARY_FIXTURE_SCHEMA_VERSION = "millefeuille-summary-fixture-evidence/v0.1"
SUMMARY_ARTIFACT_SCHEMA_VERSION = "millefeuille-hierarchical-summary/v0.1"
SUMMARY_ARTIFACT_REF = Path("summaries/hierarchical-summary.json")
SUMMARY_TEXT_DIR_REF = Path("summaries/texts")
SUMMARY_TEXT_REF_DIR = Path("texts")


@dataclass(frozen=True)
class SummaryFixtureEvidence:
    item_key: str
    attachment_key: str
    canonical_filename: str
    summary_path: Path
    expected_sha256: str
    source_type: str = "zotero"
    schema_version: str = SUMMARY_FIXTURE_SCHEMA_VERSION
    zotero_version: int | None = None
    paper_id: str | None = None

    def __post_init__(self) -> None:
        if self.source_type != "zotero":
            raise MillefeuilleContractError("source_type must be 'zotero'")
        object.__setattr__(self, "summary_path", Path(self.summary_path))
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
    ) -> SummaryFixtureEvidence:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(
                "summary fixture evidence must be an object"
            )
        schema_version = _required_string(
            payload.get("schema_version", SUMMARY_FIXTURE_SCHEMA_VERSION),
            "schema_version",
        )
        if schema_version != SUMMARY_FIXTURE_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                "unsupported summary fixture evidence schema_version "
                f"{schema_version!r}"
            )
        summary_path = Path(
            _required_string(
                payload.get("summary_path") or payload.get("hierarchical_summary_path"),
                "summary_path",
            )
        )
        if not summary_path.is_absolute() and base_dir is not None:
            summary_path = Path(base_dir) / summary_path
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
            summary_path=summary_path,
            expected_sha256=_required_string(
                payload.get("expected_sha256") or payload.get("sha256"),
                "expected_sha256",
            ),
            source_type=_required_string(
                payload.get("source_type", "zotero"),
                "source_type",
            ),
            schema_version=schema_version,
            zotero_version=_optional_int(
                payload.get("zotero_version"),
                "zotero_version",
            ),
            paper_id=_optional_string(payload.get("paper_id"), "paper_id"),
        )


@dataclass(frozen=True)
class SummaryFixtureWriteResult:
    paper_id: str
    run_id: str
    status: str
    run_dir: Path
    summary_path: Path
    summary_text_dir: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "status": self.status,
            "run_dir": str(self.run_dir),
            "summary_path": str(self.summary_path),
            "summary_text_dir": str(self.summary_text_dir),
        }


@dataclass(frozen=True)
class _PlannedSummaryWrite:
    paper_id: str
    run_id: str
    status: str
    run_dir: Path
    summary_output_path: Path
    summary_text_dir: Path
    expected_payload: dict[str, Any]
    expected_texts: dict[Path, str]


def load_summary_fixture_evidence_batch(
    path: str | Path,
) -> list[SummaryFixtureEvidence]:
    evidence_path = Path(path)
    try:
        text = evidence_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read summary fixture evidence {evidence_path}: {exc}"
        ) from exc

    if evidence_path.suffix == ".jsonl":
        records: list[SummaryFixtureEvidence] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MillefeuilleContractError(
                    "summary fixture evidence JSONL is not valid JSON at "
                    f"{evidence_path}:{line_number}"
                ) from exc
            records.append(
                SummaryFixtureEvidence.from_dict(
                    payload,
                    base_dir=evidence_path.parent,
                )
            )
        return records

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"summary fixture evidence is not valid JSON: {evidence_path}"
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
        SummaryFixtureEvidence.from_dict(
            record,
            base_dir=evidence_path.parent,
        )
        for record in raw_records
    ]


def write_summaries_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
    run_id: str,
) -> list[SummaryFixtureWriteResult]:
    root = Path(source_pack_root)
    resolved_run_id = _required_string(run_id, "run_id")
    records = load_summary_fixture_evidence_batch(evidence_path)
    _reject_duplicate_records(records)
    planned = [_plan_summary(record, root, resolved_run_id) for record in records]
    return [_apply_planned_summary_write(plan) for plan in planned]


def load_hierarchical_summary(path: str | Path) -> dict[str, Any]:
    payload = _load_json_object(path, "hierarchical summary")
    record = HierarchicalSummaryRecord.from_dict(payload)
    return record.to_dict()


def _plan_summary(
    evidence: SummaryFixtureEvidence,
    source_pack_root: Path,
    run_id: str,
) -> _PlannedSummaryWrite:
    source_pack_dir, source_hash = _resolve_source_pack_dir(
        source_pack_root=source_pack_root,
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        expected_sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
        paper_id=evidence.paper_id,
    )
    structure_payload = load_structure_sidecar(source_pack_dir / STRUCTURE_EVIDENCE_REF)
    _validate_structure_dependency(
        structure_payload=structure_payload,
        source_hash=source_hash,
        source_pack_dir=source_pack_dir,
    )
    summary_fixture = load_hierarchical_summary(evidence.summary_path)
    paper_id = source_pack_dir.name
    run_dir = source_pack_dir / "analyses" / "millefeuille" / run_id
    summary_output_path = run_dir / SUMMARY_ARTIFACT_REF
    expected_payload, expected_texts = _materialize_summary_bundle(
        fixture_payload=summary_fixture,
        fixture_base_dir=evidence.summary_path.parent,
        paper_id=paper_id,
        run_id=run_id,
        summary_output_path=summary_output_path,
    )
    status = _existing_summary_status(
        summary_output_path=summary_output_path,
        summary_text_dir=run_dir / SUMMARY_TEXT_DIR_REF,
        expected_payload=expected_payload,
        expected_texts=expected_texts,
    )
    return _PlannedSummaryWrite(
        paper_id=paper_id,
        run_id=run_id,
        status=status or "created",
        run_dir=run_dir,
        summary_output_path=summary_output_path,
        summary_text_dir=run_dir / SUMMARY_TEXT_DIR_REF,
        expected_payload=expected_payload,
        expected_texts=expected_texts,
    )


def _materialize_summary_bundle(
    *,
    fixture_payload: dict[str, Any],
    fixture_base_dir: Path,
    paper_id: str,
    run_id: str,
    summary_output_path: Path,
) -> tuple[dict[str, Any], dict[Path, str]]:
    summary_record = HierarchicalSummaryRecord.from_dict(
        {
            "schema_version": SUMMARY_ARTIFACT_SCHEMA_VERSION,
            "paper_id": paper_id,
            "run_id": run_id,
            "taxonomy_context": fixture_payload.get("taxonomy_context"),
            "summaries": [
                _rewrite_summary_entry(
                    entry,
                    fixture_base_dir=fixture_base_dir,
                    summary_output_path=summary_output_path,
                )
                for entry in fixture_payload.get("summaries", [])
            ],
        }
    )
    expected_texts: dict[Path, str] = {}
    for summary in summary_record.summaries:
        text_path = summary_output_path.parent / summary.text_ref
        source_text_path = _resolve_fixture_ref(
            fixture_base_dir,
            fixture_payload["summaries"][
                next(
                    idx
                    for idx, entry in enumerate(fixture_payload["summaries"])
                    if entry["summary_id"] == summary.summary_id
                )
            ]["text_ref"],
            field_name=f"summary[{summary.summary_id}].text_ref",
        )
        expected_texts[text_path] = _read_fixture_text(
            source_text_path,
            kind=f"summary text {summary.summary_id}",
        )
    return summary_record.to_dict(), expected_texts


def _rewrite_summary_entry(
    entry: dict[str, Any],
    *,
    fixture_base_dir: Path,
    summary_output_path: Path,
) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise MillefeuilleContractError("summary entry must be an object")
    summary_id = _required_string(entry.get("summary_id"), "summary_id")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", summary_id):
        raise MillefeuilleContractError(
            f"summary_id contains unsafe characters: {summary_id!r}"
        )
    source_text_ref = _required_string(entry.get("text_ref"), "text_ref")
    _resolve_fixture_ref(
        fixture_base_dir,
        source_text_ref,
        field_name=f"summary[{summary_id}].text_ref",
    )
    payload = dict(entry)
    payload["text_ref"] = (SUMMARY_TEXT_REF_DIR / f"{summary_id}.md").as_posix()
    return payload


def _existing_summary_status(
    *,
    summary_output_path: Path,
    summary_text_dir: Path,
    expected_payload: dict[str, Any],
    expected_texts: dict[Path, str],
) -> str | None:
    summary_exists = summary_output_path.exists()
    text_dir_exists = summary_text_dir.exists()
    if not summary_exists and not text_dir_exists:
        return None
    if not summary_output_path.is_file() or not summary_text_dir.is_dir():
        raise MillefeuilleContractError(
            f"existing summary fixture is incomplete: {summary_output_path.parent}"
        )
    existing_payload = load_hierarchical_summary(summary_output_path)
    if existing_payload != expected_payload:
        raise MillefeuilleContractError(
            f"existing hierarchical summary drift: {summary_output_path}"
        )
    existing_text_paths = {
        path for path in summary_text_dir.glob("*.md") if path.is_file()
    }
    if existing_text_paths != set(expected_texts):
        raise MillefeuilleContractError(
            f"existing summary text drift: {summary_text_dir}"
        )
    for text_path, expected_text in expected_texts.items():
        try:
            existing_text = text_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not read existing summary text {text_path}: {exc}"
            ) from exc
        if existing_text != expected_text:
            raise MillefeuilleContractError(f"existing summary text drift: {text_path}")
    return "existing"


def _apply_planned_summary_write(
    planned: _PlannedSummaryWrite,
) -> SummaryFixtureWriteResult:
    if planned.status == "created":
        planned.summary_output_path.parent.mkdir(parents=True, exist_ok=True)
        planned.summary_text_dir.mkdir(parents=True, exist_ok=True)
        planned.summary_output_path.write_text(
            json.dumps(planned.expected_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for text_path, text in planned.expected_texts.items():
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(text, encoding="utf-8")
    return SummaryFixtureWriteResult(
        paper_id=planned.paper_id,
        run_id=planned.run_id,
        status=planned.status,
        run_dir=planned.run_dir,
        summary_path=planned.summary_output_path,
        summary_text_dir=planned.summary_text_dir,
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


def _validate_structure_dependency(
    *,
    structure_payload: dict[str, Any],
    source_hash: str,
    source_pack_dir: Path,
) -> None:
    if structure_payload["source_hash"] != source_hash:
        raise MillefeuilleContractError(
            f"structure evidence source_hash drift for {source_pack_dir}"
        )
    source_markdown_ref = _required_string(
        structure_payload.get("source_markdown_ref"),
        "source_markdown_ref",
    )
    selected_fulltext_path = source_pack_dir / source_markdown_ref
    if not selected_fulltext_path.is_file():
        raise MillefeuilleContractError(
            f"summary fixture requires selected fulltext: {selected_fulltext_path}"
        )


def _reject_duplicate_records(records: list[SummaryFixtureEvidence]) -> None:
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record.item_key, record.attachment_key)
        if key in seen:
            raise MillefeuilleContractError(
                "duplicate summary fixture evidence for "
                f"{record.item_key}/{record.attachment_key}"
            )
        seen.add(key)


def _resolve_fixture_ref(
    base_dir: Path,
    ref: str,
    *,
    field_name: str,
) -> Path:
    path = Path(ref)
    if not path.is_absolute():
        path = base_dir / path
    if not path.is_file():
        raise MillefeuilleContractError(f"{field_name} file not found: {path}")
    return path


def _read_fixture_text(path: Path, *, kind: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(f"could not read {kind} {path}: {exc}") from exc


def _load_json_object(path: str | Path, kind: str) -> dict[str, Any]:
    return load_json_object_no_follow(path, kind)


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
    if not stripped:
        raise MillefeuilleContractError(f"{field_name} must not be empty")
    return stripped


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise MillefeuilleContractError(f"{field_name} must be an integer")
    return value


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise MillefeuilleContractError("expected_sha256 must be 64 lowercase hex")
    return normalized


def _normalize_source_hash(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", normalized):
        raise MillefeuilleContractError(
            "source_hash must be in sha256:<64 lowercase hex> form"
        )
    return normalized
