"""Parse and guard Millefeuille contract artifacts."""

import json
from pathlib import Path
import re
import unittest

SPEC_DIR = (
    Path(__file__).resolve().parents[1]
    / "specs"
    / "millefeuille-pipeline"
)

REQUIRED_DOCS = [
    "README.md",
    "remaining-work.md",
    "cli-contract.md",
    "tag-state-machine.md",
    "ocr-backend-contract.md",
    "release-version-policy.md",
    "live-run-plan.md",
]

MANUAL_GATE_TERMS = [
    "Zotero",
    "PDF",
    "OCR",
    "OpenKB",
    "source-pack",
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
        self.assertTrue({
            "discover",
            "handoff",
            "recover",
            "source-pack",
            "extract-native",
            "extract-ocr",
            "route",
            "openkb-add",
            "acceptance",
            "classify",
            "release",
        }.issubset(stage_names))

    def test_contract_docs_keep_manual_gates_visible(self):
        combined = "\n".join(
            (SPEC_DIR / name).read_text(encoding="utf-8")
            for name in REQUIRED_DOCS
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
