"""Fixture-first paper-card helpers for verified source packs."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.card_index_contract import (
    canonical_json_bytes,
    load_and_validate_canonical_card_index,
    load_paper_card_artifact,
)
from millefeuille.domain.millefeuille import (
    PAPER_CARD_SCHEMA_V2,
    MillefeuilleContractError,
    PaperCardRecord,
)
from millefeuille.domain.route_fixtures import ROUTE_MARKDOWN_REF
from millefeuille.domain.secure_io import (
    load_json_object_no_follow,
    read_text_no_follow,
)
from millefeuille.domain.source_packs import (
    load_source_pack_manifest,
    paper_id_for_zotero_item_key,
)
from millefeuille.domain.structure_fixtures import (
    STRUCTURE_EVIDENCE_REF,
    load_structure_sidecar,
)
from millefeuille.domain.summary_fixtures import (
    SUMMARY_ARTIFACT_REF,
    load_hierarchical_summary,
)

CARD_FIXTURE_SCHEMA_VERSION = "millefeuille-card-fixture-evidence/v0.1"
PAPER_CARD_SCHEMA_VERSION = PAPER_CARD_SCHEMA_V2
CARD_JSON_REF = Path("cards/paper-card.json")
CARD_MARKDOWN_REF = Path("cards/paper-card.md")


@dataclass(frozen=True)
class CardFixtureEvidence:
    item_key: str
    attachment_key: str
    canonical_filename: str
    card_json_path: Path
    card_markdown_path: Path
    expected_sha256: str
    source_type: str = "zotero"
    schema_version: str = CARD_FIXTURE_SCHEMA_VERSION
    zotero_version: int | None = None
    paper_id: str | None = None

    def __post_init__(self) -> None:
        if self.source_type != "zotero":
            raise MillefeuilleContractError("source_type must be 'zotero'")
        object.__setattr__(self, "card_json_path", Path(self.card_json_path))
        object.__setattr__(self, "card_markdown_path", Path(self.card_markdown_path))
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
    ) -> CardFixtureEvidence:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("card fixture evidence must be an object")
        schema_version = _required_string(
            payload.get("schema_version", CARD_FIXTURE_SCHEMA_VERSION),
            "schema_version",
        )
        if schema_version != CARD_FIXTURE_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                f"unsupported card fixture evidence schema_version {schema_version!r}"
            )
        card_json_path = _resolve_optional_path(
            payload.get("card_json_path") or payload.get("json_path"),
            base_dir=base_dir,
            field_name="card_json_path",
        )
        card_markdown_path = _resolve_optional_path(
            payload.get("card_markdown_path") or payload.get("markdown_path"),
            base_dir=base_dir,
            field_name="card_markdown_path",
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
            card_json_path=card_json_path,
            card_markdown_path=card_markdown_path,
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
class CardFixtureWriteResult:
    paper_id: str
    run_id: str
    status: str
    run_dir: Path
    card_json_path: Path
    card_markdown_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "status": self.status,
            "run_dir": str(self.run_dir),
            "card_json_path": str(self.card_json_path),
            "card_markdown_path": str(self.card_markdown_path),
        }


@dataclass(frozen=True)
class _PlannedCardWrite:
    paper_id: str
    run_id: str
    status: str
    run_dir: Path
    card_json_output_path: Path
    card_markdown_output_path: Path
    expected_payload: dict[str, Any]
    expected_markdown: str


def load_card_fixture_evidence_batch(path: str | Path) -> list[CardFixtureEvidence]:
    evidence_path = Path(path)
    try:
        text = evidence_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read card fixture evidence {evidence_path}: {exc}"
        ) from exc

    if evidence_path.suffix == ".jsonl":
        records: list[CardFixtureEvidence] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MillefeuilleContractError(
                    "card fixture evidence JSONL is not valid JSON at "
                    f"{evidence_path}:{line_number}"
                ) from exc
            records.append(
                CardFixtureEvidence.from_dict(
                    payload,
                    base_dir=evidence_path.parent,
                )
            )
        return records

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"card fixture evidence is not valid JSON: {evidence_path}"
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
        CardFixtureEvidence.from_dict(record, base_dir=evidence_path.parent)
        for record in raw_records
    ]


def write_cards_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
    run_id: str,
) -> list[CardFixtureWriteResult]:
    root = Path(source_pack_root)
    resolved_run_id = _required_string(run_id, "run_id")
    records = load_card_fixture_evidence_batch(evidence_path)
    _reject_duplicate_records(records)
    planned = [_plan_card(record, root, resolved_run_id) for record in records]
    return [_apply_planned_card_write(plan) for plan in planned]


def load_paper_card(path: str | Path) -> dict[str, Any]:
    return load_paper_card_artifact(path).payload


def _plan_card(
    evidence: CardFixtureEvidence,
    source_pack_root: Path,
    run_id: str,
) -> _PlannedCardWrite:
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
    _validate_summary_dependency(
        source_pack_dir=source_pack_dir,
        run_dir=run_dir,
        paper_id=paper_id,
        run_id=run_id,
        source_hash=source_hash,
    )
    card_payload = _load_paper_card_fixture(evidence.card_json_path)
    expected_payload = _materialize_card_payload(
        fixture_payload=card_payload,
        item_key=evidence.item_key,
        canonical_filename=evidence.canonical_filename,
        paper_id=paper_id,
        run_id=run_id,
        source_hash=source_hash,
    )
    expected_markdown = _read_text_fixture(
        evidence.card_markdown_path,
        kind="paper card markdown",
    )
    card_json_output_path = run_dir / CARD_JSON_REF
    card_markdown_output_path = run_dir / CARD_MARKDOWN_REF
    status = _existing_card_status(
        card_json_output_path=card_json_output_path,
        card_markdown_output_path=card_markdown_output_path,
        expected_payload=expected_payload,
        expected_markdown=expected_markdown,
    )
    return _PlannedCardWrite(
        paper_id=paper_id,
        run_id=run_id,
        status=status or "created",
        run_dir=run_dir,
        card_json_output_path=card_json_output_path,
        card_markdown_output_path=card_markdown_output_path,
        expected_payload=expected_payload,
        expected_markdown=expected_markdown,
    )


def _materialize_card_payload(
    *,
    fixture_payload: dict[str, Any],
    item_key: str,
    canonical_filename: str,
    paper_id: str,
    run_id: str,
    source_hash: str,
) -> dict[str, Any]:
    normalized = copy.deepcopy(fixture_payload)
    identity = dict(normalized["identity"])
    identity["zotero_item_key"] = item_key
    identity["canonical_filename"] = canonical_filename
    identity["source_hash"] = source_hash
    normalized["paper_id"] = paper_id
    normalized["identity"] = identity
    normalized["evidence_refs"] = [
        "../summaries/hierarchical-summary.json",
        "../../../structure/structure.json",
    ]
    if normalized["schema_version"] == PAPER_CARD_SCHEMA_V2:
        normalized["run_id"] = run_id
    record = PaperCardRecord.from_dict(normalized)
    if (
        record.schema_version == PAPER_CARD_SCHEMA_V2
        and record.index_state.get("phase") != "planned"
    ):
        raise MillefeuilleContractError(
            "v0.2 paper card fixture index_state must be planned"
        )
    return record.to_dict()


def _validate_summary_dependency(
    *,
    source_pack_dir: Path,
    run_dir: Path,
    paper_id: str,
    run_id: str,
    source_hash: str,
) -> None:
    structure_payload = load_structure_sidecar(source_pack_dir / STRUCTURE_EVIDENCE_REF)
    if structure_payload["source_hash"] != source_hash:
        raise MillefeuilleContractError(
            f"structure evidence source_hash drift for {source_pack_dir}"
        )
    summary_path = run_dir / SUMMARY_ARTIFACT_REF
    if not summary_path.is_file():
        raise MillefeuilleContractError(
            f"paper card fixture requires hierarchical summary: {summary_path}"
        )
    summary_payload = load_hierarchical_summary(summary_path)
    if summary_payload["paper_id"] != paper_id:
        raise MillefeuilleContractError(
            f"hierarchical summary paper_id drift for {summary_path}"
        )
    if summary_payload["run_id"] != run_id:
        raise MillefeuilleContractError(
            f"hierarchical summary run_id drift for {summary_path}"
        )


def _existing_card_status(
    *,
    card_json_output_path: Path,
    card_markdown_output_path: Path,
    expected_payload: dict[str, Any],
    expected_markdown: str,
) -> str | None:
    json_exists = card_json_output_path.exists()
    markdown_exists = card_markdown_output_path.exists()
    if not json_exists and not markdown_exists:
        return None
    if not card_json_output_path.is_file() or not card_markdown_output_path.is_file():
        raise MillefeuilleContractError(
            f"existing paper card fixture is incomplete: {card_json_output_path.parent}"
        )
    existing_payload = load_paper_card(card_json_output_path)
    if existing_payload != expected_payload:
        _require_observed_card_matches_planned_card(
            existing_payload=existing_payload,
            expected_payload=expected_payload,
            card_json_output_path=card_json_output_path,
        )
    existing_markdown = read_text_no_follow(
        card_markdown_output_path,
        "paper card markdown",
    )
    if existing_markdown != expected_markdown:
        raise MillefeuilleContractError(
            f"existing paper card markdown drift: {card_markdown_output_path}"
        )
    return "existing"


def _require_observed_card_matches_planned_card(
    *,
    existing_payload: dict[str, Any],
    expected_payload: dict[str, Any],
    card_json_output_path: Path,
) -> None:
    if (
        existing_payload.get("schema_version") != PAPER_CARD_SCHEMA_V2
        or expected_payload.get("schema_version") != PAPER_CARD_SCHEMA_V2
    ):
        raise MillefeuilleContractError(
            f"existing paper card drift: {card_json_output_path}"
        )
    existing_state = existing_payload.get("index_state")
    expected_state = expected_payload.get("index_state")
    if not isinstance(existing_state, dict) or not isinstance(expected_state, dict):
        raise MillefeuilleContractError(
            f"existing paper card drift: {card_json_output_path}"
        )
    if existing_state.get("phase") != "observed":
        raise MillefeuilleContractError(
            f"existing paper card drift: {card_json_output_path}"
        )
    if expected_state.get("phase") != "planned":
        raise MillefeuilleContractError(
            f"existing paper card drift: {card_json_output_path}"
        )
    existing_lanes = [entry.get("lane") for entry in existing_state.get("lanes", [])]
    expected_lanes = [entry.get("lane") for entry in expected_state.get("lanes", [])]
    if existing_lanes != expected_lanes:
        raise MillefeuilleContractError(
            f"existing paper card drift: {card_json_output_path}"
        )
    existing_without_state = dict(existing_payload)
    expected_without_state = dict(expected_payload)
    existing_without_state.pop("index_state", None)
    expected_without_state.pop("index_state", None)
    if existing_without_state != expected_without_state:
        raise MillefeuilleContractError(
            f"existing paper card drift: {card_json_output_path}"
        )

    run_dir = card_json_output_path.parent.parent
    source_pack_dir = run_dir.parents[2]
    identity = existing_payload["identity"]
    load_and_validate_canonical_card_index(
        card_path=card_json_output_path,
        index_path=run_dir / "index" / "index-status.json",
        paper_id=existing_payload["paper_id"],
        run_id=existing_payload["run_id"],
        source_hash=identity["source_hash"],
        selected_fulltext_path=source_pack_dir / ROUTE_MARKDOWN_REF,
        summary_path=run_dir / SUMMARY_ARTIFACT_REF,
    )


def _apply_planned_card_write(
    planned: _PlannedCardWrite,
) -> CardFixtureWriteResult:
    if planned.status == "created":
        planned.card_json_output_path.parent.mkdir(parents=True, exist_ok=True)
        planned.card_json_output_path.write_bytes(
            canonical_json_bytes(planned.expected_payload)
        )
        planned.card_markdown_output_path.write_bytes(
            planned.expected_markdown.encode("utf-8")
        )
    return CardFixtureWriteResult(
        paper_id=planned.paper_id,
        run_id=planned.run_id,
        status=planned.status,
        run_dir=planned.run_dir,
        card_json_path=planned.card_json_output_path,
        card_markdown_path=planned.card_markdown_output_path,
    )


def _load_paper_card_fixture(path: str | Path) -> dict[str, Any]:
    payload = _load_json_object(path, "paper card fixture")
    if payload.get("schema_version") == PAPER_CARD_SCHEMA_V2:
        identity = payload.get("identity")
        if isinstance(identity, dict) and "source_hash" not in identity:
            # Fixture templates precede source-pack identity binding. Inject a
            # syntactically valid sentinel only for strict template validation;
            # materialization replaces it with the verified manifest hash.
            candidate = copy.deepcopy(payload)
            candidate["identity"]["source_hash"] = "sha256:" + ("0" * 64)
            normalized = PaperCardRecord.from_dict(candidate).to_dict()
            normalized["identity"].pop("source_hash")
            return normalized
    return PaperCardRecord.from_dict(payload).to_dict()


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


def _reject_duplicate_records(records: list[CardFixtureEvidence]) -> None:
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record.item_key, record.attachment_key)
        if key in seen:
            raise MillefeuilleContractError(
                "duplicate paper card fixture evidence for "
                f"{record.item_key}/{record.attachment_key}"
            )
        seen.add(key)


def _read_text_fixture(path: Path, *, kind: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(f"could not read {kind} {path}: {exc}") from exc


def _load_json_object(path: str | Path, kind: str) -> dict[str, Any]:
    object_path = Path(path)
    try:
        return load_json_object_no_follow(object_path, kind)
    except MillefeuilleContractError as exc:
        message = str(exc)
        if f"{kind} not found:" in message or message.startswith(
            f"could not open {kind}"
        ):
            raise MillefeuilleContractError(
                f"could not read {kind} {object_path}: {message}"
            ) from exc
        raise


def _resolve_optional_path(
    value: Any,
    *,
    base_dir: str | Path | None,
    field_name: str,
) -> Path:
    path = Path(_required_string(value, field_name))
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    return path


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
