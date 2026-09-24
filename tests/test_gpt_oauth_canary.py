"""Offline safety tests for the single-use GPT OAuth canary boundary."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain import gpt_canary_broker as broker
from millefeuille.domain import gpt_oauth_canary as canary
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
        self,
        *,
        fail: bool = False,
        output_override: bytes | None = None,
        preflight_ready: bool = True,
    ) -> None:
        self.calls = 0
        self.preflight_calls = 0
        self.fail = fail
        self.output_override = output_override
        self.preflight_ready = preflight_ready

    def preflight_auth(self):
        self.preflight_calls += 1
        if not self.preflight_ready:
            raise MillefeuilleContractError(
                "OpenClaw agent-local GPT OAuth is unavailable"
            )

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


@contextmanager
def _trusted_approval(directory: str, packet, receipt):
    """Emulate trusted approval and a one-shot broker in offline client tests."""

    path = Path(directory) / "admin-approval.json"
    record = {
        "schema_version": "millefeuille-gpt-canary-approval/v0.1",
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.content_digest,
        "packet_digest": packet.content_digest,
        "approver_id": receipt.approval.approver_id,
        "approved_at": receipt.approval.approved_at,
    }
    path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    reserved: set[str] = set()

    def reserve(_packet, current_receipt):
        if current_receipt.content_digest in reserved:
            raise MillefeuilleContractError(
                "approved-live receipt has already been consumed"
            )
        reserved.add(current_receipt.content_digest)

    with (
        patch.object(canary, "_TRUSTED_APPROVAL_PATH", path),
        patch.object(canary, "_TRUSTED_APPROVAL_OWNER_UID", os.getuid()),
        patch.object(canary, "_reserve_with_broker", side_effect=reserve),
    ):
        yield


@unittest.skipUnless(os.name == "posix", "POSIX ledger only")
class TestGptOauthCanary(unittest.TestCase):
    def test_missing_agent_local_oauth_does_not_consume_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            client = _FakeClient(preflight_ready=False)
            kwargs = {
                "packet": packet,
                "receipt": receipt,
                "artifact_root": root,
                "environment": {"OPENCLAW_CODEX_OAUTH_READY": "present"},
                "client": client,
            }
            with _trusted_approval(directory, packet, receipt):
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "agent-local GPT OAuth"
                ):
                    run_gpt_oauth_canary(**kwargs)
                self.assertEqual(client.calls, 0)
                client.preflight_ready = True
                result = run_gpt_oauth_canary(**kwargs)
                self.assertEqual(result["executor_result"]["status"], "succeeded")
                self.assertEqual(client.calls, 1)

    def test_one_call_then_durable_replay_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            client = _FakeClient()
            kwargs = {
                "packet": packet,
                "receipt": receipt,
                "artifact_root": root,
                "environment": {"OPENCLAW_CODEX_OAUTH_READY": "present"},
                "client": client,
            }
            with _trusted_approval(directory, packet, receipt):
                result = run_gpt_oauth_canary(**kwargs)
                self.assertEqual(result["executor_result"]["status"], "succeeded")
                self.assertFalse(result["raw_output_persisted"])
                self.assertEqual(client.calls, 1)
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "already been consumed"
                ):
                    run_gpt_oauth_canary(**kwargs)
            self.assertEqual(client.calls, 1)

    def test_self_created_receipt_and_oauth_readiness_fail_before_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            client = _FakeClient()
            with self.assertRaisesRegex(
                MillefeuilleContractError, "broker is unavailable"
            ):
                run_gpt_oauth_canary(
                    packet=packet,
                    receipt=receipt,
                    artifact_root=root,
                    environment={"OPENCLAW_CODEX_OAUTH_READY": "present"},
                    client=client,
                )
            with _trusted_approval(directory, packet, receipt):
                with self.assertRaisesRegex(MillefeuilleContractError, "readiness"):
                    run_gpt_oauth_canary(
                        packet=packet,
                        receipt=receipt,
                        artifact_root=root,
                        environment={},
                        client=client,
                    )
                self.assertEqual(client.calls, 0)
                run_gpt_oauth_canary(
                    packet=packet,
                    receipt=receipt,
                    artifact_root=root,
                    environment={"OPENCLAW_CODEX_OAUTH_READY": "present"},
                    client=client,
                )
            self.assertEqual(client.calls, 1)

    def test_expired_receipt_cannot_reserve_or_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            client = _FakeClient()
            expired_at = datetime.fromisoformat(
                receipt.expires_at.replace("Z", "+00:00")
            )
            with (
                patch.object(canary, "_reserve_with_broker") as reserve,
                self.assertRaisesRegex(MillefeuilleContractError, "expired"),
            ):
                run_gpt_oauth_canary(
                    packet=packet,
                    receipt=receipt,
                    artifact_root=root,
                    environment={"OPENCLAW_CODEX_OAUTH_READY": "present"},
                    now=expired_at,
                    client=client,
                )
            reserve.assert_not_called()
            self.assertEqual(client.calls, 0)

    @unittest.skipIf(
        os.name != "posix" or os.getuid() == 0,
        "requires an unprivileged POSIX test user",
    )
    def test_caller_owned_approval_file_cannot_authenticate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            with (
                _trusted_approval(directory, packet, receipt),
                patch.object(canary, "_TRUSTED_APPROVAL_OWNER_UID", 0),
                self.assertRaisesRegex(
                    MillefeuilleContractError, "administrator-owned"
                ),
            ):
                canary._verify_trusted_approval(packet, receipt)
            self.assertFalse(root.exists())

    def test_grok_scope_refused_before_ledger_or_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root, model="grok-4.6")
            client = _FakeClient()
            with self.assertRaisesRegex(MillefeuilleContractError, "GPT-only"):
                run_gpt_oauth_canary(
                    packet=packet,
                    receipt=receipt,
                    artifact_root=root,
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
                "artifact_root": root,
                "environment": {"OPENCLAW_CODEX_OAUTH_READY": "present"},
            }
            with _trusted_approval(directory, packet, receipt):
                with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                    run_gpt_oauth_canary(**kwargs, client=failed_client)
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "already been consumed"
                ):
                    run_gpt_oauth_canary(**kwargs, client=_FakeClient())
            self.assertEqual(failed_client.calls, 1)

    def test_tampered_audit_blocks_future_call(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory)
            packet, receipt = _approved_pair(control / "artifact")
            with (
                patch.object(broker, "_CONTROL_DIR", control),
                patch.object(broker, "_CONTROL_OWNER_UID", os.getuid()),
            ):
                broker._reserve_root_approval(packet, receipt)
                connection = sqlite3.connect(control / "gpt-oauth-canary.sqlite3")
                try:
                    connection.execute("UPDATE approvals SET audit_json = ?", ('{}',))
                    connection.commit()
                finally:
                    connection.close()
                with self.assertRaisesRegex(MillefeuilleContractError, "audit"):
                    broker._reserve_root_approval(packet, receipt)

    @unittest.skipUnless(
        os.name == "posix" and os.geteuid() == 0,
        "requires Linux root and a separate model user",
    )
    def test_model_user_cannot_delete_root_replay_database(self):
        import pwd

        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory)
            control.chmod(0o755)
            packet, receipt = _approved_pair(control / "artifact")
            with (
                patch.object(broker, "_CONTROL_DIR", control),
                patch.object(broker, "_CONTROL_OWNER_UID", 0),
            ):
                broker._reserve_root_approval(packet, receipt)
                database = control / "gpt-oauth-canary.sqlite3"
                model_user = pwd.getpwnam("node")
                child = os.fork()
                if child == 0:
                    os.setgid(model_user.pw_gid)
                    os.setuid(model_user.pw_uid)
                    try:
                        database.unlink()
                    except PermissionError:
                        os._exit(0)
                    os._exit(1)
                _, status = os.waitpid(child, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                self.assertTrue(database.exists())
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "already been consumed"
                ):
                    broker._reserve_root_approval(packet, receipt)

    @unittest.skipUnless(
        os.name == "posix" and os.geteuid() == 0,
        "requires Linux root and a separate model user",
    )
    def test_root_broker_grants_one_node_reservation(self):
        import pwd

        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory)
            control.chmod(0o755)
            socket_path = control / "gpt-oauth-canary.sock"
            packet, receipt = _approved_pair(control / "artifact")
            record = {
                "schema_version": "millefeuille-gpt-canary-approval/v0.1",
                "receipt_id": receipt.receipt_id,
                "receipt_digest": receipt.content_digest,
                "packet_digest": packet.content_digest,
                "approver_id": receipt.approval.approver_id,
                "approved_at": receipt.approval.approved_at,
            }
            approval_path = control / "gpt-oauth-canary-approval.json"
            approval_path.write_text(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            approval_path.chmod(0o644)
            with (
                patch.object(canary, "_TRUSTED_APPROVAL_PATH", approval_path),
                patch.object(canary, "_BROKER_SOCKET_PATH", socket_path),
                patch.object(broker, "_BROKER_SOCKET_PATH", socket_path),
                patch.object(broker, "_CONTROL_DIR", control),
            ):
                server = os.fork()
                if server == 0:
                    try:
                        broker.serve_one_reservation(timeout_seconds=5)
                    except BaseException:
                        os._exit(1)
                    os._exit(0)
                for _ in range(200):
                    if socket_path.exists():
                        break
                    time.sleep(0.01)
                self.assertTrue(socket_path.exists())
                model_user = pwd.getpwnam("node")
                client = os.fork()
                if client == 0:
                    os.setgid(model_user.pw_gid)
                    os.setuid(model_user.pw_uid)
                    try:
                        canary._reserve_with_broker(packet, receipt)
                    except BaseException:
                        os._exit(1)
                    os._exit(0)
                _, client_status = os.waitpid(client, 0)
                _, server_status = os.waitpid(server, 0)
                self.assertEqual(os.waitstatus_to_exitcode(client_status), 0)
                self.assertEqual(os.waitstatus_to_exitcode(server_status), 0)
                self.assertTrue((control / "gpt-oauth-canary.sqlite3").exists())
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "already been consumed"
                ):
                    broker._reserve_root_approval(packet, receipt)

    def test_output_binding_drift_is_rejected_and_receipt_stays_consumed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            packet, receipt = _approved_pair(root)
            kwargs = {
                "packet": packet,
                "receipt": receipt,
                "artifact_root": root,
                "environment": {"OPENCLAW_CODEX_OAUTH_READY": "present"},
            }
            with _trusted_approval(directory, packet, receipt):
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
