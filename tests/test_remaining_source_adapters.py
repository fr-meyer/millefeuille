"""Closed OCR parsing regressions without transport, account, or private data."""

import hashlib
import json
from types import SimpleNamespace
import unittest

from millefeuille.clients.mistral_client import BoundedMistralAdapter
from millefeuille.domain.ocr_request_budget import OCRBudgetError


class RemainingSchemaTests(unittest.TestCase):
    payload = b'{"model":"mistral-ocr-latest","document":{"type":"document_url","document_url":"https://example.invalid/fixture"}}'

    def adapter(self, raw):
        adapter = BoundedMistralAdapter.__new__(BoundedMistralAdapter)
        adapter.graph = SimpleNamespace(
            operations=[SimpleNamespace(id="ocr", kind="ocr")]
        )
        adapter.response_receipts = {}

        def wire(*args, **kwargs):
            adapter.response_receipts["ocr"] = {
                "wire_observed": True,
                "semantic_status": "not-parsed",
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "raw_bytes": len(raw),
            }
            return raw

        adapter.execute = wire
        return adapter

    def test_request_nonobjects_closed_keys_duplicates_encoding(self):
        for payload in (
            b"[]",
            b"null",
            b'{"model":"mistral-ocr-latest"}',
            b'{"model":NaN}',
            b'{"model":0,"model":1}',
            bytes([34, 255, 34]),
        ):
            adapter = self.adapter(b"{}")
            with self.subTest(payload=payload), self.assertRaises(OCRBudgetError):
                adapter.process_pdf_with_attribution(
                    "ocr", payload, authorization="synthetic"
                )
            self.assertEqual(adapter.response_receipts, {})

    def test_provider_index_coverage_not_renumbered(self):
        for indices in ((1,), (0, 0), (1, 0), (-1,), (True,), (0, 2)):
            raw = json.dumps(
                {
                    "model": "mistral-ocr-2505",
                    "pages": [
                        {"index": index, "markdown": "Value 17.25"} for index in indices
                    ],
                }
            ).encode()
            adapter = self.adapter(raw)
            with self.subTest(indices=indices), self.assertRaises(OCRBudgetError):
                adapter.process_pdf_with_attribution(
                    "ocr", self.payload, authorization="synthetic"
                )
            receipt = adapter.response_receipts["ocr"]
            self.assertTrue(receipt["wire_observed"])
            self.assertEqual(receipt["semantic_status"], "parse-failed")
            self.assertEqual(receipt["raw_sha256"], hashlib.sha256(raw).hexdigest())

    def test_cumulative_markdown_cap(self):
        raw = json.dumps(
            {
                "model": "mistral-ocr-2505",
                "pages": [
                    {"index": index, "markdown": "x" * 65536} for index in range(5)
                ],
            }
        ).encode()
        adapter = self.adapter(raw)
        with self.assertRaises(OCRBudgetError):
            adapter.process_pdf_with_attribution(
                "ocr", self.payload, authorization="synthetic"
            )
        self.assertEqual(
            adapter.response_receipts["ocr"]["semantic_status"], "parse-failed"
        )

    def test_valid_indices_preserve_page_identity_and_model(self):
        raw = (
            b'{"model":"mistral-ocr-2505","pages":['
            b'{"index":0,"markdown":"17.25"},{"index":1,"markdown":"99"}]}'
        )
        adapter = self.adapter(raw)
        result = adapter.process_pdf_with_attribution(
            "ocr", self.payload, authorization="synthetic"
        )
        self.assertEqual([p.page_number for p in result.pages], [1, 2])
        self.assertEqual(result.requested_model, "mistral-ocr-latest")
        self.assertEqual(
            adapter.response_receipts["ocr"]["semantic_status"], "validated"
        )
