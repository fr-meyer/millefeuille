"""Guards for the hard Millefeuille identity migration."""

from __future__ import annotations

from pathlib import Path
import subprocess
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]

LEGACY_OBSERVATION_POLICY_FILES = frozenset(
    {
        "README.md",
        "millefeuille/domain/lifecycle_tags.py",
        "specs/millefeuille-pipeline/README.md",
        "specs/millefeuille-pipeline/cli-contract.md",
        "specs/millefeuille-pipeline/lifecycle-tag-migration-plan.schema.json",
        "specs/millefeuille-pipeline/lifecycle-tag-migration.md",
        "specs/millefeuille-pipeline/lifecycle-tag-registry.schema.json",
        "specs/millefeuille-pipeline/lifecycle-tag-registry.v0.1.json",
        "specs/millefeuille-pipeline/tag-state-machine.md",
        "tests/test_millefeuille_lifecycle_tags.py",
    }
)


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        text=True,
    )
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line.strip()]


# This is an upstream wire-format identifier, not this product's identity.
# Only the exact quoted literal at these import-boundary files is exempt.
EXTERNAL_SCHEMA_LITERAL = '"openkb-docai-source-pack/v0.1"'
EXTERNAL_SCHEMA_BOUNDARIES = frozenset(
    {
        "millefeuille/domain/openkb_import.py",
        "tests/test_openkb_import.py",
        "tests/test_millefeuille_hard_rename.py",  # declaration and negative tests
    }
)


def _identity_text(path, text):
    if path in EXTERNAL_SCHEMA_BOUNDARIES:
        return text.replace(EXTERNAL_SCHEMA_LITERAL, "<external-schema>")
    return text


class TestMillefeuilleHardRename(unittest.TestCase):
    def test_old_package_and_cli_identity_are_not_tracked(self) -> None:
        old_snake = "zotero" + "_doc" + "ai" + "_pipeline"
        old_kebab = "zotero" + "-doc" + "ai" + "-pipeline"
        old_title = "Zotero " + "Doc" + "AI"
        old_long_title = "Zotero " + "Document AI " + "Pipeline"
        old_short = "doc" + "ai"
        old_camel = "Doc" + "AI"
        forbidden = (
            old_snake,
            old_kebab,
            old_title,
            old_long_title,
            old_short,
            old_camel,
            old_short.capitalize(),
            old_short.upper(),
        )

        violations: list[str] = []
        for path in _tracked_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rel = path.relative_to(REPO_ROOT)
            portable_rel = rel.as_posix()
            text = _identity_text(portable_rel, text)
            for marker in forbidden:
                if (
                    marker == old_short
                    and portable_rel in LEGACY_OBSERVATION_POLICY_FILES
                ):
                    continue
                if marker in text:
                    violations.append(f"{rel}: {marker}")

        self.assertEqual([], violations)

    def test_external_exception_does_not_hide_other_legacy_identity(self):
        marker = "doc" + "ai"
        literal = EXTERNAL_SCHEMA_LITERAL
        boundary = "millefeuille/domain/openkb_import.py"
        self.assertNotIn(marker, _identity_text(boundary, literal))
        self.assertIn(marker, _identity_text("millefeuille/other.py", literal))
        self.assertIn(marker, _identity_text(boundary, literal + " " + marker))
        self.assertIn(marker, _identity_text(boundary, literal.replace("v0.1", "v0.2")))
        self.assertIn(
            marker, _identity_text(boundary, "zotero_" + marker + "_pipeline")
        )
