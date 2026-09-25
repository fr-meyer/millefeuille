"""Offline checks for receipt-bound, GPT-only paper summary execution."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain import summary_live_execution as live
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_execution_scope import (
    validate_gpt_summary_approval_preview,
)
from tests import test_millefeuille_summary_results as result_tests
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_summary_trusted_approval import _case


class _FakeClient:
    def __init__(
        self, units, events, *, after_first=None, tamper_first=False, billed_first=False
    ):
        self.units = units
        self.events = events
        self.after_first = after_first
        self.tamper_first = tamper_first
        self.billed_first = billed_first
        self.calls = 0

    def preflight_auth(self, *, run_timeout_seconds):
        self.events.append(("auth", run_timeout_seconds))

    def execute(self, *, request, input_payload, output_validator):
        unit = self.units[self.calls]
        assert request == unit.request
        assert input_payload == unit.input_payload
        self.events.append(("execute", unit.stage, unit.unit_id))
        self.calls += 1
        output = result_tests.TestSummaryResults._output(unit)
        output_validator(json.loads(output))
        execution = result_tests.TestSummaryResults._execution(unit, output)
        if self.calls == 1:
            if self.after_first is not None:
                self.after_first()
            if self.tamper_first:
                return OpenClawModelExecution(result=execution.result, output=b"{}")
            if self.billed_first:
                billed = deepcopy(execution.result)
                billed["cost"] = {"currency": "USD", "micro_usd": 1}
                return OpenClawModelExecution(result=billed, output=output)
        return execution


@requires_secure_nofollow_writes
class TestTrustedGptSummaryExecution(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "paper"
        self.root.mkdir()
        self.evidence, self.packet, self.receipt, _record = _case(self.root)
        self.plan = plan_grounded_gpt_summary_batch(
            route_evidence_path=self.evidence["route_evidence_path"],
            structure_evidence_path=self.evidence["structure_evidence_path"],
            preparation_path=self.evidence["preparation_path"],
        )
        self.preview = validate_gpt_summary_approval_preview(
            **self.evidence, packet=self.packet, receipt=self.receipt
        )

    def _run(self, client):
        return live.run_trusted_gpt_summary_batch(
            **self.evidence,
            packet=self.packet,
            receipt=self.receipt,
            environment={"OPENCLAW_CODEX_OAUTH_READY": "1"},
            client=client,
        )

    def test_approved_batch_is_private_and_has_no_paper_writes(self):
        events = []
        client = _FakeClient(self.plan.batch.units, events)
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

        def reserve(**_kwargs):
            events.append(("reserve",))
            return self.preview

        with patch.object(
            live, "request_gpt_summary_reservation", side_effect=reserve
        ) as broker:
            outcome = self._run(client)
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(client.calls, self.preview.request_count)
        self.assertEqual(broker.call_count, 1)
        self.assertEqual(events[0][0], "auth")
        self.assertEqual(events[1], ("reserve",))
        self.assertEqual(len(outcome.accepted.units), self.preview.request_count)
        self.assertEqual(len(outcome.executions), self.preview.request_count)
        self.assertNotIn("Private accepted summary", repr(outcome))

    def test_missing_readiness_or_broker_never_dispatches(self):
        client = _FakeClient(self.plan.batch.units, [])
        with patch.object(live, "request_gpt_summary_reservation") as broker:
            with self.assertRaises(MillefeuilleContractError):
                live.run_trusted_gpt_summary_batch(
                    **self.evidence,
                    packet=self.packet,
                    receipt=self.receipt,
                    environment={},
                    client=client,
                )
            broker.assert_not_called()
        self.assertEqual(client.calls, 0)

        with (
            patch.object(
                live,
                "request_gpt_summary_reservation",
                side_effect=MillefeuilleContractError("broker absent"),
            ),
            self.assertRaisesRegex(MillefeuilleContractError, "broker absent"),
        ):
            self._run(client)
        self.assertEqual(client.calls, 0)

    def test_source_drift_after_one_call_stops_the_batch(self):
        selected_markdown = self.root / "selected.md"

        def change_source():
            selected_markdown.write_text("Changed private text.\n", encoding="utf-8")

        client = _FakeClient(self.plan.batch.units, [], after_first=change_source)
        with (
            patch.object(
                live, "request_gpt_summary_reservation", return_value=self.preview
            ),
            self.assertRaises(MillefeuilleContractError),
        ):
            self._run(client)
        self.assertEqual(client.calls, 1)

    def test_bad_first_result_or_reservation_stops_before_next_call(self):
        client = _FakeClient(self.plan.batch.units, [], tamper_first=True)
        with (
            patch.object(
                live, "request_gpt_summary_reservation", return_value=self.preview
            ),
            self.assertRaisesRegex(MillefeuilleContractError, "output binding drift"),
        ):
            self._run(client)
        self.assertEqual(client.calls, 1)

        fresh_client = _FakeClient(self.plan.batch.units, [])
        with (
            patch.object(
                live,
                "request_gpt_summary_reservation",
                return_value=replace(self.preview, request_count=99),
            ),
            self.assertRaisesRegex(MillefeuilleContractError, "reservation drift"),
        ):
            self._run(fresh_client)
        self.assertEqual(fresh_client.calls, 0)

    def test_nonzero_provider_cost_stops_after_one_call(self):
        client = _FakeClient(self.plan.batch.units, [], billed_first=True)
        with (
            patch.object(
                live, "request_gpt_summary_reservation", return_value=self.preview
            ),
            self.assertRaisesRegex(MillefeuilleContractError, "nonzero provider cost"),
        ):
            self._run(client)
        self.assertEqual(client.calls, 1)
