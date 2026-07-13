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

from millefeuille.cli.source_pack import run_source_pack_cli
from millefeuille.domain.artifact_writer import write_dry_run_artifacts
from millefeuille.domain.artifacts import load_artifact_index
from millefeuille.domain.config import ArtifactExportConfig
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.models import (
    AttachmentInfo,
    DiscoveredItem,
    OpenKBHandoffRow,
    PaperMetadata,
)
from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    load_recovered_pdf_evidence,
    load_source_pack_manifest,
    write_source_pack_from_recovered_pdf,
    write_source_packs_from_handoff_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BYTES = b"fixture recovered paper bytes for source-pack intake\n"
FIXTURE_SHA256 = hashlib.sha256(FIXTURE_BYTES).hexdigest()


def _write_recovered_pdf(tempdir: str) -> Path:
    source_path = Path(tempdir) / "recovered.pdf"
    source_path.write_bytes(FIXTURE_BYTES)
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
            self.assertTrue(
                (result.source_pack_dir / "analyses/millefeuille").is_dir()
            )

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
            source_pack_dir = (
                Path(tempdir) / "source-packs" / "zotero" / "zotero-ITEM1"
            )
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
