"""Synthetic stdlib helper tests, not runtime qualification or archive evidence."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.validate_evidence_archive import manifest_check, validate_archive
from tests.fixtures.evidence_transport import (
    MAX_LINE_BYTES,
    PREFIX,
    Reassembler,
    canonical,
    frame_receipt,
    parse,
)


class TestEvidenceTransport(unittest.TestCase):
    def test_roundtrip_bounded_both_kinds(self):
        for kind in ("F05", "S04"):
            receipt = {"value": "é" * 10000}
            frames = frame_receipt(kind, receipt)
            assembler = Reassembler()
            result = None
            for line in frames:
                self.assertLessEqual(len(line.encode()), MAX_LINE_BYTES)
                result = assembler.feed(b"timestamp " + line.encode() + b"\n")
            assembler.finish()
            self.assertEqual(result, (kind, canonical(receipt), receipt))

    def test_missing_reordered_tampered_and_schema(self):
        frames = frame_receipt("F05", {"value": "a" * 12000})
        for mode in ("missing", "reordered", "hash", "count", "length", "schema"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                changed = frames[:]
                if mode == "missing":
                    changed.pop()
                elif mode == "reordered":
                    changed.reverse()
                else:
                    f = parse(changed[0][len(PREFIX) :].encode())
                    key = {
                        "hash": "sha256",
                        "count": "count",
                        "length": "byte_length",
                        "schema": "extra",
                    }[mode]
                    f[key] = "0" * 64 if mode == "hash" else 0
                    changed[0] = PREFIX + canonical(f).decode()
                assembler = Reassembler()
                for line in changed:
                    assembler.feed(line.encode())
                assembler.finish()

    def test_receipt_parse_bound_and_duplicate_json(self):
        for raw in (b" " * (3 * 1024 * 1024 + 1), b'{"x":1,"x":2}'):
            with self.assertRaises(ValueError):
                parse(raw)

    def _manifest(self):
        from tests.fixtures.f05_receipts import METHOD_SEAMS, SOURCE_PATHS

        identity = {
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "RUNNER_OS": "Linux",
            "python": "3.12",
        }
        return {
            "schema": "millefeuille-evidence-manifest/v1",
            **{
                k: identity[k]
                for k in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
            },
            "worker_sha256": "b" * 64,
            "source_sha256": dict.fromkeys(SOURCE_PATHS, "c" * 64),
            "cells": [
                {
                    "job": f"cell-{i}",
                    "runtime": {
                        **identity,
                        "RUNNER_OS": "Linux" if i < 3 else "Windows",
                        "python": ("3.11", "3.12", "3.13")[i % 3],
                    },
                    "methods": list(METHOD_SEAMS),
                }
                for i in range(6)
            ],
        }

    def test_manifest_and_missing_archive(self):
        manifest = self._manifest()
        manifest_check(manifest)
        for key in ("cells", "source_sha256", "GITHUB_RUN_ID"):
            bad = copy.deepcopy(manifest)
            bad[key] = [] if key == "cells" else {} if key == "source_sha256" else ""
            with self.assertRaises(ValueError):
                manifest_check(bad)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "logs.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("job-level.txt", "ignored")
            with self.assertRaisesRegex(ValueError, "missing/extra"):
                validate_archive(path, manifest)

    def test_archive_dedup_conflict_and_missing_cells(self):
        # Mock only semantic acceptance to exercise stdlib transport integration.
        # Separate F05 helper tests cover custody; these are not real crash receipts.
        manifest = self._manifest()
        cases = ["multi-column", "table", "figure", "rotation", "equation"]
        manifest["s04_source_sha256"] = {"synthetic.py": "d" * 64}
        for cell in manifest["cells"][:3]:
            cell["s04_case_ids"] = cases
        for mode in ("identical", "conflict", "missing", "truncated"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "logs.zip"
                with zipfile.ZipFile(path, "w") as archive:
                    for number, cell in enumerate(manifest["cells"]):
                        if mode == "missing" and number == 0:
                            continue
                        frames = []
                        for method in cell["methods"]:
                            receipt = {"runtime": cell["runtime"], "test": method}
                            frames += frame_receipt("F05", receipt)
                            frames += frame_receipt("F05", receipt)
                            if mode == "conflict" and number == 0:
                                frames += frame_receipt(
                                    "F05", {**receipt, "extra": True}
                                )
                        for case in cell.get("s04_case_ids", []):
                            frames += frame_receipt(
                                "S04",
                                {
                                    "runtime": cell["runtime"],
                                    "case_id": case,
                                    "source_sha256": manifest["s04_source_sha256"],
                                    "opaque": "x" * 12000,
                                },
                            )
                        if mode == "truncated" and number == 0:
                            frames.pop()
                        archive.writestr(
                            cell["job"] + "/4_Run unit tests.txt", "\n".join(frames)
                        )
                        archive.writestr(cell["job"] + ".txt", "ignored job-level copy")
                with patch("scripts.validate_evidence_archive.accept", return_value={}):
                    if mode == "identical":
                        result = validate_archive(path, manifest)
                        self.assertEqual(len(result["receipts"]), 27)
                    else:
                        with self.assertRaises(ValueError):
                            validate_archive(path, manifest)
