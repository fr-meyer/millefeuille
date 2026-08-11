"""Read-only artifact and status subcommands."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import sys
from typing import TextIO

from millefeuille.domain.artifacts import (
    ArtifactIndex,
    load_artifact_index,
    resolve_artifact_index_path,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.status_observability import build_status_report


def run_artifact_cli(
    argv: Sequence[str],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = _build_parser()
    args = parser.parse_args(list(argv))

    if args.command == "artifacts":
        try:
            index_path = resolve_artifact_index_path(
                index=args.index,
                artifact_root=args.artifact_root,
                paper_id=args.paper_id,
                run_id=args.run_id,
            )
            artifact_index = load_artifact_index(index_path)
        except MillefeuilleContractError as exc:
            print(f"millefeuille artifacts: {exc}", file=err)
            return 3
        _print_artifacts(
            artifact_index,
            index_path=str(index_path),
            json_output=args.json,
            out=out,
        )
        return 0

    try:
        index_path = None
        if args.source_pack_root is None:
            index_path = resolve_artifact_index_path(
                index=args.index,
                artifact_root=args.artifact_root,
                paper_id=args.paper_id,
                run_id=args.run_id,
            )
        payload = build_status_report(
            index_path=index_path,
            source_pack_root=args.source_pack_root,
            paper_id=args.paper_id,
            item_key=args.item_key,
            run_id=args.run_id,
            evidence_paths=args.evidence,
        )
    except MillefeuilleContractError as exc:
        print(f"millefeuille status: {exc}", file=err)
        return 3
    _print_status(payload, json_output=args.json, out=out)
    if args.strict and not payload["status"]["complete"]:
        return 2
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="millefeuille",
        description="Inspect Millefeuille artifact indexes without live providers.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_artifact_index_args(
        subparsers.add_parser(
            "artifacts",
            help="List artifacts recorded by a Millefeuille artifact index.",
        )
    )
    status_parser = subparsers.add_parser(
        "status",
        help="Join canonical local run status without live calls.",
    )
    _add_status_args(status_parser)
    status_parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 2 when any joined canonical or required observation blocks.",
    )
    return parser


def _add_artifact_index_args(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--index",
        help="Path to artifact-index.json.",
    )
    source.add_argument(
        "--artifact-root",
        help="Artifact root directory or direct artifact-index.json path.",
    )
    parser.add_argument("--paper-id", help="Paper id for artifact-root lookup.")
    parser.add_argument("--run-id", help="Run id for artifact-root lookup.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )


def _add_status_args(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--index", help="Path to artifact-index.json.")
    source.add_argument(
        "--artifact-root",
        help="Artifact root directory or direct artifact-index.json path.",
    )
    source.add_argument(
        "--source-pack-root",
        help="Canonical source-pack root for the strict three-way join.",
    )
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--paper-id", help="Exact paper id.")
    identity.add_argument("--item-key", help="Exact Zotero item key.")
    parser.add_argument("--run-id", help="Exact run id.")
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="Strict local status-observation envelope; repeat by kind.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit deterministic sanitized JSON.",
    )


def _print_artifacts(
    artifact_index: ArtifactIndex,
    *,
    index_path: str,
    json_output: bool,
    out: TextIO,
) -> None:
    if json_output:
        payload = artifact_index.to_dict()
        payload["index_path"] = index_path
        print(json.dumps(payload, indent=2, sort_keys=True), file=out)
        return

    print("Millefeuille artifacts", file=out)
    print(f"index_path: {index_path}", file=out)
    print(f"paper_id: {artifact_index.paper_id}", file=out)
    print(f"run_id: {artifact_index.run_id}", file=out)
    print(f"artifact_root: {artifact_index.artifact_root}", file=out)
    print(f"source_pack: {artifact_index.source_pack['ref']}", file=out)
    print("artifacts:", file=out)
    if not artifact_index.artifacts:
        print("  [none]", file=out)
    for name, record in sorted(artifact_index.artifacts.items()):
        private_marker = " private" if record.get("private_content") else ""
        stage_marker = f" stage={record['stage']}" if record.get("stage") else ""
        print(
            f"  - {name}: {record['kind']} -> {record['ref']}"
            f"{stage_marker}{private_marker}",
            file=out,
        )
    print("indexes:", file=out)
    if not artifact_index.indexes:
        print("  [none]", file=out)
    for record in artifact_index.indexes:
        detail = record.get("result_ref") or record.get("skip_reason") or ""
        suffix = f" ({detail})" if detail else ""
        print(f"  - {record['lane']}: {record['status']}{suffix}", file=out)
    writeback = artifact_index.zotero_writeback
    print(
        f"zotero_writeback: {writeback['mode']} / {writeback['status']}",
        file=out,
    )


def _print_status(
    payload: dict[str, object],
    *,
    json_output: bool,
    out: TextIO,
) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True), file=out)
        return

    status = payload["status"]
    assert isinstance(status, dict)
    print(f"Millefeuille status: {status['state']}", file=out)
    print(f"paper_id: {payload['paper_id']}", file=out)
    print(f"run_id: {payload['run_id']}", file=out)
    print(f"mode: {payload['mode']}", file=out)
    print(f"scope: {payload['observation_scope']}", file=out)
    print(f"stage_counts: {_format_counts(status['stage_counts'])}", file=out)
    print(f"index_counts: {_format_counts(status['index_counts'])}", file=out)
    acceptance = payload["acceptance"]
    classification = payload["classification"]
    assert isinstance(acceptance, dict)
    assert isinstance(classification, dict)
    print(
        f"acceptance: {acceptance['state']} / {acceptance['status']}",
        file=out,
    )
    print(
        f"classification: {classification['state']} / {classification['status']}",
        file=out,
    )
    writeback = payload["zotero_writeback"]
    assert isinstance(writeback, dict)
    print(
        f"zotero_writeback: {writeback['mode']} / {writeback['status']} / "
        f"{writeback['completion']}",
        file=out,
    )
    blocking_items = status["blocking_items"]
    assert isinstance(blocking_items, list)
    if blocking_items:
        print("blocking:", file=out)
        for item in blocking_items:
            print(f"  - {item}", file=out)


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "[none]"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
