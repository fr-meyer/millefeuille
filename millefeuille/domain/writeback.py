"""Preview-only writeback planning for classified Millefeuille runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from millefeuille.domain.classification import WRITEBACK_PREVIEW_REF
from millefeuille.domain.millefeuille import (
    MillefeuilleContractError,
    StageName,
    StageStatus,
    ZoteroWritebackPlanRecord,
)
from millefeuille.domain.stage_runtime import (
    load_json_object,
    persist_run_artifacts,
    relative_ref,
    resolve_run_artifacts,
    stage_status_map,
    update_artifact_index,
    upsert_artifact_record,
    upsert_stage_record,
    write_json_object,
    write_text,
)

WRITEBACK_PLAN_REF = Path("zotero-writeback-plan.json")
WRITEBACK_NOTE_REF = Path("reports/zotero-writeback-note.md")


@dataclass(frozen=True)
class WritebackPlanResult:
    paper_id: str
    run_id: str
    plan_path: Path
    note_path: Path | None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "plan_path": str(self.plan_path),
        }
        if self.note_path is not None:
            payload["note_path"] = str(self.note_path)
        return payload


def write_writeback_plan(
    *,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None = None,
    item_key: str | None = None,
    preview_path: str | Path | None = None,
) -> WritebackPlanResult:
    resolved = resolve_run_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
    )
    preview_json_path = (
        Path(preview_path)
        if preview_path is not None
        else resolved.run_dir / WRITEBACK_PREVIEW_REF
    )
    preview = load_json_object(preview_json_path, "writeback preview")
    if stage_status_map(resolved.stage_manifest).get(StageName.CLASSIFY.value) != (
        StageStatus.PASSED.value
    ):
        raise MillefeuilleContractError(
            "writeback preview requires a passed classification stage"
        )
    if preview.get("schema_version") != ("millefeuille-zotero-writeback-preview/v0.1"):
        raise MillefeuilleContractError("unsupported writeback preview schema_version")
    if str(preview.get("mode", "")).strip() != "preview":
        raise MillefeuilleContractError("writeback preview mode must be preview")
    if str(preview.get("status", "")).strip() != "previewed":
        raise MillefeuilleContractError("writeback preview status must be previewed")
    expected_preview_identity = {
        "paper_id": resolved.paper_id,
        "run_id": resolved.run_id,
        "source_hash": resolved.source_hash,
    }
    for field_name, expected in expected_preview_identity.items():
        actual = preview.get(field_name)
        if actual != expected:
            raise MillefeuilleContractError(
                f"writeback preview {field_name} drift: "
                f"expected {expected!r}, got {actual!r}"
            )

    note_path: Path | None = None
    note_markdown_ref: str | None = None
    note_markdown = preview.get("note_markdown")
    if isinstance(note_markdown, str) and note_markdown.strip():
        note_path = resolved.run_dir / WRITEBACK_NOTE_REF
        note_markdown_ref = relative_ref(note_path, resolved.run_dir)

    classification_ref = str(preview.get("classification_ref", "")).strip() or None
    plan = ZoteroWritebackPlanRecord(
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
        mode="preview",
        status="previewed",
        add_tags=list(preview.get("add_tags", [])),
        remove_tags=list(preview.get("remove_tags", [])),
        note_markdown_ref=note_markdown_ref,
        destination_collection=preview.get("destination_collection"),
        classification_ref=classification_ref,
        execution_notes=["preview-only plan; no live Zotero mutation"],
    )
    plan_path = resolved.run_dir / WRITEBACK_PLAN_REF
    if note_path is not None:
        write_text(note_path, note_markdown.strip() + "\n")
    write_json_object(plan_path, plan.to_dict())

    stage_manifest = upsert_stage_record(
        resolved.stage_manifest,
        name=StageName.WRITEBACK,
        status=StageStatus.PASSED.value,
        outputs=[
            relative_ref(plan_path, resolved.run_dir),
            *([note_markdown_ref] if note_markdown_ref else []),
        ],
        notes=["preview-only Zotero writeback plan materialized"],
    )
    artifact_index = upsert_artifact_record(
        resolved.artifact_index,
        name="zotero_writeback_plan",
        kind="zotero-writeback-plan",
        ref=relative_ref(plan_path, resolved.run_dir),
        format="json",
        stage=StageName.WRITEBACK.value,
        private_content=False,
    )
    if note_markdown_ref is not None:
        artifact_index = upsert_artifact_record(
            artifact_index,
            name="zotero_writeback_note",
            kind="zotero-writeback-note",
            ref=note_markdown_ref,
            format="markdown",
            stage=StageName.WRITEBACK.value,
            private_content=False,
        )
    artifact_index = update_artifact_index(
        artifact_index,
        stage_name=StageName.WRITEBACK,
        stage_status=StageStatus.PASSED.value,
        zotero_writeback={
            "mode": "preview",
            "status": "previewed",
            "plan_ref": relative_ref(plan_path, resolved.run_dir),
        },
    )
    persist_run_artifacts(
        resolved,
        stage_manifest=stage_manifest,
        artifact_index=artifact_index,
    )
    return WritebackPlanResult(
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        plan_path=plan_path,
        note_path=note_path,
    )
