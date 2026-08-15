"""Offline CLI for deterministic taxonomy registry governance."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import sys
from typing import Any, TextIO

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import load_json_object_no_follow
from millefeuille.domain.taxonomy import (
    apply_taxonomy_change,
    create_taxonomy_change_proposal,
    create_taxonomy_change_review,
    create_taxonomy_lock,
    load_taxonomy_change_proposal,
    load_taxonomy_change_review,
    load_taxonomy_lock,
    load_taxonomy_registry,
    rollback_taxonomy_change,
    seal_taxonomy_registry,
)


def run_taxonomy_cli(
    argv: Sequence[str],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run local taxonomy artifact commands without live calls or mutation."""

    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = _build_parser()
    args = parser.parse_args(list(argv))
    try:
        payload = _run_command(args)
    except MillefeuilleContractError as exc:
        print(f"millefeuille taxonomy {args.taxonomy_command}: {exc}", file=err)
        return 2
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), file=out)
    return 0


def _run_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.taxonomy_command == "seal":
        draft = load_json_object_no_follow(
            args.registry_draft, "taxonomy registry draft"
        )
        return seal_taxonomy_registry(draft)

    if args.taxonomy_command == "validate":
        registry = load_taxonomy_registry(args.registry)
        payload: dict[str, Any] = {
            "status": "valid",
            "registry_id": registry["registry_id"],
            "taxonomy_version": registry["taxonomy_version"],
            "registry_content_identity": registry["content_identity"],
            "entry_count": len(registry["entries"]),
        }
        if args.lock:
            lock = load_taxonomy_lock(args.lock, registry=registry)
            payload["lock_id"] = lock["lock_id"]
            payload["lock_content_identity"] = lock["content_identity"]
        return payload

    if args.taxonomy_command == "lock":
        return create_taxonomy_lock(
            load_taxonomy_registry(args.registry),
            lock_id=args.lock_id,
            scope_type=args.scope_type,
            scope_id=args.scope_id,
            locked_by=args.locked_by,
            locked_at=args.locked_at,
        )

    if args.taxonomy_command == "propose":
        base = load_taxonomy_registry(args.base_registry)
        candidate = load_taxonomy_registry(args.candidate_registry)
        rollback_source = (
            None
            if args.rollback_source_registry is None
            else load_taxonomy_registry(args.rollback_source_registry)
        )
        return create_taxonomy_change_proposal(
            base_registry=base,
            candidate_registry=candidate,
            change_id=args.change_id,
            operation=args.operation,
            affected_entry_ids=args.affected_entry_id,
            reason=args.reason,
            evidence_refs=args.evidence_ref,
            impact_risk=args.impact_risk,
            impact_summary=args.impact_summary,
            estimated_affected_records=args.estimated_affected_records,
            historical_reclassification=args.historical_reclassification,
            migration_instructions=args.migration_instructions,
            requested_by=args.requested_by,
            requested_at=args.requested_at,
            rollback_source_registry=rollback_source,
        )

    if args.taxonomy_command == "review":
        base = load_taxonomy_registry(args.base_registry)
        proposal = load_taxonomy_change_proposal(args.proposal, base_registry=base)
        return create_taxonomy_change_review(
            proposal,
            base_registry=base,
            review_id=args.review_id,
            role=args.role,
            decision=args.decision,
            reviewer_id=args.reviewer_id,
            reviewed_at=args.reviewed_at,
            notes=args.notes,
        )

    base = load_taxonomy_registry(args.base_registry)
    proposal = load_taxonomy_change_proposal(args.proposal, base_registry=base)
    reviews = [
        load_taxonomy_change_review(path, proposal=proposal) for path in args.review
    ]
    if args.taxonomy_command == "rollback":
        result_registry, application = rollback_taxonomy_change(
            base_registry=base,
            rollback_source_registry=load_taxonomy_registry(
                args.rollback_source_registry
            ),
            proposal=proposal,
            reviews=reviews,
            application_id=args.application_id,
            applied_by=args.applied_by,
            applied_at=args.applied_at,
        )
    else:
        result_registry, application = apply_taxonomy_change(
            base_registry=base,
            proposal=proposal,
            reviews=reviews,
            application_id=args.application_id,
            applied_by=args.applied_by,
            applied_at=args.applied_at,
        )
    return {"application": application, "registry": result_registry}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="millefeuille taxonomy",
        description=(
            "Validate and derive immutable local taxonomy governance artifacts. "
            "Commands print JSON and never replace registry or lock files."
        ),
    )
    subparsers = parser.add_subparsers(dest="taxonomy_command", required=True)

    seal = subparsers.add_parser(
        "seal", help="Canonicalize an explicit registry draft and calculate its hash."
    )
    seal.add_argument("--registry-draft", required=True)

    validate = subparsers.add_parser(
        "validate", help="Validate a registry and optional exact lock binding."
    )
    validate.add_argument("--registry", required=True)
    validate.add_argument("--lock")

    lock = subparsers.add_parser(
        "lock", help="Print an immutable registry snapshot for one scope."
    )
    lock.add_argument("--registry", required=True)
    lock.add_argument("--lock-id", required=True)
    lock.add_argument(
        "--scope-type", choices=("batch", "pilot", "single-run"), required=True
    )
    lock.add_argument("--scope-id", required=True)
    lock.add_argument("--locked-by", required=True)
    lock.add_argument("--locked-at", required=True)

    propose = subparsers.add_parser(
        "propose", help="Bind an explicit candidate and impact analysis to a base."
    )
    propose.add_argument("--base-registry", required=True)
    propose.add_argument("--candidate-registry", required=True)
    propose.add_argument("--change-id", required=True)
    propose.add_argument(
        "--operation",
        choices=(
            "add",
            "clarify",
            "rename",
            "deprecate",
            "split",
            "merge",
            "mixed",
            "rollback",
        ),
        required=True,
    )
    propose.add_argument("--affected-entry-id", action="append", required=True)
    propose.add_argument("--reason", required=True)
    propose.add_argument("--evidence-ref", action="append", required=True)
    propose.add_argument(
        "--impact-risk", choices=("low", "medium", "high"), required=True
    )
    propose.add_argument("--impact-summary", required=True)
    propose.add_argument("--estimated-affected-records", type=int)
    propose.add_argument(
        "--historical-reclassification",
        choices=("none", "review", "required"),
        required=True,
    )
    propose.add_argument("--migration-instructions", required=True)
    propose.add_argument("--requested-by", required=True)
    propose.add_argument("--requested-at", required=True)
    propose.add_argument("--rollback-source-registry")

    review = subparsers.add_parser(
        "review", help="Print an immutable review bound to proposal and candidate."
    )
    review.add_argument("--proposal", required=True)
    review.add_argument("--base-registry", required=True)
    review.add_argument("--review-id", required=True)
    review.add_argument(
        "--role",
        choices=(
            "taxonomy-owner",
            "qa-lead",
            "operations-lead",
            "subject-matter-reviewer",
        ),
        required=True,
    )
    review.add_argument("--decision", choices=("approve", "reject"), required=True)
    review.add_argument("--reviewer-id", required=True)
    review.add_argument("--reviewed-at", required=True)
    review.add_argument("--notes", required=True)

    for command in ("apply", "rollback"):
        operation = subparsers.add_parser(
            command,
            help=(
                "Authorize a new registry version without changing existing files."
                if command == "apply"
                else "Restore a prior snapshot as a new forward registry version."
            ),
        )
        operation.add_argument("--base-registry", required=True)
        operation.add_argument("--proposal", required=True)
        operation.add_argument("--review", action="append", required=True)
        operation.add_argument("--application-id", required=True)
        operation.add_argument("--applied-by", required=True)
        operation.add_argument("--applied-at", required=True)
        if command == "rollback":
            operation.add_argument("--rollback-source-registry", required=True)

    return parser
