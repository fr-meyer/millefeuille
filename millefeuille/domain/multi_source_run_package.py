"""Plan and publish an existing multi-PDF run package without model calls."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from millefeuille.domain.artifact_writer import (
    _resolve_card_refs,
    _resolve_index_refs,
    _resolve_summary_refs,
)
from millefeuille.domain.artifacts import ArtifactIndex
from millefeuille.domain.millefeuille import (
    MillefeuilleContractError,
    RunMode,
    StageManifest,
    StageName,
    StageRecord,
    StageStatus,
)
from millefeuille.domain.multi_source_summary import build_multi_source_summary_package
from millefeuille.domain.published_summary_run_link import VIEW_SCHEMA_VERSION
from millefeuille.domain.secure_io import read_bytes_no_follow, write_new_text_no_follow
from millefeuille.domain.source_packs import (
    load_source_pack_manifest,
    paper_id_for_zotero_item_key,
)
from millefeuille.domain.stage_runtime import (
    ensure_no_follow_directory,
    relative_ref,
    require_safe_package_id,
)
from millefeuille.domain.summary_fixtures import load_hierarchical_summary


@dataclass(frozen=True)
class MultiSourceRunPackagePlan:
    source_pack_root: Path
    item_key: str
    run_id: str
    run_dir: Path
    stage_manifest_text: str
    artifact_index_text: str
    verified_inputs: tuple[tuple[str, int, str], ...]
    preview_sha256: str

    def preview(self) -> dict[str, object]:
        return {
            "paper_id": paper_id_for_zotero_item_key(self.item_key),
            "run_id": self.run_id,
            "verified_inputs": [
                {"ref": ref, "byte_size": size, "sha256": digest}
                for ref, size, digest in self.verified_inputs
            ],
            "files": [
                {
                    "ref": str(self.run_dir / name),
                    "byte_size": len(content.encode("utf-8")),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
                for name, content in (
                    ("stage-manifest.json", self.stage_manifest_text),
                    ("artifact-index.json", self.artifact_index_text),
                )
            ],
            "provider_calls": 0,
        }


def _json_text(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _artifact(
    kind: str, ref: str, fmt: str, stage: StageName, *, private: bool
) -> dict[str, object]:
    return {
        "kind": kind,
        "ref": ref,
        "format": fmt,
        "stage": stage.value,
        "private_content": private,
    }


_MAX_INPUT_FILES = 4096
_MAX_INPUT_FILE_BYTES = 64 * 1024 * 1024
_MAX_INPUT_TOTAL_BYTES = 512 * 1024 * 1024


def _inventory_inputs(
    root: Path, pack: Path, run: Path
) -> tuple[tuple[str, int, str], ...]:
    """Bind every saved source and consumed run artifact to the approval."""
    entries: list[tuple[str, int, str]] = []
    total_bytes = 0
    for tree in (pack, run / "summaries", run / "cards", run / "index"):
        pending = [tree]
        while pending:
            current = pending.pop()
            ensure_no_follow_directory(
                current, "run-package input directory", root=root
            )
            try:
                children = sorted(current.iterdir(), key=lambda child: child.name)
            except OSError as exc:
                raise MillefeuilleContractError(
                    f"could not list run-package input directory: {current}"
                ) from exc
            for child in children:
                try:
                    mode = child.lstat().st_mode
                except OSError as exc:
                    raise MillefeuilleContractError(
                        f"could not inspect run-package input: {child}"
                    ) from exc
                if stat.S_ISDIR(mode):
                    pending.append(child)
                elif stat.S_ISREG(mode):
                    if len(entries) >= _MAX_INPUT_FILES:
                        raise MillefeuilleContractError(
                            "run-package input file count exceeds limit"
                        )
                    payload = read_bytes_no_follow(
                        child,
                        "run-package input",
                        max_bytes=_MAX_INPUT_FILE_BYTES,
                    )
                    total_bytes += len(payload)
                    if total_bytes > _MAX_INPUT_TOTAL_BYTES:
                        raise MillefeuilleContractError(
                            "run-package input byte count exceeds limit"
                        )
                    entries.append(
                        (
                            child.relative_to(root).as_posix(),
                            len(payload),
                            hashlib.sha256(payload).hexdigest(),
                        )
                    )
                else:
                    raise MillefeuilleContractError(
                        f"run-package input is not a regular file or directory: {child}"
                    )
    return tuple(sorted(entries))


def plan_multi_source_run_package(
    *, source_pack_root: str | Path, item_key: str, run_id: str
) -> MultiSourceRunPackagePlan:
    """Verify existing upstream, summary, card and index before planning two files.

    The deployed flat run layout is selected explicitly. This function reads
    the source pack and saved outputs and never writes or calls a provider.
    """
    if re.fullmatch(r"[A-Za-z0-9]+", item_key) is None:
        raise MillefeuilleContractError("unsafe item_key")
    require_safe_package_id(run_id, "run_id")
    # Freeze relative caller paths before previewing destination and inputs.
    root = Path(os.path.abspath(source_pack_root))
    ensure_no_follow_directory(root, "source-pack root")
    paper_id = paper_id_for_zotero_item_key(item_key)
    pack = root / "zotero" / paper_id
    run = root / "analyses" / "millefeuille" / run_id
    ensure_no_follow_directory(pack, "source pack")
    ensure_no_follow_directory(run, "existing run")
    manifest = load_source_pack_manifest(pack / "manifest.json")
    source_count = len(manifest.get("sources", []))
    if (
        manifest.get("schema_version") != "millefeuille-source-pack-manifest/v0.2"
        or manifest.get("identity", {}).get("zotero_item_key") != item_key
        or manifest.get("identity", {}).get("pdf_count") != source_count
        or source_count < 2
    ):
        raise MillefeuilleContractError("multi-source pack identity drift")
    source_hash = manifest["source_hash"]
    upstream = build_multi_source_summary_package(
        route_evidence_path=pack / "selected/route.json",
        structure_evidence_path=pack / "structure/structure.json",
        output_dir=run,
        validation_only=True,
    )
    if (
        not isinstance(upstream, dict)
        or upstream["paper_id"] != paper_id
        or upstream["source_hash"] != source_hash
        or upstream["source_count"] != source_count
    ):
        raise MillefeuilleContractError("multi-source upstream evidence drift")
    if (
        upstream.get("selected_route") != "native"
        or upstream.get("structure_route") != "native"
        or upstream.get("native_evidence_ref") != "extractions/native/evidence.json"
    ):
        raise MillefeuilleContractError(
            "run package requires verified native-only extraction"
        )
    summary = _resolve_summary_refs(run_dir=run, paper_id=paper_id, run_id=run_id)
    summary_payload = load_hierarchical_summary(
        run / "summaries/hierarchical-summary.json"
    )
    if (
        summary_payload.get("schema_version") != VIEW_SCHEMA_VERSION
        or summary_payload.get("paper_id") != paper_id
        or summary_payload.get("run_id") != run_id
        or summary_payload.get("source_hash") != source_hash
    ):
        raise MillefeuilleContractError("multi-source summary source lineage drift")
    card = _resolve_card_refs(
        run_dir=run,
        paper_id=paper_id,
        run_id=run_id,
        expected_source_hash=source_hash,
    )
    index = _resolve_index_refs(
        source_pack_dir=pack,
        run_dir=run,
        paper_id=paper_id,
        run_id=run_id,
        expected_source_hash=source_hash,
    )
    required = (
        summary["summary_artifact_ref"],
        summary["summary_text_dir_ref"],
        card["paper_card_json_ref"],
        card["paper_card_markdown_ref"],
        index["retrieval_index_status_ref"],
    )
    if any(value is None for value in required):
        raise MillefeuilleContractError("existing summary, card or index is incomplete")
    index_records = index["index_records"]
    if not isinstance(index_records, list):
        raise MillefeuilleContractError("retrieval index lanes are missing")
    written_lanes = {
        record["lane"] for record in index_records if record["status"] == "written"
    }
    if "pageindex" not in written_lanes:
        raise MillefeuilleContractError("whole-pack native index is not written")
    statuses = {
        StageName.DISCOVER: StageStatus.PASSED,
        StageName.HANDOFF: StageStatus.NOT_STARTED,
        StageName.RECOVER: StageStatus.PASSED,
        StageName.SOURCE_PACK: StageStatus.PASSED,
        StageName.EXTRACT_NATIVE: StageStatus.PASSED,
        StageName.EXTRACT_OCR: StageStatus.SKIPPED,
        StageName.ROUTE: StageStatus.PASSED,
        StageName.STRUCTURE: StageStatus.PASSED,
        StageName.SUMMARIZE: StageStatus.PASSED,
        StageName.CARD: StageStatus.PASSED,
        StageName.OPENKB_ADD: (
            StageStatus.PASSED if "openkb" in written_lanes else StageStatus.SKIPPED
        ),
        StageName.INDEX: StageStatus.PASSED,
        StageName.ACCEPTANCE: StageStatus.NOT_STARTED,
        StageName.CLASSIFY: StageStatus.NOT_STARTED,
        StageName.WRITEBACK: StageStatus.NOT_STARTED,
        StageName.RELEASE: StageStatus.NOT_STARTED,
    }
    stage_manifest = StageManifest(
        run_id=run_id,
        mode=RunMode.APPROVED_LIVE,
        stages=[
            StageRecord(name=name, status=status) for name, status in statuses.items()
        ],
    )
    artifacts = {
        "stage_manifest": _artifact(
            "stage-manifest",
            "stage-manifest.json",
            "json",
            StageName.DISCOVER,
            private=False,
        ),
        "native_extraction_evidence": _artifact(
            "native-extraction-evidence",
            relative_ref(pack / "extractions/native/evidence.json", run),
            "json",
            StageName.EXTRACT_NATIVE,
            private=False,
        ),
        "native_source_texts": _artifact(
            "native-source-texts",
            relative_ref(pack / "extractions/native/sources", run),
            "directory",
            StageName.EXTRACT_NATIVE,
            private=True,
        ),
        "route_evidence": _artifact(
            "route-selection-evidence",
            relative_ref(pack / "selected/route.json", run),
            "json",
            StageName.ROUTE,
            private=False,
        ),
        "selected_fulltext": _artifact(
            "selected-fulltext",
            relative_ref(pack / "selected/fulltext.md", run),
            "markdown",
            StageName.ROUTE,
            private=True,
        ),
        "structure_evidence": _artifact(
            "structure-evidence",
            relative_ref(pack / "structure/structure.json", run),
            "json",
            StageName.STRUCTURE,
            private=False,
        ),
        "structure_outline": _artifact(
            "structure-outline",
            relative_ref(pack / "structure/outline.md", run),
            "markdown",
            StageName.STRUCTURE,
            private=True,
        ),
        "hierarchical_summary": _artifact(
            "hierarchical-summary",
            summary["summary_artifact_ref"],
            "json",
            StageName.SUMMARIZE,
            private=False,
        ),
        "summary_texts": _artifact(
            "summary-texts",
            summary["summary_text_dir_ref"],
            "directory",
            StageName.SUMMARIZE,
            private=True,
        ),
        "paper_card_json": _artifact(
            "paper-card",
            card["paper_card_json_ref"],
            "json",
            StageName.CARD,
            private=False,
        ),
        "paper_card_markdown": _artifact(
            "paper-card-markdown",
            card["paper_card_markdown_ref"],
            "markdown",
            StageName.CARD,
            private=True,
        ),
        "retrieval_index_status": _artifact(
            "retrieval-index-status",
            index["retrieval_index_status_ref"],
            "json",
            StageName.INDEX,
            private=False,
        ),
    }
    source_identity = {
        "title": manifest["identity"]["item_title"],
        "zotero_item_key": item_key,
        "pdf_count": source_count,
    }
    artifact_index = ArtifactIndex(
        paper_id=paper_id,
        run_id=run_id,
        artifact_root=str(run),
        source_pack={
            "ref": relative_ref(pack, run),
            "manifest_ref": relative_ref(pack / "manifest.json", run),
            "source_type": "zotero",
            "source_hash": source_hash,
        },
        source_identity=source_identity,
        stages={
            name.value: {"status": status.value, "manifest_ref": "stage-manifest.json"}
            for name, status in statuses.items()
        },
        artifacts=artifacts,
        indexes=index_records,
        zotero_writeback={"mode": "none", "status": "not-planned"},
    )
    artifact_index = ArtifactIndex.from_dict(artifact_index.to_dict())
    stage_text = _json_text(stage_manifest.to_dict())
    index_text = _json_text(artifact_index.to_dict())
    verified_inputs = _inventory_inputs(root, pack, run)
    plan = MultiSourceRunPackagePlan(
        source_pack_root=root,
        item_key=item_key,
        run_id=run_id,
        run_dir=run,
        stage_manifest_text=stage_text,
        artifact_index_text=index_text,
        verified_inputs=verified_inputs,
        preview_sha256="",
    )
    digest = hashlib.sha256(_json_text(plan.preview()).encode("utf-8")).hexdigest()
    return MultiSourceRunPackagePlan(
        source_pack_root=root,
        item_key=item_key,
        run_id=run_id,
        run_dir=run,
        stage_manifest_text=stage_text,
        artifact_index_text=index_text,
        verified_inputs=verified_inputs,
        preview_sha256=digest,
    )


def _approved_fresh_plan(
    plan: MultiSourceRunPackagePlan, expected_preview_sha256: str
) -> MultiSourceRunPackagePlan:
    actual_fingerprint = hashlib.sha256(
        _json_text(plan.preview()).encode("utf-8")
    ).hexdigest()
    if (
        expected_preview_sha256 != plan.preview_sha256
        or actual_fingerprint != expected_preview_sha256
    ):
        raise MillefeuilleContractError("run-package preview fingerprint drift")
    refreshed = plan_multi_source_run_package(
        source_pack_root=plan.source_pack_root,
        item_key=plan.item_key,
        run_id=plan.run_id,
    )
    if refreshed != plan:
        raise MillefeuilleContractError("run-package evidence changed after planning")
    return refreshed


def publish_multi_source_run_package(
    plan: MultiSourceRunPackagePlan, *, expected_preview_sha256: str
) -> tuple[Path, Path]:
    """Append two exact planned files after an external approval gate."""
    refreshed = _approved_fresh_plan(plan, expected_preview_sha256)
    paths = (
        refreshed.run_dir / "stage-manifest.json",
        refreshed.run_dir / "artifact-index.json",
    )
    if any(path.exists() or path.is_symlink() for path in paths):
        raise MillefeuilleContractError("run-package target already exists")
    write_new_text_no_follow(paths[0], refreshed.stage_manifest_text, "stage manifest")
    write_new_text_no_follow(paths[1], refreshed.artifact_index_text, "artifact index")
    return paths


def recover_partial_multi_source_run_package(
    plan: MultiSourceRunPackagePlan, *, expected_preview_sha256: str
) -> tuple[Path, Path]:
    """Complete a verified first-file-only publication after recovery approval."""
    refreshed = _approved_fresh_plan(plan, expected_preview_sha256)
    paths = (
        refreshed.run_dir / "stage-manifest.json",
        refreshed.run_dir / "artifact-index.json",
    )
    if paths[1].exists() or paths[1].is_symlink():
        raise MillefeuilleContractError("run-package recovery target already exists")
    expected_first = refreshed.stage_manifest_text.encode("utf-8")
    actual_first = read_bytes_no_follow(
        paths[0], "partial stage manifest", max_bytes=len(expected_first)
    )
    if actual_first != expected_first:
        raise MillefeuilleContractError("partial stage manifest differs from approval")
    write_new_text_no_follow(
        paths[1], refreshed.artifact_index_text, "artifact index recovery"
    )
    return paths
