"""Standalone paper-card command adapter."""

import argparse
import json

from millefeuille.domain.paper_cards import export_paper_card


def run_paper_card_cli(argv=None):
    parser = argparse.ArgumentParser(prog="millefeuille paper-card")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--memory-dir")
    parser.add_argument("--project-comment")
    args = parser.parse_args(argv)
    try:
        result = export_paper_card(
            args.run_dir,
            args.output_dir,
            memory_dir=args.memory_dir,
            project_comment=args.project_comment,
        )
    except Exception as exc:
        print(json.dumps({"status": "refused", "error_class": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_paper_card_cli())
