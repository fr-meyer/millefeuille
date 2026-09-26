"""No-write canonical card assembly from a validated transient GPT outcome."""

from dataclasses import dataclass, field
import hashlib
import html
import json
from pathlib import Path
import re

from millefeuille.domain.card_index_contract import canonical_json_bytes
from millefeuille.domain.millefeuille import (
    PAPER_CARD_SCHEMA_V2,
    MillefeuilleContractError,
    PaperCardRecord,
)
from millefeuille.domain.paper_card_live_execution import TrustedGptCardOutcome
from millefeuille.domain.paper_card_results import (
    validate_published_gpt_paper_card_execution,
)
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.source_packs import parse_source_pack_manifest
from millefeuille.domain.summary_preparation import verify_summary_preparation_package
from millefeuille.domain.summary_published_handoff import GptSummaryPublicationIdentity


@dataclass(frozen=True)
class PlannedGptCardFile:
    ref: str
    sha256: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class GptCardOutputPlan:
    paper_id: str
    run_id: str
    source_hash: str
    summary_publication_sha256: str
    request_plan_sha256: str
    provenance_sha256: str
    write_manifest_sha256: str
    write_manifest_ref: str
    total_bytes: int
    files: tuple[PlannedGptCardFile, ...] = field(repr=False)
    provider_calls_performed: int = 0
    writes_performed: int = 0


def plan_gpt_paper_card_outputs(
    *,
    outcome: TrustedGptCardOutcome,
    source_pack_root: str | Path,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    publication: GptSummaryPublicationIdentity,
) -> GptCardOutputPlan:
    """Revalidate source/result and derive five exact files only in memory.

    The output uses a distinct run, preserving the immutable summary bundle.
    This is a canonical card artifact plan, not a reconciled source-pack run,
    a write permit or whole-paper acceptance.
    """

    if not isinstance(outcome, TrustedGptCardOutcome):
        raise MillefeuilleContractError("GPT card output outcome is invalid")
    plan = outcome.plan
    approval = outcome.approval
    if (
        approval.paper_id != plan.paper_id
        or approval.run_id != plan.run_id
        or approval.manifest_sha256 != plan.manifest_sha256
        or approval.summary_publication_sha256 != plan.publication_manifest_sha256
        or approval.request_count != 1
        or plan.run_id == publication.run_id
    ):
        raise MillefeuilleContractError("GPT card output execution scope drift")
    validated = validate_published_gpt_paper_card_execution(
        plan=plan,
        execution=outcome.execution,
        source_pack_root=source_pack_root,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
        publication=publication,
    )
    if validated != outcome.validated:
        raise MillefeuilleContractError("GPT card validated result drift")
    root = Path(source_pack_root)
    prefix = f"analyses/millefeuille/{plan.run_id}"
    if (root / prefix).exists() or (root / prefix).is_symlink():
        raise MillefeuilleContractError("GPT card output run already exists")
    source_bytes = read_bytes_no_follow(
        root / f"zotero/{plan.paper_id}/manifest.json", "GPT card source identity"
    )
    source = parse_source_pack_manifest(json.loads(source_bytes))
    prepared = verify_summary_preparation_package(
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
    )
    expected = prepared["identity"]
    if (
        source["paper_id"] != plan.paper_id
        or source["source_hash"] != plan.source_hash
        or source["source_type"] != expected["source_type"]
        or source["identity"]["zotero_item_key"] != expected["item_key"]
        or source["identity"]["zotero_attachment_key"] != expected["attachment_key"]
        or source["identity"]["canonical_filename"] != expected["canonical_filename"]
    ):
        raise MillefeuilleContractError("GPT card source identity drift")
    filename = source["identity"]["canonical_filename"]
    title, year = _filename_identity(filename)
    identity = {
        "title": title,
        "source_hash": plan.source_hash,
        "zotero_item_key": source["identity"]["zotero_item_key"],
        "canonical_filename": filename,
    }
    if year is not None:
        identity["year"] = year
    content = validated.content.content
    warnings = list(content["quality_warnings"]) + [
        "Bibliographic identity is derived from the verified canonical filename; "
        "Zotero metadata reconciliation is pending."
    ]
    evidence = [
        f"analyses/millefeuille/{publication.run_id}/structure/inputs/selected.md#{locator}"
        for locator in validated.content.source_locators
    ] + [
        f"analyses/millefeuille/{publication.run_id}/summaries/hierarchical-summary.json"
    ]
    provenance_ref = "cards/model-provenance.json"
    card = PaperCardRecord(
        schema_version=PAPER_CARD_SCHEMA_V2,
        paper_id=plan.paper_id,
        run_id=plan.run_id,
        identity=identity,
        evidence_refs=evidence,
        model_provenance={
            "profile_id": "research-default",
            "provenance_ref": provenance_ref,
        },
        index_state={
            "phase": "planned",
            "lanes": [
                {"lane": "openkb", "status": "pending"},
                {"lane": "pageindex", "status": "pending"},
            ],
        },
        **{
            key: content[key]
            for key in (
                "one_line_thesis",
                "primary_contribution",
                "problem_addressed",
                "method_or_approach",
                "data_modality_domain",
                "main_results",
                "limitations",
                "classification_clues",
            )
        },
        quality_warnings=warnings,
    ).to_dict()
    PaperCardRecord.from_dict(card)
    artifacts = (
        _file(f"{prefix}/cards/paper-card.json", canonical_json_bytes(card)),
        _file(f"{prefix}/cards/paper-card.md", _markdown(card).encode("utf-8")),
        _file(f"{prefix}/{provenance_ref}", validated.provenance_json),
        _file(f"{prefix}/cards/request-plan.json", plan.manifest_json),
    )
    manifest_ref = f"{prefix}/cards/write-manifest.json"
    manifest = {
        "schema_version": "millefeuille-gpt-paper-card-write-manifest/v0.1",
        "paper_id": plan.paper_id,
        "run_id": plan.run_id,
        "source_hash": plan.source_hash,
        "source_pack_manifest_sha256": _digest(source_bytes),
        "summary_run_id": publication.run_id,
        "summary_publication_sha256": plan.publication_manifest_sha256,
        "request_plan_sha256": plan.manifest_sha256,
        "execution_receipt_digest": approval.receipt_digest,
        "provenance_sha256": validated.provenance_sha256,
        "write_file_count": 5,
        "entries": [
            {"ref": item.ref, "sha256": item.sha256, "bytes": len(item.data)}
            for item in sorted(artifacts, key=lambda item: item.ref)
        ],
        "write_authorized": False,
        "source_run_reconciliation_performed": False,
        "paper_acceptance_performed": False,
    }
    encoded = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    files = tuple(
        sorted((*artifacts, _file(manifest_ref, encoded)), key=lambda item: item.ref)
    )
    return GptCardOutputPlan(
        plan.paper_id,
        plan.run_id,
        plan.source_hash,
        plan.publication_manifest_sha256,
        plan.manifest_sha256,
        validated.provenance_sha256,
        _digest(encoded),
        manifest_ref,
        sum(len(item.data) for item in files),
        files,
    )


