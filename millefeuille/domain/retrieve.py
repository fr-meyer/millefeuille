"""Read-only retrieval over Millefeuille artifact packages."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
from typing import Any
import unicodedata

from millefeuille.domain.acceptance import ACCEPTANCE_SUMMARY_REF
from millefeuille.domain.card_fixtures import CARD_JSON_REF
from millefeuille.domain.classification import (
    CLASSIFICATION_PLAN_REF,
    WRITEBACK_PREVIEW_REF,
)
from millefeuille.domain.index_fixtures import INDEX_STATUS_REF
from millefeuille.domain.millefeuille import (
    INDEX_LANE_VALUES,
    AcceptanceSummaryRecord,
    ClassificationPlanRecord,
    HierarchicalSummaryRecord,
    MillefeuilleContractError,
    PaperCardRecord,
    RetrievalIndexRecord,
    SummaryGrain,
    SummaryScope,
)
from millefeuille.domain.route_fixtures import ROUTE_MARKDOWN_REF
from millefeuille.domain.secure_io import RootArtifactReader, read_bytes_no_follow
from millefeuille.domain.stage_runtime import (
    ResolvedRunArtifacts,
    ensure_no_follow_directory,
    ensure_no_follow_regular_file,
    load_json_object,
    probe_no_follow_regular_file,
    relative_ref,
    require_safe_package_id,
    resolve_run_artifacts,
)
from millefeuille.domain.summary_fixtures import SUMMARY_ARTIFACT_REF

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
_SAFE_BATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_BATCH_LOCATOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_REF_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")

RETRIEVAL_BATCH_MANIFEST_SCHEMA = "millefeuille-retrieval-batch-manifest/v0.1"
RETRIEVAL_BATCH_RESULT_SCHEMA = "millefeuille-retrieval-batch-result/v0.1"
RETRIEVAL_BATCH_ROOT_REF = Path("batches/millefeuille")
RETRIEVAL_BATCH_RESULT_REF = Path("retrieval/batch-retrieval-result.json")
RETRIEVAL_BATCH_REPORT_REF = Path("retrieval/batch-retrieval-report.md")
_RETRIEVAL_BATCH_TEMP_PREFIX = ".retrieval.tmp-"


@dataclass(frozen=True)
class _PreparedRetrieval:
    resolved: ResolvedRunArtifacts
    payload: dict[str, Any]


@dataclass(frozen=True)
class _StagedFileDescriptors:
    read_fd: int
    scrub_fd: int


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
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
) -> dict[str, Any]:
    """Return ref-only metadata for one verified local artifact package."""

    return _prepare_retrieval(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        slug=slug,
        doi=doi,
        title=title,
        summary_scope=summary_scope,
        grain=grain,
        index_lane=index_lane,
        section=section,
        page=page,
        evidence_need=evidence_need,
        strict_filters=False,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
    ).payload


def write_retrieval_batch_result(
    *,
    source_pack_root: str | Path,
    batch_manifest_path: str | Path,
    summary_scope: str | None = None,
    grain: str | None = None,
    index_lane: str | None = None,
    section: str | None = None,
    page: int | None = None,
    evidence_need: str | None = None,
) -> dict[str, Any]:
    """Preflight and materialize deterministic ref-only batch retrieval output."""

    batch_manifest_target = Path(batch_manifest_path)
    batch_id, locators, batch_manifest_bytes = _load_retrieval_batch_manifest(
        batch_manifest_target
    )
    # Keep argument validation portable and deterministic even when this host
    # cannot provide the stronger filesystem primitives required to publish.
    _normalize_retrieval_filters(
        summary_scope=summary_scope,
        grain=grain,
        index_lane=index_lane,
        section=section,
        page=page,
        evidence_need=evidence_need,
        strict_choices=True,
    )
    root = Path(source_pack_root).absolute()
    with RootArtifactReader(root) as artifact_reader:
        source_root_identity = artifact_reader.root_identity
        prepared = [
            _prepare_retrieval(
                source_pack_root=root,
                run_id=locator["run_id"],
                paper_id=locator.get("paper_id"),
                item_key=locator.get("item_key"),
                slug=locator.get("slug"),
                doi=locator.get("doi"),
                title=locator.get("title"),
                summary_scope=summary_scope,
                grain=grain,
                index_lane=index_lane,
                section=section,
                page=page,
                evidence_need=evidence_need,
                strict_filters=True,
                artifact_reader=artifact_reader,
            )
            for locator in locators
        ]
        prepared.sort(key=lambda item: (item.resolved.paper_id, item.resolved.run_id))
        identities = [
            (item.resolved.paper_id, item.resolved.run_id) for item in prepared
        ]
        if len(identities) != len(set(identities)):
            raise MillefeuilleContractError(
                "retrieval batch runs must have unique paper_id/run_id pairs"
            )

        runs = [_portable_batch_run(item, root=root) for item in prepared]
        filters = dict(prepared[0].payload.get("filters", {}))
        counts = {
            "runs": len(runs),
            "summary_matches": sum(run["summary_match_count"] for run in runs),
            "index_lane_matches": sum(len(run["index_lanes"]) for run in runs),
            "acceptance_pass": sum(
                run.get("acceptance_status") == "pass" for run in runs
            ),
            "acceptance_needs_review": sum(
                run.get("acceptance_status") == "needs-review" for run in runs
            ),
            "acceptance_unavailable": sum(
                "acceptance_status" not in run for run in runs
            ),
            "classification_plans": sum(
                "classification_plan_ref" in run for run in runs
            ),
            "writeback_previews": sum(
                "writeback_preview_ref" in run for run in runs
            ),
        }
        result: dict[str, Any] = {
            "schema_version": RETRIEVAL_BATCH_RESULT_SCHEMA,
            "batch_id": batch_id,
            "status": "retrieved",
            "counts": counts,
            "runs": runs,
        }
        if filters:
            result["filters"] = filters

        batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / batch_id
        report = _render_retrieval_batch_markdown(result)
        result_bytes = _serialize_result_json(result)
        report_bytes = report.encode("utf-8")

        def _revalidate_batch_inputs() -> None:
            artifact_reader.revalidate_snapshot()
            current_manifest_bytes = read_bytes_no_follow(
                batch_manifest_target,
                "retrieval batch manifest",
            )
            if current_manifest_bytes != batch_manifest_bytes:
                raise MillefeuilleContractError(
                    "retrieval batch manifest changed after batch preflight: "
                    f"{batch_manifest_target}"
                )

        _publish_retrieval_batch_outputs(
            root=root,
            expected_root_identity=source_root_identity,
            batch_dir=batch_dir,
            result_bytes=result_bytes,
            report_bytes=report_bytes,
            precommit_validator=_revalidate_batch_inputs,
        )
        return result


def _prepare_retrieval(
    *,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None,
    item_key: str | None,
    slug: str | None,
    doi: str | None,
    title: str | None,
    summary_scope: str | None,
    grain: str | None,
    index_lane: str | None,
    section: str | None,
    page: int | None,
    evidence_need: str | None,
    strict_filters: bool,
    artifact_reader: RootArtifactReader | None = None,
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
) -> _PreparedRetrieval:
    filters, normalized_section = _normalize_retrieval_filters(
        summary_scope=summary_scope,
        grain=grain,
        index_lane=index_lane,
        section=section,
        page=page,
        evidence_need=evidence_need,
        strict_choices=strict_filters,
    )
    summary_scope = filters.get("summary_scope")
    grain = filters.get("grain")
    index_lane = filters.get("index_lane")
    page = filters.get("page")

    resolved, locator_type = _resolve_retrieve_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        slug=slug,
        doi=doi,
        title=title,
        artifact_reader=artifact_reader,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
    )

    _validate_resolved_package_paths(resolved, artifact_reader=artifact_reader)
    _load_verified_paper_card(resolved, artifact_reader=artifact_reader)
    summary_path = resolved.run_dir / SUMMARY_ARTIFACT_REF
    summary_payload = HierarchicalSummaryRecord.from_dict(
        _load_retrieval_json(
            summary_path,
            "hierarchical summary",
            artifact_reader=artifact_reader,
        )
    ).to_dict()
    _require_identity(
        payload=summary_payload,
        label="hierarchical summary",
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=None,
    )
    for entry_index, entry in enumerate(summary_payload["summaries"], start=1):
        _validate_summary_entry_refs(
            resolved,
            entry,
            entry_index=entry_index,
            artifact_reader=artifact_reader,
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
    if strict_filters:
        summaries.sort(
            key=lambda entry: (
                entry["scope"],
                entry["grain"],
                entry["text_ref"],
            )
        )
    index_path = resolved.run_dir / INDEX_STATUS_REF
    index_payload = RetrievalIndexRecord.from_dict(
        _load_retrieval_json(
            index_path,
            "retrieval index status",
            artifact_reader=artifact_reader,
        )
    ).to_dict()
    _require_identity(
        payload=index_payload,
        label="retrieval index status",
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
    )
    _validate_canonical_index_refs(
        resolved,
        index_payload,
        artifact_reader=artifact_reader,
    )
    lanes = [
        dict(entry)
        for entry in index_payload["lanes"]
        if index_lane is None or entry["lane"] == index_lane
    ]
    if strict_filters:
        lanes.sort(key=lambda entry: (entry["lane"], entry["status"]))

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
    if filters:
        result["filters"] = filters
    acceptance_path = resolved.run_dir / ACCEPTANCE_SUMMARY_REF
    if _probe_retrieval_file(
        acceptance_path,
        "acceptance summary",
        artifact_reader=artifact_reader,
    ):
        acceptance_payload = AcceptanceSummaryRecord.from_dict(
            _load_retrieval_json(
                acceptance_path,
                "acceptance summary",
                artifact_reader=artifact_reader,
            )
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
    if _probe_retrieval_file(
        classification_path,
        "classification plan",
        artifact_reader=artifact_reader,
    ):
        classification_payload = ClassificationPlanRecord.from_dict(
            _load_retrieval_json(
                classification_path,
                "classification plan",
                artifact_reader=artifact_reader,
            )
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
    if _probe_retrieval_file(
        writeback_preview_path,
        "writeback preview",
        artifact_reader=artifact_reader,
    ):
        preview_payload = _load_retrieval_json(
            writeback_preview_path,
            "writeback preview",
            artifact_reader=artifact_reader,
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
    return _PreparedRetrieval(resolved=resolved, payload=result)


def _load_retrieval_json(
    path: Path,
    label: str,
    *,
    artifact_reader: RootArtifactReader | None,
) -> dict[str, Any]:
    if artifact_reader is not None:
        return artifact_reader.load_json_object(path, label)
    _require_regular_artifact(path, label)
    return load_json_object(path, label)


def _probe_retrieval_file(
    path: Path,
    label: str,
    *,
    artifact_reader: RootArtifactReader | None,
) -> bool:
    if artifact_reader is not None:
        return artifact_reader.probe_regular_file(path, label)
    return probe_no_follow_regular_file(path, label)


def _verify_retrieval_file(
    path: Path,
    label: str,
    *,
    artifact_reader: RootArtifactReader | None,
) -> None:
    if artifact_reader is not None:
        artifact_reader.verify_regular_file(path, label)
    else:
        _require_regular_artifact(path, label)


def _normalize_retrieval_filters(
    *,
    summary_scope: str | None,
    grain: str | None,
    index_lane: str | None,
    section: str | None,
    page: int | None,
    evidence_need: str | None,
    strict_choices: bool,
) -> tuple[dict[str, Any], str | None]:
    if strict_choices:
        normalized_scope = _normalize_optional_choice(
            summary_scope,
            "summary_scope",
            SummaryScope.values(),
        )
        normalized_grain = _normalize_optional_choice(
            grain,
            "grain",
            SummaryGrain.values(),
        )
        normalized_lane = _normalize_optional_choice(
            index_lane,
            "index_lane",
            set(INDEX_LANE_VALUES),
        )
    else:
        normalized_scope = summary_scope
        normalized_grain = grain
        normalized_lane = index_lane
    if page is not None and (
        not isinstance(page, int) or isinstance(page, bool) or page < 1
    ):
        raise MillefeuilleContractError("page must be a positive integer")
    normalized_section = (
        _normalize_text(section, "section") if section is not None else None
    )
    if strict_choices:
        display_section = (
            " ".join(unicodedata.normalize("NFKC", section).split())
            if section is not None
            else None
        )
        if evidence_need is not None and not isinstance(evidence_need, str):
            raise MillefeuilleContractError("evidence_need must be a string")
        normalized_evidence_need = (
            evidence_need.strip() if evidence_need is not None else None
        )
    else:
        display_section = section.strip() if section is not None else None
        normalized_evidence_need = evidence_need
    if normalized_evidence_need not in (None, "classification"):
        raise MillefeuilleContractError(
            "evidence_need must be 'classification' when supplied"
        )
    if normalized_evidence_need == "classification":
        if normalized_scope not in (None, "classification"):
            raise MillefeuilleContractError(
                "classification evidence cannot be combined with a different "
                "summary_scope"
            )
        normalized_scope = "classification"

    filters = {
        key: value
        for key, value in {
            "summary_scope": normalized_scope,
            "grain": normalized_grain,
            "index_lane": normalized_lane,
            "section": display_section,
            "page": page,
            "evidence_need": normalized_evidence_need,
        }.items()
        if value is not None
    }
    return filters, normalized_section


def _normalize_optional_choice(
    value: str | None,
    field_name: str,
    allowed: set[str],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MillefeuilleContractError(f"{field_name} must be a string")
    normalized = value.strip()
    if normalized not in allowed:
        raise MillefeuilleContractError(
            f"{field_name} must be one of {sorted(allowed)!r}"
        )
    return normalized


def _load_retrieval_batch_manifest(
    path: str | Path,
) -> tuple[str, list[dict[str, str]], bytes]:
    target = Path(path)
    manifest_bytes = read_bytes_no_follow(target, "retrieval batch manifest")
    try:
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise MillefeuilleContractError(
            f"retrieval batch manifest is not valid UTF-8: {target}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"retrieval batch manifest is not valid JSON: {target}"
        ) from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError("retrieval batch manifest must be an object")
    allowed_fields = {"schema_version", "batch_id", "runs"}
    unexpected = sorted(set(payload) - allowed_fields)
    if unexpected:
        raise MillefeuilleContractError(
            "retrieval batch manifest has unsupported fields: " + ", ".join(unexpected)
        )
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str):
        raise MillefeuilleContractError(
            "retrieval batch schema_version must be a string"
        )
    if schema_version != RETRIEVAL_BATCH_MANIFEST_SCHEMA:
        raise MillefeuilleContractError(
            f"unsupported retrieval batch schema_version {schema_version!r}"
        )
    batch_id = payload.get("batch_id")
    if not isinstance(batch_id, str):
        raise MillefeuilleContractError("retrieval batch batch_id must be a string")
    if batch_id in {".", ".."} or _SAFE_BATCH_ID.fullmatch(batch_id) is None:
        raise MillefeuilleContractError(
            "batch_id must be traversal-safe and use only letters, numbers, "
            "'.', '_', or '-'"
        )
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise MillefeuilleContractError(
            "retrieval batch runs must be a non-empty array"
        )

    locator_fields = {"paper_id", "item_key", "slug", "doi", "title"}
    locators: list[dict[str, str]] = []
    for index, entry in enumerate(runs):
        if not isinstance(entry, dict):
            raise MillefeuilleContractError(
                f"retrieval batch run {index} must be an object"
            )
        unexpected = sorted(set(entry) - (locator_fields | {"run_id"}))
        if unexpected:
            raise MillefeuilleContractError(
                f"retrieval batch run {index} has unsupported fields: "
                + ", ".join(unexpected)
            )
        if "run_id" not in entry:
            raise MillefeuilleContractError(
                f"retrieval batch run {index} requires run_id"
            )
        supplied_locators = sorted(locator_fields & set(entry))
        if len(supplied_locators) != 1:
            raise MillefeuilleContractError(
                f"retrieval batch run {index} requires exactly one of "
                "paper_id, item_key, slug, doi, or title"
            )
        locator_field = supplied_locators[0]
        field_values = {
            "run_id": entry["run_id"],
            locator_field: entry[locator_field],
        }
        non_string_fields = sorted(
            field_name
            for field_name, value in field_values.items()
            if not isinstance(value, str)
        )
        if non_string_fields:
            raise MillefeuilleContractError(
                f"retrieval batch run {index} has non-string fields: "
                + ", ".join(non_string_fields)
            )
        run_id = field_values["run_id"]
        locator_value = field_values[locator_field]
        if run_id in {".", ".."} or _SAFE_BATCH_LOCATOR.fullmatch(run_id) is None:
            raise MillefeuilleContractError(
                f"retrieval batch run {index} has unsafe locator fields: run_id"
            )
        if locator_field in {"paper_id", "item_key", "slug"}:
            if (
                locator_value in {".", ".."}
                or _SAFE_BATCH_LOCATOR.fullmatch(locator_value) is None
            ):
                raise MillefeuilleContractError(
                    f"retrieval batch run {index} has unsafe locator fields: "
                    f"{locator_field}"
                )
        elif locator_field == "doi":
            if len(locator_value) > 2048:
                raise MillefeuilleContractError(
                    f"retrieval batch run {index} doi is too long"
                )
            _normalize_doi(locator_value)
        else:
            if len(locator_value) > 1024:
                raise MillefeuilleContractError(
                    f"retrieval batch run {index} title is too long"
                )
            _normalize_text(locator_value, f"retrieval batch run {index} title")
        locators.append({"run_id": run_id, locator_field: locator_value})
    return batch_id, locators, manifest_bytes


def _validate_resolved_package_paths(
    resolved: ResolvedRunArtifacts,
    *,
    artifact_reader: RootArtifactReader | None = None,
) -> None:
    root = resolved.source_pack_root
    paths = {
        "source-pack manifest": resolved.source_pack_dir / "manifest.json",
        "selected full text": resolved.source_pack_dir / ROUTE_MARKDOWN_REF,
        "stage manifest": resolved.stage_manifest_path,
        "artifact index": resolved.artifact_index_path,
        "paper card": resolved.run_dir / CARD_JSON_REF,
        "hierarchical summary": resolved.run_dir / SUMMARY_ARTIFACT_REF,
        "retrieval index status": resolved.run_dir / INDEX_STATUS_REF,
        "acceptance summary": resolved.run_dir / ACCEPTANCE_SUMMARY_REF,
        "classification plan": resolved.run_dir / CLASSIFICATION_PLAN_REF,
        "writeback preview": resolved.run_dir / WRITEBACK_PREVIEW_REF,
    }
    if artifact_reader is not None:
        for label in (
            "source-pack manifest",
            "selected full text",
            "stage manifest",
            "artifact index",
            "paper card",
            "hierarchical summary",
            "retrieval index status",
        ):
            artifact_reader.verify_regular_file(paths[label], label)
        return

    ensure_no_follow_directory(resolved.run_dir, "artifact run directory")
    source_labels = {"source-pack manifest", "selected full text"}
    for label, target in paths.items():
        boundary = root if label in source_labels else resolved.run_dir
        probe_no_follow_regular_file(
            target,
            label,
            root=boundary,
        )
    _require_regular_artifact(
        resolved.source_pack_dir / "manifest.json",
        "source-pack manifest",
        root=root,
    )
    _require_regular_artifact(
        resolved.stage_manifest_path,
        "stage manifest",
        root=resolved.run_dir,
    )
    _require_regular_artifact(
        resolved.artifact_index_path,
        "artifact index",
        root=resolved.run_dir,
    )


def _validate_summary_entry_refs(
    resolved: ResolvedRunArtifacts,
    entry: dict[str, Any],
    *,
    entry_index: int,
    artifact_reader: RootArtifactReader | None,
) -> None:
    summary_dir = resolved.run_dir / SUMMARY_ARTIFACT_REF.parent
    _resolve_safe_relative_artifact_ref(
        base_dir=summary_dir,
        ref=entry.get("text_ref"),
        label=f"summary entry {entry_index} text_ref",
        artifact_reader=artifact_reader,
    )
    if "model_provenance_ref" in entry:
        _resolve_safe_relative_artifact_ref(
            base_dir=summary_dir,
            ref=entry["model_provenance_ref"],
            label=f"summary entry {entry_index} model_provenance_ref",
            artifact_reader=artifact_reader,
        )


def _resolve_safe_relative_artifact_ref(
    *,
    base_dir: Path,
    ref: object,
    label: str,
    artifact_reader: RootArtifactReader | None = None,
    verify: bool = True,
) -> Path:
    if not isinstance(ref, str):
        raise MillefeuilleContractError(f"{label} must be a string")
    if not ref or ref != ref.strip() or "\\" in ref or "\x00" in ref:
        raise MillefeuilleContractError(
            f"{label} must be a portable traversal-safe relative ref"
        )
    pure_ref = PurePosixPath(ref)
    if (
        pure_ref.is_absolute()
        or ref != pure_ref.as_posix()
        or any(part in {"", ".", ".."} for part in pure_ref.parts)
    ):
        raise MillefeuilleContractError(
            f"{label} must be a portable traversal-safe relative ref"
        )
    target = base_dir.joinpath(*pure_ref.parts)
    if verify:
        if artifact_reader is not None:
            artifact_reader.verify_regular_file(target, label)
        else:
            _require_regular_artifact(target, label, root=base_dir)
    return target


def _portable_batch_run(
    prepared: _PreparedRetrieval,
    *,
    root: Path,
) -> dict[str, Any]:
    resolved = prepared.resolved
    payload = prepared.payload
    summary_dir = resolved.run_dir / SUMMARY_ARTIFACT_REF.parent
    summaries = [
        _portable_summary_entry(
            source_entry,
            entry_index=entry_index,
            summary_dir=summary_dir,
            root=root,
        )
        for entry_index, source_entry in enumerate(
            payload["summary_entries"],
            start=1,
        )
    ]

    run: dict[str, Any] = {
        "paper_id": resolved.paper_id,
        "run_id": resolved.run_id,
        "locator_type": payload["locator_type"],
        "status": "retrieved",
        "source_hash": resolved.source_hash,
        "source_pack_ref": _portable_ref(resolved.source_pack_dir, root=root),
        "selected_fulltext_ref": _portable_ref(
            resolved.source_pack_dir / ROUTE_MARKDOWN_REF,
            root=root,
        ),
        "summary_ref": _portable_ref(
            resolved.run_dir / SUMMARY_ARTIFACT_REF,
            root=root,
        ),
        "summary_entries": summaries,
        "summary_match_count": len(summaries),
        "paper_card_ref": _portable_ref(
            resolved.run_dir / CARD_JSON_REF,
            root=root,
        ),
        "index_status_ref": _portable_ref(
            resolved.run_dir / INDEX_STATUS_REF,
            root=root,
        ),
        "index_lanes": [
            _portable_index_lane(entry) for entry in payload["index_lanes"]
        ],
    }
    optional_refs = {
        "acceptance_summary_ref": resolved.run_dir / ACCEPTANCE_SUMMARY_REF,
        "classification_plan_ref": resolved.run_dir / CLASSIFICATION_PLAN_REF,
        "writeback_preview_ref": resolved.run_dir / WRITEBACK_PREVIEW_REF,
    }
    for field_name, path in optional_refs.items():
        if field_name in payload:
            run[field_name] = _portable_ref(path, root=root)
    if "acceptance_status" in payload:
        run["acceptance_status"] = payload["acceptance_status"]
    return run


def _portable_summary_entry(
    source_entry: dict[str, Any],
    *,
    entry_index: int,
    summary_dir: Path,
    root: Path,
) -> dict[str, Any]:
    text_path = _resolve_safe_relative_artifact_ref(
        base_dir=summary_dir,
        ref=source_entry["text_ref"],
        label=f"summary entry {entry_index} text_ref",
        verify=False,
    )
    return {
        "grain": source_entry["grain"],
        "scope": source_entry["scope"],
        "text_ref": _portable_ref(text_path, root=root),
    }


def _portable_index_lane(source_entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "lane": source_entry["lane"],
        "status": source_entry["status"],
    }


def _portable_ref(path: Path, *, root: Path) -> str:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise MillefeuilleContractError(
            f"artifact ref escapes source-pack root: {path}"
        ) from exc
    ref = relative.as_posix()
    if (
        not ref
        or ref == "."
        or ref.startswith("../")
        or any(_SAFE_REF_PART.fullmatch(part) is None for part in relative.parts)
    ):
        raise MillefeuilleContractError(f"artifact ref is not portable: {path}")
    return ref


def _serialize_result_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


@contextmanager
def _exclusive_batch_lock(batch_dir_fd: int) -> Iterator[None]:
    if os.name != "posix":  # pragma: no cover - guarded by caller
        raise MillefeuilleContractError(
            f"retrieval batch locking is unsupported on platform {os.name!r}"
        )

    try:
        import fcntl

        flock = fcntl.flock
        lock_ex = fcntl.LOCK_EX
        lock_un = fcntl.LOCK_UN
    except (AttributeError, ImportError, OSError) as exc:
        raise MillefeuilleContractError(
            "retrieval batch publication requires POSIX flock support"
        ) from exc

    try:
        flock(batch_dir_fd, lock_ex)
    except OSError as exc:
        raise MillefeuilleContractError(
            "retrieval batch publication requires a POSIX batch directory lock"
        ) from exc
    try:
        yield
    finally:
        # Closing batch_dir_fd in the caller also releases the lock. Do not turn a
        # successfully committed generation into an error solely because an
        # explicit unlock reports a late filesystem failure.
        with suppress(OSError):
            flock(batch_dir_fd, lock_un)


def _publish_retrieval_batch_outputs(
    *,
    root: Path,
    expected_root_identity: tuple[int, int],
    batch_dir: Path,
    result_bytes: bytes,
    report_bytes: bytes,
    precommit_validator: Callable[[], None],
) -> None:
    stable_dir = batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent
    result_path = stable_dir / RETRIEVAL_BATCH_RESULT_REF.name
    report_path = stable_dir / RETRIEVAL_BATCH_REPORT_REF.name
    batch_relative = batch_dir.relative_to(root)
    stable_name = RETRIEVAL_BATCH_RESULT_REF.parent.as_posix()

    if os.name != "posix":  # pragma: no cover - exercised on non-POSIX runtimes
        raise MillefeuilleContractError(
            "retrieval batch publication requires POSIX fd-relative directory "
            f"operations on platform {os.name!r}"
        )

    try:
        root_fd = _open_directory_path_no_follow(
            root,
            label="source-pack root",
        )
        try:
            root_stat = os.fstat(root_fd)
            if (root_stat.st_dev, root_stat.st_ino) != expected_root_identity:
                raise MillefeuilleContractError(
                    f"source-pack root changed during retrieval batch: {root}"
                )
            batch_dir_fd = _open_relative_directory_hierarchy(
                root_fd=root_fd,
                root=root,
                relative=batch_relative,
            )
            try:
                with _exclusive_batch_lock(batch_dir_fd):
                    _require_path_matches_fd(
                        path=root,
                        fd=root_fd,
                        label="source-pack root",
                        directory=True,
                    )
                    _require_relative_path_matches_directory_fd(
                        root_fd=root_fd,
                        relative=batch_relative,
                        fd=batch_dir_fd,
                        path=batch_dir,
                        label="retrieval batch directory",
                    )
                    if _relative_path_exists(batch_dir_fd, stable_name):
                        _require_path_matches_fd(
                            path=root,
                            fd=root_fd,
                            label="source-pack root",
                            directory=True,
                        )
                        _require_relative_path_matches_directory_fd(
                            root_fd=root_fd,
                            relative=batch_relative,
                            fd=batch_dir_fd,
                            path=batch_dir,
                            label="retrieval batch directory",
                        )
                        # A verified generation read is the exact-rerun commit
                        # point, so no later path-based check can turn a valid
                        # rerun into a false failure.
                        _validate_existing_retrieval_generation_relative(
                            stable_dir=stable_dir,
                            result_path=result_path,
                            report_path=report_path,
                            expected_result_bytes=result_bytes,
                            expected_report_bytes=report_bytes,
                            batch_dir_fd=batch_dir_fd,
                        )
                        precommit_validator()
                        return

                    temp_name = _create_relative_temp_directory(
                        batch_dir_fd,
                        prefix=_RETRIEVAL_BATCH_TEMP_PREFIX,
                    )
                    committed = False
                    staged_files: dict[str, _StagedFileDescriptors] = {}
                    temp_dir_fd = _open_relative_directory_fd(batch_dir_fd, temp_name)
                    try:
                        _require_relative_fd_entry_matches(
                            parent_fd=batch_dir_fd,
                            name=temp_name,
                            fd=temp_dir_fd,
                            path=batch_dir / temp_name,
                            label="retrieval batch staging directory",
                            directory=True,
                        )
                        staged_files[RETRIEVAL_BATCH_RESULT_REF.name] = (
                            _write_staged_bytes(
                                Path(RETRIEVAL_BATCH_RESULT_REF.name),
                                result_bytes,
                                dir_fd=temp_dir_fd,
                            )
                        )
                        staged_files[RETRIEVAL_BATCH_REPORT_REF.name] = (
                            _write_staged_bytes(
                                Path(RETRIEVAL_BATCH_REPORT_REF.name),
                                report_bytes,
                                dir_fd=temp_dir_fd,
                            )
                        )
                        expected_staged_bytes = {
                            RETRIEVAL_BATCH_RESULT_REF.name: result_bytes,
                            RETRIEVAL_BATCH_REPORT_REF.name: report_bytes,
                        }
                        if sorted(os.listdir(temp_dir_fd)) != sorted(
                            expected_staged_bytes
                        ):
                            raise MillefeuilleContractError(
                                "retrieval batch staging output drift detected at "
                                f"{batch_dir / temp_name}"
                            )
                        for name, descriptors in staged_files.items():
                            staged_path = batch_dir / temp_name / name
                            _require_relative_fd_entry_matches(
                                parent_fd=temp_dir_fd,
                                name=name,
                                fd=descriptors.read_fd,
                                path=staged_path,
                                label="retrieval batch staged output",
                                directory=False,
                                require_single_link=True,
                            )
                            _verify_open_fd_bytes(
                                fd=descriptors.read_fd,
                                expected_bytes=expected_staged_bytes[name],
                                path=staged_path,
                                label="retrieval batch staged output",
                            )
                        os.fchmod(temp_dir_fd, 0o555)
                        _fsync_directory_fd(temp_dir_fd)
                        if sorted(os.listdir(temp_dir_fd)) != sorted(
                            expected_staged_bytes
                        ):
                            raise MillefeuilleContractError(
                                "retrieval batch staging output drift detected at "
                                f"{batch_dir / temp_name}"
                            )
                        for name, descriptors in staged_files.items():
                            staged_path = batch_dir / temp_name / name
                            _require_relative_fd_entry_matches(
                                parent_fd=temp_dir_fd,
                                name=name,
                                fd=descriptors.read_fd,
                                path=staged_path,
                                label="retrieval batch staged output",
                                directory=False,
                                require_single_link=True,
                            )
                            _verify_open_fd_bytes(
                                fd=descriptors.read_fd,
                                expected_bytes=expected_staged_bytes[name],
                                path=staged_path,
                                label="retrieval batch staged output",
                            )
                        _require_relative_fd_entry_matches(
                            parent_fd=batch_dir_fd,
                            name=temp_name,
                            fd=temp_dir_fd,
                            path=batch_dir / temp_name,
                            label="retrieval batch staging directory",
                            directory=True,
                        )
                        _require_path_matches_fd(
                            path=root,
                            fd=root_fd,
                            label="source-pack root",
                            directory=True,
                        )
                        _require_relative_path_matches_directory_fd(
                            root_fd=root_fd,
                            relative=batch_relative,
                            fd=batch_dir_fd,
                            path=batch_dir,
                            label="retrieval batch directory",
                        )
                        precommit_validator()
                        try:
                            _rename_relative_directory_noreplace(
                                parent_fd=batch_dir_fd,
                                source_name=temp_name,
                                destination_name=stable_name,
                            )
                        except FileExistsError as exc:
                            raise MillefeuilleContractError(
                                "retrieval batch output appeared during publication: "
                                f"{stable_dir}"
                            ) from exc
                        # Atomic no-replace rename is the publication commit point:
                        # only the complete, descriptor-verified, read-only
                        # generation becomes visible under the stable name.
                        committed = True
                        _fsync_directory_fd(batch_dir_fd)
                    finally:
                        try:
                            if not committed:
                                _scrub_staged_files(
                                    generation_dir_fd=temp_dir_fd,
                                    staged_files=staged_files,
                                )
                                with suppress(OSError):
                                    _fsync_directory_fd(batch_dir_fd)
                        finally:
                            for descriptors in staged_files.values():
                                for fd in (descriptors.read_fd, descriptors.scrub_fd):
                                    with suppress(OSError):
                                        os.close(fd)
                            os.close(temp_dir_fd)
            finally:
                os.close(batch_dir_fd)
        finally:
            os.close(root_fd)
    except MillefeuilleContractError:
        raise
    except (NotImplementedError, OSError, TypeError) as exc:
        raise MillefeuilleContractError(
            f"could not publish retrieval batch outputs for {batch_dir}: {exc}"
        ) from exc


def _write_staged_bytes(
    path: Path,
    payload: bytes,
    *,
    dir_fd: int,
) -> _StagedFileDescriptors:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    scrub_fd: int | None = os.open(path.name, flags, 0o600, dir_fd=dir_fd)
    read_fd: int | None = None
    try:
        with os.fdopen(scrub_fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.fchmod(scrub_fd, 0o444)
        os.fsync(scrub_fd)
        read_fd = _open_verified_relative_regular_file(
            stable_dir_fd=dir_fd,
            name=path.name,
            path=path,
            label="retrieval batch staged output",
        )
        write_stat = os.fstat(scrub_fd)
        read_stat = os.fstat(read_fd)
        if (write_stat.st_dev, write_stat.st_ino) != (
            read_stat.st_dev,
            read_stat.st_ino,
        ):
            raise MillefeuilleContractError(
                f"retrieval batch staged output changed while writing: {path}"
            )
        _verify_open_fd_bytes(
            fd=read_fd,
            expected_bytes=payload,
            path=path,
            label="retrieval batch staged output",
        )
        descriptors = _StagedFileDescriptors(
            read_fd=read_fd,
            scrub_fd=scrub_fd,
        )
        read_fd = None
        scrub_fd = None
        return descriptors
    except BaseException:
        if scrub_fd is not None:
            with suppress(OSError):
                _scrub_staged_file_fd(scrub_fd)
        raise
    finally:
        if read_fd is not None:
            with suppress(OSError):
                os.close(read_fd)
        if scrub_fd is not None:
            os.close(scrub_fd)


def _fsync_directory_fd(fd: int) -> None:
    if os.name != "posix":
        return
    os.fsync(fd)


def _validate_existing_retrieval_generation_relative(
    *,
    stable_dir: Path,
    result_path: Path,
    report_path: Path,
    expected_result_bytes: bytes,
    expected_report_bytes: bytes,
    batch_dir_fd: int,
) -> None:
    stable_name = RETRIEVAL_BATCH_RESULT_REF.parent.as_posix()
    stable_dir_fd = _open_stable_generation_directory_fd(
        batch_dir_fd=batch_dir_fd,
        stable_name=stable_name,
        stable_dir=stable_dir,
    )
    open_files: list[tuple[Path, str, int, bytes]] = []
    try:
        entries = sorted(os.listdir(stable_dir_fd))
        present_entries = set(entries)
        expected = (
            (result_path, expected_result_bytes, "batch retrieval result"),
            (report_path, expected_report_bytes, "batch retrieval report"),
        )
        for path, _expected_bytes, label in expected:
            if path.name in present_entries:
                _require_stable_generation_file_relative(
                    stable_dir_fd=stable_dir_fd,
                    name=path.name,
                    path=path,
                    label=label,
                )
        expected_names = sorted((result_path.name, report_path.name))
        if entries != expected_names:
            raise MillefeuilleContractError(
                f"existing retrieval batch output drift detected at {stable_dir}"
            )
        for path, expected_bytes, label in expected:
            fd = _open_verified_relative_regular_file(
                stable_dir_fd=stable_dir_fd,
                name=path.name,
                path=path,
                label=label,
            )
            open_files.append((path, label, fd, expected_bytes))
            _verify_open_fd_bytes(
                fd=fd,
                expected_bytes=expected_bytes,
                path=path,
                label=label,
            )
        if os.fstat(stable_dir_fd).st_mode & 0o222:
            raise MillefeuilleContractError(
                f"existing retrieval batch output drift detected at {stable_dir}"
            )
        if sorted(os.listdir(stable_dir_fd)) != expected_names:
            raise MillefeuilleContractError(
                f"existing retrieval batch output drift detected at {stable_dir}"
            )
        _require_relative_fd_entry_matches(
            parent_fd=batch_dir_fd,
            name=stable_name,
            fd=stable_dir_fd,
            path=stable_dir,
            label="retrieval batch output generation",
            directory=True,
        )
        for path, label, fd, expected_bytes in open_files:
            _require_relative_fd_entry_matches(
                parent_fd=stable_dir_fd,
                name=path.name,
                fd=fd,
                path=path,
                label=label,
                directory=False,
                require_single_link=True,
            )
            _verify_open_fd_bytes(
                fd=fd,
                expected_bytes=expected_bytes,
                path=path,
                label=label,
            )
        _require_relative_fd_entry_matches(
            parent_fd=batch_dir_fd,
            name=stable_name,
            fd=stable_dir_fd,
            path=stable_dir,
            label="retrieval batch output generation",
            directory=True,
        )
        for path, label, fd, _expected_bytes in open_files:
            _require_relative_fd_entry_matches(
                parent_fd=stable_dir_fd,
                name=path.name,
                fd=fd,
                path=path,
                label=label,
                directory=False,
                require_single_link=True,
            )
        # The held-descriptor rereads plus final namespace checks are the commit
        # sequence. A non-cooperative same-UID writer can still mutate a regular
        # file after its final verification; callers requiring stronger
        # immutability must use trusted or immutable/content-addressed storage.
    finally:
        for _path, _label, fd, _expected_bytes in open_files:
            os.close(fd)
        os.close(stable_dir_fd)


def _require_stable_generation_file_relative(
    *,
    stable_dir_fd: int,
    name: str,
    path: Path,
    label: str,
) -> None:
    try:
        stat_result = os.stat(name, dir_fd=stable_dir_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(f"{label} not found: {path}") from exc
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not inspect {label} {path}: {exc}"
        ) from exc
    if stat.S_ISLNK(stat_result.st_mode):
        raise MillefeuilleContractError(f"{label} must not be a symbolic link")
    if not stat.S_ISREG(stat_result.st_mode):
        raise MillefeuilleContractError(f"{label} not found: {path}")
    if stat_result.st_nlink != 1:
        raise MillefeuilleContractError(f"{label} must not be a hard link")
    if stat_result.st_mode & 0o222:
        raise MillefeuilleContractError(f"{label} must be read-only: {path}")


def _read_bytes_from_open_fd(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _stable_file_stat(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _verify_open_fd_bytes(
    *,
    fd: int,
    expected_bytes: bytes,
    path: Path,
    label: str,
) -> None:
    before = os.fstat(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    actual_bytes = _read_bytes_from_open_fd(fd)
    after = os.fstat(fd)
    if _stable_file_stat(before) != _stable_file_stat(after):
        raise MillefeuilleContractError(f"{label} changed while read: {path}")
    if actual_bytes != expected_bytes:
        raise MillefeuilleContractError(
            f"existing retrieval batch output drift detected at {path}"
        )


def _open_directory_fd(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags)


def _directory_identity_no_follow(path: Path, *, label: str) -> tuple[int, int]:
    fd = _open_directory_path_no_follow(path, label=label)
    try:
        stat_result = os.fstat(fd)
        return stat_result.st_dev, stat_result.st_ino
    finally:
        os.close(fd)


def _open_directory_path_no_follow(path: Path, *, label: str) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise MillefeuilleContractError(
            f"{label} requires no-follow directory operations on this platform"
        )

    anchor = Path(path.anchor) if path.is_absolute() else Path(".")
    parts = path.parts[1:] if path.is_absolute() else path.parts
    current_fd = _open_directory_fd(anchor)
    try:
        for part in parts:
            if part in {"", "."}:
                continue
            next_fd = _open_relative_directory_fd(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
        _require_path_matches_fd(
            path=path,
            fd=current_fd,
            label=label,
            directory=True,
        )
        return current_fd
    except MillefeuilleContractError:
        os.close(current_fd)
        raise
    except FileNotFoundError as exc:
        os.close(current_fd)
        raise MillefeuilleContractError(f"{label} not found: {path}") from exc
    except OSError as exc:
        os.close(current_fd)
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise MillefeuilleContractError(
                f"{label} path must not contain symbolic links or non-directories: "
                f"{path}"
            ) from exc
        raise MillefeuilleContractError(
            f"could not open {label} {path}: {exc}"
        ) from exc


def _open_relative_directory_fd(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(name, flags, dir_fd=parent_fd)


def _open_verified_relative_regular_file(
    *,
    stable_dir_fd: int,
    name: str,
    path: Path,
    label: str,
) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(name, flags, dir_fd=stable_dir_fd)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(f"{label} not found: {path}") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise MillefeuilleContractError(
                f"{label} must not be a symbolic link"
            ) from exc
        raise MillefeuilleContractError(
            f"could not inspect {label} {path}: {exc}"
        ) from exc
    try:
        fd_stat = os.fstat(fd)
        if not stat.S_ISREG(fd_stat.st_mode):
            raise MillefeuilleContractError(f"{label} not found: {path}")
        if fd_stat.st_nlink != 1:
            raise MillefeuilleContractError(f"{label} must not be a hard link")
        if fd_stat.st_mode & 0o222:
            raise MillefeuilleContractError(f"{label} must be read-only: {path}")
        _require_relative_fd_entry_matches(
            parent_fd=stable_dir_fd,
            name=name,
            fd=fd,
            path=path,
            label=label,
            directory=False,
            require_single_link=True,
        )
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_relative_directory_hierarchy(
    *,
    root_fd: int,
    root: Path,
    relative: Path,
) -> int:
    current_fd = root_fd
    try:
        for index, part in enumerate(relative.parts):
            current_path = root.joinpath(*relative.parts[: index + 1])
            next_fd = _mkdir_open_relative_directory(
                parent_fd=current_fd,
                name=part,
                path=current_path,
            )
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        if current_fd != root_fd:
            os.close(current_fd)
        raise


def _mkdir_open_relative_directory(
    *,
    parent_fd: int,
    name: str,
    path: Path,
) -> int:
    try:
        os.mkdir(name, mode=0o755, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        if exc.errno == errno.ENOTDIR:
            raise MillefeuilleContractError(
                f"retrieval batch output parent is not a directory: {path.parent}"
            ) from exc
        raise MillefeuilleContractError(
            f"could not create retrieval batch directory {path}: {exc}"
        ) from exc
    try:
        fd = _open_relative_directory_fd(parent_fd, name)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(
            f"retrieval batch directory changed while locked: {path}"
        ) from exc
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR} and _relative_entry_is_symlink(
            parent_fd, name
        ):
            raise MillefeuilleContractError(
                "retrieval batch output path must not contain symbolic links"
            ) from exc
        if exc.errno == errno.ENOTDIR:
            raise MillefeuilleContractError(
                f"retrieval batch output parent is not a directory: {path}"
            ) from exc
        raise MillefeuilleContractError(
            f"could not open retrieval batch directory {path}: {exc}"
        ) from exc
    try:
        _require_relative_fd_entry_matches(
            parent_fd=parent_fd,
            name=name,
            fd=fd,
            path=path,
            label="retrieval batch directory",
            directory=True,
        )
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_existing_relative_directory_tree_fd(
    *,
    root_fd: int,
    relative: Path,
    path: Path,
    label: str,
) -> int:
    current_fd = root_fd
    try:
        for part in relative.parts:
            next_fd = _open_relative_directory_fd(current_fd, part)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except (FileNotFoundError, OSError) as exc:
        if current_fd != root_fd:
            os.close(current_fd)
        raise MillefeuilleContractError(
            f"{label} changed while locked: {path}"
        ) from exc


def _require_relative_path_matches_directory_fd(
    *,
    root_fd: int,
    relative: Path,
    fd: int,
    path: Path,
    label: str,
) -> None:
    candidate_fd = _open_existing_relative_directory_tree_fd(
        root_fd=root_fd,
        relative=relative,
        path=path,
        label=label,
    )
    try:
        candidate_stat = os.fstat(candidate_fd)
        fd_stat = os.fstat(fd)
        if (candidate_stat.st_dev, candidate_stat.st_ino) != (
            fd_stat.st_dev,
            fd_stat.st_ino,
        ):
            raise MillefeuilleContractError(f"{label} changed while locked: {path}")
    finally:
        os.close(candidate_fd)


def _open_stable_generation_directory_fd(
    *,
    batch_dir_fd: int,
    stable_name: str,
    stable_dir: Path,
) -> int:
    try:
        fd = _open_relative_directory_fd(batch_dir_fd, stable_name)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(
            f"retrieval batch output generation not found: {stable_dir}"
        ) from exc
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR} and _relative_entry_is_symlink(
            batch_dir_fd, stable_name
        ):
            raise MillefeuilleContractError(
                f"retrieval batch output must not be a symbolic link: {stable_dir}"
            ) from exc
        if exc.errno == errno.ENOTDIR:
            raise MillefeuilleContractError(
                f"retrieval batch output parent is not a directory: {stable_dir}"
            ) from exc
        raise MillefeuilleContractError(
            f"could not inspect retrieval batch output generation {stable_dir}: {exc}"
        ) from exc
    try:
        _require_relative_fd_entry_matches(
            parent_fd=batch_dir_fd,
            name=stable_name,
            fd=fd,
            path=stable_dir,
            label="retrieval batch output generation",
            directory=True,
        )
        return fd
    except Exception:
        os.close(fd)
        raise


def _require_path_matches_fd(
    *,
    path: Path,
    fd: int,
    label: str,
    directory: bool,
    require_single_link: bool = False,
) -> None:
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(
            f"{label} changed while locked: {path}"
        ) from exc
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not inspect {label} {path}: {exc}"
        ) from exc
    fd_stat = os.fstat(fd)
    if (path_stat.st_dev, path_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
        raise MillefeuilleContractError(f"{label} changed while locked: {path}")
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(path_stat.st_mode):
        raise MillefeuilleContractError(f"{label} changed while locked: {path}")
    if require_single_link and path_stat.st_nlink != 1:
        raise MillefeuilleContractError(f"{label} changed while locked: {path}")


def _relative_entry_is_symlink(parent_fd: int, name: str) -> bool:
    try:
        stat_result = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISLNK(stat_result.st_mode)


def _require_relative_fd_entry_matches(
    *,
    parent_fd: int,
    name: str,
    fd: int | None,
    path: Path,
    label: str,
    directory: bool,
    require_single_link: bool = False,
) -> None:
    try:
        path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(
            f"{label} changed while locked: {path}"
        ) from exc
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not inspect {label} {path}: {exc}"
        ) from exc
    if fd is not None:
        fd_stat = os.fstat(fd)
        if (path_stat.st_dev, path_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
            raise MillefeuilleContractError(f"{label} changed while locked: {path}")
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(path_stat.st_mode):
        raise MillefeuilleContractError(f"{label} changed while locked: {path}")
    if require_single_link and path_stat.st_nlink != 1:
        raise MillefeuilleContractError(f"{label} changed while locked: {path}")


def _relative_path_exists(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _create_relative_temp_directory(parent_fd: int, *, prefix: str) -> str:
    for _attempt in range(128):
        candidate = f"{prefix}{secrets.token_hex(8)}"
        try:
            os.mkdir(candidate, mode=0o700, dir_fd=parent_fd)
            return candidate
        except FileExistsError:
            continue
    raise MillefeuilleContractError(
        "could not allocate a unique retrieval batch staging directory"
    )


def _rename_relative_directory_noreplace(
    *,
    parent_fd: int,
    source_name: str,
    destination_name: str,
) -> None:
    try:
        import ctypes
        import sys

        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            rename_noreplace = libc.renameatx_np
            no_replace_flag = 0x00000004  # RENAME_EXCL
        else:
            rename_noreplace = libc.renameat2
            no_replace_flag = 1  # RENAME_NOREPLACE
    except (AttributeError, ImportError, OSError) as exc:
        raise MillefeuilleContractError(
            "retrieval batch publication requires atomic no-replace rename support"
        ) from exc

    rename_noreplace.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename_noreplace.restype = ctypes.c_int
    result = rename_noreplace(
        parent_fd,
        os.fsencode(source_name),
        parent_fd,
        os.fsencode(destination_name),
        no_replace_flag,
    )
    if result == 0:
        return

    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(
            error_number,
            os.strerror(error_number),
            destination_name,
        )
    unsupported = {errno.EINVAL, errno.ENOSYS}
    for name in ("ENOTSUP", "EOPNOTSUPP"):
        value = getattr(errno, name, None)
        if value is not None:
            unsupported.add(value)
    if error_number in unsupported:
        raise MillefeuilleContractError(
            "retrieval batch publication requires atomic no-replace rename support"
        )
    raise OSError(error_number, os.strerror(error_number), destination_name)


def _scrub_staged_file_fd(fd: int) -> None:
    os.ftruncate(fd, 0)
    os.fsync(fd)


def _scrub_staged_files(
    *,
    generation_dir_fd: int,
    staged_files: dict[str, _StagedFileDescriptors],
) -> None:
    """Erase owned staged inodes through retained failure-only descriptors."""

    first_error: OSError | None = None
    for descriptors in staged_files.values():
        try:
            _scrub_staged_file_fd(descriptors.scrub_fd)
        except OSError as exc:
            if first_error is None:
                first_error = exc
        finally:
            with suppress(OSError):
                os.fchmod(descriptors.scrub_fd, 0o444)
                os.fsync(descriptors.scrub_fd)
    try:
        _fsync_directory_fd(generation_dir_fd)
    except OSError as exc:
        if first_error is None:
            first_error = exc
    if first_error is not None:
        raise first_error


def _render_retrieval_batch_markdown(result: dict[str, Any]) -> str:
    counts = result["counts"]
    lines = [
        "# Millefeuille Batch Retrieval",
        "",
        f"- Batch: `{result['batch_id']}`",
        f"- Status: `{result['status']}`",
        f"- Runs: {counts['runs']}",
        f"- Summary matches: {counts['summary_matches']}",
        f"- Index lane matches: {counts['index_lane_matches']}",
        (
            "- Acceptance: "
            f"{counts['acceptance_pass']} pass, "
            f"{counts['acceptance_needs_review']} needs review, "
            f"{counts['acceptance_unavailable']} unavailable"
        ),
    ]
    filters = result.get("filters")
    if filters:
        rendered_filters = ", ".join(
            f"{key}={value!r}" for key, value in sorted(filters.items())
        )
        lines.append(f"- Filters: {rendered_filters}")
    lines.extend(["", "## Runs", ""])
    for run in result["runs"]:
        lines.extend(
            [
                f"### `{run['paper_id']}` / `{run['run_id']}`",
                "",
                f"- Locator: `{run['locator_type']}`",
                f"- Status: `{run['status']}`",
                f"- Source hash: `{run['source_hash']}`",
                f"- Source pack: `{run['source_pack_ref']}`",
                f"- Selected full text: `{run['selected_fulltext_ref']}`",
                f"- Summary bundle: `{run['summary_ref']}`",
                f"- Paper card: `{run['paper_card_ref']}`",
                f"- Index status: `{run['index_status_ref']}`",
                f"- Summary matches: {run['summary_match_count']}",
            ]
        )
        if run["summary_entries"]:
            lines.append("- Matched summary refs:")
            for entry in run["summary_entries"]:
                lines.append(
                    "  - "
                    f"`{entry['text_ref']}` "
                    f"(`{entry['scope']}` / `{entry['grain']}`)"
                )
        if run["index_lanes"]:
            lines.append("- Index lanes:")
            for lane in run["index_lanes"]:
                lines.append(f"  - `{lane['lane']}`: `{lane['status']}`")
        if "acceptance_status" in run:
            lines.append(
                "- Acceptance: "
                f"`{run['acceptance_status']}` "
                f"(`{run['acceptance_summary_ref']}`)"
            )
        else:
            lines.append("- Acceptance: unavailable")
        if "classification_plan_ref" in run:
            lines.append(f"- Classification plan: `{run['classification_plan_ref']}`")
        if "writeback_preview_ref" in run:
            lines.append(f"- Writeback preview: `{run['writeback_preview_ref']}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _resolve_retrieve_artifacts(
    *,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None,
    item_key: str | None,
    slug: str | None,
    doi: str | None,
    title: str | None,
    artifact_reader: RootArtifactReader | None = None,
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
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
            artifact_root=artifact_root,
            stage_manifest=stage_manifest,
            artifact_reader=artifact_reader,
        )
        return resolved, locator_type

    normalized_query = (
        _normalize_doi(locator_value)
        if locator_type == "doi"
        else _normalize_text(locator_value, "title")
    )
    root = Path(source_pack_root)
    ensure_no_follow_directory(root, "source-pack root")
    zotero_root = root / "zotero"
    ensure_no_follow_directory(
        zotero_root,
        "source-pack corpus",
        root=root,
    )

    resolved_run_id = str(run_id).strip()
    require_safe_package_id(resolved_run_id, "run_id")
    matches: list[ResolvedRunArtifacts] = []
    if artifact_reader is not None:
        candidate_names = artifact_reader.list_directory_names(
            zotero_root,
            "source-pack corpus",
        )
        candidates = [zotero_root / name for name in candidate_names]
    else:
        candidates = sorted(zotero_root.iterdir(), key=lambda path: path.name)

    for candidate in candidates:
        if artifact_reader is not None:
            try:
                require_safe_package_id(candidate.name, "paper_id")
            except MillefeuilleContractError:
                continue
            if not artifact_reader.is_directory(candidate, "source-pack candidate"):
                continue
        elif candidate.is_symlink() or not candidate.is_dir():
            continue
        if artifact_root is None and stage_manifest is None:
            card_path = (
                candidate
                / "analyses"
                / "millefeuille"
                / resolved_run_id
                / CARD_JSON_REF
            )
            if not _probe_retrieval_file(
                card_path,
                "paper card",
                artifact_reader=artifact_reader,
            ):
                continue
        try:
            resolved = resolve_run_artifacts(
                source_pack_root=root,
                run_id=run_id,
                paper_id=candidate.name,
                artifact_root=artifact_root,
                stage_manifest=stage_manifest,
                artifact_reader=artifact_reader,
            )
        except MillefeuilleContractError as exc:
            if artifact_root is not None or stage_manifest is not None:
                message = str(exc)
                if any(
                    expected in message
                    for expected in (
                        "contains no complete run package",
                        "stage manifest does not belong",
                        "source-pack manifest paper_id drift",
                        "artifact index paper_id drift",
                        "artifact index source-pack ref drift",
                    )
                ):
                    continue
            raise
        _validate_resolved_package_paths(
            resolved,
            artifact_reader=artifact_reader,
        )
        card_payload = _load_verified_paper_card(
            resolved,
            artifact_reader=artifact_reader,
        )
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
            f"ambiguous {locator_type} matched multiple artifact packages: {paper_ids}"
        )
    return matches[0], locator_type


def _load_verified_paper_card(
    resolved: ResolvedRunArtifacts,
    *,
    artifact_reader: RootArtifactReader | None = None,
) -> dict[str, Any]:
    card_path = resolved.run_dir / CARD_JSON_REF
    card_payload = PaperCardRecord.from_dict(
        _load_retrieval_json(
            card_path,
            "paper card",
            artifact_reader=artifact_reader,
        )
    ).to_dict()
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
    *,
    artifact_reader: RootArtifactReader | None = None,
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
        _verify_retrieval_file(
            expected_path,
            field_name,
            artifact_reader=artifact_reader,
        )


def _require_regular_artifact(
    path: Path,
    label: str,
    *,
    root: Path | None = None,
) -> None:
    ensure_no_follow_regular_file(path, label, root=root)


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
