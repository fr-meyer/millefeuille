"""Synthetic v0.2 upstream checks used by whole-paper acceptance."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from millefeuille.domain.acceptance import _load_multi_source_upstream_checks
from millefeuille.domain.millefeuille import (
    AcceptanceCheckStatus,
    MillefeuilleContractError,
)
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_multi_source_summary import _pack


class MultiSourceAcceptanceTests(unittest.TestCase):
    @requires_secure_nofollow_writes
    def test_valid_two_pdf_evidence_passes_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _pack(root)
            run = root / "run"
            run.mkdir()
            manifest = json.loads((pack / "manifest.json").read_bytes())
            resolved = SimpleNamespace(
                source_pack_dir=pack,
                run_dir=run,
                paper_id=manifest["paper_id"],
                source_hash=manifest["source_hash"],
            )
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            checks, counts = _load_multi_source_upstream_checks(resolved)
            self.assertEqual(
                [(check.name, check.status) for check in checks],
                [
                    ("extract-native", AcceptanceCheckStatus.PASSED),
                    ("route", AcceptanceCheckStatus.PASSED),
                    ("structure", AcceptanceCheckStatus.PASSED),
                    ("extract-ocr", AcceptanceCheckStatus.SKIPPED),
                ],
            )
            self.assertEqual(
                counts,
                {
                    "native_extraction": 1,
                    "ocr_extraction": 0,
                    "route": 1,
                    "structure": 1,
                },
            )
            self.assertEqual(
                before,
                {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                },
            )

    @requires_secure_nofollow_writes
    def test_native_source_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _pack(root)
            run = root / "run"
            run.mkdir()
            manifest = json.loads((pack / "manifest.json").read_bytes())
            resolved = SimpleNamespace(
                source_pack_dir=pack,
                run_dir=run,
                paper_id=manifest["paper_id"],
                source_hash=manifest["source_hash"],
            )
            source = sorted((pack / "extractions/native/sources").glob("*.md"))[0]
            source.write_bytes(source.read_bytes() + b"tampered")
            with self.assertRaisesRegex(MillefeuilleContractError, "hash drift"):
                _load_multi_source_upstream_checks(resolved)


if __name__ == "__main__":
    unittest.main()
