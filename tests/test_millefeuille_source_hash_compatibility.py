"""Regression coverage for source-pack and retrieval-index hash parity."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest
from unittest import mock

from millefeuille.domain.index_fixtures import load_retrieval_index_status
from millefeuille.domain.millefeuille import (
    IndexLaneRecord,
    MillefeuilleContractError,
    RetrievalIndexRecord,
)
from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    aggregate_source_hash,
    build_multi_source_pack_manifest,
    parse_source_pack_manifest,
    source_ref_for_attachment_key,
)

try:  # optional dependency in local dev/CI only
    import jsonschema
except ImportError:  # pragma: no cover - fallback still checks the field pattern
    jsonschema = None


SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"
SINGLE_SOURCE_HASH = "sha256:" + ("a" * 64)
AGGREGATE_SOURCE_HASH = "sha256-aggregate:" + ("b" * 64)


def _index_status_payload(source_hash: str) -> dict[str, object]:
    return RetrievalIndexRecord(
        paper_id="zotero-ITEM1",
        run_id="run-fixture",
        source_hash=source_hash,
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
            ),
        ],
    ).to_dict()


def _validate_schema(
    testcase: unittest.TestCase,
    schema: dict[str, object],
    payload: dict[str, object],
) -> None:
    if jsonschema is not None:
        jsonschema.Draft202012Validator(schema).validate(payload)
        return

    if "properties" in schema:
        source_hash_schema = schema["properties"]["source_hash"]
    else:
        source_hash_schema = schema["$defs"]["multi_source_manifest"][
            "properties"
        ]["source_hash"]
    pattern = source_hash_schema["pattern"]
    testcase.assertIsNotNone(re.fullmatch(pattern, payload["source_hash"]))
    if "not" in source_hash_schema:
        testcase.assertIsNone(
            re.search(source_hash_schema["not"]["pattern"], payload["source_hash"])
        )


class TestSourceHashCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index_schema = json.loads(
            (SPEC_DIR / "retrieval-index-status.schema.json").read_text(
                encoding="utf-8"
            )
        )
        cls.source_pack_schema = json.loads(
            (SPEC_DIR / "source-pack-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )

    def test_index_schema_runtime_and_fixture_loader_accept_both_hash_forms(self):
        for source_hash in (SINGLE_SOURCE_HASH, AGGREGATE_SOURCE_HASH):
            with self.subTest(source_hash=source_hash):
                payload = _index_status_payload(source_hash)

                _validate_schema(self, self.index_schema, payload)
                self.assertEqual(
                    RetrievalIndexRecord.from_dict(payload).source_hash,
                    source_hash,
                )
                with mock.patch(
                    "millefeuille.domain.index_fixtures._load_json_object",
                    return_value=payload,
                ):
                    loaded = load_retrieval_index_status("fixture-index.json")
                self.assertEqual(loaded["source_hash"], source_hash)

    def test_index_schema_runtime_and_fixture_loader_reject_malformed_hashes(self):
        malformed_hashes = (
            "sha256:" + ("a" * 63),
            "sha256-aggregate:" + ("b" * 65),
            "sha256-aggregate:" + ("B" * 64),
            "sha512:" + ("a" * 64),
            " sha256:" + ("a" * 64),
            "sha256:" + ("a" * 64) + "\n",
        )
        validator = (
            jsonschema.Draft202012Validator(self.index_schema)
            if jsonschema is not None
            else None
        )
        pattern = self.index_schema["properties"]["source_hash"]["pattern"]

        for source_hash in malformed_hashes:
            with self.subTest(source_hash=source_hash):
                payload = _index_status_payload(SINGLE_SOURCE_HASH)
                payload["source_hash"] = source_hash

                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "source_hash must use",
                ):
                    RetrievalIndexRecord.from_dict(payload)
                with mock.patch(
                    "millefeuille.domain.index_fixtures._load_json_object",
                    return_value=payload,
                ), self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "source_hash must use",
                ):
                    load_retrieval_index_status("fixture-index.json")
                if validator is not None:
                    self.assertFalse(validator.is_valid(payload))
                else:
                    self.assertIsNone(re.fullmatch(pattern, source_hash))

    def test_v02_multi_pdf_package_hash_flows_into_index_status(self):
        source_hashes = ("1" * 64, "2" * 64)
        expected_source_hash = aggregate_source_hash(source_hashes)
        verified = []
        for number, source_hash in enumerate(source_hashes, start=1):
            attachment_key = f"ATT{number}"
            evidence = RecoveredPdfEvidence(
                item_key="MULTIITEM1",
                attachment_key=attachment_key,
                canonical_filename=f"Paper-{number}.pdf",
                recovered_pdf_path=Path(f"unused-{number}.pdf"),
                expected_sha256=source_hash,
                file_size_bytes=number,
            )
            verified.append(
                (
                    evidence,
                    f"sha256:{source_hash}",
                    number,
                    source_ref_for_attachment_key(attachment_key),
                )
            )
        manifest = build_multi_source_pack_manifest(
            verified=verified,
            paper_id="zotero-MULTIITEM1",
            source_hash=expected_source_hash,
            created_at="2026-08-11T00:00:00+00:00",
        )

        _validate_schema(self, self.source_pack_schema, manifest)
        parsed_manifest = parse_source_pack_manifest(manifest)
        index_payload = _index_status_payload(parsed_manifest["source_hash"])
        _validate_schema(self, self.index_schema, index_payload)
        self.assertEqual(
            RetrievalIndexRecord.from_dict(index_payload).source_hash,
            expected_source_hash,
        )


if __name__ == "__main__":
    unittest.main()
