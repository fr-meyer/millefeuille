"""Standalone immutable taxonomy store command adapter."""

import argparse
import json

from millefeuille.domain.taxonomy_store import install_taxonomy


def run_taxonomy_store_cli(argv=None):
    parser = argparse.ArgumentParser(prog="millefeuille taxonomy-store")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--store-dir", required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = install_taxonomy(
            args.snapshot,
            args.store_dir,
            markdown=args.markdown,
            expected_sha256=args.expected_sha256,
        )
    except Exception as exc:
        print(json.dumps({"status": "refused", "error_class": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_taxonomy_store_cli())
