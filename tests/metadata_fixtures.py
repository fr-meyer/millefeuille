"""Completed synthetic DTO fixtures; never call a production metadata writer.

Direct writes here are confined to fresh unittest-owned temporary directories.
They are fixture construction, not a fallback backend for application writes.
"""

import json
from pathlib import Path

from millefeuille.domain.artifact_writer import (
    build_dry_run_artifact_index,
    build_dry_run_stage_manifest,
    resolve_artifact_run_context,
)


def write_fixture_metadata(*, items, handoff_rows, config, handoff_enabled=False):
    for item in items:
        context = resolve_artifact_run_context(
            item=item, config=config, run_id=config.run_id
        )
        rows = [row for row in handoff_rows if row.item_key == item.key]
        stage = build_dry_run_stage_manifest(
            item=item,
            rows=rows,
            run_id=config.run_id,
            handoff_enabled=handoff_enabled,
            source_pack_verified=context.source_pack_verified,
            native_extraction_ready=context.native_extraction_evidence_ref is not None,
            ocr_extraction_ready=context.ocr_extraction_evidence_ref is not None,
            route_ready=context.route_evidence_ref is not None,
            structure_ready=context.structure_evidence_ref is not None,
            structure_outline_ready=context.structure_outline_ref is not None,
            summarize_ready=context.summary_artifact_ref is not None,
            card_ready=context.paper_card_json_ref is not None,
            index_ready=context.retrieval_index_status_ref is not None,
        )
        index_context = dict(vars(context))
        index_context.pop("source_pack_verified")
        index = build_dry_run_artifact_index(
            item=item,
            rows=rows,
            run_id=config.run_id,
            stage_manifest=stage,
            **index_context,
        )
        context.run_dir.mkdir(parents=True, exist_ok=True)
        write_fixture_json(context.run_dir / "stage-manifest.json", stage.to_dict())
        write_fixture_json(context.run_dir / "artifact-index.json", index.to_dict())


def write_fixture_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )


def write_fixture_acceptance(*, source_pack_root, run_id, paper_id, handoff_path):
    from types import SimpleNamespace

    from millefeuille.domain import acceptance
    from millefeuille.domain.millefeuille import StageName
    from millefeuille.domain.stage_runtime import (
        resolve_run_artifacts,
        update_artifact_index,
        upsert_artifact_record,
        upsert_stage_record,
    )

    resolved = resolve_run_artifacts(
        source_pack_root=source_pack_root, run_id=run_id, paper_id=paper_id
    )
    summary = acceptance._build_acceptance_summary(
        resolved=resolved, handoff_path=handoff_path, duplicate_scan_path=None
    )
    summary_ref = str(acceptance.ACCEPTANCE_SUMMARY_REF)
    markdown_ref = str(acceptance.ACCEPTANCE_SUMMARY_MARKDOWN_REF)
    status = "passed" if summary.status.value == "pass" else "needs-review"
    stage = upsert_stage_record(
        resolved.stage_manifest,
        name=StageName.ACCEPTANCE,
        status=status,
        outputs=[summary_ref, markdown_ref],
        notes=[f"acceptance summary written for {resolved.paper_id}"],
    )
    index = resolved.artifact_index
    for name, kind, ref, fmt in (
        ("acceptance_summary", "acceptance-summary", summary_ref, "json"),
        (
            "acceptance_summary_markdown",
            "acceptance-summary-markdown",
            markdown_ref,
            "markdown",
        ),
    ):
        index = upsert_artifact_record(
            index,
            name=name,
            kind=kind,
            ref=ref,
            format=fmt,
            stage=StageName.ACCEPTANCE.value,
            private_content=False,
        )
    index = update_artifact_index(
        index, stage_name=StageName.ACCEPTANCE, stage_status=status
    )
    write_fixture_json(resolved.stage_manifest_path, stage.to_dict())
    write_fixture_json(resolved.artifact_index_path, index.to_dict())
    summary_path = resolved.run_dir / summary_ref
    write_fixture_json(summary_path, summary.to_dict())
    (resolved.run_dir / markdown_ref).write_bytes(
        acceptance._render_acceptance_markdown(summary).encode("utf-8")
    )
    return SimpleNamespace(status=summary.status.value, summary_path=summary_path)


def apply_complete_preview_fixture(source_pack_root, run_dir):
    template = (
        Path(__file__).parent
        / "fixtures/millefeuille_portable_metadata/complete-preview.json"
    )

    def render(value):
        if isinstance(value, str):
            return value.replace("${RUN_DIR}", str(run_dir)).replace(
                "${SOURCE_PACK_ROOT}", str(source_pack_root)
            )
        if isinstance(value, list):
            return [render(item) for item in value]
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        return value

    for relative, payload in json.loads(template.read_text(encoding="utf-8")).items():
        path = run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".json"):
            write_fixture_json(path, render(payload))
        else:
            path.write_bytes(render(payload).encode("utf-8"))
