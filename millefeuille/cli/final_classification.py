"""Explicit final lifecycle over saved analysis; never repeats OCR/analysis."""

import argparse
import asyncio
import json

from millefeuille.domain.final_classification import execute, prepare


def run_final_classification_cli(argv=None):
    parser = argparse.ArgumentParser(prog="millefeuille final-classification")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--taxonomy", required=True)
    parser.add_argument("--store")
    parser.add_argument("--executor-command")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-requests", type=int, choices=[1])
    parser.add_argument("--compact-plan", action="store_true")
    parser.add_argument("--writeback-authority")
    parser.add_argument("--lineage", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        context = prepare(args.run_dir, args.taxonomy, compact_plan=args.compact_plan)
        if args.compact_plan:
            if args.live or args.writeback_authority:
                raise ValueError("Plan cannot execute")
            result = {
                "status": "compact-plan-only",
                "binding": context["binding"],
                "binding_sha256": context["binding_sha256"],
            }
        elif args.writeback_authority:
            from millefeuille.domain.final_writeback import execute as writeback

            result = asyncio.run(
                writeback(
                    context, args.writeback_authority, args.lineage, live=args.live
                )
            )
        else:
            result = asyncio.run(
                execute(
                    context,
                    live=args.live,
                    max_requests=args.max_requests,
                    executor_argv=json.loads(args.executor_command)
                    if args.executor_command
                    else None,
                    store=args.store,
                )
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": (
                        "precondition-or-provider-outcome; inspect journal; never "
                        "retry uncertain work"
                    ),
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(run_final_classification_cli())
