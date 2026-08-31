"""Tests for deterministic no-call summary execution preparation."""

from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.local_structure import (
    prepare_local_structures_from_route_evidence,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_preparation import (
    SUMMARY_PREPARATION_SCHEMA_VERSION,
    prepare_summary_execution_packages,
)
from tests.platform_capabilities import requires_secure_nofollow_writes

SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"
SUMMARY_SCHEMA_NAMES = (
    "model-execution-plan.schema.json",
    "summary-preparation.schema.json",
    "summary-preparation-batch.schema.json",
)


@requires_secure_nofollow_writes
class TestSummaryPreparation(unittest.TestCase):
    def _prepare_inputs(self, root: Path) -> tuple[Path, Path]:
        markdown_path = root / "selected.md"
        markdown_path.write_text(
            "# Page 1\n# Introduction\nPrivate paper text.\n",
            encoding="utf-8",
        )
        route_path = root / "route.json"
        route_path.write_text(
            json.dumps(
                {
                    "schema_version": "millefeuille-route-selection-evidence/v0.1",
                    "source_type": "zotero",
                    "item_key": "ITEM1",
                    "attachment_key": "ATT1",
                    "canonical_filename": "Fixture.pdf",
                    "markdown_path": markdown_path.name,
                    "expected_sha256": "a" * 64,
                    "page_count": 1,
                    "selected_route": "native",
                    "paper_id": "zotero-ITEM1",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        structures = prepare_local_structures_from_route_evidence(
            route_evidence_paths=[route_path],
            output_dir=root / "structures",
        )
        return route_path, structures.documents[0].evidence_path

    def test_prepares_bound_no_call_plans_without_summary_outputs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_path, structure_path = self._prepare_inputs(root)
            output_dir = root / "summary-preparation"

            first = prepare_summary_execution_packages(
                route_evidence_paths=[route_path],
                structure_evidence_paths=[structure_path],
                output_dir=output_dir,
            )
            second = prepare_summary_execution_packages(
                route_evidence_paths=[route_path],
                structure_evidence_paths=[structure_path],
                output_dir=output_dir,
            )

            self.assertEqual(first.status, "created")
            self.assertEqual(second.status, "existing")
            self.assertEqual(
                first.documents[0].work_unit_counts,
                {
                    "summarize_page": 1,
                    "summarize_section": 1,
                    "summarize_full_paper": 1,
                },
            )
            package_text = first.documents[0].package_path.read_text(encoding="utf-8")
            package = json.loads(package_text)
            self.assertEqual(
                package["schema_version"], SUMMARY_PREPARATION_SCHEMA_VERSION
            )
            self.assertEqual(package["execution"]["provider_call_performed"], False)
            self.assertEqual(package["execution"]["summary_outputs_generated"], 0)
            self.assertEqual(set(package["execution_plans"]), {
                "summarize_page",
                "summarize_section",
                "summarize_full_paper",
            })
            self.assertEqual(
                package["work_units"],
                {
                    "summarize_page": [
                        {"unit_id": "page-1", "source_locators": ["p.1"]}
                    ],
                    "summarize_section": [
                        {
                            "unit_id": "section-s1",
                            "source_locators": ["p.1#s1"],
                        }
                    ],
                    "summarize_full_paper": [
                        {"unit_id": "full-paper", "source_locators": ["p.1"]}
                    ],
                },
            )
            self.assertNotIn("Private paper text", package_text)
            self.assertNotIn("Introduction", package_text)
            summary = json.loads(first.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["provider_calls"], 0)
            self.assertEqual(summary["source_pack_writes"], 0)
            self.assertEqual(summary["zotero_writes"], 0)
            self.assertEqual(summary["totals"]["work_units"], 3)
            self.assertEqual(
                summary["documents"][0]["work_unit_counts"],
                {
                    "summarize_page": 1,
                    "summarize_section": 1,
                    "summarize_full_paper": 1,
                },
            )

            schemas = [
                json.loads((SPEC_DIR / name).read_text(encoding="utf-8"))
                for name in SUMMARY_SCHEMA_NAMES
            ]
            registry = Registry().with_resources(
                (schema["$id"], Resource.from_contents(schema))
                for schema in schemas
            )
            by_name = {
                schema["$id"].rsplit("/", 1)[-1]: schema for schema in schemas
            }
            for schema in schemas:
                Draft202012Validator.check_schema(schema)
            Draft202012Validator(
                by_name["summary-preparation.schema.json"],
                registry=registry,
            ).validate(package)
            Draft202012Validator(
                by_name["summary-preparation-batch.schema.json"],
                registry=registry,
            ).validate(summary)

    def test_rejects_structure_locator_drift_before_writing_outputs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_path, structure_evidence_path = self._prepare_inputs(root)
            evidence = json.loads(
                structure_evidence_path.read_text(encoding="utf-8")
            )
            structure_path = structure_evidence_path.parent / evidence["structure_path"]
            structure = json.loads(structure_path.read_text(encoding="utf-8"))
            structure["pages"][0]["locator"] = ""
            structure_path.write_text(
                json.dumps(structure, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            output_dir = root / "summary-preparation"
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "page locator must be a non-empty string",
            ):
                prepare_summary_execution_packages(
                    route_evidence_paths=[route_path],
                    structure_evidence_paths=[structure_evidence_path],
                    output_dir=output_dir,
                )
            self.assertFalse(output_dir.exists())

    def test_rejects_route_and_structure_identity_drift(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_path, structure_path = self._prepare_inputs(root)
            structure = json.loads(structure_path.read_text(encoding="utf-8"))
            structure["canonical_filename"] = "Other.pdf"
            structure_path.write_text(
                json.dumps(structure, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "canonical_filename drift",
            ):
                prepare_summary_execution_packages(
                    route_evidence_paths=[route_path],
                    structure_evidence_paths=[structure_path],
                    output_dir=root / "summary-preparation",
                )

    def test_rejects_duplicate_paper_ids_before_writing_outputs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_path, structure_path = self._prepare_inputs(root)
            route_payload = json.loads(route_path.read_text(encoding="utf-8"))
            route_payload["item_key"] = "ITEM2"
            route_payload["attachment_key"] = "ATT2"
            duplicate_route = root / "route-duplicate.json"
            duplicate_route.write_text(
                json.dumps(route_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            structure_payload = json.loads(
                structure_path.read_text(encoding="utf-8")
            )
            structure_payload["item_key"] = "ITEM2"
            structure_payload["attachment_key"] = "ATT2"
            duplicate_structure = structure_path.parent / "duplicate.evidence.json"
            duplicate_structure.write_text(
                json.dumps(structure_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            output_dir = root / "summary-preparation"

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "duplicate paper_id for summary preparation",
            ):
                prepare_summary_execution_packages(
                    route_evidence_paths=[route_path, duplicate_route],
                    structure_evidence_paths=[structure_path, duplicate_structure],
                    output_dir=output_dir,
                )
            self.assertFalse(output_dir.exists())

    def test_rejects_duplicate_page_unit_ids_before_writing_outputs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_path, structure_evidence_path = self._prepare_inputs(root)
            route_payload = json.loads(route_path.read_text(encoding="utf-8"))
            route_payload["page_count"] = 2
            route_path.write_text(
                json.dumps(route_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            evidence = json.loads(
                structure_evidence_path.read_text(encoding="utf-8")
            )
            evidence["page_count"] = 2
            structure_evidence_path.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            structure_path = structure_evidence_path.parent / evidence["structure_path"]
            structure = json.loads(structure_path.read_text(encoding="utf-8"))
            structure["pages"].append(dict(structure["pages"][0]))
            structure_path.write_text(
                json.dumps(structure, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            output_dir = root / "summary-preparation"

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "page unit ids must be unique",
            ):
                prepare_summary_execution_packages(
                    route_evidence_paths=[route_path],
                    structure_evidence_paths=[structure_evidence_path],
                    output_dir=output_dir,
                )
            self.assertFalse(output_dir.exists())

    def test_cli_prepares_json_result(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_path, structure_path = self._prepare_inputs(root)
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_stage_cli(
                [
                    "summarize-prepare",
                    "--route-evidence",
                    str(route_path),
                    "--structure-evidence",
                    str(structure_path),
                    "--output-dir",
                    str(root / "summary-preparation"),
                    "--profile",
                    "research-default",
                    "--json",
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "created")
            self.assertEqual(payload["profile"], "research-default")
            self.assertEqual(payload["documents"][0]["paper_id"], "zotero-ITEM1")
            self.assertEqual(
                payload["documents"][0]["work_unit_counts"]["summarize_page"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
