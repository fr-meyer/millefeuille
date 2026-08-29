"""Tests for exact Mistral OCR request/response model attribution."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from millefeuille.clients import MistralClient, MistralOCRError
from millefeuille.domain.config import MistralOCRConfig


class TestMistralOCRModelAttribution(unittest.TestCase):
    @staticmethod
    def _client(response: object) -> tuple[MistralClient, MagicMock]:
        sdk = MagicMock()
        sdk.ocr.process.return_value = response
        with patch(
            "millefeuille.clients.mistral_client.Mistral",
            return_value=sdk,
        ):
            client = MistralClient(
                MistralOCRConfig(
                    api_key="fixture",
                    model="mistral-ocr-4-1",
                    include_image_base64=False,
                )
            )
        client._uploaded_files["document-1"] = (
            "https://example.invalid/document.pdf"
        )
        return client, sdk

    def test_records_exact_requested_and_returned_model_ids(self):
        client, sdk = self._client(
            SimpleNamespace(model="mistral-ocr-4-1", pages=[])
        )

        execution = client.process_pdf_with_attribution("document-1")

        self.assertEqual(execution.requested_model, "mistral-ocr-4-1")
        self.assertEqual(execution.returned_model, "mistral-ocr-4-1")
        self.assertEqual(execution.pages, [])
        self.assertEqual(
            sdk.ocr.process.call_args.kwargs["model"],
            "mistral-ocr-4-1",
        )

    def test_attributed_boundary_rejects_missing_returned_model(self):
        client, _ = self._client(SimpleNamespace(pages=[]))

        with self.assertRaisesRegex(
            MistralOCRError,
            "model attribution is missing",
        ):
            client.process_pdf_with_attribution("document-1")

    def test_legacy_page_list_boundary_remains_compatible(self):
        client, _ = self._client(SimpleNamespace(pages=[]))

        self.assertEqual(client.process_pdf("document-1"), [])


if __name__ == "__main__":
    unittest.main()
