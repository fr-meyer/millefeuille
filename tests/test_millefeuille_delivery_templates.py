"""Validate the reusable Millefeuille delivery and evidence templates."""

import json
from pathlib import Path
import re
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "specs" / "millefeuille-delivery"
CATALOG_PATH = TEMPLATE_ROOT / "catalog.json"
README_PATH = TEMPLATE_ROOT / "README.md"

EXPECTED_TEMPLATE_IDS = {
    "adapter-evidence",
    "approval",
    "dogfood",
    "feature",
    "maintenance-handoff",
    "migration",
    "release",
}

PLACEHOLDER_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
LOCAL_LINK_RE = re.compile(r"\[[^]]+\]\(([^)]+)\)")
FORBIDDEN_PUBLIC_MARKERS = (
    re.compile(r"authorization:\s*(bearer|basic)\s+", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/-]{8,}", re.IGNORECASE),
    re.compile(r"data:application/pdf", re.IGNORECASE),
    re.compile("%" + "PDF-"),
)


class TestMillefeuilleDeliveryTemplates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        cls.entries = cls.catalog["templates"]

    def _template_path(self, entry):
        path = (TEMPLATE_ROOT / entry["path"]).resolve()
        self.assertTrue(path.is_relative_to(TEMPLATE_ROOT.resolve()))
        return path

    def _template_text(self, entry):
        return self._template_path(entry).read_text(encoding="utf-8")

    def test_catalog_is_complete_and_paths_are_safe(self):
        self.assertEqual(
            self.catalog["schema_version"],
            "millefeuille-delivery-template-catalog/v0.1",
        )
        self.assertEqual(
            self.catalog["placeholder_pattern"],
            r"\{\{[a-z][a-z0-9_]*\}\}",
        )

        ids = [entry["id"] for entry in self.entries]
        self.assertEqual(set(ids), EXPECTED_TEMPLATE_IDS)
        self.assertEqual(len(ids), len(set(ids)))

        paths = [entry["path"] for entry in self.entries]
        self.assertEqual(len(paths), len(set(paths)))
        for entry in self.entries:
            with self.subTest(template=entry["id"]):
                path = self._template_path(entry)
                self.assertEqual(path.suffix, ".md")
                self.assertTrue(path.is_file())

    def test_templates_keep_required_sections_and_placeholders(self):
        for entry in self.entries:
            with self.subTest(template=entry["id"]):
                text = self._template_text(entry)
                headings = {
                    line.removeprefix("## ")
                    for line in text.splitlines()
                    if line.startswith("## ")
                }
                self.assertTrue(set(entry["required_headings"]).issubset(headings))

                placeholders = set(PLACEHOLDER_RE.findall(text))
                self.assertTrue(
                    set(entry["required_placeholders"]).issubset(placeholders)
                )
                self.assertNotIn("TODO", text)

                lowered = text.casefold()
                for marker in entry["required_markers"]:
                    self.assertIn(marker.casefold(), lowered)

    def test_templates_keep_publication_and_privacy_boundary(self):
        for entry in self.entries:
            with self.subTest(template=entry["id"]):
                text = self._template_text(entry)
                self.assertIn("## Publication Boundary", text)
                self.assertIn("credential", text.casefold())
                for pattern in FORBIDDEN_PUBLIC_MARKERS:
                    self.assertIsNone(pattern.search(text))

    def test_readme_links_every_template_and_local_contract(self):
        text = README_PATH.read_text(encoding="utf-8")
        for entry in self.entries:
            with self.subTest(template=entry["id"]):
                self.assertIn(f"({entry['path']})", text)

        for target in LOCAL_LINK_RE.findall(text):
            if "://" in target or target.startswith("#"):
                continue
            with self.subTest(link=target):
                self.assertTrue((TEMPLATE_ROOT / target).resolve().exists())

    def test_approval_template_preserves_live_run_gate_fields(self):
        entry = next(item for item in self.entries if item["id"] == "approval")
        text = self._template_text(entry)
        required = {
            "staging_tag",
            "maximum_item_count",
            "allowed_operation",
            "output_root",
            "source_pack_root",
            "ocr_backend",
            "provider_budget",
            "openkb_target",
            "disposal_policy",
            "stop_condition",
            "rollback_action",
        }
        self.assertTrue(required.issubset(set(PLACEHOLDER_RE.findall(text))))
        self.assertIn("Anything not listed here is denied", text)

    def test_dogfood_template_preserves_carte_blanche_boundary(self):
        entry = next(item for item in self.entries if item["id"] == "dogfood")
        text = self._template_text(entry)
        self.assertIn("no-write evidence for planning surfaces only", text)
        self.assertIn("It is not release, promotion", text)
        self.assertIn("cannot create or widen authority retroactively", text)
        self.assertIn("## Reconciliation", text)
        self.assertIn("## Carry-Forward Constraints", text)

    def test_feature_and_release_templates_preserve_branch_policy(self):
        feature = next(item for item in self.entries if item["id"] == "feature")
        release = next(item for item in self.entries if item["id"] == "release")
        feature_text = self._template_text(feature)
        release_text = self._template_text(release)

        self.assertIn("Feature PRs target `dev`", feature_text)
        self.assertIn("release/promotion PRs target `main`", feature_text)
        self.assertIn("`dev` to `main` promotion PR", release_text)
        self.assertIn("Tag creation requires separate approval", release_text)
        self.assertIn("Package publication requires separate approval", release_text)


if __name__ == "__main__":
    unittest.main()
