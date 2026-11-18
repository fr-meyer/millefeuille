"""Explicit standalone retention command; local read-only plan by default."""

import argparse
import json
import logging
import sys

from millefeuille.domain.standalone_retention import retain


def run_standalone_retention_cli(argv):
    parser = argparse.ArgumentParser(prog="millefeuille standalone-retention")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--verify-cloud", action="store_true")
    args = parser.parse_args(argv)
    if args.reconcile and not args.live:
        parser.error("--reconcile requires --live")
    old = logging.getLogger("httpx").level
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        result = retain(
            args.run_dir,
            live=args.live,
            reconcile=args.reconcile,
            verify_cloud=args.verify_cloud,
        )
        print(json.dumps(result, allow_nan=False))
        return 0
    except Exception as exc:
        print(
            f"standalone-retention: {type(exc).__name__}; stopped without retry. "
            "Inspect immutable retention journals; never remove an intent to retry.",
            file=sys.stderr,
        )
        return 2
    finally:
        logging.getLogger("httpx").setLevel(old)


if __name__ == "__main__":
    sys.exit(run_standalone_retention_cli(sys.argv[1:]))
