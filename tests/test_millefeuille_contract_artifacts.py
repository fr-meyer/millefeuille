"""Parse and guard Millefeuille contract artifacts."""

import json
from pathlib import Path
import re
import unittest

SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"

REQUIRED_DOCS = [
    "README.md",
    "vision.md",
    "remaining-work.md",
    "cli-contract.md",
    "artifact-storage.md",
    "retrieval-index-contract.md",
    "classification-orchestration.md",
    "tag-state-machine.md",
    "ocr-backend-contract.md",
    "release-version-policy.md",
    "release-candidate-preflight.md",
    "live-run-plan.md",
]

REQUIRED_JSON_SCHEMAS = [
    "stage-manifest.schema.json",
    "artifact-index.schema.json",
    "source-pack-manifest.schema.json",
    "hierarchical-summary.schema.json",
    "paper-card.schema.json",
    "retrieval-index-status.schema.json",
    "retrieval-batch-manifest.schema.json",
    "retrieval-batch-result.schema.json",
    "acceptance-batch-manifest.schema.json",
    "acceptance-batch-summary.schema.json",
    "classification-batch-manifest.schema.json",
    "classification-batch-summary.schema.json",
    "classification-action-evidence.schema.json",
    "classification-action-record.schema.json",
]

REQUIRED_STAGES = {
    "discover",
    "handoff",
    "recover",
    "source-pack",
    "extract-native",
    "extract-ocr",
    "route",
    "structure",
    "summarize",
    "card",
    "openkb-add",
    "index",
    "acceptance",
    "classify",
    "writeback",
    "release",
}

MANUAL_GATE_TERMS = [
    "Zotero",
    "PDF",
    "OCR",
    "model",
    "OpenKB",
    "PageIndex",
    "index",
    "source-pack",
    "artifact-root",
    "paper card",
    "GitHub",
    "release",
    "approval",
]

FORBIDDEN_PAYLOAD_PATTERNS = [
    re.compile(r"Authorization:\s*(Bearer|Basic)", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}", re.IGNORECASE),
    re.compile(r"data:application/pdf", re.IGNORECASE),
    re.compile(r"%PDF-"),
]


class TestMillefeuilleContractArtifacts(unittest.TestCase):
    def test_required_json_schemas_are_parseable(self):
        for name in REQUIRED_JSON_SCHEMAS:
            with self.subTest(name=name):
                schema = json.loads((SPEC_DIR / name).read_text(encoding="utf-8"))
                self.assertIn("title", schema)
                self.assertIn("type", schema)

    def test_stage_manifest_schema_is_parseable_and_names_required_stages(self):
        schema = json.loads(
            (SPEC_DIR / "stage-manifest.schema.json").read_text(encoding="utf-8")
        )
        stage_names = set(
            schema["properties"]["stages"]["items"]["properties"]["name"]["enum"]
        )

        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            "millefeuille-stage-manifest/v0.1",
        )
        self.assertEqual(schema["properties"]["source_type"]["const"], "zotero")
        self.assertTrue(REQUIRED_STAGES.issubset(stage_names))

    def test_retrieval_batch_schemas_pin_v01_contracts(self):
        manifest = json.loads(
            (SPEC_DIR / "retrieval-batch-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        result = json.loads(
            (SPEC_DIR / "retrieval-batch-result.schema.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            manifest["properties"]["schema_version"]["const"],
            "millefeuille-retrieval-batch-manifest/v0.1",
        )
        self.assertEqual(
            result["properties"]["schema_version"]["const"],
            "millefeuille-retrieval-batch-result/v0.1",
        )
        self.assertFalse(manifest["additionalProperties"])
        self.assertFalse(result["additionalProperties"])
        self.assertEqual(
            result["$defs"]["runResult"]["properties"]["source_hash"]["pattern"],
            "^sha256(?:-aggregate)?:[0-9a-f]{64}$",
        )
        self.assertEqual(
            set(result["$defs"]["summaryEntry"]["properties"]),
            {"grain", "scope", "text_ref"},
        )
        self.assertEqual(
            set(result["$defs"]["indexLane"]["properties"]),
            {"lane", "status"},
        )

    def test_model_profile_schema_names_model_using_stages(self):
        text = (SPEC_DIR / "model-profile.schema.yaml").read_text(encoding="utf-8")
        for marker in [
            "millefeuille-model-profile/v0.1",
            "extract_ocr",
            "summarize_page",
            "summarize_section",
            "paper_card",
            "classify",
        ]:
            self.assertIn(marker, text)

    def test_contract_docs_keep_manual_gates_visible(self):
        combined = "\n".join(
            (SPEC_DIR / name).read_text(encoding="utf-8") for name in REQUIRED_DOCS
        )
        for term in MANUAL_GATE_TERMS:
            self.assertIn(term, combined, f"contract packet is missing {term!r}")

        for name in REQUIRED_DOCS:
            text = (SPEC_DIR / name).read_text(encoding="utf-8")
            self.assertRegex(
                text,
                r"\b(approval|approved|manual gate|Manual gate|Manual Gates)\b",
                f"{name} is missing an approval/manual-gate marker",
            )

    def test_contract_docs_do_not_contain_payload_or_secret_markers(self):
        for path in [*SPEC_DIR.glob("*.md"), SPEC_DIR / "stage-manifest.schema.json"]:
            text = path.read_text(encoding="utf-8")
            for pattern in FORBIDDEN_PAYLOAD_PATTERNS:
                self.assertIsNone(
                    pattern.search(text),
                    f"forbidden marker in {path.name}: {pattern.pattern}",
                )


if __name__ == "__main__":
    unittest.main()
