"""Deterministic no-call preparation for hierarchical summary execution."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import (
    SUMMARY_MODEL_STAGES,
    build_summary_execution_plan,
)
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE
from millefeuille.domain.route_fixtures import (
    RouteSelectionFixtureEvidence,
    load_route_selection_evidence_batch,
)
from millefeuille.domain.secure_io import (
    read_bytes_no_follow,
    write_new_text_no_follow,
)
from millefeuille.domain.source_packs import paper_id_for_zotero_item_key
from millefeuille.domain.structure_fixtures import (
    StructureFixtureEvidence,
    load_structure_evidence_batch,
)

SUMMARY_PREPARATION_SCHEMA_VERSION = "millefeuille-summary-preparation/v0.1"
SUMMARY_PREPARATION_BATCH_SCHEMA_VERSION = (
    "millefeuille-summary-preparation-batch/v0.1"
)


@dataclass(frozen=True)
class SummaryPreparationDocumentResult:
    paper_id: str
    status: str
    package_path: Path
    profile: str
    requested_stages: tuple[str, ...]
    work_unit_counts: dict[str, int]
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "status": self.status,
            "package_path": str(self.package_path),
            "profile": self.profile,
            "requested_stages": list(self.requested_stages),
            "work_unit_counts": self.work_unit_counts,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class SummaryPreparationBatchResult:
    status: str
    output_dir: Path
    summary_path: Path
    profile: str
    documents: tuple[SummaryPreparationDocumentResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_dir": str(self.output_dir),
            "summary_path": str(self.summary_path),
            "profile": self.profile,
            "documents": [document.to_dict() for document in self.documents],
        }


@dataclass(frozen=True)
class _PreparedSummaryDocument:
    paper_id: str
    package_path: Path
    package_bytes: bytes
    work_unit_counts: dict[str, int]
    blockers: tuple[str, ...]


def prepare_summary_execution_packages(
    *,
    route_evidence_paths: list[str | Path],
    structure_evidence_paths: list[str | Path],
    output_dir: str | Path,
    profile: str | None = None,
) -> SummaryPreparationBatchResult:
    """Bind verified local inputs to no-call model plans without summaries."""

    if not route_evidence_paths:
        raise MillefeuilleContractError("at least one route evidence path is required")
    if not structure_evidence_paths:
        raise MillefeuilleContractError(
            "at least one structure evidence path is required"
        )
    profile_name = profile or _default_profile()
    plans = {
        stage: build_summary_execution_plan(profile=profile_name, stage=stage)
        for stage in SUMMARY_MODEL_STAGES
    }

    routes: list[RouteSelectionFixtureEvidence] = []
    for path in route_evidence_paths:
        routes.extend(load_route_selection_evidence_batch(path))
    structures: list[StructureFixtureEvidence] = []
    for path in structure_evidence_paths:
        structures.extend(load_structure_evidence_batch(path))

    route_by_key = _index_records(routes, "route")
    structure_by_key = _index_records(structures, "structure")
    if set(route_by_key) != set(structure_by_key):
        missing_structure = sorted(set(route_by_key).difference(structure_by_key))
        missing_route = sorted(set(structure_by_key).difference(route_by_key))
        details: list[str] = []
        if missing_structure:
            details.append(
                "missing structure for "
                + ", ".join(":".join(key) for key in missing_structure)
            )
        if missing_route:
            details.append(
                "missing route for "
                + ", ".join(":".join(key) for key in missing_route)
            )
        raise MillefeuilleContractError(
            "summary preparation evidence join is incomplete: " + "; ".join(details)
        )
    _reject_duplicate_paper_ids(list(route_by_key.values()))

    output_root = Path(output_dir)
    prepared = [
        _prepare_document(
            route=route_by_key[key],
            structure=structure_by_key[key],
            output_root=output_root,
            profile=profile_name,
            plans=plans,
        )
        for key in sorted(route_by_key)
    ]
    summary_path = output_root / "summary.json"
    summary_bytes = _canonical_json_bytes(
        {
            "schema_version": SUMMARY_PREPARATION_BATCH_SCHEMA_VERSION,
            "status": "prepared-no-call",
            "profile": profile_name,
            "output_dir": str(output_root),
            "documents": [
                {
                    "paper_id": document.paper_id,
                    "package_path": document.package_path.name,
                    "work_unit_counts": document.work_unit_counts,
                    "blockers": list(document.blockers),
                }
                for document in prepared
            ],
            "totals": {
                "documents": len(prepared),
                "requested_stages": len(prepared) * len(SUMMARY_MODEL_STAGES),
                "work_units": sum(
                    sum(document.work_unit_counts.values())
                    for document in prepared
                ),
                "summary_outputs_generated": 0,
            },
            "provider_calls": 0,
            "source_pack_writes": 0,
            "zotero_writes": 0,
        }
    )

    statuses: dict[str, str] = {}
    for document in prepared:
        statuses[document.paper_id] = _preflight_output(
            document.package_path,
            document.package_bytes,
        )
    summary_status = _preflight_output(summary_path, summary_bytes)

    for document in prepared:
        _write_new_output(document.package_path, document.package_bytes)
    _write_new_output(summary_path, summary_bytes)

    documents = tuple(
        SummaryPreparationDocumentResult(
            paper_id=document.paper_id,
            status=statuses[document.paper_id],
            package_path=document.package_path,
            profile=profile_name,
            requested_stages=tuple(SUMMARY_MODEL_STAGES),
            work_unit_counts=document.work_unit_counts,
            blockers=document.blockers,
        )
        for document in prepared
    )
    batch_status = (
        "existing"
        if summary_status == "existing"
        and all(document.status == "existing" for document in documents)
        else "created"
    )
    return SummaryPreparationBatchResult(
        status=batch_status,
        output_dir=output_root,
        summary_path=summary_path,
        profile=profile_name,
        documents=documents,
    )


def _prepare_document(
    *,
    route: RouteSelectionFixtureEvidence,
    structure: StructureFixtureEvidence,
    output_root: Path,
    profile: str,
    plans: dict[str, dict[str, Any]],
) -> _PreparedSummaryDocument:
    _validate_identity_join(route, structure)
    paper_id = route.paper_id or paper_id_for_zotero_item_key(route.item_key)
    structure_paper_id = structure.paper_id or paper_id_for_zotero_item_key(
        structure.item_key
    )
    if paper_id != structure_paper_id:
        raise MillefeuilleContractError(
            f"summary preparation paper_id drift for {route.item_key}"
        )
    if not re.fullmatch(r"[A-Za-z0-9._-]+", paper_id):
        raise MillefeuilleContractError("paper_id is not safe for summary preparation")

    markdown_bytes = read_bytes_no_follow(
        route.markdown_path,
        "selected markdown for summary preparation",
    )
    structure_bytes = read_bytes_no_follow(
        structure.structure_path,
        "structure input for summary preparation",
    )
    structure_payload = _load_structure_payload(structure_bytes)
    _validate_structure_payload(structure, structure_payload)

    work_units = _build_work_units(structure_payload)
    blockers = sorted(
        {
            blocker
            for plan in plans.values()
            for blocker in plan["execution"]["blockers"]
        }
        | {
            "summary outputs are not generated by this preparation command",
        }
    )
    package = {
        "schema_version": SUMMARY_PREPARATION_SCHEMA_VERSION,
        "status": "prepared-no-call",
        "paper_id": paper_id,
        "identity": {
            "source_type": route.source_type,
            "item_key": route.item_key,
            "attachment_key": route.attachment_key,
            "canonical_filename": route.canonical_filename,
            "expected_sha256": route.expected_sha256,
            "page_count": route.page_count,
            "selected_route": route.selected_route.value,
            "zotero_version": route.zotero_version,
        },
        "inputs": {
            "selected_markdown": {
                "path": str(route.markdown_path),
                "sha256": hashlib.sha256(markdown_bytes).hexdigest(),
                "bytes": len(markdown_bytes),
            },
            "structure": {
                "path": str(structure.structure_path),
                "sha256": hashlib.sha256(structure_bytes).hexdigest(),
                "bytes": len(structure_bytes),
                "backend": structure.structure_backend,
                "coverage_source": structure.coverage_source,
            },
        },
        "profile": profile,
        "execution_plans": plans,
        "work_units": work_units,
        "execution": {
            "provider_call_permitted": False,
            "provider_call_performed": False,
            "summary_outputs_generated": 0,
            "ready_for_approved_live_execution": False,
            "blockers": blockers,
        },
        "writes": {
            "source_pack": False,
            "zotero": False,
        },
    }
    package_path = output_root / f"{paper_id}.summary-preparation.json"
    return _PreparedSummaryDocument(
        paper_id=paper_id,
        package_path=package_path,
        package_bytes=_canonical_json_bytes(package),
        work_unit_counts={
            stage: len(units) for stage, units in work_units.items()
        },
        blockers=tuple(blockers),
    )


def _validate_identity_join(
    route: RouteSelectionFixtureEvidence,
    structure: StructureFixtureEvidence,
) -> None:
    fields = (
        "source_type",
        "item_key",
        "attachment_key",
        "canonical_filename",
        "expected_sha256",
        "page_count",
        "selected_route",
        "zotero_version",
    )
    for field_name in fields:
        if getattr(route, field_name) != getattr(structure, field_name):
            raise MillefeuilleContractError(
                f"summary preparation {field_name} drift for {route.item_key}"
            )


def _load_structure_payload(payload_bytes: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise MillefeuilleContractError(
            "structure input for summary preparation is not valid UTF-8"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            "structure input for summary preparation is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError(
            "structure input for summary preparation must be an object"
        )
    return payload


def _validate_structure_payload(
    evidence: StructureFixtureEvidence,
    payload: dict[str, Any],
) -> None:
    if payload.get("backend") != evidence.structure_backend:
        raise MillefeuilleContractError("summary preparation structure backend drift")
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict):
        raise MillefeuilleContractError(
            "summary preparation structure coverage must be an object"
        )
    if coverage.get("source") != evidence.coverage_source:
        raise MillefeuilleContractError(
            "summary preparation structure coverage source drift"
        )
    collection_counts = {
        "pages": evidence.page_count,
        "sections": evidence.sections,
        "tables": evidence.tables,
        "figures": evidence.figures,
        "references": evidence.references,
    }
    for field_name, expected_count in collection_counts.items():
        value = payload.get(field_name)
        if not isinstance(value, list) or len(value) != expected_count:
            raise MillefeuilleContractError(
                f"summary preparation structure {field_name} drift"
            )


def _build_work_units(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    pages = payload["pages"]
    sections = payload["sections"]
    page_units = [
        {
            "unit_id": f"page-{_required_positive_int(page.get('page'), 'page')}",
            "source_locators": [
                _required_locator(page.get("locator"), "page locator")
            ],
        }
        for page in pages
    ]
    section_units = [
        {
            "unit_id": "section-"
            + _required_safe_identifier(section.get("id"), "section id"),
            "source_locators": [
                _required_locator(section.get("locator"), "section locator")
            ],
        }
        for section in sections
    ]
    page_unit_ids = [unit["unit_id"] for unit in page_units]
    if len(page_unit_ids) != len(set(page_unit_ids)):
        raise MillefeuilleContractError(
            "summary preparation page unit ids must be unique"
        )
    page_locators = [unit["source_locators"][0] for unit in page_units]
    if len(page_locators) != len(set(page_locators)):
        raise MillefeuilleContractError(
            "summary preparation page locators must be unique"
        )
    section_unit_ids = [unit["unit_id"] for unit in section_units]
    if len(section_unit_ids) != len(set(section_unit_ids)):
        raise MillefeuilleContractError(
            "summary preparation section unit ids must be unique"
        )
    section_locators = [unit["source_locators"][0] for unit in section_units]
    if len(section_locators) != len(set(section_locators)):
        raise MillefeuilleContractError(
            "summary preparation section locators must be unique"
        )
    return {
        "summarize_page": page_units,
        "summarize_section": section_units,
        "summarize_full_paper": [
            {
                "unit_id": "full-paper",
                "source_locators": page_locators,
            }
        ],
    }


def _required_positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise MillefeuilleContractError(
            f"summary preparation {field_name} must be a positive integer"
        )
    return value


def _required_safe_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise MillefeuilleContractError(
            f"summary preparation {field_name} is not a safe identifier"
        )
    return value


def _required_locator(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MillefeuilleContractError(
            f"summary preparation {field_name} must be a non-empty string"
        )
    locator = value.strip()
    if len(locator) > 500 or any(character in locator for character in "\r\n\0"):
        raise MillefeuilleContractError(
            f"summary preparation {field_name} is unsafe"
        )
    return locator


def _index_records(
    records: list[RouteSelectionFixtureEvidence] | list[StructureFixtureEvidence],
    label: str,
) -> dict[tuple[str, str], Any]:
    indexed: dict[tuple[str, str], Any] = {}
    duplicates: list[str] = []
    for record in records:
        key = (record.item_key, record.attachment_key)
        if key in indexed:
            duplicates.append(":".join(key))
        indexed[key] = record
    if duplicates:
        raise MillefeuilleContractError(
            f"duplicate {label} evidence for summary preparation: "
            + ", ".join(sorted(set(duplicates)))
        )
    return indexed


def _reject_duplicate_paper_ids(
    records: list[RouteSelectionFixtureEvidence],
) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for record in records:
        paper_id = record.paper_id or paper_id_for_zotero_item_key(record.item_key)
        if paper_id in seen:
            duplicates.append(paper_id)
        seen.add(paper_id)
    if duplicates:
        raise MillefeuilleContractError(
            "duplicate paper_id for summary preparation: "
            + ", ".join(sorted(set(duplicates)))
        )


def _default_profile() -> str:
    value = DEFAULT_MODEL_PROFILE_BUNDLE.get("default_profile")
    if not isinstance(value, str) or not value:
        raise MillefeuilleContractError("model profile bundle has no default_profile")
    return value


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _preflight_output(path: Path, expected: bytes) -> str:
    try:
        target_stat = path.lstat()
    except FileNotFoundError:
        return "created"
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not inspect summary preparation output {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(target_stat.st_mode):
        raise MillefeuilleContractError(
            f"summary preparation output is not a file: {path}"
        )
    existing = read_bytes_no_follow(path, "summary preparation output")
    if existing != expected:
        raise MillefeuilleContractError(f"summary preparation output drift: {path}")
    return "existing"


def _write_new_output(path: Path, expected: bytes) -> None:
    if _preflight_output(path, expected) == "existing":
        return
    write_new_text_no_follow(
        path,
        expected.decode("utf-8"),
        "summary preparation output",
    )
