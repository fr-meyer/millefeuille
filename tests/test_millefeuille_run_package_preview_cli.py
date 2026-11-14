"""Provider-free run-package CLI metadata and failure boundaries."""

from contextlib import ExitStack, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.cli.source_pack import run_source_pack_cli
from millefeuille.domain.multi_source_run_package import plan_multi_source_run_package
from tests.test_millefeuille_multi_source_run_package import (
    _saved_output_refs,
    _saved_run,
)


class RunPackagePreviewCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pack, self.run = _saved_run(self.root)
        self.source_root = self.root / "source-packs"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for guard in _saved_output_refs(self.pack):
            self.stack.enter_context(guard)
        self.args = [
            "source-pack",
            "run-package-preview",
            "--source-pack-root",
            str(self.source_root),
            "--item-key",
            "ITEM1234",
            "--run-id",
            self.run.name,
        ]

    def invoke(self, args=None):
        out, err = StringIO(), StringIO()
        code = run_source_pack_cli(
            self.args if args is None else args, stdout=out, stderr=err
        )
        return code, out.getvalue(), err.getvalue()

    def inventory(self):
        return {
            p.relative_to(self.source_root).as_posix(): p.read_bytes()
            if p.is_file()
            else None
            for p in self.source_root.rglob("*")
        }

    def test_json_exact_preview_deterministic_and_no_writes_or_providers(self):
        before = self.inventory()
        plan = plan_multi_source_run_package(
            source_pack_root=self.source_root, item_key="ITEM1234", run_id=self.run.name
        )
        with ExitStack() as guards:
            for target in (
                "millefeuille.domain.multi_source_run_package.publish_multi_source_run_package",
                "millefeuille.domain.multi_source_run_package.recover_partial_multi_source_run_package",
                "millefeuille.domain.multi_source_run_package.write_new_text_no_follow",
                "millefeuille.cli.source_pack.write_source_pack_from_recovered_pdf",
                "requests.sessions.Session.request",
                "socket.create_connection",
            ):
                guards.enter_context(
                    patch(target, side_effect=AssertionError("unexpected effect"))
                )
            first = self.invoke(self.args + ["--json"])
            self.assertEqual(first, self.invoke(self.args + ["--json"]))
        self.assertEqual(first[0], 0)
        self.assertEqual(first[2], "")
        self.assertEqual(
            json.loads(first[1]),
            {
                **plan.preview(),
                "preview_sha256": plan.preview_sha256,
                "status": "planned",
                "writes": 0,
            },
        )
        self.assertEqual(before, self.inventory())
        self.assertFalse((self.run / "stage-manifest.json").exists())
        self.assertFalse((self.run / "artifact-index.json").exists())
        self.assertNotIn("saved section", first[1])

    def test_public_entrypoint_routes_preview_without_clients(self):
        from millefeuille.cli.main import entrypoint

        out, err = StringIO(), StringIO()
        from contextlib import redirect_stdout

        with (
            patch("sys.argv", ["millefeuille", *self.args, "--json"]),
            patch(
                "millefeuille.cli.main.initialize_clients",
                side_effect=AssertionError("provider initialization"),
            ),
            redirect_stdout(out),
            redirect_stderr(err),
            self.assertRaises(SystemExit) as raised,
        ):
            entrypoint()
        self.assertEqual((raised.exception.code, err.getvalue()), (0, ""))
        self.assertEqual(json.loads(out.getvalue())["status"], "planned")

    def test_human_metadata_only(self):
        code, out, err = self.invoke()
        self.assertEqual((code, err), (0, ""))
        for text in (
            "status: planned",
            "paper_id: zotero-ITEM1234",
            "preview_sha256:",
            "planned_files: 2",
            "writes: 0; provider_calls: 0",
        ):
            self.assertIn(text, out)
        self.assertNotIn("saved section", out)
        self.assertNotIn("saved card", out)

    def test_invalid_identifiers_and_missing_path_fail_closed(self):
        for flag, value in (
            ("--item-key", "../bad"),
            ("--run-id", "../bad"),
            ("--source-pack-root", str(self.root / "missing")),
        ):
            with self.subTest(flag=flag):
                args = self.args.copy()
                args[args.index(flag) + 1] = value
                code, out, err = self.invoke(args)
                self.assertNotEqual(code, 0)
                self.assertEqual(out, "")
                self.assertIn("run-package-preview:", err)

    def test_source_input_drift_fails_closed(self):
        source = next(self.pack.rglob("*.pdf"))
        source.write_bytes(source.read_bytes() + b"synthetic drift")
        code, out, err = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
        self.assertTrue(err)

    def test_os_read_errors_are_concise(self):
        for failure in (PermissionError("permission denied"), OSError("read failed")):
            with (
                self.subTest(failure=failure),
                patch(
                    "millefeuille.domain.multi_source_run_package.Path.lstat",
                    side_effect=failure,
                ),
            ):
                code, out, err = self.invoke()
                self.assertNotEqual(code, 0)
                self.assertEqual(out, "")
                self.assertEqual(len(err.splitlines()), 1)
                self.assertNotIn("Traceback", err)

    def test_argparse_rejects_missing_arguments_and_write_flags(self):
        for args in (
            ["run-package-preview"],
            self.args + ["--publish"],
            self.args + ["--approval", "yes"],
            self.args + ["--recover"],
        ):
            with (
                self.subTest(args=args),
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                self.invoke(args)
            self.assertEqual(raised.exception.code, 2)

    def test_intake_dispatch_unchanged(self):
        with (
            patch(
                "millefeuille.cli.source_pack.load_recovered_pdf_evidence",
                return_value="evidence",
            ) as load,
            patch(
                "millefeuille.cli.source_pack.write_source_pack_from_recovered_pdf"
            ) as writer,
        ):
            writer.return_value.to_dict.return_value = {"status": "written"}
            code, out, err = self.invoke(
                [
                    "intake",
                    "--evidence",
                    "fixture.json",
                    "--source-pack-root",
                    "destination",
                    "--json",
                ]
            )
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"status": "written"})
        load.assert_called_once_with("fixture.json")
        writer.assert_called_once_with(
            evidence="evidence", source_pack_root="destination"
        )
