"""Read-only retrieval over Millefeuille artifact packages."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any
import unicodedata

from millefeuille.domain.acceptance import ACCEPTANCE_SUMMARY_REF
from millefeuille.domain.card_fixtures import CARD_JSON_REF, load_paper_card
from millefeuille.domain.classification import (
    CLASSIFICATION_PLAN_REF,
    WRITEBACK_PREVIEW_REF,
)
from millefeuille.domain.index_fixtures import (
    INDEX_STATUS_REF,
    load_retrieval_index_status,
)
from millefeuille.domain.millefeuille import (
    AcceptanceSummaryRecord,
    ClassificationPlanRecord,
    MillefeuilleContractError,
)
from millefeuille.domain.route_fixtures import ROUTE_MARKDOWN_REF
from millefeuille.domain.stage_runtime import (
    ResolvedRunArtifacts,
    load_json_object,
    relative_ref,
    require_safe_package_id,
    resolve_run_artifacts,
)
from millefeuille.domain.summary_fixtures import (
    SUMMARY_ARTIFACT_REF,
    load_hierarchical_summary,
)

_PAGE_LOCATOR = re.compile(
    r"(?:p(?:age)?s?\.?|pages?)\s*:?\s*(\d+)"
    r"(?:\s*[-\u2013]\s*(\d+))?\Z",
    re.IGNORECASE,
)
_SECTION_LOCATOR = re.compile(r"(?:section|sec)\s*:\s*(.+)\Z", re.IGNORECASE)
_DOI_PREFIX = re.compile(
    r"\A(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)",
    re.IGNORECASE,
)


def retrieve_artifact_refs(
    *,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None = None,
    item_key: str | None = None,
    slug: str | None = None,
    doi: str | None = None,
    title: str | None = None,
    summary_scope: str | None = None,
    grain: str | None = None,
    index_lane: str | None = None,
    section: str | None = None,
    page: int | None = None,
    evidence_need: str | None = None,
) -> dict[str, Any]:
    resolved, locator_type = _resolve_retrieve_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        slug=slug,
        doi=doi,
        title=title,
    )
    if page is not None and (
        not isinstance(page, int) or isinstance(page, bool) or page < 1
    ):
        raise MillefeuilleContractError("page must be a positive integer")
    normalized_section = (
        _normalize_text(section, "section") if section is not None else None
    )
    if evidence_need not in (None, "classification"):
        raise MillefeuilleContractError(
            "evidence_need must be 'classification' when supplied"
        )
    if evidence_need == "classification":
        if summary_scope not in (None, "classification"):
            raise MillefeuilleContractError(
                "classification evidence cannot be combined with a different "
                "summary_scope"
            )
        summary_scope = "classification"

    _load_verified_paper_card(resolved)
    summary_path = resolved.run_dir / SUMMARY_ARTIFACT_REF
    _require_regular_artifact(summary_path, "hierarchical summary")
    summary_payload = load_hierarchical_summary(summary_path)
    _require_identity(
        payload=summary_payload,
        label="hierarchical summary",
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=None,
    )
    summaries = [
        dict(entry)
        for entry in summary_payload["summaries"]
        if (summary_scope is None or entry["scope"] == summary_scope)
        and (grain is None or entry["grain"] == grain)
        and _matches_location_filters(
            entry,
            section=normalized_section,
            page=page,
        )
    ]
    index_path = resolved.run_dir / INDEX_STATUS_REF
    _require_regular_artifact(index_path, "retrieval index status")
    index_payload = load_retrieval_index_status(index_path)
    _require_identity(
        payload=index_payload,
        label="retrieval index status",
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
    )
    _validate_canonical_index_refs(resolved, index_payload)
    lanes = [
        dict(entry)
        for entry in index_payload["lanes"]
        if index_lane is None or entry["lane"] == index_lane
    ]

    result: dict[str, Any] = {
        "paper_id": resolved.paper_id,
        "run_id": resolved.run_id,
        "locator_type": locator_type,
        "source_hash": resolved.source_hash,
        "source_pack_ref": relative_ref(resolved.source_pack_dir, resolved.run_dir),
        "selected_fulltext_ref": relative_ref(
            resolved.source_pack_dir / ROUTE_MARKDOWN_REF,
            resolved.run_dir,
        ),
        "summary_ref": relative_ref(
            resolved.run_dir / SUMMARY_ARTIFACT_REF,
            resolved.run_dir,
        ),
        "summary_entries": summaries,
        "summary_match_count": len(summaries),
        "paper_card_ref": relative_ref(
            resolved.run_dir / CARD_JSON_REF,
            resolved.run_dir,
        ),
        "index_lanes": lanes,
    }
    filters = {
        key: value
        for key, value in {
            "summary_scope": summary_scope,
            "grain": grain,
            "index_lane": index_lane,
            "section": section.strip() if section is not None else None,
            "page": page,
            "evidence_need": evidence_need,
        }.items()
        if value is not None
    }
    if filters:
        result["filters"] = filters
    acceptance_path = resolved.run_dir / ACCEPTANCE_SUMMARY_REF
    if acceptance_path.is_file():
        _require_regular_artifact(acceptance_path, "acceptance summary")
        acceptance_payload = AcceptanceSummaryRecord.from_dict(
            load_json_object(acceptance_path, "acceptance summary")
        ).to_dict()
        _require_identity(
            payload=acceptance_payload,
            label="acceptance summary",
            paper_id=resolved.paper_id,
            run_id=resolved.run_id,
            source_hash=resolved.source_hash,
        )
        result["acceptance_summary_ref"] = relative_ref(
            acceptance_path, resolved.run_dir
        )
        result["acceptance_status"] = acceptance_payload["status"]
    classification_path = resolved.run_dir / CLASSIFICATION_PLAN_REF
    if classification_path.is_file():
        _require_regular_artifact(classification_path, "classification plan")
        classification_payload = ClassificationPlanRecord.from_dict(
            load_json_object(classification_path, "classification plan")
        ).to_dict()
        if classification_payload["run_id"] != resolved.run_id:
            raise MillefeuilleContractError(
                "classification plan run_id drift: "
                f"expected {resolved.run_id!r}, got "
                f"{classification_payload['run_id']!r}"
            )
        plan_paper_ids = {
            paper.get("paper_id") for paper in classification_payload["papers"]
        }
        if plan_paper_ids != {resolved.paper_id}:
            raise MillefeuilleContractError(
                "classification plan paper_id drift: "
                f"expected only {resolved.paper_id!r}, got "
                f"{sorted(str(value) for value in plan_paper_ids)!r}"
            )
        result["classification_plan_ref"] = relative_ref(
            classification_path,
            resolved.run_dir,
        )
    writeback_preview_path = resolved.run_dir / WRITEBACK_PREVIEW_REF
    if writeback_preview_path.is_file():
        _require_regular_artifact(writeback_preview_path, "writeback preview")
        preview_payload = load_json_object(
            writeback_preview_path,
            "writeback preview",
        )
        if (
            preview_payload.get("schema_version")
            != "millefeuille-zotero-writeback-preview/v0.1"
        ):
            raise MillefeuilleContractError(
                "unsupported writeback preview schema_version "
                f"{preview_payload.get('schema_version')!r}"
            )
        _require_identity(
            payload=preview_payload,
            label="writeback preview",
            paper_id=resolved.paper_id,
            run_id=resolved.run_id,
            source_hash=resolved.source_hash,
        )
        result["writeback_preview_ref"] = relative_ref(
            writeback_preview_path,
            resolved.run_dir,
        )
    return result


def _resolve_retrieve_artifacts(
    *,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None,
    item_key: str | None,
    slug: str | None,
    doi: str | None,
    title: str | None,
) -> tuple[ResolvedRunArtifacts, str]:
    locators = {
        "paper_id": paper_id,
        "item_key": item_key,
        "slug": slug,
        "doi": doi,
        "title": title,
    }
    supplied = [(name, value) for name, value in locators.items() if value is not None]
    if len(supplied) != 1:
        raise MillefeuilleContractError(
            "exactly one of paper_id, item_key, slug, doi, or title is required"
        )
    locator_type, locator_value = supplied[0]
    if locator_type in {"paper_id", "item_key", "slug"}:
        resolved = resolve_run_artifacts(
            source_pack_root=source_pack_root,
            run_id=run_id,
            paper_id=locator_value if locator_type in {"paper_id", "slug"} else None,
            item_key=locator_value if locator_type == "item_key" else None,
        )
        return resolved, locator_type

    normalized_query = (
        _normalize_doi(locator_value)
        if locator_type == "doi"
        else _normalize_text(locator_value, "title")
    )
    root = Path(source_pack_root)
    zotero_root = root / "zotero"
    if not zotero_root.is_dir():
        raise MillefeuilleContractError(
            f"source-pack corpus not found: {zotero_root}"
        )

    resolved_run_id = str(run_id).strip()
    require_safe_package_id(resolved_run_id, "run_id")
    matches: list[ResolvedRunArtifacts] = []
    for candidate in sorted(zotero_root.iterdir(), key=lambda path: path.name):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        card_path = (
            candidate
            / "analyses"
            / "millefeuille"
            / resolved_run_id
            / CARD_JSON_REF
        )
        if card_path.is_symlink():
            raise MillefeuilleContractError("paper card must not be a symbolic link")
        if not card_path.is_file():
            continue
        resolved = resolve_run_artifacts(
            source_pack_root=root,
            run_id=run_id,
            paper_id=candidate.name,
        )
        card_payload = _load_verified_paper_card(resolved)
        identity = card_payload["identity"]
        candidate_value = identity.get(locator_type)
        if not isinstance(candidate_value, str):
            continue
        normalized_candidate = (
            _normalize_doi(candidate_value)
            if locator_type == "doi"
            else _normalize_text(candidate_value, "paper card title")
        )
        if normalized_candidate == normalized_query:
            matches.append(resolved)

    if not matches:
        raise MillefeuilleContractError(
            f"no artifact package matched {locator_type} for run_id {run_id!r}"
        )
    if len(matches) > 1:
        paper_ids = ", ".join(sorted(match.paper_id for match in matches))
        raise MillefeuilleContractError(
            f"ambiguous {locator_type} matched multiple artifact packages: "
            f"{paper_ids}"
        )
    return matches[0], locator_type


def _load_verified_paper_card(
    resolved: ResolvedRunArtifacts,
) -> dict[str, Any]:
    card_path = resolved.run_dir / CARD_JSON_REF
    _require_regular_artifact(card_path, "paper card")
    card_payload = load_paper_card(card_path)
    _require_identity(
        payload=card_payload,
        label="paper card",
        paper_id=resolved.paper_id,
        run_id=None,
        source_hash=None,
    )
    return card_payload


def _validate_canonical_index_refs(
    resolved: ResolvedRunArtifacts,
    index_payload: dict[str, Any],
) -> None:
    index_dir = resolved.run_dir / INDEX_STATUS_REF.parent
    expected_paths = {
        "selected_fulltext_ref": resolved.source_pack_dir / ROUTE_MARKDOWN_REF,
        "summary_ref": resolved.run_dir / SUMMARY_ARTIFACT_REF,
        "paper_card_ref": resolved.run_dir / CARD_JSON_REF,
    }
    for field_name, expected_path in expected_paths.items():
        expected_ref = relative_ref(expected_path, index_dir)
        actual_ref = index_payload.get(field_name)
        if actual_ref != expected_ref:
            raise MillefeuilleContractError(
                f"retrieval index {field_name} drift: "
                f"expected {expected_ref!r}, got {actual_ref!r}"
            )
        _require_regular_artifact(expected_path, field_name)


def _require_regular_artifact(path: Path, label: str) -> None:
    if path.is_symlink():
        raise MillefeuilleContractError(f"{label} must not be a symbolic link")
    if not path.is_file():
        raise MillefeuilleContractError(f"{label} not found")


def _normalize_doi(value: str) -> str:
    normalized = _DOI_PREFIX.sub("", _normalize_text(value, "doi"), count=1)
    normalized = normalized.rstrip(".")
    if not normalized:
        raise MillefeuilleContractError("doi must not be empty")
    return normalized.casefold()


def _normalize_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise MillefeuilleContractError(f"{field_name} must be a string")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        raise MillefeuilleContractError(f"{field_name} must not be empty")
    return normalized.casefold()


def _matches_location_filters(
    entry: dict[str, Any],
    *,
    section: str | None,
    page: int | None,
) -> bool:
    locators = entry.get("source_locators", [])
    if section is not None and not any(
        _matches_section_locator(locator, section) for locator in locators
    ):
        return False
    return page is None or any(
        _matches_page_locator(locator, page) for locator in locators
    )


def _matches_section_locator(locator: str, section: str) -> bool:
    match = _SECTION_LOCATOR.fullmatch(locator.strip())
    return bool(match and _normalize_text(match.group(1), "section locator") == section)


def _matches_page_locator(locator: str, page: int) -> bool:
    match = _PAGE_LOCATOR.fullmatch(locator.strip())
    if match is None:
        return False
    start = int(match.group(1))
    end = int(match.group(2) or start)
    return start <= page <= end


def _require_identity(
    *,
    payload: dict[str, Any],
    label: str,
    paper_id: str,
    run_id: str | None,
    source_hash: str | None,
) -> None:
    expected_identity = {"paper_id": paper_id}
    if run_id is not None:
        expected_identity["run_id"] = run_id
    if source_hash is not None:
        expected_identity["source_hash"] = source_hash
    for field_name, expected in expected_identity.items():
        actual = payload.get(field_name)
        if actual != expected:
            raise MillefeuilleContractError(
                f"{label} {field_name} drift: expected {expected!r}, got {actual!r}"
            )
