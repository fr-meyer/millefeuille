"""Offline tests for the fail-closed MF-102 operator preflight."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from millefeuille.cli.operator_preflight import run_operator_preflight_cli
from millefeuille.domain.live_receipts import (
    APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
    ApprovedLiveReceipt,
    ReceiptReplayState,
    compute_approved_live_receipt_digest,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import (
    OPERATOR_PREFLIGHT_PACKET_MAX_BYTES,
    OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION,
    OPERATOR_PREFLIGHT_RESULT_MAX_BYTES,
    OPERATOR_PREFLIGHT_RESULT_SCHEMA_VERSION,
    PREFLIGHT_SCOPE_TARGET_KIND,
    SUPPORTED_OPERATOR_OPERATIONS,
    OperatorPreflightPacket,
    OperatorPreflightResult,
    compute_operator_authorization_context_digest,
    compute_operator_preflight_packet_digest,
    compute_operator_preflight_result_digest,
    compute_operator_root_target_id,
    evaluate_operator_preflight,
    load_operator_preflight_packet,
    load_operator_preflight_result,
    render_operator_preflight_markdown,
    validate_operator_preflight_result,
)

APPROVED_AT = "2026-08-11T10:00:00Z"
EXPIRES_AT = "2026-08-11T11:00:00Z"
EVALUATION_TIME = datetime(2026, 8, 11, 10, 30, tzinfo=UTC)
SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"
TEST_ENV_REFS = {
    "model-oauth": "MILLEFEUILLE_CREDENTIAL_MODEL_OAUTH",
    "openkb-write": "MILLEFEUILLE_CREDENTIAL_OPENKB_WRITE",
    "zotero-library-id": "MILLEFEUILLE_CREDENTIAL_ZOTERO_LIBRARY_ID",
    "zotero-read": "MILLEFEUILLE_CREDENTIAL_ZOTERO_READ",
    "zotero-write": "MILLEFEUILLE_CREDENTIAL_ZOTERO_WRITE",
}


def _sign_packet(payload: dict[str, object]) -> dict[str, object]:
    payload.pop("integrity", None)
    payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_operator_preflight_packet_digest(payload),
    }
    return payload


def _sign_receipt(payload: dict[str, object]) -> dict[str, object]:
    payload.pop("integrity", None)
    payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_approved_live_receipt_digest(payload),
    }
    return payload


def _sign_result(payload: dict[str, object]) -> dict[str, object]:
    payload.pop("integrity", None)
    payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_operator_preflight_result_digest(payload),
    }
    return payload


def _credential_rows(*credential_types: str) -> list[dict[str, str]]:
    return [
        {"type": credential_type, "reference": TEST_ENV_REFS[credential_type]}
        for credential_type in sorted(credential_types)
    ]


def _preview_packet_payload(root: Path) -> dict[str, object]:
    canonical_root = str(root.resolve())
    return _sign_packet(
        {
            "schema_version": OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION,
            "packet_id": "packet-mf-102-preview",
            "mode": "preview",
            "source": {
                "adapter": "local-fixture",
                "selector": {"kind": "paper-id", "value": "paper-001"},
                "item_cap": 1,
                "resolved_item_count": 1,
            },
            "run_id": "run-mf-102-preview",
            "roots": {
                "artifact_root": canonical_root,
                "source_pack_root": canonical_root,
            },
            "operations": ["status.inspect"],
            "targets": [{"kind": "paper-id", "id": "paper-001"}],
            "destinations": [{"kind": "paper-id", "id": "paper-001"}],
            "provider": None,
            "limits": {
                "max_provider_calls": 0,
                "max_cost_usd_micros": 0,
            },
            "disposal_policy": {
                "pdfs": "not-applicable",
                "provider_payloads": "not-applicable",
                "temporary_files": "not-applicable",
            },
            "stop_conditions": ["first-error", "scope-drift"],
            "rollback_actions": ["no-effect"],
            "acceptance_status": "not-applicable",
            "credential_requirements": [],
            "approval_receipt": None,
        }
    )


def _read_only_packet_payload(root: Path) -> dict[str, object]:
    payload = _preview_packet_payload(root)
    payload["packet_id"] = "packet-mf-102-read-only"
    payload["mode"] = "read-only-live"
    payload["source"] = {
        "adapter": "zotero",
        "selector": {"kind": "zotero-tag", "value": "mf-staging"},
        "item_cap": 2,
        "resolved_item_count": 2,
    }
    payload["operations"] = ["zotero.discover"]
    payload["targets"] = [{"kind": "zotero-tag", "id": "mf-staging"}]
    payload["destinations"] = [{"kind": "zotero-tag", "id": "mf-staging"}]
    payload["credential_requirements"] = _credential_rows(
        "zotero-library-id",
        "zotero-read",
    )
    return _sign_packet(payload)


def _approved_pair(
    root: Path,
    *,
    packet_root: str | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    receipt_root = str(root.resolve())
    requested_root = packet_root or receipt_root
    artifact_root_target = compute_operator_root_target_id(requested_root)
    packet: dict[str, object] = {
        "schema_version": OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION,
        "packet_id": "packet-mf-102-approved",
        "mode": "approved-live",
        "source": {
            "adapter": "zotero",
            "selector": {"kind": "zotero-tag", "value": "mf-staging"},
            "item_cap": 2,
            "resolved_item_count": 2,
        },
        "run_id": "run-mf-102-approved",
        "roots": {
            "artifact_root": requested_root,
            "source_pack_root": requested_root,
        },
        "operations": ["model.summarize", "openkb.write"],
        "targets": [
            {"kind": "artifact-root", "id": artifact_root_target},
            {"kind": "openkb-collection", "id": "research-secondary"},
            {"kind": "openkb-collection", "id": "research-staging"},
            {"kind": "paper-id", "id": "paper-001"},
        ],
        "destinations": [
            {"kind": "artifact-root", "id": artifact_root_target},
            {"kind": "openkb-collection", "id": "research-staging"},
        ],
        "provider": {
            "provider_id": "openai",
            "model_id": "gpt-5.6-sol",
            "profile_id": "research-default",
        },
        "limits": {
            "max_provider_calls": 4,
            "max_cost_usd_micros": 250000,
        },
        "disposal_policy": {
            "pdfs": "delete-after-run",
            "provider_payloads": "never-persist",
            "temporary_files": "delete-on-failure",
        },
        "stop_conditions": ["approval-expired", "first-error", "scope-drift"],
        "rollback_actions": ["delete-temporary-files", "revert-openkb-write"],
        "acceptance_status": "pass",
        "credential_requirements": _credential_rows(
            "model-oauth",
            "openkb-write",
            "zotero-library-id",
            "zotero-read",
        ),
        "approval_receipt": None,
    }
    context_digest = compute_operator_authorization_context_digest(packet)
    packet["targets"] = sorted(
        [
            *packet["targets"],  # type: ignore[list-item]
            {"kind": PREFLIGHT_SCOPE_TARGET_KIND, "id": context_digest},
        ],
        key=lambda target: (target["kind"], target["id"]),
    )
    receipt: dict[str, object] = {
        "schema_version": APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
        "receipt_id": "receipt-mf-102-approved",
        "run_id": packet["run_id"],
        "approval": {
            "approver_id": "franck.meyer@kaist.ac.kr",
            "approved_at": APPROVED_AT,
        },
        "expires_at": EXPIRES_AT,
        "scope": {
            "operations": deepcopy(packet["operations"]),
            "targets": deepcopy(packet["targets"]),
            "max_items": packet["source"]["item_cap"],  # type: ignore[index]
            "selector": deepcopy(packet["source"]["selector"]),  # type: ignore[index]
            "output_root": receipt_root,
            "source_pack_root": receipt_root,
            "provider": {
                "provider_id": packet["provider"]["provider_id"],  # type: ignore[index]
                "model_id": packet["provider"]["model_id"],  # type: ignore[index]
            },
            "limits": deepcopy(packet["limits"]),
            "disposal_policy": deepcopy(packet["disposal_policy"]),
            "stop_conditions": deepcopy(packet["stop_conditions"]),
        },
    }
    _sign_receipt(receipt)
    packet["approval_receipt"] = {
        "receipt_id": receipt["receipt_id"],
        "content_digest": receipt["integrity"]["content_digest"],  # type: ignore[index]
    }
    _sign_packet(packet)
    return packet, receipt


def _refresh_context_and_sign(payload: dict[str, object]) -> dict[str, object]:
    targets = [
        target
        for target in payload["targets"]  # type: ignore[union-attr]
        if target["kind"] != PREFLIGHT_SCOPE_TARGET_KIND
    ]
    payload["targets"] = targets
    context_digest = compute_operator_authorization_context_digest(payload)
    targets.append({"kind": PREFLIGHT_SCOPE_TARGET_KIND, "id": context_digest})
    payload["targets"] = sorted(
        targets,
        key=lambda target: (target["kind"], target["id"]),
    )
    return _sign_packet(payload)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class TestOperatorPreflightContracts(unittest.TestCase):
    def test_preview_packet_and_result_match_strict_schemas(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet_payload = _preview_packet_payload(root)
            packet_schema = json.loads(
                (SPEC_DIR / "operator-preflight-packet.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            result_schema = json.loads(
                (SPEC_DIR / "operator-preflight-result.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator.check_schema(packet_schema)
            Draft202012Validator.check_schema(result_schema)
            Draft202012Validator(packet_schema).validate(packet_payload)
            self.assertEqual(
                set(packet_schema["$defs"]["operationArray"]["items"]["enum"]),
                SUPPORTED_OPERATOR_OPERATIONS,
            )

            packet = OperatorPreflightPacket.from_dict(packet_payload)
            result = evaluate_operator_preflight(
                packet,
                explicit_mode="preview",
                environment={},
            )

            self.assertEqual(result.decision, "ready-for-preview")
            self.assertFalse(result.to_dict()["external_effects_performed"])
            self.assertFalse(result.to_dict()["secret_material_persisted"])
            Draft202012Validator(result_schema).validate(result.to_dict())
            validate_operator_preflight_result(result, packet)

    def test_packet_loader_is_bounded_and_accepts_exact_boundary(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            payload = _preview_packet_payload(root)
            encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.assertLess(len(encoded), OPERATOR_PREFLIGHT_PACKET_MAX_BYTES)
            padding = b" " * (OPERATOR_PREFLIGHT_PACKET_MAX_BYTES - len(encoded))
            packet_path = root / "packet.json"
            packet_path.write_bytes(encoded + padding)

            packet = load_operator_preflight_packet(packet_path)

            self.assertEqual(packet.to_dict(), payload)
            packet_path.write_bytes(encoded + padding + b" ")
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "exceeds 65536 bytes",
            ):
                load_operator_preflight_packet(packet_path)

    def test_packet_loader_passes_preallocation_bound_to_stable_reader(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            payload = _preview_packet_payload(root)
            encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
            with patch(
                "millefeuille.domain.operator_preflight.read_bytes_no_follow",
                return_value=encoded,
            ) as reader:
                packet = load_operator_preflight_packet(root / "packet.json")

            self.assertEqual(packet.packet_id, "packet-mf-102-preview")
            self.assertEqual(
                reader.call_args.kwargs["max_bytes"],
                OPERATOR_PREFLIGHT_PACKET_MAX_BYTES,
            )

    def test_duplicate_unknown_and_nonfinite_json_are_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet_path = root / "packet.json"
            packet_path.write_text('{"schema_version":"a","schema_version":"b"}')
            with self.assertRaisesRegex(MillefeuilleContractError, "duplicate"):
                load_operator_preflight_packet(packet_path)

            payload = _preview_packet_payload(root)
            payload["unknown"] = "field"
            with self.assertRaisesRegex(MillefeuilleContractError, "unknown fields"):
                OperatorPreflightPacket.from_dict(payload)

            packet_path.write_text('{"value":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(MillefeuilleContractError, "non-finite"):
                load_operator_preflight_packet(packet_path)

    def test_wildcard_traversal_secret_control_and_non_nfc_fail_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            base = _preview_packet_payload(root)
            cases: list[tuple[str, dict[str, object], str]] = []

            wildcard = deepcopy(base)
            wildcard["operations"] = ["status.*"]
            cases.append(("wildcard", _sign_packet(wildcard), "over-broad"))

            traversal = deepcopy(base)
            traversal["roots"]["artifact_root"] = str(root / "x" / ".." / "y")  # type: ignore[index]
            cases.append(("traversal", _sign_packet(traversal), "traversal"))

            control = deepcopy(base)
            control["source"]["selector"]["value"] = "paper\n001"  # type: ignore[index]
            cases.append(("control", _sign_packet(control), "control"))

            non_nfc = deepcopy(base)
            non_nfc["source"]["selector"]["value"] = "cafe\u0301"  # type: ignore[index]
            cases.append(("non-nfc", _sign_packet(non_nfc), "NFC"))

            for name, payload, pattern in cases:
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(
                        MillefeuilleContractError,
                        pattern,
                    ),
                ):
                    OperatorPreflightPacket.from_dict(payload)

            secret_value = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX"
            secret = deepcopy(base)
            secret["source"]["selector"]["value"] = secret_value  # type: ignore[index]
            with self.assertRaises(MillefeuilleContractError) as raised:
                OperatorPreflightPacket.from_dict(secret)
            self.assertNotIn(secret_value, str(raised.exception))

    def test_count_destination_disposal_and_acceptance_invariants(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            cases: list[tuple[str, dict[str, object], str]] = []

            count = _preview_packet_payload(root)
            count["source"]["resolved_item_count"] = 2  # type: ignore[index]
            cases.append(("count", _sign_packet(count), "exceeds"))

            destination = _preview_packet_payload(root)
            destination["destinations"] = [{"kind": "paper-id", "id": "paper-002"}]
            cases.append(("destination", _sign_packet(destination), "destination"))

            disposal = _preview_packet_payload(root)
            del disposal["disposal_policy"]["pdfs"]  # type: ignore[index]
            cases.append(("disposal", _sign_packet(disposal), "missing required"))

            classify = _preview_packet_payload(root)
            classify["operations"] = ["model.classify"]
            classify["provider"] = {
                "provider_id": "openai",
                "model_id": "gpt-5.6-sol",
                "profile_id": "research-default",
            }
            classify["limits"] = {
                "max_provider_calls": 1,
                "max_cost_usd_micros": 1,
            }
            classify["disposal_policy"]["provider_payloads"] = "never-persist"  # type: ignore[index]
            classify["targets"] = [
                {
                    "kind": "artifact-root",
                    "id": compute_operator_root_target_id(str(root.resolve())),
                }
            ]
            classify["destinations"] = deepcopy(classify["targets"])
            classify["credential_requirements"] = _credential_rows("model-oauth")
            cases.append(("acceptance", _sign_packet(classify), "acceptance"))

            for name, payload, pattern in cases:
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(
                        MillefeuilleContractError,
                        pattern,
                    ),
                ):
                    OperatorPreflightPacket.from_dict(payload)

    def test_direct_construction_cannot_forge_packet_digest_or_nested_types(self):
        with tempfile.TemporaryDirectory() as tempdir:
            packet = OperatorPreflightPacket.from_dict(
                _preview_packet_payload(Path(tempdir))
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "content_digest mismatch",
            ):
                replace(packet, content_digest="sha256:" + ("0" * 64))
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "exact OperatorSource",
            ):
                replace(packet, source={})  # type: ignore[arg-type]

    def test_packet_tamper_and_unallowlisted_credential_reference_are_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            tampered = _preview_packet_payload(root)
            tampered["run_id"] = "run-tampered"
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "content_digest mismatch",
            ):
                OperatorPreflightPacket.from_dict(tampered)

            unallowlisted = _read_only_packet_payload(root)
            unallowlisted["credential_requirements"][0]["reference"] = (  # type: ignore[index]
                "UNAPPROVED_SYNTHETIC_ENV"
            )
            _sign_packet(unallowlisted)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "not allowlisted",
            ):
                OperatorPreflightPacket.from_dict(unallowlisted)

    def test_operation_vocabulary_rejects_unknown_aliases_and_prefix_variants(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            for operation in (
                "filesystem.erase",
                "classification.classify",
                "model.classify.batch",
                "prefix.model.classify",
            ):
                with self.subTest(operation=operation):
                    payload = _preview_packet_payload(root)
                    payload["operations"] = [operation]
                    _sign_packet(payload)
                    with self.assertRaisesRegex(
                        MillefeuilleContractError,
                        "unsupported operation",
                    ):
                        OperatorPreflightPacket.from_dict(payload)

    def test_every_supported_classification_variant_requires_acceptance(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            canonical_root = str(root.resolve())
            root_target_id = compute_operator_root_target_id(canonical_root)
            for operation in (
                "classification.adjudicate",
                "classification.review",
                "model.classify",
            ):
                with self.subTest(operation=operation):
                    payload = _preview_packet_payload(root)
                    payload["operations"] = [operation]
                    payload["targets"] = [
                        {"kind": "artifact-root", "id": root_target_id}
                    ]
                    payload["destinations"] = deepcopy(payload["targets"])
                    if operation == "model.classify":
                        payload["provider"] = {
                            "provider_id": "openai",
                            "model_id": "gpt-5.6-sol",
                            "profile_id": "research-default",
                        }
                        payload["limits"] = {
                            "max_provider_calls": 1,
                            "max_cost_usd_micros": 1,
                        }
                        payload["disposal_policy"]["provider_payloads"] = (  # type: ignore[index]
                            "never-persist"
                        )
                        payload["credential_requirements"] = _credential_rows(
                            "model-oauth"
                        )
                    _sign_packet(payload)
                    with self.assertRaisesRegex(
                        MillefeuilleContractError,
                        "acceptance_status pass",
                    ):
                        OperatorPreflightPacket.from_dict(payload)

    def test_operation_matrix_binds_provider_credentials_and_destinations(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)

            provider_destination_drift = _preview_packet_payload(root)
            provider_destination_drift["operations"] = ["model.summarize"]
            provider_destination_drift["provider"] = {
                "provider_id": "openai",
                "model_id": "gpt-5.6-sol",
                "profile_id": "research-default",
            }
            provider_destination_drift["limits"] = {
                "max_provider_calls": 1,
                "max_cost_usd_micros": 1,
            }
            provider_destination_drift["disposal_policy"][  # type: ignore[index]
                "provider_payloads"
            ] = "never-persist"
            provider_destination_drift["credential_requirements"] = _credential_rows(
                "model-oauth"
            )
            _sign_packet(provider_destination_drift)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "required exact destination role",
            ):
                OperatorPreflightPacket.from_dict(provider_destination_drift)

            missing_write_credential = _preview_packet_payload(root)
            missing_write_credential["operations"] = ["openkb.write"]
            missing_write_credential["targets"] = [
                {"kind": "openkb-collection", "id": "research-staging"}
            ]
            missing_write_credential["destinations"] = deepcopy(
                missing_write_credential["targets"]
            )
            _sign_packet(missing_write_credential)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "credential requirements drift",
            ):
                OperatorPreflightPacket.from_dict(missing_write_credential)

            root_role_drift = _preview_packet_payload(root)
            root_role_drift["operations"] = ["stage.extract-native"]
            root_role_drift["targets"] = [
                {
                    "kind": "artifact-root",
                    "id": compute_operator_root_target_id(
                        str((root / "other").resolve())
                    ),
                }
            ]
            root_role_drift["destinations"] = deepcopy(root_role_drift["targets"])
            _sign_packet(root_role_drift)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "artifact-root destination identity drifts",
            ):
                OperatorPreflightPacket.from_dict(root_role_drift)

            unapproved_destination = _preview_packet_payload(root)
            unapproved_destination["targets"] = [
                {"kind": "openkb-collection", "id": "research-staging"},
                {"kind": "paper-id", "id": "paper-001"},
            ]
            unapproved_destination["destinations"] = deepcopy(
                unapproved_destination["targets"]
            )
            _sign_packet(unapproved_destination)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "not authorized",
            ):
                OperatorPreflightPacket.from_dict(unapproved_destination)


class TestApprovedOperatorPreflight(unittest.TestCase):
    def test_valid_approved_scope_is_still_execution_unsupported(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet_payload, receipt_payload = _approved_pair(root)
            packet_schema = json.loads(
                (SPEC_DIR / "operator-preflight-packet.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            result_schema = json.loads(
                (SPEC_DIR / "operator-preflight-result.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator(packet_schema).validate(packet_payload)
            packet = OperatorPreflightPacket.from_dict(packet_payload)
            receipt = ApprovedLiveReceipt.from_dict(receipt_payload)
            environment = {
                row["reference"]: f"synthetic-value-{index}"
                for index, row in enumerate(packet_payload["credential_requirements"])
            }

            result = evaluate_operator_preflight(
                packet,
                explicit_mode="approved-live",
                approval_receipt=receipt,
                environment=environment,
                now=EVALUATION_TIME,
            )

            self.assertEqual(
                result.decision,
                "approved-scope-validated-execution-unsupported",
            )
            self.assertEqual(result.blockers, ("external-execution-unsupported",))
            self.assertIsNotNone(result.approval_receipt)
            self.assertFalse(result.to_dict()["external_effects_performed"])
            Draft202012Validator(result_schema).validate(result.to_dict())

    def test_authorization_context_rejects_every_packet_only_scope_drift(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            original, _ = _approved_pair(root)
            mutations: dict[
                str,
                Callable[[dict[str, object]], None],
            ] = {
                "adapter": lambda payload: _mutate_adapter(payload),
                "profile": lambda payload: payload["provider"].__setitem__(  # type: ignore[union-attr]
                    "profile_id", "alternate-profile"
                ),
                "destination-role": lambda payload: _mutate_destination(payload),
                "rollback": lambda payload: payload.__setitem__(
                    "rollback_actions", ["delete-temporary-files"]
                ),
                "acceptance": lambda payload: payload.__setitem__(
                    "acceptance_status", "not-applicable"
                ),
                "credential-reference": lambda payload: _mutate_credential_ref(payload),
            }

            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    changed = deepcopy(original)
                    mutate(changed)
                    _sign_packet(changed)
                    with self.assertRaisesRegex(
                        MillefeuilleContractError,
                        "authorization context digest mismatch",
                    ):
                        OperatorPreflightPacket.from_dict(changed)

    def test_recomputed_context_still_requires_a_new_receipt(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet_payload, receipt_payload = _approved_pair(root)
            packet_payload["provider"]["profile_id"] = "alternate-profile"  # type: ignore[index]
            _refresh_context_and_sign(packet_payload)
            packet = OperatorPreflightPacket.from_dict(packet_payload)
            receipt = ApprovedLiveReceipt.from_dict(receipt_payload)

            with self.assertRaisesRegex(MillefeuilleContractError, "targets drift"):
                evaluate_operator_preflight(
                    packet,
                    explicit_mode="approved-live",
                    approval_receipt=receipt,
                    environment={
                        row["reference"]: "synthetic-present"
                        for row in packet_payload["credential_requirements"]
                    },
                    now=EVALUATION_TIME,
                )

    def test_wrong_identity_expiry_and_replay_fail_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet_payload, receipt_payload = _approved_pair(root)
            packet = OperatorPreflightPacket.from_dict(packet_payload)
            receipt = ApprovedLiveReceipt.from_dict(receipt_payload)
            environment = {
                row["reference"]: "synthetic-present"
                for row in packet_payload["credential_requirements"]
            }

            wrong_identity = deepcopy(packet_payload)
            wrong_identity["approval_receipt"]["receipt_id"] = "wrong-receipt"  # type: ignore[index]
            _sign_packet(wrong_identity)
            wrong_packet = OperatorPreflightPacket.from_dict(wrong_identity)
            with self.assertRaisesRegex(MillefeuilleContractError, "identity drift"):
                evaluate_operator_preflight(
                    wrong_packet,
                    explicit_mode="approved-live",
                    approval_receipt=receipt,
                    environment=environment,
                    now=EVALUATION_TIME,
                )

            with self.assertRaisesRegex(MillefeuilleContractError, "expired"):
                evaluate_operator_preflight(
                    packet,
                    explicit_mode="approved-live",
                    approval_receipt=receipt,
                    environment=environment,
                    now=datetime(2026, 8, 11, 11, 0, tzinfo=UTC),
                )

            replay = ReceiptReplayState(
                receipt_ids=frozenset({receipt.receipt_id}),
                content_digests=frozenset(),
            )
            with self.assertRaisesRegex(MillefeuilleContractError, "consumed"):
                evaluate_operator_preflight(
                    packet,
                    explicit_mode="approved-live",
                    approval_receipt=receipt,
                    environment=environment,
                    now=EVALUATION_TIME,
                    replay_state=replay,
                )

    def test_receipt_run_root_target_and_budget_drift_are_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet_payload, receipt_payload = _approved_pair(root)
            environment = {
                row["reference"]: "synthetic-present"
                for row in packet_payload["credential_requirements"]
            }
            mutations = {
                "run": (
                    lambda receipt: receipt.__setitem__("run_id", "run-mf-102-other"),
                    "run_id drift",
                ),
                "root": (
                    lambda receipt: receipt["scope"].__setitem__(  # type: ignore[union-attr]
                        "output_root", str((root / "other").resolve())
                    ),
                    "output_root drift",
                ),
                "target": (
                    lambda receipt: _mutate_receipt_target(receipt),
                    "targets drift",
                ),
                "budget": (
                    lambda receipt: receipt["scope"]["limits"].__setitem__(  # type: ignore[index]
                        "max_cost_usd_micros", 250001
                    ),
                    "max_cost_usd_micros drift",
                ),
            }

            for name, (mutate, pattern) in mutations.items():
                with self.subTest(name=name):
                    changed_receipt = deepcopy(receipt_payload)
                    changed_packet = deepcopy(packet_payload)
                    mutate(changed_receipt)
                    _sign_receipt(changed_receipt)
                    changed_packet["approval_receipt"]["content_digest"] = (  # type: ignore[index]
                        changed_receipt["integrity"]["content_digest"]  # type: ignore[index]
                    )
                    _sign_packet(changed_packet)
                    packet = OperatorPreflightPacket.from_dict(changed_packet)
                    receipt = ApprovedLiveReceipt.from_dict(changed_receipt)

                    with self.assertRaisesRegex(
                        MillefeuilleContractError,
                        pattern,
                    ):
                        evaluate_operator_preflight(
                            packet,
                            explicit_mode="approved-live",
                            approval_receipt=receipt,
                            environment=environment,
                            now=EVALUATION_TIME,
                        )

    @unittest.skipUnless(os.name == "nt", "Windows root identity semantics")
    def test_windows_equivalent_root_spelling_validates_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            alternate_case = str(root.resolve()).swapcase()
            packet_payload, receipt_payload = _approved_pair(
                root,
                packet_root=alternate_case,
            )
            packet = OperatorPreflightPacket.from_dict(packet_payload)
            receipt = ApprovedLiveReceipt.from_dict(receipt_payload)
            result = evaluate_operator_preflight(
                packet,
                explicit_mode="approved-live",
                approval_receipt=receipt,
                environment={
                    row["reference"]: "synthetic-present"
                    for row in packet_payload["credential_requirements"]
                },
                now=EVALUATION_TIME,
            )

            round_trip = OperatorPreflightResult.from_dict(result.to_dict())
            validate_operator_preflight_result(round_trip, packet)
            self.assertEqual(round_trip.to_dict(), result.to_dict())


class TestCredentialAndResultSanitization(unittest.TestCase):
    def test_credential_values_never_affect_json_markdown_or_result_digest(self):
        with tempfile.TemporaryDirectory() as tempdir:
            packet = OperatorPreflightPacket.from_dict(
                _read_only_packet_payload(Path(tempdir))
            )
            first_values = {
                TEST_ENV_REFS["zotero-library-id"]: "SYNTHETIC_SECRET_ALPHA_123",
                TEST_ENV_REFS["zotero-read"]: "SYNTHETIC_SECRET_BETA_456",
            }
            second_values = {
                TEST_ENV_REFS["zotero-library-id"]: "SYNTHETIC_SECRET_GAMMA_789",
                TEST_ENV_REFS["zotero-read"]: "SYNTHETIC_SECRET_DELTA_012",
            }

            first = evaluate_operator_preflight(
                packet,
                explicit_mode="read-only-live",
                environment=first_values,
            )
            second = evaluate_operator_preflight(
                packet,
                explicit_mode="read-only-live",
                environment=second_values,
            )

            self.assertEqual(first.to_dict(), second.to_dict())
            aggregate = json.dumps(
                first.to_dict()
            ) + render_operator_preflight_markdown(first)
            for secret_value in (*first_values.values(), *second_values.values()):
                self.assertNotIn(secret_value, aggregate)
            self.assertIn(TEST_ENV_REFS["zotero-read"], aggregate)
            self.assertIn('"state": "present"', aggregate)

    def test_missing_credentials_emit_only_reference_type_and_state(self):
        with tempfile.TemporaryDirectory() as tempdir:
            packet = OperatorPreflightPacket.from_dict(
                _read_only_packet_payload(Path(tempdir))
            )
            result = evaluate_operator_preflight(
                packet,
                explicit_mode="read-only-live",
                environment={},
            )

            self.assertEqual(result.decision, "blocked")
            self.assertEqual(
                result.blockers,
                (
                    "credential-missing.zotero-library-id",
                    "credential-missing.zotero-read",
                ),
            )
            for readiness in result.to_dict()["credential_readiness"]:
                self.assertEqual(set(readiness), {"type", "reference", "state"})

    def test_result_identity_and_packet_linkage_cannot_be_forged(self):
        with tempfile.TemporaryDirectory() as tempdir:
            packet = OperatorPreflightPacket.from_dict(
                _preview_packet_payload(Path(tempdir))
            )
            result = evaluate_operator_preflight(
                packet,
                explicit_mode="preview",
                environment={},
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "result content_digest mismatch",
            ):
                replace(result, content_digest="sha256:" + ("0" * 64))

            forged = result.to_dict()
            forged["packet_digest"] = "sha256:" + ("1" * 64)
            _sign_result(forged)
            forged_result = OperatorPreflightResult.from_dict(forged)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "packet_digest drift",
            ):
                validate_operator_preflight_result(forged_result, packet)

            extraneous = result.to_dict()
            extraneous["blockers"] = ["unexpected-blocker"]
            _sign_result(extraneous)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "blockers drift",
            ):
                OperatorPreflightResult.from_dict(extraneous)

    def test_result_linkage_binds_credentials_and_approval_identity(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            read_only_packet = OperatorPreflightPacket.from_dict(
                _read_only_packet_payload(root)
            )
            read_only_result = evaluate_operator_preflight(
                read_only_packet,
                explicit_mode="read-only-live",
                environment={
                    TEST_ENV_REFS["zotero-library-id"]: "SYNTHETIC_PRESENT_ALPHA",
                    TEST_ENV_REFS["zotero-read"]: "SYNTHETIC_PRESENT_BETA",
                },
            )
            forged_readiness = read_only_result.to_dict()
            forged_readiness["credential_readiness"] = []
            _sign_result(forged_readiness)
            self.assertEqual(
                OperatorPreflightResult.from_dict(forged_readiness).decision,
                "ready-for-read-only-live",
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "credential readiness scope drift",
            ):
                validate_operator_preflight_result(
                    OperatorPreflightResult.from_dict(forged_readiness),
                    read_only_packet,
                )

            packet_payload, receipt_payload = _approved_pair(root)
            approved_packet = OperatorPreflightPacket.from_dict(packet_payload)
            approved_result = evaluate_operator_preflight(
                approved_packet,
                explicit_mode="approved-live",
                approval_receipt=ApprovedLiveReceipt.from_dict(receipt_payload),
                environment={
                    requirement.reference: "SYNTHETIC_PRESENT_VALUE"
                    for requirement in approved_packet.credential_requirements
                },
                now=EVALUATION_TIME,
            )
            forged_approval = approved_result.to_dict()
            forged_approval["approval_receipt"]["receipt_id"] = (  # type: ignore[index]
                "receipt-mf-102-different"
            )
            _sign_result(forged_approval)
            forged_approval_result = OperatorPreflightResult.from_dict(forged_approval)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "approval receipt identity drift",
            ):
                validate_operator_preflight_result(
                    forged_approval_result,
                    approved_packet,
                )

    def test_result_loader_is_bounded_strict_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet = OperatorPreflightPacket.from_dict(_preview_packet_payload(root))
            result = evaluate_operator_preflight(
                packet,
                explicit_mode="preview",
                environment={},
            )
            encoded = json.dumps(result.to_dict(), sort_keys=True).encode("utf-8")
            padding = b" " * (OPERATOR_PREFLIGHT_RESULT_MAX_BYTES - len(encoded))
            result_path = root / "result.json"
            result_path.write_bytes(encoded + padding)

            loaded = load_operator_preflight_result(result_path)

            self.assertEqual(loaded.to_dict(), result.to_dict())
            result_path.write_bytes(encoded + padding + b" ")
            with self.assertRaisesRegex(MillefeuilleContractError, "exceeds"):
                load_operator_preflight_result(result_path)

            tampered = result.to_dict()
            tampered["decision"] = "blocked"
            _write_json(result_path, tampered)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "content_digest mismatch",
            ):
                load_operator_preflight_result(result_path)


class TestOperatorPreflightCli(unittest.TestCase):
    def test_packaged_entrypoint_dispatches_operator_preflight(self):
        from millefeuille.cli import main as packaged_main

        argv = [
            "millefeuille",
            "operator-preflight",
            "--packet",
            "synthetic.json",
            "--mode",
            "preview",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(
                packaged_main,
                "run_operator_preflight_cli",
                return_value=7,
            ) as dispatch,
            self.assertRaises(SystemExit) as raised,
        ):
            packaged_main.entrypoint()

        self.assertEqual(raised.exception.code, 7)
        dispatch.assert_called_once_with(argv[1:])

    def test_preview_json_and_markdown_are_deterministic_and_sanitized(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet_path = root / "packet.json"
            _write_json(packet_path, _preview_packet_payload(root))
            outputs: list[str] = []
            for output_format in ("json", "markdown", "markdown"):
                stdout = StringIO()
                stderr = StringIO()
                exit_code = run_operator_preflight_cli(
                    [
                        "operator-preflight",
                        "--packet",
                        str(packet_path),
                        "--mode",
                        "preview",
                        "--format",
                        output_format,
                    ],
                    stdout=stdout,
                    stderr=stderr,
                    environment={"UNRELATED": "SYNTHETIC_SECRET_NEVER_READ"},
                )
                self.assertEqual(exit_code, 0)
                self.assertEqual(stderr.getvalue(), "")
                self.assertNotIn("SYNTHETIC_SECRET_NEVER_READ", stdout.getvalue())
                outputs.append(stdout.getvalue())
            json_payload = json.loads(outputs[0])
            self.assertEqual(
                json_payload["schema_version"],
                OPERATOR_PREFLIGHT_RESULT_SCHEMA_VERSION,
            )
            self.assertEqual(outputs[1], outputs[2])

    def test_read_only_missing_credentials_returns_validation_exit_two(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet_path = root / "packet.json"
            _write_json(packet_path, _read_only_packet_payload(root))
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_operator_preflight_cli(
                [
                    "operator-preflight",
                    "--packet",
                    str(packet_path),
                    "--mode",
                    "read-only-live",
                ],
                stdout=stdout,
                stderr=stderr,
                environment={},
            )

            self.assertEqual(exit_code, 2)
            self.assertEqual(json.loads(stdout.getvalue())["decision"], "blocked")
            self.assertEqual(stderr.getvalue(), "")

    def test_receipt_cannot_promote_preview_and_mode_drift_fails(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet_path = root / "packet.json"
            receipt_path = root / "receipt.json"
            _write_json(packet_path, _preview_packet_payload(root))
            _, receipt = _approved_pair(root)
            _write_json(receipt_path, receipt)
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_operator_preflight_cli(
                [
                    "operator-preflight",
                    "--packet",
                    str(packet_path),
                    "--mode",
                    "preview",
                    "--approval-receipt",
                    str(receipt_path),
                ],
                stdout=stdout,
                stderr=stderr,
                environment={},
            )

            self.assertEqual(exit_code, 3)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("cannot promote", stderr.getvalue())

            stderr = StringIO()
            exit_code = run_operator_preflight_cli(
                [
                    "operator-preflight",
                    "--packet",
                    str(packet_path),
                    "--mode",
                    "read-only-live",
                ],
                stdout=StringIO(),
                stderr=stderr,
                environment={},
            )
            self.assertEqual(exit_code, 2)
            self.assertIn("mode drift", stderr.getvalue())

    def test_valid_approved_packet_outputs_result_but_preserves_exit_three(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet_payload, receipt_payload = _approved_pair(root)
            packet_path = root / "packet.json"
            receipt_path = root / "receipt.json"
            _write_json(packet_path, packet_payload)
            _write_json(receipt_path, receipt_payload)
            secrets = {
                row["reference"]: f"SYNTHETIC_SECRET_VALUE_{index}"
                for index, row in enumerate(packet_payload["credential_requirements"])
            }
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_operator_preflight_cli(
                [
                    "operator-preflight",
                    "--packet",
                    str(packet_path),
                    "--mode",
                    "approved-live",
                    "--approval-receipt",
                    str(receipt_path),
                ],
                stdout=stdout,
                stderr=stderr,
                environment=secrets,
                now=EVALUATION_TIME,
            )

            self.assertEqual(exit_code, 3)
            result = json.loads(stdout.getvalue())
            self.assertEqual(
                result["decision"],
                "approved-scope-validated-execution-unsupported",
            )
            self.assertIn("execution remains unsupported", stderr.getvalue())
            aggregate = stdout.getvalue() + stderr.getvalue()
            for secret in secrets.values():
                self.assertNotIn(secret, aggregate)

    def test_secret_like_packet_failure_does_not_reflect_value(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            packet = _preview_packet_payload(root)
            secret = "sk-proj-SECRETVALUENEVERREFLECTED12345"
            packet["source"]["selector"]["value"] = secret  # type: ignore[index]
            packet_path = root / "packet.json"
            _write_json(packet_path, packet)
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_operator_preflight_cli(
                [
                    "operator-preflight",
                    "--packet",
                    str(packet_path),
                    "--mode",
                    "preview",
                ],
                stdout=stdout,
                stderr=stderr,
                environment={"UNRELATED": secret},
            )

            self.assertEqual(exit_code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertNotIn(secret, stderr.getvalue())


def _mutate_receipt_target(receipt: dict[str, object]) -> None:
    targets = receipt["scope"]["targets"]  # type: ignore[index]
    for target in targets:  # type: ignore[union-attr]
        if target["kind"] == "paper-id":
            target["id"] = "paper-002"
            return
    raise AssertionError("paper target not found")


def _mutate_adapter(payload: dict[str, object]) -> None:
    payload["source"]["adapter"] = "source-pack"  # type: ignore[index]
    payload["source"]["selector"] = {  # type: ignore[index]
        "kind": "source-pack",
        "value": "paper-001",
    }
    payload["credential_requirements"] = _credential_rows(
        "model-oauth",
        "openkb-write",
    )


def _mutate_destination(payload: dict[str, object]) -> None:
    payload["destinations"] = [
        destination
        if destination["kind"] != "openkb-collection"
        else {"kind": "openkb-collection", "id": "research-secondary"}
        for destination in payload["destinations"]  # type: ignore[union-attr]
    ]


def _mutate_credential_ref(payload: dict[str, object]) -> None:
    for requirement in payload["credential_requirements"]:  # type: ignore[union-attr]
        if requirement["type"] == "model-oauth":
            requirement["reference"] = "OPENCLAW_CODEX_OAUTH_READY"
            return
    raise AssertionError("model-oauth requirement not found")


if __name__ == "__main__":
    unittest.main()
