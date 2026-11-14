"""Offline release boundary contracts, not hosted CI or actor verification."""

import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def metadata(path):
    return yaml.load((ROOT / path).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class ReleasePublicationSafetyTests(unittest.TestCase):
    def test_every_trigger_has_only_read_only_test_job(self):
        workflow = metadata(".github/workflows/ci.yml")
        self.assertEqual(
            set(workflow["on"]), {"push", "pull_request", "workflow_dispatch"}
        )
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(set(workflow["jobs"]), {"test"})
        job = workflow["jobs"]["test"]
        self.assertNotIn("permissions", job)
        steps = job["steps"]
        self.assertEqual(steps[0]["with"]["persist-credentials"], "false")
        self.assertEqual(
            [step["run"] for step in steps if "run" in step],
            [
                "python -m pip install --upgrade pip\n"
                'python -m pip install --editable ".[test,native]"\n',
                "python -m ruff check .",
                "python -m unittest discover -v",
            ],
        )
        self.assertFalse(any("env" in step for step in steps))
        matrix = job["strategy"]["matrix"]
        self.assertEqual(matrix["os"], ["ubuntu-latest", "windows-latest"])
        self.assertEqual(matrix["python-version"], ["3.11", "3.12", "3.13"])
        self.assertEqual(set(matrix), {"os", "python-version"})

    def test_no_other_workflow_can_mutate_release(self):
        self.assertEqual(
            sorted(p.name for p in (ROOT / ".github/workflows").iterdir()),
            ["ci.yml"],
        )

    def test_actor_and_dependent_approvals_remain_explicit(self):
        actors = json.loads((ROOT / ".speculoos/actors.json").read_text())
        self.assertEqual(actors["defaults"]["release_actor"], "human_web")
        self.assertTrue(actors["profiles"]["human_web"]["requires_explicit_approval"])
        policy = metadata(".speculoos/publish-policy.yaml")["release_safety"]
        self.assertEqual(policy["release_actor"], "human_web")
        self.assertEqual(policy["final_main_rebuild_required"], "true")
        self.assertEqual(
            policy["approvals"],
            [
                "promotion",
                "dependent-final-main-tag-assets",
                "package-publication",
                "activation-if-selected",
            ],
        )

    def test_current_task_does_not_claim_pr98_is_current(self):
        manifest = metadata(".speculoos/manifest.yaml")
        task_id = manifest["canonical_task"]
        self.assertNotEqual(task_id, "pr-098-reconcile-delivery-ledgers")
        self.assertEqual(manifest["current_task"], task_id)
        task = metadata(f".speculoos/tasks/{task_id}.yaml")
        self.assertEqual(manifest["repository"]["working_branch"], task["branch"])
        self.assertEqual(task["base_branch"], "dev")
        historical = manifest["surfaces"]["github_pull_request"]
        self.assertEqual(historical["number"], "98")
        self.assertEqual(historical["status"], "historical-not-refreshed")
        self.assertEqual(
            manifest["review_policy"]["enforcement_class"], "repository_declared"
        )
        self.assertEqual(
            manifest["review_policy"]["current_authority_status"],
            "unresolved-requires-supported-live-readback",
        )

    def test_final_main_sequence_is_documented_without_selecting_version(self):
        preflight = (
            ROOT / "specs/millefeuille-pipeline/release-candidate-preflight.md"
        ).read_text()
        markers = [
            "Obtain exact promotion approval",
            "Read back actual final main SHA",
            "Rebuild wheel and sdist",
            "Clean-install",
            "Seal asset SHA256",
            "Obtain a dependent tag/assets approval",
            "The human_web owner",
        ]
        offsets = [preflight.index(marker) for marker in markers]
        self.assertEqual(offsets, sorted(offsets))
        policy = (
            ROOT / "specs/millefeuille-pipeline/release-version-policy.md"
        ).read_text()
        for marker in [
            "cached 0.5.0 tag",
            "does not bump a version",
            "legacy Hydra",
            "No replacement actor or waiver",
            "not proven github_enforced",
        ]:
            self.assertIn(marker, policy)
