"""Offline tests for evidence-safe abandoned staging quarantine."""

from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
from io import StringIO
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from millefeuille.cli.maintenance import run_maintenance_cli
from millefeuille.domain import staging_cleanup as cleanup
from millefeuille.domain.live_receipts import (
    APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
    ApprovedLiveReceipt,
    ReceiptReplayState,
    compute_approved_live_receipt_digest,
    load_approved_live_receipt,
    validate_approved_live_receipt,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.retrieve import (
    RETRIEVAL_BATCH_REPORT_REF,
    RETRIEVAL_BATCH_RESULT_REF,
    RETRIEVAL_BATCH_ROOT_REF,
)

SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"
SCHEMA_NAMES = (
    "approved-live-receipt.schema.json",
    "staging-cleanup-audit.schema.json",
    "staging-cleanup-disposition.schema.json",
    "staging-cleanup-inspection.schema.json",
    "staging-cleanup-plan.schema.json",
    "temporary-bridge-ownership.schema.json",
)
PRIVATE_MARKER = "%PDF-private-fixture-DO-NOT-SERIALIZE"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _make_read_only(path: Path) -> None:
    os.chmod(path, 0o555 if path.is_dir() else 0o444)


def _make_tree_writable(path: Path) -> None:
    if not path.exists():
        return
    for entry in sorted(
        path.rglob("*"),
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        with suppress(OSError):
            os.chmod(entry, 0o700 if entry.is_dir() else 0o600)
    with suppress(OSError):
        os.chmod(path, 0o700)


def _write_retrieval_generation(
    source: Path,
    *,
    batch_id: str = "batch-one",
    suffix: str = "0123456789abcdef",
    result_bytes: bytes = b"",
    report_bytes: bytes = b"",
    extra_name: str | None = None,
) -> str:
    relative = RETRIEVAL_BATCH_ROOT_REF / batch_id / f".retrieval.tmp-{suffix}"
    generation = source / relative
    generation.mkdir(parents=True)
    result = generation / RETRIEVAL_BATCH_RESULT_REF.name
    report = generation / RETRIEVAL_BATCH_REPORT_REF.name
    result.write_bytes(result_bytes)
    report.write_bytes(report_bytes)
    _make_read_only(result)
    _make_read_only(report)
    if extra_name is not None:
        extra = generation / extra_name
        extra.write_bytes(b"")
        _make_read_only(extra)
    _make_read_only(generation)
    return relative.as_posix()


def _bridge_manifest(
    asset_name: str,
    asset_bytes: bytes,
    *,
    producer_id: str = "millefeuille.fixture",
    run_id: str = "run-bridge-fixture",
    bridge_id: str = "bridge-fixture",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": cleanup.BRIDGE_OWNERSHIP_SCHEMA_VERSION,
        "producer_id": producer_id,
        "run_id": run_id,
        "bridge_id": bridge_id,
        "publication_state": "unpublished",
        "cleanup_state": "abandoned",
        "inventory": [
            {
                "relative_path": asset_name,
                "type": "file",
                "size_bytes": len(asset_bytes),
                "sha256": "sha256:" + hashlib.sha256(asset_bytes).hexdigest(),
            }
        ],
    }
    payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": cleanup.compute_bridge_ownership_digest(payload),
    }
    return payload


def _write_bridge_generation(
    source: Path,
    *,
    suffix: str = "fedcba9876543210",
    asset_name: str = "private-paper.pdf",
    asset_bytes: bytes = PRIVATE_MARKER.encode("utf-8"),
    producer_id: str = "millefeuille.fixture",
    run_id: str = "run-bridge-fixture",
    bridge_id: str = "bridge-fixture",
) -> str:
    relative = f"{cleanup.BRIDGE_TEMP_PREFIX}{suffix}"
    generation = source / relative
    generation.mkdir(parents=True)
    asset = generation / asset_name
    asset.write_bytes(asset_bytes)
    manifest = _bridge_manifest(
        asset_name,
        asset_bytes,
        producer_id=producer_id,
        run_id=run_id,
        bridge_id=bridge_id,
    )
    ownership = generation / cleanup.BRIDGE_OWNERSHIP_MANIFEST
    _write_json(ownership, manifest)
    _make_read_only(asset)
    _make_read_only(ownership)
    _make_read_only(generation)
    return relative


def _schemas() -> tuple[dict[str, object], Registry]:
    schemas = tuple(
        json.loads((SPEC_DIR / name).read_text(encoding="utf-8"))
        for name in SCHEMA_NAMES
    )
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    return {schema["$id"].rsplit("/", 1)[-1]: schema for schema in schemas}, registry


def _validate_schema(name: str, payload: object) -> None:
    schemas, registry = _schemas()
    schema = schemas[name]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, registry=registry).validate(payload)


def _sign_receipt(payload: dict[str, object]) -> dict[str, object]:
    payload.pop("integrity", None)
    payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_approved_live_receipt_digest(payload),
    }
    return payload


def _maintenance_receipt(
    plan: dict[str, object],
    source: Path,
    quarantine: Path,
    *,
    receipt_id: str = "receipt-mf-106-fixture",
) -> tuple[dict[str, object], datetime, datetime]:
    now = datetime.now(UTC).replace(microsecond=0)
    approved_at = now - timedelta(minutes=1)
    expires_at = now + timedelta(hours=1)
    candidates = plan["candidates"]
    assert isinstance(candidates, list)
    payload: dict[str, object] = {
        "schema_version": APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "run_id": plan["run_id"],
        "approval": {
            "approver_id": "franck.meyer@kaist.ac.kr",
            "approved_at": approved_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": {
            "operations": [cleanup.STAGING_CLEANUP_OPERATION],
            "targets": [
                {"kind": candidate["kind"], "id": candidate["relative_path"]}
                for candidate in candidates
            ],
            "max_items": plan["candidate_count"],
            "selector": {
                "kind": "maintenance-plan",
                "value": plan["integrity"]["content_digest"],
            },
            "output_root": str(quarantine),
            "source_pack_root": str(source),
            "provider": None,
            "limits": {
                "max_provider_calls": 0,
                "max_cost_usd_micros": 0,
            },
            "disposal_policy": {
                "pdfs": "not-applicable",
                "provider_payloads": "not-applicable",
                "temporary_files": cleanup.STAGING_CLEANUP_DISPOSAL,
            },
            "stop_conditions": list(cleanup.STAGING_CLEANUP_STOP_CONDITIONS),
        },
    }
    return _sign_receipt(payload), now, expires_at


