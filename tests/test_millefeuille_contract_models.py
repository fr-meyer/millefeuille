"""Tests for executable Millefeuille contract models."""

import json
from pathlib import Path
import unittest

from millefeuille.domain.millefeuille import (
    AcceptanceBatchRunRecord,
    AcceptanceBatchSummaryRecord,
    AcceptanceCheckRecord,
    AcceptanceStatus,
    AcceptanceSummaryRecord,
    AttachmentEvidenceIdentity,
    ClassificationActionOutcome,
    ClassificationActionRecord,
    ClassificationBatchRunRecord,
    ClassificationBatchSummaryRecord,
    ClassificationDecisionRecord,
    ClassificationMode,
    ClassificationPlanRecord,
    ClassificationStatus,
    HierarchicalSummaryRecord,
    IndexLaneRecord,
    ManualGate,
    MillefeuilleContractError,
    NativeExtractionEvidenceRecord,
    OCREvidenceRecord,
    PaperCardRecord,
    ProviderPayloadDisposition,
    RejectedAlternativeRecord,
    ReleaseCandidatePreflightRecord,
    RetrievalIndexRecord,
    RouteEvidenceRecord,
    RouteSelection,
    RunMode,
    StageManifest,
    StageName,
    StageRecord,
    StageStatus,
    StructureEvidenceRecord,
    SummaryEntryRecord,
    SummaryGrain,
    SummaryScope,
    TagState,
    ZoteroWritebackPlanRecord,
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

        self.assertEqual(payload["schema_version"], "millefeuille-stage-manifest/v0.1")
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
        self.assertTrue(can_transition_tag(TagState.SELECTED, TagState.PREVIEWED))
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
        self.assertFalse(can_transition_tag(TagState.OPENKB_ADDED, TagState.CLASSIFIED))

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

    def test_hierarchical_summary_record_serializes(self):
        summary = HierarchicalSummaryRecord(
            paper_id="zotero-ITEM1",
            run_id="run-fixture",
            taxonomy_context={
                "taxonomy_version": "v0-fixture",
                "classification_scope": "classification",
            },
            summaries=[
                SummaryEntryRecord(
                    summary_id="page-1",
                    grain=SummaryGrain.PAGE,
                    scope=SummaryScope.GENERAL,
                    text_ref="summaries/texts/page-1.md",
                    source_locators=["p.1"],
                ),
                SummaryEntryRecord(
                    summary_id="full-paper",
                    grain=SummaryGrain.FULL_PAPER,
                    scope=SummaryScope.CLASSIFICATION,
                    text_ref="summaries/texts/full-paper.md",
                    source_locators=["section:introduction", "section:methods"],
                    depends_on=["page-1"],
                    quality_warnings=["fixture summary only"],
                ),
            ],
        )

        payload = summary.to_dict()

        self.assertEqual(
            payload["schema_version"],
            "millefeuille-hierarchical-summary/v0.1",
        )
        self.assertEqual(payload["paper_id"], "zotero-ITEM1")
        self.assertEqual(payload["run_id"], "run-fixture")
        self.assertEqual(payload["summaries"][0]["grain"], "page")
        self.assertEqual(payload["summaries"][1]["scope"], "classification")
        self.assertEqual(payload["summaries"][1]["depends_on"], ["page-1"])

    def test_paper_card_record_serializes(self):
        card = PaperCardRecord(
            paper_id="zotero-ITEM1",
            identity={
                "title": "Fixture Paper",
                "year": 2026,
                "source_hash": "sha256:" + ("a" * 64),
            },
            one_line_thesis="A concise thesis.",
            primary_contribution="A clear primary contribution.",
            evidence_refs=[
                "../summaries/hierarchical-summary.json",
                "../../../structure/structure.json",
            ],
            index_status=[{"lane": "openkb", "status": "skipped"}],
            model_provenance={"profile_id": "fixture-card"},
            classification_clues=["vision", "benchmarking"],
        )

        payload = card.to_dict()

        self.assertEqual(payload["schema_version"], "millefeuille-paper-card/v0.1")
        self.assertEqual(payload["paper_id"], "zotero-ITEM1")
        self.assertEqual(payload["identity"]["title"], "Fixture Paper")
        self.assertEqual(
            payload["evidence_refs"][0],
            "../summaries/hierarchical-summary.json",
        )
        self.assertEqual(payload["model_provenance"]["profile_id"], "fixture-card")

    def test_retrieval_index_record_serializes(self):
        index_record = RetrievalIndexRecord(
            paper_id="zotero-ITEM1",
            run_id="run-fixture",
            source_hash="sha256:" + ("a" * 64),
            selected_fulltext_ref="../../../selected/fulltext.md",
            summary_ref="../summaries/hierarchical-summary.json",
            paper_card_ref="../cards/paper-card.json",
            lanes=[
                IndexLaneRecord(
                    lane="openkb",
                    status="skipped",
                    skip_reason="fixture-only run",
                ),
                IndexLaneRecord(
                    lane="pageindex",
                    status="previewed",
                    target={"service": "pageindex-local"},
                    chunking_profile={"strategy": "section", "max_chars": 1200},
                ),
            ],
            duplicate_scan={"status": "not-run", "reason": "fixture-only run"},
        )

        payload = index_record.to_dict()

        self.assertEqual(
            payload["schema_version"],
            "millefeuille-retrieval-index-status/v0.1",
        )
        self.assertEqual(payload["paper_id"], "zotero-ITEM1")
        self.assertEqual(payload["run_id"], "run-fixture")
        self.assertEqual(
            payload["selected_fulltext_ref"],
            "../../../selected/fulltext.md",
        )
        self.assertEqual(payload["lanes"][0]["skip_reason"], "fixture-only run")
        self.assertEqual(
            payload["lanes"][1]["chunking_profile"]["strategy"],
            "section",
        )

    def test_attachment_identity_requires_sha256_shape(self):
        with self.assertRaises(MillefeuilleContractError):
            AttachmentEvidenceIdentity(
                item_key="ITEM1",
                attachment_key="ATT1",
                canonical_filename="Redacted.pdf",
                sha256="short",
            )

    def test_acceptance_summary_record_serializes(self):
        summary = AcceptanceSummaryRecord(
            paper_id="zotero-ITEM1",
            run_id="run-fixture",
            source_hash="sha256:" + ("a" * 64),
            source_pack_ref="../../..",
            status=AcceptanceStatus.PASS,
            counts={"handoff_rows": 1, "summary": 1},
            checks=[
                AcceptanceCheckRecord(
                    name="handoff",
                    status="passed",
                    refs=["../../handoff.jsonl"],
                ),
                AcceptanceCheckRecord(
                    name="summarize",
                    status="passed",
                    refs=["summaries/hierarchical-summary.json"],
                ),
            ],
        )

        payload = summary.to_dict()

        self.assertEqual(
            payload["schema_version"],
            "openkb-millefeuille-acceptance-summary/v0.1",
        )
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["checks"][0]["name"], "handoff")

    def test_acceptance_batch_summary_enforces_aggregate_counts(self):
        batch = AcceptanceBatchSummaryRecord(
            batch_id="batch-fixture",
            status=AcceptanceStatus.NEEDS_REVIEW,
            counts={"runs": 2, "passed": 1, "needs_review": 1},
            runs=[
                AcceptanceBatchRunRecord(
                    paper_id="zotero-ITEM1",
                    run_id="run-1",
                    source_hash="sha256:" + ("a" * 64),
                    status=AcceptanceStatus.PASS,
                    summary_ref=(
                        "zotero/zotero-ITEM1/analyses/millefeuille/"
                        "run-1/reports/acceptance-summary.json"
                    ),
                ),
                AcceptanceBatchRunRecord(
                    paper_id="zotero-ITEM2",
                    run_id="run-2",
                    source_hash="sha256:" + ("b" * 64),
                    status=AcceptanceStatus.NEEDS_REVIEW,
                    summary_ref=(
                        "zotero/zotero-ITEM2/analyses/millefeuille/"
                        "run-2/reports/acceptance-summary.json"
                    ),
                    review_reasons=["duplicate scan flagged an existing match"],
                ),
            ],
        )

        payload = batch.to_dict()

        self.assertEqual(
            payload["schema_version"],
            "millefeuille-acceptance-batch-summary/v0.1",
        )
        self.assertEqual(payload["status"], "needs-review")
        self.assertEqual(payload["counts"]["needs_review"], 1)
        with self.assertRaises(MillefeuilleContractError):
            AcceptanceBatchSummaryRecord(
                batch_id="batch-fixture",
                status=AcceptanceStatus.PASS,
                counts={"runs": 2, "passed": 2, "needs_review": 0},
                runs=batch.runs,
            )

    def test_classification_and_writeback_records_serialize(self):
        decision = ClassificationDecisionRecord(
            paper_id="zotero-ITEM1",
            run_id="run-fixture",
            source_hash="sha256:" + ("a" * 64),
            taxonomy_version="taxonomy-v1",
            mode=ClassificationMode.SINGLE,
            status=ClassificationStatus.CLASSIFIED,
            primary_path="Methods > Optimization",
            confidence="high",
            evidence_refs=["summaries/hierarchical-summary.json"],
            rejected_alternatives=[
                RejectedAlternativeRecord(
                    path="Applications > Vision",
                    reason="method-first contribution",
                )
            ],
            writeback_preview_ref="classification/zotero-writeback-preview.json",
        )
        plan = ClassificationPlanRecord(
            run_id="run-fixture",
            taxonomy_version="taxonomy-v1",
            mode="single",
            papers=[
                {
                    "paper_id": "zotero-ITEM1",
                    "decision_ref": "classification/decision-records/zotero-ITEM1.json",
                }
            ],
            default_profile="research-default",
        )
        writeback = ZoteroWritebackPlanRecord(
            paper_id="zotero-ITEM1",
            run_id="run-fixture",
            source_hash="sha256:" + ("a" * 64),
            mode="preview",
            status="previewed",
            add_tags=["millefeuille-classified"],
            remove_tags=["millefeuille"],
        )
        preflight = ReleaseCandidatePreflightRecord(
            current_version="0.4.0",
            candidate_version="0.5.0-rc1",
            completed_stages=["acceptance", "classify", "writeback"],
            manual_gates_remaining=["release_tag", "package_publication"],
            readiness="ready-for-rc-review",
        )

        self.assertEqual(decision.to_dict()["mode"], "single")
        self.assertEqual(plan.to_dict()["default_profile"], "research-default")
        self.assertEqual(writeback.to_dict()["status"], "previewed")
        self.assertEqual(preflight.to_dict()["readiness"], "ready-for-rc-review")

    def test_classification_action_record_enforces_mode_outcome_and_status(self):
        action = ClassificationActionRecord(
            action_id="review-001",
            paper_id="zotero-ITEM1",
            run_id="run-fixture",
            source_hash="sha256:" + ("a" * 64),
            taxonomy_version="taxonomy-v1",
            mode=ClassificationMode.REVIEW,
            outcome=ClassificationActionOutcome.NO_CHANGE,
            status=ClassificationStatus.CLASSIFIED,
            summary="The reviewed evidence supports the prior decision.",
            prior_decision_ref=(
                "classification/decision-records/zotero-ITEM1.json"
            ),
            final_decision_ref=(
                "classification/actions/review-001/final-decision.json"
            ),
            writeback_preview_ref=(
                "classification/zotero-writeback-preview.json"
            ),
            evidence_refs=["summaries/hierarchical-summary.json"],
        )

        self.assertEqual(action.to_dict()["outcome"], "no-change")
        with self.assertRaises(MillefeuilleContractError):
            ClassificationActionRecord(
                action_id="review-002",
                paper_id="zotero-ITEM1",
                run_id="run-fixture",
                source_hash="sha256:" + ("a" * 64),
                taxonomy_version="taxonomy-v1",
                mode=ClassificationMode.REVIEW,
                outcome=ClassificationActionOutcome.CONFIRMED,
                status=ClassificationStatus.CLASSIFIED,
                summary="Invalid cross-mode outcome.",
                prior_decision_ref="prior.json",
                final_decision_ref="final.json",
                writeback_preview_ref="preview.json",
                evidence_refs=["evidence.json"],
            )

    def test_classification_batch_summary_enforces_routes_and_counts(self):
        decision_ref = (
            "zotero/zotero-ITEM1/analyses/millefeuille/"
            "run-1/classification/decision-records/zotero-ITEM1.json"
        )
        run = ClassificationBatchRunRecord(
            paper_id="zotero-ITEM1",
            run_id="run-1",
            source_hash="sha256:" + ("a" * 64),
            taxonomy_version="taxonomy-v1",
            status=ClassificationStatus.CLASSIFIED,
            primary_path="Methods > Optimization",
            decision_ref=decision_ref,
            writeback_preview_ref=(
                "zotero/zotero-ITEM1/analyses/millefeuille/"
                "run-1/classification/zotero-writeback-preview.json"
            ),
        )
        summary = ClassificationBatchSummaryRecord(
            batch_id="classification-fixture",
            taxonomy_version="taxonomy-v1",
            status=ClassificationStatus.CLASSIFIED,
            counts={
                "runs": 1,
                "classified": 1,
                "needs_review": 0,
                "adjudication_required": 0,
            },
            routes=[
                {
                    "primary_path": "Methods > Optimization",
                    "count": 1,
                    "runs": [
                        {
                            "paper_id": "zotero-ITEM1",
                            "run_id": "run-1",
                            "status": "classified",
                            "decision_ref": decision_ref,
                        }
                    ],
                }
            ],
            runs=[run],
        )

        payload = summary.to_dict()

        self.assertEqual(
            payload["schema_version"],
            "millefeuille-classification-batch-summary/v0.1",
        )
        self.assertEqual(payload["routes"][0]["count"], 1)
        with self.assertRaises(MillefeuilleContractError):
            ClassificationBatchSummaryRecord(
                batch_id="classification-fixture",
                taxonomy_version="taxonomy-v1",
                status=ClassificationStatus.NEEDS_REVIEW,
                counts=summary.counts,
                routes=summary.routes,
                runs=summary.runs,
            )


if __name__ == "__main__":
    unittest.main()
