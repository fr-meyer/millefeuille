"""Tests for deterministic provider-free structure preparation."""

from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.local_structure import (
    LOCAL_STRUCTURE_BACKEND,
    LOCAL_STRUCTURE_SCHEMA_VERSION,
    build_local_markdown_structure,
    prepare_local_structures_from_route_evidence,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from tests.platform_capabilities import (
    requires_secure_nofollow_writes,
    requires_unsupported_secure_nofollow_writes,
)


class TestLocalMarkdownStructure(unittest.TestCase):
    def test_builds_pages_sections_tables_figures_and_references(self):
        markdown = """# Page 1
# Introduction
Opening text.
| Metric | Value |
| --- | --- |
| score | 1 |
![Architecture](figure-1.png)
# Page 2
# Methods
## References
[1] Example reference.
"""

        structure, outline, counts, warnings = build_local_markdown_structure(
            markdown,
            expected_page_count=2,
        )

        self.assertEqual(structure["schema_version"], LOCAL_STRUCTURE_SCHEMA_VERSION)
        self.assertEqual(structure["backend"], LOCAL_STRUCTURE_BACKEND)
        self.assertEqual(counts["pages"], 2)
        self.assertEqual(counts["sections"], 3)
        self.assertEqual(counts["tables"], 1)
        self.assertEqual(counts["figures"], 1)
        self.assertEqual(counts["references"], 1)
        self.assertEqual(warnings, [])
        self.assertEqual(structure["sections"][2]["parent_id"], "s2")
        self.assertIn("Introduction (p.1#s1)", outline)

    def test_records_page_coverage_warning_without_fabricating_pages(self):
        structure, _outline, counts, warnings = build_local_markdown_structure(
            "# Page 1\nUnstructured text.\n",
            expected_page_count=3,
        )

        self.assertEqual(counts["pages"], 1)
        self.assertEqual([page["page"] for page in structure["pages"]], [1])
        self.assertIn(
            "page marker coverage mismatch: expected 3, detected 1",
            warnings,
        )
        self.assertIn("missing page markers: 2, 3", warnings)
        self.assertIn("no non-page Markdown headings detected", warnings)


@requires_secure_nofollow_writes
class TestLocalStructurePreparation(unittest.TestCase):
    def _write_route_evidence(self, root: Path) -> Path:
        markdown_path = root / "selected.md"
        markdown_path.write_text(
            "# Page 1\n# Introduction\nText.\n# Page 2\n# Results\nText.\n",
            encoding="utf-8",
        )
        evidence_path = root / "route.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": "millefeuille-route-selection-evidence/v0.1",
                    "source_type": "zotero",
                    "item_key": "ITEM1",
                    "attachment_key": "ATT1",
                    "canonical_filename": "Fixture.pdf",
                    "markdown_path": markdown_path.name,
                    "expected_sha256": "a" * 64,
                    "page_count": 2,
                    "selected_route": "native",
                    "paper_id": "zotero-ITEM1",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return evidence_path

    def test_prepares_idempotent_fixture_evidence_without_source_pack_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_evidence = self._write_route_evidence(root)
            output_dir = root / "prepared"

            first = prepare_local_structures_from_route_evidence(
                route_evidence_paths=[route_evidence],
                output_dir=output_dir,
            )
            second = prepare_local_structures_from_route_evidence(
                route_evidence_paths=[route_evidence],
                output_dir=output_dir,
            )

            self.assertEqual(first.status, "created")
            self.assertEqual(first.documents[0].status, "created")
            self.assertEqual(second.status, "existing")
            self.assertEqual(second.documents[0].status, "existing")
            summary = json.loads(first.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["provider_calls"], 0)
            self.assertEqual(summary["source_pack_writes"], 0)
            evidence = json.loads(
                first.documents[0].evidence_path.read_text(encoding="utf-8")
            )
            self.assertEqual(evidence["structure_backend"], LOCAL_STRUCTURE_BACKEND)
            self.assertEqual(evidence["coverage_source"], "selected-markdown")
            self.assertEqual(evidence["sections"], 2)

    def test_input_order_replays_deterministically(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            first_route = self._write_route_evidence(root)
            second_payload = json.loads(first_route.read_text(encoding="utf-8"))
            second_payload["item_key"] = "ITEM2"
            second_payload["attachment_key"] = "ATT2"
            second_payload["paper_id"] = "zotero-ITEM2"
            second_route = root / "route-second.json"
            second_route.write_text(
                json.dumps(second_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            output_dir = root / "prepared"

            first = prepare_local_structures_from_route_evidence(
                route_evidence_paths=[second_route, first_route],
                output_dir=output_dir,
            )
            replay = prepare_local_structures_from_route_evidence(
                route_evidence_paths=[first_route, second_route],
                output_dir=output_dir,
            )

            self.assertEqual(first.status, "created")
            self.assertEqual(replay.status, "existing")
            self.assertEqual(
                [document.paper_id for document in replay.documents],
                ["zotero-ITEM1", "zotero-ITEM2"],
            )

    def test_rejects_existing_output_drift(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_evidence = self._write_route_evidence(root)
            output_dir = root / "prepared"
            prepare_local_structures_from_route_evidence(
                route_evidence_paths=[route_evidence],
                output_dir=output_dir,
            )
            structure_path = output_dir / "zotero-ITEM1.structure.json"
            structure_path.write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "local structure output drift",
            ):
                prepare_local_structures_from_route_evidence(
                    route_evidence_paths=[route_evidence],
                    output_dir=output_dir,
                )

    def test_rejects_duplicate_paper_ids_before_writing_outputs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_evidence = self._write_route_evidence(root)
            duplicate_payload = json.loads(route_evidence.read_text(encoding="utf-8"))
            duplicate_payload["item_key"] = "ITEM2"
            duplicate_payload["attachment_key"] = "ATT2"
            duplicate_path = root / "route-duplicate.json"
            duplicate_path.write_text(
                json.dumps(duplicate_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            output_dir = root / "prepared"

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "duplicate paper_id for local structure preparation",
            ):
                prepare_local_structures_from_route_evidence(
                    route_evidence_paths=[route_evidence, duplicate_path],
                    output_dir=output_dir,
                )
            self.assertFalse(output_dir.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_rejects_symlinked_selected_markdown(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_evidence = self._write_route_evidence(root)
            markdown_path = root / "selected.md"
            real_markdown = root / "real-selected.md"
            markdown_path.replace(real_markdown)
            markdown_path.symlink_to(real_markdown.name)

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "symbolic link",
            ):
                prepare_local_structures_from_route_evidence(
                    route_evidence_paths=[route_evidence],
                    output_dir=root / "prepared",
                )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_rejects_symlinked_existing_output(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_evidence = self._write_route_evidence(root)
            output_dir = root / "prepared"
            output_dir.mkdir()
            real_output = root / "real-output.json"
            real_output.write_text("{}\n", encoding="utf-8")
            (output_dir / "zotero-ITEM1.structure.json").symlink_to(real_output)

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "local structure output is not a file",
            ):
                prepare_local_structures_from_route_evidence(
                    route_evidence_paths=[route_evidence],
                    output_dir=output_dir,
                )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_rejects_symlinked_output_parent_without_creating_beneath_it(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_evidence = self._write_route_evidence(root)
            real_parent = root / "real-parent"
            real_parent.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            output_dir = linked_parent / "prepared"

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "symbolic links or non-directories",
            ):
                prepare_local_structures_from_route_evidence(
                    route_evidence_paths=[route_evidence],
                    output_dir=output_dir,
                )

            self.assertFalse((real_parent / "prepared").exists())

    def test_cli_prepares_json_result(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route_evidence = self._write_route_evidence(root)
            output_dir = root / "prepared"
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_stage_cli(
                [
                    "structure-prepare",
                    "--route-evidence",
                    str(route_evidence),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "created")
            self.assertEqual(payload["documents"][0]["section_count"], 2)


class TestUnsupportedLocalStructurePreparation(unittest.TestCase):
    @requires_unsupported_secure_nofollow_writes
    def test_cli_fails_closed_without_creating_output_parent(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            markdown_path = root / "selected.md"
            markdown_path.write_text(
                "# Page 1\n# Introduction\nText.\n",
                encoding="utf-8",
            )
            route_evidence = root / "route.json"
            route_evidence.write_text(
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
            output_dir = root / "prepared"
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_stage_cli(
                [
                    "structure-prepare",
                    "--route-evidence",
                    str(route_evidence),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn(
                "requires no-follow filesystem writes on this platform",
                stderr.getvalue(),
            )
            self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
