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
            for marker in forbidden:
                if (
                    marker == old_short
                    and portable_rel in LEGACY_OBSERVATION_POLICY_FILES
                ):
                    continue
                if marker in text:
                    violations.append(f"{rel}: {marker}")

        self.assertEqual([], violations)
