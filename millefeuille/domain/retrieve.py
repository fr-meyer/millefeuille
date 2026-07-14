"""Read-only retrieval over Millefeuille artifact packages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from millefeuille.domain.acceptance import ACCEPTANCE_SUMMARY_REF
from millefeuille.domain.classification import (
    CLASSIFICATION_PLAN_REF,
    WRITEBACK_PREVIEW_REF,
)
from millefeuille.domain.index_fixtures import (
    INDEX_STATUS_REF,
    load_retrieval_index_status,
)
from millefeuille.domain.stage_runtime import (
    load_json_object,
    relative_ref,
    resolve_run_artifacts,
)
from millefeuille.domain.summary_fixtures import (
    SUMMARY_ARTIFACT_REF,
    load_hierarchical_summary,
)


def retrieve_artifact_refs(
    *,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None = None,
    item_key: str | None = None,
    summary_scope: str | None = None,
    grain: str | None = None,
    index_lane: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_run_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
    )
    summary_payload = load_hierarchical_summary(resolved.run_dir / SUMMARY_ARTIFACT_REF)
    summaries = [
        dict(entry)
        for entry in summary_payload["summaries"]
        if (summary_scope is None or entry["scope"] == summary_scope)
        and (grain is None or entry["grain"] == grain)
    ]
    index_payload = load_retrieval_index_status(resolved.run_dir / INDEX_STATUS_REF)
    lanes = [
        dict(entry)
        for entry in index_payload["lanes"]
        if index_lane is None or entry["lane"] == index_lane
    ]

    result: dict[str, Any] = {
        "paper_id": resolved.paper_id,
        "run_id": resolved.run_id,
        "source_hash": resolved.source_hash,
        "source_pack_ref": relative_ref(resolved.source_pack_dir, resolved.run_dir),
        "selected_fulltext_ref": index_payload["selected_fulltext_ref"],
        "summary_ref": relative_ref(
            resolved.run_dir / SUMMARY_ARTIFACT_REF,
            resolved.run_dir,
        ),
        "summary_entries": summaries,
        "paper_card_ref": index_payload["paper_card_ref"],
        "index_lanes": lanes,
    }
    acceptance_path = resolved.run_dir / ACCEPTANCE_SUMMARY_REF
    if acceptance_path.is_file():
        result["acceptance_summary_ref"] = relative_ref(
            acceptance_path, resolved.run_dir
        )
        result["acceptance_status"] = load_json_object(
            acceptance_path,
            "acceptance summary",
        )["status"]
    classification_path = resolved.run_dir / CLASSIFICATION_PLAN_REF
    if classification_path.is_file():
        result["classification_plan_ref"] = relative_ref(
            classification_path,
            resolved.run_dir,
        )
    writeback_preview_path = resolved.run_dir / WRITEBACK_PREVIEW_REF
    if writeback_preview_path.is_file():
        result["writeback_preview_ref"] = relative_ref(
            writeback_preview_path,
            resolved.run_dir,
        )
    return result
