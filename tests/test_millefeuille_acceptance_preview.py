"""Read-only inspection is distinct from persisted or semantic acceptance."""

from contextlib import ExitStack
from io import StringIO
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain import acceptance as api
from millefeuille.domain.millefeuille import (
    AcceptanceCheckRecord,
    AcceptanceCheckStatus,
    AcceptanceStatus,
    MillefeuilleContractError,
)
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_multi_source_handoff import _check, _fixture
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_handoff_jsonl,
)


def _census(root):
    return {
        str(p.relative_to(root)): (
            p.read_bytes() if p.is_file() else None,
            p.stat().st_mode,
            p.stat().st_mtime_ns,
        )
        for p in [root, *root.rglob("*")]
    }


@requires_secure_nofollow_writes
class AcceptancePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root, self.run = _prepare_fixture_run(self.temp.name)
        self.handoff = _write_handoff_jsonl(self.temp.name)
        self.index = self.run / "index/index-status.json"
        payload = json.loads(self.index.read_bytes())
        payload["duplicate_scan"] = {"matched_existing": False, "status": "completed"}
        self.index.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.values = {
            "source_pack_root": self.root,
            "run_id": RUN_ID,
            "paper_id": PAPER_ID,
            "handoff_path": self.handoff,
        }

    def _preview(self, **changes):
        return api.preview_acceptance_summary(**dict(self.values, **changes))

    def test_complete_saved_fixture_is_deterministic_without_writes_or_network(self):
        before = _census(self.base)
        with ExitStack() as stack:
            for name in (
                "_persist_acceptance_summary",
                "persist_run_artifacts",
                "write_json_object",
                "write_text",
                "write_acceptance_summary",
                "upsert_stage_record",
                "upsert_artifact_record",
                "update_artifact_index",
            ):
                stack.enter_context(
                    patch.object(api, name, side_effect=AssertionError(name))
                )
            stack.enter_context(
                patch.object(
                    socket.socket, "connect", side_effect=AssertionError("network")
                )
            )
            first = self._preview()
            self.assertEqual(first, self._preview())
        self.assertEqual(before, _census(self.base))
        self.assertEqual(first["candidate_status"], "pass")
        self.assertEqual((first["writes"], first["provider_calls"]), (0, 0))
        self.assertEqual(first["status"], "planned")
        self.assertFalse(first["recorded"])
        self.assertFalse(first["semantic_acceptance_performed"])
        self.assertFalse(first["write_authorized"])
        self.assertFalse(first["input_snapshot_bound"])
        self.assertEqual(first["scope"], "saved-structural-evidence-only")
        self.assertNotIn(
            "acceptance_summary", (self.run / "artifact-index.json").read_text()
        )

    def test_missing_handoff_requires_review_but_is_not_a_command_failure(self):
        payload = self._preview(handoff_path=None)
        self.assertEqual(payload["candidate_status"], "needs-review")
        self.assertIn("handoff:needs-review", payload["review_codes"])

    def test_missing_duplicate_scan_is_not_a_verified_negative(self):
        payload = json.loads(self.index.read_bytes())
        payload.pop("duplicate_scan", None)
        self.index.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        result = self._preview()
        self.assertEqual(result["candidate_status"], "needs-review")
        self.assertFalse(result["duplicate_scan_evidence_present"])
        self.assertIn("duplicate-scan-evidence-unavailable", result["review_codes"])

    def test_unrun_duplicate_scan_is_not_verified(self):
        payload = json.loads(self.index.read_bytes())
        payload["duplicate_scan"] = {"status": "not-run", "matched_existing": False}
        self.index.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        self.assertFalse(self._preview()["duplicate_scan_evidence_present"])

    def test_positive_duplicate_requires_review(self):
        payload = json.loads(self.index.read_bytes())
        payload["duplicate_scan"] = {"matched_existing": True, "status": "completed"}
        self.index.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        result = self._preview()
        self.assertEqual(result["candidate_status"], "needs-review")
        self.assertIn("duplicate-scan:needs-review", result["review_codes"])

    def test_missing_card_is_review_not_success(self):
        (self.run / "cards/paper-card.json").unlink()
        result = self._preview()
        self.assertEqual(result["candidate_status"], "needs-review")
        self.assertIn("card:needs-review", result["review_codes"])

    def test_identity_drift_is_review_and_never_persisted(self):
        path = self.run / "cards/paper-card.json"
        card = json.loads(path.read_bytes())
        card["paper_id"] = "zotero-OTHER"
        path.write_text(json.dumps(card))
        before = _census(self.base)
        with self.assertRaises(MillefeuilleContractError):
            self._preview()
        self.assertEqual(before, _census(self.base))

    def test_invalid_metadata_error_is_fixed_and_content_free(self):
        secret = "SYNTHETIC_PRIVATE_CONTENT_71591"
        (self.run / "artifact-index.json").write_text(secret)
        before = _census(self.base)
        with self.assertRaisesRegex(
            MillefeuilleContractError,
            "^acceptance-preview: saved-evidence-invalid-or-unavailable$",
        ):
            self._preview()
        self.assertEqual(before, _census(self.base))

    def test_os_failure_error_is_fixed_and_content_free(self):
        with (
            patch.object(
                api, "resolve_run_artifacts", side_effect=PermissionError("PRIVATE")
            ),
            self.assertRaises(MillefeuilleContractError) as caught,
        ):
            self._preview()
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_cli_json_and_human_outputs_are_nonrecording(self):
        args = [
            "acceptance-preview",
            "--source-pack-root",
            str(self.root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            RUN_ID,
            "--handoff",
            str(self.handoff),
        ]
        before = _census(self.base)
        for suffix in (["--json"], []):
            out, err = StringIO(), StringIO()
            self.assertEqual(run_stage_cli(args + suffix, stdout=out, stderr=err), 0)
            self.assertEqual(err.getvalue(), "")
            self.assertNotIn("Native text", out.getvalue())
            if suffix:
                self.assertEqual(json.loads(out.getvalue())["writes"], 0)
            else:
                self.assertIn("status: planned", out.getvalue())
        self.assertEqual(before, _census(self.base))

    def test_cli_errors_are_private_and_emit_no_success_payload(self):
        args = [
            "acceptance-preview",
            "--source-pack-root",
            str(self.root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            RUN_ID,
            "--json",
        ]
        with patch.object(api, "resolve_run_artifacts", side_effect=OSError("PRIVATE")):
            out, err = StringIO(), StringIO()
            self.assertEqual(run_stage_cli(args, stdout=out, stderr=err), 2)
        self.assertEqual(out.getvalue(), "")
        self.assertNotIn("PRIVATE", err.getvalue())

    def test_preview_has_no_approval_write_or_batch_flags(self):
        args = [
            "acceptance-preview",
            "--source-pack-root",
            str(self.root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            RUN_ID,
        ]
        for option in (
            "--write",
            "--approval-receipt=x",
            "--mode=approved-live",
            "--batch-manifest=x",
        ):
            with self.subTest(option=option), patch("sys.stderr", StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    run_stage_cli(args + [option])
                self.assertEqual(caught.exception.code, 2)

    def test_legacy_writer_still_records_the_same_builder_result(self):
        resolved = api.resolve_run_artifacts(
            source_pack_root=self.root, run_id=RUN_ID, paper_id=PAPER_ID
        )
        summary = api._build_acceptance_summary(
            resolved=resolved, handoff_path=self.handoff, duplicate_scan_path=None
        )
        self._preview()
        result = api.write_acceptance_summary(**self.values)
        self.assertEqual(
            json.loads(result.summary_path.read_bytes()), summary.to_dict()
        )


class PreviewProjectionTests(unittest.TestCase):
    def test_projection_omits_raw_notes_refs_details_and_unknown_counts(self):
        secret = "SYNTHETIC_PRIVATE_CONTENT_51931"
        summary = SimpleNamespace(
            paper_id="zotero-ITEM1",
            run_id="run-test",
            status=AcceptanceStatus.PASS,
            counts={"summary": 2, secret: 3, "index": True, "card": -1},
            review_reasons=[secret],
            checks=[
                AcceptanceCheckRecord(
                    name="source-pack",
                    status=AcceptanceCheckStatus.PASSED,
                    refs=[secret],
                    notes=[secret],
                )
            ],
            duplicate_scan={"matched_existing": False, "title": secret},
        )
        with (
            patch.object(api, "resolve_run_artifacts"),
            patch.object(api, "_build_acceptance_summary", return_value=summary),
        ):
            result = api.preview_acceptance_summary(
                source_pack_root="/synthetic", run_id="run-test"
            )
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result["counts"], {"summary": 2})

    @requires_secure_nofollow_writes
    def test_two_source_handoff_and_upstream_feed_preview_without_effects(self):
        # Real native two-PDF/three-page verification and two-row identity join.
        # Downstream projection is isolated; this is not full live-paper acceptance.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolved, rows = _fixture(root)
            handoff, _ = _check(root, resolved, rows)
            pack = root / "source-packs/zotero/zotero-ITEM1234"
            manifest = json.loads((pack / "manifest.json").read_bytes())
            resolved.source_pack_dir = pack
            resolved.paper_id = manifest["paper_id"]
            resolved.source_hash = manifest["source_hash"]
            resolved.source_pack_root = root / "source-packs"
            resolved.run_id = "run-two-source"
            resolved.stage_manifest = SimpleNamespace(stages=[])

            def passed(name):
                return AcceptanceCheckRecord(
                    name=name, status=AcceptanceCheckStatus.PASSED
                )

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(api, "resolve_run_artifacts", return_value=resolved)
                )
                stack.enter_context(
                    patch.object(
                        api,
                        "_load_verified_multi_source_profile",
                        return_value="research-default",
                    )
                )
                stack.enter_context(
                    patch.object(
                        api,
                        "_load_run_scoped_summary_check",
                        return_value=passed("summarize"),
                    )
                )
                stack.enter_context(
                    patch.object(
                        api, "_load_run_scoped_card_check", return_value=passed("card")
                    )
                )
                stack.enter_context(
                    patch.object(
                        api,
                        "_load_run_scoped_index_check",
                        return_value=(
                            passed("index"),
                            {"duplicate_scan": {"matched_existing": False}},
                        ),
                    )
                )
                values = {
                    "source_pack_root": resolved.source_pack_root,
                    "run_id": resolved.run_id,
                    "handoff_path": root / "handoff.jsonl",
                }
                before = _census(root)
                result = api.preview_acceptance_summary(**values)
                self.assertEqual(result["candidate_status"], "pass")
                self.assertEqual(result["counts"]["handoff_rows"], 2)
                self.assertEqual(result["counts"]["native_extraction"], 1)
                self.assertEqual(before, _census(root))
                for invalid in (rows[:1], [rows[0], rows[0]]):
                    _check(root, resolved, invalid)
                    result = api.preview_acceptance_summary(**values)
                    self.assertEqual(result["candidate_status"], "needs-review")
                    self.assertIn("handoff:needs-review", result["review_codes"])
                _check(root, resolved, rows)
                source = next((pack / "extractions/native/sources").glob("*.md"))
                source.write_bytes(source.read_bytes() + b"synthetic drift")
                with self.assertRaises(MillefeuilleContractError):
                    api.preview_acceptance_summary(**values)


if __name__ == "__main__":
    unittest.main()
