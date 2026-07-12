"""Guard the public-safe aggregate evidence from the first live run."""

import base64
import binascii
import json
from pathlib import Path
import re
import unittest

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "millefeuille_live_golden_run"
    / "golden-run.json"
)

SENSITIVE_FIELD_NAMES = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "key",
    "library_id",
    "password",
    "secret",
    "token",
}

SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"authorization:\s*(bearer|basic)\s+", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~-]+", re.IGNORECASE),
    re.compile(r"\bbasic\s+[A-Za-z0-9._~-]+", re.IGNORECASE),
    re.compile(r"cookie:\s*", re.IGNORECASE),
    re.compile("data:application/" + "pdf;base64,", re.IGNORECASE),
    re.compile("%" + "PDF-"),
    re.compile("/" + "home/"),
    re.compile("/" + "srv/openkb/"),
    re.compile(r"https://api\." + r"zotero\.org/"),
)


def _walk_json(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key, nested
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _looks_like_large_base64_blob(value: str) -> bool:
    compact = value.strip()
    if len(compact) < 200 or not re.fullmatch(r"[A-Za-z0-9+/=\s]+", compact):
        return False
    try:
        base64.b64decode(compact, validate=True)
    except binascii.Error:
        return False
    return True


class TestMillefeuilleLiveGoldenRunFixture(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_records_the_first_successful_live_lane(self):
        data = self.fixture

        self.assertEqual(
            data["schema_version"], "millefeuille-live-golden-run/v0.1"
        )
        self.assertEqual(
            data["run_id"], "zotero-docai-pipeline-live-20260711-2345-kst"
        )
        self.assertEqual(data["captured_from"], "approved-live-run-aggregate")
        self.assertEqual(data["discovery"]["matched_items"], 1)
        self.assertEqual(data["discovery"]["attachments_total"], 2)
        self.assertEqual(data["discovery"]["pdf_attachments"], 1)
        self.assertEqual(data["discovery"]["non_pdf_attachments"], 1)
        self.assertEqual(data["recovery"]["verification_strength"], "full")
        self.assertTrue(data["recovery"]["sha256_verified"])
        self.assertEqual(data["native_extraction"]["pages"], 9)
        self.assertEqual(data["ocr_extraction"]["pages"], 9)
        self.assertEqual(data["route"]["selected_route"], "merged-dual")
        self.assertTrue(data["route"]["dual_extraction_complete"])
        self.assertEqual(data["acceptance"]["status"], "pass")
        self.assertEqual(data["acceptance"]["duplicate_candidates"], 0)
        self.assertEqual(data["classification"]["confidence"], "high")

    def test_fixture_preserves_model_route_history(self):
        openkb = self.fixture["openkb"]

        self.assertEqual(openkb["model_at_live_run"], "dashscope/qwen3.7-max")
        self.assertEqual(
            openkb["model_route_after_followup_smoke"],
            "openai/openkb-qwen",
        )
        self.assertEqual(openkb["local_proxy_smoke_status"], "passed")
        self.assertIn(
            "reproducibility-in-deep-learning", openkb["concepts_created"]
        )
        self.assertIn("random-seed-variance", openkb["concepts_created"])

    def test_writeback_plan_stays_explicit_and_gated(self):
        plan = self.fixture["zotero_writeback_plan"]

        self.assertEqual(plan["approval_status"], "approved-by-operator")
        self.assertEqual(
            plan["execution_status"], "blocked-missing-zotero-write-key"
        )
        self.assertEqual(plan["required_credential"], "ZOTERO_WRITE_KEY")
        self.assertEqual(plan["remove_tags"], ["docai"])
        self.assertIn("docai-classified", plan["add_tags"])
        self.assertIn("docai-processed", plan["add_tags"])
        self.assertEqual(
            plan["classification_destination"],
            "Methods · Normalization & training dynamics",
        )

    def test_fixture_contains_no_private_payloads_or_credentials(self):
        for key, value in _walk_json(self.fixture):
            lower_key = key.lower()
            self.assertNotIn(
                lower_key,
                SENSITIVE_FIELD_NAMES,
                f"unexpected sensitive field in fixture: {key}",
            )
            if isinstance(value, str):
                self.assertNotIn("?", value, f"query string in {key}: {value}")
                for pattern in SENSITIVE_TEXT_PATTERNS:
                    self.assertIsNone(
                        pattern.search(value),
                        f"sensitive value in {key}: {value}",
                    )
                self.assertFalse(
                    _looks_like_large_base64_blob(value),
                    f"large base64-looking blob in {key}",
                )


if __name__ == "__main__":
    unittest.main()
