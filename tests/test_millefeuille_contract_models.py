"""Tests for executable Millefeuille contract models."""

import json
from pathlib import Path
import unittest

from millefeuille.domain.millefeuille import (
    AttachmentEvidenceIdentity,
    ManualGate,
    MillefeuilleContractError,
    NativeExtractionEvidenceRecord,
    OCREvidenceRecord,
    ProviderPayloadDisposition,
    RouteEvidenceRecord,
    RouteSelection,
    RunMode,
    StageManifest,
    StageName,
    StageRecord,
    StageStatus,
    StructureEvidenceRecord,
    TagState,
    can_transition_tag,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "specs"
    / "millefeuille-pipeline"
    / "stage-manifest.schema.json"
)


class TestStageManifestContractModels(unittest.TestCase):
    def test_stage_manifest_round_trips_to_schema_shape(self):
        manifest = StageManifest(
            run_id="fixture-run",
            mode=RunMode.PREVIEW,
            manual_gates=[ManualGate.PDF_RECOVERY],
            stages=[
                StageRecord(
                    name=StageName.HANDOFF,
                    status=StageStatus.PASSED,
                    inputs=["fixture/discovery.jsonl"],
                    outputs=["fixture/handoff.jsonl"],
                ),
                StageRecord(
                    name=StageName.RECOVER,
                    status=StageStatus.MANUAL_GATE,
                    manual_gate_required=True,
                    gate=ManualGate.PDF_RECOVERY,
                    inputs=["fixture/handoff.jsonl"],
                    outputs=[],
                ),
            ],
        )

        payload = manifest.to_dict()

        self.assertEqual(
            payload["schema_version"], "millefeuille-stage-manifest/v0.1"
        )
        self.assertEqual(payload["source_type"], "zotero")
        self.assertEqual(payload["mode"], "preview")
        self.assertEqual(payload["manual_gates"], ["pdf_recovery"])
        self.assertEqual(payload["stages"][1]["status"], "manual-gate")

    def test_stage_manifest_gate_must_be_declared(self):
        with self.assertRaises(MillefeuilleContractError):
            StageManifest(
                run_id="fixture-run",
                mode="preview",
                manual_gates=[],
                stages=[
                    StageRecord(
                        name="recover",
                        status="manual-gate",
                        manual_gate_required=True,
                        gate="pdf_recovery",
                    )
                ],
            )

    def test_model_enums_match_stage_manifest_schema(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        stage_values = set(
            schema["properties"]["stages"]["items"]["properties"]["name"]["enum"]
        )
        mode_values = set(schema["properties"]["mode"]["enum"])
        gate_values = set(schema["properties"]["manual_gates"]["items"]["enum"])

        self.assertEqual(StageName.values(), stage_values)
        self.assertEqual(RunMode.values(), mode_values)
        self.assertEqual(ManualGate.values(), gate_values)


class TestTagStateContract(unittest.TestCase):
    def test_allowed_forward_transitions(self):
        self.assertTrue(
            can_transition_tag(TagState.SELECTED, TagState.PREVIEWED)
        )
        self.assertTrue(
            can_transition_tag(
                TagState.ACCEPTANCE_PASSED,
                TagState.READY_FOR_CLASSIFICATION,
            )
        )
        self.assertTrue(
            can_transition_tag(
                TagState.STRUCTURE_READY,
                TagState.SUMMARIZED,
            )
        )
        self.assertTrue(can_transition_tag(TagState.CARD_READY, TagState.INDEXED))

    def test_classification_before_acceptance_is_not_allowed(self):
        self.assertFalse(
            can_transition_tag(TagState.OPENKB_ADDED, TagState.CLASSIFIED)
        )

    def test_review_and_error_are_allowed_from_any_state(self):
        self.assertTrue(can_transition_tag(TagState.SELECTED, TagState.ERROR))
        self.assertTrue(
            can_transition_tag(TagState.SOURCE_PACKED, TagState.NEEDS_REVIEW)
        )


class TestOCREvidenceContract(unittest.TestCase):
    def test_native_extraction_evidence_record_serializes(self):
        identity = AttachmentEvidenceIdentity(
            item_key="ITEM1",
            attachment_key="ATT1",
            canonical_filename="Redacted - 2026 - Paper.pdf",
            sha256="a" * 64,
            zotero_version=7,
        )
        evidence = NativeExtractionEvidenceRecord(
            tool="PyPDF2-fixture",
            source_pack="fixture/openkb/source-packs/zotero/redacted",
            attachment_identity=identity,
            output_markdown_ref="extractions/native/fulltext.md",
            page_count=12,
        )

        payload = evidence.to_dict()

        self.assertEqual(payload["tool"], "PyPDF2-fixture")
        self.assertEqual(payload["attachment_identity"]["sha256"], "a" * 64)
        self.assertEqual(
            payload["output_markdown_ref"],
            "extractions/native/fulltext.md",
        )

    def test_ocr_evidence_record_serializes_without_payload(self):
        identity = AttachmentEvidenceIdentity(
            item_key="ITEM1",
            attachment_key="ATT1",
            canonical_filename="Redacted - 2026 - Paper.pdf",
            sha256="a" * 64,
            zotero_version=7,
        )
        evidence = OCREvidenceRecord(
            provider="mistral",
            provider_version="ocr-4-fixture",
            source_pack="fixture/openkb/source-packs/zotero/redacted",
            attachment_identity=identity,
            output_markdown_ref="fixture/openkb/raw/redacted.md",
            page_count=12,
            provider_payload_disposition=ProviderPayloadDisposition.DISCARDED,
            requested_model="mistral-ocr-latest",
        )

        payload = evidence.to_dict()

        self.assertEqual(payload["provider"], "mistral")
        self.assertEqual(payload["attachment_identity"]["sha256"], "a" * 64)
        self.assertEqual(payload["provider_payload_disposition"], "discarded")
        self.assertEqual(payload["requested_model"], "mistral-ocr-latest")
        self.assertNotIn("provider_payload", payload)

    def test_route_evidence_record_serializes(self):
        identity = AttachmentEvidenceIdentity(
            item_key="ITEM1",
            attachment_key="ATT1",
            canonical_filename="Redacted - 2026 - Paper.pdf",
            sha256="a" * 64,
            zotero_version=7,
        )
        evidence = RouteEvidenceRecord(
            selected_route=RouteSelection.MERGED_DUAL,
            source_pack="fixture/openkb/source-packs/zotero/redacted",
            attachment_identity=identity,
            output_markdown_ref="selected/fulltext.md",
            page_count=12,
            extraction_routes=["native", "mistral-ocr"],
            dual_extraction_complete=True,
            reason="fixture route selection",
        )

        payload = evidence.to_dict()

        self.assertEqual(payload["selected_route"], "merged-dual")
        self.assertEqual(payload["output_markdown_ref"], "selected/fulltext.md")
        self.assertTrue(payload["dual_extraction_complete"])

    def test_structure_evidence_record_serializes(self):
        identity = AttachmentEvidenceIdentity(
            item_key="ITEM1",
            attachment_key="ATT1",
            canonical_filename="Redacted - 2026 - Paper.pdf",
            sha256="a" * 64,
            zotero_version=7,
        )
        evidence = StructureEvidenceRecord(
            structure_backend="fixture-structure",
            selected_route=RouteSelection.MERGED_DUAL,
            source_pack="fixture/openkb/source-packs/zotero/redacted",
            attachment_identity=identity,
            source_markdown_ref="selected/fulltext.md",
            page_count=12,
            section_count=5,
            table_count=2,
            figure_count=1,
            reference_count=20,
            coverage={"locators": 18},
            structure={"sections": [{"id": "s1"}]},
            outline_markdown_ref="structure/outline.md",
        )

        payload = evidence.to_dict()

        self.assertEqual(payload["selected_route"], "merged-dual")
        self.assertEqual(payload["source_markdown_ref"], "selected/fulltext.md")
        self.assertEqual(payload["outline_markdown_ref"], "structure/outline.md")
        self.assertEqual(payload["section_count"], 5)

    def test_attachment_identity_requires_sha256_shape(self):
        with self.assertRaises(MillefeuilleContractError):
            AttachmentEvidenceIdentity(
                item_key="ITEM1",
                attachment_key="ATT1",
                canonical_filename="Redacted.pdf",
                sha256="short",
            )


if __name__ == "__main__":
    unittest.main()
