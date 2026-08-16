"""Parse and guard Millefeuille contract artifacts."""

import json
from pathlib import Path
import re
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"

REQUIRED_DOCS = [
    "README.md",
    "vision.md",
    "remaining-work.md",
    "cli-contract.md",
    "legacy-migration.md",
    "artifact-storage.md",
    "retrieval-index-contract.md",
    "classification-orchestration.md",
    "taxonomy-registry.md",
    "tag-state-machine.md",
    "lifecycle-tag-migration.md",
    "ocr-backend-contract.md",
    "release-version-policy.md",
    "release-candidate-preflight.md",
    "live-run-plan.md",
    "approved-live-receipts.md",
    "operator-preflight.md",
    "status-observability.md",
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
    "taxonomy-registry.schema.json",
    "taxonomy-lock.schema.json",
    "taxonomy-change-proposal.schema.json",
    "taxonomy-change-review.schema.json",
    "taxonomy-application.schema.json",
    "lifecycle-tag-registry.schema.json",
    "lifecycle-tag-migration-plan.schema.json",
    "model-execution-evidence.schema.json",
    "model-execution-plan.schema.json",
    "model-provenance-record.schema.json",
    "approved-live-receipt.schema.json",
    "approved-live-audit.schema.json",
    "operator-preflight-packet.schema.json",
    "operator-preflight-result.schema.json",
    "status.schema.json",
    "status-observation.schema.json",
    "completion-gate-result.schema.json",
    "zotero-writeback-result.schema.json",
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

        registry = json.loads(
            (SPEC_DIR / "lifecycle-tag-registry.v0.1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            registry["schema_version"],
            "millefeuille-lifecycle-tag-registry/v0.1",
        )

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

    def test_legacy_migration_contract_pins_compatibility_boundaries(self):
        migration = (SPEC_DIR / "legacy-migration.md").read_text(encoding="utf-8")
        required_markers = [
            "Legacy Hydra surface",
            "Lifecycle stage surface",
            "millefeuille-source-pack-manifest/v0.1",
            "millefeuille-source-pack-manifest/v0.2",
            "millefeuille-processed",
            "millefeuille-acceptance-passed",
            "No earlier than `1.0.0`",
            "Safe Stop And Rollback",
        ]
        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, migration)

        pageindex = migration.split("## PageIndex Boundary", 1)[1].split(
            "## Output And Artifact Root Mapping",
            1,
        )[0]
        normalized_pageindex = " ".join(pageindex.split())
        pageindex_guarantees = [
            "direct PageIndex HTTP API mode and a PageIndex SDK mode",
            "All newly implemented lifecycle PageIndex ingestion and indexing "
            "must follow the later **PageIndex MCP-only** policy.",
            "Lifecycle code must not reuse the legacy direct HTTP client, SDK "
            "client, or an ad-hoc multipart upload as its production connector.",
            "fail-closed access control",
            "an authenticated bridge request or a narrowly scoped, unguessable, "
            "short-lived, single-use capability URL",
            "reject expired, replayed, or mismatched access attempts",
            "revoke the serving capability",
            "must not enter handoff rows, source-pack manifests, committed "
            "evidence, logs, or the bridge ledger",
        ]
        for guarantee in pageindex_guarantees:
            with self.subTest(pageindex_guarantee=guarantee):
                self.assertIn(guarantee, normalized_pageindex)

        removal = migration.split(
            "Legacy removal is not automatic at `1.0.0`. "
            "It requires all of the following:",
            1,
        )[1].split("## Safe Stop And Rollback", 1)[0]
        actual_exit_criteria: list[str] = []
        current = ""
        for raw_line in removal.splitlines():
            if raw_line.startswith("- "):
                if current:
                    actual_exit_criteria.append(
                        current.removesuffix("; and").rstrip(";.")
                    )
                current = raw_line[2:].strip()
            elif current and raw_line.startswith("  "):
                current += " " + raw_line.strip()
        if current:
            actual_exit_criteria.append(current.removesuffix("; and").rstrip(";."))

        expected_exit_criteria = [
            "staged discovery through writeback has bounded live evidence",
            "v0.1 and v0.2 source packs remain readable or have a tested migration",
            "PageIndex MCP bridge and ledger reconciliation are operational",
            "legacy tag and output migrations have audit reports",
            "a clean-install and upgrade test passes on supported platforms",
            "release notes provide a rollback path",
            "the incompatible CLI change receives explicit release approval",
        ]
        self.assertEqual(expected_exit_criteria, actual_exit_criteria)
        self.assertLess(
            migration.index("`0.4.x` (current)"),
            migration.index("No earlier than `1.0.0`"),
        )

    def test_migration_contract_is_linked_from_operator_navigation(self):
        packet_readme = (SPEC_DIR / "README.md").read_text(encoding="utf-8")
        root_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("`legacy-migration.md`", packet_readme)
        self.assertIn(
            "(specs/millefeuille-pipeline/legacy-migration.md)",
            root_readme,
        )
        self.assertTrue((SPEC_DIR / "legacy-migration.md").is_file())


if __name__ == "__main__":
    unittest.main()
