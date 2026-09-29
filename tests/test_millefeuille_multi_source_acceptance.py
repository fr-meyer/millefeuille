"""Synthetic v0.2 upstream checks used by whole-paper acceptance."""

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.domain.acceptance import (
    _load_multi_source_upstream_checks,
    _load_verified_multi_source_profile,
)
from millefeuille.domain.millefeuille import (
    AcceptanceCheckStatus,
    MillefeuilleContractError,
)
from millefeuille.domain.multi_source_summary import build_multi_source_summary_package
from millefeuille.domain.published_summary_run_link import LINK_SCHEMA_VERSION
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
            checks, counts = _load_multi_source_upstream_checks(
                resolved, profile="research-default"
            )
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
                _load_multi_source_upstream_checks(resolved, profile="research-default")

    @requires_secure_nofollow_writes
    def test_non_default_profile_comes_from_verified_preparation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _pack(root)
            source_root = root / "source-packs"
            preparation_dir = source_root / "prepared"
            preparation_dir.mkdir()
            preparation_path, preparation_bytes, _counts, _blockers = (
                build_multi_source_summary_package(
                    route_evidence_path=pack / "selected/route.json",
                    structure_evidence_path=pack / "structure/structure.json",
                    output_dir=preparation_dir,
                    profile="offline-preview",
                )
            )
            preparation_path.write_bytes(preparation_bytes)
            run = source_root / "analyses/millefeuille/run-card"
            (run / "summaries").mkdir(parents=True)
            link_path = run / "summaries/hierarchical-summary.json"
            link_path.write_text(
                json.dumps(
                    {
                        "schema_version": LINK_SCHEMA_VERSION,
                        "evidence_refs": {
                            "preparation_ref": preparation_path.relative_to(
                                source_root
                            ).as_posix()
                        },
                    }
                ),
                encoding="utf-8",
            )
            manifest = json.loads((pack / "manifest.json").read_bytes())
            resolved = SimpleNamespace(
                source_pack_root=source_root,
                source_pack_dir=pack,
                run_dir=run,
                paper_id=manifest["paper_id"],
                run_id="run-card",
                source_hash=manifest["source_hash"],
            )
            view = {
                "summary_link_sha256": "sha256:"
                + hashlib.sha256(link_path.read_bytes()).hexdigest(),
                "paper_id": resolved.paper_id,
                "run_id": resolved.run_id,
                "source_hash": resolved.source_hash,
            }
            with patch(
                "millefeuille.domain.acceptance.load_published_summary_run_view",
                return_value=view,
            ):
                profile = _load_verified_multi_source_profile(resolved)
            self.assertEqual(profile, "offline-preview")
            checks, _counts = _load_multi_source_upstream_checks(
                resolved, profile=profile
            )
            self.assertEqual(checks[0].status, AcceptanceCheckStatus.PASSED)

    def test_preparation_ref_cannot_escape_source_pack_root(self):
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp) / "source-packs"
            run = source_root / "analyses/millefeuille/run-card"
            (run / "summaries").mkdir(parents=True)
            link_path = run / "summaries/hierarchical-summary.json"
            resolved = SimpleNamespace(
                source_pack_root=source_root,
                source_pack_dir=source_root / "zotero/item",
                run_dir=run,
                paper_id="paper",
                run_id="run-card",
                source_hash="sha256:" + "a" * 64,
            )
            for unsafe_ref in (
                str(Path(temp) / "outside.json"),
                "../outside.json",
                "prepared/../outside.json",
            ):
                with self.subTest(unsafe_ref=unsafe_ref):
                    link_path.write_text(
                        json.dumps(
                            {
                                "schema_version": LINK_SCHEMA_VERSION,
                                "evidence_refs": {"preparation_ref": unsafe_ref},
                            }
                        ),
                        encoding="utf-8",
                    )
                    view = {
                        "summary_link_sha256": "sha256:"
                        + hashlib.sha256(link_path.read_bytes()).hexdigest(),
                        "paper_id": resolved.paper_id,
                        "run_id": resolved.run_id,
                        "source_hash": resolved.source_hash,
                    }
                    with (
                        patch(
                            "millefeuille.domain.acceptance.load_published_summary_run_view",
                            return_value=view,
                        ),
                        self.assertRaisesRegex(
                            MillefeuilleContractError, "preparation ref is unsafe"
                        ),
                    ):
                        _load_verified_multi_source_profile(resolved)


if __name__ == "__main__":
    unittest.main()
