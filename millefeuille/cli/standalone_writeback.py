"""Explicit CLI for one additive standalone note, never parent mutation."""

import argparse
import asyncio
import json

from millefeuille.domain.standalone_writeback import execute, prepare


def run_standalone_writeback_cli(argv=None):
    parser = argparse.ArgumentParser(prog="millefeuille standalone-writeback")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--item-key", required=True)
    parser.add_argument("--library-id", required=True)
    parser.add_argument("--approval-manifest", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        context = prepare(
            args.run_dir, args.item_key, args.library_id, args.approval_manifest
        )
        result = asyncio.run(execute(context, args.live))
    except Exception as exc:
        result = {
            "status": "refused",
            "note_key": None,
            "error_class": type(exc).__name__,
        }
    print(json.dumps(result, sort_keys=True))
    return (
        0
        if result["status"]
        in {"planned-read-only", "written-verified", "already-written-verified"}
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(run_standalone_writeback_cli())
