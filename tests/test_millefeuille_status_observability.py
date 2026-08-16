"""Strict read-only progressive status joins and local observation evidence."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from millefeuille.cli.artifacts import run_artifact_cli
from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.status_observability import (
    STATUS_OBSERVATION_SCHEMA_VERSION,
    STATUS_OBSERVATION_SOURCE,
    _canonical_source_zotero_version,
    build_status_report,
    compute_status_digest,
    compute_status_observation_digest,
    load_status_observation,
)
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_classification_evidence_json,
    _write_handoff_jsonl,
)

_SPEC_DIR = Path(__file__).resolve().parents[1] / "specs/millefeuille-pipeline"
_BLOCKED_INDEX = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/millefeuille_artifact_index/blocked/artifact-index.json"
)

_STATUS_ARTIFACTS = frozenset(
    {
        "stage_manifest",
        "selected_fulltext",
        "hierarchical_summary",
        "paper_card_json",
        "retrieval_index_status",
        "acceptance_summary",
        "classification_plan",
        "classification_decision",
        "zotero_writeback_plan",
    }
)


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _prepare_complete_preview(tempdir: str) -> tuple[Path, Path]:
    source_pack_root, run_dir = _prepare_fixture_run(tempdir)
    handoff = _write_handoff_jsonl(tempdir)
    classification = _write_classification_evidence_json(tempdir)
    commands = (
        [
            "acceptance",
            "--source-pack-root",
            str(source_pack_root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            RUN_ID,
            "--handoff",
            str(handoff),
        ],
        [
            "classify",
            "--source-pack-root",
            str(source_pack_root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            RUN_ID,
            "--evidence",
            str(classification),
        ],
        [
            "writeback",
            "--source-pack-root",
            str(source_pack_root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            RUN_ID,
        ],
    )
    for command in commands:
        if run_stage_cli(command, stdout=StringIO(), stderr=StringIO()) != 0:
            raise AssertionError("fixture stage command failed")

    stage_path = run_dir / "stage-manifest.json"
    index_path = run_dir / "artifact-index.json"
    retrieval_path = run_dir / "index/index-status.json"
    stage_manifest = _read_json(stage_path)
    artifact_index = _read_json(index_path)
    retrieval = _read_json(retrieval_path)
    stages = {record["name"]: record for record in stage_manifest["stages"]}

    stages["openkb-add"]["status"] = "skipped"
    artifact_index["stages"]["openkb-add"]["status"] = "skipped"
    artifact_index["indexes"] = [
        {
            "lane": lane["lane"],
            "status": lane["status"],
            **({"skip_reason": lane["skip_reason"]} if "skip_reason" in lane else {}),
        }
        for lane in retrieval["lanes"]
    ]
    for name, record in artifact_index["artifacts"].items():
        if name not in _STATUS_ARTIFACTS:
            continue
        outputs = stages[record["stage"]]["outputs"]
        if record["ref"] not in outputs:
            outputs.append(record["ref"])

    _write_json(stage_path, stage_manifest)
    _write_json(index_path, artifact_index)
    return source_pack_root, run_dir


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _observation_payload(
    *,
    kind: str,
    source_hash: str,
    summary: dict[str, object],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": STATUS_OBSERVATION_SCHEMA_VERSION,
        "kind": kind,
        "authority": {
            "source": STATUS_OBSERVATION_SOURCE,
            "authoritative": False,
        },
        "paper_id": PAPER_ID,
        "run_id": RUN_ID,
        "source_hash": source_hash,
        "summary": summary,
    }
    payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_status_observation_digest(payload),
    }
    return payload


class TestStatusCanonicalJoin(unittest.TestCase):
    def test_preview_join_is_deterministic_read_only_and_sanitized(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_complete_preview(tempdir)
            before = _snapshot(root)

            first = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
            )
            second = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
            )

            self.assertEqual(first, second)
            self.assertEqual(before, _snapshot(root))
            self.assertEqual(first["status"]["state"], "preview-complete")
            self.assertTrue(first["status"]["complete"])
            self.assertFalse(first["status"]["observationally_ready"])
            self.assertFalse(first["status"]["authoritative_live_complete"])
            self.assertEqual(
                first["authority"],
                {
                    "source": STATUS_OBSERVATION_SOURCE,
                    "authoritative": False,
                },
            )
            serialized = json.dumps(first, sort_keys=True)
            for marker in (
                tempdir,
                "Selected text",
                "Complete Fixture Paper",
                "artifact_root",
                "ZOTERO_READ_KEY",
                "https://",
            ):
                self.assertNotIn(marker, serialized)

    def test_internal_parent_traversal_is_rejected_before_normalization(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_complete_preview(tempdir)
            index_path = run_dir / "artifact-index.json"
            stage_path = run_dir / "stage-manifest.json"
            artifact_index = _read_json(index_path)
            stage_manifest = _read_json(stage_path)
            artifact_index["artifacts"]["retrieval_index_status"]["ref"] = (
                "cards/../index/index-status.json"
            )
            index_stage = next(
                record
                for record in stage_manifest["stages"]
                if record["name"] == "index"
            )
            index_stage["outputs"] = [
                "cards/../index/index-status.json"
                if value == "index/index-status.json"
                else value
                for value in index_stage["outputs"]
            ]
            _write_json(index_path, artifact_index)
            _write_json(stage_path, stage_manifest)

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "portable bounded relative ref",
            ):
                build_status_report(
                    source_pack_root=root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

    def test_index_lanes_refs_and_stage_outputs_are_exact(self):
        mutations = {
            "lane cardinality": lambda index, retrieval, stages: index[
                "indexes"
            ].append(
                {
                    "lane": "condb",
                    "status": "skipped",
                    "skip_reason": "unrelated",
                }
            ),
            "summary ref": lambda index, retrieval, stages: retrieval.__setitem__(
                "summary_ref", "../cards/paper-card.json"
            ),
            "stage output": lambda index, retrieval, stages: stages["index"][
                "outputs"
            ].remove("index/index-status.json"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tempdir:
                root, run_dir = _prepare_complete_preview(tempdir)
                index_path = run_dir / "artifact-index.json"
                retrieval_path = run_dir / "index/index-status.json"
                stage_path = run_dir / "stage-manifest.json"
                index = _read_json(index_path)
                retrieval = _read_json(retrieval_path)
                stage = _read_json(stage_path)
                stages = {record["name"]: record for record in stage["stages"]}
                mutate(index, retrieval, stages)
                _write_json(index_path, index)
                _write_json(retrieval_path, retrieval)
                _write_json(stage_path, stage)

                with self.assertRaises(MillefeuilleContractError):
                    build_status_report(
                        source_pack_root=root,
                        paper_id=PAPER_ID,
                        run_id=RUN_ID,
                    )

    def test_classification_plan_binds_one_exact_decision(self):
        mutations = {
            "decision ref": lambda plan: plan["papers"][0].__setitem__(
                "decision_ref", "classification/decision-records/other.json"
            ),
            "mode": lambda plan: plan.__setitem__("mode", "review"),
            "taxonomy": lambda plan: plan.__setitem__(
                "taxonomy_version", "taxonomy-other"
            ),
            "status": lambda plan: plan["papers"][0].__setitem__(
                "status", "needs-review"
            ),
            "second paper": lambda plan: plan["papers"].append(
                deepcopy(plan["papers"][0])
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tempdir:
                root, run_dir = _prepare_complete_preview(tempdir)
                plan_path = run_dir / "classification/classification-plan.json"
                plan = _read_json(plan_path)
                mutate(plan)
                _write_json(plan_path, plan)
                with self.assertRaises(MillefeuilleContractError):
                    build_status_report(
                        source_pack_root=root,
                        paper_id=PAPER_ID,
                        run_id=RUN_ID,
                    )

    def test_writeback_index_plan_ref_is_exact(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_complete_preview(tempdir)
            index_path = run_dir / "artifact-index.json"
            artifact_index = _read_json(index_path)
            artifact_index["zotero_writeback"]["plan_ref"] = (
                "classification/zotero-writeback-preview.json"
            )
            _write_json(index_path, artifact_index)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "plan_ref drifts",
            ):
                build_status_report(
                    source_pack_root=root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

    def test_classification_preview_is_joined_without_reading_preview_payload(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_complete_preview(tempdir)
            index_path = run_dir / "artifact-index.json"
            stage_path = run_dir / "stage-manifest.json"
            artifact_index = _read_json(index_path)
            stage_manifest = _read_json(stage_path)
            artifact_index["artifacts"].pop("zotero_writeback_plan")
            artifact_index["zotero_writeback"] = {
                "mode": "preview",
                "status": "previewed",
                "plan_ref": "classification/zotero-writeback-preview.json",
            }
            artifact_index["stages"]["writeback"]["status"] = "skipped"
            writeback_stage = next(
                record
                for record in stage_manifest["stages"]
                if record["name"] == "writeback"
            )
            writeback_stage["status"] = "skipped"
            writeback_stage["outputs"] = []
            preview_path = run_dir / "classification/zotero-writeback-preview.json"
            preview = _read_json(preview_path)
            preview["note_markdown"] = "private fixture marker that must not emit"
            _write_json(preview_path, preview)
            _write_json(index_path, artifact_index)
            _write_json(stage_path, stage_manifest)

            report = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
            )
            self.assertTrue(report["status"]["complete"])
            self.assertEqual(report["zotero_writeback"]["state"], "indexed-preview")
            self.assertNotIn("private fixture marker", json.dumps(report))


class TestStatusObservationJoins(unittest.TestCase):
    def _source_hash(self, run_dir: Path) -> str:
        source_pack = _read_json(run_dir / "artifact-index.json")["source_pack"]
        return str(source_pack["source_hash"])

    def _write_envelope(
        self,
        tempdir: str,
        *,
        run_dir: Path,
        kind: str,
        summary: dict[str, object],
    ) -> Path:
        path = Path(tempdir) / f"{kind}.json"
        _write_json(
            path,
            _observation_payload(
                kind=kind,
                source_hash=self._source_hash(run_dir),
                summary=summary,
            ),
        )
        return path

    def test_provider_usage_requires_exact_canonical_provenance(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_complete_preview(tempdir)
            provenance = {
                "schema_version": "millefeuille-model-provenance/v0.1",
                "profile": "fixture-profile",
                "stage": "summarize_full_paper",
                "requested_model": "fixture-requested",
                "resolved_model": "fixture-resolved",
                "provider": "fixture-provider",
                "backend": "fixture-backend",
                "reasoning_effort": None,
                "fast_mode": None,
                "prompt_version": None,
                "input_refs": ["structure/structure.json"],
                "output_refs": ["summaries/full-paper.json"],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "total_tokens": 14,
                },
                "quality_warnings": [],
            }
            provenance_path = run_dir / "model-provenance.json"
            _write_json(provenance_path, provenance)
            index_path = run_dir / "artifact-index.json"
            stage_path = run_dir / "stage-manifest.json"
            artifact_index = _read_json(index_path)
            stage_manifest = _read_json(stage_path)
            artifact_index["artifacts"]["model_provenance_record"] = {
                "kind": "model-provenance-record",
                "ref": "model-provenance.json",
                "format": "json",
                "stage": "summarize",
                "private_content": False,
            }
            summarize = next(
                record
                for record in stage_manifest["stages"]
                if record["name"] == "summarize"
            )
            summarize["outputs"].append("model-provenance.json")
            _write_json(index_path, artifact_index)
            _write_json(stage_path, stage_manifest)

            missing = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
            )
            self.assertIn(
                "provider-usage observation: not-observed",
                missing["status"]["blocking_items"],
            )
            summary = {
                "status": "within-approved-limits",
                "record_digest": _canonical_digest(provenance),
                "call_count": 1,
                "input_tokens": 10,
                "output_tokens": 4,
                "total_tokens": 14,
            }
            envelope = self._write_envelope(
                tempdir,
                run_dir=run_dir,
                kind="provider-usage",
                summary=summary,
            )
            joined = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
                evidence_paths=[envelope],
            )
            self.assertTrue(joined["status"]["complete"])
            serialized = json.dumps(joined)
            self.assertNotIn("fixture-provider", serialized)
            self.assertNotIn("fixture-resolved", serialized)

            drifted = dict(summary)
            drifted["input_tokens"] = 11
            drifted["total_tokens"] = 15
            drifted_path = self._write_envelope(
                tempdir,
                run_dir=run_dir,
                kind="provider-usage",
                summary=drifted,
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "drifts from canonical local evidence",
            ):
                build_status_report(
                    source_pack_root=root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                    evidence_paths=[drifted_path],
                )

    def test_written_index_lane_requires_exact_ledger_observation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_complete_preview(tempdir)
            index_path = run_dir / "artifact-index.json"
            stage_path = run_dir / "stage-manifest.json"
            retrieval_path = run_dir / "index/index-status.json"
            artifact_index = _read_json(index_path)
            stage_manifest = _read_json(stage_path)
            retrieval = _read_json(retrieval_path)
            for lane in artifact_index["indexes"]:
                if lane["lane"] == "pageindex":
                    lane["status"] = "written"
                    lane["result_ref"] = "index/pageindex-ledger.json"
            for lane in retrieval["lanes"]:
                if lane["lane"] == "pageindex":
                    lane["status"] = "written"
            index_stage = next(
                record
                for record in stage_manifest["stages"]
                if record["name"] == "index"
            )
            index_stage["outputs"].append("index/pageindex-ledger.json")
            _write_json(run_dir / "index/pageindex-ledger.json", {})
            _write_json(index_path, artifact_index)
            _write_json(stage_path, stage_manifest)
            _write_json(retrieval_path, retrieval)

            missing = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
            )
            self.assertIn(
                "index-ledger observation: not-observed",
                missing["status"]["blocking_items"],
            )
            envelope = self._write_envelope(
                tempdir,
                run_dir=run_dir,
                kind="index-ledger",
                summary={
                    "status": "consistent",
                    "index_status_digest": missing["index"]["content_digest"],
                    "lanes": [{"lane": "pageindex", "status": "written"}],
                },
            )
            joined = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
                evidence_paths=[envelope],
            )
            self.assertTrue(joined["status"]["complete"])

    def test_quality_observation_exactly_matches_completion_gate(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_complete_preview(tempdir)
            body: dict[str, object] = {
                "schema_version": "millefeuille-completion-gate-result/v0.1",
                "paper_id": PAPER_ID,
                "run_id": RUN_ID,
                "source_hash": self._source_hash(run_dir),
                "status": "passed",
                "checks_total": 4,
                "checks_passed": 4,
                "checks_failed": 0,
            }
            gate = dict(body)
            gate["integrity"] = {
                "algorithm": "sha256",
                "content_digest": _canonical_digest(body),
            }
            _write_json(run_dir / "completion-gate-result.json", gate)
            index_path = run_dir / "artifact-index.json"
            stage_path = run_dir / "stage-manifest.json"
            artifact_index = _read_json(index_path)
            stage_manifest = _read_json(stage_path)
            stage_manifest["stages"].append(
                {
                    "name": "release",
                    "status": "passed",
                    "inputs": [],
                    "outputs": ["completion-gate-result.json"],
                    "manual_gate_required": False,
                }
            )
            artifact_index["stages"]["release"] = {
                "status": "passed",
                "manifest_ref": "stage-manifest.json",
            }
            artifact_index["artifacts"]["completion_gate_result"] = {
                "kind": "completion-gate-result",
                "ref": "completion-gate-result.json",
                "format": "json",
                "stage": "release",
                "private_content": False,
            }
            _write_json(index_path, artifact_index)
            _write_json(stage_path, stage_manifest)
            envelope = self._write_envelope(
                tempdir,
                run_dir=run_dir,
                kind="quality",
                summary={
                    "status": "passed",
                    "checks_total": 4,
                    "checks_passed": 4,
                    "checks_failed": 0,
                    "record_digest": gate["integrity"]["content_digest"],
                },
            )
            joined = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
                evidence_paths=[envelope],
            )
            self.assertTrue(joined["status"]["complete"])

    def test_approved_live_is_only_observationally_ready(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_complete_preview(tempdir)
            index_path = run_dir / "artifact-index.json"
            stage_path = run_dir / "stage-manifest.json"
            plan_path = run_dir / "zotero-writeback-plan.json"
            artifact_index = _read_json(index_path)
            stage_manifest = _read_json(stage_path)
            plan = _read_json(plan_path)
            stage_manifest["mode"] = "approved-live"
            plan["mode"] = "approved-live"
            plan["status"] = "written"
            plan["execution_notes"] = ["approved-live result observed locally"]
            result_body: dict[str, object] = {
                "schema_version": "millefeuille-zotero-writeback-result/v0.1",
                "paper_id": PAPER_ID,
                "run_id": RUN_ID,
                "source_hash": self._source_hash(run_dir),
                "mode": "approved-live",
                "status": "written",
                "source_item_version": 7,
                "item_version": 8,
                "operation_count": 2,
                "receipt_digest": "sha256:" + "1" * 64,
                "audit_digest": "sha256:" + "2" * 64,
            }
            result = dict(result_body)
            result["integrity"] = {
                "algorithm": "sha256",
                "content_digest": _canonical_digest(result_body),
            }
            _write_json(run_dir / "zotero-writeback-result.json", result)
            artifact_index["zotero_writeback"] = {
                "mode": "approved-live",
                "status": "written",
                "plan_ref": "zotero-writeback-plan.json",
                "result_ref": "zotero-writeback-result.json",
            }
            artifact_index["artifacts"]["zotero_writeback_result"] = {
                "kind": "zotero-writeback-result",
                "ref": "zotero-writeback-result.json",
                "format": "json",
                "stage": "writeback",
                "private_content": False,
            }
            writeback_stage = next(
                record
                for record in stage_manifest["stages"]
                if record["name"] == "writeback"
            )
            writeback_stage["outputs"].append("zotero-writeback-result.json")
            _write_json(index_path, artifact_index)
            _write_json(stage_path, stage_manifest)
            _write_json(plan_path, plan)

            missing = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
            )
            self.assertFalse(missing["status"]["complete"])
            self.assertIn(
                "writeback-result observation: not-observed",
                missing["status"]["blocking_items"],
            )
            writeback = self._write_envelope(
                tempdir,
                run_dir=run_dir,
                kind="writeback-result",
                summary={
                    "status": "written",
                    "operation_count": 2,
                    "receipt_digest": result_body["receipt_digest"],
                    "audit_digest": result_body["audit_digest"],
                    "record_digest": result["integrity"]["content_digest"],
                },
            )
            zotero = self._write_envelope(
                tempdir,
                run_dir=run_dir,
                kind="zotero-live-state",
                summary={
                    "status": "consistent",
                    "item_version": 8,
                    "tag_state": "consistent",
                    "note_state": "consistent",
                },
            )
            joined = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
                evidence_paths=[writeback, zotero],
            )
            self.assertTrue(joined["status"]["complete"])
            self.assertEqual(
                joined["status"]["state"],
                "observational-live-ready",
            )
            self.assertTrue(joined["status"]["observationally_ready"])
            self.assertFalse(joined["status"]["authoritative_live_complete"])
            self.assertFalse(joined["authority"]["authoritative"])
            self.assertNotIn("live-complete", json.dumps(joined))

            for state_name in ("tag_state", "note_state"):
                with self.subTest(drifted_state=state_name):
                    drifted_summary = {
                        "status": "consistent",
                        "item_version": 8,
                        "tag_state": "consistent",
                        "note_state": "consistent",
                    }
                    drifted_summary[state_name] = "drifted"
                    drifted_zotero = self._write_envelope(
                        tempdir,
                        run_dir=run_dir,
                        kind="zotero-live-state",
                        summary=drifted_summary,
                    )
                    drifted = build_status_report(
                        source_pack_root=root,
                        paper_id=PAPER_ID,
                        run_id=RUN_ID,
                        evidence_paths=[writeback, drifted_zotero],
                    )
                    self.assertFalse(drifted["status"]["complete"])
                    self.assertEqual(drifted["status"]["state"], "needs-review")
                    self.assertFalse(drifted["status"]["observationally_ready"])
                    self.assertIn(
                        f"zotero-live-state observation {state_name}: drifted",
                        drifted["status"]["blocking_items"],
                    )

            wrong_version = self._write_envelope(
                tempdir,
                run_dir=run_dir,
                kind="zotero-live-state",
                summary={
                    "status": "consistent",
                    "item_version": 9,
                    "tag_state": "consistent",
                    "note_state": "consistent",
                },
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "drifts from canonical local evidence",
            ):
                build_status_report(
                    source_pack_root=root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                    evidence_paths=[writeback, wrong_version],
                )

            manifest_path = root / "zotero" / PAPER_ID / "manifest.json"
            manifest = _read_json(manifest_path)
            manifest["identity"].pop("zotero_version")
            _write_json(manifest_path, manifest)
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "one exact source Zotero version",
            ):
                build_status_report(
                    source_pack_root=root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                    evidence_paths=[writeback, zotero],
                )

    def test_source_zotero_version_fails_closed_for_multi_source_ambiguity(self):
        base = {
            "schema_version": "millefeuille-source-pack-manifest/v0.2",
            "sources": [
                {"identity": {"zotero_version": 7}},
                {"identity": {"zotero_version": 7}},
            ],
        }
        self.assertEqual(
            _canonical_source_zotero_version(base, required=True),
            7,
        )
        for versions in ((7, None), (7, 8)):
            with self.subTest(versions=versions):
                payload = deepcopy(base)
                for source, version in zip(payload["sources"], versions, strict=True):
                    if version is None:
                        source["identity"].pop("zotero_version")
                    else:
                        source["identity"]["zotero_version"] = version
                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "one exact source Zotero version",
                ):
                    _canonical_source_zotero_version(payload, required=True)


class TestStatusParserHardening(unittest.TestCase):
    def test_deep_json_and_direct_cycles_fail_as_contract_errors(self):
        with tempfile.TemporaryDirectory() as tempdir:
            nested = '{"x":' * 70 + "null" + "}" * 70
            path = Path(tempdir) / "deep.json"
            path.write_text(nested, encoding="utf-8")
            with self.assertRaisesRegex(MillefeuilleContractError, "depth limit"):
                load_status_observation(path)

            recursive = '{"x":' * 1500 + "null" + "}" * 1500
            path.write_text(recursive, encoding="utf-8")
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "depth limit|not valid JSON",
            ):
                load_status_observation(path)

            path.write_text('{"integer":' + "9" * 5000 + "}", encoding="utf-8")
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "not valid JSON|unsafe integer",
            ):
                load_status_observation(path)

        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        with self.assertRaisesRegex(
            MillefeuilleContractError,
            "cyclic or reused JSON container",
        ):
            compute_status_digest(cyclic)

    def test_duplicate_fields_oversize_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "evidence.json"
            path.write_text('{"kind":"quality","kind":"quality"}', encoding="utf-8")
            with self.assertRaisesRegex(MillefeuilleContractError, "duplicate"):
                load_status_observation(path)

            path.write_bytes(b" " * 65_537)
            with self.assertRaisesRegex(MillefeuilleContractError, "exceeds"):
                load_status_observation(path)

            target = Path(tempdir) / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = Path(tempdir) / "link.json"
            try:
                link.symlink_to(target)
            except OSError:
                return
            with self.assertRaises(MillefeuilleContractError):
                load_status_observation(link)

    def test_observation_private_fields_and_values_fail_without_reflection(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "private.json"
            payload = _observation_payload(
                kind="zotero-live-state",
                source_hash="sha256:" + "a" * 64,
                summary={
                    "status": "consistent",
                    "item_version": 1,
                    "tag_state": "consistent",
                    "note_state": "consistent",
                    "raw_tags": ["private-marker-value"],
                },
            )
            _write_json(path, payload)
            with self.assertRaises(MillefeuilleContractError) as captured:
                load_status_observation(path)
            self.assertNotIn("private-marker-value", str(captured.exception))

            payload["summary"].pop("raw_tags")
            payload["summary"]["note_state"] = (
                "https://private.invalid/private-marker-value"
            )
            payload["integrity"]["content_digest"] = compute_status_observation_digest(
                payload
            )
            _write_json(path, payload)
            with self.assertRaises(MillefeuilleContractError) as captured:
                load_status_observation(path)
            self.assertNotIn("private-marker-value", str(captured.exception))


class TestStatusCli(unittest.TestCase):
    def test_canonical_cli_json_and_strict_use_joined_blockers(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_complete_preview(tempdir)
            stdout = StringIO()
            stderr = StringIO()
            result = run_artifact_cli(
                [
                    "status",
                    "--source-pack-root",
                    str(root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                    "--strict",
                    "--json",
                ],
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual(result, 0, stderr.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"]["state"], "preview-complete")
            self.assertNotIn(tempdir, stdout.getvalue())


class TestStatusSchemas(unittest.TestCase):
    def test_status_schemas_are_strict_and_validate_generated_records(self):
        schemas = {
            name: _read_json(_SPEC_DIR / name)
            for name in (
                "status.schema.json",
                "status-observation.schema.json",
                "completion-gate-result.schema.json",
                "zotero-writeback-result.schema.json",
            )
        }
        for name, schema in schemas.items():
            with self.subTest(name=name):
                Draft202012Validator.check_schema(schema)
                self.assertFalse(schema["additionalProperties"])

        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_complete_preview(tempdir)
            status = build_status_report(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
            )
            Draft202012Validator(schemas["status.schema.json"]).validate(status)

            incomplete_index = _read_json(_BLOCKED_INDEX)
            incomplete_index["indexes"][0]["status"] = "not-started"
            incomplete_path = Path(tempdir) / "incomplete-artifact-index.json"
            _write_json(incomplete_path, incomplete_index)
            incomplete_status = build_status_report(index_path=incomplete_path)
            self.assertIn(
                "index openkb: not-started",
                incomplete_status["status"]["blocking_items"],
            )
            self.assertEqual(
                incomplete_status["index"]["lanes"],
                [{"lane": "openkb", "status": "not-started"}],
            )
            Draft202012Validator(schemas["status.schema.json"]).validate(
                incomplete_status
            )

            observation = _observation_payload(
                kind="zotero-live-state",
                source_hash=str(
                    _read_json(run_dir / "artifact-index.json")["source_pack"][
                        "source_hash"
                    ]
                ),
                summary={
                    "status": "consistent",
                    "item_version": 1,
                    "tag_state": "not-observed",
                    "note_state": "not-observed",
                },
            )
            Draft202012Validator(schemas["status-observation.schema.json"]).validate(
                observation
            )


if __name__ == "__main__":
    unittest.main()
