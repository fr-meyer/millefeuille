"""Offline release boundary contracts, not hosted CI or actor verification."""

import copy
import hashlib
import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Exact reviewed workflow bytes replace the old three-command-only contract.
# Any workflow change still needs explicit source review and a pin update.
APPROVED_WORKFLOW_SHA256 = (
    "a48284f3e271b3af46d971972f1bf5dc155594dabccdd98daffd1fc2a5b3dacb"
)


def metadata(path):
    return yaml.load((ROOT / path).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class ReleasePublicationSafetyTests(unittest.TestCase):
    def assert_read_only_cloud_workflow(self, workflow):
        self.assertEqual(
            set(workflow["on"]), {"push", "pull_request", "workflow_dispatch"}
        )
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(set(workflow["jobs"]), {"test", "optimized", "distributions"})
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")
        allowed_actions = {
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        }
        for job in workflow["jobs"].values():
            self.assertNotIn("permissions", job)
            self.assertNotIn("environment", job)
            self.assertNotIn("env", job)
            self.assertEqual(job["timeout-minutes"], "20")
            checkout = [
                step
                for step in job["steps"]
                if step.get("uses", "").startswith("actions/checkout@")
            ]
            self.assertEqual(len(checkout), 1)
            self.assertEqual(checkout[0]["with"]["persist-credentials"], "false")
            self.assertEqual(
                checkout[0]["with"]["ref"],
                "${{ github.event.pull_request.head.sha || github.sha }}",
            )
            for step in job["steps"]:
                self.assertNotIn("env", step)
                self.assertNotIn("continue-on-error", step)
                self.assertNotIn("cache", step.get("with", {}))
                if "uses" in step:
                    self.assertIn(step["uses"], allowed_actions)
            encoded = json.dumps(job)
            self.assertNotIn("secrets.", encoded)
            self.assertNotIn("secrets[", encoded)
            self.assertNotIn("id-token", encoded)
        job = workflow["jobs"]["test"]
        self.assertEqual(job["strategy"]["max-parallel"], "2")
        self.assertEqual(job["strategy"]["fail-fast"], "false")
        matrix = job["strategy"]["matrix"]
        self.assertEqual(matrix["os"], ["ubuntu-latest", "windows-latest"])
        self.assertEqual(matrix["python-version"], ["3.11", "3.12", "3.13"])
        self.assertEqual(set(matrix), {"os", "python-version"})
        for name in ("optimized", "distributions"):
            self.assertEqual(workflow["jobs"][name]["runs-on"], "ubuntu-latest")
        # Pin all commands, conditionals, action inputs and triggers, not just
        # their names. Unreviewed release-capable steps cannot hide behind
        # read-only permissions or benign labels.
        approved = metadata(".github/workflows/ci.yml")
        self.assertEqual(
            hashlib.sha256(
                (ROOT / ".github/workflows/ci.yml").read_bytes()
            ).hexdigest(),
            APPROVED_WORKFLOW_SHA256,
        )
        self.assertEqual(workflow, approved)

    def test_every_trigger_has_only_read_only_test_job(self):
        self.assert_read_only_cloud_workflow(metadata(".github/workflows/ci.yml"))

    def test_cloud_contract_rejects_privilege_and_execution_drift(self):
        original = metadata(".github/workflows/ci.yml")
        changes = [
            lambda w: w.update(permissions={"contents": "write"}),
            lambda w: w["on"].update(pull_request_target={}),
            lambda w: w["jobs"].update(release={}),
            lambda w: w["jobs"]["test"].update(permissions={"contents": "write"}),
            lambda w: w["jobs"]["test"].update(environment="production"),
            lambda w: w["jobs"]["test"].update(env={"TOKEN": "secret"}),
            lambda w: w["jobs"]["test"].update({"timeout-minutes": "21"}),
            lambda w: w["jobs"]["test"]["strategy"].update({"max-parallel": "3"}),
            lambda w: w["jobs"]["test"]["strategy"].update({"fail-fast": "true"}),
            lambda w: w["jobs"]["test"]["steps"].append({"run": "git push"}),
            lambda w: w["jobs"]["test"]["steps"].append(
                {"uses": "actions/checkout@main"}
            ),
            lambda w: w["jobs"]["test"]["steps"].append(
                {"run": "true", "continue-on-error": "true"}
            ),
            lambda w: w["jobs"]["test"]["steps"].append(
                {"run": "echo ${{ secrets.TOKEN }}"}
            ),
            lambda w: w["jobs"]["test"]["steps"].append(
                {"run": "python -m unittest tests.selected"}
            ),
        ]
        for index, change in enumerate(changes):
            with self.subTest(mutation=index):
                workflow = copy.deepcopy(original)
                change(workflow)
                with self.assertRaises(AssertionError):
                    self.assert_read_only_cloud_workflow(workflow)

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
            "pullfrog-supported-identity-pending-repository-activation-and-exact-pr-evidence",
        )

    def test_supported_review_declaration_does_not_claim_native_approval(self):
        policy = metadata(".speculoos/manifest.yaml")["review_policy"]
        review = policy["pullfrog_review"]
        self.assertEqual(review["policy"], "primary")
        self.assertEqual(review["bot_login"], "pullfrog[bot]")
        self.assertEqual(review["app_id"], "1768019")
        self.assertEqual(review["repository_activation"], "not-established")
        self.assertEqual(
            review["acceptance"],
            {
                "distinct_pr_author": "required",
                "exact_pr_base_head_sha": "required",
                "complete_changed_file_coverage": "required",
                "native_review_verdict": "APPROVED",
                "blocking_findings": "zero",
                "unresolved_review_threads": "zero",
                "successful_check_alone_is_approval": "false",
                "commented_review_is_approval": "false",
            },
        )
        self.assertEqual(policy["required_pr_review"]["primary"], ["pullfrog_review"])
        self.assertEqual(policy["required_pr_review"]["fallback_any_of"], [])
        self.assertEqual(policy["self_managed"]["fallback_role"], "supplemental-only")
        for retired in ("legacy_pullfrog", "legacy_coderabbit", "mergeguez_review"):
            self.assertEqual(policy[retired]["policy"], "disabled")

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
