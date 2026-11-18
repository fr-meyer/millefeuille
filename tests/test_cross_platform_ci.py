"""Regression tests for the declared cross-platform CI contract."""

from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
# The reviewed adapter discovers the complete suite once and accounts for
# every occurrence, without selection or failure suppression.
FULL_SUITE_ADAPTER_SHA256 = (
    "b5813a04616d54617953aa6eb144ce4246846091afd8e47061efe74f16ea2c55"
)


class CrossPlatformCIContractTests(unittest.TestCase):
    def test_ci_matrix_covers_supported_operating_systems_and_python_versions(self):
        workflow = yaml.load(
            WORKFLOW_PATH.read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )

        self.assertEqual(workflow["permissions"], {"contents": "read"})
        triggers = workflow["on"]
        self.assertEqual(triggers["push"]["branches"], ["main", "dev"])
        self.assertEqual(triggers["pull_request"]["branches"], ["main", "dev"])
        matrix = workflow["jobs"]["test"]["strategy"]["matrix"]
        self.assertEqual(matrix["os"], ["ubuntu-latest", "windows-latest"])
        self.assertEqual(matrix["python-version"], ["3.11", "3.12", "3.13"])

        commands = "\n".join(
            step.get("run", "") for step in workflow["jobs"]["test"]["steps"]
        )
        self.assertIn("python -m ruff check .", commands)
        self.assertEqual(commands.count(".github/ci/run_tests.py"), 2)
        for marker in ("unshare --net", "New-NetFirewallRule", "-Action Block"):
            self.assertIn(marker, commands)
        adapter = REPOSITORY_ROOT / ".github/ci/run_tests.py"
        self.assertEqual(
            hashlib.sha256(adapter.read_bytes()).hexdigest(), FULL_SUITE_ADAPTER_SHA256
        )

    def test_readme_documents_capability_boundaries(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("CPython 3.11, 3.12, and 3.13", readme)
        self.assertIn("Atomic retrieval-batch publication", readme)
        self.assertIn("Fails closed before publication", readme)
        self.assertIn("Fails closed before output creation", readme)


if __name__ == "__main__":
    unittest.main()
