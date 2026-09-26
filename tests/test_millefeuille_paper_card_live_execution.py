"""One approved card call requires reservation and fresh source before dispatch."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain import paper_card_live_execution as live
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.paper_card_execution_scope import (
    validate_gpt_card_approval_preview,
)
from tests import test_millefeuille_paper_card_execution_scope as scope_tests
from tests import test_millefeuille_paper_card_results as result_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


class _FakeClient:
    def __init__(
        self, fixture, events, *, change=None, failure=None, auth_failure=False
    ):
        self.fixture, self.events = fixture, events
        self.change, self.failure, self.auth_failure = change, failure, auth_failure
        self.calls = 0

    def preflight_auth(self, *, run_timeout_seconds):
        self.events.append("auth")
        if self.auth_failure:
            raise MillefeuilleContractError("saved OAuth unavailable")

    def execute(self, *, request, input_payload, output_validator):
        assert request == self.fixture.plan.request
        assert input_payload == self.fixture.plan.input_payload
        self.events.append("execute")
        self.calls += 1
        if self.failure:
            raise MillefeuilleContractError(self.failure)
        execution = self.fixture._execution()
        output_validator(json.loads(execution.output))
        if self.change:
            return self.change(execution)
        return execution


@requires_secure_nofollow_writes
class TestTrustedGptCardExecution(unittest.TestCase):
    def setUp(self):
        fixture = result_tests.TestGptPaperCardResults(
            "test_validates_exact_result_and_plans_actual_metadata_without_writes"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        root = fixture.fixture.fixture.root
        self.packet, self.receipt = scope_tests._card_pair(root, fixture.plan)
        self.values = dict(
            fixture.values,
            artifact_root=root,
            run_id=fixture.plan.run_id,
            packet=self.packet,
            receipt=self.receipt,
        )
        self.preview = validate_gpt_card_approval_preview(**self.values)

    def _run(self, client, *, environment=None):
        return live.run_trusted_gpt_paper_card(
            **self.values,
            environment={"OPENCLAW_CODEX_OAUTH_READY": "1"}
            if environment is None
            else environment,
            client=client,
        )

    def test_one_call_follows_reservation_and_holds_validated_output_without_writes(
        self,
    ):
        events = []
        client = _FakeClient(self.fixture, events)
        root = self.values["source_pack_root"]
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

        def reserve(**_kwargs):
            events.append("reserve")
            return self.preview

        with patch.object(
            live, "request_gpt_card_reservation", side_effect=reserve
        ) as broker:
            outcome = self._run(client)
        after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(events, ["auth", "reserve", "auth", "execute"])
        self.assertEqual(client.calls, 1)
        self.assertEqual(broker.call_count, 1)
        self.assertEqual(
            outcome.validated.executor_result["usage"]["total_tokens"], 120
        )
        self.assertEqual(outcome.approval, self.preview)
        self.assertNotIn("Private", repr(outcome))

    def test_missing_readiness_or_saved_auth_never_reserves_or_dispatches(self):
        for missing_marker in (True, False):
            client = _FakeClient(self.fixture, [], auth_failure=not missing_marker)
            with (
                patch.object(live, "request_gpt_card_reservation") as broker,
                self.assertRaises(MillefeuilleContractError),
            ):
                self._run(client, environment={} if missing_marker else None)
            broker.assert_not_called()
            self.assertEqual(client.calls, 0)

    def test_missing_broker_or_wrong_ack_never_dispatches(self):
        for behavior in (
            {"side_effect": MillefeuilleContractError("broker unavailable")},
            {
                "return_value": replace(
                    self.preview, manifest_sha256="sha256:" + "0" * 64
                )
            },
        ):
            client = _FakeClient(self.fixture, [])
            with (
                patch.object(live, "request_gpt_card_reservation", **behavior),
                self.assertRaises(MillefeuilleContractError),
            ):
                self._run(client)
            self.assertEqual(client.calls, 0)

    def test_source_drift_after_reservation_stops_before_call(self):
        root_fixture = self.fixture.fixture.fixture
        path = root_fixture.root / root_fixture.plan.texts[0].ref

        def reserve(**_kwargs):
            path.write_bytes(path.read_bytes() + b"changed")
            return self.preview

        client = _FakeClient(self.fixture, [])
        with (
            patch.object(live, "request_gpt_card_reservation", side_effect=reserve),
            self.assertRaises(MillefeuilleContractError),
        ):
            self._run(client)
        self.assertEqual(client.calls, 0)

    def test_invalid_output_missing_usage_or_nonzero_cost_stops_after_one_call(self):
        def changed_result(execution, *, key, value):
            result = deepcopy(execution.result)
            result[key] = value
            return OpenClawModelExecution(result=result, output=execution.output)

        changes = (
            lambda execution: OpenClawModelExecution(execution.result, b"changed"),
            lambda execution: changed_result(execution, key="usage", value=None),
            lambda execution: changed_result(
                execution, key="cost", value={"currency": "USD", "micro_usd": 1}
            ),
        )
        for change in changes:
            client = _FakeClient(self.fixture, [], change=change)
            with (
                patch.object(
                    live, "request_gpt_card_reservation", return_value=self.preview
                ),
                self.assertRaises(MillefeuilleContractError),
            ):
                self._run(client)
            self.assertEqual(client.calls, 1)

    def test_failed_call_has_no_retry(self):
        client = _FakeClient(self.fixture, [], failure="provider failed")
        with (
            patch.object(
                live, "request_gpt_card_reservation", return_value=self.preview
            ),
            self.assertRaisesRegex(MillefeuilleContractError, "provider failed"),
        ):
            self._run(client)
        self.assertEqual(client.calls, 1)
