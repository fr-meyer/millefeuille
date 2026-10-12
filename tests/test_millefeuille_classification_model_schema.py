"""Synthetic source-pack/classification output identity compatibility checks."""

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError

from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    aggregate_source_hash,
    build_multi_source_pack_manifest,
    parse_source_pack_manifest,
    source_ref_for_attachment_key,
)

SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"


def _output(source_hash):
    return {
        "schema_version": "millefeuille-classification-model-output/v0.1",
        "paper_id": "zotero-SYNTHETIC",
        "run_id": "run-synthetic",
        "source_hash": source_hash,
        "taxonomy_version": "synthetic-v1",
        "taxonomy_lock_identity": "sha256:" + "c" * 64,
        "primary_entry_id": "synthetic-L2",
        "strongest_rejected_entry_id": None,
        "confidence": "high",
        "rationale": "Synthetic schema fixture only.",
        "evidence": [{"locator": "p.1", "claim": "Synthetic claim only."}],
        "taxonomy_gap": False,
        "review_reasons": [],
    }


class ClassificationModelSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads(
            (SPEC_DIR / "classification-model-output.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema)
        source_schema = json.loads(
            (SPEC_DIR / "source-pack-manifest.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(source_schema)
        cls.source_validator = Draft202012Validator(source_schema)

    def test_single_and_aggregate_source_identities_are_accepted(self):
        for source_hash in (
            "sha256:" + "a" * 64,
            "sha256-aggregate:" + "b" * 64,
        ):
            with self.subTest(source_hash=source_hash):
                self.validator.validate(_output(source_hash))

    def test_v02_two_pdf_manifest_identity_flows_into_classification_output(self):
        source_hashes = ("1" * 64, "2" * 64)
        verified = []
        for number, source_hash in enumerate(source_hashes, start=1):
            attachment_key = f"ATT{number}"
            evidence = RecoveredPdfEvidence(
                item_key="SYNTHETIC",
                attachment_key=attachment_key,
                canonical_filename=f"Synthetic-{number}.pdf",
                recovered_pdf_path=Path(f"unused-synthetic-{number}.pdf"),
                expected_sha256=source_hash,
                file_size_bytes=number,
            )
            verified.append(
                (
                    evidence,
                    "sha256:" + source_hash,
                    number,
                    source_ref_for_attachment_key(attachment_key),
                )
            )
        manifest = build_multi_source_pack_manifest(
            verified=verified,
            paper_id="zotero-SYNTHETIC",
            source_hash=aggregate_source_hash(source_hashes),
            created_at="2026-10-02T00:00:00+00:00",
        )
        self.source_validator.validate(manifest)
        parsed = parse_source_pack_manifest(manifest)
        output = _output(parsed["source_hash"])
        self.validator.validate(output)
        self.assertEqual(output["source_hash"], manifest["source_hash"])

    def test_malformed_source_identities_are_rejected(self):
        malformed = (
            "sha256:" + "a" * 63,
            "sha256:" + "a" * 65,
            "sha256-aggregate:" + "b" * 63,
            "sha256-aggregate:" + "b" * 65,
            "sha256:" + "A" * 64,
            "sha256-aggregate:" + "B" * 64,
            "sha512:" + "a" * 64,
            "sha256-other:" + "a" * 64,
            " sha256:" + "a" * 64,
            "sha256-aggregate:" + "b" * 64 + " ",
            "sha256:" + "a" * 64 + "\n",
            "sha256-aggregate:" + "b" * 64 + "\n",
            "sha256-aggregate:" + "b" * 64 + "\r",
            "sha256-aggregate:" + "b" * 64 + "\r\n",
        )
        for source_hash in malformed:
            with (
                self.subTest(source_hash=source_hash),
                self.assertRaises(ValidationError),
            ):
                self.validator.validate(_output(source_hash))

    def test_taxonomy_lock_identity_remains_an_ordinary_sha256(self):
        output = _output("sha256-aggregate:" + "b" * 64)
        output["taxonomy_lock_identity"] = "sha256-aggregate:" + "c" * 64
        errors = list(self.validator.iter_errors(output))
        self.assertTrue(
            any(list(error.path) == ["taxonomy_lock_identity"] for error in errors)
        )


if __name__ == "__main__":
    unittest.main()
