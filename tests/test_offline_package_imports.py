"""Normal imports only: no fabricated packages or provider implementations.

Run this module in a fresh interpreter. The audit hook observes, never blocks
or replaces behavior. This is import regression evidence, not sandbox admission.
"""

import ast
import os
from pathlib import Path
import subprocess
import sys
import unittest

EFFECTS = []


def observe(event, args):
    if event.startswith(("socket.", "subprocess.", "os.exec", "os.spawn")) or event in {
        "os.system",
        "os.fork",
        "os.forkpty",
        "ctypes.dlopen",
    }:
        EFFECTS.append(event)


class OfflinePackageImportTests(unittest.TestCase):
    def test_ordinary_imports_and_public_compatibility(self):
        if __name__ != "__main__":
            # A normal interpreter, not an isolation/security worker. The child
            # observes product effects only, not this test-runner launch.
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "tests.test_offline_package_imports",
                    "-v",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        sys.addaudithook(observe)
        self.assertFalse(any(n.startswith("millefeuille") for n in sys.modules))
        from millefeuille.domain.evidence_answer import answer_evidence
        from millefeuille.domain.evidence_source_adapter import iter_verified_spans
        from millefeuille.domain.reviewed_summary_handoff import (
            retrieve_reviewed_summary_refs,
        )

        for function in (
            answer_evidence,
            iter_verified_spans,
            retrieve_reviewed_summary_refs,
        ):
            self.assertTrue(callable(function))
            self.assertTrue(function.__module__.startswith("millefeuille.domain."))

        import millefeuille.clients as clients
        import millefeuille.domain as domain

        self.assertNotIn("PageContent", vars(domain))
        self.assertNotIn("OCRClientError", vars(clients))
        from millefeuille.clients import OCRClientError, ZoteroAuthError, exceptions
        from millefeuille.domain import (
            NotePayload,
            PageContent,
            ProcessingResult,
            models,
        )

        for name, value in (
            ("NotePayload", NotePayload),
            ("PageContent", PageContent),
            ("ProcessingResult", ProcessingResult),
        ):
            self.assertIs(value, getattr(models, name))
            self.assertIs(value, vars(domain)[name])
            self.assertIs(value, getattr(domain, name))
        self.assertIs(OCRClientError, exceptions.OCRClientError)
        self.assertIs(ZoteroAuthError, exceptions.ZoteroAuthError)
        self.assertIs(OCRClientError, vars(clients)["OCRClientError"])

        for package in (domain, clients):
            self.assertEqual(set(package.__all__), set(package._EXPORT_MODULES))
            self.assertEqual(len(package.__all__), len(set(package.__all__)))
            self.assertTrue(set(package.__all__) <= set(dir(package)))
            with self.assertRaisesRegex(AttributeError, "no attribute"):
                package.not_a_public_export
            self.assertNotIn("not_a_public_export", vars(package))
            # Type-checking declarations preserve every original export target.
            tree = ast.parse(Path(package.__file__).read_text())
            declarations = next(n for n in tree.body if isinstance(n, ast.If))
            targets = {
                a.name: n.module
                for n in declarations.body
                if isinstance(n, ast.ImportFrom)
                for a in n.names
            }
            self.assertEqual(targets, package._EXPORT_MODULES)
            # Real provider imports are deferred, never swallowed or replaced.
            resolver = next(
                n
                for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "__getattr__"
            )
            self.assertFalse(any(isinstance(n, ast.Try) for n in ast.walk(resolver)))

        prohibited = (
            "mistralai",
            "pyzotero",
            "pageindex",
            "httpx",
            "requests",
            "millefeuille.clients.mistral_client",
            "millefeuille.clients.zotero_client",
            "millefeuille.clients.pageindex_client",
            "millefeuille.clients.pageindex_tree_client",
            "millefeuille.domain.config",
            "millefeuille.domain.note_formatter",
        )
        self.assertEqual(
            [],
            [
                n
                for n in sys.modules
                if any(n == p or n.startswith(p + ".") for p in prohibited)
            ],
        )
        self.assertEqual([], EFFECTS)
        print(
            "IMPORT_CLOSURE="
            + repr(sorted(n for n in sys.modules if n.startswith("millefeuille")))
        )


if __name__ == "__main__":
    unittest.main()
