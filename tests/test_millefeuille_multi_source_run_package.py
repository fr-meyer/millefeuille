"""Whole-pack run packaging preserves verified sources and append-only writes."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.multi_source_run_package import (
    plan_multi_source_run_package,
    publish_multi_source_run_package,
)
from millefeuille.domain.stage_runtime import resolve_run_artifacts
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_multi_source_summary import _pack


def _saved_run(root: Path) -> tuple[Path, Path]:
    pack = _pack(root)
    source_root = root / "source-packs"
    run = source_root / "analyses/millefeuille/run-two-source"
    run.mkdir(parents=True)
    return pack, run


def _saved_output_refs():
    return (
        patch(
            "millefeuille.domain.multi_source_run_package._resolve_summary_refs",
            return_value={
                "summary_artifact_ref": "summaries/hierarchical-summary.json",
                "summary_text_dir_ref": "summaries/texts",
            },
        ),
        patch(
            "millefeuille.domain.multi_source_run_package._resolve_card_refs",
            return_value={
                "paper_card_json_ref": "cards/paper-card.json",
                "paper_card_markdown_ref": "cards/paper-card.md",
            },
        ),
        patch(
            "millefeuille.domain.multi_source_run_package._resolve_index_refs",
            return_value={
                "retrieval_index_status_ref": "index/index-status.json",
                "index_records": [
                    {"lane": "openkb", "status": "written"},
                    {"lane": "pageindex", "status": "written"},
                ],
            },
        ),
    )


class MultiSourceRunPackageTests(unittest.TestCase):
    @requires_secure_nofollow_writes
    def test_plan_is_read_only_and_publication_is_exact_and_append_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, run = _saved_run(root)
            summary_patch, card_patch, index_patch = _saved_output_refs()
            with summary_patch, card_patch, index_patch:
                plan = plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id="run-two-source",
                )
                self.assertFalse((run / "stage-manifest.json").exists())
                self.assertFalse((run / "artifact-index.json").exists())
                self.assertEqual(plan.preview()["provider_calls"], 0)
                self.assertEqual(len(plan.preview()["files"]), 2)
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "fingerprint drift"
                ):
                    publish_multi_source_run_package(
                        plan, expected_preview_sha256="0" * 64
                    )
                paths = publish_multi_source_run_package(
                    plan, expected_preview_sha256=plan.preview_sha256
                )
                self.assertEqual(
                    [path.name for path in paths],
                    ["stage-manifest.json", "artifact-index.json"],
                )
                resolved = resolve_run_artifacts(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id="run-two-source",
                    artifact_root=run,
                )
                self.assertEqual(
                    resolved.artifact_index.source_identity["pdf_count"], 2
                )
                self.assertEqual(len(resolved.stage_manifest.stages), 16)
                self.assertEqual(
                    json.loads(paths[1].read_bytes())["source_pack"]["source_hash"],
                    resolved.source_hash,
                )
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "target already exists"
                ):
                    publish_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )

    @requires_secure_nofollow_writes
    def test_changed_supplement_pdf_blocks_planning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            with self.assertRaisesRegex(MillefeuilleContractError, "traversal-safe"):
                plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id="..",
                )
            manifest = json.loads((pack / "manifest.json").read_bytes())
            supplement = next(
                source
                for source in manifest["sources"]
                if source["identity"]["zotero_attachment_key"] == "SUPP5678"
            )
            (pack / supplement["ref"]).write_bytes(b"changed supplement")
            with self.assertRaises(MillefeuilleContractError):
                plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id=run.name,
                )


if __name__ == "__main__":
    unittest.main()
