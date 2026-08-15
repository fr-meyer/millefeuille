"""Offline tests for fail-closed approved-live receipt controls."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.live_receipts import (
    APPROVED_LIVE_AUDIT_SCHEMA_VERSION,
    APPROVED_LIVE_RECEIPT_MAX_BYTES,
    APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
    ApprovedLiveReceipt,
    ApprovedLiveRequest,
    LiveDisposalPolicy,
    LiveProvider,
    LiveSelector,
    LiveTarget,
    ReceiptReplayState,
    build_approved_live_audit_record,
    compute_approved_live_receipt_digest,
    load_approved_live_receipt,
    validate_approved_live_receipt,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError

APPROVED_AT = "2026-08-11T10:00:00Z"
EXPIRES_AT = "2026-08-11T11:00:00Z"
EVALUATION_TIME = datetime(2026, 8, 11, 10, 30, tzinfo=UTC)
RUN_ID = "run-live-receipt"
PAPER_ID = "zotero-ITEM1"
STOP_CONDITIONS = ("approval-expired", "first-error", "scope-drift")
SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"


def _sign(payload: dict[str, object]) -> dict[str, object]:
    payload.pop("integrity", None)
    payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_approved_live_receipt_digest(payload),
    }
    return payload


def _receipt_payload(
    root: Path,
    *,
    run_id: str = RUN_ID,
    target_id: str = PAPER_ID,
    max_items: int = 1,
    operations: tuple[str, ...] = ("zotero.writeback",),
    provider: dict[str, str] | None = None,
    max_provider_calls: int = 0,
    max_cost_usd_micros: int = 0,
    approved_at: str = APPROVED_AT,
    expires_at: str = EXPIRES_AT,
) -> dict[str, object]:
    # Match the contract's lexical absolute-root identity without expanding a
    # Windows 8.3 alias into a different path spelling.
    canonical_root = str(root.absolute())
    payload: dict[str, object] = {
        "schema_version": APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
        "receipt_id": "receipt-mf-100-001",
        "run_id": run_id,
        "approval": {
            "approver_id": "franck.meyer@kaist.ac.kr",
            "approved_at": approved_at,
        },
        "expires_at": expires_at,
        "scope": {
            "operations": list(operations),
            "targets": [{"kind": "paper-id", "id": target_id}],
            "max_items": max_items,
            "selector": {"kind": "paper-id", "value": target_id},
            "output_root": canonical_root,
            "source_pack_root": canonical_root,
            "provider": provider,
            "limits": {
                "max_provider_calls": max_provider_calls,
                "max_cost_usd_micros": max_cost_usd_micros,
            },
            "disposal_policy": {
                "pdfs": "not-applicable",
                "provider_payloads": (
                    "not-applicable" if provider is None else "never-persist"
                ),
                "temporary_files": "not-applicable",
            },
            "stop_conditions": list(STOP_CONDITIONS),
        },
    }
    return _sign(payload)


def _request(
    root: Path,
    *,
    run_id: str = RUN_ID,
    target_id: str = PAPER_ID,
    selector_value: str | None = None,
    operations: tuple[str, ...] = ("zotero.writeback",),
    item_cap: int = 1,
    selected_item_count: int = 1,
    output_root: Path | None = None,
    source_pack_root: Path | None = None,
    provider: LiveProvider | None = None,
    provider_call_limit: int = 0,
    cost_limit_usd_micros: int = 0,
) -> ApprovedLiveRequest:
    return ApprovedLiveRequest(
        run_id=run_id,
        operations=operations,
        targets=(LiveTarget(kind="paper-id", id=target_id),),
        item_cap=item_cap,
        selected_item_count=selected_item_count,
        selector=LiveSelector(
            kind="paper-id",
            value=target_id if selector_value is None else selector_value,
        ),
        output_root=str((output_root or root).absolute()),
        source_pack_root=str((source_pack_root or root).absolute()),
        provider=provider,
        provider_call_limit=provider_call_limit,
        cost_limit_usd_micros=cost_limit_usd_micros,
        disposal_policy=LiveDisposalPolicy(
            pdfs="not-applicable",
            provider_payloads=(
                "not-applicable" if provider is None else "never-persist"
            ),
            temporary_files="not-applicable",
        ),
        stop_conditions=STOP_CONDITIONS,
    )


def _write_receipt(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _current_window() -> tuple[str, str]:
    now = datetime.now(UTC).replace(microsecond=0)
    approved_at = now - timedelta(minutes=1)
    expires_at = now + timedelta(hours=1)
    return (
        approved_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


class TestApprovedLiveReceiptModel(unittest.TestCase):
    def test_schema_and_runtime_accept_exact_bounded_receipt(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            payload = _receipt_payload(root)
            schema_path = SPEC_DIR / "approved-live-receipt.schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(payload)

            receipt_path = root / "approval.json"
            _write_receipt(receipt_path, payload)
            receipt = load_approved_live_receipt(receipt_path)
            request = _request(root)

            validate_approved_live_receipt(
                receipt,
                request,
                now=EVALUATION_TIME,
            )
            self.assertEqual(receipt.to_dict(), payload)
            self.assertEqual(
                receipt.content_digest,
                compute_approved_live_receipt_digest(payload),
            )

    def test_tampered_receipt_is_rejected_by_content_identity(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            payload = _receipt_payload(root)
            payload["run_id"] = "tampered-run"
            receipt_path = root / "tampered.json"
            _write_receipt(receipt_path, payload)

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "content_digest mismatch",
            ):
                load_approved_live_receipt(receipt_path)

    def test_direct_construction_cannot_bypass_content_identity(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            receipt = ApprovedLiveReceipt.from_dict(_receipt_payload(root))
            forged_digest = "sha256:" + ("0" * 64)
            if forged_digest == receipt.content_digest:
                forged_digest = "sha256:" + ("1" * 64)

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "content_digest mismatch",
            ):
                ApprovedLiveReceipt(
                    receipt_id=receipt.receipt_id,
                    run_id=receipt.run_id,
                    approval=receipt.approval,
                    expires_at=receipt.expires_at,
                    scope=receipt.scope,
                    content_digest=forged_digest,
                )

    def test_loader_accepts_exact_size_limit_and_rejects_one_byte_over(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            payload = _receipt_payload(root)
            encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.assertLess(len(encoded), APPROVED_LIVE_RECEIPT_MAX_BYTES)
            padding = b" " * (APPROVED_LIVE_RECEIPT_MAX_BYTES - len(encoded))
            receipt_path = root / "bounded.json"
            receipt_path.write_bytes(encoded + padding)

            receipt = load_approved_live_receipt(receipt_path)

            self.assertEqual(receipt.to_dict(), payload)
            receipt_path.write_bytes(encoded + padding + b" ")
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "exceeds 65536 bytes",
            ):
                load_approved_live_receipt(receipt_path)

    def test_expired_and_not_yet_active_receipts_are_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            receipt = ApprovedLiveReceipt.from_dict(_receipt_payload(root))
            request = _request(root)

            with self.assertRaisesRegex(MillefeuilleContractError, "expired"):
                validate_approved_live_receipt(
                    receipt,
                    request,
                    now=datetime(2026, 8, 11, 11, 0, tzinfo=UTC),
                )
            with self.assertRaisesRegex(MillefeuilleContractError, "not active"):
                validate_approved_live_receipt(
                    receipt,
                    request,
                    now=datetime(2026, 8, 11, 9, 59, tzinfo=UTC),
                )

    def test_validity_window_cannot_be_over_broad(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            payload = _receipt_payload(
                root,
                expires_at="2026-08-12T10:00:01Z",
            )
            with self.assertRaisesRegex(MillefeuilleContractError, "24 hours"):
                ApprovedLiveReceipt.from_dict(payload)

    def test_consumed_id_or_digest_rejects_replay(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            receipt = ApprovedLiveReceipt.from_dict(_receipt_payload(root))
            request = _request(root)

            with self.assertRaisesRegex(MillefeuilleContractError, "consumed"):
                validate_approved_live_receipt(
                    receipt,
                    request,
                    now=EVALUATION_TIME,
                    replay_state=ReceiptReplayState(
                        receipt_ids=frozenset({receipt.receipt_id})
                    ),
                )
            with self.assertRaisesRegex(MillefeuilleContractError, "consumed"):
                validate_approved_live_receipt(
                    receipt,
                    request,
                    now=EVALUATION_TIME,
                    replay_state=ReceiptReplayState(
                        content_digests=frozenset({receipt.content_digest})
                    ),
                )

    def test_scope_drift_rejects_over_broad_wrong_target_and_wrong_run(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            over_broad = ApprovedLiveReceipt.from_dict(
                _receipt_payload(root, max_items=10)
            )
            exact = ApprovedLiveReceipt.from_dict(_receipt_payload(root))
            cases = (
                (over_broad, _request(root), "max_items drift"),
                (exact, _request(root, target_id="zotero-ITEM2"), "targets drift"),
                (
                    exact,
                    _request(root, selector_value="zotero-ITEM2"),
                    "selector drift",
                ),
                (exact, _request(root, run_id="other-run"), "run_id drift"),
                (
                    exact,
                    _request(root, operations=("zotero.write-tag", "zotero.writeback")),
                    "operations drift",
                ),
            )
            for receipt, request, message in cases:
                with (
                    self.subTest(message=message),
                    self.assertRaisesRegex(MillefeuilleContractError, message),
                ):
                    validate_approved_live_receipt(
                        receipt,
                        request,
                        now=EVALUATION_TIME,
                    )

    def test_wrong_output_and_source_pack_roots_are_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            wrong = root / "wrong"
            wrong.mkdir()
            receipt = ApprovedLiveReceipt.from_dict(_receipt_payload(root))

            for request, message in (
                (_request(root, output_root=wrong), "output_root drift"),
                (_request(root, source_pack_root=wrong), "source_pack_root drift"),
            ):
                with (
                    self.subTest(message=message),
                    self.assertRaisesRegex(MillefeuilleContractError, message),
                ):
                    validate_approved_live_receipt(
                        receipt,
                        request,
                        now=EVALUATION_TIME,
                    )

    def test_provider_model_and_budget_are_exact(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            payload = _receipt_payload(
                root,
                operations=("model.summarize",),
                provider={"provider_id": "openai", "model_id": "gpt-5.6-sol"},
                max_provider_calls=3,
                max_cost_usd_micros=2_000_000,
            )
            receipt = ApprovedLiveReceipt.from_dict(payload)
            correct = _request(
                root,
                operations=("model.summarize",),
                provider=LiveProvider("openai", "gpt-5.6-sol"),
                provider_call_limit=3,
                cost_limit_usd_micros=2_000_000,
            )
            validate_approved_live_receipt(
                receipt,
                correct,
                now=EVALUATION_TIME,
            )

            for request, message in (
                (
                    _request(
                        root,
                        operations=("model.summarize",),
                        provider=LiveProvider("openai", "other-model"),
                        provider_call_limit=3,
                        cost_limit_usd_micros=2_000_000,
                    ),
                    "provider drift",
                ),
                (
                    _request(
                        root,
                        operations=("model.summarize",),
                        provider=LiveProvider("openai", "gpt-5.6-sol"),
                        provider_call_limit=4,
                        cost_limit_usd_micros=2_000_000,
                    ),
                    "max_provider_calls drift",
                ),
                (
                    _request(
                        root,
                        operations=("model.summarize",),
                        provider=LiveProvider("openai", "gpt-5.6-sol"),
                        provider_call_limit=3,
                        cost_limit_usd_micros=2_000_001,
                    ),
                    "max_cost_usd_micros drift",
                ),
            ):
                with (
                    self.subTest(message=message),
                    self.assertRaisesRegex(MillefeuilleContractError, message),
                ):
                    validate_approved_live_receipt(
                        receipt,
                        request,
                        now=EVALUATION_TIME,
                    )

    def test_provider_operations_cannot_omit_provider_and_model(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            payload = _receipt_payload(root, operations=("model.summarize",))

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "exact provider and model",
            ):
                ApprovedLiveReceipt.from_dict(payload)

    def test_wildcard_and_secret_like_input_are_rejected_without_reflection(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            wildcard = _receipt_payload(root)
            wildcard["scope"]["selector"]["value"] = "*"  # type: ignore[index]
            _sign(wildcard)
            with self.assertRaisesRegex(MillefeuilleContractError, "exact value"):
                ApprovedLiveReceipt.from_dict(wildcard)

            secrets = (
                ("ghp_" + ("a" * 24), "approver"),
                ("https://example.test/?access_token=do-not-persist", "selector"),
            )
            for secret, field in secrets:
                payload = _receipt_payload(root)
                if field == "approver":
                    payload["approval"]["approver_id"] = secret  # type: ignore[index]
                else:
                    payload["scope"]["selector"]["value"] = secret  # type: ignore[index]
                    payload["scope"]["targets"][0]["id"] = secret  # type: ignore[index]
                _sign(payload)
                with self.subTest(field=field):
                    with self.assertRaises(MillefeuilleContractError) as raised:
                        ApprovedLiveReceipt.from_dict(payload)
                    self.assertNotIn(secret, str(raised.exception))

            forbidden_field = _receipt_payload(root)
            forbidden_field["api_key"] = "do-not-persist"
            _sign(forbidden_field)
            with self.assertRaisesRegex(MillefeuilleContractError, "forbidden"):
                ApprovedLiveReceipt.from_dict(forbidden_field)

    def test_loader_rejects_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            payload = _receipt_payload(root)
            encoded = json.dumps(payload, sort_keys=True)
            duplicate = encoded.replace(
                '"receipt_id":',
                '"receipt_id":"duplicate","receipt_id":',
                1,
            )
            receipt_path = root / "duplicate.json"
            receipt_path.write_text(duplicate, encoding="utf-8")

            with self.assertRaisesRegex(MillefeuilleContractError, "duplicate"):
                load_approved_live_receipt(receipt_path)

    def test_audit_serialization_is_allowlisted_and_drives_replay(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            receipt = ApprovedLiveReceipt.from_dict(_receipt_payload(root))
            request = _request(root)
            validated = build_approved_live_audit_record(
                receipt,
                request,
                evaluated_at=EVALUATION_TIME,
            )

            self.assertEqual(
                validated["schema_version"],
                APPROVED_LIVE_AUDIT_SCHEMA_VERSION,
            )
            self.assertEqual(validated["status"], "validated")
            self.assertFalse(validated["secret_material_persisted"])
            receipt_schema = json.loads(
                (SPEC_DIR / "approved-live-receipt.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            audit_schema = json.loads(
                (SPEC_DIR / "approved-live-audit.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            registry = Registry().with_resource(
                receipt_schema["$id"],
                Resource.from_contents(receipt_schema),
            )
            Draft202012Validator(audit_schema, registry=registry).validate(validated)
            encoded = json.dumps(validated, sort_keys=True)
            for forbidden in ("api_key", "authorization", "raw_response"):
                self.assertNotIn(forbidden, encoded.casefold())
            self.assertEqual(
                ReceiptReplayState.from_audit_records([validated]),
                ReceiptReplayState(),
            )

            with self.assertRaisesRegex(MillefeuilleContractError, "run_id drift"):
                build_approved_live_audit_record(
                    receipt,
                    _request(root, run_id="other-run"),
                    evaluated_at=EVALUATION_TIME,
                )

            consumed = deepcopy(validated)
            consumed["status"] = "consumed"
            replay = ReceiptReplayState.from_audit_records([consumed])
            self.assertIn(receipt.receipt_id, replay.receipt_ids)
            self.assertIn(receipt.content_digest, replay.content_digests)

            tampered = deepcopy(consumed)
            tampered["scope"]["max_items"] = 2
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "receipt_digest mismatch",
            ):
                ReceiptReplayState.from_audit_records([tampered])

            unsafe = deepcopy(consumed)
            unsafe["secret_material_persisted"] = True
            with self.assertRaisesRegex(MillefeuilleContractError, "secret material"):
                ReceiptReplayState.from_audit_records([unsafe])

    @unittest.skipUnless(os.name == "nt", "Windows root identity regression")
    def test_audit_digest_roundtrips_equivalent_windows_root_case(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            receipt = ApprovedLiveReceipt.from_dict(_receipt_payload(root))
            canonical_request = _request(root)
            case_variant = str(root.absolute()).swapcase()
            request = replace(
                canonical_request,
                output_root=case_variant,
                source_pack_root=case_variant,
            )

            canonical_record = build_approved_live_audit_record(
                receipt,
                canonical_request,
                evaluated_at=EVALUATION_TIME,
                status="consumed",
            )
            variant_record = build_approved_live_audit_record(
                receipt,
                request,
                evaluated_at=EVALUATION_TIME,
                status="consumed",
            )

            self.assertEqual(
                variant_record["request_digest"],
                canonical_record["request_digest"],
            )
            replay = ReceiptReplayState.from_audit_records([variant_record])
            self.assertIn(receipt.receipt_id, replay.receipt_ids)
            self.assertIn(receipt.content_digest, replay.content_digests)


class TestApprovedLiveReceiptCliGate(unittest.TestCase):
    def _live_receipt(self, root: Path, **overrides: object) -> dict[str, object]:
        approved_at, expires_at = _current_window()
        return _receipt_payload(
            root,
            approved_at=approved_at,
            expires_at=expires_at,
            **overrides,
        )

    def _args(self, root: Path, receipt_path: Path) -> list[str]:
        return [
            "writeback",
            "--source-pack-root",
            str(root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            RUN_ID,
            "--mode",
            "approved-live",
            "--approval-receipt",
            str(receipt_path),
            "--writeback",
            "approved-live",
            "--approved-live-pdf-disposal",
            "not-applicable",
            "--approved-live-provider-payload-disposal",
            "not-applicable",
            "--approved-live-temporary-file-disposal",
            "not-applicable",
            "--approved-live-stop-condition",
            "approval-expired",
            "--approved-live-stop-condition",
            "first-error",
            "--approved-live-stop-condition",
            "scope-drift",
        ]

    def test_valid_receipt_still_stops_at_unsupported_live_gate(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            payload = self._live_receipt(root)
            receipt_path = root / "approval.json"
            _write_receipt(receipt_path, payload)
            stderr = StringIO()

            exit_code = run_stage_cli(self._args(root, receipt_path), stderr=stderr)

            self.assertEqual(exit_code, 3)
            self.assertIn(payload["integrity"]["content_digest"], stderr.getvalue())
            self.assertIn("not implemented", stderr.getvalue())

    def test_receipt_cannot_promote_preview_mode(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            receipt_path = root / "approval.json"
            _write_receipt(receipt_path, self._live_receipt(root))
            args = self._args(root, receipt_path)
            mode_index = args.index("approved-live")
            args[mode_index] = "preview"
            stderr = StringIO()

            exit_code = run_stage_cli(args, stderr=stderr)

            self.assertEqual(exit_code, 3)
            self.assertIn("cannot promote preview", stderr.getvalue())

    def test_cli_rejects_wrong_run_and_over_broad_cap(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            cases = (
                (self._live_receipt(root, run_id="other-run"), "run_id drift"),
                (self._live_receipt(root, max_items=2), "max_items drift"),
            )
            for index, (payload, message) in enumerate(cases):
                receipt_path = root / f"approval-{index}.json"
                _write_receipt(receipt_path, payload)
                stderr = StringIO()

                exit_code = run_stage_cli(
                    self._args(root, receipt_path),
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 3)
                self.assertIn(message, stderr.getvalue())

    def test_cli_derives_disposal_and_stop_policy_independently(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            disposal_drift = self._live_receipt(root)
            disposal_drift["scope"]["disposal_policy"]["pdfs"] = (
                "retain-until-expiry"
            )
            _sign(disposal_drift)
            stop_drift = self._live_receipt(root)
            stop_drift["scope"]["stop_conditions"] = [
                "approval-expired",
                "scope-drift",
            ]
            _sign(stop_drift)
            cases = (
                (disposal_drift, "disposal_policy drift"),
                (stop_drift, "stop_conditions drift"),
            )
            for index, (payload, message) in enumerate(cases):
                receipt_path = root / f"policy-drift-{index}.json"
                _write_receipt(receipt_path, payload)
                stderr = StringIO()

                exit_code = run_stage_cli(
                    self._args(root, receipt_path),
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 3)
                self.assertIn(message, stderr.getvalue())

    def test_cli_requires_independent_disposal_and_stop_controls(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            receipt_path = root / "approval.json"
            _write_receipt(receipt_path, self._live_receipt(root))
            cases = (
                ("--approved-live-pdf-disposal", "explicit disposal controls"),
                ("--approved-live-stop-condition", "explicit stop conditions"),
            )
            for flag, message in cases:
                args = self._args(root, receipt_path)
                flag_index = args.index(flag)
                del args[flag_index : flag_index + 2]
                if flag == "--approved-live-stop-condition":
                    while flag in args:
                        flag_index = args.index(flag)
                        del args[flag_index : flag_index + 2]
                stderr = StringIO()

                exit_code = run_stage_cli(args, stderr=stderr)

                self.assertEqual(exit_code, 3)
                self.assertIn(message, stderr.getvalue())

    def test_cli_binds_distinct_artifact_output_root(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_root = Path(tempdir) / "source"
            artifact_root = Path(tempdir) / "artifacts"
            source_root.mkdir()
            artifact_root.mkdir()
            payload = self._live_receipt(source_root)
            payload["scope"]["output_root"] = str(artifact_root.absolute())
            _sign(payload)
            receipt_path = source_root / "approval.json"
            _write_receipt(receipt_path, payload)
            args = self._args(source_root, receipt_path)
            args.extend(("--artifact-root", str(artifact_root)))
            stderr = StringIO()

            exit_code = run_stage_cli(args, stderr=stderr)

            self.assertEqual(exit_code, 3)
            self.assertIn(payload["integrity"]["content_digest"], stderr.getvalue())
            self.assertIn("not implemented", stderr.getvalue())

            wrong_payload = self._live_receipt(source_root)
            wrong_path = source_root / "wrong-output-root.json"
            _write_receipt(wrong_path, wrong_payload)
            wrong_args = self._args(source_root, wrong_path)
            wrong_args.extend(("--artifact-root", str(artifact_root)))
            wrong_stderr = StringIO()

            exit_code = run_stage_cli(wrong_args, stderr=wrong_stderr)

            self.assertEqual(exit_code, 3)
            self.assertIn("output_root drift", wrong_stderr.getvalue())

    def test_live_writeback_flag_requires_explicit_live_mode(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            receipt_path = root / "approval.json"
            _write_receipt(receipt_path, self._live_receipt(root))
            stderr = StringIO()

            exit_code = run_stage_cli(
                [
                    "writeback",
                    "--source-pack-root",
                    str(root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                    "--writeback",
                    "approved-live",
                    "--approval-receipt",
                    str(receipt_path),
                ],
                stderr=stderr,
            )

            self.assertEqual(exit_code, 3)
            self.assertIn("explicit --mode approved-live", stderr.getvalue())

    def test_live_mode_requires_explicit_live_writeback_flag(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            receipt_path = root / "approval.json"
            _write_receipt(receipt_path, self._live_receipt(root))
            args = self._args(root, receipt_path)
            writeback_index = args.index("approved-live", args.index("--writeback"))
            args[writeback_index] = "preview"
            stderr = StringIO()

            exit_code = run_stage_cli(args, stderr=stderr)

            self.assertEqual(exit_code, 3)
            self.assertIn("explicit --writeback approved-live", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
