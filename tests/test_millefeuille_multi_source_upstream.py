"""Synthetic, no-provider coverage for whole-pack native upstream planning."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.multi_source_upstream import (
    MultiSourceNativeInput,
    plan_multi_source_native_upstream,
)
from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    write_source_pack_from_recovered_pdfs,
)
from millefeuille.domain.source_scope import MultiSourceScope


def _fixture(root: Path):
    pack_root = root / "source-packs"
    recovered = root / "recovered"
    recovered.mkdir()
    records = []
    inputs = []
    for key, filename, pdf_bytes, markdown, page_count, label in (
        (
            "MAIN1234",
            "Main.pdf",
            b"synthetic main PDF",
            "## Page 1\n\nMain first page.\n\n## Page 2\n\nMain second page.\n",
            2,
            "main paper",
        ),
        (
            "SUPP5678",
            "Supplement.pdf",
            b"synthetic supplementary PDF",
            "## Page 1\n\nSupplement first page.\n",
            1,
            "supplement",
        ),
    ):
        pdf = recovered / filename
        pdf.write_bytes(pdf_bytes)
        records.append(
            RecoveredPdfEvidence(
                item_key="ITEM1234",
                attachment_key=key,
                canonical_filename=filename,
                recovered_pdf_path=pdf,
                expected_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
                item_title="Synthetic two-source paper",
                file_size_bytes=len(pdf_bytes),
                zotero_version=1,
            )
        )
        path = recovered / (key + ".md")
        path.write_text(markdown, encoding="utf-8")
        inputs.append(
            MultiSourceNativeInput(
                attachment_key=key,
                markdown_path=path,
                markdown_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                page_count=page_count,
                label=label,
                tool="synthetic-native",
            )
        )
    intake = write_source_pack_from_recovered_pdfs(
        evidence_records=records,
        source_pack_root=pack_root,
        created_at="2026-09-28T00:00:00+00:00",
    )
    manifest = intake.manifest
    scope = MultiSourceScope.from_dict(
        {
            "schema_version": "millefeuille-source-scope/v0.1",
            "source_hash": manifest["source_hash"],
            "sources": [
                {
                    "attachment_key": source["identity"]["zotero_attachment_key"],
                    "canonical_filename": source["identity"]["canonical_filename"],
                    "sha256": source["sha256"],
                    "source_ref": source["ref"],
                    "zotero_version": source["identity"]["zotero_version"],
                }
                for source in manifest["sources"]
            ],
        }
    )
    return pack_root, intake.source_pack_dir, scope, tuple(inputs)


def _plan(
    pack_root: Path, scope: MultiSourceScope, inputs: tuple[MultiSourceNativeInput, ...]
):
    return plan_multi_source_native_upstream(
        source_pack_root=pack_root,
        item_key="ITEM1234",
        paper_id="zotero-ITEM1234",
        scope=scope,
        inputs=inputs,
    )


class MultiSourceUpstreamTests(unittest.TestCase):
    def test_full_member_join_and_order_independent_no_write_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack_root, pack, scope, inputs = _fixture(root)
            before = sorted(
                str(p.relative_to(pack)) for p in pack.rglob("*") if p.is_file()
            )
            plan = _plan(pack_root, scope, inputs)
            reversed_plan = _plan(pack_root, scope, tuple(reversed(inputs)))
            self.assertEqual(plan.preview_sha256(), reversed_plan.preview_sha256())
            self.assertEqual(plan.page_count, 3)
            self.assertEqual(plan.source_count, 2)
            self.assertEqual(len(plan.outputs), 7)
            self.assertEqual(plan.warnings, ())
            preview = plan.preview()
            self.assertEqual(preview["provider_calls"], 0)
            self.assertEqual(preview["source_pack_writes"], 0)
            self.assertNotIn("Main first page", json.dumps(preview))
            self.assertEqual(
                before,
                sorted(
                    str(p.relative_to(pack)) for p in pack.rglob("*") if p.is_file()
                ),
            )
            route = json.loads(
                next(x.content for x in plan.outputs if x.ref == "selected/route.json")
            )
            self.assertEqual(route["source_hash"], scope.source_hash)
            self.assertEqual(
                [
                    member["attachment_key"]
                    for member in route["source_scope"]["sources"]
                ],
                ["MAIN1234", "SUPP5678"],
            )
            self.assertEqual(
                route["output_markdown_sha256"],
                next(x.sha256 for x in plan.outputs if x.ref == "selected/fulltext.md"),
            )
            self.assertEqual(
                [p["attachment_locator"] for p in route["page_map"]],
                [
                    "attachment:MAIN1234/p.1",
                    "attachment:MAIN1234/p.2",
                    "attachment:SUPP5678/p.1",
                ],
            )
            selected = next(
                x.content.decode()
                for x in plan.outputs
                if x.ref == "selected/fulltext.md"
            )
            self.assertEqual(selected.count("## Page"), 3)
            self.assertIn("Main first page", selected)
            self.assertIn("Supplement first page", selected)
            structure = json.loads(
                next(
                    x.content
                    for x in plan.outputs
                    if x.ref == "structure/structure.json"
                )
            )
            supplement_sections = [
                section
                for section in structure["structure"]["sections"]
                if section["title"] == "Source: supplement (SUPP5678)"
            ]
            self.assertEqual(len(supplement_sections), 1)
            self.assertEqual(supplement_sections[0]["locator"], "p.3#s3")
            self.assertEqual(structure["source_hash"], scope.source_hash)
            self.assertEqual(structure["page_count"], 3)

    def test_missing_duplicate_and_wrong_member_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            pack_root, _pack, scope, inputs = _fixture(Path(temp))
            for changed in (
                inputs[:1],
                (inputs[0], inputs[0]),
                (inputs[0], replace(inputs[1], attachment_key="WRONG")),
                (inputs[0], object()),
            ):
                with (
                    self.subTest(changed=changed),
                    self.assertRaises(MillefeuilleContractError),
                ):
                    _plan(pack_root, scope, changed)

    def test_markdown_and_page_drift_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            pack_root, _pack, scope, inputs = _fixture(Path(temp))
            with self.assertRaisesRegex(
                MillefeuilleContractError, "Markdown hash drift"
            ):
                _plan(
                    pack_root,
                    scope,
                    (replace(inputs[0], markdown_sha256="0" * 64), inputs[1]),
                )
            with self.assertRaisesRegex(
                MillefeuilleContractError, "page-marker coverage"
            ):
                _plan(pack_root, scope, (replace(inputs[0], page_count=3), inputs[1]))
            path = inputs[1].markdown_path
            path.write_text("## Page 2\n\nWrong page.\n", encoding="utf-8")
            updated = replace(
                inputs[1], markdown_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError, "page markers are not consecutive"
            ):
                _plan(pack_root, scope, (inputs[0], updated))

    def test_pack_byte_drift_refused_before_output(self):
        with tempfile.TemporaryDirectory() as temp:
            pack_root, pack, scope, inputs = _fixture(Path(temp))
            source = pack / scope.sources[0].source_ref
            source.write_bytes(source.read_bytes() + b"drift")
            with self.assertRaises(MillefeuilleContractError):
                _plan(pack_root, scope, inputs)


if __name__ == "__main__":
    unittest.main()
