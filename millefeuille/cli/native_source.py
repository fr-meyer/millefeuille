"""Read-only observations through the kernel-isolated native boundary."""

import argparse
import hashlib
import json
from pathlib import Path

from millefeuille.domain.source_qualification import (
    NativeSourceObservation,
    qualify_untrusted_native_source,
)


def run_native_source_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="millefeuille inspect-native",
        description=(
            "Inspect exact hash-bound PDFs through the isolated native boundary."
        ),
    )
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--format", choices=["json"], default="json")
    parser.add_argument(
        "--trusted-original-fixture",
        action="store_true",
        help="Deprecated compatibility flag; never bypasses kernel isolation",
    )
    parser.add_argument(
        "--include-text",
        action="store_true",
        help="Explicitly disclose full source plaintext to stdout",
    )
    args = parser.parse_args(argv)
    # A CLI trust attestation never bypasses process/kernel isolation.
    result = qualify_untrusted_native_source({"path": args.pdf, "sha256": args.sha256})
    if result["outcome"] == "refused":
        print(
            json.dumps(
                {
                    "disposition": "refused",
                    "reason": result["reason"],
                    "boundary": result,
                }
            )
        )
        return 2
    texts = tuple(row["text"] for row in result["pages"])
    digest = hashlib.sha256(
        b"".join(len(p.encode()).to_bytes(8, "big") + p.encode() for p in texts)
    ).hexdigest()
    observation = NativeSourceObservation(
        result["source_sha256"], texts, digest, result["tool"]
    )
    pages = []
    for number, page in enumerate(observation.pages, 1):
        raw = page.encode("utf-8")
        row = {
            "source_sha256": observation.source_sha256,
            "page": number,
            "text_sha256": hashlib.sha256(raw).hexdigest(),
            "text_bytes": len(raw),
            "route": "native-needs-review" if page.strip() else "needs-ocr",
        }
        if args.include_text:
            row["text"] = page
        pages.append(row)
    print(
        json.dumps(
            {
                "schema_version": 1,
                "disposition": observation.disposition,
                "source_sha256": observation.source_sha256,
                "text_sha256": observation.text_sha256,
                "tool": observation.tool,
                "page_count": len(pages),
                "pages": pages,
                "limitations": observation.limitations,
                "execution_scope": "kernel-isolated-native",
                "text_included": args.include_text,
            },
            ensure_ascii=True,
        )
    )
    return 0
