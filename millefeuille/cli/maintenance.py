"""Evidence-safe local maintenance commands."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from io import TextIOBase
import json
import sys

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.staging_cleanup import (
    STAGING_CLEANUP_DISPOSAL,
    STAGING_CLEANUP_STOP_CONDITIONS,
    CleanupApplyError,
    apply_staging_cleanup_plan,
    build_staging_cleanup_plan,
    inspect_staging_cleanup,
)


def run_maintenance_cli(
    argv: Sequence[str],
    *,
    stdout: TextIOBase | None = None,
    stderr: TextIOBase | None = None,
) -> int:
    """Run the explicit local maintenance surface."""

    out = stdout or sys.stdout
    err = stderr or sys.stderr
    raw_args = list(argv)
    if raw_args and raw_args[0] == "maintenance":
        raw_args = raw_args[1:]
    parser = _build_parser()
    args = parser.parse_args(raw_args)
    try:
        if args.action == "inspect":
            payload = inspect_staging_cleanup(
                source_root=args.source_root,
                quarantine_root=args.quarantine_root,
            )
        elif args.action == "plan":
            payload = build_staging_cleanup_plan(
                source_root=args.source_root,
                quarantine_root=args.quarantine_root,
                run_id=args.run_id,
                candidate_paths=args.candidate,
                expected_candidate_count=args.candidate_count,
                disposal=args.disposal,
                stop_conditions=args.stop_condition,
            )
        elif args.action == "apply":
            if args.mode != "approved-live":
                print(
                    "millefeuille maintenance staging apply: local mutation "
                    "requires explicit --mode approved-live",
                    file=err,
                )
                return 3
            payload = apply_staging_cleanup_plan(
                plan_path=args.plan,
                source_root=args.source_root,
                quarantine_root=args.quarantine_root,
                approval_receipt_path=args.approval_receipt,
            )
        else:  # pragma: no cover - argparse enforces the action
            parser.error(f"unsupported staging maintenance action {args.action!r}")
            return 3
    except CleanupApplyError as exc:
        _print_payload(exc.disposition, out=out, as_json=args.json)
        print(f"millefeuille maintenance staging apply: {exc}", file=err)
        return 2
    except MillefeuilleContractError as exc:
        print(f"millefeuille maintenance staging {args.action}: {exc}", file=err)
        return 2

    _print_payload(payload, out=out, as_json=args.json)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="millefeuille maintenance",
        description="Inspect, plan, or quarantine verified abandoned staging.",
    )
    namespace = parser.add_subparsers(dest="namespace", required=True)
    staging = namespace.add_parser(
        "staging",
        help="Evidence-safe retrieval/bridge staging maintenance.",
    )
    actions = staging.add_subparsers(dest="action", required=True)

    inspect_parser = actions.add_parser(
        "inspect",
        help="Read-only inspection of known temporary namespaces.",
    )
    _add_roots(inspect_parser)
    _add_json(inspect_parser)

    plan_parser = actions.add_parser(
        "plan",
        help="Build a strict content-addressed read-only quarantine plan.",
    )
    _add_roots(plan_parser)
    plan_parser.add_argument("--run-id", required=True)
    plan_parser.add_argument(
        "--candidate",
        action="append",
        required=True,
        help="Exact source-root-relative candidate; repeat for each candidate.",
    )
    plan_parser.add_argument("--candidate-count", required=True, type=int)
    plan_parser.add_argument(
        "--disposal",
        required=True,
        choices=(STAGING_CLEANUP_DISPOSAL,),
    )
    plan_parser.add_argument(
        "--stop-condition",
        action="append",
        required=True,
        choices=STAGING_CLEANUP_STOP_CONDITIONS,
        help="Repeat in sorted order for the complete supported stop set.",
    )
    _add_json(plan_parser)

    apply_parser = actions.add_parser(
        "apply",
        help="Receipt-gated atomic no-replace quarantine; never deletes.",
    )
    _add_roots(apply_parser)
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--approval-receipt", required=True)
    apply_parser.add_argument(
        "--mode",
        default="preview",
        choices=("preview", "approved-live"),
        help="Mutation requires the explicit approved-live value.",
    )
    _add_json(apply_parser)
    return parser


def _add_roots(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-root",
        required=True,
        help="Exact absolute root containing known temporary namespaces.",
    )
    parser.add_argument(
        "--quarantine-root",
        required=True,
        help="Exact absolute existing same-filesystem quarantine root.",
    )


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit strict JSON.")


def _print_payload(
    payload: dict[str, object],
    *,
    out: TextIOBase,
    as_json: bool,
) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True), file=out)
        return
    print(f"schema_version: {payload['schema_version']}", file=out)
    if "status" in payload:
        print(f"status: {payload['status']}", file=out)
    if "counts" in payload:
        counts = payload["counts"]
        if isinstance(counts, dict):
            print(f"eligible: {counts.get('eligible', 0)}", file=out)
            print(f"unverified: {counts.get('unverified', 0)}", file=out)
    integrity = payload.get("integrity")
    if isinstance(integrity, dict) and "content_digest" in integrity:
        print(f"content_digest: {integrity['content_digest']}", file=out)
