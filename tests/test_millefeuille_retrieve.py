"""Tests for corpus-aware read-only artifact retrieval."""

from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from millefeuille.cli.stages import run_stage_cli
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_classification_evidence_json,
    _write_handoff_jsonl,
)


class TestMillefeuilleRetrieve(unittest.TestCase):
    def test_doi_and_normalized_title_resolve_verified_corpus_package(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            _update_card_identity(
                run_dir,
                title="Fixture   Paper",
                doi="10.1234/Fixture.One",
            )

            doi_payload = _run_retrieve_json(
                source_pack_root,
                "--doi",
                "https://doi.org/10.1234/fixture.one",
            )
            title_payload = _run_retrieve_json(
                source_pack_root,
                "--title",
                "  FIXTURE paper ",
            )
            slug_payload = _run_retrieve_json(
                source_pack_root,
                "--slug",
                PAPER_ID,
            )
            item_key_payload = _run_retrieve_json(
                source_pack_root,
                "--item-key",
                "ITEM1",
            )

            self.assertEqual(doi_payload["paper_id"], PAPER_ID)
            self.assertEqual(doi_payload["locator_type"], "doi")
            self.assertEqual(title_payload["paper_id"], PAPER_ID)
            self.assertEqual(title_payload["locator_type"], "title")
            self.assertEqual(slug_payload["paper_id"], PAPER_ID)
            self.assertEqual(slug_payload["locator_type"], "slug")
            self.assertEqual(item_key_payload["paper_id"], PAPER_ID)
            self.assertEqual(item_key_payload["locator_type"], "item_key")
            self.assertNotIn("title", title_payload)
            self.assertNotIn("doi", doi_payload)

    def test_section_page_and_classification_evidence_filters_return_refs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, _run_dir = _prepare_fixture_run(tempdir)

            page_payload = _run_retrieve_json(
                source_pack_root,
                "--paper-id",
                PAPER_ID,
                "--page",
                "1",
            )
            section_payload = _run_retrieve_json(
                source_pack_root,
                "--paper-id",
                PAPER_ID,
                "--section",
                "  METHODS ",
                "--evidence-need",
                "classification",
            )

            self.assertEqual(page_payload["summary_match_count"], 1)
            self.assertEqual(
                page_payload["summary_entries"][0]["summary_id"],
                "page-1",
            )
            self.assertEqual(section_payload["summary_match_count"], 1)
            self.assertEqual(
                section_payload["summary_entries"][0]["summary_id"],
                "full-paper",
            )
            self.assertEqual(
                section_payload["filters"]["summary_scope"],
                "classification",
            )
            self.assertIn("paper_card_ref", section_payload)
            self.assertIn("summary_ref", section_payload)

    def test_unknown_and_ambiguous_corpus_locators_fail_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, _run_dir = _prepare_fixture_run(tempdir)

            missing_stderr = StringIO()
            missing_exit = run_stage_cli(
                [
                    "retrieve",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--doi",
                    "10.1234/missing",
                    "--run-id",
                    RUN_ID,
                ],
                stderr=missing_stderr,
            )
            _clone_package(source_pack_root, PAPER_ID, "zotero-ITEM2")
            ambiguous_stderr = StringIO()
            ambiguous_exit = run_stage_cli(
                [
                    "retrieve",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--title",
                    "Fixture Paper",
                    "--run-id",
                    RUN_ID,
                ],
                stderr=ambiguous_stderr,
            )

            self.assertEqual(missing_exit, 2)
            self.assertIn("no artifact package matched doi", missing_stderr.getvalue())
            self.assertEqual(ambiguous_exit, 2)
            self.assertIn("ambiguous title", ambiguous_stderr.getvalue())
            self.assertIn(PAPER_ID, ambiguous_stderr.getvalue())
            self.assertIn("zotero-ITEM2", ambiguous_stderr.getvalue())

    def test_traversal_invalid_page_and_conflicting_evidence_fail_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, _run_dir = _prepare_fixture_run(tempdir)
            cases = (
                (
                    ["--paper-id", "../outside"],
                    "paper_id must be traversal-safe",
                ),
                (
                    ["--paper-id", PAPER_ID, "--page", "0"],
                    "page must be a positive integer",
                ),
                (
                    ["--item-key", "ITEM1."],
                    "source-pack item_key drift",
                ),
                (
                    [
                        "--paper-id",
                        PAPER_ID,
                        "--summary-scope",
                        "general",
                        "--evidence-need",
                        "classification",
                    ],
                    "classification evidence cannot be combined",
                ),
            )
            for extra_args, expected_error in cases:
                with self.subTest(expected_error=expected_error):
                    stderr = StringIO()
                    exit_code = run_stage_cli(
                        [
                            "retrieve",
                            "--source-pack-root",
                            str(source_pack_root),
                            *extra_args,
                            "--run-id",
                            RUN_ID,
                        ],
                        stderr=stderr,
                    )
                    self.assertEqual(exit_code, 2)
                    self.assertIn(expected_error, stderr.getvalue())

            corpus_stderr = StringIO()
            corpus_exit = run_stage_cli(
                [
                    "retrieve",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--title",
                    "Fixture Paper",
                    "--run-id",
                    "../outside",
                ],
                stderr=corpus_stderr,
            )
            self.assertEqual(corpus_exit, 2)
            self.assertIn("run_id must be traversal-safe", corpus_stderr.getvalue())

    def test_corpus_scan_rejects_card_identity_drift(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            card_path = run_dir / "cards" / "paper-card.json"
            payload = _read_json(card_path)
            payload["paper_id"] = "zotero-WRONG"
            _write_json(card_path, payload)
            stderr = StringIO()

            exit_code = run_stage_cli(
                [
                    "retrieve",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--title",
                    "Fixture Paper",
                    "--run-id",
                    RUN_ID,
                ],
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("paper card paper_id drift", stderr.getvalue())

    def test_direct_lookup_rejects_symlinked_paper_card(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            card_path = run_dir / "cards" / "paper-card.json"
            real_card_path = run_dir / "cards" / "paper-card-real.json"
            card_path.rename(real_card_path)
            card_path.symlink_to(real_card_path.name)
            stderr = StringIO()

            exit_code = run_stage_cli(
                [
                    "retrieve",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                ],
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("paper card must not be a symbolic link", stderr.getvalue())

    def test_index_ref_and_optional_preview_identity_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            index_path = run_dir / "index" / "index-status.json"
            index_payload = _read_json(index_path)
            index_payload["paper_card_ref"] = "../../../../outside.json"
            _write_json(index_path, index_payload)
            index_stderr = StringIO()

            index_exit = run_stage_cli(
                [
                    "retrieve",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                ],
                stderr=index_stderr,
            )

            self.assertEqual(index_exit, 2)
            self.assertIn(
                "retrieval index paper_card_ref drift",
                index_stderr.getvalue(),
            )

        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            classification_path = _write_classification_evidence_json(tempdir)
            pipeline_exit = run_stage_cli(
                [
                    "run",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                    "--stages",
                    "acceptance,classify,writeback",
                    "--handoff",
                    str(handoff_path),
                    "--classification-evidence",
                    str(classification_path),
                ],
                stdout=StringIO(),
            )
            self.assertEqual(pipeline_exit, 0)
            preview_path = (
                run_dir / "classification" / "zotero-writeback-preview.json"
            )
            preview_payload = _read_json(preview_path)
            preview_payload["paper_id"] = "zotero-WRONG"
            _write_json(preview_path, preview_payload)
            preview_stderr = StringIO()

            preview_exit = run_stage_cli(
                [
                    "retrieve",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                ],
                stderr=preview_stderr,
            )

            self.assertEqual(preview_exit, 2)
            self.assertIn("writeback preview paper_id drift", preview_stderr.getvalue())


def _run_retrieve_json(
    source_pack_root: Path,
    locator_flag: str,
    locator_value: str,
    *extra_args: str,
) -> dict[str, object]:
    stdout = StringIO()
    exit_code = run_stage_cli(
        [
            "retrieve",
            "--source-pack-root",
            str(source_pack_root),
            locator_flag,
            locator_value,
            "--run-id",
            RUN_ID,
            *extra_args,
            "--json",
        ],
        stdout=stdout,
    )
    if exit_code != 0:
        raise AssertionError(f"retrieve exited with {exit_code}")
    return json.loads(stdout.getvalue())


def _update_card_identity(run_dir: Path, **identity: object) -> None:
    card_path = run_dir / "cards" / "paper-card.json"
    payload = _read_json(card_path)
    payload["identity"].update(identity)
    _write_json(card_path, payload)


def _clone_package(root: Path, source_paper_id: str, target_paper_id: str) -> None:
    source_dir = root / "zotero" / source_paper_id
    target_dir = root / "zotero" / target_paper_id
    shutil.copytree(source_dir, target_dir)
    run_dir = target_dir / "analyses" / "millefeuille" / RUN_ID
    for path in (
        target_dir / "manifest.json",
        run_dir / "artifact-index.json",
        run_dir / "summaries" / "hierarchical-summary.json",
        run_dir / "cards" / "paper-card.json",
        run_dir / "index" / "index-status.json",
    ):
        payload = _read_json(path)
        payload["paper_id"] = target_paper_id
        _write_json(path, payload)


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
