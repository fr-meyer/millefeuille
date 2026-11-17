"""Offline selection contracts; all receipts and model results are synthetic."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from millefeuille.domain import summary_live_execution as live
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_execution_scope import (
    validate_gpt_summary_approval_preview,
)
from millefeuille.domain.summary_preparation import (
    prepare_summary_execution_packages,
    verify_summary_preparation_package,
)
from millefeuille.domain.summary_results import verify_summary_dispatch_batch
from millefeuille.domain.summary_stage_selection import validate_summary_stages
from tests import test_millefeuille_summary_dispatch as dispatch_tests
from tests.test_millefeuille_summary_execution_scope import _approved_pair

STAGES = ["summarize_page", "summarize_full_paper"]


class TestSummaryStageSelection(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        text = ""
        for page in range(1, 10):
            text += f"# Page {page}\n"
            for section in range(6 if page == 1 else 5):
                text += f"# Section {page}-{section}\nSynthetic evidence.\n"
        route, structure, default = dispatch_tests.TestSummaryDispatch()._fixture(
            self.root, page_count=9, markdown_text=text
        )
        self.default = {
            "route_evidence_path": route,
            "structure_evidence_path": structure,
            "preparation_path": default,
        }
        result = prepare_summary_execution_packages(
            route_evidence_paths=[route],
            structure_evidence_paths=[structure],
            output_dir=self.root / "bounded",
            requested_stages=STAGES,
        )
        self.evidence = {
            **self.default,
            "preparation_path": result.documents[0].package_path,
        }
        self.plan = plan_grounded_gpt_summary_batch(**self.evidence)

    def test_exact_ten_preserves_all_fifty_six_prepared_units(self):
        package = verify_summary_preparation_package(**self.evidence)
        self.assertEqual(len(package["work_units"]["summarize_section"]), 46)
        self.assertEqual(len(self.plan.batch.units), 10)
        self.assertEqual(
            [u.stage for u in self.plan.batch.units], [STAGES[0]] * 9 + [STAGES[1]]
        )
        self.assertEqual(
            len(plan_grounded_gpt_summary_batch(**self.default).batch.units), 56
        )
        self.assertEqual(
            json.loads(self.plan.manifest.json_bytes)["requested_stages"], STAGES
        )
        self.assertNotEqual(
            self.plan.manifest.sha256,
            plan_grounded_gpt_summary_batch(**self.default).manifest.sha256,
        )
        verify_summary_dispatch_batch(batch=self.plan.batch, **self.evidence)

    def test_invalid_selections_fail_closed(self):
        for value in (
            [],
            [STAGES[0], STAGES[0]],
            ["unknown"],
            "summarize_page",
            [False],
            list(reversed(STAGES)),
        ):
            with (
                self.subTest(value=value),
                self.assertRaises(MillefeuilleContractError),
            ):
                validate_summary_stages(value)

    def test_batch_payload_selection_and_coverage_drift_rejected(self):
        batch = self.plan.batch
        changed = replace(batch.units[0], input_payload=b"tampered")
        for altered in (
            replace(batch, requested_stages=None),
            replace(batch, units=batch.units[:-1]),
            replace(batch, units=(changed,) + batch.units[1:]),
            replace(batch, preparation_sha256="sha256:" + "0" * 64),
        ):
            with self.subTest(), self.assertRaises(MillefeuilleContractError):
                verify_summary_dispatch_batch(batch=altered, **self.evidence)

    def test_unselected_units_cannot_be_truncated(self):
        path = self.evidence["preparation_path"]
        original = path.read_bytes()
        package = json.loads(original)
        package["work_units"]["summarize_section"] = []
        path.write_text(
            json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n"
        )
        with self.assertRaises(MillefeuilleContractError):
            plan_grounded_gpt_summary_batch(**self.evidence)

    def test_selection_tamper_invalidates_approval_before_reservation(self):
        packet, receipt = _approved_pair(
            self.root, self.plan.manifest.sha256, self.plan.batch.paper_id, 10
        )
        scope = {
            **self.evidence,
            "source_pack_root": self.root,
            "artifact_root": self.root,
        }
        preview = validate_gpt_summary_approval_preview(
            **scope, packet=packet, receipt=receipt
        )
        self.assertEqual(preview.request_count, 10)
        path = self.evidence["preparation_path"]
        package = json.loads(path.read_bytes())
        package["requested_stages"] = [
            "summarize_page",
            "summarize_section",
            "summarize_full_paper",
        ]
        path.write_text(
            json.dumps(
                package, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            + "\n"
        )
        client = Mock()
        with (
            patch.object(live, "request_gpt_summary_reservation") as reservation,
            self.assertRaises(MillefeuilleContractError),
        ):
            live.run_trusted_gpt_summary_batch(
                **scope,
                packet=packet,
                receipt=receipt,
                environment={"OPENCLAW_CODEX_OAUTH_READY": "1"},
                client=client,
            )
        reservation.assert_not_called()
        client.execute.assert_not_called()

    def test_unknown_duplicate_and_null_package_selection_refused(self):
        path = self.evidence["preparation_path"]
        original = json.loads(path.read_bytes())
        for selection in (None, [], ["unknown"], [STAGES[0], STAGES[0]]):
            package = deepcopy(original)
            package["requested_stages"] = selection
            path.write_text(
                json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n"
            )
            with (
                self.subTest(selection=selection),
                self.assertRaises(MillefeuilleContractError),
            ):
                verify_summary_preparation_package(**self.evidence)

    def test_bounded_trusted_execution_and_replay_use_exact_ten(self):
        from millefeuille.domain import summary_replay_reservation as reservation
        from millefeuille.domain import summary_trusted_approval as trusted
        from tests.test_millefeuille_summary_live_execution import _FakeClient
        from tests.test_millefeuille_summary_replay_reservation import _trusted_control
        from tests.test_millefeuille_summary_trusted_approval import _write_record

        packet, receipt = _approved_pair(
            self.root, self.plan.manifest.sha256, self.plan.batch.paper_id, 10
        )
        scope = {
            **self.evidence,
            "source_pack_root": self.root,
            "artifact_root": self.root,
        }
        record = {
            "schema_version": trusted._TRUSTED_APPROVAL_SCHEMA,
            "receipt_id": receipt.receipt_id,
            "receipt_digest": receipt.content_digest,
            "packet_digest": packet.content_digest,
            "manifest_sha256": self.plan.manifest.sha256,
            "paper_id": self.plan.batch.paper_id,
            "request_count": 10,
            "approver_id": receipt.approval.approver_id,
            "approved_at": receipt.approval.approved_at,
        }
        control = self.root / "control"
        control.mkdir(mode=0o700)
        approval = control / "approval.json"
        _write_record(approval, record)

        def reserve(**kwargs):
            return reservation.reserve_trusted_gpt_summary_receipt(**kwargs)

        events = []
        client = _FakeClient(self.plan.batch.units, events)
        with (
            _trusted_control(control, approval),
            patch.object(live, "request_gpt_summary_reservation", side_effect=reserve),
        ):
            outcome = live.run_trusted_gpt_summary_batch(
                **scope,
                packet=packet,
                receipt=receipt,
                environment={"OPENCLAW_CODEX_OAUTH_READY": "1"},
                client=client,
            )
            self.assertEqual(len(outcome.accepted.units), 10)
            with self.assertRaises(MillefeuilleContractError):
                live.run_trusted_gpt_summary_batch(
                    **scope,
                    packet=packet,
                    receipt=receipt,
                    environment={"OPENCLAW_CODEX_OAUTH_READY": "1"},
                    client=client,
                )
        self.assertEqual(client.calls, 10)
        self.assertFalse(
            any(e[0] == "execute" and e[1] == "summarize_section" for e in events)
        )

    def test_cli_selection_and_single_stage_manifest(self):
        from io import StringIO

        from jsonschema import Draft202012Validator

        from millefeuille.cli.stages import run_stage_cli

        output = self.root / "cli"
        args = [
            "summarize-prepare",
            "--route-evidence",
            str(self.evidence["route_evidence_path"]),
            "--structure-evidence",
            str(self.evidence["structure_evidence_path"]),
            "--output-dir",
            str(output),
            "--summary-stage",
            "summarize_full_paper",
            "--json",
        ]
        out, err = StringIO(), StringIO()
        self.assertEqual(run_stage_cli(args, stdout=out, stderr=err), 0, err.getvalue())
        plan = plan_grounded_gpt_summary_batch(
            **{
                **self.evidence,
                "preparation_path": output / "zotero-ITEM1.summary-preparation.json",
            }
        )
        self.assertEqual(len(plan.batch.units), 1)
        schema = json.loads(
            (
                Path(__file__).parents[1]
                / "specs/millefeuille-pipeline/summary-execution-manifest.schema.json"
            ).read_text()
        )
        Draft202012Validator(schema).validate(json.loads(plan.manifest.json_bytes))

    def test_unselected_structure_and_source_are_still_verified(self):
        path = self.root / "selected.md"
        path.write_text(path.read_text() + "Source drift")
        with self.assertRaises(MillefeuilleContractError):
            plan_grounded_gpt_summary_batch(**self.evidence)
