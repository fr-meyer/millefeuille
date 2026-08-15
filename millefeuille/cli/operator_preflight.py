"""No-effect operator preflight command for bounded Millefeuille work."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime
import json
import sys
from typing import TextIO

from millefeuille.domain.live_receipts import load_approved_live_receipt
from millefeuille.domain.millefeuille import MillefeuilleContractError, RunMode
from millefeuille.domain.operator_preflight import (
    evaluate_operator_preflight,
    load_operator_preflight_packet,
    render_operator_preflight_markdown,
)


def run_operator_preflight_cli(
    argv: Sequence[str],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> int:
    """Validate one local packet without performing an external operation."""

    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = _build_parser()
    raw_args = list(argv)
    if raw_args and raw_args[0] == "operator-preflight":
        raw_args = raw_args[1:]
    args = parser.parse_args(raw_args)

    if args.approval_receipt is not None and args.mode != RunMode.APPROVED_LIVE.value:
        print(
            "millefeuille operator-preflight: --approval-receipt requires "
            "explicit --mode approved-live; a receipt cannot promote preview "
            "or read-only-live",
            file=err,
        )
        return 3
    if args.mode == RunMode.APPROVED_LIVE.value and args.approval_receipt is None:
        print(
            "millefeuille operator-preflight: approved-live requires a separate "
            "MF-100 receipt via --approval-receipt",
            file=err,
        )
        return 3

    try:
        packet = load_operator_preflight_packet(args.packet)
        receipt = (
            load_approved_live_receipt(args.approval_receipt)
            if args.approval_receipt is not None
            else None
        )
        result = evaluate_operator_preflight(
            packet,
            explicit_mode=args.mode,
            approval_receipt=receipt,
            environment=environment,
            now=now,
        )
    except MillefeuilleContractError as exc:
        print(f"millefeuille operator-preflight: {exc}", file=err)
        return 3 if args.mode == RunMode.APPROVED_LIVE.value else 2

    if args.format == "markdown":
        out.write(render_operator_preflight_markdown(result))
    else:
        out.write(
            json.dumps(
                result.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    if args.mode == RunMode.APPROVED_LIVE.value:
        print(
            "millefeuille operator-preflight: exact approved scope validated, "
            "but external execution remains unsupported",
            file=err,
        )
        return 3
    return 2 if result.decision == "blocked" else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="millefeuille operator-preflight",
        description=(
            "Validate one strict operator packet and credential-reference "
            "readiness without external adapter/provider data operations or writes."
        ),
    )
    parser.add_argument(
        "--packet",
        required=True,
        help="Path to a strict millefeuille-operator-preflight-packet/v0.1 JSON file.",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=tuple(sorted(RunMode.values())),
        help="Explicit invocation mode; it must exactly match the packet mode.",
    )
    parser.add_argument(
        "--approval-receipt",
        help=(
            "Separate exact MF-100 receipt required only by approved-live; "
            "it never changes the explicit mode."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Deterministic sanitized output format (default: json).",
    )
    return parser
