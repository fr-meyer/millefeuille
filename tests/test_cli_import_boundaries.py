"""Fresh-interpreter regression for offline CLI import boundaries."""

import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
import unittest


class OfflineCLIImportTests(unittest.TestCase):
    def test_cli_import_defers_http_provider_implementations(self):
        if __name__ != "__main__":
            # Ordinary test-runner launch, not a containment or security worker.
            result = subprocess.run(
                [sys.executable, "-B", "-m", "tests.test_cli_import_boundaries", "-v"],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        # Run this module in a fresh interpreter; no provider module stubs or
        # altered import loaders are used. Observe effects instead of masking them.
        self.assertFalse(
            any(
                name == "requests" or name.startswith("urllib3") for name in sys.modules
            )
        )
        effects = []

        def observe(event, args):
            if event.startswith(("socket.", "subprocess.")) or event in {
                "os.system",
                "os.posix_spawn",
                "os.fork",
            }:
                effects.append(event)

        sys.addaudithook(observe)
        main = importlib.import_module("millefeuille.cli.main")
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            Path(main.__file__).resolve(), root / "millefeuille" / "cli" / "main.py"
        )
        self.assertEqual(effects, [])
        for prefix in (
            "requests",
            "urllib3",
            "millefeuille.clients.pageindex_client",
            "millefeuille.clients.pageindex_tree_client",
        ):
            self.assertFalse(
                any(
                    name == prefix or name.startswith(prefix + ".")
                    for name in sys.modules
                ),
                prefix,
            )
        self.assertTrue(callable(main.initialize_clients))
        self.assertTrue(callable(main.initialize_tree_processor))
        tree = ast.parse(Path(main.__file__).read_text())
        targets = {
            "initialize_clients": "millefeuille.clients.pageindex_client",
            "initialize_tree_processor": "millefeuille.clients.pageindex_tree_client",
        }
        for function, module in targets.items():
            node = next(
                item
                for item in tree.body
                if isinstance(item, ast.FunctionDef) and item.name == function
            )
            self.assertTrue(
                any(
                    isinstance(item, ast.ImportFrom) and item.module == module
                    for item in ast.walk(node)
                )
            )
            self.assertFalse(
                any(
                    isinstance(item, ast.ImportFrom) and item.module == module
                    for item in tree.body
                )
            )


if __name__ == "__main__":
    unittest.main()
