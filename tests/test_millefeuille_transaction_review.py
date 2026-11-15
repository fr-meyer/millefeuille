"""Offline synthetic regressions for transaction review R1, R2 and R3."""

from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import classification as c
from millefeuille.domain import metadata_transaction as t
from millefeuille.domain import stage_runtime as s
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import RootArtifactReader
from tests.platform_capabilities import requires_posix_batch_publication
from tests.test_millefeuille_metadata_transaction import (
    ClassificationWritebackIdempotenceRegression as Pipeline,
)
from tests.test_millefeuille_stage_cli import PAPER_ID, RUN_ID, _prepare_fixture_run


@requires_posix_batch_publication
class TransactionReviewRegression(unittest.TestCase):
    def prepare(self, root, evidence):
        return c._prepare_classification_from_evidence(
            evidence_path=evidence,
            source_pack_root=root,
            run_id=RUN_ID,
            paper_id=PAPER_ID,
            item_key=None,
            default_profile=None,
        )

    def test_changed_decision_preserves_identical_preview_inode(self):
        for field in ("confidence", "rejected_alternatives"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                helper = Pipeline()
                root, run, evidence = helper._complete_pipeline(tmp)
                preview = run / c.WRITEBACK_PREVIEW_REF
                before = t.capture_metadata_binding(preview)
                payload = json.loads(evidence.read_text())
                if field == "confidence":
                    payload[field] = "medium"
                else:
                    payload[field][0]["reason"] = "updated synthetic reason"
                evidence.write_text(json.dumps(payload))
                result = helper._classify(root, evidence)
                self.assertEqual(result.status, "classified")
                self.assertEqual(t.capture_metadata_binding(preview), before)
                decision = json.loads(result.decision_json_path.read_text())
                self.assertEqual(decision[field], payload[field])
                self.assertFalse((run / t.PENDING).exists())

    def test_resolver_rejects_drift_after_parse_for_each_reader_and_member(self):
        for reader_mode in (False, True):
            for name in ("stage-manifest.json", "artifact-index.json"):
                with (
                    self.subTest(reader=reader_mode, name=name),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    root, run = _prepare_fixture_run(tmp)
                    path = run / name
                    original = s.capture_metadata_binding
                    calls = []
                    foreign = path.read_bytes() + b" "

                    def capture(
                        target,
                        path=path,
                        calls=calls,
                        foreign=foreign,
                        original=original,
                    ):
                        if Path(target) == path:
                            calls.append(True)
                            if len(calls) == 2:
                                path.write_bytes(foreign)
                        return original(target)

                    context = RootArtifactReader(root) if reader_mode else nullcontext()
                    with (
                        context as reader,
                        patch.object(s, "capture_metadata_binding", capture),
                        self.assertRaisesRegex(
                            MillefeuilleContractError, "changed while parsing"
                        ),
                    ):
                        s.resolve_run_artifacts(
                            source_pack_root=root,
                            paper_id=PAPER_ID,
                            run_id=RUN_ID,
                            artifact_reader=reader,
                        )
                    self.assertEqual(path.read_bytes(), foreign)

    def test_revoked_or_changed_inputs_reject_without_partial_outputs(self):
        for kind in ("acceptance", "evidence", "source", "manifest"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root, run, evidence = Pipeline()._complete_pipeline(tmp)
                prepared = self.prepare(root, evidence)
                outputs = c.render_classification_outputs(prepared)
                before = {ref: (run / ref).read_bytes() for ref in outputs}
                targets = {
                    "acceptance": run / "reports/acceptance-summary.json",
                    "evidence": evidence,
                    "source": run / prepared.decision.evidence_refs[0],
                    "manifest": prepared.resolved.source_pack_dir / "manifest.json",
                }
                target = targets[kind]
                payload = json.loads(target.read_text())
                if kind == "acceptance":
                    payload["status"] = "needs-review"
                else:
                    payload["foreign-update"] = True
                foreign = json.dumps(payload).encode()
                target.write_bytes(foreign)
                with self.assertRaisesRegex(MillefeuilleContractError, "input drift"):
                    c._persist_classification(prepared)
                self.assertEqual(target.read_bytes(), foreign)
                self.assertEqual(
                    before, {ref: (run / ref).read_bytes() for ref in outputs}
                )
                self.assertFalse((run / t.PENDING).exists())

    def test_acceptance_revoked_during_commit_rolls_back_owned_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, run, evidence = Pipeline()._complete_pipeline(tmp)
            payload = json.loads(evidence.read_text())
            payload["confidence"] = "medium"
            evidence.write_text(json.dumps(payload))
            prepared = self.prepare(root, evidence)
            before = {
                ref: (run / ref).read_bytes()
                for ref in c.render_classification_outputs(prepared)
            }
            acceptance = run / "reports/acceptance-summary.json"
            rename = t._rename_entry
            injected = []

            def revoke(*args, **kwargs):
                rename(*args, **kwargs)
                if not injected:
                    payload = json.loads(acceptance.read_text())
                    payload["status"] = "needs-review"
                    acceptance.write_text(json.dumps(payload))
                    injected.append(True)

            with (
                patch.object(t, "_rename_entry", revoke),
                self.assertRaisesRegex(MillefeuilleContractError, "recovery required"),
            ):
                c._persist_classification(prepared)
            self.assertEqual(
                json.loads(acceptance.read_text())["status"], "needs-review"
            )
            self.assertEqual(before, {ref: (run / ref).read_bytes() for ref in before})
            self.assertTrue((run / t.PENDING).exists())

    def test_unchanged_output_foreign_edit_is_not_overwritten_on_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            stable, changed = run / "stable", run / "changed"
            stable.write_text("same")
            changed.write_text("old")
            rename = t._rename_entry
            injected = []

            def drift(*args, **kwargs):
                rename(*args, **kwargs)
                if not injected:
                    stable.write_text("foreign")
                    injected.append(True)

            with (
                patch.object(t, "_rename_entry", drift),
                self.assertRaisesRegex(MillefeuilleContractError, "recovery required"),
            ):
                t.write_metadata_transaction(run, {"stable": "same", "changed": "new"})
            self.assertEqual(stable.read_text(), "foreign")
            self.assertEqual(changed.read_text(), "old")

    def test_acceptance_change_during_preparation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, run, evidence = Pipeline()._complete_pipeline(tmp)
            original = c._load_classification_evidence
            acceptance = run / "reports/acceptance-summary.json"

            def revoke(path):
                value = original(path)
                payload = json.loads(acceptance.read_text())
                payload["status"] = "needs-review"
                acceptance.write_text(json.dumps(payload))
                return value

            with (
                patch.object(c, "_load_classification_evidence", revoke),
                self.assertRaisesRegex(MillefeuilleContractError, "input drift"),
            ):
                self.prepare(root, evidence)
            self.assertFalse((run / t.PENDING).exists())

    def test_unchanged_preview_as_explicit_evidence_allows_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Pipeline()
            root, run, evidence = helper._complete_pipeline(tmp)
            payload = json.loads(evidence.read_text())
            payload["evidence_refs"].append(str(c.WRITEBACK_PREVIEW_REF))
            payload["confidence"] = "medium"
            evidence.write_text(json.dumps(payload))
            preview = run / c.WRITEBACK_PREVIEW_REF
            before = t.capture_metadata_binding(preview)
            self.assertEqual(helper._classify(root, evidence).status, "classified")
            self.assertEqual(t.capture_metadata_binding(preview), before)
