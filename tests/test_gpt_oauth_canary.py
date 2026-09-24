"""Offline safety tests for the single-use GPT OAuth canary boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain.gpt_oauth_canary import run_gpt_oauth_canary
from millefeuille.domain.live_receipts import (
    APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
    ApprovedLiveReceipt,
    compute_approved_live_receipt_digest,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_executor import materialize_model_executor_result
from millefeuille.domain.operator_preflight import (
    OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION,
    OperatorPreflightPacket,
    compute_operator_authorization_context_digest,
    compute_operator_preflight_packet_digest,
    compute_operator_root_target_id,
)


class _FakeClient:
    def __init__(
        self, *, fail: bool = False, output_override: bytes | None = None
    ) -> None:
        self.calls = 0
        self.fail = fail
        self.output_override = output_override

    def execute(self, *, request, input_payload, output_validator):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated failure after reservation")
        assert b'"canary":"ok"' in input_payload
        output_validator({"canary": "ok"})
        output = b'{"canary":"ok"}'
        timestamp = datetime.now(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        model = request["requested_model"]
        result = materialize_model_executor_result(
            request=request,
            status="succeeded",
            actual_model=model,
            auth_profile_ref="openai:test-oauth",
            started_at=timestamp,
            completed_at=timestamp,
            attempts=[
                {
                    "sequence": 1,
                    "model": model,
                    "actual_model": model,
                    "status": "succeeded",
                    "started_at": timestamp,
                    "completed_at": timestamp,
                    "auth_class": "subscription_oauth",
                    "auth_profile_ref": "openai:test-oauth",
                    "failure_code": None,
                }
            ],
            fallback_reason=None,
            schema_validation_status="passed",
            output_sha256="sha256:" + hashlib.sha256(output).hexdigest(),
            output_bytes=len(output),
            usage=None,
            cost_micro_usd=None,
        )
        return OpenClawModelExecution(
            result=result,
            output=self.output_override if self.output_override is not None else output,
        )


def _approved_pair(root: Path, *, model: str = "gpt-5.6-sol"):
    now = datetime.now(UTC).replace(microsecond=0)
    root_text = str(root)
    destination = {
        "kind": "artifact-root",
        "id": compute_operator_root_target_id(root_text),
    }
    packet = {
        "schema_version": OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION,
        "packet_id": "packet-gpt-oauth-canary-1",
        "mode": "approved-live",
        "source": {
            "adapter": "local-fixture",
            "selector": {"kind": "paper-id", "value": "gpt-oauth-canary"},
            "item_cap": 1,
            "resolved_item_count": 1,
        },
        "run_id": "run-gpt-oauth-canary-1",
        "roots": {"artifact_root": root_text, "source_pack_root": root_text},
        "operations": ["model.execute"],
        "targets": [destination],
        "destinations": [destination],
        "provider": {
            "provider_id": "openai",
            "model_id": model,
            "profile_id": "openclaw-subscription-oauth",
        },
        "limits": {"max_provider_calls": 1, "max_cost_usd_micros": 0},
        "disposal_policy": {
            "pdfs": "not-applicable",
            "provider_payloads": "never-persist",
            "temporary_files": "delete-after-run",
        },
        "stop_conditions": [
            "auth-failure",
            "model-mismatch",
            "provider-error",
            "schema-failure",
        ],
        "rollback_actions": ["stop-and-review"],
        "acceptance_status": "not-applicable",
        "credential_requirements": [
            {"type": "model-oauth", "reference": "OPENCLAW_CODEX_OAUTH_READY"}
        ],
        "approval_receipt": None,
    }
    packet["targets"] = sorted(
        [
            destination,
            {
                "kind": "preflight-scope",
                "id": compute_operator_authorization_context_digest(packet),
            },
        ],
        key=lambda row: (row["kind"], row["id"]),
    )
    receipt = {
        "schema_version": APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
        "receipt_id": "receipt-gpt-oauth-canary-1",
        "run_id": packet["run_id"],
        "approval": {
            "approver_id": "fr-meyer",
            "approved_at": now.isoformat().replace("+00:00", "Z"),
        },
        "expires_at": (now + timedelta(hours=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "scope": {
            "operations": packet["operations"],
            "targets": packet["targets"],
            "max_items": 1,
            "selector": packet["source"]["selector"],
            "output_root": root_text,
            "source_pack_root": root_text,
            "provider": {"provider_id": "openai", "model_id": model},
            "limits": packet["limits"],
            "disposal_policy": packet["disposal_policy"],
            "stop_conditions": packet["stop_conditions"],
        },
    }
    receipt["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_approved_live_receipt_digest(receipt),
    }
    packet["approval_receipt"] = {
        "receipt_id": receipt["receipt_id"],
        "content_digest": receipt["integrity"]["content_digest"],
    }
    packet["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_operator_preflight_packet_digest(packet),
    }
    return OperatorPreflightPacket.from_dict(packet), ApprovedLiveReceipt.from_dict(
        receipt
    )


@unittest.skipUnless(__import__("os").name == "posix", "POSIX ledger only")
class TestGptOauthCanary(unittest.TestCase):
    def test_one_call_then_durable_replay_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            client = _FakeClient()
            kwargs = {
                "packet": packet,
                "receipt": receipt,
                "trusted_receipt_digest": receipt.content_digest,
                "ledger_dir": root,
                "environment": {"OPENCLAW_CODEX_OAUTH_READY": "present"},
                "client": client,
            }
            result = run_gpt_oauth_canary(**kwargs)
            self.assertEqual(result["executor_result"]["status"], "succeeded")
            self.assertFalse(result["raw_output_persisted"])
            self.assertEqual(client.calls, 1)
            with self.assertRaisesRegex(
                MillefeuilleContractError, "already been consumed"
            ):
                run_gpt_oauth_canary(**kwargs)
            self.assertEqual(client.calls, 1)

    def test_trusted_digest_and_oauth_readiness_fail_before_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            client = _FakeClient()
            with self.assertRaisesRegex(MillefeuilleContractError, "trusted approval"):
                run_gpt_oauth_canary(
                    packet=packet,
                    receipt=receipt,
                    trusted_receipt_digest="sha256:" + "0" * 64,
                    ledger_dir=root,
                    environment={"OPENCLAW_CODEX_OAUTH_READY": "present"},
                    client=client,
                )
            with self.assertRaisesRegex(MillefeuilleContractError, "readiness"):
                run_gpt_oauth_canary(
                    packet=packet,
                    receipt=receipt,
                    trusted_receipt_digest=receipt.content_digest,
                    ledger_dir=root,
                    environment={},
                    client=client,
                )
            self.assertEqual(client.calls, 0)
            run_gpt_oauth_canary(
                packet=packet,
                receipt=receipt,
                trusted_receipt_digest=receipt.content_digest,
                ledger_dir=root,
                environment={"OPENCLAW_CODEX_OAUTH_READY": "present"},
                client=client,
            )
            self.assertEqual(client.calls, 1)

    def test_grok_scope_refused_before_ledger_or_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root, model="grok-4.6")
            client = _FakeClient()
            with self.assertRaisesRegex(MillefeuilleContractError, "GPT-only"):
                run_gpt_oauth_canary(
                    packet=packet,
                    receipt=receipt,
                    trusted_receipt_digest=receipt.content_digest,
                    ledger_dir=root,
                    environment={"OPENCLAW_CODEX_OAUTH_READY": "present"},
                    client=client,
                )
            self.assertEqual(client.calls, 0)
            self.assertFalse(root.exists())

    def test_crash_after_reservation_requires_new_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            failed_client = _FakeClient(fail=True)
            kwargs = {
                "packet": packet,
                "receipt": receipt,
                "trusted_receipt_digest": receipt.content_digest,
                "ledger_dir": root,
                "environment": {"OPENCLAW_CODEX_OAUTH_READY": "present"},
            }
            with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                run_gpt_oauth_canary(**kwargs, client=failed_client)
            with self.assertRaisesRegex(
                MillefeuilleContractError, "already been consumed"
            ):
                run_gpt_oauth_canary(**kwargs, client=_FakeClient())
            self.assertEqual(failed_client.calls, 1)

    def test_tampered_audit_blocks_future_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            kwargs = {
                "packet": packet,
                "receipt": receipt,
                "trusted_receipt_digest": receipt.content_digest,
                "ledger_dir": root,
                "environment": {"OPENCLAW_CODEX_OAUTH_READY": "present"},
            }
            run_gpt_oauth_canary(**kwargs, client=_FakeClient())
            connection = sqlite3.connect(root / "gpt-oauth-canary.sqlite3")
            try:
                connection.execute("UPDATE approvals SET audit_json = ?", ('{}',))
                connection.commit()
            finally:
                connection.close()
            client = _FakeClient()
            with self.assertRaisesRegex(MillefeuilleContractError, "audit"):
                run_gpt_oauth_canary(**kwargs, client=client)
            self.assertEqual(client.calls, 0)

    def test_output_binding_drift_is_rejected_and_receipt_stays_consumed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            kwargs = {
                "packet": packet,
                "receipt": receipt,
                "trusted_receipt_digest": receipt.content_digest,
                "ledger_dir": root,
                "environment": {"OPENCLAW_CODEX_OAUTH_READY": "present"},
            }
            with self.assertRaisesRegex(MillefeuilleContractError, "binding"):
                run_gpt_oauth_canary(
                    **kwargs,
                    client=_FakeClient(output_override=b'{"canary":"bad"}'),
                )
            with self.assertRaisesRegex(
                MillefeuilleContractError, "already been consumed"
            ):
                run_gpt_oauth_canary(**kwargs, client=_FakeClient())


if __name__ == "__main__":
    unittest.main()