def _filename_identity(filename: str) -> tuple[str, int | None]:
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    parts = stem.split(" - ", 2)
    if len(parts) == 3 and re.fullmatch(r"[0-9]{4}", parts[1]):
        title, year = parts[2], int(parts[1])
    else:
        title, year = stem, None
    if not title or title != title.strip():
        raise MillefeuilleContractError("GPT card canonical filename title is invalid")
    return title, year


def _markdown(card: dict) -> str:
    lines = ["# " + html.escape(card["identity"]["title"]), ""]
    for key, label in (
        ("one_line_thesis", "Thesis"),
        ("primary_contribution", "Contribution"),
        ("problem_addressed", "Problem"),
        ("method_or_approach", "Method"),
        ("data_modality_domain", "Domain"),
        ("main_results", "Results"),
        ("limitations", "Limitations"),
    ):
        lines.extend(["## " + label, "", html.escape(card[key]), ""])
    for key, label in (
        ("classification_clues", "Classification clues"),
        ("quality_warnings", "Quality notes"),
    ):
        if card.get(key):
            lines.extend(["## " + label, ""])
            lines.extend("- " + html.escape(item) for item in card[key])
            lines.append("")
    lines.extend(["## Indexing", "", "OpenKB: pending. PageIndex: pending.", ""])
    return "\n".join(lines)


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _file(ref: str, data: bytes) -> PlannedGptCardFile:
    return PlannedGptCardFile(ref, _digest(data), data)
