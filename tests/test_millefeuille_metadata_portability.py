"""Portable reads stay safe; unqualified native writes have no effects."""

import builtins
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from millefeuille.cli.commands import dry_run_command
from millefeuille.domain import metadata_transaction as transaction
from millefeuille.domain import secure_io
from millefeuille.domain.artifacts import load_artifact_index, write_artifact_index
from millefeuille.domain.config import ArtifactExportConfig, ExportConfig
from millefeuille.domain.millefeuille import MillefeuilleContractError
from tests.metadata_fixtures import apply_complete_preview_fixture, write_fixture_json
from tests.test_millefeuille_artifact_writer import _make_app_config
from tests.test_millefeuille_stage_cli import _prepare_fixture_run


class PortableMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fallback = patch.object(
            secure_io, "_supports_no_follow", return_value=False
        )
        self.fallback.start()
        self.addCleanup(self.fallback.stop)

    def test_binding_matches_exact_bytes_and_missing_leaf(self):
        path = self.root / "metadata.json"
        path.write_bytes(b'{"stable": true}\n')
        binding = transaction.capture_metadata_binding(path)
        self.assertEqual(binding["bytes"], path.stat().st_size)
        self.assertEqual(
            binding["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
        )
        self.assertEqual(binding["inode"], [path.stat().st_dev, path.stat().st_ino])
        self.assertIsNone(
            transaction.capture_metadata_binding(self.root / "missing.json")
        )

    def test_binding_refuses_hardlink_before_read(self):
        path = self.root / "metadata.json"
        path.write_text("stable")
        try:
            os.link(path, self.root / "other.json")
        except (OSError, NotImplementedError):
            self.skipTest("hardlink creation is unavailable")
        with patch.object(transaction, "read_bytes_no_follow") as reader:
            with self.assertRaisesRegex(MillefeuilleContractError, "single-link"):
                transaction.capture_metadata_binding(path)
            reader.assert_not_called()

    def test_binding_refuses_byte_cap_and_mutation(self):
        path = self.root / "metadata.json"
        path.write_text("stable")
        with (
            patch.object(transaction, "_MAX_FILE_BYTES", 3),
            self.assertRaisesRegex(MillefeuilleContractError, "exceeds"),
        ):
            transaction.capture_metadata_binding(path)
        real = transaction.read_bytes_no_follow

        def mutate(*args, **kwargs):
            data = real(*args, **kwargs)
            path.write_text("drifted")
            return data

        with (
            patch.object(transaction, "read_bytes_no_follow", side_effect=mutate),
            self.assertRaisesRegex(MillefeuilleContractError, "changed"),
        ):
            transaction.capture_metadata_binding(path)

    def test_binding_refuses_same_byte_inode_swap(self):
        path = self.root / "metadata.json"
        path.write_text("stable")
        replacement = self.root / "replacement.json"
        replacement.write_bytes(path.read_bytes())
        before = path.stat()
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        real = transaction.read_bytes_no_follow

        def swap(*args, **kwargs):
            replacement.replace(path)
            return real(*args, **kwargs)

        with (
            patch.object(transaction, "read_bytes_no_follow", side_effect=swap),
            self.assertRaisesRegex(MillefeuilleContractError, "changed"),
        ):
            transaction.capture_metadata_binding(path)

    def test_guard_checks_pending_before_and_after_read(self):
        pending = self.root / transaction.PENDING
        pending.mkdir()
        with (
            self.assertRaisesRegex(MillefeuilleContractError, "pending"),
            transaction.metadata_read_guard(self.root),
        ):
            self.fail("read admitted while recovery is required")
        pending.rmdir()
        with (
            self.assertRaisesRegex(MillefeuilleContractError, "pending"),
            transaction.metadata_read_guard(self.root),
        ):
            pending.mkdir()

    def test_guard_refuses_run_identity_swap(self):
        run = self.root / "run"
        run.mkdir()
        with (
            self.assertRaisesRegex(MillefeuilleContractError, "directory changed"),
            transaction.metadata_read_guard(run),
        ):
            run.rename(self.root / "old-run")
            run.mkdir()

    def test_guard_and_binding_refuse_symlinked_parent_before_read(self):
        actual = self.root / "actual"
        actual.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(actual, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic-link creation is unavailable")
        with patch.object(transaction, "read_bytes_no_follow") as reader:
            with self.assertRaisesRegex(MillefeuilleContractError, "symbolic links"):
                transaction.capture_metadata_binding(link / "missing.json")
            with (
                self.assertRaisesRegex(MillefeuilleContractError, "symbolic links"),
                transaction.metadata_read_guard(link),
            ):
                self.fail("symlinked parent admitted")
            reader.assert_not_called()

    def test_guard_refuses_ancestor_swap_even_when_run_inode_is_preserved(self):
        parent = self.root / "parent"
        run = parent / "run"
        run.mkdir(parents=True)
        with (
            self.assertRaisesRegex(MillefeuilleContractError, "ancestor changed"),
            transaction.metadata_read_guard(run),
        ):
            parent.rename(self.root / "old-parent")
            parent.mkdir()
            (self.root / "old-parent/run").rename(run)

    def test_fixture_construction_works_without_production_metadata_writes(self):
        with patch.object(
            transaction,
            "write_metadata_transaction",
            side_effect=AssertionError("production writer used"),
        ):
            root, run = _prepare_fixture_run(self.temp.name)
            with patch.object(
                Path,
                "write_text",
                side_effect=AssertionError("text translation must not be used"),
            ):
                apply_complete_preview_fixture(root, run)
                probe = self.root / "utf8-lf.json"
                write_fixture_json(probe, {"title": "café"})
            self.assertNotIn(b"\r\n", probe.read_bytes())
            self.assertEqual(json.loads(probe.read_bytes()), {"title": "café"})
            self.assertEqual(
                load_artifact_index(run / "artifact-index.json").paper_id,
                "zotero-ITEM1",
            )
            self.assertEqual(
                json.loads((run / "reports/acceptance-summary.json").read_text())[
                    "status"
                ],
                "pass",
            )


class UnsupportedMetadataWriteTests(unittest.TestCase):
    def test_missing_fcntl_refuses_direct_and_export_writes_before_effects(self):
        real_import = builtins.__import__

        def unsupported(name, globals=None, locals=None, fromlist=(), level=0):
            if (
                name == "fcntl"
                and globals
                and (globals.get("__name__") == transaction.__name__)
            ):
                raise ImportError("simulated unsupported native writer")
            return real_import(name, globals, locals, fromlist, level)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "sentinel"
            sentinel.write_bytes(b"unchanged")
            cfg = _make_app_config(
                export=ExportConfig(
                    artifacts=ArtifactExportConfig(
                        enabled=True,
                        artifact_root=str(root / "artifacts"),
                        source_pack_root=str(root / "source-packs"),
                        source_pack_intake_evidence_path=str(
                            root / "missing-evidence.json"
                        ),
                    )
                )
            )
            client = MagicMock()
            with patch.object(builtins, "__import__", side_effect=unsupported):
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "POSIX transaction"
                ):
                    transaction.write_metadata_transaction(
                        root / "new-run", {"stage-manifest.json": "{}"}
                    )
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "POSIX transaction"
                ):
                    dry_run_command(cfg, MagicMock(), client)
                client.get_items_by_selection.assert_not_called()
                from millefeuille.domain import offline_stages
                from tests.test_millefeuille_stage_cli import _prepare_source_pack_run

                pack, run = _prepare_source_pack_run(temporary)
                with patch.object(offline_stages, "_write_stage") as stage_writer:
                    with self.assertRaisesRegex(
                        MillefeuilleContractError, "POSIX transaction"
                    ):
                        offline_stages.write_offline_fixture_stage(
                            stage="extract-native",
                            source_pack_root=pack,
                            run_id="run-fixture",
                            paper_id="zotero-ITEM1",
                            evidence_path=root / "missing-evidence.json",
                        )
                    stage_writer.assert_not_called()
                import shutil

                shutil.rmtree(pack)
                (root / "recovered.pdf").unlink()
            self.assertEqual(list(root.iterdir()), [sentinel])
            self.assertEqual(sentinel.read_bytes(), b"unchanged")

    def test_absent_no_follow_and_rename_refuse_before_directory_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "new-run"
            for seam in (
                patch.object(secure_io, "_supports_no_follow", return_value=False),
                patch.object(
                    transaction.ctypes, "CDLL", return_value=SimpleNamespace()
                ),
            ):
                with seam, self.assertRaises(MillefeuilleContractError):
                    transaction.write_metadata_transaction(
                        target, {"stage-manifest.json": "{}"}
                    )
                self.assertFalse(target.exists())

    def test_artifact_index_write_refuses_without_changing_existing_bytes(self):
        fixture = (
            Path(__file__).parent
            / "fixtures/millefeuille_artifact_index/complete/artifact-index.json"
        )
        index = load_artifact_index(fixture)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "artifact-index.json"
            target.write_bytes(b"sentinel")
            with (
                patch.object(secure_io, "_supports_no_follow", return_value=False),
                self.assertRaises(MillefeuilleContractError),
            ):
                write_artifact_index(index, target)
            self.assertEqual(target.read_bytes(), b"sentinel")
            self.assertEqual(list(target.parent.iterdir()), [target])
