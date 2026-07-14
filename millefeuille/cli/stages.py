"""Offline stage-oriented Millefeuille commands."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys
from typing import Any, TextIO

from millefeuille.domain.acceptance import write_acceptance_summary
from millefeuille.domain.classification import write_classification_from_evidence
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE
from millefeuille.domain.release_preflight import write_release_candidate_preflight
from millefeuille.domain.retrieve import retrieve_artifact_refs
from millefeuille.domain.writeback import write_writeback_plan


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
    try:
        if args.command == "acceptance":
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
                summary_scope=args.summary_scope,
                grain=args.grain,
                index_lane=args.index_lane,
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
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="millefeuille",
        description="Offline stage-oriented Millefeuille commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    acceptance = subparsers.add_parser(
        "acceptance",
        help="Synthesize run-scoped acceptance summary artifacts.",
    )
    _add_run_locator_args(acceptance)
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
        help="Write offline classification preview artifacts from explicit evidence.",
    )
    _add_run_locator_args(classify)
    classify.add_argument("--evidence", required=True)
    classify.add_argument("--model-profile")
    classify.add_argument("--json", action="store_true")

    writeback = subparsers.add_parser(
        "writeback",
        help="Materialize preview-only Zotero writeback plans.",
    )
    _add_run_locator_args(writeback)
    writeback.add_argument("--preview-path")
    writeback.add_argument("--json", action="store_true")

    retrieve = subparsers.add_parser(
        "retrieve",
        help="Return refs into an existing Millefeuille artifact package.",
    )
    _add_run_locator_args(retrieve)
    retrieve.add_argument("--summary-scope")
    retrieve.add_argument("--grain")
    retrieve.add_argument("--index-lane")
    retrieve.add_argument("--json", action="store_true")

    models = subparsers.add_parser(
        "models",
        help="List bundled preview model profiles.",
    )
    models.add_argument("--json", action="store_true")

    run = subparsers.add_parser(
        "run",
        help=(
            "Sequence offline acceptance, classify, writeback, and "
            "release-preflight stages."
        ),
    )
    _add_run_locator_args(run)
    run.add_argument(
        "--stages",
        required=True,
        help="Comma-separated stage list. Supported: acceptance,classify,writeback",
    )
    run.add_argument("--handoff")
    run.add_argument("--duplicate-scans")
    run.add_argument("--classification-evidence")
    run.add_argument("--model-profile")
    run.add_argument(
        "--release-preflight",
        action="store_true",
        help="Also write local release-candidate preflight artifacts.",
    )
    run.add_argument("--candidate-version")
    run.add_argument("--json", action="store_true")
    return parser


def _add_run_locator_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-pack-root", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--paper-id")
    source.add_argument("--item-key")
    parser.add_argument("--run-id", required=True)


def _run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    requested = [stage.strip() for stage in args.stages.split(",") if stage.strip()]
    supported = {"acceptance", "classify", "writeback"}
    unsupported = [stage for stage in requested if stage not in supported]
    if unsupported:
        raise MillefeuilleContractError(
            f"unsupported run stages: {', '.join(sorted(unsupported))}"
        )

    results: dict[str, Any] = {"stages": requested}
    for stage in requested:
        if stage == "acceptance":
            if not args.handoff:
                raise MillefeuilleContractError(
                    "run stage acceptance requires --handoff"
                )
            result = write_acceptance_summary(
                source_pack_root=args.source_pack_root,
                run_id=args.run_id,
                paper_id=args.paper_id,
                item_key=args.item_key,
                handoff_path=args.handoff,
                duplicate_scan_path=args.duplicate_scans,
            )
            results["acceptance"] = result.to_dict()
        elif stage == "classify":
            if not args.classification_evidence:
                raise MillefeuilleContractError(
                    "run stage classify requires --classification-evidence"
                )
            result = write_classification_from_evidence(
                evidence_path=args.classification_evidence,
                source_pack_root=args.source_pack_root,
                run_id=args.run_id,
                paper_id=args.paper_id,
                item_key=args.item_key,
                default_profile=args.model_profile,
            )
            results["classify"] = result.to_dict()
        elif stage == "writeback":
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


def _print_payload(command: str, payload: dict[str, Any], out: TextIO) -> None:
    print(f"Millefeuille {command}", file=out)
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, sort_keys=True)}", file=out)
        else:
            print(f"{key}: {value}", file=out)
