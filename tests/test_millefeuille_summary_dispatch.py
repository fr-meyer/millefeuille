"""Read-only, exact-input summary dispatch planning tests."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.local_structure import (
    prepare_local_structures_from_route_evidence,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_executor import verify_model_executor_input
from millefeuille.domain.summary_dispatch import plan_verified_summary_dispatch
from millefeuille.domain.summary_preparation import prepare_summary_execution_packages
from tests.platform_capabilities import requires_secure_nofollow_writes

CONTRACTS = {
    "summarize_page": ("summary-page", "v1"),
    "summarize_section": ("summary-section", "v1"),
    "summarize_full_paper": ("summary-full-paper", "v1"),
}


@requires_secure_nofollow_writes
class TestSummaryDispatch(unittest.TestCase):
    def _fixture(self, root: Path, *, page_count: int = 1) -> tuple[Path, Path, Path]:
        markdown = root / "selected.md"
        markdown.write_text(
            "# Page 1\n# Introduction\nPrivate text.\n", encoding="utf-8"
        )
        route = root / "route.json"
        route.write_text(
            json.dumps(
                {
                    "schema_version": "millefeuille-route-selection-evidence/v0.1",
                    "source_type": "zotero",
                    "item_key": "ITEM1",
                    "attachment_key": "ATT1",
                    "canonical_filename": "Fixture.pdf",
                    "markdown_path": markdown.name,
                    "expected_sha256": "a" * 64,
                    "page_count": page_count,
                    "selected_route": "native",
                    "paper_id": "zotero-ITEM1",
                }
            ),
            encoding="utf-8",
        )
        structures = prepare_local_structures_from_route_evidence(
            route_evidence_paths=[route], output_dir=root / "structures"
        )
        structure = structures.documents[0].evidence_path
        preparation = (
            prepare_summary_execution_packages(
                route_evidence_paths=[route],
                structure_evidence_paths=[structure],
                output_dir=root / "summary-preparation",
            )
            .documents[0]
            .package_path
        )
        return route, structure, preparation

    @staticmethod
    def _prompt(
        stage: str,
        unit_id: str,
        locators: tuple[str, ...],
        markdown: bytes,
        structure: bytes,
    ) -> bytes:
        assert markdown.startswith(b"# Page 1")
        assert b'"pages"' in structure
        return f"{stage}:{unit_id}:{','.join(locators)}\n".encode() + markdown

    def test_builds_complete_gpt_only_requests_without_writes_or_calls(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route, structure, preparation = self._fixture(root)
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            batch = plan_verified_summary_dispatch(
                route_evidence_path=route,
                structure_evidence_path=structure,
                preparation_path=preparation,
                prompt_builder=self._prompt,
                output_contracts=CONTRACTS,
            )
            after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(batch.paper_id, "zotero-ITEM1")
            self.assertEqual(len(batch.units), 3)
            self.assertNotIn("Private text", repr(batch))
            self.assertTrue(batch.preparation_sha256.startswith("sha256:"))
            self.assertEqual(
                [(unit.stage, unit.unit_id) for unit in batch.units],
                [
                    ("summarize_page", "page-1"),
                    ("summarize_section", "section-s1"),
                    ("summarize_full_paper", "full-paper"),
                ],
            )
            self.assertEqual(
                len({unit.request["idempotency_key"] for unit in batch.units}), 3
            )
            for unit in batch.units:
                self.assertEqual(unit.request["requested_model"], "openai/gpt-5.6-sol")
                self.assertEqual(unit.request["thinking"], "xhigh")
                self.assertEqual(
                    unit.request["fallback"], {"policy": "none", "models": []}
                )
                self.assertEqual(
                    unit.request["authentication"]["required_class"],
                    "subscription_oauth",
                )
                verify_model_executor_input(unit.request, unit.input_payload)

    def test_rejects_tampered_preparation_and_changed_source(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route, structure, preparation = self._fixture(root)
            package = json.loads(preparation.read_text(encoding="utf-8"))
            package["work_units"]["summarize_page"][0]["source_locators"] = ["p.99"]
            preparation.write_text(json.dumps(package), encoding="utf-8")
            with self.assertRaisesRegex(
                MillefeuilleContractError, "package or source drift"
            ):
                plan_verified_summary_dispatch(
                    route_evidence_path=route,
                    structure_evidence_path=structure,
                    preparation_path=preparation,
                    prompt_builder=self._prompt,
                    output_contracts=CONTRACTS,
                )

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route, structure, preparation = self._fixture(root)
            (root / "selected.md").write_text("Changed text.\n", encoding="utf-8")
            with self.assertRaises(MillefeuilleContractError):
                plan_verified_summary_dispatch(
                    route_evidence_path=route,
                    structure_evidence_path=structure,
                    preparation_path=preparation,
                    prompt_builder=self._prompt,
                    output_contracts=CONTRACTS,
                )

    def test_rejects_prompts_the_adapter_cannot_execute(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route, structure, preparation = self._fixture(root)
            for prompt, reason in ((b"\xff", "valid UTF-8"), (b"  \n", "contain text")):
                with (
                    self.subTest(reason=reason),
                    self.assertRaisesRegex(MillefeuilleContractError, reason),
                ):
                    plan_verified_summary_dispatch(
                        route_evidence_path=route,
                        structure_evidence_path=structure,
                        preparation_path=preparation,
                        prompt_builder=lambda *_, payload=prompt: payload,
                        output_contracts=CONTRACTS,
                    )

    def test_rechecks_source_after_prompt_builder_runs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route, structure, preparation = self._fixture(root)

            def changing_prompt(
                stage: str,
                unit_id: str,
                locators: tuple[str, ...],
                markdown: bytes,
                structure_bytes: bytes,
            ) -> bytes:
                (root / "selected.md").write_text(
                    "Changed during planning.\n", encoding="utf-8"
                )
                return self._prompt(stage, unit_id, locators, markdown, structure_bytes)

            with self.assertRaisesRegex(MillefeuilleContractError, "source drift"):
                plan_verified_summary_dispatch(
                    route_evidence_path=route,
                    structure_evidence_path=structure,
                    preparation_path=preparation,
                    prompt_builder=changing_prompt,
                    output_contracts=CONTRACTS,
                )

    def test_rejects_incomplete_coverage_and_missing_contract(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            route, structure, preparation = self._fixture(root, page_count=2)
            with self.assertRaisesRegex(
                MillefeuilleContractError, "complete structure page coverage"
            ):
                plan_verified_summary_dispatch(
                    route_evidence_path=route,
                    structure_evidence_path=structure,
                    preparation_path=preparation,
                    prompt_builder=self._prompt,
                    output_contracts=CONTRACTS,
                )
            with self.assertRaisesRegex(
                MillefeuilleContractError, "every summary stage"
            ):
                plan_verified_summary_dispatch(
                    route_evidence_path=route,
                    structure_evidence_path=structure,
                    preparation_path=preparation,
                    prompt_builder=self._prompt,
                    output_contracts={"summarize_page": ("summary-page", "v1")},
                )
