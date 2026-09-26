"""Read-only reuse of an immutable published GPT summary in its card run.

The link is operator-controlled data, not an execution or write approval. It
retains the original generation run and provenance. Readers derive an explicit
view with portable refs into the verified publication; no paper bytes are copied.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

from millefeuille.domain.card_index_contract import canonical_json_bytes
from millefeuille.domain.millefeuille import (
    PAPER_CARD_SCHEMA_V2,
    HierarchicalSummaryRecord,
    MillefeuilleContractError,
    PaperCardRecord,
)
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.stage_runtime import require_safe_package_id
from millefeuille.domain.summary_published_handoff import (
    GptSummaryPublicationIdentity,
    load_verified_published_gpt_summary_package,
)

LINK_SCHEMA_VERSION = "millefeuille-published-summary-run-link/v0.1"
VIEW_SCHEMA_VERSION = "millefeuille-published-summary-run-view/v0.1"
_LINK_FIELDS = frozenset(
    {
        "schema_version",
        "paper_id",
        "run_id",
        "source_hash",
        "summary_publication",
        "evidence_refs",
        "card_write_manifest_sha256",
        "summary_record_ref",
        "summary_record_sha256",
    }
)
_EVIDENCE_FIELDS = {
    "route_evidence_ref": "route_evidence_path",
    "structure_evidence_ref": "structure_evidence_path",
    "preparation_ref": "preparation_path",
}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PLANNED_INDEX_STATE = {
    "phase": "planned",
    "lanes": [
        {"lane": "openkb", "status": "pending"},
        {"lane": "pageindex", "status": "pending"},
    ],
}


@dataclass(frozen=True)
class PublishedSummaryRunLinkPlan:
    paper_id: str
    run_id: str
    origin_run_id: str
    ref: str
    manifest_sha256: str
    source_hash: str
    total_bytes: int
    link_json: bytes = field(repr=False)
    provider_calls_performed: int = 0
    writes_performed: int = 0


def plan_published_gpt_summary_run_link(
    *,
    source_pack_root: str | Path,
    run_id: str,
    publication: GptSummaryPublicationIdentity,
    card_write_manifest_sha256: str,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
) -> PublishedSummaryRunLinkPlan:
    """Derive one metadata-only link without writing or granting authority."""
    plan, _record = _plan_and_load(
        source_pack_root=source_pack_root,
        run_id=run_id,
        publication=publication,
        card_write_manifest_sha256=card_write_manifest_sha256,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
    )
    return plan


def load_published_summary_run_view(path: str | Path) -> dict[str, Any]:
    """Reverify the link, source publication and card, then expose a read view.

    The view schema is distinct from generated summary records. ``run_id`` is
    its consuming card run; ``origin_run_id`` identifies the unchanged original
    generation. Text/provenance refs resolve to the original verified files.
    """
    target = Path(path).absolute()
    if str(target) != os.path.normpath(str(target)) or len(target.parts) < 6:
        raise MillefeuilleContractError("published summary link location is invalid")
    run_id = target.parent.parent.name
    require_safe_package_id(run_id, "summary link run_id")
    if target.parts[-5:] != (
        "analyses",
        "millefeuille",
        run_id,
        "summaries",
        "hierarchical-summary.json",
    ):
        raise MillefeuilleContractError("published summary link location is invalid")
    root = target.parents[4]
    raw = read_bytes_no_follow(target, "published summary run link", max_bytes=32768)
    payload = _object(raw)
    if set(payload) != _LINK_FIELDS or payload["schema_version"] != LINK_SCHEMA_VERSION:
        raise MillefeuilleContractError("published summary link fields are invalid")
    if payload["run_id"] != run_id:
        raise MillefeuilleContractError("published summary link run_id drift")
    identity = payload["summary_publication"]
    if not isinstance(identity, dict) or set(identity) != {
        item.name for item in fields(GptSummaryPublicationIdentity)
    }:
        raise MillefeuilleContractError("published summary link identity is invalid")
    publication = GptSummaryPublicationIdentity(**identity)
    refs = payload["evidence_refs"]
    if not isinstance(refs, dict) or set(refs) != set(_EVIDENCE_FIELDS):
        raise MillefeuilleContractError("published summary link evidence is invalid")
    evidence = {
        argument: root / _safe_ref(refs[name])
        for name, argument in _EVIDENCE_FIELDS.items()
    }
    plan, record = _plan_and_load(
        source_pack_root=root,
        run_id=run_id,
        publication=publication,
        card_write_manifest_sha256=payload["card_write_manifest_sha256"],
        **evidence,
    )
    if raw != plan.link_json:
        raise MillefeuilleContractError(
            "published summary link source or metadata drift"
        )
    origin_dir = root / f"analyses/millefeuille/{publication.run_id}/summaries"
    view = deepcopy(record)
    view.update(
        {
            "schema_version": VIEW_SCHEMA_VERSION,
            "run_id": run_id,
            "origin_run_id": publication.run_id,
            "source_hash": plan.source_hash,
            "summary_link_sha256": plan.manifest_sha256,
            "source_summary_ref": payload["summary_record_ref"],
            "source_summary_sha256": payload["summary_record_sha256"],
            "summary_text_dir_ref": _relative(origin_dir / "texts", target.parent),
        }
    )
    for entry in view["summaries"]:
        entry["text_ref"] = _relative(origin_dir / entry["text_ref"], target.parent)
        if entry.get("model_provenance_ref") is not None:
            entry["model_provenance_ref"] = _relative(
                origin_dir / entry["model_provenance_ref"],
                target.parent,
            )
    return view


def _plan_and_load(
    *,
    source_pack_root: str | Path,
    run_id: str,
    publication: GptSummaryPublicationIdentity,
    card_write_manifest_sha256: str,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
) -> tuple[PublishedSummaryRunLinkPlan, dict[str, Any]]:
    root = Path(source_pack_root)
    if not isinstance(publication, GptSummaryPublicationIdentity):
        raise MillefeuilleContractError("summary link publication identity is invalid")
    if not root.is_absolute() or str(root) != os.path.normpath(str(root)):
        raise MillefeuilleContractError("summary link source root is invalid")
    require_safe_package_id(run_id, "summary link run_id")
    if run_id == publication.run_id:
        raise MillefeuilleContractError("summary link must use a distinct card run")
    if not isinstance(card_write_manifest_sha256, str) or not _DIGEST.fullmatch(
        card_write_manifest_sha256
    ):
        raise MillefeuilleContractError("summary link card manifest digest is invalid")
    evidence = {
        "route_evidence_path": route_evidence_path,
        "structure_evidence_path": structure_evidence_path,
        "preparation_path": preparation_path,
    }
    refs = {}
    for name, argument in _EVIDENCE_FIELDS.items():
        try:
            refs[name] = _safe_ref(
                Path(evidence[argument]).relative_to(root).as_posix()
            )
        except ValueError as exc:
            raise MillefeuilleContractError(
                "summary link evidence escaped source root"
            ) from exc
    package = load_verified_published_gpt_summary_package(
        source_pack_root=root,
        publication=publication,
        **evidence,
    )
    held = {item.ref: item.data for item in package.files}
    record_ref = package.handoff.summary_record_ref
    record_bytes = held[record_ref]
    record = HierarchicalSummaryRecord.from_dict(_object(record_bytes)).to_dict()
    _verify_card(
        root=root,
        run_id=run_id,
        publication=publication,
        source_hash=package.handoff.source_hash,
        expected_digest=card_write_manifest_sha256,
    )
    payload = {
        "schema_version": LINK_SCHEMA_VERSION,
        "paper_id": publication.paper_id,
        "run_id": run_id,
        "source_hash": package.handoff.source_hash,
        "summary_publication": asdict(publication),
        "evidence_refs": refs,
        "card_write_manifest_sha256": card_write_manifest_sha256,
        "summary_record_ref": record_ref,
        "summary_record_sha256": _digest(record_bytes),
    }
    encoded = _canonical(payload)
    return PublishedSummaryRunLinkPlan(
        publication.paper_id,
        run_id,
        publication.run_id,
        f"analyses/millefeuille/{run_id}/summaries/hierarchical-summary.json",
        _digest(encoded),
        package.handoff.source_hash,
        len(encoded),
        encoded,
    ), record


def _verify_card(
    *,
    root: Path,
    run_id: str,
    publication: GptSummaryPublicationIdentity,
    source_hash: str,
    expected_digest: str,
) -> None:
    prefix = f"analyses/millefeuille/{run_id}/cards"
    raw = read_bytes_no_follow(
        root / prefix / "write-manifest.json",
        "summary link card manifest",
        max_bytes=32768,
    )
    if _digest(raw) != expected_digest:
        raise MillefeuilleContractError("summary link card manifest drift")
    manifest = _object(raw)
    expected = {
        "schema_version": "millefeuille-gpt-paper-card-write-manifest/v0.1",
        "paper_id": publication.paper_id,
        "run_id": run_id,
        "source_hash": source_hash,
        "summary_run_id": publication.run_id,
        "summary_publication_sha256": publication.bundle_manifest_sha256,
        "write_file_count": 5,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise MillefeuilleContractError("summary link card/source identity drift")
    source = read_bytes_no_follow(
        root / f"zotero/{publication.paper_id}/manifest.json",
        "summary link source identity",
        max_bytes=1048576,
    )
    if _digest(source) != manifest.get("source_pack_manifest_sha256"):
        raise MillefeuilleContractError("summary link source manifest drift")
    entries = manifest.get("entries")
    expected_refs = [
        f"{prefix}/{name}"
        for name in (
            "model-provenance.json",
            "paper-card.json",
            "paper-card.md",
            "request-plan.json",
        )
    ]
    if (
        not isinstance(entries, list)
        or len(entries) != 4
        or [item.get("ref") for item in entries if isinstance(item, dict)]
        != expected_refs
    ):
        raise MillefeuilleContractError("summary link card census drift")
    for entry in entries:
        if (
            set(entry) != {"ref", "sha256", "bytes"}
            or type(entry["bytes"]) is not int
            or entry["bytes"] <= 0
        ):
            raise MillefeuilleContractError("summary link card file binding is invalid")
        data = read_bytes_no_follow(
            root / entry["ref"], "summary link card artifact", max_bytes=1048576
        )
        if entry["ref"].endswith("/paper-card.json"):
            card = PaperCardRecord.from_dict(_object(data)).to_dict()
            if (
                card["schema_version"] != PAPER_CARD_SCHEMA_V2
                or card["paper_id"] != publication.paper_id
                or card["run_id"] != run_id
                or card["identity"].get("source_hash") != source_hash
            ):
                raise MillefeuilleContractError(
                    "summary link current card identity drift"
                )
            # The sanctioned index refresh changes only index_state. Recover the
            # exact planned card bytes to bind every other field to publication.
            card["index_state"] = deepcopy(_PLANNED_INDEX_STATE)
            data = canonical_json_bytes(card)
        elif entry["ref"].endswith("/paper-card.md"):
            # Readability output may be refreshed by its own governed stage.
            # The bound JSON is the content/identity authority for this reader.
            continue
        if _digest(data) != entry["sha256"] or len(data) != entry["bytes"]:
            raise MillefeuilleContractError("summary link card artifact drift")


def _safe_ref(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or ":" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise MillefeuilleContractError("summary link reference is unsafe")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {".", ".."} for part in value.split("/"))
        or path.as_posix() != value
    ):
        raise MillefeuilleContractError("summary link reference is unsafe")
    return value


def _object(data: bytes) -> dict[str, Any]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MillefeuilleContractError("summary link duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            data,
            object_pairs_hook=unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except MillefeuilleContractError:
        raise
    except (ValueError, UnicodeError) as exc:
        raise MillefeuilleContractError("summary link JSON is invalid") from exc
    if not isinstance(value, dict):
        raise MillefeuilleContractError("summary link JSON must be an object")
    return value


def _canonical(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _relative(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path, base)).as_posix()
