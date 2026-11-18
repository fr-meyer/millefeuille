"""Synthetic exact-scope classification controller tests; no real authority."""

from contextlib import ExitStack, contextmanager, redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.cli import classification_operator as cli
from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain import classification_live as live
from millefeuille.domain import classification_live_broker as broker
from millefeuille.domain import classification_live_scope as scope
from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    compute_approved_live_receipt_digest,
)
from millefeuille.domain.model_executor import materialize_model_executor_result
from millefeuille.domain.operator_preflight import (
    OperatorPreflightPacket,
    compute_operator_authorization_context_digest,
    compute_operator_preflight_packet_digest,
    compute_operator_root_target_id,
)
from tests import test_millefeuille_classification_model as model_tests
from tests.test_millefeuille_summary_execution_scope import _approved_pair


def pair(inputs, plan, publication=None):
    packet, receipt = _approved_pair(
        Path(inputs["source_pack_root"]), scope.identity(plan), plan.paper_id, 1
    )
    p, r = packet.to_dict(), receipt.to_dict()
    destination = {
        "kind": "artifact-root",
        "id": compute_operator_root_target_id(str(scope.OUTPUT_ROOT)),
    }
    p["run_id"] = plan.run_id
    p["roots"]["artifact_root"] = str(scope.OUTPUT_ROOT)
    p["operations"] = [
        "model.classify" if publication is None else "classification.review"
    ]
    p["acceptance_status"] = "pass"
    p["stop_conditions"] = list(scope.STOPS)
    p["destinations"] = [destination]
    p["targets"] = [
        destination,
        {"kind": "paper-id", "id": plan.paper_id},
        {"kind": "analysis-run", "id": plan.run_id},
        {"kind": "classification-plan-v1", "id": scope.identity(plan)},
        {"kind": "taxonomy-lock", "id": plan.taxonomy_lock["content_identity"]},
    ]
    if publication is not None:
        p["targets"].append({"kind": "classification-output-v1", "id": publication})
        p["disposal_policy"]["provider_payloads"] = "not-applicable"
        p["provider"] = None
        p["credential_requirements"] = []
        p["limits"]["max_provider_calls"] = 0
    p["targets"] = sorted(p["targets"], key=lambda x: (x["kind"], x["id"]))
    p["targets"].append(
        {
            "kind": "preflight-scope",
            "id": compute_operator_authorization_context_digest(p),
        }
    )
    p["targets"] = sorted(p["targets"], key=lambda x: (x["kind"], x["id"]))
    r["run_id"] = plan.run_id
    r["scope"]["output_root"] = str(scope.OUTPUT_ROOT)
    r["scope"]["stop_conditions"] = p["stop_conditions"]
    r["receipt_id"] = "classification-" + (
        "execute" if publication is None else "publish"
    )
    for name in ("operations", "provider", "targets", "limits", "disposal_policy"):
        r["scope"][name] = p[name]
    if r["scope"]["provider"] is not None:
        r["scope"]["provider"] = {
            k: v for k, v in p["provider"].items() if k != "profile_id"
        }
    r["integrity"]["content_digest"] = compute_approved_live_receipt_digest(r)
    p["approval_receipt"] = {
        "receipt_id": r["receipt_id"],
        "content_digest": r["integrity"]["content_digest"],
    }
    p["integrity"]["content_digest"] = compute_operator_preflight_packet_digest(p)
    return OperatorPreflightPacket.from_dict(p), ApprovedLiveReceipt.from_dict(r)


@contextmanager
def portable_cli_admission(test, args):
    """Controlled downstream unit coverage, not native Windows CLI support."""
    native_os = cli.os
    if native_os.name != "nt":
        yield
        return

    def refused():
        # The real guard must reject before metadata, approval or any effect.
        with ExitStack() as stack:
            effects = [
                stack.enter_context(patch.object(cli, name))
                for name in (
                    "read_bytes_no_follow",
                    "prepare",
                    "_approval",
                    "validate_scope",
                    "execute_classification",
                    "_publication_approval",
                    "request",
                )
            ]
            effects.append(
                stack.enter_context(patch.object(live, "OpenClawModelClient"))
            )
            with test.assertRaisesRegex(
                ValueError, "^unprivileged POSIX operator required$"
            ):
                cli._operate(SimpleNamespace())
            with redirect_stdout(io.StringIO()) as output:
                test.assertEqual(cli.run_classification_operator_cli(args), 3)
            test.assertEqual(
                json.loads(output.getvalue()),
                {
                    "status": "stopped",
                    "publication": "not-confirmed",
                    "retry": "forbidden-without-fresh-review",
                },
            )
            for effect in effects:
                effect.assert_not_called()

    refused()
    try:
        # Replace only this module binding, never os.name or filesystem semantics.
        with patch.object(
            cli, "os", SimpleNamespace(name="posix", geteuid=lambda: 1000)
        ):
            yield
    finally:
        test.assertIs(cli.os, native_os)
        refused()


