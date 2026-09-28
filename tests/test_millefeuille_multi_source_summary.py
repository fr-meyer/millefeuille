"""Synthetic whole-pack summary preparation and dispatch acceptance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.multi_source_summary import (
    SUMMARY_MULTI_SCHEMA,
    build_multi_source_summary_package,
)
from millefeuille.domain.multi_source_upstream import (
    MultiSourceNativeInput,
    plan_multi_source_native_upstream,
)
from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    parse_source_pack_manifest,
    write_source_pack_from_recovered_pdfs,
)
from millefeuille.domain.source_scope import MultiSourceScope
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_dispatch import (
    SUMMARY_OUTPUT_CONTRACTS,
    plan_verified_summary_dispatch,
)
from millefeuille.domain.summary_preparation import (
    prepare_summary_execution_packages,
    verify_summary_preparation_package,
)
from millefeuille.domain.summary_published_handoff import _source_hash_for_preparation
from tests.platform_capabilities import requires_secure_nofollow_writes


def _pack(root: Path) -> Path:
    recovered = root / "recovered"
    recovered.mkdir()
    records = []
    native = []
    for key, filename, body, markdown, pages in (
        (
            "MAIN1234",
            "Main.pdf",
            b"synthetic main PDF",
            "## Page 1\n\nMain page one.\n\n## Page 2\n\nMain page two.\n",
            2,
        ),
        (
            "SUPP5678",
            "Supplement.pdf",
            b"synthetic supplement PDF",
            "## Page 1\n\nSupplement page.\n",
            1,
        ),
    ):
        pdf = recovered / filename
        pdf.write_bytes(body)
        records.append(
            RecoveredPdfEvidence(
                item_key="ITEM1234",
                attachment_key=key,
                canonical_filename=filename,
                recovered_pdf_path=pdf,
                expected_sha256=hashlib.sha256(body).hexdigest(),
                item_title="Synthetic two-source paper",
                file_size_bytes=len(body),
                zotero_version=1,
            )
        )
        text = recovered / (key + ".md")
        text.write_text(markdown)
        native.append(
            MultiSourceNativeInput(
                attachment_key=key,
                markdown_path=text,
                markdown_sha256=hashlib.sha256(text.read_bytes()).hexdigest(),
                page_count=pages,
                label=key,
            )
        )
    result = write_source_pack_from_recovered_pdfs(
        evidence_records=records,
        source_pack_root=root / "source-packs",
        created_at="2026-09-28T00:00:00+00:00",
    )
    manifest = result.manifest
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
    plan = plan_multi_source_native_upstream(
        source_pack_root=root / "source-packs",
        item_key="ITEM1234",
        paper_id="zotero-ITEM1234",
        scope=scope,
        inputs=tuple(native),
    )
    for output in plan.outputs:
        target = result.source_pack_dir / output.ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(output.content)
    return result.source_pack_dir


class MultiSourceSummaryTests(unittest.TestCase):
    @requires_secure_nofollow_writes
    def test_whole_pack_preparation_dispatch_and_idempotence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _pack(root)
            route, structure = (
                pack / "selected/route.json",
                pack / "structure/structure.json",
            )
            output = root / "preparation"
            output.mkdir()
            package_path, raw, counts, blockers = build_multi_source_summary_package(
                route_evidence_path=route,
                structure_evidence_path=structure,
                output_dir=output,
                profile="research-default",
            )
            prepared = json.loads(raw)
            self.assertEqual(prepared["schema_version"], SUMMARY_MULTI_SCHEMA)
            self.assertEqual(counts["summarize_page"], 3)
            self.assertEqual(counts["summarize_full_paper"], 1)
            self.assertEqual(len(prepared["identity"]["source_scope"]["sources"]), 2)
            self.assertEqual(
                prepared["identity"]["page_map"][-1]["attachment_key"], "SUPP5678"
            )
            manifest = parse_source_pack_manifest(
                json.loads((pack / "manifest.json").read_text())
            )
            handoff_kwargs = {
                "source": manifest,
                "prepared": prepared,
                "root": root / "source-packs",
                "source_pack_ref": "zotero/zotero-ITEM1234",
                "paper_id": "zotero-ITEM1234",
            }
            self.assertEqual(
                _source_hash_for_preparation(**handoff_kwargs),
                prepared["identity"]["source_hash"],
            )
            with self.assertRaisesRegex(MillefeuilleContractError, "identity drift"):
                _source_hash_for_preparation(
                    **{
                        **handoff_kwargs,
                        "source": {**manifest, "source_hash": "sha256:" + "0" * 64},
                    }
                )
            self.assertIn(
                "summary outputs are not generated by this preparation command",
                blockers,
            )
            first = prepare_summary_execution_packages(
                route_evidence_paths=[route],
                structure_evidence_paths=[structure],
                output_dir=output,
                profile="research-default",
            )
            self.assertEqual(first.status, "created")
            self.assertEqual(package_path.read_bytes(), raw)
            second = prepare_summary_execution_packages(
                route_evidence_paths=[route],
                structure_evidence_paths=[structure],
                output_dir=output,
                profile="research-default",
            )
            self.assertEqual(second.status, "existing")
            self.assertEqual(
                verify_summary_preparation_package(
                    route_evidence_path=route,
                    structure_evidence_path=structure,
                    preparation_path=package_path,
                ),
                prepared,
            )
            batch = plan_verified_summary_dispatch(
                route_evidence_path=route,
                structure_evidence_path=structure,
                preparation_path=package_path,
                prompt_builder=lambda stage, unit_id, locators, markdown, outline: (
                    f"Summarize {stage}/{unit_id} from {','.join(locators)}.\n".encode()
                    + markdown
                    + b"\n"
                    + outline
                ),
                output_contracts=SUMMARY_OUTPUT_CONTRACTS,
            )
            self.assertEqual(len(batch.units), sum(counts.values()))
            self.assertEqual(batch.units[0].source_locators, ("p.1",))
            grounded = plan_grounded_gpt_summary_batch(
                route_evidence_path=route,
                structure_evidence_path=structure,
                preparation_path=package_path,
            )
            self.assertEqual(len(grounded.batch.units), sum(counts.values()))
            full_paper = next(
                unit
                for unit in grounded.batch.units
                if unit.stage == "summarize_full_paper"
            )
            self.assertIn(b"Main page one.", full_paper.input_payload)
            self.assertIn(b"Supplement page.", full_paper.input_payload)
            supplement_page = next(
                unit
                for unit in grounded.batch.units
                if unit.stage == "summarize_page" and unit.unit_id == "page-3"
            )
            self.assertEqual(supplement_page.source_locators, ("p.3",))
            self.assertIn(b"Supplement page.", supplement_page.input_payload)

    def test_member_page_map_and_pdf_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _pack(root)
            route, structure = (
                pack / "selected/route.json",
                pack / "structure/structure.json",
            )
            kwargs = {
                "route_evidence_path": route,
                "structure_evidence_path": structure,
                "output_dir": root / "preparation",
                "profile": "research-default",
            }
            original = route.read_bytes()
            payload = json.loads(original)
            payload["page_map"][2]["attachment_locator"] = "attachment:MAIN1234/p.3"
            route.write_text(json.dumps(payload))
            with self.assertRaisesRegex(MillefeuilleContractError, "page attribution"):
                build_multi_source_summary_package(**kwargs)
            route.write_bytes(original)
            payload = json.loads(original)
            payload["page_map"][2]["text_sha256"] = "0" * 64
            route.write_text(json.dumps(payload))
            with self.assertRaisesRegex(MillefeuilleContractError, "page body digest"):
                build_multi_source_summary_package(**kwargs)
            route.write_bytes(original)
            source = next((pack / "sources").glob("*MAIN*"), None)
            if source is None:
                source = sorted((pack / "sources").iterdir())[0]
            source.write_bytes(source.read_bytes() + b"tampered")
            with self.assertRaisesRegex(
                MillefeuilleContractError, "exceeds|byte_size|hash"
            ):
                build_multi_source_summary_package(**kwargs)


if __name__ == "__main__":
    unittest.main()
