"""Explicit optional Decisions command, registered by the parent CLI."""

import argparse
import asyncio
import json
from pathlib import Path

from millefeuille.domain.decisions_classification import execute, prepare


def run_decisions_classification_cli(argv=None):
    parser = argparse.ArgumentParser(prog="millefeuille decisions-classification")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-requests", type=int, choices=[1])
    args = parser.parse_args(argv)
    if args.live and args.max_requests != 1:
        parser.error(
            "--live requires explicit --max-requests 1 and separate spend authority"
        )
    try:
        context = prepare(args.run_dir, args.taxonomy)
        result = asyncio.run(
            execute(context, live=args.live, max_requests=args.max_requests)
        )
    except (OSError, ValueError, KeyError, TypeError):
        print(
            json.dumps({"status": "blocked", "reason": "invalid-evidence-or-preflight"})
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    proposal = result.get("proposal", {})
    return (
        2
        if result["status"] == "needs_review"
        or proposal.get("status") == "needs_review"
        else 0
    )
