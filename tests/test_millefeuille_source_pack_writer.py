"""Tests for fixture-first source-pack intake."""

from __future__ import annotations

import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.cli.source_pack import run_source_pack_cli
from millefeuille.domain.artifact_writer import write_dry_run_artifacts
from millefeuille.domain.artifacts import load_artifact_index
from millefeuille.domain.card_fixtures import (
    load_paper_card,
    write_cards_from_evidence,
)
from millefeuille.domain.card_index_contract import canonical_json_bytes
from millefeuille.domain.config import ArtifactExportConfig
from millefeuille.domain.extraction_fixtures import (
    load_native_extraction_sidecar,
    load_ocr_extraction_sidecar,
    write_native_extractions_from_evidence,
    write_ocr_extractions_from_evidence,
)
from millefeuille.domain.index_fixtures import (
    CARD_INDEX_TRANSACTION_MAX_CARD_BYTES,
    CARD_INDEX_TRANSACTION_ROOT_REF,
    load_retrieval_index_status,
    write_indexes_from_evidence,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.models import (
    AttachmentInfo,
    DiscoveredItem,
    OpenKBHandoffRow,
    PaperMetadata,
)
from millefeuille.domain.route_fixtures import (
    load_route_selection_sidecar,
    write_route_selections_from_evidence,
)
from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    aggregate_source_hash,
    load_recovered_pdf_evidence,
    load_source_pack_manifest,
    write_source_pack_from_recovered_pdf,
    write_source_packs_from_handoff_evidence,
)
from millefeuille.domain.structure_fixtures import (
    load_structure_sidecar,
    write_structures_from_evidence,
)
from millefeuille.domain.summary_fixtures import (
    load_hierarchical_summary,
    write_summaries_from_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BYTES = b"fixture recovered paper bytes for source-pack intake\n"
FIXTURE_SHA256 = hashlib.sha256(FIXTURE_BYTES).hexdigest()


def _write_recovered_pdf(tempdir: str) -> Path:
    source_path = Path(tempdir) / "recovered.pdf"
    source_path.write_bytes(FIXTURE_BYTES)
    return source_path


def _write_recovered_pdf_bytes(
    tempdir: str,
    filename: str,
    payload: bytes,
) -> Path:
    source_path = Path(tempdir) / filename
    source_path.write_bytes(payload)
    return source_path


def _evidence(source_path: Path) -> RecoveredPdfEvidence:
    return RecoveredPdfEvidence(
        item_key="ITEM1",
        attachment_key="ATT1",
        canonical_filename="Example Author - 2026 - Intake Fixture.pdf",
        recovered_pdf_path=source_path,
        expected_sha256=FIXTURE_SHA256,
        item_title="Intake Fixture Paper",
        citation_key="fixture2026intake",
        discovered_at="2026-07-13T12:00:00+00:00",
        verification_strength="full",
        recovery={
            "method": "fixture-recovered-pdf",
            "item_key": "ITEM1",
            "attachment_key": "ATT1",
        },
        openkb_policy_hints={
            "no_auth_url": True,
            "verification_required": True,
        },
        file_size_bytes=len(FIXTURE_BYTES),
        zotero_version=7,
    )


def _write_evidence_json(tempdir: str, source_path: Path) -> Path:
    evidence_path = Path(tempdir) / "recovered-pdf-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "millefeuille-recovered-pdf-evidence/v0.1",
                "source_type": "zotero",
                "item_key": "ITEM1",
                "attachment_key": "ATT1",
                "canonical_filename": "Example Author - 2026 - Intake Fixture.pdf",
                "recovered_pdf_path": source_path.name,
                "expected_sha256": FIXTURE_SHA256,
                "item_title": "Intake Fixture Paper",
                "citation_key": "fixture2026intake",
                "discovered_at": "2026-07-13T12:00:00+00:00",
                "verification_strength": "full",
                "recovery": {
                    "method": "fixture-recovered-pdf",
                    "item_key": "ITEM1",
                    "attachment_key": "ATT1",
                },
                "openkb_policy_hints": {
                    "no_auth_url": True,
                    "verification_required": True,
                },
                "file_size_bytes": len(FIXTURE_BYTES),
                "zotero_version": 7,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence_path


def _write_markdown(tempdir: str, filename: str, text: str) -> Path:
    markdown_path = Path(tempdir) / filename
    markdown_path.write_text(text, encoding="utf-8")
    return markdown_path


def _write_native_extraction_evidence_json(
    tempdir: str,
    markdown_path: Path,
    *,
    item_key: str = "ITEM1",
    attachment_key: str = "ATT1",
    canonical_filename: str = "Example Author - 2026 - Intake Fixture.pdf",
    sha256: str = FIXTURE_SHA256,
) -> Path:
    evidence_path = Path(tempdir) / "native-extraction-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": ("millefeuille-native-extraction-evidence/v0.1"),
                "source_type": "zotero",
                "item_key": item_key,
                "attachment_key": attachment_key,
                "canonical_filename": canonical_filename,
                "markdown_path": markdown_path.name,
                "expected_sha256": sha256,
                "page_count": 3,
                "tool": "PyPDF2-fixture",
                "empty_pages": 0,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence_path


def _write_ocr_extraction_evidence_json(
    tempdir: str,
    markdown_path: Path,
    *,
    item_key: str = "ITEM1",
    attachment_key: str = "ATT1",
    canonical_filename: str = "Example Author - 2026 - Intake Fixture.pdf",
    sha256: str = FIXTURE_SHA256,
) -> Path:
    evidence_path = Path(tempdir) / "ocr-extraction-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "millefeuille-ocr-extraction-evidence/v0.1",
                "source_type": "zotero",
                "item_key": item_key,
                "attachment_key": attachment_key,
                "canonical_filename": canonical_filename,
                "markdown_path": markdown_path.name,
                "expected_sha256": sha256,
                "page_count": 3,
                "provider": "mistral-ocr",
                "requested_model": "mistral-ocr-latest",
                "provider_version": "mistral-ocr-4-0-fixture",
                "provider_payload_disposition": "discarded",
                "confidence_scores_granularity": "word",
                "table_format": "markdown",
                "include_blocks": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence_path


def _write_route_selection_evidence_json(
    tempdir: str,
    markdown_path: Path,
    *,
    selected_route: str = "merged-dual",
) -> Path:
    evidence_path = Path(tempdir) / "route-selection-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": ("millefeuille-route-selection-evidence/v0.1"),
                "source_type": "zotero",
                "item_key": "ITEM1",
                "attachment_key": "ATT1",
                "canonical_filename": "Example Author - 2026 - Intake Fixture.pdf",
                "markdown_path": markdown_path.name,
                "expected_sha256": FIXTURE_SHA256,
                "page_count": 3,
                "selected_route": selected_route,
                "reason": "fixture route selection",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence_path


def _write_structure_payload_json(tempdir: str) -> Path:
    structure_path = Path(tempdir) / "structure-fixture.json"
    structure_path.write_text(
        json.dumps(
            {
                "pages": [
                    {"page": 1, "sections": ["Introduction"]},
                    {"page": 2, "sections": ["Methods"]},
                    {"page": 3, "sections": ["Results"]},
                ],
                "sections": [
                    {"id": "s1", "title": "Introduction", "page": 1},
                    {"id": "s2", "title": "Methods", "page": 2},
                ],
                "tables": [{"id": "t1", "caption": "Fixture table", "page": 2}],
                "figures": [],
                "references": [{"id": "r1", "label": "[1]"}],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return structure_path


def _write_structure_evidence_json(
    tempdir: str,
    structure_path: Path,
    outline_path: Path | None = None,
) -> Path:
    evidence_path = Path(tempdir) / "structure-evidence.json"
    payload = {
        "schema_version": "millefeuille-structure-evidence/v0.1",
        "source_type": "zotero",
        "item_key": "ITEM1",
        "attachment_key": "ATT1",
        "canonical_filename": "Example Author - 2026 - Intake Fixture.pdf",
        "structure_path": structure_path.name,
        "expected_sha256": FIXTURE_SHA256,
        "page_count": 3,
        "selected_route": "merged-dual",
        "sections": 2,
        "tables": 1,
        "figures": 0,
        "references": 1,
        "locators": 7,
    }
    if outline_path is not None:
        payload["outline_path"] = outline_path.name
    evidence_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return evidence_path


def _write_summary_fixture_json(tempdir: str) -> Path:
    summary_path = Path(tempdir) / "hierarchical-summary-fixture.json"
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": "millefeuille-hierarchical-summary/v0.1",
                "paper_id": "fixture-paper",
                "run_id": "fixture-run",
                "taxonomy_context": {
                    "taxonomy_version": "v0-fixture",
                    "classification_scope": "classification",
                },
                "summaries": [
                    {
                        "summary_id": "page-1",
                        "grain": "page",
                        "scope": "general",
                        "text_ref": "page-1.md",
                        "source_locators": ["p.1"],
                    },
                    {
                        "summary_id": "full-paper",
                        "grain": "full-paper",
                        "scope": "classification",
                        "text_ref": "full-paper.md",
                        "source_locators": ["section:introduction", "section:methods"],
                        "depends_on": ["page-1"],
                        "quality_warnings": ["fixture summary only"],
                    },
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary_path


def _write_summary_evidence_json(
    tempdir: str,
    summary_path: Path,
) -> Path:
    evidence_path = Path(tempdir) / "summary-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "millefeuille-summary-fixture-evidence/v0.1",
                "source_type": "zotero",
                "item_key": "ITEM1",
                "attachment_key": "ATT1",
                "canonical_filename": "Example Author - 2026 - Intake Fixture.pdf",
                "summary_path": summary_path.name,
                "expected_sha256": FIXTURE_SHA256,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence_path


def _write_card_fixture_json(tempdir: str) -> Path:
    card_path = Path(tempdir) / "paper-card-fixture.json"
    card_path.write_text(
        json.dumps(
            {
                "schema_version": "millefeuille-paper-card/v0.2",
                "paper_id": "fixture-paper",
                "run_id": "fixture-run",
                "identity": {
                    "title": "Fixture Paper",
                    "authors": ["Alice Example", "Bob Example"],
                    "year": 2026,
                },
                "one_line_thesis": "A concise thesis.",
                "primary_contribution": "A clear primary contribution.",
                "problem_addressed": "A fixture problem.",
                "method_or_approach": "Fixture method.",
                "main_results": "Fixture results.",
                "limitations": "Fixture limitations.",
                "classification_clues": ["benchmark", "vision"],
                "evidence_refs": ["fixture-summary.json"],
                "index_state": {
                    "phase": "planned",
                    "lanes": [
                        {"lane": "openkb", "status": "pending"},
                        {"lane": "pageindex", "status": "pending"},
                    ],
                },
                "model_provenance": {"profile_id": "fixture-card"},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return card_path


def _write_card_evidence_json(
    tempdir: str,
    card_json_path: Path,
    card_markdown_path: Path,
) -> Path:
    evidence_path = Path(tempdir) / "card-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "millefeuille-card-fixture-evidence/v0.1",
                "source_type": "zotero",
                "item_key": "ITEM1",
                "attachment_key": "ATT1",
                "canonical_filename": "Example Author - 2026 - Intake Fixture.pdf",
                "card_json_path": card_json_path.name,
                "card_markdown_path": card_markdown_path.name,
                "expected_sha256": FIXTURE_SHA256,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence_path


def _write_index_fixture_json(tempdir: str) -> Path:
    index_path = Path(tempdir) / "retrieval-index-fixture.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "millefeuille-retrieval-index-status/v0.1",
                "paper_id": "fixture-paper",
                "run_id": "fixture-run",
                "source_hash": "sha256:" + ("0" * 64),
                "selected_fulltext_ref": "fixture/fulltext.md",
                "summary_ref": "fixture/summary.json",
                "paper_card_ref": "fixture/card.json",
                "lanes": [
                    {
                        "lane": "openkb",
                        "status": "skipped",
                        "skip_reason": "fixture-only run",
                    },
                    {
                        "lane": "pageindex",
                        "status": "previewed",
                        "target": {"service": "pageindex-local"},
                        "chunking_profile": {
                            "strategy": "section",
                            "max_chars": 1200,
                        },
                    },
                ],
                "duplicate_scan": {
                    "status": "not-run",
                    "reason": "fixture-only run",
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return index_path


def _write_index_evidence_json(tempdir: str, index_status_path: Path) -> Path:
    evidence_path = Path(tempdir) / "index-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "millefeuille-index-fixture-evidence/v0.1",
                "source_type": "zotero",
                "item_key": "ITEM1",
                "attachment_key": "ATT1",
                "canonical_filename": "Example Author - 2026 - Intake Fixture.pdf",
                "index_status_path": index_status_path.name,
                "expected_sha256": FIXTURE_SHA256,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence_path


def _prepare_card_index_fixture(
    tempdir: str,
) -> tuple[Path, Path, Path, Path]:
    source_path = _write_recovered_pdf(tempdir)
    source_pack_root = Path(tempdir) / "source-packs"
    result = write_source_pack_from_recovered_pdf(
        evidence=_evidence(source_path),
        source_pack_root=source_pack_root,
        created_at="2026-07-13T12:00:00+00:00",
    )
    write_native_extractions_from_evidence(
        evidence_path=_write_native_extraction_evidence_json(
            tempdir,
            _write_markdown(tempdir, "native-fulltext.md", "Native fixture page 1\n"),
        ),
        source_pack_root=source_pack_root,
    )
    write_ocr_extractions_from_evidence(
        evidence_path=_write_ocr_extraction_evidence_json(
            tempdir,
            _write_markdown(tempdir, "ocr-fulltext.md", "OCR fixture page 1\n"),
        ),
        source_pack_root=source_pack_root,
    )
    write_route_selections_from_evidence(
        evidence_path=_write_route_selection_evidence_json(
            tempdir,
            _write_markdown(
                tempdir,
                "selected-fulltext.md",
                "Merged fixture page 1\n",
            ),
        ),
        source_pack_root=source_pack_root,
    )
    write_structures_from_evidence(
        evidence_path=_write_structure_evidence_json(
            tempdir,
            _write_structure_payload_json(tempdir),
        ),
        source_pack_root=source_pack_root,
    )
    _write_markdown(tempdir, "page-1.md", "Page 1 summary.\n")
    _write_markdown(tempdir, "full-paper.md", "Full paper summary.\n")
    write_summaries_from_evidence(
        evidence_path=_write_summary_evidence_json(
            tempdir,
            _write_summary_fixture_json(tempdir),
        ),
        source_pack_root=source_pack_root,
        run_id="run-fixture",
    )
    card_evidence_path = _write_card_evidence_json(
        tempdir,
        _write_card_fixture_json(tempdir),
        _write_markdown(
            tempdir,
            "paper-card.md",
            "# Fixture Paper Card\n\nA concise thesis.\n",
        ),
    )
    write_cards_from_evidence(
        evidence_path=card_evidence_path,
        source_pack_root=source_pack_root,
        run_id="run-fixture",
    )
    index_evidence_path = _write_index_evidence_json(
        tempdir,
        _write_index_fixture_json(tempdir),
    )
    run_dir = result.source_pack_dir / "analyses" / "millefeuille" / "run-fixture"
    return source_pack_root, run_dir, card_evidence_path, index_evidence_path


def _make_item() -> DiscoveredItem:
    attachment = AttachmentInfo(
        key="ATT1",
        filename="Example Author - 2026 - Intake Fixture.pdf",
        content_type="application/pdf",
        link_mode="imported_file",
        file_size_bytes=len(FIXTURE_BYTES),
        sha256=FIXTURE_SHA256,
        zotero_version=7,
        item_type="journalArticle",
    )
    return DiscoveredItem(
        key="ITEM1",
        title="Intake Fixture Paper",
        tags=["millefeuille"],
        attachments=[attachment],
        citation_key="fixture2026intake",
        paper_metadata=PaperMetadata(
            title="Intake Fixture Paper",
            year=2026,
            doi="10.0000/intake-fixture",
        ),
    )


def _make_handoff_row(
    *,
    item_key: str = "ITEM1",
    attachment_key: str = "ATT1",
    canonical_filename: str = "Example Author - 2026 - Intake Fixture.pdf",
    sha256: str | None = FIXTURE_SHA256,
    file_size_bytes: int = len(FIXTURE_BYTES),
) -> OpenKBHandoffRow:
    return OpenKBHandoffRow(
        schema_version="openkb-millefeuille-handoff/v0.1",
        source_type="zotero",
        discovered_at="2026-07-13T12:00:00+00:00",
        item_key=item_key,
        attachment_key=attachment_key,
        canonical_filename=canonical_filename,
        is_pdf=True,
        verification_strength="full",
        recovery={
            "method": "fixture-recovered-pdf",
            "item_key": item_key,
            "attachment_key": attachment_key,
        },
        openkb_policy_hints={
            "no_auth_url": True,
            "verification_required": True,
        },
        item_title="Intake Fixture Paper",
        citation_key="fixture2026intake",
        content_type="application/pdf",
        file_size_bytes=file_size_bytes,
        zotero_version=7,
        sha256=sha256,
    )


class TestSourcePackIntakeWriter(unittest.TestCase):
    def test_writes_source_pack_from_verified_recovered_pdf(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=Path(tempdir) / "source-packs",
                created_at="2026-07-13T12:00:00+00:00",
            )

            self.assertEqual(result.status, "created")
            self.assertEqual(result.paper_id, "zotero-ITEM1")
            self.assertEqual(result.source_hash, f"sha256:{FIXTURE_SHA256}")
            self.assertTrue(result.manifest_path.is_file())
            self.assertEqual(result.source_path.read_bytes(), FIXTURE_BYTES)
            self.assertTrue((result.source_pack_dir / "pages").is_dir())
            self.assertTrue((result.source_pack_dir / "extractions/native").is_dir())
            self.assertTrue((result.source_pack_dir / "analyses/millefeuille").is_dir())

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["schema_version"],
                "millefeuille-source-pack-manifest/v0.1",
            )
            self.assertEqual(manifest["source_hash"], f"sha256:{FIXTURE_SHA256}")
            self.assertEqual(manifest["source"]["ref"], "source.pdf")
            self.assertEqual(
                manifest["identity"]["zotero_attachment_key"],
                "ATT1",
            )
            self.assertNotIn(str(source_path), json.dumps(manifest))

    def test_idempotent_rerun_accepts_same_manifest_and_source_hash(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"

            first = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            second = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-14T12:00:00+00:00",
            )

            self.assertEqual(first.status, "created")
            self.assertEqual(second.status, "existing")
            self.assertEqual(second.source_hash, f"sha256:{FIXTURE_SHA256}")

    def test_hash_mismatch_fails_before_creating_source_pack(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            evidence = RecoveredPdfEvidence(
                item_key="ITEM1",
                attachment_key="ATT1",
                canonical_filename="Example.pdf",
                recovered_pdf_path=source_path,
                expected_sha256="0" * 64,
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "hash mismatch",
            ):
                write_source_pack_from_recovered_pdf(
                    evidence=evidence,
                    source_pack_root=Path(tempdir) / "source-packs",
                )

            self.assertFalse((Path(tempdir) / "source-packs").exists())

    def test_prefixed_expected_sha256_is_normalized(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            evidence = RecoveredPdfEvidence(
                item_key="ITEM1",
                attachment_key="ATT1",
                canonical_filename="Example.pdf",
                recovered_pdf_path=source_path,
                expected_sha256=f"sha256:{FIXTURE_SHA256}",
            )

            result = write_source_pack_from_recovered_pdf(
                evidence=evidence,
                source_pack_root=Path(tempdir) / "source-packs",
                created_at="2026-07-13T12:00:00+00:00",
            )

            self.assertEqual(result.source_hash, f"sha256:{FIXTURE_SHA256}")

    def test_existing_source_pack_drift_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            result.source_path.write_bytes(b"different recovered bytes\n")

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "source hash drift",
            ):
                write_source_pack_from_recovered_pdf(
                    evidence=_evidence(source_path),
                    source_pack_root=source_pack_root,
                    created_at="2026-07-13T12:00:00+00:00",
                )

            self.assertEqual(
                result.source_path.read_bytes(),
                b"different recovered bytes\n",
            )

    def test_manifest_source_hash_and_source_sha_must_match(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=Path(tempdir) / "source-packs",
                created_at="2026-07-13T12:00:00+00:00",
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            manifest["source"]["sha256"] = "0" * 64
            result.manifest_path.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "does not match source.sha256",
            ):
                load_source_pack_manifest(result.manifest_path)

    def test_existing_partial_nonempty_directory_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_dir = Path(tempdir) / "source-packs" / "zotero" / "zotero-ITEM1"
            source_pack_dir.mkdir(parents=True)
            (source_pack_dir / "unexpected.txt").write_text(
                "do not overwrite\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "without manifest/source evidence",
            ):
                write_source_pack_from_recovered_pdf(
                    evidence=_evidence(source_path),
                    source_pack_root=Path(tempdir) / "source-packs",
                )

            self.assertFalse((source_pack_dir / "source.pdf").exists())

    def test_evidence_json_resolves_recovered_pdf_relative_to_evidence_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            evidence_path = _write_evidence_json(tempdir, source_path)

            evidence = load_recovered_pdf_evidence(evidence_path)

            self.assertEqual(evidence.recovered_pdf_path, source_path)
            self.assertEqual(evidence.expected_sha256, FIXTURE_SHA256)

    def test_source_pack_artifact_root_consumes_intake_manifest_hash(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )

            results = write_dry_run_artifacts(
                items=[_make_item()],
                handoff_rows=[_make_handoff_row()],
                config=ArtifactExportConfig(
                    enabled=True,
                    artifact_root="source-pack",
                    source_pack_root=str(source_pack_root),
                    run_id="run-fixture",
                ),
                handoff_enabled=True,
            )

            self.assertEqual(len(results), 1)
            artifact_index = load_artifact_index(results[0].artifact_index_path)
            self.assertEqual(
                artifact_index.source_pack["source_hash"],
                f"sha256:{FIXTURE_SHA256}",
            )
            self.assertEqual(
                artifact_index.stages["source-pack"]["status"],
                "passed",
            )

    def test_handoff_intake_writes_from_matching_fixture_evidence(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            evidence_path = _write_evidence_json(tempdir, source_path)
            source_pack_root = Path(tempdir) / "source-packs"

            results = write_source_packs_from_handoff_evidence(
                handoff_rows=[_make_handoff_row()],
                evidence_path=evidence_path,
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "created")
            self.assertEqual(results[0].source_hash, f"sha256:{FIXTURE_SHA256}")
            self.assertTrue(results[0].manifest_path.is_file())
            self.assertEqual(results[0].source_path.read_bytes(), FIXTURE_BYTES)

    def test_handoff_intake_missing_evidence_fails_before_any_write(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            evidence_path = _write_evidence_json(tempdir, source_path)
            source_pack_root = Path(tempdir) / "source-packs"

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "missing recovered PDF evidence",
            ):
                write_source_packs_from_handoff_evidence(
                    handoff_rows=[
                        _make_handoff_row(),
                        _make_handoff_row(
                            item_key="ITEM2",
                            attachment_key="ATT2",
                            canonical_filename=(
                                "Example Author - 2026 - Missing Fixture.pdf"
                            ),
                            sha256="1" * 64,
                        ),
                    ],
                    evidence_path=evidence_path,
                    source_pack_root=source_pack_root,
                    created_at="2026-07-13T12:00:00+00:00",
                )

            self.assertFalse((source_pack_root / "zotero").exists())

    def test_handoff_intake_requires_handoff_sha256(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            evidence_path = _write_evidence_json(tempdir, source_path)

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "requires handoff row sha256",
            ):
                write_source_packs_from_handoff_evidence(
                    handoff_rows=[_make_handoff_row(sha256=None)],
                    evidence_path=evidence_path,
                    source_pack_root=Path(tempdir) / "source-packs",
                    created_at="2026-07-13T12:00:00+00:00",
                )

    def test_handoff_intake_writes_idempotent_multi_pdf_same_item_pack(self):
        with tempfile.TemporaryDirectory() as tempdir:
            bytes_one = b"main paper bytes\n"
            bytes_two = b"supplement paper bytes\n"
            sha_one = hashlib.sha256(bytes_one).hexdigest()
            sha_two = hashlib.sha256(bytes_two).hexdigest()
            source_one = _write_recovered_pdf_bytes(tempdir, "main.pdf", bytes_one)
            source_two = _write_recovered_pdf_bytes(
                tempdir,
                "supplement.pdf",
                bytes_two,
            )
            evidence_path = Path(tempdir) / "recovered-pdf-evidence.jsonl"
            evidence_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "schema_version": (
                                    "millefeuille-recovered-pdf-evidence/v0.1"
                                ),
                                "source_type": "zotero",
                                "item_key": "ITEM1",
                                "attachment_key": "ATT1",
                                "canonical_filename": "Main.pdf",
                                "recovered_pdf_path": source_one.name,
                                "expected_sha256": sha_one,
                                "content_type": "application/pdf",
                                "file_size_bytes": len(bytes_one),
                            }
                        ),
                        json.dumps(
                            {
                                "schema_version": (
                                    "millefeuille-recovered-pdf-evidence/v0.1"
                                ),
                                "source_type": "zotero",
                                "item_key": "ITEM1",
                                "attachment_key": "ATT2",
                                "canonical_filename": "Supplement.pdf",
                                "recovered_pdf_path": source_two.name,
                                "expected_sha256": sha_two,
                                "content_type": "application/pdf",
                                "file_size_bytes": len(bytes_two),
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            handoff_rows = [
                _make_handoff_row(
                    attachment_key="ATT1",
                    canonical_filename="Main.pdf",
                    sha256=sha_one,
                    file_size_bytes=len(bytes_one),
                ),
                _make_handoff_row(
                    attachment_key="ATT2",
                    canonical_filename="Supplement.pdf",
                    sha256=sha_two,
                    file_size_bytes=len(bytes_two),
                ),
            ]
            source_pack_root = Path(tempdir) / "source-packs"
            results = write_source_packs_from_handoff_evidence(
                handoff_rows=handoff_rows,
                evidence_path=evidence_path,
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )

            self.assertEqual(len(results), 1)
            result = results[0]
            self.assertEqual(result.status, "created")
            self.assertEqual(len(result.source_paths), 2)
            self.assertFalse((result.source_pack_dir / "source.pdf").exists())
            self.assertEqual(
                {path.read_bytes() for path in result.source_paths},
                {bytes_one, bytes_two},
            )
            expected_pack_hash = aggregate_source_hash([sha_one, sha_two])
            self.assertEqual(result.source_hash, expected_pack_hash)
            manifest = load_source_pack_manifest(result.manifest_path)
            self.assertEqual(
                manifest["schema_version"],
                "millefeuille-source-pack-manifest/v0.2",
            )
            self.assertEqual(manifest["identity"]["pdf_count"], 2)
            self.assertEqual(len(manifest["sources"]), 2)
            self.assertTrue(
                all(
                    source["ref"].startswith("sources/")
                    for source in manifest["sources"]
                )
            )
            supplement_entry = next(
                source
                for source in manifest["sources"]
                if source["identity"]["zotero_attachment_key"] == "ATT2"
            )
            self.assertEqual(supplement_entry["sha256"], sha_two)

            rerun = write_source_packs_from_handoff_evidence(
                handoff_rows=list(reversed(handoff_rows)),
                evidence_path=evidence_path,
                source_pack_root=source_pack_root,
                created_at="2026-07-14T12:00:00+00:00",
            )
            self.assertEqual(rerun[0].status, "existing")
            self.assertEqual(rerun[0].source_paths, result.source_paths)

            artifacts = write_dry_run_artifacts(
                items=[_make_item()],
                handoff_rows=handoff_rows,
                config=ArtifactExportConfig(
                    enabled=True,
                    artifact_root="source-pack",
                    source_pack_root=str(source_pack_root),
                    run_id="run-multi-fixture",
                ),
                handoff_enabled=True,
            )
            artifact_index = load_artifact_index(artifacts[0].artifact_index_path)
            self.assertEqual(
                artifact_index.source_pack["source_hash"],
                expected_pack_hash,
            )

            drifted_manifest = dict(manifest)
            drifted_manifest["source_hash"] = "sha256-aggregate:" + ("0" * 64)
            result.manifest_path.write_text(
                json.dumps(drifted_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "does not match sources aggregate",
            ):
                load_source_pack_manifest(result.manifest_path)

            result.manifest_path.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            result.source_paths[1].write_bytes(b"drifted supplement bytes\n")
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "source hash drift",
            ):
                write_source_packs_from_handoff_evidence(
                    handoff_rows=handoff_rows,
                    evidence_path=evidence_path,
                    source_pack_root=source_pack_root,
                    created_at="2026-07-14T12:00:00+00:00",
                )

    def test_native_extraction_fixture_writes_sidecars_after_source_pack_verification(
        self,
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            markdown_path = _write_markdown(
                tempdir,
                "native-fulltext.md",
                "Native fixture page 1\n\nNative fixture page 2\n",
            )
            evidence_path = _write_native_extraction_evidence_json(
                tempdir,
                markdown_path,
            )

            write_results = write_native_extractions_from_evidence(
                evidence_path=evidence_path,
                source_pack_root=source_pack_root,
            )

            self.assertEqual(len(write_results), 1)
            self.assertEqual(write_results[0].status, "created")
            native_dir = result.source_pack_dir / "extractions" / "native"
            self.assertEqual(
                (native_dir / "fulltext.md").read_text(encoding="utf-8"),
                markdown_path.read_text(encoding="utf-8"),
            )
            payload = load_native_extraction_sidecar(native_dir / "evidence.json")
            self.assertEqual(
                payload["schema_version"],
                "millefeuille-native-extraction-evidence/v0.1",
            )
            self.assertEqual(payload["source_hash"], f"sha256:{FIXTURE_SHA256}")
            self.assertEqual(payload["tool"], "PyPDF2-fixture")

    def test_ocr_extraction_fixture_writes_sidecars_after_source_pack_verification(
        self,
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            markdown_path = _write_markdown(
                tempdir,
                "ocr-fulltext.md",
                "OCR fixture page 1\n\nOCR fixture page 2\n",
            )
            evidence_path = _write_ocr_extraction_evidence_json(
                tempdir,
                markdown_path,
            )

            write_results = write_ocr_extractions_from_evidence(
                evidence_path=evidence_path,
                source_pack_root=source_pack_root,
            )

            self.assertEqual(len(write_results), 1)
            self.assertEqual(write_results[0].status, "created")
            ocr_dir = result.source_pack_dir / "extractions" / "mistral-ocr"
            self.assertEqual(
                (ocr_dir / "fulltext.md").read_text(encoding="utf-8"),
                markdown_path.read_text(encoding="utf-8"),
            )
            payload = load_ocr_extraction_sidecar(ocr_dir / "evidence.json")
            self.assertEqual(
                payload["schema_version"],
                "millefeuille-ocr-extraction-evidence/v0.1",
            )
            self.assertEqual(payload["requested_model"], "mistral-ocr-latest")
            self.assertEqual(
                payload["provider_version"],
                "mistral-ocr-4-0-fixture",
            )

    def test_extraction_fixture_batch_preflights_missing_source_pack_before_any_write(
        self,
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            markdown_path = _write_markdown(
                tempdir,
                "native-fulltext.md",
                "Native fixture page 1\n",
            )
            evidence_path = Path(tempdir) / "native-extraction-evidence.jsonl"
            evidence_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "schema_version": (
                                    "millefeuille-native-extraction-evidence/v0.1"
                                ),
                                "source_type": "zotero",
                                "item_key": "ITEM1",
                                "attachment_key": "ATT1",
                                "canonical_filename": (
                                    "Example Author - 2026 - Intake Fixture.pdf"
                                ),
                                "markdown_path": markdown_path.name,
                                "expected_sha256": FIXTURE_SHA256,
                                "page_count": 1,
                                "tool": "PyPDF2-fixture",
                            }
                        ),
                        json.dumps(
                            {
                                "schema_version": (
                                    "millefeuille-native-extraction-evidence/v0.1"
                                ),
                                "source_type": "zotero",
                                "item_key": "ITEM2",
                                "attachment_key": "ATT2",
                                "canonical_filename": "Missing.pdf",
                                "markdown_path": markdown_path.name,
                                "expected_sha256": "1" * 64,
                                "page_count": 1,
                                "tool": "PyPDF2-fixture",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "could not read source-pack manifest",
            ):
                write_native_extractions_from_evidence(
                    evidence_path=evidence_path,
                    source_pack_root=source_pack_root,
                )

            native_evidence_path = (
                result.source_pack_dir / "extractions" / "native" / "evidence.json"
            )
            self.assertFalse(native_evidence_path.exists())

    def test_extraction_fixture_batch_rejects_duplicate_item_attachment_records(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            markdown_path = _write_markdown(
                tempdir,
                "native-fulltext.md",
                "Native fixture page 1\n",
            )
            evidence_path = Path(tempdir) / "native-extraction-evidence.jsonl"
            record = {
                "schema_version": "millefeuille-native-extraction-evidence/v0.1",
                "source_type": "zotero",
                "item_key": "ITEM1",
                "attachment_key": "ATT1",
                "canonical_filename": "Example Author - 2026 - Intake Fixture.pdf",
                "markdown_path": markdown_path.name,
                "expected_sha256": FIXTURE_SHA256,
                "page_count": 1,
                "tool": "PyPDF2-fixture",
            }
            evidence_path.write_text(
                "\n".join([json.dumps(record), json.dumps(record)]) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "duplicate native extraction evidence",
            ):
                write_native_extractions_from_evidence(
                    evidence_path=evidence_path,
                    source_pack_root=source_pack_root,
                )

            self.assertFalse(
                (
                    result.source_pack_dir / "extractions" / "native" / "evidence.json"
                ).exists()
            )

    def test_route_selection_fixture_writes_selected_fulltext(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            native_markdown_path = _write_markdown(
                tempdir,
                "native-fulltext.md",
                "Native fixture page 1\n",
            )
            ocr_markdown_path = _write_markdown(
                tempdir,
                "ocr-fulltext.md",
                "OCR fixture page 1\n",
            )
            write_native_extractions_from_evidence(
                evidence_path=_write_native_extraction_evidence_json(
                    tempdir,
                    native_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            write_ocr_extractions_from_evidence(
                evidence_path=_write_ocr_extraction_evidence_json(
                    tempdir,
                    ocr_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            selected_markdown_path = _write_markdown(
                tempdir,
                "selected-fulltext.md",
                "Merged fixture page 1\n",
            )
            route_evidence_path = _write_route_selection_evidence_json(
                tempdir,
                selected_markdown_path,
            )

            write_results = write_route_selections_from_evidence(
                evidence_path=route_evidence_path,
                source_pack_root=source_pack_root,
            )

            self.assertEqual(len(write_results), 1)
            self.assertEqual(write_results[0].status, "created")
            selected_dir = result.source_pack_dir / "selected"
            self.assertEqual(
                (selected_dir / "fulltext.md").read_text(encoding="utf-8"),
                selected_markdown_path.read_text(encoding="utf-8"),
            )
            payload = load_route_selection_sidecar(selected_dir / "route.json")
            self.assertEqual(
                payload["schema_version"],
                "millefeuille-route-selection-evidence/v0.1",
            )
            self.assertEqual(payload["selected_route"], "merged-dual")
            self.assertEqual(
                payload["extraction_routes"],
                ["native", "mistral-ocr"],
            )
            self.assertTrue(payload["dual_extraction_complete"])

    def test_route_selection_fixture_requires_both_extractions_for_merged_dual(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            native_markdown_path = _write_markdown(
                tempdir,
                "native-fulltext.md",
                "Native fixture page 1\n",
            )
            write_native_extractions_from_evidence(
                evidence_path=_write_native_extraction_evidence_json(
                    tempdir,
                    native_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            route_evidence_path = _write_route_selection_evidence_json(
                tempdir,
                _write_markdown(
                    tempdir,
                    "selected-fulltext.md",
                    "Merged fixture page 1\n",
                ),
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "merged-dual requires both native and OCR extraction evidence",
            ):
                write_route_selections_from_evidence(
                    evidence_path=route_evidence_path,
                    source_pack_root=source_pack_root,
                )

    def test_structure_fixture_writes_after_route_selection(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            native_markdown_path = _write_markdown(
                tempdir,
                "native-fulltext.md",
                "Native fixture page 1\n",
            )
            ocr_markdown_path = _write_markdown(
                tempdir,
                "ocr-fulltext.md",
                "OCR fixture page 1\n",
            )
            write_native_extractions_from_evidence(
                evidence_path=_write_native_extraction_evidence_json(
                    tempdir,
                    native_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            write_ocr_extractions_from_evidence(
                evidence_path=_write_ocr_extraction_evidence_json(
                    tempdir,
                    ocr_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            route_evidence_path = _write_route_selection_evidence_json(
                tempdir,
                _write_markdown(
                    tempdir,
                    "selected-fulltext.md",
                    "Merged fixture page 1\n",
                ),
            )
            write_route_selections_from_evidence(
                evidence_path=route_evidence_path,
                source_pack_root=source_pack_root,
            )
            outline_path = _write_markdown(
                tempdir,
                "outline.md",
                "# Fixture outline\n\n- Introduction p.1\n",
            )
            structure_evidence_path = _write_structure_evidence_json(
                tempdir,
                _write_structure_payload_json(tempdir),
                outline_path,
            )

            write_results = write_structures_from_evidence(
                evidence_path=structure_evidence_path,
                source_pack_root=source_pack_root,
            )
            rerun_results = write_structures_from_evidence(
                evidence_path=structure_evidence_path,
                source_pack_root=source_pack_root,
            )

            self.assertEqual(len(write_results), 1)
            self.assertEqual(write_results[0].status, "created")
            self.assertEqual(rerun_results[0].status, "existing")
            structure_dir = result.source_pack_dir / "structure"
            payload = load_structure_sidecar(structure_dir / "structure.json")
            self.assertEqual(
                payload["schema_version"],
                "millefeuille-structure-evidence/v0.1",
            )
            self.assertEqual(payload["source_hash"], f"sha256:{FIXTURE_SHA256}")
            self.assertEqual(payload["selected_route"], "merged-dual")
            self.assertEqual(payload["route_evidence_ref"], "selected/route.json")
            self.assertEqual(payload["selected_fulltext_ref"], "selected/fulltext.md")
            self.assertEqual(payload["section_count"], 2)
            self.assertEqual(payload["table_count"], 1)
            self.assertEqual(
                (structure_dir / "outline.md").read_text(encoding="utf-8"),
                outline_path.read_text(encoding="utf-8"),
            )

    def test_structure_fixture_requires_route_selection_sidecar(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            structure_evidence_path = _write_structure_evidence_json(
                tempdir,
                _write_structure_payload_json(tempdir),
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "could not read route selection evidence",
            ):
                write_structures_from_evidence(
                    evidence_path=structure_evidence_path,
                    source_pack_root=source_pack_root,
                )

            self.assertFalse(
                (result.source_pack_dir / "structure" / "structure.json").exists()
            )

    def test_summary_fixture_writes_run_scoped_hierarchical_summary(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            native_markdown_path = _write_markdown(
                tempdir,
                "native-fulltext.md",
                "Native fixture page 1\n",
            )
            ocr_markdown_path = _write_markdown(
                tempdir,
                "ocr-fulltext.md",
                "OCR fixture page 1\n",
            )
            write_native_extractions_from_evidence(
                evidence_path=_write_native_extraction_evidence_json(
                    tempdir,
                    native_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            write_ocr_extractions_from_evidence(
                evidence_path=_write_ocr_extraction_evidence_json(
                    tempdir,
                    ocr_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            write_route_selections_from_evidence(
                evidence_path=_write_route_selection_evidence_json(
                    tempdir,
                    _write_markdown(
                        tempdir,
                        "selected-fulltext.md",
                        "Merged fixture page 1\n",
                    ),
                ),
                source_pack_root=source_pack_root,
            )
            write_structures_from_evidence(
                evidence_path=_write_structure_evidence_json(
                    tempdir,
                    _write_structure_payload_json(tempdir),
                ),
                source_pack_root=source_pack_root,
            )
            _write_markdown(tempdir, "page-1.md", "Page 1 summary.\n")
            _write_markdown(tempdir, "full-paper.md", "Full paper summary.\n")
            summary_evidence_path = _write_summary_evidence_json(
                tempdir,
                _write_summary_fixture_json(tempdir),
            )

            write_results = write_summaries_from_evidence(
                evidence_path=summary_evidence_path,
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )
            rerun_results = write_summaries_from_evidence(
                evidence_path=summary_evidence_path,
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )

            self.assertEqual(len(write_results), 1)
            self.assertEqual(write_results[0].status, "created")
            self.assertEqual(rerun_results[0].status, "existing")
            summary_path = (
                result.source_pack_dir
                / "analyses"
                / "millefeuille"
                / "run-fixture"
                / "summaries"
                / "hierarchical-summary.json"
            )
            payload = load_hierarchical_summary(summary_path)
            self.assertEqual(
                payload["schema_version"],
                "millefeuille-hierarchical-summary/v0.1",
            )
            self.assertEqual(payload["paper_id"], result.paper_id)
            self.assertEqual(payload["run_id"], "run-fixture")
            self.assertEqual(
                payload["summaries"][0]["text_ref"],
                "texts/page-1.md",
            )
            self.assertEqual(
                payload["summaries"][1]["depends_on"],
                ["page-1"],
            )
            self.assertEqual(
                (summary_path.parent / "texts" / "full-paper.md").read_text(
                    encoding="utf-8"
                ),
                "Full paper summary.\n",
            )

    def test_summary_fixture_requires_structure_sidecar(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            _write_markdown(tempdir, "page-1.md", "Page 1 summary.\n")
            _write_markdown(tempdir, "full-paper.md", "Full paper summary.\n")
            summary_evidence_path = _write_summary_evidence_json(
                tempdir,
                _write_summary_fixture_json(tempdir),
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "could not read structure evidence",
            ):
                write_summaries_from_evidence(
                    evidence_path=summary_evidence_path,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertFalse(
                (
                    result.source_pack_dir
                    / "analyses"
                    / "millefeuille"
                    / "run-fixture"
                    / "summaries"
                    / "hierarchical-summary.json"
                ).exists()
            )

    def test_card_fixture_writes_run_scoped_paper_card(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            native_markdown_path = _write_markdown(
                tempdir,
                "native-fulltext.md",
                "Native fixture page 1\n",
            )
            ocr_markdown_path = _write_markdown(
                tempdir,
                "ocr-fulltext.md",
                "OCR fixture page 1\n",
            )
            write_native_extractions_from_evidence(
                evidence_path=_write_native_extraction_evidence_json(
                    tempdir,
                    native_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            write_ocr_extractions_from_evidence(
                evidence_path=_write_ocr_extraction_evidence_json(
                    tempdir,
                    ocr_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            write_route_selections_from_evidence(
                evidence_path=_write_route_selection_evidence_json(
                    tempdir,
                    _write_markdown(
                        tempdir,
                        "selected-fulltext.md",
                        "Merged fixture page 1\n",
                    ),
                ),
                source_pack_root=source_pack_root,
            )
            write_structures_from_evidence(
                evidence_path=_write_structure_evidence_json(
                    tempdir,
                    _write_structure_payload_json(tempdir),
                ),
                source_pack_root=source_pack_root,
            )
            _write_markdown(tempdir, "page-1.md", "Page 1 summary.\n")
            _write_markdown(tempdir, "full-paper.md", "Full paper summary.\n")
            write_summaries_from_evidence(
                evidence_path=_write_summary_evidence_json(
                    tempdir,
                    _write_summary_fixture_json(tempdir),
                ),
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )
            card_markdown_path = _write_markdown(
                tempdir,
                "paper-card.md",
                "# Fixture Paper Card\n\nA concise thesis.\n",
            )
            card_evidence_path = _write_card_evidence_json(
                tempdir,
                _write_card_fixture_json(tempdir),
                card_markdown_path,
            )

            write_results = write_cards_from_evidence(
                evidence_path=card_evidence_path,
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )
            rerun_results = write_cards_from_evidence(
                evidence_path=card_evidence_path,
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )

            self.assertEqual(len(write_results), 1)
            self.assertEqual(write_results[0].status, "created")
            self.assertEqual(rerun_results[0].status, "existing")
            card_json_path = (
                result.source_pack_dir
                / "analyses"
                / "millefeuille"
                / "run-fixture"
                / "cards"
                / "paper-card.json"
            )
            payload = load_paper_card(card_json_path)
            self.assertEqual(payload["paper_id"], result.paper_id)
            self.assertEqual(
                payload["identity"]["source_hash"],
                f"sha256:{FIXTURE_SHA256}",
            )
            self.assertEqual(
                payload["evidence_refs"],
                [
                    "../summaries/hierarchical-summary.json",
                    "../../../structure/structure.json",
                ],
            )
            self.assertEqual(
                (card_json_path.parent / "paper-card.md").read_text(encoding="utf-8"),
                "# Fixture Paper Card\n\nA concise thesis.\n",
            )

    def test_card_fixture_requires_hierarchical_summary(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            native_markdown_path = _write_markdown(
                tempdir,
                "native-fulltext.md",
                "Native fixture page 1\n",
            )
            ocr_markdown_path = _write_markdown(
                tempdir,
                "ocr-fulltext.md",
                "OCR fixture page 1\n",
            )
            write_native_extractions_from_evidence(
                evidence_path=_write_native_extraction_evidence_json(
                    tempdir,
                    native_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            write_ocr_extractions_from_evidence(
                evidence_path=_write_ocr_extraction_evidence_json(
                    tempdir,
                    ocr_markdown_path,
                ),
                source_pack_root=source_pack_root,
            )
            write_route_selections_from_evidence(
                evidence_path=_write_route_selection_evidence_json(
                    tempdir,
                    _write_markdown(
                        tempdir,
                        "selected-fulltext.md",
                        "Merged fixture page 1\n",
                    ),
                ),
                source_pack_root=source_pack_root,
            )
            write_structures_from_evidence(
                evidence_path=_write_structure_evidence_json(
                    tempdir,
                    _write_structure_payload_json(tempdir),
                ),
                source_pack_root=source_pack_root,
            )
            card_evidence_path = _write_card_evidence_json(
                tempdir,
                _write_card_fixture_json(tempdir),
                _write_markdown(
                    tempdir,
                    "paper-card.md",
                    "# Fixture Paper Card\n",
                ),
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "paper card fixture requires hierarchical summary",
            ):
                write_cards_from_evidence(
                    evidence_path=card_evidence_path,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertFalse(
                (
                    result.source_pack_dir
                    / "analyses"
                    / "millefeuille"
                    / "run-fixture"
                    / "cards"
                    / "paper-card.json"
                ).exists()
            )

    def test_index_fixture_writes_run_scoped_index_status(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            write_native_extractions_from_evidence(
                evidence_path=_write_native_extraction_evidence_json(
                    tempdir,
                    _write_markdown(
                        tempdir,
                        "native-fulltext.md",
                        "Native fixture page 1\n",
                    ),
                ),
                source_pack_root=source_pack_root,
            )
            write_ocr_extractions_from_evidence(
                evidence_path=_write_ocr_extraction_evidence_json(
                    tempdir,
                    _write_markdown(
                        tempdir,
                        "ocr-fulltext.md",
                        "OCR fixture page 1\n",
                    ),
                ),
                source_pack_root=source_pack_root,
            )
            write_route_selections_from_evidence(
                evidence_path=_write_route_selection_evidence_json(
                    tempdir,
                    _write_markdown(
                        tempdir,
                        "selected-fulltext.md",
                        "Merged fixture page 1\n",
                    ),
                ),
                source_pack_root=source_pack_root,
            )
            write_structures_from_evidence(
                evidence_path=_write_structure_evidence_json(
                    tempdir,
                    _write_structure_payload_json(tempdir),
                ),
                source_pack_root=source_pack_root,
            )
            _write_markdown(tempdir, "page-1.md", "Page 1 summary.\n")
            _write_markdown(tempdir, "full-paper.md", "Full paper summary.\n")
            write_summaries_from_evidence(
                evidence_path=_write_summary_evidence_json(
                    tempdir,
                    _write_summary_fixture_json(tempdir),
                ),
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )
            write_cards_from_evidence(
                evidence_path=_write_card_evidence_json(
                    tempdir,
                    _write_card_fixture_json(tempdir),
                    _write_markdown(
                        tempdir,
                        "paper-card.md",
                        "# Fixture Paper Card\n\nA concise thesis.\n",
                    ),
                ),
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )
            index_evidence_path = _write_index_evidence_json(
                tempdir,
                _write_index_fixture_json(tempdir),
            )
            card_path = (
                result.source_pack_dir
                / "analyses"
                / "millefeuille"
                / "run-fixture"
                / "cards"
                / "paper-card.json"
            )
            planned_card_payload = load_paper_card(card_path)
            planned_card_bytes = card_path.read_bytes()

            write_results = write_indexes_from_evidence(
                evidence_path=index_evidence_path,
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )
            observed_card_bytes = card_path.read_bytes()
            rerun_results = write_indexes_from_evidence(
                evidence_path=index_evidence_path,
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )
            card_rerun_results = write_cards_from_evidence(
                evidence_path=_write_card_evidence_json(
                    tempdir,
                    _write_card_fixture_json(tempdir),
                    Path(tempdir) / "paper-card.md",
                ),
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )

            self.assertEqual(len(write_results), 1)
            self.assertEqual(write_results[0].status, "created")
            self.assertEqual(rerun_results[0].status, "existing")
            self.assertEqual(card_rerun_results[0].status, "existing")
            self.assertEqual(card_path.read_bytes(), observed_card_bytes)
            run_dir = card_path.parents[1]
            transaction_root = run_dir / CARD_INDEX_TRANSACTION_ROOT_REF
            transaction_dirs = [
                path for path in transaction_root.iterdir() if path.is_dir()
            ]
            self.assertEqual(len(transaction_dirs), 1)
            transaction_dir = transaction_dirs[0]
            displaced_name = (
                "displaced-card.json" if os.name == "nt" else "card-exchange.json"
            )
            self.assertEqual(
                (transaction_dir / displaced_name).read_bytes(),
                planned_card_bytes,
            )
            self.assertFalse(list((run_dir / "cards").glob(".paper-card.json.*")))
            self.assertFalse(list((run_dir / "index").glob(".index-status.json.*")))
            index_status_path = (
                result.source_pack_dir
                / "analyses"
                / "millefeuille"
                / "run-fixture"
                / "index"
                / "index-status.json"
            )
            payload = load_retrieval_index_status(index_status_path)
            self.assertEqual(
                (transaction_dir / "index-status.json").read_bytes(),
                index_status_path.read_bytes(),
            )
            self.assertEqual(payload["paper_id"], result.paper_id)
            self.assertEqual(payload["run_id"], "run-fixture")
            self.assertEqual(payload["source_hash"], f"sha256:{FIXTURE_SHA256}")
            self.assertEqual(
                payload["selected_fulltext_ref"],
                "../../../../selected/fulltext.md",
            )
            self.assertEqual(
                payload["summary_ref"],
                "../summaries/hierarchical-summary.json",
            )
            self.assertEqual(payload["paper_card_ref"], "../cards/paper-card.json")
            self.assertEqual(payload["lanes"][0]["skip_reason"], "fixture-only run")
            self.assertEqual(
                payload["lanes"][1]["chunking_profile"]["strategy"],
                "section",
            )
            card_payload = load_paper_card(card_path)
            planned_without_state = dict(planned_card_payload)
            observed_without_state = dict(card_payload)
            planned_without_state.pop("index_state")
            observed_without_state.pop("index_state")
            self.assertEqual(observed_without_state, planned_without_state)
            self.assertEqual(card_payload["identity"]["title"], "Fixture Paper")
            self.assertEqual(card_payload["run_id"], "run-fixture")
            self.assertEqual(
                card_payload["index_state"],
                {
                    "phase": "observed",
                    "status_ref": "../index/index-status.json",
                    "lanes": [
                        {"lane": "openkb", "status": "skipped"},
                        {"lane": "pageindex", "status": "previewed"},
                    ],
                },
            )
            original_index_bytes = index_status_path.read_bytes()
            conflicting_card = dict(card_payload)
            conflicting_card["index_state"] = {
                **card_payload["index_state"],
                "lanes": [
                    {"lane": "openkb", "status": "written"},
                    {"lane": "pageindex", "status": "previewed"},
                ],
            }
            card_path.write_bytes(canonical_json_bytes(conflicting_card))

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "observed index_state conflicts",
            ):
                write_indexes_from_evidence(
                    evidence_path=index_evidence_path,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertEqual(index_status_path.read_bytes(), original_index_bytes)

    def test_observed_card_rerun_requires_exact_canonical_index_join(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir, card_evidence, index_evidence = (
                _prepare_card_index_fixture(tempdir)
            )
            write_indexes_from_evidence(
                evidence_path=index_evidence,
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )
            card_path = run_dir / "cards" / "paper-card.json"
            index_path = run_dir / "index" / "index-status.json"
            observed_bytes = card_path.read_bytes()
            tampered = json.loads(observed_bytes)
            tampered["index_state"]["lanes"][0]["status"] = "written"
            card_path.write_bytes(canonical_json_bytes(tampered))

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "observed index_state conflicts",
            ):
                write_cards_from_evidence(
                    evidence_path=card_evidence,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            card_path.write_bytes(observed_bytes)
            index_path.unlink()
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "retrieval index status",
            ):
                write_cards_from_evidence(
                    evidence_path=card_evidence,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

    def test_card_refresh_detects_mutation_after_last_precommit_read(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir, _card_evidence, index_evidence = (
                _prepare_card_index_fixture(tempdir)
            )
            card_path = run_dir / "cards" / "paper-card.json"
            concurrent = json.loads(card_path.read_bytes())
            concurrent["one_line_thesis"] = "Concurrent writer value."
            concurrent_bytes = (
                json.dumps(concurrent, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")

            def mutate_after_last_read(_plan):
                card_path.write_bytes(concurrent_bytes)

            with (
                patch(
                    "millefeuille.domain.index_fixtures._before_card_exchange",
                    side_effect=mutate_after_last_read,
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "atomic index-state commit boundary",
                ),
            ):
                write_indexes_from_evidence(
                    evidence_path=index_evidence,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertEqual(card_path.read_bytes(), concurrent_bytes)
            self.assertTrue((run_dir / "index" / "index-status.json").is_file())
            transaction_dirs = list(
                (run_dir / CARD_INDEX_TRANSACTION_ROOT_REF).iterdir()
            )
            self.assertEqual(len(transaction_dirs), 1)
            rejected = transaction_dirs[0] / "card-exchange.json"
            self.assertTrue(rejected.is_file())
            self.assertEqual(
                json.loads(rejected.read_text(encoding="utf-8"))["index_state"][
                    "phase"
                ],
                "observed",
            )

    def test_card_refresh_rejects_hardlinked_staged_replacement(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir, _card_evidence, index_evidence = (
                _prepare_card_index_fixture(tempdir)
            )
            card_path = run_dir / "cards" / "paper-card.json"
            planned_bytes = card_path.read_bytes()
            alias_path = run_dir / "aliased-card.json"

            def hardlink_replacement_at_preexchange(_plan):
                transaction_dirs = list(
                    (run_dir / CARD_INDEX_TRANSACTION_ROOT_REF).iterdir()
                )
                self.assertEqual(len(transaction_dirs), 1)
                os.link(
                    transaction_dirs[0] / "card-exchange.json",
                    alias_path,
                )

            with (
                patch(
                    "millefeuille.domain.index_fixtures._before_card_exchange",
                    side_effect=hardlink_replacement_at_preexchange,
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "card replacement must be singly linked",
                ),
            ):
                write_indexes_from_evidence(
                    evidence_path=index_evidence,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertEqual(card_path.read_bytes(), planned_bytes)
            self.assertTrue(alias_path.is_file())
            self.assertTrue((run_dir / "index" / "index-status.json").is_file())

    def test_rollback_rejects_displaced_recovery_name_swap(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir, _card_evidence, index_evidence = (
                _prepare_card_index_fixture(tempdir)
            )
            card_path = run_dir / "cards" / "paper-card.json"
            concurrent = json.loads(card_path.read_bytes())
            concurrent["one_line_thesis"] = "Concurrent writer value."
            concurrent_bytes = canonical_json_bytes(concurrent)
            attacker_bytes = b"attacker replacement at displaced name\n"
            preserved_paths: list[Path] = []

            def mutate_after_last_read(_plan):
                card_path.write_bytes(concurrent_bytes)

            def swap_displaced_name(displaced):
                preserved_path = displaced.path.with_name(
                    "operator-preserved-card.json"
                )
                os.replace(displaced.path, preserved_path)
                displaced.path.write_bytes(attacker_bytes)
                preserved_paths.append(preserved_path)

            with (
                patch(
                    "millefeuille.domain.index_fixtures._before_card_exchange",
                    side_effect=mutate_after_last_read,
                ),
                patch(
                    "millefeuille.domain.index_fixtures._before_card_rollback",
                    side_effect=swap_displaced_name,
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "captured displaced paper card changed before rollback",
                ),
            ):
                write_indexes_from_evidence(
                    evidence_path=index_evidence,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertEqual(len(preserved_paths), 1)
            self.assertEqual(preserved_paths[0].read_bytes(), concurrent_bytes)
            displaced_path = preserved_paths[0].with_name("displaced-card.json")
            if os.name != "nt":
                displaced_path = preserved_paths[0].with_name("card-exchange.json")
            self.assertEqual(displaced_path.read_bytes(), attacker_bytes)
            self.assertEqual(
                load_paper_card(card_path)["index_state"]["phase"],
                "observed",
            )

    def test_oversized_displaced_card_fails_bounded_capture(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir, _card_evidence, index_evidence = (
                _prepare_card_index_fixture(tempdir)
            )
            card_path = run_dir / "cards" / "paper-card.json"
            oversized_bytes = b"x" * (CARD_INDEX_TRANSACTION_MAX_CARD_BYTES + 1)

            def grow_after_last_read(_plan):
                card_path.write_bytes(oversized_bytes)

            with (
                patch(
                    "millefeuille.domain.index_fixtures._before_card_exchange",
                    side_effect=grow_after_last_read,
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "could not securely capture",
                ) as raised,
            ):
                write_indexes_from_evidence(
                    evidence_path=index_evidence,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertIsNotNone(raised.exception.__cause__)
            self.assertIn(
                "byte card transaction limit",
                str(raised.exception.__cause__),
            )
            transaction_dirs = list(
                (run_dir / CARD_INDEX_TRANSACTION_ROOT_REF).iterdir()
            )
            self.assertEqual(len(transaction_dirs), 1)
            displaced_name = (
                "displaced-card.json" if os.name == "nt" else "card-exchange.json"
            )
            self.assertEqual(
                (transaction_dirs[0] / displaced_name).stat().st_size,
                len(oversized_bytes),
            )
            self.assertEqual(
                load_paper_card(card_path)["index_state"]["phase"],
                "observed",
            )

    def test_rollback_validates_restored_card_after_atomic_restore(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir, _card_evidence, index_evidence = (
                _prepare_card_index_fixture(tempdir)
            )
            card_path = run_dir / "cards" / "paper-card.json"
            concurrent = json.loads(card_path.read_bytes())
            concurrent["one_line_thesis"] = "Concurrent writer value."
            concurrent_bytes = canonical_json_bytes(concurrent)
            post_restore_bytes = b"hostile post-restore mutation\n"

            def mutate_after_last_read(_plan):
                card_path.write_bytes(concurrent_bytes)

            def mutate_after_restore(_displaced):
                card_path.write_bytes(post_restore_bytes)

            with (
                patch(
                    "millefeuille.domain.index_fixtures._before_card_exchange",
                    side_effect=mutate_after_last_read,
                ),
                patch(
                    "millefeuille.domain.index_fixtures._after_card_rollback",
                    side_effect=mutate_after_restore,
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "rollback did not restore the exact displaced entry",
                ),
            ):
                write_indexes_from_evidence(
                    evidence_path=index_evidence,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertEqual(card_path.read_bytes(), post_restore_bytes)
            transaction_dirs = list(
                (run_dir / CARD_INDEX_TRANSACTION_ROOT_REF).iterdir()
            )
            self.assertEqual(len(transaction_dirs), 1)
            self.assertEqual(
                json.loads((transaction_dirs[0] / "card-exchange.json").read_bytes())[
                    "index_state"
                ]["phase"],
                "observed",
            )

    def test_concurrent_index_creation_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir, _card_evidence, index_evidence = (
                _prepare_card_index_fixture(tempdir)
            )
            card_path = run_dir / "cards" / "paper-card.json"
            planned_bytes = card_path.read_bytes()
            index_path = run_dir / "index" / "index-status.json"
            concurrent_bytes = b'{"concurrent":"index"}\n'

            def create_concurrent_destination(_src, dst):
                Path(dst).write_bytes(concurrent_bytes)
                raise FileExistsError

            with (
                patch(
                    "millefeuille.domain.index_fixtures._move_file_no_replace",
                    side_effect=create_concurrent_destination,
                ),
                self.assertRaises(MillefeuilleContractError),
            ):
                write_indexes_from_evidence(
                    evidence_path=index_evidence,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertEqual(index_path.read_bytes(), concurrent_bytes)
            self.assertEqual(card_path.read_bytes(), planned_bytes)

    def test_index_recovery_mutation_cannot_change_canonical_index(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir, _card_evidence, index_evidence = (
                _prepare_card_index_fixture(tempdir)
            )
            write_indexes_from_evidence(
                evidence_path=index_evidence,
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )

            index_path = run_dir / "index" / "index-status.json"
            canonical_bytes = index_path.read_bytes()
            transaction_dirs = list(
                (run_dir / CARD_INDEX_TRANSACTION_ROOT_REF).iterdir()
            )
            self.assertEqual(len(transaction_dirs), 1)
            recovery_path = transaction_dirs[0] / "index-status.json"
            self.assertEqual(recovery_path.read_bytes(), canonical_bytes)
            self.assertNotEqual(
                (recovery_path.stat().st_dev, recovery_path.stat().st_ino),
                (index_path.stat().st_dev, index_path.stat().st_ino),
            )

            recovery_path.write_bytes(b'{"mutated":"recovery"}\n')

            self.assertEqual(index_path.read_bytes(), canonical_bytes)
            self.assertEqual(
                load_paper_card(run_dir / "cards" / "paper-card.json")[
                    "index_state"
                ]["phase"],
                "observed",
            )

    def test_interrupted_exchange_preserves_card_and_drifted_transaction_entry(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir, _card_evidence, index_evidence = (
                _prepare_card_index_fixture(tempdir)
            )
            card_path = run_dir / "cards" / "paper-card.json"
            planned_bytes = card_path.read_bytes()

            with (
                patch(
                    "millefeuille.domain.index_fixtures._atomic_capture_replace",
                    side_effect=MillefeuilleContractError(
                        "simulated interrupted exchange"
                    ),
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "simulated interrupted exchange",
                ),
            ):
                write_indexes_from_evidence(
                    evidence_path=index_evidence,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertEqual(card_path.read_bytes(), planned_bytes)
            transaction_dirs = list(
                (run_dir / CARD_INDEX_TRANSACTION_ROOT_REF).iterdir()
            )
            self.assertEqual(len(transaction_dirs), 1)
            recovery_entry = transaction_dirs[0] / "card-exchange.json"
            self.assertTrue(recovery_entry.is_file())
            external_bytes = b"external replacement at recovery name\n"
            recovery_entry.write_bytes(external_bytes)

            with self.assertRaises(MillefeuilleContractError):
                write_indexes_from_evidence(
                    evidence_path=index_evidence,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertEqual(recovery_entry.read_bytes(), external_bytes)
            self.assertEqual(card_path.read_bytes(), planned_bytes)

    def test_index_fixture_requires_paper_card(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            source_pack_root = Path(tempdir) / "source-packs"
            result = write_source_pack_from_recovered_pdf(
                evidence=_evidence(source_path),
                source_pack_root=source_pack_root,
                created_at="2026-07-13T12:00:00+00:00",
            )
            write_native_extractions_from_evidence(
                evidence_path=_write_native_extraction_evidence_json(
                    tempdir,
                    _write_markdown(
                        tempdir,
                        "native-fulltext.md",
                        "Native fixture page 1\n",
                    ),
                ),
                source_pack_root=source_pack_root,
            )
            write_ocr_extractions_from_evidence(
                evidence_path=_write_ocr_extraction_evidence_json(
                    tempdir,
                    _write_markdown(
                        tempdir,
                        "ocr-fulltext.md",
                        "OCR fixture page 1\n",
                    ),
                ),
                source_pack_root=source_pack_root,
            )
            write_route_selections_from_evidence(
                evidence_path=_write_route_selection_evidence_json(
                    tempdir,
                    _write_markdown(
                        tempdir,
                        "selected-fulltext.md",
                        "Merged fixture page 1\n",
                    ),
                ),
                source_pack_root=source_pack_root,
            )
            write_structures_from_evidence(
                evidence_path=_write_structure_evidence_json(
                    tempdir,
                    _write_structure_payload_json(tempdir),
                ),
                source_pack_root=source_pack_root,
            )
            _write_markdown(tempdir, "page-1.md", "Page 1 summary.\n")
            _write_markdown(tempdir, "full-paper.md", "Full paper summary.\n")
            write_summaries_from_evidence(
                evidence_path=_write_summary_evidence_json(
                    tempdir,
                    _write_summary_fixture_json(tempdir),
                ),
                source_pack_root=source_pack_root,
                run_id="run-fixture",
            )
            index_evidence_path = _write_index_evidence_json(
                tempdir,
                _write_index_fixture_json(tempdir),
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "could not read paper card",
            ):
                write_indexes_from_evidence(
                    evidence_path=index_evidence_path,
                    source_pack_root=source_pack_root,
                    run_id="run-fixture",
                )

            self.assertFalse(
                (
                    result.source_pack_dir
                    / "analyses"
                    / "millefeuille"
                    / "run-fixture"
                    / "index"
                    / "index-status.json"
                ).exists()
            )


class TestSourcePackIntakeCli(unittest.TestCase):
    def test_cli_writes_source_pack_without_zotero_environment(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            evidence_path = _write_evidence_json(tempdir, source_path)
            env = os.environ.copy()
            for name in ("ZOTERO_LIBRARY_ID", "ZOTERO_READ_KEY", "ZOTERO_WRITE_KEY"):
                env.pop(name, None)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "millefeuille",
                    "source-pack",
                    "intake",
                    "--evidence",
                    str(evidence_path),
                    "--source-pack-root",
                    str(Path(tempdir) / "source-packs"),
                    "--json",
                ],
                cwd=REPO_ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("ZOTERO_READ_KEY", result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "created")
            self.assertEqual(payload["source_hash"], f"sha256:{FIXTURE_SHA256}")
            self.assertTrue(Path(payload["manifest_path"]).is_file())

    def test_direct_cli_helper_returns_two_for_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = _write_recovered_pdf(tempdir)
            evidence_path = Path(tempdir) / "bad-evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "item_key": "ITEM1",
                        "attachment_key": "ATT1",
                        "canonical_filename": "Example.pdf",
                        "recovered_pdf_path": source_path.name,
                        "expected_sha256": "0" * 64,
                    }
                ),
                encoding="utf-8",
            )
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_source_pack_cli(
                [
                    "source-pack",
                    "intake",
                    "--evidence",
                    str(evidence_path),
                    "--source-pack-root",
                    str(Path(tempdir) / "source-packs"),
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("hash mismatch", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
