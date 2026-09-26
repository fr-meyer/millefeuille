"""Read-only, hash-bound handoff from a published GPT summary run.

The caller supplies commit identity from a trusted publication record. This
reader checks files and source identity only; it grants no execution, write,
acceptance, indexing, or classification authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from millefeuille.domain.millefeuille import (
    HierarchicalSummaryRecord,
    MillefeuilleContractError,
)
from millefeuille.domain.operator_preflight import compute_operator_root_target_id
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.source_packs import (
    SOURCE_PACK_MANIFEST_SCHEMA_VERSION,
    parse_source_pack_manifest,
)
from millefeuille.domain.stage_runtime import require_safe_package_id
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_execution_scope import (
    validate_gpt_summary_evidence_paths,
)
from millefeuille.domain.summary_preparation import verify_summary_preparation_package

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SUMMARY_ID = re.compile(r"summarize_(?:page|section|full_paper)-[a-z0-9-]+\Z")


@dataclass(frozen=True)
class GptSummaryPublicationIdentity:
    paper_id: str
    run_id: str
    root_target_id: str
    packet_digest: str
    receipt_digest: str
    source_manifest_sha256: str
    write_manifest_sha256: str
    observed_usage_sha256: str
    provenance_manifest_sha256: str
    bundle_manifest_sha256: str
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class PublishedGptSummaryHandoff:
    publication: GptSummaryPublicationIdentity
    source_hash: str
    source_pack_ref: str
    summary_record_ref: str
    summary_text_refs: tuple[str, ...]
    source_input_refs: tuple[str, ...]
    provenance_refs: tuple[str, ...]
    provider_calls_performed: int = 0
    writes_performed: int = 0


@dataclass(frozen=True)
class VerifiedPublishedGptSummaryInputs:
    """Private bytes captured during the same verified publication read."""

    handoff: PublishedGptSummaryHandoff
    markdown: bytes = field(repr=False)
    structure: bytes = field(repr=False)
    summaries: tuple[dict[str, Any], ...] = field(repr=False)


def plan_published_gpt_summary_handoff(
    *,
    source_pack_root: str | Path,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    publication: GptSummaryPublicationIdentity,
) -> PublishedGptSummaryHandoff:
    """Verify the immutable run and expose metadata only, without authority."""

    handoff, _data = _verify_published_gpt_summary_data(
        source_pack_root=source_pack_root,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
        publication=publication,
    )
    return handoff


def load_published_gpt_summary_card_inputs(
    *,
    source_pack_root: str | Path,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    publication: GptSummaryPublicationIdentity,
) -> VerifiedPublishedGptSummaryInputs:
    """Return bytes held by the hash verifier, with no unchecked second read.

    This grants no execution, reservation, publication or acceptance authority.
    Every snapshot and summary byte participates in the expected trusted bundle.
    """

    handoff, data = _verify_published_gpt_summary_data(
        source_pack_root=source_pack_root,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
        publication=publication,
    )
    record = HierarchicalSummaryRecord.from_dict(
        _object(data[handoff.summary_record_ref])
    ).to_dict()
    prefix = handoff.summary_record_ref.rsplit("/", 1)[0]
    expected_refs = set(handoff.summary_text_refs)
    seen: set[str] = set()
    summaries = []
    for item in record["summaries"]:
        summary_id = item["summary_id"]
        relative = f"texts/{summary_id}.md"
        ref = f"{prefix}/{relative}"
        if item["text_ref"] != relative or ref not in expected_refs or ref in seen:
            raise MillefeuilleContractError("GPT published summary input ref drift")
        seen.add(ref)
        try:
            text = data[ref].decode("utf-8")
        except UnicodeError as exc:
            raise MillefeuilleContractError(
                "GPT published summary is not UTF-8"
            ) from exc
        summaries.append(
            {
                "summary_id": summary_id,
                "summary": text,
                "source_locators": list(item["source_locators"]),
            }
        )
    if seen != expected_refs:
        raise MillefeuilleContractError("GPT published summary input coverage drift")
    snapshots = {ref.rsplit("/", 1)[-1]: data[ref] for ref in handoff.source_input_refs}
    return VerifiedPublishedGptSummaryInputs(
        handoff, snapshots["selected.md"], snapshots["structure.json"], tuple(summaries)
    )


def _verify_published_gpt_summary_data(
    *,
    source_pack_root: str | Path,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    publication: GptSummaryPublicationIdentity,
) -> tuple[PublishedGptSummaryHandoff, dict[str, bytes]]:
    """Verify an immutable publication before exposing downstream input refs.

    Publication identity is an expected fingerprint, not a self-issued
    authorization. No receipt is reserved or checked for current expiry when
    reading an already published run.
    """

    if not isinstance(publication, GptSummaryPublicationIdentity):
        raise MillefeuilleContractError("GPT published identity is invalid")
    require_safe_package_id(publication.paper_id, "paper_id")
    require_safe_package_id(publication.run_id, "run_id")
    for key, value in vars(publication).items():
        if (
            key.endswith("sha256") or key.endswith("digest") or key == "root_target_id"
        ) and (not isinstance(value, str) or not _DIGEST.fullmatch(value)):
            raise MillefeuilleContractError("GPT published digest is invalid")
    if any(
        type(value) is not int or value <= 0
        for value in (publication.file_count, publication.total_bytes)
    ):
        raise MillefeuilleContractError("GPT published size is invalid")
    root = Path(source_pack_root)
    evidence = {
        "route_evidence_path": str(route_evidence_path),
        "structure_evidence_path": str(structure_evidence_path),
        "preparation_path": str(preparation_path),
    }
    validate_gpt_summary_evidence_paths(evidence=evidence, source_pack_root=str(root))
    if compute_operator_root_target_id(str(root)) != publication.root_target_id:
        raise MillefeuilleContractError("GPT published destination drift")
    fresh = plan_grounded_gpt_summary_batch(**evidence)
    prepared = verify_summary_preparation_package(**evidence)
    if (
        fresh.batch.paper_id != publication.paper_id
        or fresh.manifest.sha256 != publication.source_manifest_sha256
    ):
        raise MillefeuilleContractError("GPT published source scope drift")
    source_pack_ref = f"zotero/{publication.paper_id}"
    source = parse_source_pack_manifest(
        _object(
            read_bytes_no_follow(
                root / source_pack_ref / "manifest.json",
                "GPT published source-pack manifest",
            )
        )
    )
    if source["schema_version"] != SOURCE_PACK_MANIFEST_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "GPT published handoff requires a single-source pack"
        )
    identity = prepared["identity"]
    source_hash = "sha256:" + identity["expected_sha256"]
    if (
        source["paper_id"] != publication.paper_id
        or source["source_hash"] != source_hash
        or source["source_type"] != identity["source_type"]
        or source["identity"]["zotero_item_key"] != identity["item_key"]
        or source["identity"]["zotero_attachment_key"] != identity["attachment_key"]
        or source["identity"]["canonical_filename"] != identity["canonical_filename"]
    ):
        raise MillefeuilleContractError("GPT published source-pack identity drift")
    prefix = f"analyses/millefeuille/{publication.run_id}"
    summaries = f"{prefix}/summaries"
    data: dict[str, bytes] = {}

    def read(ref: str, expected: str) -> bytes:
        if not isinstance(ref, str) or not ref.startswith(prefix + "/"):
            raise MillefeuilleContractError("GPT published ref is outside its run")
        if any(part in {"", ".", ".."} for part in ref.split("/")):
            raise MillefeuilleContractError("GPT published ref is unsafe")
        encoded = read_bytes_no_follow(root / ref, "GPT published artifact")
        if _digest(encoded) != expected:
            raise MillefeuilleContractError("GPT published artifact hash drift")
        if ref in data:
            raise MillefeuilleContractError("GPT published artifact ref is duplicated")
        data[ref] = encoded
        return encoded

    write_ref = f"{summaries}/write-manifest.json"
    write = _object(read(write_ref, publication.write_manifest_sha256))
    observed = _object(
        read(f"{summaries}/observed-usage.json", publication.observed_usage_sha256)
    )
    provenance = _object(
        read(
            f"{summaries}/provenance-manifest.json",
            publication.provenance_manifest_sha256,
        )
    )
    for manifest in (write, observed, provenance):
        if (
            manifest.get("paper_id") != publication.paper_id
            or manifest.get("run_id") != publication.run_id
            or manifest.get("source_manifest_sha256")
            != publication.source_manifest_sha256
        ):
            raise MillefeuilleContractError("GPT published manifest identity drift")
    summary_ref = f"{summaries}/hierarchical-summary.json"
    if write.get("summary_record_ref") != summary_ref:
        raise MillefeuilleContractError("GPT published summary ref drift")
    summary = HierarchicalSummaryRecord.from_dict(
        _object(read(summary_ref, write["summary_record_sha256"]))
    ).to_dict()
    if (
        summary["paper_id"] != publication.paper_id
        or summary["run_id"] != publication.run_id
    ):
        raise MillefeuilleContractError("GPT published summary identity drift")
    source_refs = []
    for key, filename in (
        ("selected_markdown", "selected.md"),
        ("structure", "structure.json"),
    ):
        ref = f"{prefix}/structure/inputs/{filename}"
        expected = "sha256:" + prepared["inputs"][key]["sha256"]
        if {"ref": ref, "sha256": expected} not in write["source_inputs"]:
            raise MillefeuilleContractError("GPT published source snapshot drift")
        read(ref, expected)
        source_refs.append(ref)
    if len(write["source_inputs"]) != 2:
        raise MillefeuilleContractError("GPT published source snapshot coverage drift")
    entries = write["entries"]
    provenance_entries = provenance["entries"]
    if len(entries) != len(fresh.batch.units) or len(provenance_entries) != len(
        entries
    ):
        raise MillefeuilleContractError("GPT published unit coverage drift")
    text_refs, provenance_refs = [], []
    for unit, entry, model_entry in zip(
        fresh.batch.units, entries, provenance_entries, strict=True
    ):
        summary_id = f"{unit.stage}-{unit.unit_id}"
        if not _SUMMARY_ID.fullmatch(summary_id) or (
            entry["summary_id"] != summary_id or model_entry["summary_id"] != summary_id
        ):
            raise MillefeuilleContractError("GPT published unit identity drift")
        ref = f"{summaries}/texts/{summary_id}.md"
        model_ref = f"{summaries}/provenance/{summary_id}.json"
        if (
            entry["text_ref"] != ref
            or entry["provenance_ref"] != model_ref
            or model_entry["text_ref"] != ref
            or model_entry["provenance_ref"] != model_ref
            or model_entry["text_sha256"] != entry["text_sha256"]
        ):
            raise MillefeuilleContractError("GPT published unit ref drift")
        read(ref, entry["text_sha256"])
        read(model_ref, model_entry["provenance_sha256"])
        text_refs.append(ref)
        provenance_refs.append(model_ref)
    run_dir = root / prefix
    actual_refs = set()
    for directory, children, filenames in os.walk(run_dir, followlinks=False):
        for name in [*children, *filenames]:
            path = Path(directory) / name
            if stat.S_ISLNK(path.lstat().st_mode):
                raise MillefeuilleContractError("GPT published run contains a symlink")
        actual_refs.update(
            str((Path(directory) / name).relative_to(root)) for name in filenames
        )
    if actual_refs != set(data) or len(data) != publication.file_count:
        raise MillefeuilleContractError("GPT published file coverage drift")
    if sum(map(len, data.values())) != publication.total_bytes:
        raise MillefeuilleContractError("GPT published byte coverage drift")
    manifest = {
        "schema_version": "millefeuille-gpt-summary-publication-bundle/v0.1",
        "paper_id": publication.paper_id,
        "run_id": publication.run_id,
        "packet_digest": publication.packet_digest,
        "receipt_digest": publication.receipt_digest,
        "source_manifest_sha256": publication.source_manifest_sha256,
        "write_manifest_sha256": publication.write_manifest_sha256,
        "observed_usage_sha256": publication.observed_usage_sha256,
        "provenance_manifest_sha256": publication.provenance_manifest_sha256,
        "entries": [
            {"ref": ref, "sha256": _digest(encoded), "bytes": len(encoded)}
            for ref, encoded in sorted(data.items())
        ],
    }
    if _digest(_canonical(manifest)) != publication.bundle_manifest_sha256:
        raise MillefeuilleContractError("GPT published bundle identity drift")
    handoff = PublishedGptSummaryHandoff(
        publication,
        source_hash,
        source_pack_ref,
        summary_ref,
        tuple(text_refs),
        tuple(source_refs),
        tuple(provenance_refs),
    )

    return handoff, data


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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
    ).encode("utf-8")


def _object(encoded: bytes) -> dict[str, Any]:
    try:
        value = json.loads(encoded)
    except (ValueError, UnicodeError) as exc:
        raise MillefeuilleContractError("GPT published JSON is invalid") from exc
    if not isinstance(value, dict):
        raise MillefeuilleContractError("GPT published JSON must be an object")
    return value