class TestClassificationLive(unittest.TestCase):
    def setUp(self):
        self.fixture = model_tests.ClassificationModelTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        if os.name == "nt":
            self.enterContext(
                patch.object(scope, "OUTPUT_ROOT", (f.root / "live-output").resolve())
            )
        self.inputs = {
            "source_pack_root": str(f.root.resolve()),
            "run_id": model_tests.RUN_ID,
            "paper_id": model_tests.PAPER_ID,
            "expected_selected_text_sha256": f.selected_sha,
            "taxonomy_lock_path": str(f.lock_path.resolve()),
            "handoff_path": str(f.handoff.resolve()),
        }
        self.plan = scope.prepare(self.inputs)
        self.packet, self.receipt = pair(self.inputs, self.plan)

    def execution(self):
        output = scope.canonical(self.fixture.output(self.plan))
        request = self.plan.executor_request()
        result = materialize_model_executor_result(
            request=request,
            status="succeeded",
            actual_model="openai/gpt-6-astra",
            auth_profile_ref="openai:synthetic",
            started_at="2026-11-16T00:00:00Z",
            completed_at="2026-11-16T00:00:01Z",
            attempts=[
                {
                    "sequence": 1,
                    "model": "openai/gpt-6-astra",
                    "actual_model": "openai/gpt-6-astra",
                    "status": "succeeded",
                    "failure_code": None,
                    "auth_class": "subscription_oauth",
                    "auth_profile_ref": "openai:synthetic",
                    "started_at": "2026-11-16T00:00:00Z",
                    "completed_at": "2026-11-16T00:00:01Z",
                }
            ],
            fallback_reason=None,
            schema_validation_status="passed",
            output_sha256=scope.digest(output),
            output_bytes=len(output),
            usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            cost_micro_usd=None,
        )
        return OpenClawModelExecution(result=result, output=output)

    def test_scope_and_output(self):
        checked = scope.validate_scope(
            inputs=self.inputs, packet=self.packet, receipt=self.receipt
        )
        outcome = live.ClassificationOutcome(
            checked.receipt_digest, checked.plan_sha256, self.execution()
        )
        files = live.validated_files(inputs=self.inputs, outcome=outcome)
        self.assertEqual(len(files), 3)
        p, r = pair(self.inputs, self.plan, live.output_identity(files))
        self.assertEqual(
            scope.validate_scope(
                inputs=self.inputs,
                packet=p,
                receipt=r,
                output_sha256=live.output_identity(files),
            ).operation,
            "classification.review",
        )

    def test_actual_executor_and_no_retry_after_broker_denial(self):
        with (
            patch.dict(os.environ, {"OPENCLAW_CODEX_OAUTH_READY": "1"}),
            patch.object(live, "OpenClawModelClient") as factory,
            patch.object(broker, "request") as reserve,
        ):
            factory.return_value.execute.return_value = self.execution()
            outcome = live.execute_classification(
                inputs=self.inputs, packet=self.packet, receipt=self.receipt
            )
            self.assertEqual(outcome.plan_sha256, scope.identity(self.plan))
            factory.return_value.execute.assert_called_once()
            reserve.assert_called_once()
            reserve.side_effect = ValueError("deny")
            with self.assertRaises(ValueError):
                live.execute_classification(
                    inputs=self.inputs, packet=self.packet, receipt=self.receipt
                )
            self.assertEqual(factory.return_value.execute.call_count, 1)

    def test_expiry_and_source_drift_deny(self):
        with self.assertRaises(ValueError):
            scope.validate_scope(
                inputs=self.inputs,
                packet=self.packet,
                receipt=self.receipt,
                now=datetime.now(UTC) + timedelta(days=10),
            )
        self.fixture.lock_path.write_text("{}")
        with self.assertRaises(ValueError):
            scope.validate_scope(
                inputs=self.inputs, packet=self.packet, receipt=self.receipt
            )

    def assert_native_broker_refusal(self, outcome=None):
        before = {
            p: p.read_bytes() for p in self.fixture.root.rglob("*") if p.is_file()
        }
        with (
            patch.object(broker, "_read_administrator_file") as authority,
            patch.object(broker, "validate_scope") as validate,
            patch.object(broker, "_consume") as consume,
        ):
            with self.assertRaisesRegex(
                ValueError, "^classification broker requires Linux root$"
            ):
                broker._process(
                    {
                        "inputs": self.inputs,
                        "packet": self.packet.to_dict(),
                        "receipt": self.receipt.to_dict(),
                        "outcome": outcome,
                    }
                )
            authority.assert_not_called()
            validate.assert_not_called()
            consume.assert_not_called()
        self.assertEqual(
            before,
            {p: p.read_bytes() for p in self.fixture.root.rglob("*") if p.is_file()},
        )

    def test_real_ledger_publication_and_replay_denial(self):
        if os.name == "nt":
            execution = self.execution()
            outcome = live.ClassificationOutcome(
                self.receipt.content_digest, scope.identity(self.plan), execution
            )
            self.assertEqual(
                len(live.validated_files(inputs=self.inputs, outcome=outcome)), 3
            )
            self.assert_native_broker_refusal()
            self.assert_native_broker_refusal(
                {
                    "execution_receipt_digest": self.receipt.content_digest,
                    "plan_sha256": scope.identity(self.plan),
                    "result": execution.result,
                    "output": execution.output.decode(),
                }
            )
            return
        from contextlib import ExitStack
        import sqlite3

        root = Path(self.inputs["source_pack_root"])
        output = root / "output-root"
        output.mkdir(mode=0o700)
        control = root / "control"
        control.mkdir(mode=0o700)
        with ExitStack() as stack:
            stack.enter_context(patch.object(scope, "OUTPUT_ROOT", output))
            stack.enter_context(patch.object(broker, "OUTPUT_ROOT", output))
            stack.enter_context(patch.object(broker, "CONTROL_DIR", control))
            stack.enter_context(patch.object(broker, "SOCKET", control / "broker.sock"))
            stack.enter_context(
                patch.object(broker, "LEDGER", control / "ledger.sqlite3")
            )
            stack.enter_context(patch.object(broker, "_require_control_dir"))
            stack.enter_context(
                patch.object(broker, "_require_private_database_or_absent")
            )
            stack.enter_context(patch.object(broker, "_ROOT_UID", os.geteuid()))
            packet, receipt = pair(self.inputs, self.plan)

            def payload(p, r, outcome=None):
                return {
                    "inputs": self.inputs,
                    "packet": p.to_dict(),
                    "receipt": r.to_dict(),
                    "outcome": outcome,
                }

            current = [packet, receipt]
            stack.enter_context(
                patch.object(
                    broker,
                    "_read_administrator_file",
                    side_effect=lambda _path: (
                        scope.canonical(
                            broker.approval_record(
                                inputs=self.inputs,
                                packet=current[0],
                                receipt=current[1],
                            )
                        )
                        + b"\n"
                    ),
                )
            )
            self.assertEqual(
                broker._process(payload(packet, receipt))["status"], "reserved"
            )
            with self.assertRaises(ValueError):
                broker._process(payload(packet, receipt))
            execution = self.execution()
            outcome = live.ClassificationOutcome(
                receipt.content_digest, scope.identity(self.plan), execution
            )
            files = live.validated_files(inputs=self.inputs, outcome=outcome)
            p, r = pair(self.inputs, self.plan, live.output_identity(files))
            current[:] = [p, r]
            wire = {
                "execution_receipt_digest": receipt.content_digest,
                "plan_sha256": scope.identity(self.plan),
                "result": execution.result,
                "output": execution.output.decode(),
            }
            self.assertEqual(
                broker._process(payload(p, r, wire))["status"],
                "published-proposal-not-classified",
            )
            for ref, data in files.items():
                self.assertEqual((output / ref).read_bytes(), data)
            from millefeuille.domain.classification_output_plan import (
                plan_classification_from_evidence,
            )

            evidence_path = next(output.glob("*.evidence.json"))
            rendered = plan_classification_from_evidence(
                evidence_path=evidence_path,
                source_pack_root=self.inputs["source_pack_root"],
                run_id=self.inputs["run_id"],
                paper_id=self.inputs["paper_id"],
            )
            self.assertTrue(rendered)
            with self.assertRaises(ValueError):
                broker._process(payload(p, r, wire))
            connection = sqlite3.connect(control / "ledger.sqlite3")
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0],
                    2,
                )
            finally:
                connection.close()

    def test_wrong_root_authority_denies_before_privileged_source_reads(self):
        if os.name == "nt":
            self.assert_native_broker_refusal()
            return
        with (
            patch.object(broker, "_ROOT_UID", os.geteuid()),
            patch.object(
                broker, "_read_administrator_file", side_effect=ValueError("untrusted")
            ),
            patch.object(broker, "validate_scope") as validate,
        ):
            with self.assertRaises(ValueError):
                broker._process(
                    {
                        "inputs": self.inputs,
                        "packet": self.packet.to_dict(),
                        "receipt": self.receipt.to_dict(),
                        "outcome": None,
                    }
                )
            validate.assert_not_called()

    def test_invalid_model_output_or_usage_cannot_publish(self):
        execution = self.execution()
        for bad in (
            replace(execution, output=b"{}"),
            replace(execution, result={**execution.result, "usage": None}),
        ):
            with self.assertRaises(ValueError):
                live.validated_files(
                    inputs=self.inputs,
                    outcome=live.ClassificationOutcome(
                        self.receipt.content_digest, scope.identity(self.plan), bad
                    ),
                )

    def test_cli_dispatches_execution_and_distinct_publication(self):
        root = Path(self.inputs["source_pack_root"])
        input_path = root / "cli-inputs.json"
        input_path.write_text(json.dumps(self.inputs))
        args = [
            "execute-publish",
            "--mode",
            "approved-live",
            "--inputs",
            str(input_path),
        ]
        with portable_cli_admission(self, ["plan", "--inputs", str(input_path)]):
            for flag, value in (("packet", self.packet), ("receipt", self.receipt)):
                path = root / (flag + ".json")
                path.write_text(json.dumps(value.to_dict()))
                args += ["--" + flag, str(path)]
            outcome = live.ClassificationOutcome(
                self.receipt.content_digest, scope.identity(self.plan), self.execution()
            )
            files = live.validated_files(inputs=self.inputs, outcome=outcome)
            p, r = pair(self.inputs, self.plan, live.output_identity(files))
            with (
                patch.object(
                    cli, "execute_classification", return_value=outcome
                ) as execute,
                patch.object(
                    cli,
                    "_publication_approval",
                    return_value={"packet": p, "receipt": r},
                ),
                patch.object(
                    cli,
                    "request",
                    return_value={"status": "published-proposal-not-classified"},
                ) as publish,
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(cli.run_classification_operator_cli(args), 0)
                execute.assert_called_once()
                publish.assert_called_once()
                self.assertIs(publish.call_args.kwargs["outcome"], outcome)
            self.assertNotIn("rationale", output.getvalue())
            with (
                patch.object(cli, "execute_classification", return_value=outcome),
                patch.object(cli, "_publication_approval", side_effect=EOFError),
                patch.object(cli, "request") as publish,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.run_classification_operator_cli(args), 3)
                publish.assert_not_called()

    def test_summary_receipt_or_missing_root_socket_cannot_authorize(self):
        packet, receipt = _approved_pair(
            Path(self.inputs["source_pack_root"]),
            scope.identity(self.plan),
            self.plan.paper_id,
            1,
        )
        with self.assertRaises(ValueError):
            scope.validate_scope(inputs=self.inputs, packet=packet, receipt=receipt)
        with (
            patch.object(
                broker, "SOCKET", Path(self.inputs["source_pack_root"]) / "missing.sock"
            ),
            self.assertRaises(OSError),
        ):
            broker.request(inputs=self.inputs, packet=self.packet, receipt=self.receipt)

    def test_offline_cli_and_signal_lifecycle(self):
        root = Path(self.inputs["source_pack_root"])
        input_path = root / "cli-inputs.json"
        input_path.write_text(json.dumps(self.inputs))
        with portable_cli_admission(self, ["plan", "--inputs", str(input_path)]):
            for action in ("plan", "preflight"):
                args = [action, "--inputs", str(input_path)]
                if action == "preflight":
                    for flag, value in (
                        ("packet", self.packet),
                        ("receipt", self.receipt),
                    ):
                        path = root / (flag + ".json")
                        path.write_text(json.dumps(value.to_dict()))
                        args += ["--" + flag, str(path)]
                with (
                    patch.object(
                        live,
                        "OpenClawModelClient",
                        side_effect=AssertionError("auth forbidden"),
                    ),
                    redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(cli.run_classification_operator_cli(args), 0)
                self.assertEqual(json.loads(output.getvalue())["provider_calls"], 0)
            with (
                patch.object(cli, "_operate", side_effect=KeyboardInterrupt),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(
                    cli.run_classification_operator_cli(
                        ["plan", "--inputs", str(input_path)]
                    ),
                    3,
                )
            self.assertEqual(
                json.loads(output.getvalue())["publication"], "not-confirmed"
            )
