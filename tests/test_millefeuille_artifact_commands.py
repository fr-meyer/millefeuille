"""Tests for read-only Millefeuille artifact/status commands."""

from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from millefeuille.cli.artifacts import run_artifact_cli
from millefeuille.domain.artifacts import (
    load_artifact_index,
    resolve_artifact_index_path,
    write_artifact_index,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "millefeuille_artifact_index"
COMPLETE_INDEX = FIXTURE_DIR / "complete" / "artifact-index.json"
BLOCKED_INDEX = FIXTURE_DIR / "blocked" / "artifact-index.json"


class TestArtifactIndexDomain(unittest.TestCase):
    def test_complete_fixture_loads_and_summarizes(self):
        index = load_artifact_index(COMPLETE_INDEX)
        status = index.status()

        self.assertEqual(index.paper_id, "fixture-paper-complete")
        self.assertTrue(status.complete)
        self.assertEqual(status.blocking_items, [])
        self.assertEqual(status.stage_counts["passed"], 9)
        self.assertEqual(status.stage_counts["skipped"], 6)

    def test_blocked_fixture_surfaces_blockers(self):
        index = load_artifact_index(BLOCKED_INDEX)
        status = index.status()

        self.assertFalse(status.complete)
        self.assertIn("stage extract-ocr: manual-gate", status.blocking_items)
        self.assertIn("stage route: not-started", status.blocking_items)
        self.assertIn("index openkb: needs-review", status.blocking_items)

    def test_resolve_artifact_index_from_root_and_run_id(self):
        resolved = resolve_artifact_index_path(
            artifact_root=FIXTURE_DIR / "complete",
            run_id="run-complete",
        )

        self.assertEqual(resolved, COMPLETE_INDEX)

    def test_write_round_trip_preserves_schema_shape(self):
        index = load_artifact_index(COMPLETE_INDEX)
        with tempfile.TemporaryDirectory() as tempdir:
            output_path = Path(tempdir) / "artifact-index.json"
            write_artifact_index(index, output_path)
            loaded = load_artifact_index(output_path)

        self.assertEqual(loaded.to_dict(), index.to_dict())


class TestArtifactCli(unittest.TestCase):
    def test_artifacts_json_command_outputs_index_payload(self):
        stdout = StringIO()
        exit_code = run_artifact_cli(
            ["artifacts", "--index", str(COMPLETE_INDEX), "--json"],
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["paper_id"], "fixture-paper-complete")
        self.assertIn("paper_card_json", payload["artifacts"])

    def test_status_strict_returns_two_for_blocked_index(self):
        stdout = StringIO()
        exit_code = run_artifact_cli(
            ["status", "--index", str(BLOCKED_INDEX), "--strict"],
            stdout=stdout,
        )

        self.assertEqual(exit_code, 2)
        self.assertIn("Millefeuille status: needs-review", stdout.getvalue())
        self.assertIn("stage route: not-started", stdout.getvalue())

    def test_incomplete_writeback_states_remain_valid_status_blockers(self):
        for writeback_status in (
            "failed",
            "manual-gate",
            "needs-review",
            "not-started",
        ):
            with self.subTest(writeback_status=writeback_status):
                payload = json.loads(BLOCKED_INDEX.read_text(encoding="utf-8"))
                payload["zotero_writeback"] = {
                    "mode": "preview",
                    "status": writeback_status,
                }
                with tempfile.TemporaryDirectory() as tempdir:
                    index_path = Path(tempdir) / "artifact-index.json"
                    index_path.write_text(
                        json.dumps(payload, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    stdout = StringIO()
                    stderr = StringIO()
                    exit_code = run_artifact_cli(
                        ["status", "--index", str(index_path), "--json"],
                        stdout=stdout,
                        stderr=stderr,
                    )
                    report = json.loads(stdout.getvalue())
                    self.assertEqual(exit_code, 0, stderr.getvalue())
                    self.assertEqual(
                        report["zotero_writeback"]["status"], writeback_status
                    )
                    self.assertEqual(
                        report["zotero_writeback"]["completion"], "incomplete"
                    )
                    self.assertIn(
                        f"zotero_writeback preview: {writeback_status}",
                        report["status"]["blocking_items"],
                    )
                    self.assertEqual(
                        run_artifact_cli(
                            ["status", "--index", str(index_path), "--strict"],
                            stdout=StringIO(),
                            stderr=StringIO(),
                        ),
                        2,
                    )

    def test_status_command_does_not_require_zotero_environment(self):
        env = os.environ.copy()
        for name in ("ZOTERO_LIBRARY_ID", "ZOTERO_READ_KEY", "ZOTERO_WRITE_KEY"):
            env.pop(name, None)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "millefeuille",
                "status",
                "--index",
                str(COMPLETE_INDEX),
                "--json",
            ],
            cwd=REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ZOTERO_READ_KEY", result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["status"]["complete"])
        self.assertEqual(
            payload["canonical"]["stage_manifest"]["state"],
            "not-observed",
        )
        self.assertIn(
            "source-pack identity: not-observed",
            payload["status"]["blocking_items"],
        )


if __name__ == "__main__":
    unittest.main()
