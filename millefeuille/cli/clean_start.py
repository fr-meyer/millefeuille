"""Explicit clean root initialization and bounded one-paper lifecycle."""

import argparse
import asyncio
import json


def run_clean_start_cli(argv=None):
    parser = argparse.ArgumentParser(prog="millefeuille clean-start")
    parser.add_argument(
        "operation",
        choices=[
            "init",
            "export",
            "plan",
            "live",
            "replay",
            "project",
            "inventory",
            "writeback-plan",
            "writeback-live",
        ],
    )
    parser.add_argument("--store")
    parser.add_argument("--seed")
    parser.add_argument("--output-dir")
    parser.add_argument("--run-dir")
    parser.add_argument("--historical", action="append", default=[])
    parser.add_argument("--scope")
    parser.add_argument("--inventory")
    parser.add_argument("--authority")
    parser.add_argument("--snapshot")
    parser.add_argument("--lineage", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        from millefeuille.domain.clean_evidence import project
        from millefeuille.domain.clean_final import execute, prepare
        from millefeuille.domain.clean_taxonomy import initialize

        if args.operation == "init":
            result = initialize(args.store, seed=args.seed)
        elif args.operation == "export":
            from millefeuille.domain.clean_cards import export

            result = export(prepare(args.run_dir, args.store), args.output_dir)
        elif args.operation == "project":
            result = project(args.run_dir)
        elif args.operation == "inventory":
            from millefeuille.domain.clean_inventory import build
            from millefeuille.domain.clean_taxonomy import current
            from millefeuille.domain.decisions_classification import load

            result = build(
                args.historical, current(args.store)["registry_id"], load(args.scope)
            )
        elif args.operation.startswith("writeback-"):
            from millefeuille.domain.clean_writeback import execute as writeback

            result = asyncio.run(
                writeback(
                    prepare(args.run_dir, args.store),
                    args.authority,
                    args.lineage,
                    inventory_path=args.inventory,
                    historical_paths=args.historical,
                    snapshot_path=args.snapshot,
                    live=args.operation == "writeback-live",
                )
            )
        else:
            result = asyncio.run(
                execute(
                    prepare(args.run_dir, args.store),
                    live=args.operation == "live",
                    replay=args.operation == "replay",
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
                        "Invalid input, consumed reservation or uncertain "
                        "outcome. Inspect journal; never retry provider work."
                    ),
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(run_clean_start_cli())
