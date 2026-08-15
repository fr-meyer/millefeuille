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
    (
        "authorization-header",
        re.compile(r"authorization:\s*(bearer|basic)\s+", re.IGNORECASE),
    ),
    (
        "bearer-token",
        re.compile(r"\bbearer\s+[A-Za-z0-9._~+/-]{8,}", re.IGNORECASE),
    ),
    (
        "credential-assignment",
        re.compile(
            r"\b(?:api[_-]?key|access[_-]?token|password|client[_-]?secret|"
            r"private[_-]?key)\s*[:=]\s*"
            r"(?!\{\{|not-applicable\b|none\b|redacted\b)"
            r"[\"']?[A-Za-z0-9+/_.=-]{12,}",
            re.IGNORECASE,
        ),
    ),
    (
        "provider-key",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|"
            r"gh[pousr]_[A-Za-z0-9]{20,})\b"
        ),
    ),
    (
        "authenticated-url-userinfo",
        re.compile(r"https?://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
    ),
    (
        "authenticated-url-query-secret",
        re.compile(
            r"https?://[^\s)>\]]+[?&]"
            r"(?:access_token|api_key|key|sig|signature|token)="
            r"(?!\{\{)[^&\s)<\]]+",
            re.IGNORECASE,
        ),
    ),
    (
        "private-unix-root",
        re.compile(
            r"(?<![\w.])/(?:home|Users|private|root)/[^\s`\"')\]]+"
        ),
    ),
    (
        "private-windows-root",
        re.compile(r"\b[A-Za-z]:\\(?:Users|Documents and Settings)\\", re.IGNORECASE),
    ),
    ("pdf-data-url", re.compile(r"data:application/pdf", re.IGNORECASE)),
    ("raw-pdf-signature", re.compile("%" + "PDF-")),
)

PUBLIC_ARTIFACT_PATHS = (
    REPO_ROOT / ".speculoos" / "README.md",
    REPO_ROOT
    / ".speculoos"
    / "tasks"
    / "pr-096-delivery-evidence-templates.yaml",
    TEMPLATE_ROOT / "README.md",
    CATALOG_PATH,
    REPO_ROOT / "specs" / "millefeuille-pipeline" / "README.md",
    REPO_ROOT
    / "specs"
    / "pr-096-delivery-evidence-templates"
    / "commit-message.txt",
    REPO_ROOT / "specs" / "pr-096-delivery-evidence-templates" / "pr-body.md",
) + tuple(sorted((TEMPLATE_ROOT / "templates").glob("*.md")))


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
                for marker, pattern in FORBIDDEN_PUBLIC_MARKERS:
                    self.assertIsNone(
                        pattern.search(text),
                        f"{entry['id']}: forbidden {marker}",
                    )

    def test_all_public_docs_and_metadata_reject_detectable_markers(self):
        for path in PUBLIC_ARTIFACT_PATHS:
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                for marker, pattern in FORBIDDEN_PUBLIC_MARKERS:
                    self.assertIsNone(
                        pattern.search(text),
                        f"{path.relative_to(REPO_ROOT)}: forbidden {marker}",
                    )

    def test_readme_links_every_template_and_local_contract(self):
        text = README_PATH.read_text(encoding="utf-8")
        for entry in self.entries:
            with self.subTest(template=entry["id"]):
                self.assertIn(f"({entry['path']})", text)

        for target in LOCAL_LINK_RE.findall(text):
            if "://" in target or target.startswith("#"):
                continue
            with self.subTest(link=target):
                link_path = Path(target)
                self.assertFalse(link_path.is_absolute())
                resolved = (TEMPLATE_ROOT / link_path).resolve()
                self.assertTrue(resolved.is_relative_to(REPO_ROOT))
                self.assertTrue(resolved.exists())

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
