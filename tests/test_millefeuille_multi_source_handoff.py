"""Two-source handoff checks for whole-paper acceptance."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from millefeuille.domain.acceptance import _build_handoff_context
from millefeuille.domain.millefeuille import AcceptanceCheckStatus
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_multi_source_summary import _pack


def _fixture(root: Path):
    pack = _pack(root)
    manifest = json.loads((pack / "manifest.json").read_bytes())
    run = root / "run"
    run.mkdir()
    resolved = SimpleNamespace(
        source_pack_manifest=manifest,
        artifact_index=SimpleNamespace(
            source_identity={
                "zotero_item_key": "ITEM1234",
                "pdf_count": 2,
            }
        ),
        run_dir=run,
    )
    rows = [
        {
            "item_key": "ITEM1234",
            "attachment_key": source["identity"]["zotero_attachment_key"],
            "canonical_filename": source["identity"]["canonical_filename"],
            "sha256": source["sha256"],
            "file_size_bytes": source["byte_size"],
            "is_pdf": True,
            "verification_strength": "full",
        }
        for source in manifest["sources"]
    ]
    return resolved, rows


def _check(root: Path, resolved, rows):
    path = root / "handoff.jsonl"
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    reasons = []
    result = _build_handoff_context(
        resolved=resolved, handoff_path=path, review_reasons=reasons
    )
    return result, reasons


class MultiSourceHandoffTests(unittest.TestCase):
    @requires_secure_nofollow_writes
    def test_both_verified_attachments_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolved, rows = _fixture(root)
            result, reasons = _check(root, resolved, rows)
            self.assertEqual(result["check"].status, AcceptanceCheckStatus.PASSED)
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["details"]["source_count"], 2)
            self.assertEqual(reasons, [])

    @requires_secure_nofollow_writes
    def test_missing_or_drifted_attachment_requires_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolved, rows = _fixture(root)
            cases = {
                "missing": rows[:1],
                "wrong-hash": [rows[0], {**rows[1], "sha256": "0" * 64}],
                "wrong-filename": [
                    rows[0],
                    {**rows[1], "canonical_filename": "another.pdf"},
                ],
                "duplicate": [rows[0], rows[0]],
            }
            for name, candidate in cases.items():
                with self.subTest(name=name):
                    result, reasons = _check(root, resolved, candidate)
                    self.assertEqual(
                        result["check"].status,
                        AcceptanceCheckStatus.NEEDS_REVIEW,
                    )
                    self.assertEqual(len(reasons), 1)


if __name__ == "__main__":
    unittest.main()
