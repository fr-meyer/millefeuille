"""Offline CLI for canonical lifecycle tags and migration previews."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import sys
from typing import Any, TextIO

from millefeuille.domain.lifecycle_tags import (
    build_lifecycle_tag_migration_plan,
    canonical_lifecycle_tag_registry,
    load_lifecycle_tag_json,
    load_lifecycle_tag_migration_plan,
    load_lifecycle_tag_registry,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError


def run_lifecycle_tags_cli(
    argv: Sequence[str],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one no-effect lifecycle registry or migration command."""

    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = _build_parser()
    args = parser.parse_args(list(argv))
    try:
        payload = _run_command(args)
    except MillefeuilleContractError as exc:
        print(f"millefeuille lifecycle-tags {args.lifecycle_command}: {exc}", file=err)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), file=out)
    return 0


def _run_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.lifecycle_command == "registry":
        return canonical_lifecycle_tag_registry()

    registry = load_lifecycle_tag_registry(args.registry)
    if args.lifecycle_command == "validate":
        result: dict[str, Any] = {
            "status": "valid",
            "registry_id": registry["registry_id"],
            "registry_version": registry["registry_version"],
            "registry_content_identity": registry["content_identity"],
            "external_effects_performed": False,
        }
        if args.plan is not None:
            plan = load_lifecycle_tag_migration_plan(args.plan)
            expected_registry = {
                "registry_id": registry["registry_id"],
                "registry_version": registry["registry_version"],
                "content_identity": registry["content_identity"],
            }
            if plan["registry"] != expected_registry:
                raise MillefeuilleContractError(
                    "migration plan registry identity drift"
                )
            result.update(
                {
                    "plan_id": plan["plan_id"],
                    "plan_content_identity": plan["content_identity"],
                    "paper_id": plan["run"]["paper_id"],
                    "run_id": plan["run"]["run_id"],
                    "item_key": plan["item"]["item_key"],
                }
            )
        return result

    acceptance = _optional_json(args.acceptance_summary, "acceptance summary")
    classification_plan = _optional_json(
        args.classification_plan,
        "classification plan",
    )
    classification_decision = _optional_json(
        args.classification_decision,
        "classification decision",
    )
    taxonomy_lock = _optional_json(args.taxonomy_lock, "taxonomy lock")
    return build_lifecycle_tag_migration_plan(
        registry=registry,
        plan_id=args.plan_id,
        item_key=args.item_key,
        zotero_version=args.zotero_version,
        current_tags=args.current_tag,
        stage_manifest_payload=load_lifecycle_tag_json(
            args.stage_manifest,
            "stage manifest",
        ),
        artifact_index_payload=load_lifecycle_tag_json(
            args.artifact_index,
            "artifact index",
        ),
        acceptance_payload=acceptance,
        classification_plan_payload=classification_plan,
        classification_decision_payload=classification_decision,
        taxonomy_lock=taxonomy_lock,
        remove_selection_after_terminal_success=(
            args.remove_selection_after_terminal_success
        ),
    )


def _optional_json(path: str | None, label: str) -> dict[str, Any] | None:
    return None if path is None else load_lifecycle_tag_json(path, label)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="millefeuille lifecycle-tags",
        description=(
            "Inspect the canonical lifecycle registry and derive strict migration "
            "previews from local evidence without Zotero/provider operations or "
            "writes."
        ),
    )
    commands = parser.add_subparsers(dest="lifecycle_command", required=True)
    commands.add_parser(
        "registry",
        help="Print the immutable content-addressed v0.1 lifecycle registry.",
    )

    validate = commands.add_parser(
        "validate",
        help="Validate the exact registry and an optional migration plan.",
    )
    validate.add_argument("--registry", required=True)
    validate.add_argument("--plan")

    plan = commands.add_parser(
        "plan",
        help="Print one preview-only migration plan from exact local evidence.",
    )
    plan.add_argument("--registry", required=True)
    plan.add_argument("--plan-id", required=True)
    plan.add_argument("--item-key", required=True)
    plan.add_argument("--zotero-version", type=int, required=True)
    plan.add_argument(
        "--current-tag",
        action="append",
        default=[],
        help="Exact observed current tag; repeat for every current tag.",
    )
    plan.add_argument("--stage-manifest", required=True)
    plan.add_argument("--artifact-index", required=True)
    plan.add_argument("--acceptance-summary")
    plan.add_argument("--classification-plan")
    plan.add_argument("--classification-decision")
    plan.add_argument("--taxonomy-lock")
    plan.add_argument(
        "--remove-selection-after-terminal-success",
        action="store_true",
        help=(
            "Preview removal of only the `millefeuille` selection tag after "
            "terminal success; MF-160 approval and version recheck remain required."
        ),
    )
    return parser
