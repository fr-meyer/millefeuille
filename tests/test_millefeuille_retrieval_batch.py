"""Tests for deterministic offline multi-run retrieval aggregates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
from io import StringIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import unittest
from unittest import mock

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain import retrieve as retrieve_domain
from millefeuille.domain.artifacts import load_artifact_index
from millefeuille.domain.card_fixtures import load_paper_card
from millefeuille.domain.index_fixtures import (
    INDEX_STATUS_REF,
    load_retrieval_index_status,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.retrieve import (
    _RETRIEVAL_BATCH_TEMP_PREFIX,
    RETRIEVAL_BATCH_REPORT_REF,
    RETRIEVAL_BATCH_RESULT_REF,
    RETRIEVAL_BATCH_ROOT_REF,
)
from millefeuille.domain.source_packs import (
    load_source_pack_manifest,
    write_source_packs_from_handoff_evidence,
)
from millefeuille.domain.stage_runtime import load_json_object, load_jsonl_records
from millefeuille.domain.summary_fixtures import (
    SUMMARY_ARTIFACT_REF,
    load_hierarchical_summary,
)
from tests.test_millefeuille_source_pack_writer import (
    _make_handoff_row,
    _write_recovered_pdf_bytes,
)
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_handoff_jsonl,
)

try:  # optional dependency in local dev/CI only
    import jsonschema
except ImportError:  # pragma: no cover - exercised by fallback assertions
    jsonschema = None


SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _clone_fixture_run(run_dir: Path, run_id: str) -> Path:
    destination = run_dir.parent / run_id
    shutil.copytree(run_dir, destination)
    for relative_path in (
        Path("stage-manifest.json"),
        Path("artifact-index.json"),
        SUMMARY_ARTIFACT_REF,
        Path("cards/paper-card.json"),
        INDEX_STATUS_REF,
    ):
        target = destination / relative_path
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["run_id"] = run_id
        _write_json(target, payload)
    return destination


def _clone_fixture_package(source_pack_root: Path, paper_id: str) -> Path:
    source = source_pack_root / "zotero" / PAPER_ID
    destination = source_pack_root / "zotero" / paper_id
    shutil.copytree(source, destination)
    for path in destination.rglob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("paper_id") == PAPER_ID:
            payload["paper_id"] = paper_id
            _write_json(path, payload)
    return destination


def _accept_run(
    *,
    source_pack_root: Path,
    run_id: str,
    handoff_path: Path,
) -> None:
    exit_code = run_stage_cli(
        [
            "acceptance",
            "--source-pack-root",
            str(source_pack_root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            run_id,
            "--handoff",
            str(handoff_path),
        ],
        stdout=StringIO(),
    )
    if exit_code != 0:
        raise AssertionError(f"fixture acceptance failed with {exit_code}")


def _write_batch_manifest(
    path: Path,
    *,
    runs: object,
    batch_id: object = "retrieval-fixture",
    schema_version: object = "millefeuille-retrieval-batch-manifest/v0.1",
    **extra: object,
) -> None:
    payload = {
        "schema_version": schema_version,
        "batch_id": batch_id,
        "runs": runs,
        **extra,
    }
    _write_json(path, payload)


def _batch_args(
    *,
    source_pack_root: Path,
    manifest_path: Path,
    extra: list[str] | None = None,
) -> list[str]:
    return [
        "retrieve",
        "--source-pack-root",
        str(source_pack_root),
        "--batch-manifest",
        str(manifest_path),
        *(extra or []),
        "--json",
    ]


def _assert_matches_retrieval_batch_schema(payload: dict[str, object]) -> None:
    schema = json.loads(
        (SPEC_DIR / "retrieval-batch-result.schema.json").read_text(encoding="utf-8")
    )
    if jsonschema is not None:
        jsonschema.Draft202012Validator(schema).validate(payload)
        return

    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise AssertionError("runs must be a non-empty list")
    for run in runs:
        if not isinstance(run, dict):
            raise AssertionError("run payload must be an object")
        source_hash = run.get("source_hash")
        if not isinstance(source_hash, str):
            raise AssertionError("run source_hash must be a string")
        if not re.fullmatch(r"sha256(?:-aggregate)?:[0-9a-f]{64}", source_hash):
            raise AssertionError(f"unexpected source_hash {source_hash!r}")
        for entry in run.get("summary_entries", []):
            if set(entry) != {"grain", "scope", "text_ref"}:
                raise AssertionError(f"unexpected summary entry fields: {entry!r}")
        for lane in run.get("index_lanes", []):
            if set(lane) != {"lane", "status"}:
                raise AssertionError(f"unexpected index lane fields: {lane!r}")


def _prepare_multi_pdf_runtime_run(tempdir: str) -> tuple[Path, str, str]:
    single_tempdir = Path(tempdir) / "single"
    single_tempdir.mkdir(parents=True, exist_ok=True)
    single_root, single_run = _prepare_fixture_run(str(single_tempdir))
    bytes_one = b"multi main fixture bytes\n"
    bytes_two = b"multi supplement fixture bytes\n"
    sha_one = hashlib.sha256(bytes_one).hexdigest()
    sha_two = hashlib.sha256(bytes_two).hexdigest()
    source_one = _write_recovered_pdf_bytes(tempdir, "multi-main.pdf", bytes_one)
    source_two = _write_recovered_pdf_bytes(tempdir, "multi-supplement.pdf", bytes_two)
    evidence_path = Path(tempdir) / "multi-recovered-pdf-evidence.jsonl"
    evidence_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema_version": "millefeuille-recovered-pdf-evidence/v0.1",
                        "source_type": "zotero",
                        "item_key": "MULTIITEM1",
                        "attachment_key": "ATT1",
                        "canonical_filename": "Main.pdf",
                        "recovered_pdf_path": source_one.name,
                        "expected_sha256": sha_one,
                        "content_type": "application/pdf",
                        "file_size_bytes": len(bytes_one),
                    }
                ),
                json.dumps(
                    {
                        "schema_version": "millefeuille-recovered-pdf-evidence/v0.1",
                        "source_type": "zotero",
                        "item_key": "MULTIITEM1",
                        "attachment_key": "ATT2",
                        "canonical_filename": "Supplement.pdf",
                        "recovered_pdf_path": source_two.name,
                        "expected_sha256": sha_two,
                        "content_type": "application/pdf",
                        "file_size_bytes": len(bytes_two),
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    root = Path(tempdir) / "multi-source-packs"
    result = write_source_packs_from_handoff_evidence(
        handoff_rows=[
            _make_handoff_row(
                item_key="MULTIITEM1",
                attachment_key="ATT1",
                canonical_filename="Main.pdf",
                sha256=sha_one,
                file_size_bytes=len(bytes_one),
            ),
            _make_handoff_row(
                item_key="MULTIITEM1",
                attachment_key="ATT2",
                canonical_filename="Supplement.pdf",
                sha256=sha_two,
                file_size_bytes=len(bytes_two),
            ),
        ],
        evidence_path=evidence_path,
        source_pack_root=root,
        created_at="2026-07-17T00:00:00+00:00",
    )[0]
    paper_id = result.paper_id
    run_id = "run-multi-fixture"
    shutil.copy2(
        single_root / "zotero" / PAPER_ID / "selected" / "fulltext.md",
        result.source_pack_dir / "selected" / "fulltext.md",
    )
    destination = result.source_pack_dir / "analyses" / "millefeuille" / run_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(single_run, destination)
    source_hash = result.source_hash
    for relative in (
        Path("stage-manifest.json"),
        Path("artifact-index.json"),
        SUMMARY_ARTIFACT_REF,
        Path("cards/paper-card.json"),
        INDEX_STATUS_REF,
    ):
        target = destination / relative
        payload = json.loads(target.read_text(encoding="utf-8"))
        if payload.get("paper_id") == PAPER_ID:
            payload["paper_id"] = paper_id
        if payload.get("run_id") == RUN_ID:
            payload["run_id"] = run_id
        if payload.get("source_hash") is not None:
            payload["source_hash"] = source_hash
        identity = payload.get("identity")
        if isinstance(identity, dict):
            if identity.get("source_hash") is not None:
                identity["source_hash"] = source_hash
            if identity.get("title") == "Fixture Paper":
                identity["title"] = "Synthetic Multi PDF Fixture Paper"
            if identity.get("zotero_item_key") == "ITEM1":
                identity["zotero_item_key"] = "MULTIITEM1"
        source_identity = payload.get("source_identity")
        if (
            isinstance(source_identity, dict)
            and source_identity.get("zotero_item_key") == "ITEM1"
        ):
            source_identity["zotero_item_key"] = "MULTIITEM1"
        source_pack = payload.get("source_pack")
        if isinstance(source_pack, dict) and source_pack.get("source_hash") is not None:
            source_pack["source_hash"] = source_hash
        _write_json(target, payload)
    return root, paper_id, run_id


class TestMillefeuilleRetrievalBatch(unittest.TestCase):
    def test_batch_retrieval_is_sorted_portable_filtered_and_byte_stable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, first_run = _prepare_fixture_run(tempdir)
            second_run_id = "run-fixture-2"
            _clone_fixture_run(first_run, second_run_id)
            _accept_run(
                source_pack_root=root,
                run_id=RUN_ID,
                handoff_path=_write_handoff_jsonl(tempdir),
            )
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[
                    {"item_key": "ITEM1", "run_id": second_run_id},
                    {"paper_id": PAPER_ID, "run_id": RUN_ID},
                ],
            )
            stdout = StringIO()

            exit_code = run_stage_cli(
                _batch_args(
                    source_pack_root=root,
                    manifest_path=manifest_path,
                    extra=["--index-lane", "pageindex"],
                ),
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(
                payload["schema_version"],
                "millefeuille-retrieval-batch-result/v0.1",
            )
            self.assertEqual(payload["status"], "retrieved")
            _assert_matches_retrieval_batch_schema(payload)
            self.assertEqual(
                payload["counts"],
                {
                    "runs": 2,
                    "summary_matches": 4,
                    "index_lane_matches": 2,
                    "acceptance_pass": 1,
                    "acceptance_needs_review": 0,
                    "acceptance_unavailable": 1,
                    "classification_plans": 0,
                    "writeback_previews": 0,
                },
            )
            self.assertEqual(payload["filters"], {"index_lane": "pageindex"})
            self.assertEqual(
                [run["run_id"] for run in payload["runs"]],
                [RUN_ID, second_run_id],
            )
            self.assertEqual(
                [run["locator_type"] for run in payload["runs"]],
                ["paper_id", "item_key"],
            )
            self.assertEqual(
                [
                    lane["lane"]
                    for run in payload["runs"]
                    for lane in run["index_lanes"]
                ],
                ["pageindex", "pageindex"],
            )
            portable_fields = (
                "source_pack_ref",
                "selected_fulltext_ref",
                "summary_ref",
                "paper_card_ref",
                "index_status_ref",
            )
            for run in payload["runs"]:
                self.assertEqual(
                    [entry["text_ref"] for entry in run["summary_entries"]],
                    [
                        entry["text_ref"]
                        for entry in sorted(
                            run["summary_entries"],
                            key=lambda entry: (
                                entry["scope"],
                                entry["grain"],
                                entry["text_ref"],
                            ),
                        )
                    ],
                )
                self.assertTrue(
                    all(
                        set(entry) == {"grain", "scope", "text_ref"}
                        for entry in run["summary_entries"]
                    )
                )
                self.assertTrue(
                    all(set(lane) == {"lane", "status"} for lane in run["index_lanes"])
                )
                refs = [run[field] for field in portable_fields]
                refs.extend(entry["text_ref"] for entry in run["summary_entries"])
                if "acceptance_summary_ref" in run:
                    refs.append(run["acceptance_summary_ref"])
                for ref in refs:
                    self.assertFalse(PurePosixPath(ref).is_absolute())
                    self.assertNotIn("..", PurePosixPath(ref).parts)
                    self.assertTrue((root / ref).exists(), ref)

            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            result_path = batch_dir / RETRIEVAL_BATCH_RESULT_REF
            report_path = batch_dir / RETRIEVAL_BATCH_REPORT_REF
            self.assertEqual(
                json.loads(result_path.read_text(encoding="utf-8")), payload
            )
            aggregate_text = result_path.read_text(
                encoding="utf-8"
            ) + report_path.read_text(encoding="utf-8")
            for private_fixture_text in (
                "A concise thesis",
                "Full paper summary.",
                "Page 1 summary.",
            ):
                self.assertNotIn(private_fixture_text, aggregate_text)
            first_bytes = (result_path.read_bytes(), report_path.read_bytes())

            rerun_exit = run_stage_cli(
                _batch_args(
                    source_pack_root=root,
                    manifest_path=manifest_path,
                    extra=["--index-lane", "pageindex"],
                ),
                stdout=StringIO(),
            )
            self.assertEqual(rerun_exit, 0)
            self.assertEqual(
                (result_path.read_bytes(), report_path.read_bytes()),
                first_bytes,
            )

    def test_batch_omits_private_summary_and_index_metadata_from_json_and_markdown(
        self,
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_fixture_run(tempdir)
            summary_path = run_dir / SUMMARY_ARTIFACT_REF
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
            summary_payload["summaries"][0]["summary_id"] = (
                "PRIVATE_SUMMARY_CONTENT_SENTINEL"
            )
            summary_payload["summaries"][0]["source_locators"] = [
                "PRIVATE_PAPER_SENTINEL"
            ]
            summary_payload["summaries"][0]["depends_on"] = [
                "PRIVATE_PROVIDER_SENTINEL"
            ]
            summary_payload["summaries"][0]["quality_warnings"] = [
                "PRIVATE_WARNING_SENTINEL"
            ]
            summary_payload["summaries"][0]["model_provenance_ref"] = (
                "texts/private-provider-sentinel.md"
            )
            _write_json(summary_path, summary_payload)
            (
                run_dir / "summaries" / "texts" / "private-provider-sentinel.md"
            ).write_text(
                "provider artifact\n",
                encoding="utf-8",
            )

            index_path = run_dir / INDEX_STATUS_REF
            index_payload = json.loads(index_path.read_text(encoding="utf-8"))
            index_payload["lanes"][0]["skip_reason"] = "PRIVATE_SKIP_SENTINEL"
            index_payload["lanes"][0]["target"] = {
                "provider": "PRIVATE_TARGET_SENTINEL"
            }
            index_payload["lanes"][0]["chunking_profile"] = {
                "note": "PRIVATE_CHUNK_SENTINEL"
            }
            index_payload["lanes"][0]["quality_warnings"] = [
                "PRIVATE_LANE_WARNING_SENTINEL"
            ]
            _write_json(index_path, index_payload)

            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            stdout = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            aggregate_text = json.dumps(payload, sort_keys=True) + (
                root
                / RETRIEVAL_BATCH_ROOT_REF
                / "retrieval-fixture"
                / RETRIEVAL_BATCH_REPORT_REF
            ).read_text(encoding="utf-8")
            for sentinel in (
                "PRIVATE_SUMMARY_CONTENT_SENTINEL",
                "PRIVATE_PAPER_SENTINEL",
                "PRIVATE_PROVIDER_SENTINEL",
                "PRIVATE_WARNING_SENTINEL",
                "PRIVATE_SKIP_SENTINEL",
                "PRIVATE_TARGET_SENTINEL",
                "PRIVATE_CHUNK_SENTINEL",
                "PRIVATE_LANE_WARNING_SENTINEL",
                "private-provider-sentinel",
            ):
                self.assertNotIn(sentinel, aggregate_text)

    def test_descendant_directory_substitution_fails_before_aggregate_write(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            source_pack_dir = root / "zotero" / PAPER_ID
            analyses_dir = source_pack_dir / "analyses"
            saved_analyses_dir = source_pack_dir / "analyses-saved"
            outside = Path(tempdir) / "outside-analyses"
            outside_stage = outside / "millefeuille" / RUN_ID / "stage-manifest.json"
            _write_json(outside_stage, {"private": "OUTSIDE_CONTENT_SENTINEL"})
            real_load = retrieve_domain.RootArtifactReader.load_json_object
            swapped = False

            def _swap_before_stage_read(
                reader: retrieve_domain.RootArtifactReader,
                path: str | Path,
                label: str,
            ) -> dict[str, object]:
                nonlocal swapped
                if label == "stage manifest" and not swapped:
                    analyses_dir.rename(saved_analyses_dir)
                    analyses_dir.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return real_load(reader, path, label)

            stderr = StringIO()
            with mock.patch.object(
                retrieve_domain.RootArtifactReader,
                "load_json_object",
                autospec=True,
                side_effect=_swap_before_stage_read,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertTrue(swapped)
            self.assertIn("symbolic links or non-directories", stderr.getvalue())
            self.assertNotIn("OUTSIDE_CONTENT_SENTINEL", stderr.getvalue())
            self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

    def test_preflighted_source_artifact_mutation_fails_before_atomic_commit(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            card_path = run_dir / retrieve_domain.CARD_JSON_REF
            real_render = retrieve_domain._render_retrieval_batch_markdown
            mutated = False

            def _mutate_card_after_preflight(payload: dict[str, object]) -> str:
                nonlocal mutated
                report = real_render(payload)
                card_payload = json.loads(card_path.read_text(encoding="utf-8"))
                card_payload["identity"]["title"] = "changed after preflight"
                _write_json(card_path, card_payload)
                mutated = True
                return report

            stderr = StringIO()
            with mock.patch(
                "millefeuille.domain.retrieve._render_retrieval_batch_markdown",
                side_effect=_mutate_card_after_preflight,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertTrue(mutated)
            self.assertIn("changed after batch preflight", stderr.getvalue())
            self.assertIn(card_path.as_posix(), stderr.getvalue())
            self.assertFalse(
                (
                    root
                    / RETRIEVAL_BATCH_ROOT_REF
                    / "retrieval-fixture"
                    / RETRIEVAL_BATCH_RESULT_REF.parent
                ).exists()
            )

    def test_missing_optional_input_appearing_after_preflight_fails_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            optional_path = run_dir / retrieve_domain.ACCEPTANCE_SUMMARY_REF
            self.assertFalse(optional_path.exists())
            real_render = retrieve_domain._render_retrieval_batch_markdown

            def _create_optional_after_preflight(payload: dict[str, object]) -> str:
                report = real_render(payload)
                _write_json(optional_path, {"late": True})
                return report

            stderr = StringIO()
            with mock.patch(
                "millefeuille.domain.retrieve._render_retrieval_batch_markdown",
                side_effect=_create_optional_after_preflight,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("appeared after batch preflight", stderr.getvalue())
            self.assertIn(optional_path.as_posix(), stderr.getvalue())

    def test_non_regular_optional_input_fails_during_preflight(self):
        cases = ["directory"]
        if hasattr(os, "mkfifo"):
            cases.append("fifo")

        for kind in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tempdir:
                root, run_dir = _prepare_fixture_run(tempdir)
                manifest_path = Path(tempdir) / "retrieval-batch.json"
                _write_batch_manifest(
                    manifest_path,
                    runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
                )
                optional_path = run_dir / retrieve_domain.ACCEPTANCE_SUMMARY_REF
                optional_path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "directory":
                    optional_path.mkdir()
                else:
                    os.mkfifo(optional_path)

                stderr = StringIO()
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 2)
                self.assertIn("is not a regular file", stderr.getvalue())
                self.assertIn(optional_path.as_posix(), stderr.getvalue())
                self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

    def test_missing_optional_input_becoming_non_regular_fails_revalidation(self):
        cases = ["directory"]
        if hasattr(os, "mkfifo"):
            cases.append("fifo")

        for kind in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tempdir:
                root, run_dir = _prepare_fixture_run(tempdir)
                manifest_path = Path(tempdir) / "retrieval-batch.json"
                _write_batch_manifest(
                    manifest_path,
                    runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
                )
                optional_path = run_dir / retrieve_domain.ACCEPTANCE_SUMMARY_REF
                self.assertFalse(optional_path.exists())
                real_render = retrieve_domain._render_retrieval_batch_markdown

                def _replace_optional_after_preflight(
                    payload: dict[str, object],
                    *,
                    _real_render=real_render,
                    _optional_path=optional_path,
                    _kind=kind,
                ) -> str:
                    report = _real_render(payload)
                    _optional_path.parent.mkdir(parents=True, exist_ok=True)
                    if _kind == "directory":
                        _optional_path.mkdir()
                    else:
                        os.mkfifo(_optional_path)
                    return report

                stderr = StringIO()
                with mock.patch(
                    "millefeuille.domain.retrieve._render_retrieval_batch_markdown",
                    side_effect=_replace_optional_after_preflight,
                ):
                    exit_code = run_stage_cli(
                        _batch_args(
                            source_pack_root=root,
                            manifest_path=manifest_path,
                        ),
                        stderr=stderr,
                    )

                self.assertEqual(exit_code, 2)
                self.assertIn("changed after batch preflight", stderr.getvalue())
                self.assertIn(optional_path.as_posix(), stderr.getvalue())

    def test_corpus_enumeration_mutation_after_preflight_fails_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_fixture_run(tempdir)
            card_payload = json.loads(
                (run_dir / retrieve_domain.CARD_JSON_REF).read_text(encoding="utf-8")
            )
            title = card_payload["identity"]["title"]
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"title": title, "run_id": RUN_ID}],
            )
            late_candidate = root / "zotero" / "late-corpus-entry"
            real_render = retrieve_domain._render_retrieval_batch_markdown

            def _mutate_corpus_after_preflight(payload: dict[str, object]) -> str:
                report = real_render(payload)
                late_candidate.mkdir()
                return report

            stderr = StringIO()
            with mock.patch(
                "millefeuille.domain.retrieve._render_retrieval_batch_markdown",
                side_effect=_mutate_corpus_after_preflight,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn(
                "source-pack corpus changed after batch preflight",
                stderr.getvalue(),
            )
            self.assertFalse(
                (
                    root
                    / RETRIEVAL_BATCH_ROOT_REF
                    / "retrieval-fixture"
                    / RETRIEVAL_BATCH_RESULT_REF.parent
                ).exists()
            )

    def test_batch_manifest_mutation_fails_before_atomic_commit(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            real_render = retrieve_domain._render_retrieval_batch_markdown
            mutated = False

            def _mutate_manifest_after_preflight(payload: dict[str, object]) -> str:
                nonlocal mutated
                report = real_render(payload)
                _write_batch_manifest(
                    manifest_path,
                    runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
                    batch_id="changed-after-preflight",
                )
                mutated = True
                return report

            stderr = StringIO()
            with mock.patch(
                "millefeuille.domain.retrieve._render_retrieval_batch_markdown",
                side_effect=_mutate_manifest_after_preflight,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertTrue(mutated)
            self.assertIn(
                "retrieval batch manifest changed after batch preflight",
                stderr.getvalue(),
            )
            self.assertFalse(
                (
                    root
                    / RETRIEVAL_BATCH_ROOT_REF
                    / "retrieval-fixture"
                    / RETRIEVAL_BATCH_RESULT_REF.parent
                ).exists()
            )

    def test_staging_failure_leaves_only_scrubbed_owned_output(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            stderr = StringIO()
            real_write = retrieve_domain._write_staged_bytes
            write_count = 0

            def _fail_second_write(
                path: Path,
                payload: bytes,
                *,
                dir_fd: int,
            ) -> int:
                nonlocal write_count
                write_count += 1
                if write_count == 2:
                    raise OSError("synthetic second write failure")
                return real_write(path, payload, dir_fd=dir_fd)

            with mock.patch(
                "millefeuille.domain.retrieve._write_staged_bytes",
                side_effect=_fail_second_write,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn(
                "could not publish retrieval batch outputs",
                stderr.getvalue(),
            )
            self.assertFalse((batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent).exists())
            staging_dirs = list(batch_dir.glob(f"{_RETRIEVAL_BATCH_TEMP_PREFIX}*"))
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual(
                (staging_dirs[0] / RETRIEVAL_BATCH_RESULT_REF.name).stat().st_size,
                0,
            )
            self.assertFalse(
                (staging_dirs[0] / RETRIEVAL_BATCH_REPORT_REF.name).exists()
            )

    def test_late_destination_creation_fails_closed_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            stable_dir = batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent
            stderr = StringIO()
            real_create = retrieve_domain._create_relative_temp_directory

            def _create_late_destination(parent_fd: int, *, prefix: str) -> str:
                temp_name = real_create(parent_fd, prefix=prefix)
                os.mkdir(RETRIEVAL_BATCH_RESULT_REF.parent.name, dir_fd=parent_fd)
                return temp_name

            with mock.patch(
                "millefeuille.domain.retrieve._create_relative_temp_directory",
                side_effect=_create_late_destination,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("output appeared during publication", stderr.getvalue())
            self.assertTrue(stable_dir.is_dir())
            self.assertEqual(list(stable_dir.iterdir()), [])
            staging_dirs = list(batch_dir.glob(f"{_RETRIEVAL_BATCH_TEMP_PREFIX}*"))
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual(
                {path.name: path.stat().st_size for path in staging_dirs[0].iterdir()},
                {
                    RETRIEVAL_BATCH_REPORT_REF.name: 0,
                    RETRIEVAL_BATCH_RESULT_REF.name: 0,
                },
            )

    def test_batch_relative_dot_source_pack_root_remains_supported(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            stdout = StringIO()
            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                exit_code = run_stage_cli(
                    _batch_args(
                        source_pack_root=Path("."), manifest_path=manifest_path
                    ),
                    stdout=stdout,
                )
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                json.loads(stdout.getvalue())["schema_version"],
                "millefeuille-retrieval-batch-result/v0.1",
            )
            self.assertTrue(
                (root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture").is_dir()
            )

    def test_concurrent_same_batch_calls_publish_one_coherent_generation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )

            def _invoke() -> tuple[int, str]:
                stdout = StringIO()
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stdout=stdout,
                )
                return exit_code, stdout.getvalue()

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _unused: _invoke(), range(2)))

            self.assertEqual([code for code, _ in results], [0, 0])
            payloads = [json.loads(text) for _, text in results]
            self.assertEqual(payloads[0], payloads[1])
            stable_dir = (
                root
                / RETRIEVAL_BATCH_ROOT_REF
                / "retrieval-fixture"
                / RETRIEVAL_BATCH_RESULT_REF.parent
            )
            self.assertEqual(
                sorted(path.name for path in stable_dir.iterdir()),
                [RETRIEVAL_BATCH_REPORT_REF.name, RETRIEVAL_BATCH_RESULT_REF.name],
            )

    def test_batch_fails_closed_without_no_follow_platform_support(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            stderr = StringIO()

            with mock.patch(
                "millefeuille.domain.secure_io._supports_no_follow",
                return_value=False,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("requires no-follow filesystem reads", stderr.getvalue())
            self.assertFalse(
                (
                    root
                    / RETRIEVAL_BATCH_ROOT_REF
                    / "retrieval-fixture"
                    / RETRIEVAL_BATCH_RESULT_REF
                ).exists()
            )

    def test_public_json_loaders_preserve_read_error_prefixes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            missing = Path(tempdir) / "missing.json"
            loaders = (
                ("artifact index", lambda: load_artifact_index(missing)),
                ("paper card", lambda: load_paper_card(missing)),
                (
                    "retrieval index status",
                    lambda: load_retrieval_index_status(missing),
                ),
                (
                    "source-pack manifest",
                    lambda: load_source_pack_manifest(missing),
                ),
                (
                    "hierarchical summary",
                    lambda: load_hierarchical_summary(missing),
                ),
                ("stage fixture", lambda: load_json_object(missing, "stage fixture")),
                ("queue", lambda: load_jsonl_records(missing, "queue")),
            )

            for label, loader in loaders:
                pattern = (
                    rf"^could not read {re.escape(label)} "
                    rf"{re.escape(str(missing))}:"
                )
                with (
                    self.subTest(label=label),
                    self.assertRaisesRegex(MillefeuilleContractError, pattern),
                ):
                    loader()

    def test_missing_fcntl_support_fails_closed_before_publication(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            stderr = StringIO()
            real_import = __import__

            def _without_fcntl(
                name: str,
                globals: object = None,
                locals: object = None,
                fromlist: object = (),
                level: int = 0,
            ) -> object:
                if name == "fcntl":
                    raise ImportError("fcntl unavailable")
                return real_import(name, globals, locals, fromlist, level)

            with mock.patch("builtins.__import__", side_effect=_without_fcntl):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("requires POSIX flock support", stderr.getvalue())
            self.assertFalse((batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent).exists())

    def test_legacy_lock_path_replacement_during_staging_does_not_break_publication(
        self,
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            displaced_lock = batch_dir / ".retrieval.displaced.lock"
            lock_path = batch_dir / ".retrieval.lock"
            batch_dir.mkdir(parents=True)
            lock_path.write_text("legacy lock\n", encoding="utf-8")
            stdout = StringIO()
            stderr = StringIO()
            real_write = retrieve_domain._write_staged_bytes
            raced = False

            def _swap_lock_path(
                path: Path,
                payload: bytes,
                *,
                dir_fd: int,
            ) -> int:
                nonlocal raced
                fd = real_write(path, payload, dir_fd=dir_fd)
                if not raced:
                    raced = True
                    os.replace(lock_path, displaced_lock)
                    lock_path.write_text("replacement lock\n", encoding="utf-8")
                return fd

            with mock.patch(
                "millefeuille.domain.retrieve._write_staged_bytes",
                side_effect=_swap_lock_path,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stdout=stdout,
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 0, stderr.getvalue())
            self.assertEqual(
                json.loads(
                    (batch_dir / RETRIEVAL_BATCH_RESULT_REF).read_text(encoding="utf-8")
                ),
                json.loads(stdout.getvalue()),
            )
            self.assertEqual(
                sorted(
                    path.name
                    for path in batch_dir.glob(f"{_RETRIEVAL_BATCH_TEMP_PREFIX}*")
                ),
                [],
            )

    def test_batch_directory_substitution_before_commit_scrubs_staging(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            displaced_batch_dir = batch_dir.with_name(f"{batch_dir.name}-displaced")
            outside = Path(tempdir) / "outside-batch"
            outside.mkdir()
            stderr = StringIO()
            real_fsync = retrieve_domain._fsync_directory_fd
            raced = False

            def _swap_batch_dir(fd: int) -> None:
                nonlocal raced
                real_fsync(fd)
                if not raced:
                    raced = True
                    os.replace(batch_dir, displaced_batch_dir)
                    batch_dir.symlink_to(outside, target_is_directory=True)

            with mock.patch(
                "millefeuille.domain.retrieve._fsync_directory_fd",
                side_effect=_swap_batch_dir,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn(
                "retrieval batch directory changed while locked",
                stderr.getvalue(),
            )
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((outside / RETRIEVAL_BATCH_RESULT_REF.parent).exists())
            staging_dirs = list(
                displaced_batch_dir.glob(f"{_RETRIEVAL_BATCH_TEMP_PREFIX}*")
            )
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual(
                {path.name: path.stat().st_size for path in staging_dirs[0].iterdir()},
                {
                    RETRIEVAL_BATCH_REPORT_REF.name: 0,
                    RETRIEVAL_BATCH_RESULT_REF.name: 0,
                },
            )

    def test_batch_hierarchy_symlink_substitution_before_lock_fails_closed(self):
        for link_kind, link_relative in (
            ("parent", Path("batches")),
            ("batch", RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"),
        ):
            with (
                self.subTest(link_kind=link_kind),
                tempfile.TemporaryDirectory() as tempdir,
            ):
                root, _run_dir = _prepare_fixture_run(tempdir)
                manifest_path = Path(tempdir) / "retrieval-batch.json"
                _write_batch_manifest(
                    manifest_path,
                    runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
                )
                outside = Path(tempdir) / f"outside-{link_kind}"
                outside.mkdir()
                link_path = root / link_relative
                link_path.parent.mkdir(parents=True, exist_ok=True)
                link_path.symlink_to(outside, target_is_directory=True)
                stderr = StringIO()

                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 2)
                self.assertIn("symbolic links", stderr.getvalue())
                self.assertEqual(list(outside.iterdir()), [])

    def test_source_root_ancestor_substitution_before_open_fails_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            sandbox = Path(tempdir)
            workspace = sandbox / "workspace"
            workspace.mkdir()
            root, _run_dir = _prepare_fixture_run(str(workspace))
            manifest_path = sandbox / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            displaced_workspace = sandbox / "workspace-displaced"
            outside = sandbox / "outside"
            (outside / "source-packs").mkdir(parents=True)
            stderr = StringIO()
            real_open = retrieve_domain._open_directory_path_no_follow
            raced = False

            def _swap_source_root_ancestor(path: Path, *, label: str) -> int:
                nonlocal raced
                if not raced:
                    raced = True
                    os.replace(workspace, displaced_workspace)
                    workspace.symlink_to(outside, target_is_directory=True)
                return real_open(path, label=label)

            try:
                with mock.patch(
                    "millefeuille.domain.retrieve._open_directory_path_no_follow",
                    side_effect=_swap_source_root_ancestor,
                ):
                    exit_code = run_stage_cli(
                        _batch_args(
                            source_pack_root=root,
                            manifest_path=manifest_path,
                        ),
                        stderr=stderr,
                    )
            finally:
                if workspace.is_symlink():
                    workspace.unlink()
                if displaced_workspace.exists():
                    os.replace(displaced_workspace, workspace)

            self.assertEqual(exit_code, 2)
            self.assertIn("source-pack root path must not contain", stderr.getvalue())
            self.assertEqual(list((outside / "source-packs").iterdir()), [])

    def test_source_root_identity_substitution_before_publish_fails_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            sandbox = Path(tempdir)
            workspace = sandbox / "workspace"
            workspace.mkdir()
            root, _run_dir = _prepare_fixture_run(str(workspace))
            manifest_path = sandbox / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            displaced_workspace = sandbox / "workspace-displaced"
            replacement_root = workspace / "source-packs"
            stderr = StringIO()
            real_publish = retrieve_domain._publish_retrieval_batch_outputs
            raced = False

            def _swap_source_root_identity(**kwargs: object) -> None:
                nonlocal raced
                if not raced:
                    raced = True
                    os.replace(workspace, displaced_workspace)
                    replacement_root.mkdir(parents=True)
                real_publish(**kwargs)

            try:
                with mock.patch(
                    "millefeuille.domain.retrieve._publish_retrieval_batch_outputs",
                    side_effect=_swap_source_root_identity,
                ):
                    exit_code = run_stage_cli(
                        _batch_args(
                            source_pack_root=root,
                            manifest_path=manifest_path,
                        ),
                        stderr=stderr,
                    )
                self.assertEqual(exit_code, 2)
                self.assertIn(
                    "source-pack root changed during retrieval batch",
                    stderr.getvalue(),
                )
                self.assertEqual(list(replacement_root.iterdir()), [])
            finally:
                if workspace.exists():
                    shutil.rmtree(workspace)
                if displaced_workspace.exists():
                    os.replace(displaced_workspace, workspace)

    def test_staging_hard_link_injection_fails_without_external_write(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            external = Path(tempdir) / "external.txt"
            external.write_bytes(b"sentinel\n")
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            stderr = StringIO()
            real_write = retrieve_domain._write_staged_bytes
            raced = False

            def _inject_hard_link(
                path: Path,
                payload: bytes,
                *,
                dir_fd: int,
            ) -> int:
                nonlocal raced
                if not raced:
                    raced = True
                    os.link(
                        external,
                        path.name,
                        dst_dir_fd=dir_fd,
                        follow_symlinks=False,
                    )
                return real_write(path, payload, dir_fd=dir_fd)

            with mock.patch(
                "millefeuille.domain.retrieve._write_staged_bytes",
                side_effect=_inject_hard_link,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn(
                "could not publish retrieval batch outputs", stderr.getvalue()
            )
            self.assertEqual(external.read_bytes(), b"sentinel\n")
            self.assertFalse((batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent).exists())
            staging_dirs = list(batch_dir.glob(f"{_RETRIEVAL_BATCH_TEMP_PREFIX}*"))
            self.assertEqual(len(staging_dirs), 1)
            injected_name = staging_dirs[0] / RETRIEVAL_BATCH_RESULT_REF.name
            self.assertTrue(injected_name.exists())
            self.assertEqual(injected_name.stat().st_ino, external.stat().st_ino)
            self.assertEqual(injected_name.read_bytes(), b"sentinel\n")

        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            external = Path(tempdir) / "linked-staged-result.json"
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            stderr = StringIO()
            real_write = retrieve_domain._write_staged_bytes
            linked = False

            def _export_staged_hard_link(
                path: Path,
                payload: bytes,
                *,
                dir_fd: int,
            ) -> int:
                nonlocal linked
                fd = real_write(path, payload, dir_fd=dir_fd)
                if not linked:
                    linked = True
                    os.link(
                        path.name,
                        external,
                        src_dir_fd=dir_fd,
                        follow_symlinks=False,
                    )
                return fd

            with mock.patch(
                "millefeuille.domain.retrieve._write_staged_bytes",
                side_effect=_export_staged_hard_link,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("staged output changed while locked", stderr.getvalue())
            self.assertTrue(external.exists())
            self.assertEqual(external.read_bytes(), b"")
            self.assertFalse((batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent).exists())
            staging_dirs = list(batch_dir.glob(f"{_RETRIEVAL_BATCH_TEMP_PREFIX}*"))
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual(
                {path.name: path.stat().st_size for path in staging_dirs[0].iterdir()},
                {
                    RETRIEVAL_BATCH_REPORT_REF.name: 0,
                    RETRIEVAL_BATCH_RESULT_REF.name: 0,
                },
            )

    def test_staging_directory_substitution_scrubs_only_pinned_generation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            displaced_generation = batch_dir / "displaced-staging-generation"
            attacker_payload = b"attacker-owned\n"
            stderr = StringIO()
            real_write = retrieve_domain._write_staged_bytes
            raced = False

            def _swap_staging_directory(
                path: Path,
                payload: bytes,
                *,
                dir_fd: int,
            ) -> int:
                nonlocal raced
                fd = real_write(path, payload, dir_fd=dir_fd)
                if not raced:
                    raced = True
                    staging_dirs = list(
                        batch_dir.glob(f"{_RETRIEVAL_BATCH_TEMP_PREFIX}*")
                    )
                    self.assertEqual(len(staging_dirs), 1)
                    staging_dir = staging_dirs[0]
                    os.replace(staging_dir, displaced_generation)
                    staging_dir.mkdir()
                    (staging_dir / "attacker.txt").write_bytes(attacker_payload)
                return fd

            with mock.patch(
                "millefeuille.domain.retrieve._write_staged_bytes",
                side_effect=_swap_staging_directory,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("staging directory changed while locked", stderr.getvalue())
            self.assertFalse((batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent).exists())
            self.assertEqual(
                {
                    path.name: path.stat().st_size
                    for path in displaced_generation.iterdir()
                },
                {
                    RETRIEVAL_BATCH_REPORT_REF.name: 0,
                    RETRIEVAL_BATCH_RESULT_REF.name: 0,
                },
            )
            replacement_dirs = list(batch_dir.glob(f"{_RETRIEVAL_BATCH_TEMP_PREFIX}*"))
            self.assertEqual(len(replacement_dirs), 1)
            self.assertEqual(
                (replacement_dirs[0] / "attacker.txt").read_bytes(),
                attacker_payload,
            )

    def test_atomic_rename_commits_read_only_generation_before_parent_fsync(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            stable_dir = batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent
            real_fsync = retrieve_domain._fsync_directory_fd
            fsync_calls = 0

            def _observe_atomic_commit(fd: int) -> None:
                nonlocal fsync_calls
                fsync_calls += 1
                if fsync_calls == 2:
                    self.assertTrue(stable_dir.is_dir())
                    self.assertEqual(stable_dir.stat().st_mode & 0o222, 0)
                    for name in (
                        RETRIEVAL_BATCH_RESULT_REF.name,
                        RETRIEVAL_BATCH_REPORT_REF.name,
                    ):
                        self.assertEqual((stable_dir / name).stat().st_mode & 0o222, 0)
                    with self.assertRaises(PermissionError):
                        (stable_dir / RETRIEVAL_BATCH_RESULT_REF.name).write_bytes(
                            b"attacker-owned\n"
                        )
                real_fsync(fd)

            with mock.patch(
                "millefeuille.domain.retrieve._fsync_directory_fd",
                side_effect=_observe_atomic_commit,
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=StringIO(),
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(fsync_calls, 2)

    def test_new_generation_does_not_use_existing_generation_validator(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            real_validator = (
                retrieve_domain._validate_existing_retrieval_generation_relative
            )

            with mock.patch(
                "millefeuille.domain.retrieve."
                "_validate_existing_retrieval_generation_relative",
                wraps=real_validator,
            ) as validator:
                first_exit = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=StringIO(),
                )
                self.assertEqual(first_exit, 0)
                validator.assert_not_called()

                rerun_exit = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=StringIO(),
                )
                self.assertEqual(rerun_exit, 0)
                validator.assert_called_once()

    def test_late_staging_mutations_are_detected_before_atomic_commit(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            stderr = StringIO()
            real_write = retrieve_domain._write_staged_bytes
            real_read = retrieve_domain._read_bytes_from_open_fd
            staging_dir_fd: int | None = None
            read_count = 0

            def _capture_staging_fd(
                path: Path,
                payload: bytes,
                *,
                dir_fd: int,
            ) -> int:
                nonlocal staging_dir_fd
                staging_dir_fd = dir_fd
                return real_write(path, payload, dir_fd=dir_fd)

            def _inject_entry_after_first_verification(fd: int) -> bytes:
                nonlocal read_count
                payload = real_read(fd)
                read_count += 1
                if read_count == 2:
                    assert staging_dir_fd is not None
                    extra_fd = os.open(
                        "extra.txt",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=staging_dir_fd,
                    )
                    try:
                        os.write(extra_fd, b"attacker-owned\n")
                    finally:
                        os.close(extra_fd)
                return payload

            with (
                mock.patch(
                    "millefeuille.domain.retrieve._write_staged_bytes",
                    side_effect=_capture_staging_fd,
                ),
                mock.patch(
                    "millefeuille.domain.retrieve._read_bytes_from_open_fd",
                    side_effect=_inject_entry_after_first_verification,
                ),
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("staging output drift detected", stderr.getvalue())
            self.assertFalse((batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent).exists())
            staging_dirs = list(batch_dir.glob(f"{_RETRIEVAL_BATCH_TEMP_PREFIX}*"))
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual(
                (staging_dirs[0] / "extra.txt").read_bytes(),
                b"attacker-owned\n",
            )
            self.assertEqual(
                {
                    path.name: path.stat().st_size
                    for path in staging_dirs[0].iterdir()
                    if path.name != "extra.txt"
                },
                {
                    RETRIEVAL_BATCH_REPORT_REF.name: 0,
                    RETRIEVAL_BATCH_RESULT_REF.name: 0,
                },
            )

        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            external = Path(tempdir) / "late-report-link.md"
            stderr = StringIO()
            real_write = retrieve_domain._write_staged_bytes
            real_read = retrieve_domain._read_bytes_from_open_fd
            staging_dir_fd: int | None = None
            read_count = 0

            def _capture_hardlink_staging_fd(
                path: Path,
                payload: bytes,
                *,
                dir_fd: int,
            ) -> int:
                nonlocal staging_dir_fd
                staging_dir_fd = dir_fd
                return real_write(path, payload, dir_fd=dir_fd)

            def _inject_hard_link_after_first_verification(fd: int) -> bytes:
                nonlocal read_count
                payload = real_read(fd)
                read_count += 1
                if read_count == 2:
                    assert staging_dir_fd is not None
                    os.link(
                        RETRIEVAL_BATCH_REPORT_REF.name,
                        external,
                        src_dir_fd=staging_dir_fd,
                        follow_symlinks=False,
                    )
                return payload

            with (
                mock.patch(
                    "millefeuille.domain.retrieve._write_staged_bytes",
                    side_effect=_capture_hardlink_staging_fd,
                ),
                mock.patch(
                    "millefeuille.domain.retrieve._read_bytes_from_open_fd",
                    side_effect=_inject_hard_link_after_first_verification,
                ),
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("changed while read", stderr.getvalue())
            self.assertTrue(external.exists())
            self.assertEqual(external.stat().st_size, 0)
            self.assertFalse((batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent).exists())

    def test_displaced_staged_inode_is_scrubbed_through_retained_descriptor(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            external = Path(tempdir) / "displaced-report-link.md"
            displaced_name = "displaced-report.md"
            replacement_bytes = b"attacker-owned replacement\n"
            stderr = StringIO()
            real_write = retrieve_domain._write_staged_bytes
            real_read = retrieve_domain._read_bytes_from_open_fd
            staging_dir_fd: int | None = None
            read_count = 0

            def _capture_staging_fd(
                path: Path,
                payload: bytes,
                *,
                dir_fd: int,
            ) -> retrieve_domain._StagedFileDescriptors:
                nonlocal staging_dir_fd
                staging_dir_fd = dir_fd
                return real_write(path, payload, dir_fd=dir_fd)

            def _displace_report_after_initial_verification(fd: int) -> bytes:
                nonlocal read_count
                payload = real_read(fd)
                read_count += 1
                if read_count == 4:
                    assert staging_dir_fd is not None
                    os.link(
                        RETRIEVAL_BATCH_REPORT_REF.name,
                        external,
                        src_dir_fd=staging_dir_fd,
                        follow_symlinks=False,
                    )
                    os.rename(
                        RETRIEVAL_BATCH_REPORT_REF.name,
                        displaced_name,
                        src_dir_fd=staging_dir_fd,
                        dst_dir_fd=staging_dir_fd,
                    )
                    replacement_fd = os.open(
                        RETRIEVAL_BATCH_REPORT_REF.name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=staging_dir_fd,
                    )
                    try:
                        os.write(replacement_fd, replacement_bytes)
                    finally:
                        os.close(replacement_fd)
                return payload

            with (
                mock.patch(
                    "millefeuille.domain.retrieve._write_staged_bytes",
                    side_effect=_capture_staging_fd,
                ),
                mock.patch(
                    "millefeuille.domain.retrieve._read_bytes_from_open_fd",
                    side_effect=_displace_report_after_initial_verification,
                ),
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("changed while read", stderr.getvalue())
            self.assertFalse((batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent).exists())
            self.assertTrue(external.exists())
            self.assertEqual(external.stat().st_size, 0)
            staging_dirs = list(batch_dir.glob(f"{_RETRIEVAL_BATCH_TEMP_PREFIX}*"))
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual((staging_dirs[0] / displaced_name).stat().st_size, 0)
            self.assertEqual(
                (staging_dirs[0] / RETRIEVAL_BATCH_REPORT_REF.name).read_bytes(),
                replacement_bytes,
            )
            self.assertEqual(
                (staging_dirs[0] / RETRIEVAL_BATCH_RESULT_REF.name).stat().st_size,
                0,
            )

    def test_staged_descriptors_are_read_only_before_final_verification(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            result_path = batch_dir / RETRIEVAL_BATCH_RESULT_REF
            stdout = StringIO()
            stderr = StringIO()
            real_write = retrieve_domain._write_staged_bytes
            real_read = retrieve_domain._read_bytes_from_open_fd
            result_fd: int | None = None
            result_size = 0
            read_count = 0
            rejected_errno: int | None = None

            def _capture_result_fd(
                path: Path,
                payload: bytes,
                *,
                dir_fd: int,
            ) -> retrieve_domain._StagedFileDescriptors:
                nonlocal result_fd, result_size
                descriptors = real_write(path, payload, dir_fd=dir_fd)
                if path.name == RETRIEVAL_BATCH_RESULT_REF.name:
                    result_fd = descriptors.read_fd
                    result_size = len(payload)
                return descriptors

            def _attempt_retained_fd_mutation(fd: int) -> bytes:
                nonlocal read_count, rejected_errno
                payload = real_read(fd)
                read_count += 1
                if read_count == 4:
                    assert result_fd is not None
                    try:
                        os.pwrite(result_fd, b"X" * result_size, 0)
                    except OSError as exc:
                        rejected_errno = exc.errno
                return payload

            with (
                mock.patch(
                    "millefeuille.domain.retrieve._write_staged_bytes",
                    side_effect=_capture_result_fd,
                ),
                mock.patch(
                    "millefeuille.domain.retrieve._read_bytes_from_open_fd",
                    side_effect=_attempt_retained_fd_mutation,
                ),
            ):
                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stdout=stdout,
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 0, stderr.getvalue())
            self.assertIn(rejected_errno, {errno.EBADF, errno.EACCES})
            self.assertEqual(
                json.loads(result_path.read_text()),
                json.loads(stdout.getvalue()),
            )
            self.assertFalse(result_path.read_bytes().startswith(b"X"))

    def test_existing_generation_hard_link_incomplete_and_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            first_exit = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stdout=StringIO(),
            )
            self.assertEqual(first_exit, 0)
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            stable_dir = batch_dir / RETRIEVAL_BATCH_RESULT_REF.parent
            result_path = batch_dir / RETRIEVAL_BATCH_RESULT_REF
            report_path = batch_dir / RETRIEVAL_BATCH_REPORT_REF

            hardlink_path = batch_dir / "linked-result.json"
            os.link(result_path, hardlink_path)
            hardlink_stderr = StringIO()
            hardlink_exit = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=hardlink_stderr,
            )
            self.assertEqual(hardlink_exit, 2)
            self.assertIn("must not be a hard link", hardlink_stderr.getvalue())
            hardlink_path.unlink()

            stable_dir.chmod(0o755)
            report_path.unlink()
            incomplete_stderr = StringIO()
            incomplete_exit = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=incomplete_stderr,
            )
            self.assertEqual(incomplete_exit, 2)
            self.assertIn("drift detected", incomplete_stderr.getvalue())
            report_path.write_text("drifted\n", encoding="utf-8")
            report_path.chmod(0o444)
            stable_dir.chmod(0o555)

            drift_stderr = StringIO()
            drift_exit = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=drift_stderr,
            )
            self.assertEqual(drift_exit, 2)
            self.assertIn("drift detected", drift_stderr.getvalue())
            self.assertEqual(
                sorted(path.name for path in stable_dir.iterdir()),
                [
                    RETRIEVAL_BATCH_REPORT_REF.name,
                    RETRIEVAL_BATCH_RESULT_REF.name,
                ],
            )

    def test_multi_pdf_batch_result_accepts_aggregate_source_hash(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, paper_id, run_id = _prepare_multi_pdf_runtime_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": paper_id, "run_id": run_id}],
                batch_id="retrieval-multi-pdf",
            )
            stdout = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            _assert_matches_retrieval_batch_schema(payload)
            self.assertTrue(
                payload["runs"][0]["source_hash"].startswith("sha256-aggregate:")
            )

    def test_batch_supports_every_single_run_locator(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, first_run = _prepare_fixture_run(tempdir)
            run_locators = [
                (RUN_ID, "paper_id", PAPER_ID),
                ("run-item", "item_key", "ITEM1"),
                ("run-slug", "slug", PAPER_ID),
                ("run-doi", "doi", "https://doi.org/10.1000/FIXTURE"),
                ("run-title", "title", "  fixture   PAPER  "),
            ]
            card_path = first_run / "cards" / "paper-card.json"
            card_payload = json.loads(card_path.read_text(encoding="utf-8"))
            card_payload["identity"]["doi"] = "10.1000/fixture"
            _write_json(card_path, card_payload)
            for run_id, _locator_type, _locator_value in run_locators[1:]:
                _clone_fixture_run(first_run, run_id)
            manifest_path = Path(tempdir) / "all-locators.json"
            _write_batch_manifest(
                manifest_path,
                runs=[
                    {locator_type: locator_value, "run_id": run_id}
                    for run_id, locator_type, locator_value in reversed(run_locators)
                ],
            )
            stdout = StringIO()

            exit_code = run_stage_cli(
                _batch_args(
                    source_pack_root=root,
                    manifest_path=manifest_path,
                    extra=[
                        "--evidence-need",
                        "classification",
                        "--section",
                        "methods",
                    ],
                ),
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            expected = {
                run_id: locator_type
                for run_id, locator_type, _locator_value in run_locators
            }
            self.assertEqual(
                {run["run_id"]: run["locator_type"] for run in payload["runs"]},
                expected,
            )
            self.assertEqual(
                payload["filters"],
                {
                    "evidence_need": "classification",
                    "section": "methods",
                    "summary_scope": "classification",
                },
            )
            self.assertTrue(
                all(
                    entry["scope"] == "classification"
                    for run in payload["runs"]
                    for entry in run["summary_entries"]
                )
            )

    def test_duplicate_resolved_run_fails_before_aggregate_write(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "duplicate.json"
            _write_batch_manifest(
                manifest_path,
                runs=[
                    {"paper_id": PAPER_ID, "run_id": RUN_ID},
                    {"item_key": "ITEM1", "run_id": RUN_ID},
                ],
            )
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("unique paper_id/run_id", stderr.getvalue())
            self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

    def test_later_missing_or_ambiguous_locator_fails_before_write(self):
        cases = ("missing", "ambiguous")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tempdir:
                root, _run_dir = _prepare_fixture_run(tempdir)
                if case == "ambiguous":
                    _clone_fixture_package(root, "zotero-ITEM2")
                    invalid_locator = {"title": "Fixture Paper", "run_id": RUN_ID}
                    expected = "ambiguous title"
                else:
                    invalid_locator = {"doi": "10.1000/missing", "run_id": RUN_ID}
                    expected = "no artifact package matched doi"
                manifest_path = Path(tempdir) / f"{case}.json"
                _write_batch_manifest(
                    manifest_path,
                    runs=[
                        {"paper_id": PAPER_ID, "run_id": RUN_ID},
                        invalid_locator,
                    ],
                )
                stderr = StringIO()

                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 2)
                self.assertIn(expected, stderr.getvalue())
                self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

    def test_strict_manifest_rejects_malformed_non_string_and_unsafe_fields(self):
        cases = (
            ({"runs": [{"paper_id": PAPER_ID, "run_id": 7}]}, "non-string fields"),
            ({"runs": [{"paper_id": None, "run_id": RUN_ID}]}, "non-string fields"),
            ({"runs": [{"paper_id": PAPER_ID, "run_id": "../run"}]}, "unsafe"),
            ({"runs": [{"paper_id": PAPER_ID, "run_id": ".."}]}, "unsafe"),
            ({"runs": [{"paper_id": ".", "run_id": RUN_ID}]}, "unsafe"),
            ({"runs": [{"paper_id": PAPER_ID}]}, "requires run_id"),
            ({"runs": [{"run_id": RUN_ID}]}, "exactly one"),
            (
                {"runs": [{"paper_id": PAPER_ID, "run_id": RUN_ID, "extra": "x"}]},
                "unsupported fields",
            ),
            (
                {
                    "runs": [
                        {"paper_id": PAPER_ID, "item_key": "ITEM1", "run_id": RUN_ID}
                    ]
                },
                "exactly one",
            ),
            ({"runs": []}, "non-empty array"),
            ({"runs": ["not-an-object"]}, "must be an object"),
            (
                {
                    "runs": [{"paper_id": PAPER_ID, "run_id": RUN_ID}],
                    "batch_id": "../escape",
                },
                "batch_id",
            ),
            (
                {
                    "runs": [{"paper_id": PAPER_ID, "run_id": RUN_ID}],
                    "batch_id": 7,
                },
                "batch_id must be a string",
            ),
            (
                {
                    "runs": [{"paper_id": PAPER_ID, "run_id": RUN_ID}],
                    "schema_version": "v0",
                },
                "unsupported retrieval batch",
            ),
            (
                {
                    "runs": [{"paper_id": PAPER_ID, "run_id": RUN_ID}],
                    "schema_version": None,
                },
                "schema_version must be a string",
            ),
            (
                {"runs": [{"paper_id": PAPER_ID, "run_id": RUN_ID}], "extra": True},
                "unsupported fields",
            ),
        )
        for overrides, expected in cases:
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as tempdir,
            ):
                root, _run_dir = _prepare_fixture_run(tempdir)
                manifest_path = Path(tempdir) / "invalid.json"
                _write_batch_manifest(
                    manifest_path,
                    runs=overrides.get("runs"),
                    batch_id=overrides.get("batch_id", "retrieval-fixture"),
                    schema_version=overrides.get(
                        "schema_version",
                        "millefeuille-retrieval-batch-manifest/v0.1",
                    ),
                    **({"extra": True} if "extra" in overrides else {}),
                )
                stderr = StringIO()

                exit_code = run_stage_cli(
                    _batch_args(source_pack_root=root, manifest_path=manifest_path),
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 2)
                self.assertIn(expected, stderr.getvalue())
                self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

    def test_malformed_json_fails_before_write(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "malformed.json"
            manifest_path.write_text('{"schema_version":', encoding="utf-8")
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn(
                "retrieval batch manifest is not valid JSON", stderr.getvalue()
            )
            self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

    def test_unsafe_summary_ref_fails_preflight_without_write(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_fixture_run(tempdir)
            summary_path = run_dir / SUMMARY_ARTIFACT_REF
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
            summary_payload["summaries"][0]["text_ref"] = "../outside.md"
            _write_json(summary_path, summary_payload)
            manifest_path = Path(tempdir) / "unsafe-summary.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("traversal-safe relative ref", stderr.getvalue())
            self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

    def test_batch_rejects_schema_valid_but_non_portable_summary_refs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_fixture_run(tempdir)
            summary_path = run_dir / SUMMARY_ARTIFACT_REF
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
            summary_payload["summaries"][0]["text_ref"] = "texts/page 1.md"
            _write_json(summary_path, summary_payload)
            (run_dir / "summaries" / "texts" / "page 1.md").write_text(
                "Page 1 summary.\n",
                encoding="utf-8",
            )
            manifest_path = Path(tempdir) / "non-portable-summary.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("artifact ref is not portable", stderr.getvalue())
            self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

    def test_symlinked_aggregate_output_is_rejected_without_partial_write(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            result_path = batch_dir / RETRIEVAL_BATCH_RESULT_REF
            report_path = batch_dir / RETRIEVAL_BATCH_REPORT_REF
            result_path.parent.mkdir(parents=True)
            outside = Path(tempdir) / "outside.json"
            outside.write_text("sentinel\n", encoding="utf-8")
            result_path.symlink_to(outside)
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("must not be a symbolic link", stderr.getvalue())
            self.assertEqual(outside.read_text(encoding="utf-8"), "sentinel\n")
            self.assertFalse(report_path.exists())

    def test_symlinked_aggregate_parent_is_rejected_without_escape_write(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            batch_dir = root / RETRIEVAL_BATCH_ROOT_REF / "retrieval-fixture"
            batch_dir.mkdir(parents=True)
            outside = Path(tempdir) / "outside"
            outside.mkdir()
            (batch_dir / "retrieval").symlink_to(outside, target_is_directory=True)
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("must not be a symbolic link", stderr.getvalue())
            self.assertEqual(list(outside.iterdir()), [])

    def test_batch_mode_rejects_single_locator_run_id_and_live_mode(self):
        invalid_extras = (
            ["--paper-id", PAPER_ID],
            ["--run-id", RUN_ID],
            ["--mode", "read-only-live"],
        )
        for extra in invalid_extras:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tempdir:
                root, _run_dir = _prepare_fixture_run(tempdir)
                manifest_path = Path(tempdir) / "retrieval-batch.json"
                _write_batch_manifest(
                    manifest_path,
                    runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
                )
                stderr = StringIO()

                exit_code = run_stage_cli(
                    _batch_args(
                        source_pack_root=root,
                        manifest_path=manifest_path,
                        extra=extra,
                    ),
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 2)
                self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(
                    source_pack_root=root,
                    manifest_path=manifest_path,
                    extra=["--mode", "approved-live"],
                ),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 3)
            self.assertIn("separate manual approval", stderr.getvalue())
            self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

    def test_batch_approved_live_is_rejected_after_generic_gate(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            manifest_path = Path(tempdir) / "retrieval-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            stderr = StringIO()

            with mock.patch(
                "millefeuille.cli.stages._mode_gate",
                return_value=None,
            ):
                exit_code = run_stage_cli(
                    _batch_args(
                        source_pack_root=root,
                        manifest_path=manifest_path,
                        extra=["--mode", "approved-live"],
                    ),
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("preview-only", stderr.getvalue())
            self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())

    def test_batch_manifest_argument_rejects_empty_whitespace_and_direct_locators(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, _run_dir = _prepare_fixture_run(tempdir)
            cases = (
                (
                    [
                        "retrieve",
                        "--source-pack-root",
                        str(root),
                        "--batch-manifest",
                        "",
                    ],
                    "must not be empty",
                ),
                (
                    [
                        "retrieve",
                        "--source-pack-root",
                        str(root),
                        "--batch-manifest",
                        "   ",
                    ],
                    "must not be empty",
                ),
                (
                    [
                        "retrieve",
                        "--source-pack-root",
                        str(root),
                        "--batch-manifest",
                        str(Path(tempdir) / "retrieval-batch.json"),
                        "--paper-id",
                        PAPER_ID,
                    ],
                    "cannot be combined",
                ),
            )
            _write_batch_manifest(
                Path(tempdir) / "retrieval-batch.json",
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            for argv, expected in cases:
                with self.subTest(argv=argv):
                    stderr = StringIO()
                    exit_code = run_stage_cli(argv, stderr=stderr)
                    self.assertEqual(exit_code, 2)
                    self.assertIn(expected, stderr.getvalue())

    def test_batch_rejects_unknown_filter_values_before_write(self):
        cases = (
            (["--summary-scope", "unknown"], "summary_scope must be one of"),
            (["--grain", "unknown"], "grain must be one of"),
            (["--index-lane", "unknown"], "index_lane must be one of"),
        )
        for extra, expected in cases:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tempdir:
                root, _run_dir = _prepare_fixture_run(tempdir)
                manifest_path = Path(tempdir) / "retrieval-batch.json"
                _write_batch_manifest(
                    manifest_path,
                    runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
                )
                stderr = StringIO()

                exit_code = run_stage_cli(
                    _batch_args(
                        source_pack_root=root,
                        manifest_path=manifest_path,
                        extra=extra,
                    ),
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 2)
                self.assertIn(expected, stderr.getvalue())
                self.assertFalse((root / RETRIEVAL_BATCH_ROOT_REF).exists())


if __name__ == "__main__":
    unittest.main()