def _legacy_receipt(root: Path) -> dict[str, object]:
    now = datetime.now(UTC).replace(microsecond=0)
    payload: dict[str, object] = {
        "schema_version": APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
        "receipt_id": "receipt-mf-100-compatible",
        "run_id": "run-compatible",
        "approval": {
            "approver_id": "franck.meyer@kaist.ac.kr",
            "approved_at": (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "expires_at": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": {
            "operations": ["zotero.writeback"],
            "targets": [{"kind": "paper-id", "id": "zotero-ITEM1"}],
            "max_items": 1,
            "selector": {"kind": "paper-id", "value": "zotero-ITEM1"},
            "output_root": str(root.resolve()),
            "source_pack_root": str(root.resolve()),
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
            "stop_conditions": ["approval-expired", "first-error", "scope-drift"],
        },
    }
    return _sign_receipt(payload)


def _linux_executor_available() -> bool:
    return cleanup._has_pinned_directory_primitives()


class StagingCleanupFixtureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / "source-packs"
        self.quarantine = self.base / "quarantine"
        self.source.mkdir()
        self.quarantine.mkdir()

    def tearDown(self) -> None:
        _make_tree_writable(self.base)
        self.temporary.cleanup()

    def build_plan(self, paths: list[str]) -> dict[str, object]:
        return cleanup.build_staging_cleanup_plan(
            source_root=self.source,
            quarantine_root=self.quarantine,
            run_id="run-mf-106-fixture",
            candidate_paths=paths,
            expected_candidate_count=len(paths),
            disposal=cleanup.STAGING_CLEANUP_DISPOSAL,
            stop_conditions=cleanup.STAGING_CLEANUP_STOP_CONDITIONS,
        )

    def write_plan_and_receipt(
        self,
        plan: dict[str, object],
        *,
        receipt_id: str = "receipt-mf-106-fixture",
    ) -> tuple[Path, Path, datetime, datetime]:
        plan_path = self.base / "cleanup-plan.json"
        receipt_path = self.base / "approved-live-receipt.json"
        _write_json(plan_path, plan)
        receipt, now, expires = _maintenance_receipt(
            plan,
            self.source,
            self.quarantine,
            receipt_id=receipt_id,
        )
        _write_json(receipt_path, receipt)
        return plan_path, receipt_path, now, expires


class TestStagingCleanupInspectionAndPlan(StagingCleanupFixtureTestCase):
    def test_strict_schemas_compile(self) -> None:
        schemas, _registry = _schemas()
        self.assertEqual(set(schemas), set(SCHEMA_NAMES))
        for schema in schemas.values():
            Draft202012Validator.check_schema(schema)

    def test_mixed_candidates_are_deterministic_sanitized_and_schema_valid(self):
        retrieval = _write_retrieval_generation(self.source)
        bridge = _write_bridge_generation(self.source)
        unmarked = self.source / f"{cleanup.BRIDGE_TEMP_PREFIX}1111111111111111"
        unmarked.mkdir()
        (unmarked / "legacy-private.pdf").write_text(
            PRIVATE_MARKER,
            encoding="utf-8",
        )
        _make_read_only(unmarked / "legacy-private.pdf")
        _make_read_only(unmarked)
        legacy = self.source / "legacy-staged-bridge-private-marker"
        legacy.mkdir()
        (legacy / "paper.pdf").write_text(PRIVATE_MARKER, encoding="utf-8")

        inspection = cleanup.inspect_staging_cleanup(
            source_root=self.source,
            quarantine_root=self.quarantine,
        )

        self.assertEqual(inspection["counts"], {"eligible": 2, "unverified": 1})
        self.assertEqual(inspection["default_action"], "inspect-only")
        self.assertFalse(inspection["private_bytes_serialized"])
        self.assertFalse(inspection["secret_material_persisted"])
        self.assertIn("apply_supported", inspection["capabilities"])
        self.assertIn("same_device_stable_roots", inspection["capabilities"])
        _validate_schema("staging-cleanup-inspection.schema.json", inspection)

        serialized_inspection = json.dumps(inspection, sort_keys=True)
        self.assertNotIn(PRIVATE_MARKER, serialized_inspection)
        self.assertNotIn(str(self.source), serialized_inspection)
        self.assertNotIn(str(self.quarantine), serialized_inspection)
        self.assertNotIn(unmarked.name, serialized_inspection)
        self.assertNotIn(legacy.name, serialized_inspection)

        plan = self.build_plan([bridge, retrieval])
        repeated = self.build_plan([retrieval, bridge])

        self.assertEqual(plan, repeated)
        self.assertEqual(
            [candidate["kind"] for candidate in plan["candidates"]],
            [cleanup.RETRIEVAL_STAGING_KIND, cleanup.TEMPORARY_BRIDGE_KIND],
        )
        _validate_schema("staging-cleanup-plan.schema.json", plan)
        serialized_plan = json.dumps(plan, sort_keys=True)
        self.assertNotIn(PRIVATE_MARKER, serialized_plan)
        self.assertNotIn(str(self.source), serialized_plan)
        self.assertNotIn(str(self.quarantine), serialized_plan)

        manifest = json.loads(
            (self.source / bridge / cleanup.BRIDGE_OWNERSHIP_MANIFEST).read_text(
                encoding="utf-8"
            )
        )
        _validate_schema("temporary-bridge-ownership.schema.json", manifest)
        plan_path = self.base / "plan.json"
        _write_json(plan_path, plan)
        self.assertEqual(cleanup.load_staging_cleanup_plan(plan_path), plan)

        tampered = deepcopy(plan)
        tampered["run_id"] = "run-tampered"
        _write_json(plan_path, tampered)
        with self.assertRaisesRegex(MillefeuilleContractError, "digest mismatch"):
            cleanup.load_staging_cleanup_plan(plan_path)

        self.assertTrue(unmarked.exists())
        self.assertTrue(legacy.exists())

    def test_unverified_private_and_published_entries_are_preserved(self):
        nonzero = _write_retrieval_generation(
            self.source,
            suffix="0000000000000001",
            result_bytes=b"unowned-private-bytes",
        )
        extra = _write_retrieval_generation(
            self.source,
            suffix="0000000000000002",
            extra_name="unknown-entry",
        )
        hard_linked = _write_retrieval_generation(
            self.source,
            suffix="0000000000000003",
        )
        hard_link_copy = self.source / "outside-hard-link"
        try:
            os.link(
                self.source / hard_linked / RETRIEVAL_BATCH_RESULT_REF.name,
                hard_link_copy,
            )
        except OSError:
            hard_link_copy = None

        batch = self.source / RETRIEVAL_BATCH_ROOT_REF / "batch-one"
        canonical = batch / "retrieval"
        canonical.mkdir()
        (canonical / RETRIEVAL_BATCH_RESULT_REF.name).write_bytes(b"published")
        (canonical / RETRIEVAL_BATCH_REPORT_REF.name).write_text(
            "published",
            encoding="utf-8",
        )
        symlink_generation = batch / ".retrieval.tmp-0000000000000004"
        try:
            symlink_generation.symlink_to(canonical, target_is_directory=True)
        except OSError:
            symlink_generation = None

        missing_manifest = self.source / f"{cleanup.BRIDGE_TEMP_PREFIX}0000000000000005"
        missing_manifest.mkdir()
        (missing_manifest / "unowned.pdf").write_bytes(b"unowned")
        _make_read_only(missing_manifest / "unowned.pdf")
        _make_read_only(missing_manifest)
        drifted_bridge = _write_bridge_generation(
            self.source,
            suffix="0000000000000006",
        )
        drifted_asset = self.source / drifted_bridge / "private-paper.pdf"
        os.chmod(self.source / drifted_bridge, 0o700)
        os.chmod(drifted_asset, 0o600)
        drifted_asset.write_bytes(b"X" * len(PRIVATE_MARKER.encode("utf-8")))
        _make_read_only(drifted_asset)
        _make_read_only(self.source / drifted_bridge)
        secret_bridge = _write_bridge_generation(
            self.source,
            suffix="0000000000000007",
            run_id="https://private.invalid/run",
        )

        before_nonzero = (
            self.source / nonzero / RETRIEVAL_BATCH_RESULT_REF.name
        ).read_bytes()
        inspection = cleanup.inspect_staging_cleanup(
            source_root=self.source,
            quarantine_root=self.quarantine,
        )

        self.assertEqual(inspection["counts"]["eligible"], 0)
        reasons = {
            reason
            for candidate in inspection["unverified_candidates"]
            for reason in candidate["reason_codes"]
        }
        self.assertIn("nonzero-staging-bytes", reasons)
        self.assertIn("unexpected-entries", reasons)
        if hard_link_copy is not None:
            self.assertIn("hard-linked-entry", reasons)
        self.assertIn("missing-ownership-manifest", reasons)
        self.assertIn("hash-drift", reasons)
        self.assertIn("invalid-ownership-manifest", reasons)

        serialized = json.dumps(inspection, sort_keys=True)
        for private_name in (
            PurePosixPath(nonzero).name,
            PurePosixPath(extra).name,
            PurePosixPath(drifted_bridge).name,
            PurePosixPath(secret_bridge).name,
        ):
            self.assertNotIn(private_name, serialized)
        self.assertEqual(
            (self.source / nonzero / RETRIEVAL_BATCH_RESULT_REF.name).read_bytes(),
            before_nonzero,
        )
        self.assertEqual(
            (canonical / RETRIEVAL_BATCH_RESULT_REF.name).read_bytes(),
            b"published",
        )
        if symlink_generation is not None:
            self.assertTrue(symlink_generation.is_symlink())
        with self.assertRaisesRegex(MillefeuilleContractError, "unverified"):
            self.build_plan([nonzero])

    def test_safe_nondirectory_batch_is_reported_opaque_and_preserved(self):
        batch_root = self.source / RETRIEVAL_BATCH_ROOT_REF
        batch_root.mkdir(parents=True)
        unsafe_batch = batch_root / "safe-batch"
        unsafe_batch.write_text(PRIVATE_MARKER, encoding="utf-8")

        inspection = cleanup.inspect_staging_cleanup(
            source_root=self.source,
            quarantine_root=self.quarantine,
        )

        self.assertEqual(inspection["counts"], {"eligible": 0, "unverified": 1})
        self.assertEqual(
            inspection["unverified_candidates"][0]["namespace"],
            "retrieval",
        )
        self.assertEqual(
            inspection["unverified_candidates"][0]["reason_codes"],
            ["link-reparse-or-nondirectory"],
        )
        serialized = json.dumps(inspection, sort_keys=True)
        self.assertNotIn(unsafe_batch.name, serialized)
        self.assertNotIn(PRIVATE_MARKER, serialized)
        self.assertEqual(unsafe_batch.read_text(encoding="utf-8"), PRIVATE_MARKER)

    def test_plan_rejects_existing_deterministic_quarantine_destination(self):
        retrieval = _write_retrieval_generation(self.source)
        first_plan = self.build_plan([retrieval])
        destination_name = first_plan["candidates"][0]["quarantine_name"]
        destination = self.quarantine / destination_name
        destination.mkdir()
        (destination / "foreign-private-entry").write_text(
            PRIVATE_MARKER,
            encoding="utf-8",
        )

        with self.assertRaisesRegex(MillefeuilleContractError, "already exists"):
            self.build_plan([retrieval])

        self.assertEqual(
            (destination / "foreign-private-entry").read_text(encoding="utf-8"),
            PRIVATE_MARKER,
        )

    def test_malformed_persistent_lock_fails_closed_and_is_never_removed(self):
        lock = self.quarantine / cleanup._LOCK_NAME
        lock.write_bytes(b"x")

        with self.assertRaisesRegex(MillefeuilleContractError, "malformed"):
            cleanup.inspect_staging_cleanup(
                source_root=self.source,
                quarantine_root=self.quarantine,
            )

        self.assertEqual(lock.read_bytes(), b"x")

    def test_valid_persistent_lock_is_reserved_metadata_not_a_candidate(self):
        empty = cleanup.inspect_staging_cleanup(
            source_root=self.source,
            quarantine_root=self.quarantine,
        )
        lock = self.quarantine / cleanup._LOCK_NAME
        lock.write_bytes(b"0")

        with_lock = cleanup.inspect_staging_cleanup(
            source_root=self.source,
            quarantine_root=self.quarantine,
        )

        self.assertEqual(with_lock["counts"], {"eligible": 0, "unverified": 0})
        self.assertEqual(
            with_lock["quarantine_namespace_identity"],
            empty["quarantine_namespace_identity"],
        )
        self.assertEqual(lock.read_bytes(), b"0")


class TestStagingCleanupReceiptAndCli(StagingCleanupFixtureTestCase):
    def test_mf100_schema_is_backward_compatible_and_accepts_narrow_maintenance(self):
        legacy = _legacy_receipt(self.base)
        _validate_schema("approved-live-receipt.schema.json", legacy)
        self.assertEqual(ApprovedLiveReceipt.from_dict(legacy).to_dict(), legacy)

        retrieval = _write_retrieval_generation(self.source)
        plan = self.build_plan([retrieval])
        payload, evaluation_time, _expires = _maintenance_receipt(
            plan,
            self.source,
            self.quarantine,
        )
        _validate_schema("approved-live-receipt.schema.json", payload)
        receipt_path = self.base / "receipt.json"
        _write_json(receipt_path, payload)
        receipt = load_approved_live_receipt(receipt_path)
        roots = cleanup._root_context(self.source, self.quarantine)
        request = cleanup._approved_live_request(plan, roots)

        validate_approved_live_receipt(
            receipt,
            request,
            now=evaluation_time,
            replay_state=ReceiptReplayState(),
        )
        self.assertEqual(request.selector.kind, "maintenance-plan")
        self.assertEqual(
            request.disposal_policy.temporary_files,
            "quarantine-until-mf-197",
        )
        self.assertEqual(
            tuple(target.id for target in request.targets),
            (retrieval,),
        )

        wrong_selector = deepcopy(legacy)
        wrong_selector["scope"]["selector"] = {
            "kind": "maintenance-plan",
            "value": "sha256:" + ("0" * 64),
        }
        _sign_receipt(wrong_selector)
        with self.assertRaises(ValidationError):
            _validate_schema("approved-live-receipt.schema.json", wrong_selector)
        with self.assertRaisesRegex(MillefeuilleContractError, "exact maintenance"):
            ApprovedLiveReceipt.from_dict(wrong_selector)

        wrong_disposal = deepcopy(legacy)
        wrong_disposal["scope"]["disposal_policy"]["temporary_files"] = (
            "quarantine-until-mf-197"
        )
        _sign_receipt(wrong_disposal)
        with self.assertRaises(ValidationError):
            _validate_schema("approved-live-receipt.schema.json", wrong_disposal)
        with self.assertRaisesRegex(MillefeuilleContractError, "exact maintenance"):
            ApprovedLiveReceipt.from_dict(wrong_disposal)

    def test_cli_inspect_and_plan_are_read_only_and_apply_defaults_to_preview(self):
        retrieval = _write_retrieval_generation(self.source)
        before_source = cleanup.inspect_staging_cleanup(
            source_root=self.source,
            quarantine_root=self.quarantine,
        )["source_namespace_identity"]
        stdout = StringIO()
        stderr = StringIO()
        inspect_code = run_maintenance_cli(
            [
                "staging",
                "inspect",
                "--source-root",
                str(self.source.resolve()),
                "--quarantine-root",
                str(self.quarantine.resolve()),
                "--json",
            ],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(inspect_code, 0, stderr.getvalue())
        _validate_schema(
            "staging-cleanup-inspection.schema.json",
            json.loads(stdout.getvalue()),
        )

        plan_args = [
            "staging",
            "plan",
            "--source-root",
            str(self.source.resolve()),
            "--quarantine-root",
            str(self.quarantine.resolve()),
            "--run-id",
            "run-cli-fixture",
            "--candidate",
            retrieval,
            "--candidate-count",
            "1",
            "--disposal",
            cleanup.STAGING_CLEANUP_DISPOSAL,
        ]
        for condition in cleanup.STAGING_CLEANUP_STOP_CONDITIONS:
            plan_args.extend(("--stop-condition", condition))
        plan_args.append("--json")
        stdout = StringIO()
        plan_code = run_maintenance_cli(plan_args, stdout=stdout, stderr=stderr)
        self.assertEqual(plan_code, 0, stderr.getvalue())
        plan = json.loads(stdout.getvalue())
        _validate_schema("staging-cleanup-plan.schema.json", plan)

        stdout = StringIO()
        stderr = StringIO()
        apply_code = run_maintenance_cli(
            [
                "staging",
                "apply",
                "--source-root",
                str(self.source.resolve()),
                "--quarantine-root",
                str(self.quarantine.resolve()),
                "--plan",
                str(self.base / "missing-plan.json"),
                "--approval-receipt",
                str(self.base / "missing-receipt.json"),
                "--json",
            ],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(apply_code, 3)
        self.assertIn("requires explicit --mode approved-live", stderr.getvalue())
        self.assertEqual(list(self.quarantine.iterdir()), [])
        after_source = cleanup.inspect_staging_cleanup(
            source_root=self.source,
            quarantine_root=self.quarantine,
        )["source_namespace_identity"]
        self.assertEqual(before_source, after_source)

    def test_unsupported_executor_refuses_before_receipt_or_local_writes(self):
        retrieval = _write_retrieval_generation(self.source)
        plan = self.build_plan([retrieval])
        plan_path = self.base / "plan.json"
        _write_json(plan_path, plan)

        with (
            mock.patch.object(
                cleanup,
                "_roots_support_atomic_quarantine",
                return_value=False,
            ),
            self.assertRaisesRegex(MillefeuilleContractError, "supported local"),
        ):
            cleanup.apply_staging_cleanup_plan(
                plan_path=plan_path,
                source_root=self.source,
                quarantine_root=self.quarantine,
                approval_receipt_path=self.base / "missing-receipt.json",
            )

        self.assertTrue((self.source / retrieval).exists())
        self.assertEqual(list(self.quarantine.iterdir()), [])


class TestStagingCleanupPlatformPrimitives(unittest.TestCase):
    def test_windows_capabilities_do_not_claim_atomic_or_pinned_mutation(self):
        with (
            mock.patch.object(cleanup.os, "name", "nt"),
            mock.patch.object(cleanup.sys, "platform", "win32"),
        ):
            capabilities = cleanup.staging_cleanup_capabilities()

        self.assertEqual(capabilities["platform"], "windows")
        self.assertFalse(capabilities["atomic_no_replace_quarantine"])
        self.assertFalse(capabilities["pinned_directory_mutation"])

    def test_darwin_renamex_np_uses_the_three_argument_signature(self):
        source = PurePosixPath("/source/candidate")
        destination = PurePosixPath("/quarantine/candidate")
        renamex_np = mock.Mock(return_value=0)
        library = SimpleNamespace(renamex_np=renamex_np)
        with (
            mock.patch.object(cleanup.os, "name", "posix"),
            mock.patch.object(cleanup.sys, "platform", "darwin"),
            mock.patch("ctypes.CDLL", return_value=library),
            mock.patch.object(
                cleanup,
                "_entry_exists_no_follow",
                return_value=False,
            ),
        ):
            cleanup._atomic_rename_noreplace(
                source,
                destination,
            )

        self.assertEqual(renamex_np.call_count, 1)
        args = renamex_np.call_args.args
        self.assertEqual(len(args), 3)
        self.assertEqual(args[0], b"/source/candidate")
        self.assertEqual(args[1], b"/quarantine/candidate")
        self.assertEqual(args[2], 0x00000004)

    def test_linux_renameat2_uses_dirfds_and_five_arguments(self):
        source = PurePosixPath("/ignored/source")
        destination = PurePosixPath("/ignored/destination")
        renameat2 = mock.Mock(return_value=0)
        library = SimpleNamespace(renameat2=renameat2)
        with (
            mock.patch.object(cleanup.os, "name", "posix"),
            mock.patch.object(cleanup.sys, "platform", "linux"),
            mock.patch("ctypes.CDLL", return_value=library),
            mock.patch.object(cleanup, "_relative_entry_exists", return_value=False),
        ):
            cleanup._atomic_rename_noreplace(
                source,
                destination,
                source_dir_fd=11,
                destination_dir_fd=12,
                source_name="candidate",
                destination_name="quarantined",
            )

        self.assertEqual(
            renameat2.call_args.args,
            (11, b"candidate", 12, b"quarantined", 1),
        )

    def test_macos_pinned_rename_and_windows_rename_fail_closed(self):
        darwin_source = PurePosixPath("/source/candidate")
        darwin_destination = PurePosixPath("/quarantine/candidate")
        windows_source = Path("C:/source/candidate")
        windows_destination = Path("C:/quarantine/candidate")
        with (
            mock.patch.object(cleanup.os, "name", "posix"),
            mock.patch.object(cleanup.sys, "platform", "darwin"),
            self.assertRaisesRegex(MillefeuilleContractError, "not supported on macOS"),
        ):
            cleanup._atomic_rename_noreplace(
                darwin_source,
                darwin_destination,
                source_dir_fd=3,
                destination_dir_fd=4,
                source_name="candidate",
                destination_name="candidate",
            )
        with (
            mock.patch.object(cleanup.os, "name", "nt"),
            self.assertRaisesRegex(MillefeuilleContractError, "unsupported"),
        ):
            cleanup._atomic_rename_noreplace(
                windows_source,
                windows_destination,
            )


@unittest.skipUnless(
    _linux_executor_available(),
    "requires Linux dirfd, flock, and renameat2 support",
)
class TestStagingCleanupLinuxApply(StagingCleanupFixtureTestCase):
    def test_apply_quarantines_without_deletion_and_exact_rerun_is_no_effect(self):
        retrieval = _write_retrieval_generation(self.source)
        bridge = _write_bridge_generation(self.source)
        preserved_source = self.source / "legacy-bridge-private"
        preserved_source.mkdir()
        (preserved_source / "paper.pdf").write_text(
            PRIVATE_MARKER,
            encoding="utf-8",
        )
        preserved_quarantine = self.quarantine / "foreign-private-entry"
        preserved_quarantine.write_text(PRIVATE_MARKER, encoding="utf-8")
        plan = self.build_plan([retrieval, bridge])
        plan_path, receipt_path, evaluation_time, expires = self.write_plan_and_receipt(
            plan
        )

        disposition = cleanup.apply_staging_cleanup_plan(
            plan_path=plan_path,
            source_root=self.source,
            quarantine_root=self.quarantine,
            approval_receipt_path=receipt_path,
            evaluated_at=evaluation_time,
        )

        self.assertEqual(disposition["status"], "quarantined")
        self.assertFalse(disposition["permanent_deletion_performed"])
        _validate_schema("staging-cleanup-disposition.schema.json", disposition)
        for candidate in plan["candidates"]:
            self.assertFalse((self.source / candidate["relative_path"]).exists())
            destination = self.quarantine / candidate["quarantine_name"]
            self.assertTrue(destination.is_dir())
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o555)
            if candidate["kind"] == cleanup.TEMPORARY_BRIDGE_KIND:
                self.assertEqual(
                    (destination / "private-paper.pdf").read_text(encoding="utf-8"),
                    PRIVATE_MARKER,
                )
        lock = self.quarantine / cleanup._LOCK_NAME
        self.assertEqual(lock.read_bytes(), b"0")
        self.assertEqual(
            (preserved_source / "paper.pdf").read_text(encoding="utf-8"),
            PRIVATE_MARKER,
        )
        self.assertEqual(
            preserved_quarantine.read_text(encoding="utf-8"),
            PRIVATE_MARKER,
        )
        lock_identity = os.lstat(lock).st_ino
        audits = tuple(self.quarantine.glob(".mf106-audit-*.json"))
        self.assertEqual(len(audits), 1)
        audit = json.loads(audits[0].read_text(encoding="utf-8"))
        _validate_schema("staging-cleanup-audit.schema.json", audit)
        sanitized = json.dumps(
            {"audit": audit, "disposition": disposition},
            sort_keys=True,
        )
        self.assertNotIn(PRIVATE_MARKER, sanitized)
        self.assertNotIn(str(self.source), sanitized)
        self.assertNotIn(str(self.quarantine), sanitized)

        rerun = cleanup.apply_staging_cleanup_plan(
            plan_path=plan_path,
            source_root=self.source,
            quarantine_root=self.quarantine,
            approval_receipt_path=receipt_path,
            evaluated_at=expires + timedelta(seconds=1),
        )

        self.assertEqual(rerun["status"], "already-quarantined")
        self.assertEqual(os.lstat(lock).st_ino, lock_identity)
        self.assertEqual(tuple(self.quarantine.glob(".mf106-audit-*.json")), audits)

    def test_second_move_failure_rolls_back_but_consumes_receipt(self):
        retrieval = _write_retrieval_generation(self.source)
        bridge = _write_bridge_generation(self.source)
        plan = self.build_plan([retrieval, bridge])
        plan_path, receipt_path, evaluation_time, _expires = (
            self.write_plan_and_receipt(plan)
        )
        real_rename = cleanup._atomic_rename_noreplace
        calls = 0

        def fail_second(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fixture second move failure")
            real_rename(*args, **kwargs)

        with (
            mock.patch.object(
                cleanup,
                "_atomic_rename_noreplace",
                side_effect=fail_second,
            ),
            self.assertRaises(cleanup.CleanupApplyError) as raised,
        ):
            cleanup.apply_staging_cleanup_plan(
                plan_path=plan_path,
                source_root=self.source,
                quarantine_root=self.quarantine,
                approval_receipt_path=receipt_path,
                evaluated_at=evaluation_time,
            )

        self.assertEqual(raised.exception.disposition["status"], "failed-rolled-back")
        self.assertEqual(calls, 3)
        self.assertTrue((self.source / retrieval).exists())
        self.assertTrue((self.source / bridge).exists())
        self.assertEqual(len(tuple(self.quarantine.glob(".mf106-audit-*.json"))), 1)
        self.assertTrue((self.quarantine / cleanup._LOCK_NAME).exists())
        with self.assertRaises(MillefeuilleContractError):
            cleanup.apply_staging_cleanup_plan(
                plan_path=plan_path,
                source_root=self.source,
                quarantine_root=self.quarantine,
                approval_receipt_path=receipt_path,
                evaluated_at=evaluation_time,
            )

    def test_final_hook_root_swap_does_not_touch_replacement_namespace(self):
        retrieval = _write_retrieval_generation(self.source)
        plan = self.build_plan([retrieval])
        plan_path, receipt_path, evaluation_time, _expires = (
            self.write_plan_and_receipt(plan)
        )
        parked = self.base / "source-packs-parked"
        replacement_marker = b"replacement-private-namespace"

        def swap_root(_candidate: dict[str, object]) -> None:
            self.source.rename(parked)
            self.source.mkdir()
            (self.source / "foreign-entry").write_bytes(replacement_marker)

        try:
            with (
                mock.patch.object(
                    cleanup,
                    "_final_precommit_hook",
                    side_effect=swap_root,
                ),
                self.assertRaises(cleanup.CleanupApplyError),
            ):
                cleanup.apply_staging_cleanup_plan(
                    plan_path=plan_path,
                    source_root=self.source,
                    quarantine_root=self.quarantine,
                    approval_receipt_path=receipt_path,
                    evaluated_at=evaluation_time,
                )
            self.assertEqual(
                (self.source / "foreign-entry").read_bytes(),
                replacement_marker,
            )
            self.assertTrue((parked / retrieval).exists())
            self.assertFalse(
                (self.quarantine / plan["candidates"][0]["quarantine_name"]).exists()
            )
        finally:
            if parked.exists():
                _make_tree_writable(self.source)
                shutil.rmtree(self.source)
                parked.rename(self.source)

    def test_final_hook_quarantine_root_swap_preserves_replacement_namespace(self):
        retrieval = _write_retrieval_generation(self.source)
        plan = self.build_plan([retrieval])
        plan_path, receipt_path, evaluation_time, _expires = (
            self.write_plan_and_receipt(plan)
        )
        parked = self.base / "quarantine-parked"
        replacement_marker = b"replacement-quarantine-private"

        def swap_quarantine(_candidate: dict[str, object]) -> None:
            self.quarantine.rename(parked)
            self.quarantine.mkdir()
            (self.quarantine / "foreign-entry").write_bytes(replacement_marker)

        try:
            with (
                mock.patch.object(
                    cleanup,
                    "_final_precommit_hook",
                    side_effect=swap_quarantine,
                ),
                self.assertRaises(cleanup.CleanupApplyError),
            ):
                cleanup.apply_staging_cleanup_plan(
                    plan_path=plan_path,
                    source_root=self.source,
                    quarantine_root=self.quarantine,
                    approval_receipt_path=receipt_path,
                    evaluated_at=evaluation_time,
                )
            self.assertEqual(
                (self.quarantine / "foreign-entry").read_bytes(),
                replacement_marker,
            )
            self.assertTrue((self.source / retrieval).exists())
            self.assertTrue((parked / cleanup._LOCK_NAME).exists())
            self.assertEqual(len(tuple(parked.glob(".mf106-audit-*.json"))), 1)
        finally:
            if parked.exists():
                _make_tree_writable(self.quarantine)
                shutil.rmtree(self.quarantine)
                parked.rename(self.quarantine)

    def test_final_hook_parent_swap_and_candidate_mutation_fail_before_move(self):
        for race in ("parent-swap", "candidate-mutation"):
            with self.subTest(race=race):
                if race != "parent-swap":
                    _make_tree_writable(self.base)
                    shutil.rmtree(self.source)
                    shutil.rmtree(self.quarantine)
                    self.source.mkdir()
                    self.quarantine.mkdir()
                retrieval = _write_retrieval_generation(self.source)
                plan = self.build_plan([retrieval])
                plan_path, receipt_path, evaluation_time, _expires = (
                    self.write_plan_and_receipt(
                        plan,
                        receipt_id=f"receipt-mf-106-{race}",
                    )
                )
                generation = self.source / retrieval
                batch = generation.parent
                parked = batch.with_name(f"{batch.name}-parked")
                replacement_marker = b"foreign-replacement-private"

                def race_hook(
                    _candidate: dict[str, object],
                    *,
                    race_name: str = race,
                    batch_path: Path = batch,
                    parked_path: Path = parked,
                    generation_path: Path = generation,
                    marker: bytes = replacement_marker,
                ) -> None:
                    if race_name == "parent-swap":
                        batch_path.rename(parked_path)
                        batch_path.mkdir()
                        replacement = batch_path / generation_path.name
                        replacement.mkdir()
                        (replacement / "foreign-entry").write_bytes(marker)
                    else:
                        os.chmod(generation_path, 0o700)
                        unexpected = generation_path / "foreign-entry"
                        unexpected.write_bytes(marker)
                        _make_read_only(unexpected)
                        _make_read_only(generation_path)

                try:
                    with (
                        mock.patch.object(
                            cleanup,
                            "_final_precommit_hook",
                            side_effect=race_hook,
                        ),
                        self.assertRaises(cleanup.CleanupApplyError),
                    ):
                        cleanup.apply_staging_cleanup_plan(
                            plan_path=plan_path,
                            source_root=self.source,
                            quarantine_root=self.quarantine,
                            approval_receipt_path=receipt_path,
                            evaluated_at=evaluation_time,
                        )
                    if race == "parent-swap":
                        self.assertEqual(
                            (batch / generation.name / "foreign-entry").read_bytes(),
                            replacement_marker,
                        )
                        self.assertTrue((parked / generation.name).exists())
                    else:
                        self.assertEqual(
                            (generation / "foreign-entry").read_bytes(),
                            replacement_marker,
                        )
                    self.assertFalse(
                        (
                            self.quarantine / plan["candidates"][0]["quarantine_name"]
                        ).exists()
                    )
                finally:
                    if parked.exists():
                        _make_tree_writable(batch)
                        shutil.rmtree(batch)
                        parked.rename(batch)

    def test_concurrent_destination_is_preserved_and_receipt_is_consumed(self):
        retrieval = _write_retrieval_generation(self.source)
        plan = self.build_plan([retrieval])
        plan_path, receipt_path, evaluation_time, _expires = (
            self.write_plan_and_receipt(plan)
        )
        destination = self.quarantine / plan["candidates"][0]["quarantine_name"]
        marker = b"foreign-quarantine-private"

        def create_destination(_candidate: dict[str, object]) -> None:
            destination.mkdir()
            (destination / "foreign-entry").write_bytes(marker)

        with (
            mock.patch.object(
                cleanup,
                "_final_precommit_hook",
                side_effect=create_destination,
            ),
            self.assertRaises(cleanup.CleanupApplyError) as raised,
        ):
            cleanup.apply_staging_cleanup_plan(
                plan_path=plan_path,
                source_root=self.source,
                quarantine_root=self.quarantine,
                approval_receipt_path=receipt_path,
                evaluated_at=evaluation_time,
            )

        self.assertEqual(raised.exception.disposition["status"], "failed-rolled-back")
        self.assertEqual((destination / "foreign-entry").read_bytes(), marker)
        self.assertTrue((self.source / retrieval).exists())
        self.assertEqual(len(tuple(self.quarantine.glob(".mf106-audit-*.json"))), 1)

    def test_held_lock_name_replacement_is_detected_through_pinned_root(self):
        retrieval = _write_retrieval_generation(self.source)
        plan = self.build_plan([retrieval])
        plan_path, receipt_path, evaluation_time, _expires = (
            self.write_plan_and_receipt(plan)
        )
        parked_lock = self.base / "held-lock-parked"

        def replace_lock_name(_candidate: dict[str, object]) -> None:
            held_name = self.quarantine / cleanup._LOCK_NAME
            held_name.rename(parked_lock)
            held_name.write_bytes(b"0")

        with (
            mock.patch.object(
                cleanup,
                "_final_precommit_hook",
                side_effect=replace_lock_name,
            ),
            self.assertRaises(cleanup.CleanupApplyError) as raised,
        ):
            cleanup.apply_staging_cleanup_plan(
                plan_path=plan_path,
                source_root=self.source,
                quarantine_root=self.quarantine,
                approval_receipt_path=receipt_path,
                evaluated_at=evaluation_time,
            )

        self.assertEqual(raised.exception.disposition["status"], "failed-rolled-back")
        self.assertEqual(parked_lock.read_bytes(), b"0")
        self.assertEqual(
            (self.quarantine / cleanup._LOCK_NAME).read_bytes(),
            b"0",
        )
        self.assertTrue((self.source / retrieval).exists())
        self.assertFalse(
            (self.quarantine / plan["candidates"][0]["quarantine_name"]).exists()
        )

    def test_locked_namespace_scan_requires_reserved_lock_name(self):
        roots = cleanup._root_context(self.source, self.quarantine)
        parked_lock = self.base / "held-lock-parked"
        with cleanup._pin_apply_roots(roots) as pinned:
            lock_fd = cleanup._acquire_pinned_maintenance_lock(
                quarantine_fd=pinned.quarantine_fd,
            )
            try:
                (self.quarantine / cleanup._LOCK_NAME).rename(parked_lock)
                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "maintenance lock changed while held",
                ):
                    cleanup._cleanup_quarantine_namespace(
                        self.quarantine,
                        held_lock_fd=lock_fd,
                        held_lock_parent_fd=pinned.quarantine_fd,
                    )
            finally:
                cleanup._release_maintenance_lock(lock_fd)

        self.assertEqual(parked_lock.read_bytes(), b"0")

    def test_in_place_drift_after_move_cannot_be_reported_as_rolled_back(self):
        bridge = _write_bridge_generation(self.source)
        plan = self.build_plan([bridge])
        plan_path, receipt_path, evaluation_time, _expires = (
            self.write_plan_and_receipt(plan)
        )
        real_rename = cleanup._atomic_rename_noreplace
        drifted_bytes = b"Z" * len(PRIVATE_MARKER.encode("utf-8"))
        injected = False

        def drift_after_move(*args: object, **kwargs: object) -> None:
            nonlocal injected
            real_rename(*args, **kwargs)
            if injected:
                return
            injected = True
            destination = Path(args[1])
            asset = destination / "private-paper.pdf"
            os.chmod(destination, 0o700)
            os.chmod(asset, 0o600)
            asset.write_bytes(drifted_bytes)
            _make_read_only(asset)
            _make_read_only(destination)

        with (
            mock.patch.object(
                cleanup,
                "_atomic_rename_noreplace",
                side_effect=drift_after_move,
            ),
            self.assertRaises(cleanup.CleanupApplyError) as raised,
        ):
            cleanup.apply_staging_cleanup_plan(
                plan_path=plan_path,
                source_root=self.source,
                quarantine_root=self.quarantine,
                approval_receipt_path=receipt_path,
                evaluated_at=evaluation_time,
            )

        disposition = raised.exception.disposition
        self.assertEqual(disposition["status"], "recovery-required")
        self.assertEqual(disposition["failure_code"], "rollback-blocked")
        self.assertEqual(disposition["candidates"][0]["status"], "rollback-blocked")
        self.assertEqual(
            (self.source / bridge / "private-paper.pdf").read_bytes(),
            drifted_bytes,
        )
        self.assertFalse(
            (self.quarantine / plan["candidates"][0]["quarantine_name"]).exists()
        )
        self.assertEqual(len(tuple(self.quarantine.glob(".mf106-audit-*.json"))), 1)

    def test_active_persistent_lock_refuses_without_audit_and_remains_reserved(self):
        retrieval = _write_retrieval_generation(self.source)
        plan = self.build_plan([retrieval])
        plan_path, receipt_path, evaluation_time, _expires = (
            self.write_plan_and_receipt(plan)
        )
        roots = cleanup._root_context(self.source, self.quarantine)
        with cleanup._pin_apply_roots(roots) as pinned:
            lock_fd = cleanup._acquire_pinned_maintenance_lock(
                quarantine_fd=pinned.quarantine_fd,
            )
            try:
                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "active maintenance lock",
                ):
                    cleanup.apply_staging_cleanup_plan(
                        plan_path=plan_path,
                        source_root=self.source,
                        quarantine_root=self.quarantine,
                        approval_receipt_path=receipt_path,
                        evaluated_at=evaluation_time,
                    )
            finally:
                cleanup._release_maintenance_lock(lock_fd)

        lock = self.quarantine / cleanup._LOCK_NAME
        self.assertEqual(lock.read_bytes(), b"0")
        self.assertEqual(tuple(self.quarantine.glob(".mf106-audit-*.json")), ())
        self.assertTrue((self.source / retrieval).exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
