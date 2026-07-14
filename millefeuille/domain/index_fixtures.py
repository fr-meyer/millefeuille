"""Fixture-first retrieval/index helpers for verified source packs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.card_fixtures import CARD_JSON_REF, load_paper_card
from millefeuille.domain.millefeuille import (
    MillefeuilleContractError,
    RetrievalIndexRecord,
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
from millefeuille.domain.summary_fixtures import (
    SUMMARY_ARTIFACT_REF,
    load_hierarchical_summary,
)

INDEX_FIXTURE_SCHEMA_VERSION = "millefeuille-index-fixture-evidence/v0.1"
RETRIEVAL_INDEX_STATUS_SCHEMA_VERSION = "millefeuille-retrieval-index-status/v0.1"
INDEX_STATUS_REF = Path("index/index-status.json")


@dataclass(frozen=True)
class IndexFixtureEvidence:
    item_key: str
    attachment_key: str
    canonical_filename: str
    index_status_path: Path
    expected_sha256: str
    source_type: str = "zotero"
    schema_version: str = INDEX_FIXTURE_SCHEMA_VERSION
    zotero_version: int | None = None
    paper_id: str | None = None

    def __post_init__(self) -> None:
        if self.source_type != "zotero":
            raise MillefeuilleContractError("source_type must be 'zotero'")
        object.__setattr__(self, "index_status_path", Path(self.index_status_path))
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
    ) -> IndexFixtureEvidence:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("index fixture evidence must be an object")
        schema_version = _required_string(
            payload.get("schema_version", INDEX_FIXTURE_SCHEMA_VERSION),
            "schema_version",
        )
        if schema_version != INDEX_FIXTURE_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                "unsupported index fixture evidence schema_version "
                f"{schema_version!r}"
            )
        index_status_path = Path(
            _required_string(
                payload.get("index_status_path") or payload.get("index_path"),
                "index_status_path",
            )
        )
        if not index_status_path.is_absolute() and base_dir is not None:
            index_status_path = Path(base_dir) / index_status_path
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
            index_status_path=index_status_path,
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
class IndexFixtureWriteResult:
    paper_id: str
    run_id: str
    status: str
    run_dir: Path
    index_status_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "status": self.status,
            "run_dir": str(self.run_dir),
            "index_status_path": str(self.index_status_path),
        }


@dataclass(frozen=True)
class _PlannedIndexWrite:
    paper_id: str
    run_id: str
    status: str
    run_dir: Path
    index_status_output_path: Path
    expected_payload: dict[str, Any]


def load_index_fixture_evidence_batch(path: str | Path) -> list[IndexFixtureEvidence]:
    evidence_path = Path(path)
    try:
        text = evidence_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read index fixture evidence {evidence_path}: {exc}"
        ) from exc

    if evidence_path.suffix == ".jsonl":
        records: list[IndexFixtureEvidence] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MillefeuilleContractError(
                    "index fixture evidence JSONL is not valid JSON at "
                    f"{evidence_path}:{line_number}"
                ) from exc
            records.append(
                IndexFixtureEvidence.from_dict(
                    payload,
                    base_dir=evidence_path.parent,
                )
            )
        return records

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"index fixture evidence is not valid JSON: {evidence_path}"
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
        IndexFixtureEvidence.from_dict(record, base_dir=evidence_path.parent)
        for record in raw_records
    ]


def write_indexes_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
    run_id: str,
) -> list[IndexFixtureWriteResult]:
    root = Path(source_pack_root)
    resolved_run_id = _required_string(run_id, "run_id")
    records = load_index_fixture_evidence_batch(evidence_path)
    _reject_duplicate_records(records)
    planned = [_plan_index(record, root, resolved_run_id) for record in records]
    return [_apply_planned_index_write(plan) for plan in planned]


def load_retrieval_index_status(path: str | Path) -> dict[str, Any]:
    payload = _load_json_object(path, "retrieval index status")
    record = RetrievalIndexRecord.from_dict(payload)
    return record.to_dict()


def _plan_index(
    evidence: IndexFixtureEvidence,
    source_pack_root: Path,
    run_id: str,
) -> _PlannedIndexWrite:
    source_pack_dir, source_hash = _resolve_source_pack_dir(
        source_pack_root=source_pack_root,
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        expected_sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
        paper_id=evidence.paper_id,
    )
    paper_id = source_pack_dir.name
    run_dir = source_pack_dir / "analyses" / "millefeuille" / run_id
    _validate_route_dependency(source_pack_dir=source_pack_dir, source_hash=source_hash)
    _validate_summary_dependency(
        run_dir=run_dir,
        paper_id=paper_id,
        run_id=run_id,
    )
    _validate_card_dependency(
        run_dir=run_dir,
        paper_id=paper_id,
        source_hash=source_hash,
    )
    fixture_payload = load_retrieval_index_status(evidence.index_status_path)
    index_status_output_path = run_dir / INDEX_STATUS_REF
    expected_payload = _materialize_index_payload(
        fixture_payload=fixture_payload,
        source_pack_dir=source_pack_dir,
        run_dir=run_dir,
        paper_id=paper_id,
        run_id=run_id,
        source_hash=source_hash,
        index_dir=index_status_output_path.parent,
    )
    status = _existing_index_status(
        index_status_output_path=index_status_output_path,
        expected_payload=expected_payload,
    )
    return _PlannedIndexWrite(
        paper_id=paper_id,
        run_id=run_id,
        status=status or "created",
        run_dir=run_dir,
        index_status_output_path=index_status_output_path,
        expected_payload=expected_payload,
    )


def _apply_planned_index_write(plan: _PlannedIndexWrite) -> IndexFixtureWriteResult:
    if plan.status == "existing":
        return IndexFixtureWriteResult(
            paper_id=plan.paper_id,
            run_id=plan.run_id,
            status=plan.status,
            run_dir=plan.run_dir,
            index_status_path=plan.index_status_output_path,
        )

    plan.index_status_output_path.parent.mkdir(parents=True, exist_ok=True)
    plan.index_status_output_path.write_text(
        json.dumps(plan.expected_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return IndexFixtureWriteResult(
        paper_id=plan.paper_id,
        run_id=plan.run_id,
        status=plan.status,
        run_dir=plan.run_dir,
        index_status_path=plan.index_status_output_path,
    )


def _materialize_index_payload(
    *,
    fixture_payload: dict[str, Any],
    source_pack_dir: Path,
    run_dir: Path,
    paper_id: str,
    run_id: str,
    source_hash: str,
    index_dir: Path,
) -> dict[str, Any]:
    record = RetrievalIndexRecord.from_dict(fixture_payload)
    payload = record.to_dict()
    payload["paper_id"] = paper_id
    payload["run_id"] = run_id
    payload["source_hash"] = source_hash
    payload["selected_fulltext_ref"] = _relative_ref(
        source_pack_dir / ROUTE_MARKDOWN_REF,
        index_dir,
    )
    payload["summary_ref"] = _relative_ref(run_dir / SUMMARY_ARTIFACT_REF, index_dir)
    payload["paper_card_ref"] = _relative_ref(run_dir / CARD_JSON_REF, index_dir)
    return payload


def _existing_index_status(
    *,
    index_status_output_path: Path,
    expected_payload: dict[str, Any],
) -> str | None:
    if not index_status_output_path.exists():
        return None
    if not index_status_output_path.is_file():
        raise MillefeuilleContractError(
            "existing retrieval index status is not a file: "
            f"{index_status_output_path}"
        )
    existing_payload = load_retrieval_index_status(index_status_output_path)
    if existing_payload != expected_payload:
        raise MillefeuilleContractError(
            "existing retrieval index status drift for "
            f"{index_status_output_path}"
        )
    return "existing"


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


def _validate_route_dependency(*, source_pack_dir: Path, source_hash: str) -> None:
    route_payload = load_route_selection_sidecar(source_pack_dir / ROUTE_EVIDENCE_REF)
    if route_payload["source_hash"] != source_hash:
        raise MillefeuilleContractError(
            f"route selection source_hash drift for {source_pack_dir}"
        )
    selected_fulltext_path = source_pack_dir / ROUTE_MARKDOWN_REF
    if not selected_fulltext_path.is_file():
        raise MillefeuilleContractError(
            "retrieval index fixture requires selected fulltext: "
            f"{selected_fulltext_path}"
        )


def _validate_summary_dependency(
    *,
    run_dir: Path,
    paper_id: str,
    run_id: str,
) -> None:
    summary_path = run_dir / SUMMARY_ARTIFACT_REF
    payload = load_hierarchical_summary(summary_path)
    if payload["paper_id"] != paper_id:
        raise MillefeuilleContractError(
            f"hierarchical summary paper_id drift at {summary_path}"
        )
    if payload["run_id"] != run_id:
        raise MillefeuilleContractError(
            f"hierarchical summary run_id drift at {summary_path}"
        )
    summary_dir = summary_path.parent
    for summary in payload["summaries"]:
        text_ref = _required_string(summary.get("text_ref"), "text_ref")
        text_path = summary_dir / text_ref
        if not text_path.is_file():
            raise MillefeuilleContractError(
                f"retrieval index fixture requires summary text: {text_path}"
            )


def _validate_card_dependency(
    *,
    run_dir: Path,
    paper_id: str,
    source_hash: str,
) -> None:
    card_json_path = run_dir / CARD_JSON_REF
    payload = load_paper_card(card_json_path)
    if payload["paper_id"] != paper_id:
        raise MillefeuilleContractError(
            f"paper card paper_id drift at {card_json_path}"
        )
    identity = payload.get("identity")
    if not isinstance(identity, dict):
        raise MillefeuilleContractError(
            f"paper card identity must be an object: {card_json_path}"
        )
    if identity.get("source_hash") != source_hash:
        raise MillefeuilleContractError(
            f"paper card source_hash drift at {card_json_path}"
        )


def _reject_duplicate_records(records: list[IndexFixtureEvidence]) -> None:
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record.item_key, record.attachment_key)
        if key in seen:
            raise MillefeuilleContractError(
                "duplicate index fixture evidence for "
                f"{record.item_key}/{record.attachment_key}"
            )
        seen.add(key)


def _relative_ref(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path, start=start)).as_posix()


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
