"""Canonical card plans preserve summary publication and declare five exact files."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from markdown_it import MarkdownIt

from millefeuille.domain import paper_card_live_execution as live
from millefeuille.domain.millefeuille import MillefeuilleContractError, PaperCardRecord
from millefeuille.domain.paper_card_output_plan import (
    _markdown,
    plan_gpt_paper_card_outputs,
)
from tests import test_millefeuille_paper_card_live_execution as live_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestGptCardOutputPlan(unittest.TestCase):
    def setUp(self):
        fixture = live_tests.TestTrustedGptCardExecution(
            "test_one_call_follows_reservation_and_holds_validated_output_without_writes"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        client = live_tests._FakeClient(fixture.fixture, [])
        with patch.object(
            live, "request_gpt_card_reservation", return_value=fixture.preview
        ):
            self.outcome = fixture._run(client)
        self.values = {
            key: value
            for key, value in fixture.values.items()
            if key not in {"artifact_root", "run_id", "packet", "receipt"}
        }

    def _plan(self, outcome=None):
        return plan_gpt_paper_card_outputs(
            **self.values, outcome=outcome or self.outcome
        )

    def test_plans_canonical_files_without_writes_or_false_downstream_status(self):
        root = self.values["source_pack_root"]
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        plan = self._plan()
        self.assertEqual(plan, self._plan())
        after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(len(plan.files), 5)
        self.assertEqual(plan.total_bytes, sum(len(item.data) for item in plan.files))
        self.assertEqual((plan.provider_calls_performed, plan.writes_performed), (0, 0))
        self.assertNotIn("Private", repr(plan))
        data = {Path(item.ref).name: item.data for item in plan.files}
        card = json.loads(data["paper-card.json"])
        schema = json.loads(
            (
                Path(__file__).parents[1]
                / "specs/millefeuille-pipeline/paper-card.schema.json"
            ).read_text()
        )
        Draft202012Validator(schema).validate(card)
        self.assertEqual(card, PaperCardRecord.from_dict(card).to_dict())
        self.assertEqual(card["identity"]["source_hash"], plan.source_hash)
        self.assertEqual(card["index_state"]["phase"], "planned")
        self.assertNotIn("strongest_rejected_classification_path", card)
        self.assertNotIn("zotero_lifecycle_tag_state", card)
        self.assertNotIn("acceptance", card)
        self.assertNotIn("doi", card["identity"])
        self.assertNotIn("authors", card["identity"])
        self.assertIn(
            "metadata reconciliation is pending", card["quality_warnings"][-1]
        )
        manifest = json.loads(data["write-manifest.json"])
        self.assertEqual(manifest["summary_run_id"], self.values["publication"].run_id)
        self.assertFalse(manifest["write_authorized"])
        self.assertFalse(manifest["source_run_reconciliation_performed"])
        self.assertFalse(manifest["paper_acceptance_performed"])
        self.assertNotIn(b"Private", data["write-manifest.json"])
        for item in plan.files:
            self.assertTrue(
                item.ref.startswith(f"analyses/millefeuille/{plan.run_id}/cards/")
            )
            self.assertEqual(
                item.sha256, "sha256:" + hashlib.sha256(item.data).hexdigest()
            )

    def test_rejects_output_tampering_and_unbound_execution_approval(self):
        altered = replace(
            self.outcome.validated, provenance_sha256="sha256:" + "0" * 64
        )
        with self.assertRaisesRegex(
            MillefeuilleContractError, "validated result drift"
        ):
            self._plan(replace(self.outcome, validated=altered))
        with self.assertRaisesRegex(MillefeuilleContractError, "execution scope drift"):
            self._plan(
                replace(
                    self.outcome,
                    approval=replace(self.outcome.approval, request_count=2),
                )
            )

    def test_existing_destination_and_changed_source_are_rejected_without_effects(self):
        root = self.values["source_pack_root"]
        destination = root / f"analyses/millefeuille/{self.outcome.plan.run_id}"
        destination.mkdir()
        with self.assertRaisesRegex(MillefeuilleContractError, "already exists"):
            self._plan()
        destination.rmdir()
        path = root / f"zotero/{self.outcome.plan.paper_id}/manifest.json"
        source = json.loads(path.read_bytes())
        source["identity"]["canonical_filename"] = "other.pdf"
        path.write_text(json.dumps(source))
        with self.assertRaises(MillefeuilleContractError):
            self._plan()


class TestGptCardMarkdownText(unittest.TestCase):
    def test_titles_scalars_and_lists_cannot_inject_active_markdown(self):
        hostile = (
            "![track](https://attacker.example/track) [link](https://example.com)"
            "\n# forged heading\n- forged list\n1. ordered list\n```html\n"
            '<img src="https://attacker.example/html">\n```\n'
            "**bold** _emphasis_ `code` \\ [ref]: https://example.com"
        )
        card = {
            "identity": {"title": hostile},
            **dict.fromkeys(
                (
                    "one_line_thesis",
                    "primary_contribution",
                    "problem_addressed",
                    "method_or_approach",
                    "data_modality_domain",
                    "main_results",
                    "limitations",
                ),
                hostile,
            ),
            "classification_clues": [hostile],
            "quality_warnings": [hostile],
        }
        tokens = MarkdownIt("commonmark").parse(_markdown(card))
        self.assertEqual(sum(t.type == "heading_open" for t in tokens), 11)
        self.assertEqual(sum(t.type == "bullet_list_open" for t in tokens), 2)
        self.assertFalse(
            any(t.type in {"html_block", "fence", "code_block"} for t in tokens)
        )
        normalized = " ".join(hostile.split())
        occurrences = 0
        for token in tokens:
            for child in token.children or []:
                self.assertEqual(child.type, "text")
                if child.content == normalized:
                    occurrences += 1
        self.assertEqual(occurrences, 10)
