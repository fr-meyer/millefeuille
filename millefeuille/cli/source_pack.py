"""Offline source-pack commands."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import sys
from typing import TextIO

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.source_packs import (
    load_recovered_pdf_evidence,
    write_source_pack_from_recovered_pdf,
)


def run_source_pack_cli(
    argv: Sequence[str],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = _build_parser()
    raw_args = list(argv)
    if raw_args and raw_args[0] == "source-pack":
        raw_args = raw_args[1:]
    args = parser.parse_args(raw_args)

    if args.command != "intake":
        parser.error(f"unsupported source-pack command {args.command!r}")

    try:
        evidence = load_recovered_pdf_evidence(args.evidence)
        result = write_source_pack_from_recovered_pdf(
            evidence=evidence,
            source_pack_root=args.source_pack_root,
        )
    except MillefeuilleContractError as exc:
        print(f"millefeuille source-pack intake: {exc}", file=err)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True), file=out)
    else:
        print("Millefeuille source-pack intake", file=out)
        print(f"status: {result.status}", file=out)
        print(f"paper_id: {result.paper_id}", file=out)
        print(f"source_hash: {result.source_hash}", file=out)
        print(f"manifest_path: {result.manifest_path}", file=out)
        print(f"source_path: {result.source_path}", file=out)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="millefeuille source-pack",
        description=(
            "Create fixture source packs from verified local recovered-PDF "
            "evidence without live providers."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    intake_parser = subparsers.add_parser(
        "intake",
        help="Create a source pack from local recovered-PDF evidence.",
    )
    intake_parser.add_argument(
        "--evidence",
        required=True,
        help="Path to recovered PDF evidence JSON.",
    )
    intake_parser.add_argument(
        "--source-pack-root",
        required=True,
        help="Base source-pack directory to write under.",
    )
    intake_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )
    return parser
