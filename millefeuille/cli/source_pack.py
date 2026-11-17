"""Offline source-pack commands."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import sys
from typing import TextIO

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.multi_source_run_package import plan_multi_source_run_package
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

    if args.command == "materialize-imported":
        from millefeuille.domain.openkb_source_materialization import (
            materialize_imported_source,
        )

        try:
            payload = materialize_imported_source(
                imported=args.imported,
                pdf=args.pdf,
                source_pack_root=args.source_pack_root,
                structure_output=args.structure_output,
                protected_staging_root=args.protected_staging_root,
            )
            print(json.dumps(payload, indent=2, sort_keys=True), file=out)
            return 0
        except (
            MillefeuilleContractError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            print(
                f"millefeuille source-pack materialize-imported: {type(exc).__name__}",
                file=err,
            )
            return 2

    if args.command in {"import-openkb", "retrieve-imported"}:
        from millefeuille.domain.openkb_import import (
            import_openkb,
            retrieve_imported_pages,
        )

        try:
            if args.command == "import-openkb":
                payload = import_openkb(
                    pack=args.pack,
                    pdf=args.pdf,
                    source_sha256=args.source_sha256,
                    manifest_sha256=args.manifest_sha256,
                    evidence_sha256=args.evidence_sha256,
                    protected_staging_root=args.protected_staging_root,
                    output=args.output,
                )
            else:
                payload = retrieve_imported_pages(output=args.output, pages=args.page)
            print(json.dumps(payload, indent=2, sort_keys=True), file=out)
            return 0
        except (
            MillefeuilleContractError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            print(
                f"millefeuille source-pack {args.command}: {type(exc).__name__}",
                file=err,
            )
            return 2

    if args.command == "run-package-preview":
        return _run_package_preview(args, out=out, err=err)

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


def _run_package_preview(args: argparse.Namespace, *, out: TextIO, err: TextIO) -> int:
    try:
        plan = plan_multi_source_run_package(
            source_pack_root=args.source_pack_root,
            item_key=args.item_key,
            run_id=args.run_id,
        )
        preview = {
            **plan.preview(),
            "preview_sha256": plan.preview_sha256,
            "status": "planned",
            "writes": 0,
        }
    except (MillefeuilleContractError, OSError) as exc:
        message = " ".join(str(exc).splitlines())
        print(f"millefeuille source-pack run-package-preview: {message}", file=err)
        return 1

    if args.json:
        print(json.dumps(preview, indent=2, sort_keys=True), file=out)
    else:
        print("Millefeuille run-package preview", file=out)
        for key in ("status", "paper_id", "run_id", "preview_sha256"):
            print(f"{key}: {preview[key]}", file=out)
        print(f"verified_inputs: {len(plan.verified_inputs)}", file=out)
        print(f"planned_files: {len(preview['files'])}", file=out)
        print("writes: 0; provider_calls: 0", file=out)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="millefeuille source-pack",
        description=(
            "Intake local recovered-PDF evidence or preview saved run-package "
            "metadata without live providers."
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
    preview_parser = subparsers.add_parser(
        "run-package-preview",
        help="Verify saved multi-source outputs and preview metadata without writes.",
    )
    preview_parser.add_argument("--source-pack-root", required=True)
    preview_parser.add_argument("--item-key", required=True)
    preview_parser.add_argument("--run-id", required=True)
    preview_parser.add_argument("--json", action="store_true")
    import_parser = subparsers.add_parser(
        "import-openkb",
        help="Convert pinned existing OpenKB extraction evidence, not accepted runs.",
    )
    for name in (
        "pack",
        "pdf",
        "source-sha256",
        "manifest-sha256",
        "evidence-sha256",
        "output",
    ):
        import_parser.add_argument("--" + name, required=True)
    import_parser.add_argument("--protected-staging-root")
    retrieve_parser = subparsers.add_parser(
        "retrieve-imported",
        help="Select hash-checked imported pages, not accepted-run retrieval.",
    )
    retrieve_parser.add_argument("--output", required=True)
    retrieve_parser.add_argument("--page", type=int, action="append", required=True)
    materialize_parser = subparsers.add_parser(
        "materialize-imported",
        help="Materialize source, route and structure from imported evidence.",
    )
    materialize_parser.add_argument("--protected-staging-root")
    for name in ("imported", "pdf", "source-pack-root", "structure-output"):
        materialize_parser.add_argument("--" + name, required=True)
    return parser
