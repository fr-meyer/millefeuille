"""Offline stage-oriented Millefeuille commands."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys
from typing import Any, TextIO

from millefeuille.domain.acceptance import (
    write_acceptance_batch_summary,
    write_acceptance_summary,
)
from millefeuille.domain.classification import (
    write_classification_action_from_evidence,
    write_classification_batch_summary,
    write_classification_from_evidence,
)
from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    ApprovedLiveRequest,
    LiveSelector,
    LiveTarget,
    load_approved_live_receipt,
    validate_approved_live_receipt,
)
from millefeuille.domain.millefeuille import (
    MillefeuilleContractError,
    RunMode,
    StageName,
)
from millefeuille.domain.model_execution import (
    SUMMARY_MODEL_STAGES,
    build_summary_execution_plan,
    materialize_model_provenance_record_from_files,
)
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE
from millefeuille.domain.offline_stages import (
    OFFLINE_FIXTURE_STAGES,
    can_resume_stage,
    write_offline_fixture_stage,
)
from millefeuille.domain.release_preflight import write_release_candidate_preflight
from millefeuille.domain.retrieve import (
    retrieve_artifact_refs,
    write_retrieval_batch_result,
)
from millefeuille.domain.stage_runtime import resolve_run_artifacts
from millefeuille.domain.writeback import write_writeback_plan

CANONICAL_RUN_STAGES = (
    StageName.EXTRACT_NATIVE.value,
    StageName.EXTRACT_OCR.value,
    StageName.ROUTE.value,
    StageName.STRUCTURE.value,
    StageName.SUMMARIZE.value,
    StageName.CARD.value,
    StageName.INDEX.value,
    StageName.ACCEPTANCE.value,
    StageName.CLASSIFY.value,
    StageName.WRITEBACK.value,
)

FIXTURE_EVIDENCE_ARGS = {
    StageName.EXTRACT_NATIVE.value: "native_extraction_evidence",
    StageName.EXTRACT_OCR.value: "ocr_extraction_evidence",
    StageName.ROUTE.value: "route_selection_evidence",
    StageName.STRUCTURE.value: "structure_evidence",
    StageName.SUMMARIZE.value: "summary_evidence",
    StageName.CARD.value: "card_evidence",
    StageName.INDEX.value: "index_evidence",
}

_LIVE_OPERATION_BY_COMMAND = {
    StageName.EXTRACT_NATIVE.value: "stage.extract-native",
    StageName.EXTRACT_OCR.value: "ocr.execute",
    StageName.ROUTE.value: "stage.route",
    StageName.STRUCTURE.value: "stage.structure",
    StageName.SUMMARIZE.value: "model.summarize",
    StageName.CARD.value: "model.card",
    StageName.INDEX.value: "index.write",
    StageName.ACCEPTANCE.value: "stage.acceptance",
    StageName.CLASSIFY.value: "model.classify",
    StageName.WRITEBACK.value: "zotero.writeback",
    "retrieve": "index.read",
    "models": "model.execute",
}


def run_stage_cli(
    argv: Sequence[str],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = _build_parser()
    args = parser.parse_args(list(argv))
    mode_exit = _mode_gate(args, err)
    if mode_exit is not None:
        return mode_exit
    try:
        if args.command in {stage.value for stage in OFFLINE_FIXTURE_STAGES}:
            result = write_offline_fixture_stage(
                stage=args.command,
                evidence_path=args.evidence,
                source_pack_root=args.source_pack_root,
                run_id=args.run_id,
                paper_id=args.paper_id,
                item_key=args.item_key,
                artifact_root=args.artifact_root,
                stage_manifest=args.stage_manifest,
            )
            payload = result.to_dict()
        elif args.command == "acceptance":
            _validate_acceptance_args(args)
            if args.batch_manifest:
                result = write_acceptance_batch_summary(
                    source_pack_root=args.source_pack_root,
                    batch_manifest_path=args.batch_manifest,
                    handoff_path=args.handoff,
                    duplicate_scan_path=args.duplicate_scans,
                )
            else:
                result = write_acceptance_summary(
                    source_pack_root=args.source_pack_root,
                    run_id=args.run_id,
                    paper_id=args.paper_id,
                    item_key=args.item_key,
                    handoff_path=args.handoff,
                    duplicate_scan_path=args.duplicate_scans,
                    artifact_root=args.artifact_root,
                    stage_manifest=args.stage_manifest,
                )
            payload = result.to_dict()
        elif args.command == "classify":
            _validate_classify_args(args)
            if args.batch_manifest:
                result = write_classification_batch_summary(
                    source_pack_root=args.source_pack_root,
                    batch_manifest_path=args.batch_manifest,
                    default_profile=args.model_profile,
                )
            elif args.action_evidence:
                result = write_classification_action_from_evidence(
                    action_evidence_path=args.action_evidence,
                    source_pack_root=args.source_pack_root,
                    run_id=args.run_id,
                    paper_id=args.paper_id,
                    item_key=args.item_key,
                    default_profile=args.model_profile,
                    artifact_root=args.artifact_root,
                    stage_manifest=args.stage_manifest,
                )
            else:
                result = write_classification_from_evidence(
                    evidence_path=args.evidence,
                    source_pack_root=args.source_pack_root,
                    run_id=args.run_id,
                    paper_id=args.paper_id,
                    item_key=args.item_key,
                    default_profile=args.model_profile,
                    artifact_root=args.artifact_root,
                    stage_manifest=args.stage_manifest,
                )
            payload = result.to_dict()
        elif args.command == "writeback":
            if args.writeback_mode == "none":
                raise MillefeuilleContractError(
                    "writeback command requires --writeback preview"
                )
            result = write_writeback_plan(
                source_pack_root=args.source_pack_root,
                run_id=args.run_id,
                paper_id=args.paper_id,
                item_key=args.item_key,
                preview_path=args.preview_path,
                artifact_root=args.artifact_root,
                stage_manifest=args.stage_manifest,
            )
            payload = result.to_dict()
        elif args.command == "retrieve":
            _validate_retrieve_args(args)
            if args.batch_manifest is not None:
                payload = write_retrieval_batch_result(
                    source_pack_root=args.source_pack_root,
                    batch_manifest_path=args.batch_manifest,
                    summary_scope=args.summary_scope,
                    grain=args.grain,
                    index_lane=args.index_lane,
                    section=args.section,
                    page=args.page,
                    evidence_need=args.evidence_need,
                )
            else:
                payload = retrieve_artifact_refs(
                    source_pack_root=args.source_pack_root,
                    run_id=args.run_id,
                    paper_id=args.paper_id,
                    item_key=args.item_key,
                    slug=args.slug,
                    doi=args.doi,
                    title=args.title,
                    summary_scope=args.summary_scope,
                    grain=args.grain,
                    index_lane=args.index_lane,
                    section=args.section,
                    page=args.page,
                    evidence_need=args.evidence_need,
                    artifact_root=args.artifact_root,
                    stage_manifest=args.stage_manifest,
                )
        elif args.command == "models":
            if args.provenance:
                if args.mode != RunMode.PREVIEW.value:
                    raise MillefeuilleContractError(
                        "models --provenance is preview-only because it "
                        "materializes local JSON evidence"
                    )
                if args.plan or args.profile or args.stage:
                    raise MillefeuilleContractError(
                        "models --provenance cannot be combined with --plan, "
                        "--profile, or --stage"
                    )
                if not args.plan_file or not args.execution_evidence:
                    raise MillefeuilleContractError(
                        "models --provenance requires --plan-file and "
                        "--execution-evidence"
                    )
                payload = materialize_model_provenance_record_from_files(
                    execution_plan_path=args.plan_file,
                    execution_evidence_path=args.execution_evidence,
                    output_path=args.output,
                )
            elif args.plan:
                if args.plan_file or args.execution_evidence or args.output:
                    raise MillefeuilleContractError(
                        "models --plan cannot be combined with provenance files"
                    )
                if not args.profile or not args.stage:
                    raise MillefeuilleContractError(
                        "models --plan requires --profile and --stage"
                    )
                payload = build_summary_execution_plan(
                    profile=args.profile,
                    stage=args.stage,
                )
            else:
                if (
                    args.profile
                    or args.stage
                    or args.plan_file
                    or args.execution_evidence
                    or args.output
                ):
                    raise MillefeuilleContractError(
                        "models --profile/--stage/provenance files require "
                        "--plan or --provenance"
                    )
                payload = DEFAULT_MODEL_PROFILE_BUNDLE
        elif args.command == "run":
            payload = _run_pipeline(args)
        else:
            parser.error(f"unsupported stage command {args.command!r}")
            return 3
    except MillefeuilleContractError as exc:
        print(f"millefeuille {args.command}: {exc}", file=err)
        return 2

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True), file=out)
    else:
        _print_payload(args.command, payload, out)
    if args.command == "acceptance" and payload.get("status") != "pass":
        return 2
    if args.command == "classify" and payload.get("status") != "classified":
        return 2
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="millefeuille",
        description="Offline stage-oriented Millefeuille commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fixture_stage_help = {
        StageName.EXTRACT_NATIVE.value: (
            "Write verified native-extraction artifacts from local fixture evidence."
        ),
        StageName.EXTRACT_OCR.value: (
            "Write verified OCR artifacts from local fixture evidence without a "
            "provider call."
        ),
        StageName.ROUTE.value: (
            "Write verified route-selection artifacts from local fixture evidence."
        ),
        StageName.STRUCTURE.value: (
            "Write verified structure artifacts from local fixture evidence."
        ),
        StageName.SUMMARIZE.value: (
            "Write verified hierarchical summaries from local fixture evidence."
        ),
        StageName.CARD.value: (
            "Write verified paper-card artifacts from local fixture evidence."
        ),
        StageName.INDEX.value: (
            "Write verified retrieval/index status from local fixture evidence."
        ),
    }
    for stage_name in CANONICAL_RUN_STAGES[:7]:
        fixture_stage = subparsers.add_parser(
            stage_name,
            help=fixture_stage_help[stage_name],
        )
        _add_run_locator_args(fixture_stage)
        fixture_stage.add_argument("--evidence", required=True)
        fixture_stage.add_argument("--json", action="store_true")

    acceptance = subparsers.add_parser(
        "acceptance",
        help="Synthesize run-scoped or offline batch acceptance artifacts.",
    )
    _add_mode_arg(acceptance)
    acceptance.add_argument("--source-pack-root", required=True)
    _add_artifact_locator_args(acceptance)
    acceptance_source = acceptance.add_mutually_exclusive_group()
    acceptance_source.add_argument("--paper-id")
    acceptance_source.add_argument("--item-key")
    acceptance.add_argument("--run-id")
    acceptance.add_argument(
        "--batch-manifest",
        help=(
            "Path to a millefeuille-acceptance-batch-manifest/v0.1 JSON file; "
            "cannot be combined with single-run locators."
        ),
    )
    acceptance.add_argument(
        "--handoff",
        required=True,
        help="Path to handoff JSONL evidence for this paper.",
    )
    acceptance.add_argument(
        "--duplicate-scans",
        help="Optional duplicate-scan JSONL evidence.",
    )
    acceptance.add_argument("--json", action="store_true")

    classify = subparsers.add_parser(
        "classify",
        help=(
            "Write run-scoped or deterministic offline batch classification "
            "preview artifacts."
        ),
    )
    _add_mode_arg(classify)
    classify.add_argument("--source-pack-root", required=True)
    _add_artifact_locator_args(classify)
    classify_source = classify.add_mutually_exclusive_group()
    classify_source.add_argument("--paper-id")
    classify_source.add_argument("--item-key")
    classify.add_argument("--run-id")
    classify_input = classify.add_mutually_exclusive_group()
    classify_input.add_argument("--evidence")
    classify_input.add_argument(
        "--batch-manifest",
        help=(
            "Path to a millefeuille-classification-batch-manifest/v0.1 JSON "
            "file; cannot be combined with single-run locators or --evidence."
        ),
    )
    classify_input.add_argument(
        "--action-evidence",
        help=(
            "Path to a millefeuille-classification-action-evidence/v0.1 JSON "
            "file for one lineage-checked review or adjudication action."
        ),
    )
    classify.add_argument("--model-profile")
    classify.add_argument("--json", action="store_true")

    writeback = subparsers.add_parser(
        "writeback",
        help="Materialize preview-only Zotero writeback plans.",
    )
    _add_run_locator_args(writeback)
    writeback.add_argument(
        "--writeback",
        dest="writeback_mode",
        choices=("none", "preview", "approved-live"),
        default="preview",
    )
    writeback.add_argument("--preview-path")
    writeback.add_argument("--json", action="store_true")

    retrieve = subparsers.add_parser(
        "retrieve",
        help="Return refs into an existing Millefeuille artifact package.",
    )
    _add_mode_arg(retrieve)
    retrieve.add_argument("--source-pack-root", required=True)
    _add_artifact_locator_args(retrieve)
    retrieve_source = retrieve.add_mutually_exclusive_group()
    retrieve_source.add_argument("--paper-id")
    retrieve_source.add_argument("--item-key")
    retrieve_source.add_argument("--slug")
    retrieve_source.add_argument("--doi")
    retrieve_source.add_argument("--title")
    retrieve.add_argument("--run-id")
    retrieve.add_argument(
        "--batch-manifest",
        help=(
            "Path to a millefeuille-retrieval-batch-manifest/v0.1 JSON file; "
            "cannot be combined with single-run locators or --run-id."
        ),
    )
    retrieve.add_argument("--summary-scope")
    retrieve.add_argument("--grain")
    retrieve.add_argument("--index-lane")
    retrieve.add_argument("--section")
    retrieve.add_argument("--page", type=int)
    retrieve.add_argument(
        "--evidence-need",
        choices=("classification",),
    )
    retrieve.add_argument("--json", action="store_true")

    models = subparsers.add_parser(
        "models",
        help="List profiles or build a deterministic no-call summary plan.",
    )
    _add_mode_arg(models)
    models.add_argument(
        "--plan",
        action="store_true",
        help="Resolve one summary-stage execution plan without a provider call.",
    )
    models.add_argument(
        "--provenance",
        action="store_true",
        help=(
            "Validate model execution evidence against a plan and materialize "
            "one safe provenance JSON record."
        ),
    )
    models.add_argument("--profile")
    models.add_argument("--stage", choices=SUMMARY_MODEL_STAGES)
    models.add_argument("--plan-file")
    models.add_argument("--execution-evidence")
    models.add_argument("--output")
    models.add_argument("--json", action="store_true")

    run = subparsers.add_parser(
        "run",
        help=(
            "Sequence the resumable offline fixture pipeline from extraction "
            "through preview writeback."
        ),
    )
    _add_run_locator_args(run)
    run.add_argument(
        "--stages",
        required=True,
        help=(
            "Comma-separated canonical stage list from extract-native through "
            "writeback."
        ),
    )
    run.add_argument("--native-extraction-evidence")
    run.add_argument("--ocr-extraction-evidence")
    run.add_argument("--route-selection-evidence")
    run.add_argument("--structure-evidence")
    run.add_argument("--summary-evidence")
    run.add_argument("--card-evidence")
    run.add_argument("--index-evidence")
    run.add_argument("--handoff")
    run.add_argument("--duplicate-scans")
    run.add_argument("--classification-evidence")
    run.add_argument("--model-profile")
    run.add_argument(
        "--writeback",
        dest="writeback_mode",
        choices=("none", "preview", "approved-live"),
        default="preview",
    )
    run.add_argument(
        "--resume",
        action="store_true",
        help="Skip passed stages only after their matching outputs revalidate.",
    )
    run.add_argument(
        "--release-preflight",
        action="store_true",
        help="Also write local release-candidate preflight artifacts.",
    )
    run.add_argument("--candidate-version")
    run.add_argument("--json", action="store_true")
    return parser


def _add_run_locator_args(parser: argparse.ArgumentParser) -> None:
    _add_mode_arg(parser)
    parser.add_argument("--source-pack-root", required=True)
    _add_artifact_locator_args(parser)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--paper-id")
    source.add_argument("--item-key")
    parser.add_argument("--run-id", required=True)


def _add_artifact_locator_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--artifact-root",
        help=(
            "Run-package directory or declared container root. Defaults to the "
            "verified source-pack run; 'source-pack' explicitly selects it."
        ),
    )
    parser.add_argument(
        "--stage-manifest",
        help=(
            "Exact stage-manifest file inside the selected run package. The "
            "artifact index must reference the same file."
        ),
    )


def _add_mode_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mode",
        choices=tuple(sorted(RunMode.values())),
        default=RunMode.PREVIEW.value,
    )
    parser.add_argument(
        "--approval-receipt",
        help=(
            "Path to an exact-scope approved-live receipt. A receipt never "
            "promotes preview mode and current live execution remains unsupported."
        ),
    )


def _run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    requested = [stage.strip() for stage in args.stages.split(",") if stage.strip()]
    _preflight_run_request(args, requested)

    results: dict[str, Any] = {"stages": requested, "resumed": []}
    for stage in requested:
        if args.resume:
            resolved = resolve_run_artifacts(
                source_pack_root=args.source_pack_root,
                run_id=args.run_id,
                paper_id=args.paper_id,
                item_key=args.item_key,
                artifact_root=args.artifact_root,
                stage_manifest=args.stage_manifest,
            )
            if can_resume_stage(resolved, stage):
                results[stage] = {"status": "resumed"}
                results["resumed"].append(stage)
                continue

        if stage in FIXTURE_EVIDENCE_ARGS:
            evidence_path = getattr(args, FIXTURE_EVIDENCE_ARGS[stage])
            result = write_offline_fixture_stage(
                stage=stage,
                evidence_path=evidence_path,
                source_pack_root=args.source_pack_root,
                run_id=args.run_id,
                paper_id=args.paper_id,
                item_key=args.item_key,
                artifact_root=args.artifact_root,
                stage_manifest=args.stage_manifest,
            )
            results[stage] = result.to_dict()
        elif stage == StageName.ACCEPTANCE.value:
            result = write_acceptance_summary(
                source_pack_root=args.source_pack_root,
                run_id=args.run_id,
                paper_id=args.paper_id,
                item_key=args.item_key,
                artifact_root=args.artifact_root,
                stage_manifest=args.stage_manifest,
                handoff_path=args.handoff,
                duplicate_scan_path=args.duplicate_scans,
            )
            results["acceptance"] = result.to_dict()
            if result.status != "pass":
                raise MillefeuilleContractError(
                    "acceptance needs review; later stages were not executed"
                )
        elif stage == StageName.CLASSIFY.value:
            result = write_classification_from_evidence(
                evidence_path=args.classification_evidence,
                source_pack_root=args.source_pack_root,
                run_id=args.run_id,
                paper_id=args.paper_id,
                item_key=args.item_key,
                artifact_root=args.artifact_root,
                stage_manifest=args.stage_manifest,
                default_profile=args.model_profile,
            )
            results["classify"] = result.to_dict()
            if result.status != "classified":
                raise MillefeuilleContractError(
                    "classification needs review; writeback was not executed"
                )
        elif stage == StageName.WRITEBACK.value:
            result = write_writeback_plan(
                source_pack_root=args.source_pack_root,
                run_id=args.run_id,
                paper_id=args.paper_id,
                item_key=args.item_key,
                artifact_root=args.artifact_root,
                stage_manifest=args.stage_manifest,
            )
            results["writeback"] = result.to_dict()

    if args.release_preflight:
        results["release_preflight"] = write_release_candidate_preflight(
            source_pack_root=args.source_pack_root,
            run_id=args.run_id,
            paper_id=args.paper_id,
            item_key=args.item_key,
            repo_root=Path(__file__).resolve().parents[2],
            candidate_version=args.candidate_version,
            artifact_root=args.artifact_root,
            stage_manifest=args.stage_manifest,
        ).to_dict()
    return results


def _preflight_run_request(
    args: argparse.Namespace,
    requested: list[str],
) -> None:
    if not requested:
        raise MillefeuilleContractError("run stages must not be empty")
    unsupported = [stage for stage in requested if stage not in CANONICAL_RUN_STAGES]
    if unsupported:
        raise MillefeuilleContractError(
            f"unsupported run stages: {', '.join(sorted(set(unsupported)))}"
        )
    if len(requested) != len(set(requested)):
        raise MillefeuilleContractError("run stages must not contain duplicates")
    positions = [CANONICAL_RUN_STAGES.index(stage) for stage in requested]
    if positions != sorted(positions):
        raise MillefeuilleContractError(
            "run stages must follow canonical pipeline order: "
            + ",".join(CANONICAL_RUN_STAGES)
        )

    required_args = {
        **FIXTURE_EVIDENCE_ARGS,
        StageName.ACCEPTANCE.value: "handoff",
        StageName.CLASSIFY.value: "classification_evidence",
    }
    missing: list[str] = []
    resolved_for_resume = None
    for stage in requested:
        argument_name = required_args.get(stage)
        if argument_name is None or getattr(args, argument_name):
            continue
        if args.resume:
            if resolved_for_resume is None:
                resolved_for_resume = resolve_run_artifacts(
                    source_pack_root=args.source_pack_root,
                    run_id=args.run_id,
                    paper_id=args.paper_id,
                    item_key=args.item_key,
                    artifact_root=args.artifact_root,
                    stage_manifest=args.stage_manifest,
                )
            if can_resume_stage(resolved_for_resume, stage):
                continue
        missing.append(f"--{argument_name.replace('_', '-')}")
    if missing:
        raise MillefeuilleContractError(
            "run is missing required stage evidence: " + ", ".join(missing)
        )
    if StageName.WRITEBACK.value in requested and args.writeback_mode != "preview":
        raise MillefeuilleContractError(
            "run stage writeback requires --writeback preview"
        )


def _mode_gate(args: argparse.Namespace, err: TextIO) -> int | None:
    mode = getattr(args, "mode", RunMode.PREVIEW.value)
    receipt_path = getattr(args, "approval_receipt", None)
    writeback_mode = getattr(args, "writeback_mode", "preview")
    if receipt_path is not None and mode != RunMode.APPROVED_LIVE.value:
        print(
            f"millefeuille {args.command}: --approval-receipt requires explicit "
            "--mode approved-live; a receipt cannot promote preview or "
            "read-only-live execution",
            file=err,
        )
        return 3
    if writeback_mode == "approved-live" and mode != RunMode.APPROVED_LIVE.value:
        print(
            f"millefeuille {args.command}: --writeback approved-live requires "
            "explicit --mode approved-live and a separate manual approval receipt",
            file=err,
        )
        return 3
    if mode == RunMode.APPROVED_LIVE.value:
        if receipt_path is None:
            print(
                f"millefeuille {args.command}: approved-live requires a separate "
                "manual approval receipt via --approval-receipt",
                file=err,
            )
            return 3
        if _cli_requests_writeback(args) and writeback_mode != "approved-live":
            print(
                f"millefeuille {args.command}: approved-live writeback requires "
                "explicit --writeback approved-live as well as the approval receipt",
                file=err,
            )
            return 3
        try:
            receipt = load_approved_live_receipt(receipt_path)
            request = _build_cli_approved_live_request(args, receipt)
            validate_approved_live_receipt(receipt, request)
        except MillefeuilleContractError as exc:
            print(f"millefeuille {args.command}: {exc}", file=err)
            return 3
        print(
            f"millefeuille {args.command}: approved-live receipt "
            f"{receipt.content_digest} validated, but live execution is not "
            "implemented by this offline command",
            file=err,
        )
        return 3
    if mode == RunMode.READ_ONLY_LIVE.value and args.command not in {
        "retrieve",
        "models",
    }:
        print(
            f"millefeuille {args.command}: read-only-live is available only for "
            "retrieve and models; this command writes derived preview artifacts",
            file=err,
        )
        return 3
    return None


def _cli_requests_writeback(args: argparse.Namespace) -> bool:
    if args.command == StageName.WRITEBACK.value:
        return True
    if args.command != "run":
        return False
    return StageName.WRITEBACK.value in {
        stage.strip() for stage in args.stages.split(",") if stage.strip()
    }


def _build_cli_approved_live_request(
    args: argparse.Namespace,
    receipt: ApprovedLiveReceipt,
) -> ApprovedLiveRequest:
    """Bind the controls exposed by today's unsupported stage surface.

    Disposal and stop policies have no local execution counterpart yet, so the
    structural values come from the validated receipt. Future live adapters
    must derive every request field from their execution plan instead.
    """

    operations = _cli_live_operations(args)
    target, selector = _cli_live_target_and_selector(args)
    run_id = getattr(args, "run_id", None)
    if not isinstance(run_id, str) or not run_id:
        raise MillefeuilleContractError(
            "current approved-live gate cannot bind an exact run_id"
        )
    source_pack_root = getattr(args, "source_pack_root", None)
    if not isinstance(source_pack_root, str) or not source_pack_root:
        raise MillefeuilleContractError(
            "current approved-live gate cannot bind an exact source-pack root"
        )
    canonical_root = str(Path(source_pack_root).absolute())
    return ApprovedLiveRequest(
        run_id=run_id,
        operations=operations,
        targets=(target,),
        item_cap=1,
        selected_item_count=1,
        selector=selector,
        output_root=canonical_root,
        source_pack_root=canonical_root,
        provider=None,
        provider_call_limit=0,
        cost_limit_usd_micros=0,
        disposal_policy=receipt.scope.disposal_policy,
        stop_conditions=receipt.scope.stop_conditions,
    )


def _cli_live_operations(args: argparse.Namespace) -> tuple[str, ...]:
    if args.command == "run":
        requested = [stage.strip() for stage in args.stages.split(",") if stage.strip()]
        try:
            operations = {_LIVE_OPERATION_BY_COMMAND[stage] for stage in requested}
        except KeyError as exc:
            raise MillefeuilleContractError(
                "current approved-live gate cannot bind an unsupported run stage"
            ) from exc
        if not operations:
            raise MillefeuilleContractError(
                "current approved-live gate requires at least one exact operation"
            )
        return tuple(sorted(operations))
    operation = _LIVE_OPERATION_BY_COMMAND.get(args.command)
    if operation is None:
        raise MillefeuilleContractError(
            "current approved-live gate cannot bind an exact operation"
        )
    return (operation,)


def _cli_live_target_and_selector(
    args: argparse.Namespace,
) -> tuple[LiveTarget, LiveSelector]:
    locators = (
        ("paper-id", "paper_id"),
        ("zotero-item-key", "item_key"),
        ("slug", "slug"),
        ("doi", "doi"),
        ("title", "title"),
        ("source-pack", "source_pack"),
    )
    supplied = [
        (kind, value)
        for kind, attribute in locators
        if isinstance(value := getattr(args, attribute, None), str) and value
    ]
    if len(supplied) != 1:
        raise MillefeuilleContractError(
            "current approved-live gate requires one exact source selector"
        )
    kind, value = supplied[0]
    return LiveTarget(kind=kind, id=value), LiveSelector(kind=kind, value=value)


def _validate_acceptance_args(args: argparse.Namespace) -> None:
    single_locator_supplied = bool(args.paper_id or args.item_key or args.run_id)
    if args.batch_manifest:
        _reject_batch_artifact_overrides(args, "acceptance")
        if single_locator_supplied:
            raise MillefeuilleContractError(
                "acceptance --batch-manifest cannot be combined with "
                "--paper-id, --item-key, or --run-id"
            )
        return
    if not args.run_id or not (args.paper_id or args.item_key):
        raise MillefeuilleContractError(
            "acceptance requires --batch-manifest or a single-run locator "
            "(--paper-id|--item-key plus --run-id)"
        )


def _validate_classify_args(args: argparse.Namespace) -> None:
    single_locator_supplied = bool(args.paper_id or args.item_key or args.run_id)
    if args.batch_manifest:
        _reject_batch_artifact_overrides(args, "classify")
        if single_locator_supplied:
            raise MillefeuilleContractError(
                "classify --batch-manifest cannot be combined with --paper-id, "
                "--item-key, or --run-id"
            )
        return
    if not args.run_id or not (args.paper_id or args.item_key):
        raise MillefeuilleContractError(
            "classify requires --batch-manifest or a single-run locator "
            "(--paper-id|--item-key plus --run-id)"
        )
    if not (args.evidence or args.action_evidence):
        raise MillefeuilleContractError(
            "single-run classify requires --evidence or --action-evidence"
        )


def _validate_retrieve_args(args: argparse.Namespace) -> None:
    locator_values = (
        args.paper_id,
        args.item_key,
        args.slug,
        args.doi,
        args.title,
    )
    single_locator_supplied = any(value is not None for value in locator_values)
    batch_manifest_supplied = args.batch_manifest is not None
    if batch_manifest_supplied:
        _reject_batch_artifact_overrides(args, "retrieve")
        if not isinstance(args.batch_manifest, str) or not args.batch_manifest.strip():
            raise MillefeuilleContractError(
                "retrieve --batch-manifest must not be empty"
            )
        args.batch_manifest = args.batch_manifest.strip()
        if single_locator_supplied or args.run_id is not None:
            raise MillefeuilleContractError(
                "retrieve --batch-manifest cannot be combined with --paper-id, "
                "--item-key, --slug, --doi, --title, or --run-id"
            )
        if args.mode != RunMode.PREVIEW.value:
            raise MillefeuilleContractError(
                "retrieve --batch-manifest is preview-only because it writes "
                "local aggregate artifacts"
            )
        return
    if not single_locator_supplied or args.run_id is None:
        raise MillefeuilleContractError(
            "retrieve requires --batch-manifest or a single-run locator "
            "(--paper-id|--item-key|--slug|--doi|--title plus --run-id)"
        )


def _reject_batch_artifact_overrides(
    args: argparse.Namespace,
    command: str,
) -> None:
    if args.artifact_root is not None or args.stage_manifest is not None:
        raise MillefeuilleContractError(
            f"{command} --batch-manifest cannot be combined with "
            "--artifact-root or --stage-manifest; each batch entry resolves its "
            "canonical source-pack run"
        )


def _print_payload(command: str, payload: dict[str, Any], out: TextIO) -> None:
    print(f"Millefeuille {command}", file=out)
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, sort_keys=True)}", file=out)
        else:
            print(f"{key}: {value}", file=out)
