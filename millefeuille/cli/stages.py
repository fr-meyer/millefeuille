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
from millefeuille.domain.millefeuille import (
    MillefeuilleContractError,
    RunMode,
    StageName,
)
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE
from millefeuille.domain.offline_stages import (
    OFFLINE_FIXTURE_STAGES,
    can_resume_stage,
    write_offline_fixture_stage,
)
from millefeuille.domain.release_preflight import write_release_candidate_preflight
from millefeuille.domain.retrieve import retrieve_artifact_refs
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
                )
            else:
                result = write_classification_from_evidence(
                    evidence_path=args.evidence,
                    source_pack_root=args.source_pack_root,
                    run_id=args.run_id,
                    paper_id=args.paper_id,
                    item_key=args.item_key,
                    default_profile=args.model_profile,
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
            )
            payload = result.to_dict()
        elif args.command == "retrieve":
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
            )
        elif args.command == "models":
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
    retrieve_source = retrieve.add_mutually_exclusive_group(required=True)
    retrieve_source.add_argument("--paper-id")
    retrieve_source.add_argument("--item-key")
    retrieve_source.add_argument("--slug")
    retrieve_source.add_argument("--doi")
    retrieve_source.add_argument("--title")
    retrieve.add_argument("--run-id", required=True)
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
        help="List bundled preview model profiles.",
    )
    _add_mode_arg(models)
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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--paper-id")
    source.add_argument("--item-key")
    parser.add_argument("--run-id", required=True)


def _add_mode_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mode",
        choices=tuple(sorted(RunMode.values())),
        default=RunMode.PREVIEW.value,
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
            )
            results[stage] = result.to_dict()
        elif stage == StageName.ACCEPTANCE.value:
            result = write_acceptance_summary(
                source_pack_root=args.source_pack_root,
                run_id=args.run_id,
                paper_id=args.paper_id,
                item_key=args.item_key,
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
    if mode == RunMode.APPROVED_LIVE.value:
        print(
            f"millefeuille {args.command}: approved-live requires a separate "
            "manual approval and is not implemented by this offline command",
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
    writeback_mode = getattr(args, "writeback_mode", "preview")
    if writeback_mode == "approved-live":
        print(
            f"millefeuille {args.command}: approved-live Zotero writeback "
            "requires a separate manual approval and is not implemented",
            file=err,
        )
        return 3
    return None


def _validate_acceptance_args(args: argparse.Namespace) -> None:
    single_locator_supplied = bool(args.paper_id or args.item_key or args.run_id)
    if args.batch_manifest:
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


def _print_payload(command: str, payload: dict[str, Any], out: TextIO) -> None:
    print(f"Millefeuille {command}", file=out)
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, sort_keys=True)}", file=out)
        else:
            print(f"{key}: {value}", file=out)
