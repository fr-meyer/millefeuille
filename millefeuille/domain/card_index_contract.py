"""Canonical, acyclic contract joining paper-card and retrieval-index artifacts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from millefeuille.domain.millefeuille import (
    PAPER_CARD_INDEX_STATUS_REF,
    PAPER_CARD_SCHEMA_V1,
    PAPER_CARD_SCHEMA_V2,
    MillefeuilleContractError,
    PaperCardRecord,
    RetrievalIndexRecord,
)
from millefeuille.domain.secure_io import (
    read_bytes_no_follow,
    verify_regular_file_no_follow,
)

FileVerifier = Callable[[Path, str], None]


@dataclass(frozen=True)
class LoadedJsonArtifact:
    payload: dict[str, Any]
    raw_bytes: bytes


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """Return the one canonical on-disk JSON representation for run artifacts."""

    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_paper_card_artifact(path: str | Path) -> LoadedJsonArtifact:
    """Securely load a card; v0.2 run artifacts must use canonical JSON bytes."""

    target = Path(path)
    raw_bytes, payload = _load_json_bytes_no_follow(target, "paper card")
    normalized = PaperCardRecord.from_dict(payload).to_dict()
    if payload.get("schema_version") == PAPER_CARD_SCHEMA_V2:
        if normalized != payload:
            raise MillefeuilleContractError(
                f"v0.2 paper card is not canonical: {target}"
            )
        if raw_bytes != canonical_json_bytes(payload):
            raise MillefeuilleContractError(
                f"v0.2 paper card does not use canonical JSON bytes: {target}"
            )
        return LoadedJsonArtifact(payload=payload, raw_bytes=raw_bytes)
    return LoadedJsonArtifact(payload=normalized, raw_bytes=raw_bytes)


def load_retrieval_index_artifact(path: str | Path) -> LoadedJsonArtifact:
    """Securely load an exact canonical retrieval-index run artifact."""

    target = Path(path)
    raw_bytes, payload = _load_json_bytes_no_follow(
        target,
        "retrieval index status",
    )
    normalized = RetrievalIndexRecord.from_dict(payload).to_dict()
    if normalized != payload:
        raise MillefeuilleContractError(
            f"retrieval index status is not canonical: {target}"
        )
    if raw_bytes != canonical_json_bytes(payload):
        raise MillefeuilleContractError(
            f"retrieval index status does not use canonical JSON bytes: {target}"
        )
    return LoadedJsonArtifact(payload=payload, raw_bytes=raw_bytes)


def validate_paper_card_identity(
    card_payload: dict[str, Any],
    *,
    paper_id: str,
    run_id: str,
    source_hash: str,
    require_source_hash: bool = False,
) -> None:
    """Validate card identity with explicit read-only v0.1 compatibility.

    Strict card/index joins set require_source_hash so legacy cards cannot be
    associated with a source pack unless they carry its exact hash.
    """

    card = PaperCardRecord.from_dict(card_payload).to_dict()
    if card["paper_id"] != paper_id:
        raise MillefeuilleContractError("paper card paper_id drift")
    identity = card.get("identity")
    if not isinstance(identity, dict):
        raise MillefeuilleContractError("paper card identity must be an object")
    card_source_hash = identity.get("source_hash")
    if card["schema_version"] == PAPER_CARD_SCHEMA_V2:
        if card.get("run_id") != run_id:
            raise MillefeuilleContractError("paper card run_id drift")
        if card_source_hash != source_hash:
            raise MillefeuilleContractError("paper card source_hash drift")
        return
    if card["schema_version"] != PAPER_CARD_SCHEMA_V1:
        raise MillefeuilleContractError(
            f"unsupported paper card schema_version {card['schema_version']!r}"
        )
    if require_source_hash and card_source_hash is None:
        raise MillefeuilleContractError(
            "legacy paper card source_hash is required for card/index join"
        )
    if card_source_hash not in (None, source_hash):
        raise MillefeuilleContractError("legacy paper card source_hash drift")


def observed_index_state(index_payload: dict[str, Any]) -> dict[str, Any]:
    record = RetrievalIndexRecord.from_dict(index_payload)
    return {
        "phase": "observed",
        "status_ref": PAPER_CARD_INDEX_STATUS_REF,
        "lanes": [{"lane": lane.lane, "status": lane.status} for lane in record.lanes],
    }


def validate_observed_card_index_state(
    card_payload: dict[str, Any],
    index_payload: dict[str, Any],
) -> None:
    """Validate the immutable identity and v0.2 observed-state join."""

    card = PaperCardRecord.from_dict(card_payload).to_dict()
    index = RetrievalIndexRecord.from_dict(index_payload).to_dict()
    validate_paper_card_identity(
        card,
        paper_id=index["paper_id"],
        run_id=index["run_id"],
        source_hash=index["source_hash"],
        require_source_hash=True,
    )
    if card["schema_version"] == PAPER_CARD_SCHEMA_V1:
        return
    if card["index_state"] != observed_index_state(index):
        raise MillefeuilleContractError(
            "paper card observed index_state conflicts with retrieval index status"
        )


def validate_canonical_card_index_contract(
    *,
    card_payload: dict[str, Any],
    index_payload: dict[str, Any],
    paper_id: str,
    run_id: str,
    source_hash: str,
    index_dir: Path,
    selected_fulltext_path: Path,
    summary_path: Path,
    card_path: Path,
    verify_file: FileVerifier = verify_regular_file_no_follow,
) -> None:
    """Validate identities, exact canonical refs, target files, and card state."""

    index = RetrievalIndexRecord.from_dict(index_payload).to_dict()
    if index["paper_id"] != paper_id:
        raise MillefeuilleContractError("retrieval index status paper_id drift")
    if index["run_id"] != run_id:
        raise MillefeuilleContractError("retrieval index status run_id drift")
    if index["source_hash"] != source_hash:
        raise MillefeuilleContractError("retrieval index status source_hash drift")

    expected_refs = {
        "selected_fulltext_ref": _relative_ref(selected_fulltext_path, index_dir),
        "summary_ref": _relative_ref(summary_path, index_dir),
        "paper_card_ref": _relative_ref(card_path, index_dir),
    }
    resolved_paths = {
        "selected_fulltext_ref": selected_fulltext_path,
        "summary_ref": summary_path,
        "paper_card_ref": card_path,
    }
    labels = {
        "selected_fulltext_ref": "selected fulltext",
        "summary_ref": "hierarchical summary",
        "paper_card_ref": "paper card",
    }
    for field_name, expected_ref in expected_refs.items():
        if index[field_name] != expected_ref:
            raise MillefeuilleContractError(
                f"retrieval index {field_name} drift: expected canonical reference"
            )
        verify_file(resolved_paths[field_name], labels[field_name])

    validate_paper_card_identity(
        card_payload,
        paper_id=paper_id,
        run_id=run_id,
        source_hash=source_hash,
        require_source_hash=True,
    )
    validate_observed_card_index_state(card_payload, index)


def load_and_validate_canonical_card_index(
    *,
    card_path: Path,
    index_path: Path,
    paper_id: str,
    run_id: str,
    source_hash: str,
    selected_fulltext_path: Path,
    summary_path: Path,
) -> tuple[LoadedJsonArtifact, LoadedJsonArtifact]:
    """Securely load and validate the canonical pair used by filesystem consumers."""

    card = load_paper_card_artifact(card_path)
    index = load_retrieval_index_artifact(index_path)
    validate_canonical_card_index_contract(
        card_payload=card.payload,
        index_payload=index.payload,
        paper_id=paper_id,
        run_id=run_id,
        source_hash=source_hash,
        index_dir=index_path.parent,
        selected_fulltext_path=selected_fulltext_path,
        summary_path=summary_path,
        card_path=card_path,
    )
    return card, index


def _load_json_bytes_no_follow(
    path: Path,
    label: str,
) -> tuple[bytes, dict[str, Any]]:
    raw_bytes = read_bytes_no_follow(path, label)
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MillefeuilleContractError(f"{label} is not valid UTF-8: {path}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError(f"{label} must be an object")
    return raw_bytes, payload


def _relative_ref(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path, start=start)).as_posix()
