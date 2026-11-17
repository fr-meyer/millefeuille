"""Synthetic unit fixtures only; real-paper proof is kept outside the repository."""

import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.cli.source_pack import run_source_pack_cli
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.openkb_import import import_openkb, retrieve_imported_pages


def digest(data):
    return hashlib.sha256(data).hexdigest()


class OpenKBImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Separate authority domains, on the same fixture filesystem.
        self.fixture_root = Path(self.temp.name)
        self.protected_staging_root = self.fixture_root / "protected"
        self.protected_staging_root.mkdir(mode=0o700)
        self.root = self.fixture_root / "destinations"
        self.root.mkdir(mode=0o700)
        self.pack = self.root / "openkb"
        self.pack.mkdir()
        self.pdf = self.root / "input.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\nsynthetic-test-only\n%%EOF")
        self.source_hash = digest(self.pdf.read_bytes())
        info = {"status": "ok", "page_count": 1}
        self.manifest = {
            "schema_version": "openkb-docai-source-pack/v0.1",
            "source_hash": {"algorithm": "sha256", "value": self.source_hash},
            "source_bytes": self.pdf.stat().st_size,
            "page_count": 1,
            "canonical_filename": "test.pdf",
            "metadata": {
                "expected_sha256": self.source_hash,
                "item_key": "ITEM1",
                "attachment_key": "ATTACH1",
            },
            "extractions": {
                "native": dict(info, tool="synthetic-test"),
                "mistral-ocr": dict(info, ocr_model="mistral-ocr-latest"),
            },
            "selected_reconstruction": info,
        }
        for prefix in ("", "extractions/native/", "extractions/mistral-ocr/"):
            p = self.pack / (prefix + "pages/page-0001.md")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# Synthetic test page\n")
        raw = self.pack / "raw/provider-response.json"
        raw.parent.mkdir()
        raw.write_text(
            json.dumps(
                {
                    "model": "mistral-ocr-latest",
                    "pages": [{"index": 0}],
                    "usage_info": {"pages_processed": 1},
                }
            )
        )
        self.save_manifest()
        inventory = {
            str(p.relative_to(self.pack)): digest(p.read_bytes())
            for p in self.pack.rglob("*")
            if p.is_file()
        }
        self.evidence_hash = digest(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
        )
        self.output = self.root / "converted"

    def save_manifest(self):
        self.manifest_path = self.pack / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.manifest_hash = digest(self.manifest_path.read_bytes())

    def run_import(self, **overrides):
        args = {
            "pack": self.pack,
            "pdf": self.pdf,
            "source_sha256": self.source_hash,
            "manifest_sha256": self.manifest_hash,
            "evidence_sha256": self.evidence_hash,
            "output": self.output,
            "protected_staging_root": self.protected_staging_root,
        }
        args.update(overrides)
        return import_openkb(**args)

    def test_import_and_page_retrieval_are_not_acceptance(self):
        result = self.run_import()
        self.assertFalse(result["accepted_run"])
        pages = retrieve_imported_pages(output=self.output, pages=[1])
        self.assertEqual(pages["pages"][0]["text"], "# Synthetic test page\n")
        self.assertFalse((self.output / "stage-manifest.json").exists())
        self.assertFalse((self.output / "manifest.json").exists())
        record = json.loads((self.output / "imported-evidence.json").read_text())
        self.assertIsNone(record["immutable_backend_version"])
        self.assertEqual(record["requested_model"], record["returned_model"])
        self.assertEqual(record["provider_calls"], 0)

    def test_source_hash_mismatch_has_no_output(self):
        with self.assertRaises(MillefeuilleContractError):
            self.run_import(source_sha256="0" * 64)
        self.assertFalse(self.output.exists())

    def test_manifest_hash_mismatch_has_no_output(self):
        with self.assertRaises(MillefeuilleContractError):
            self.run_import(manifest_sha256="0" * 64)
        self.assertFalse(self.output.exists())

    def test_missing_page_fails_before_writes(self):
        (self.pack / "pages/page-0001.md").unlink()
        with self.assertRaises((MillefeuilleContractError, OSError)):
            self.run_import()
        self.assertFalse(self.output.exists())

    def test_symlink_page_rejected(self):
        p = self.pack / "pages/page-0001.md"
        p.unlink()
        p.symlink_to(self.pdf)
        with self.assertRaises(MillefeuilleContractError):
            self.run_import()
        self.assertFalse(self.output.exists())

    def test_incomplete_extraction_rejected(self):
        self.manifest["extractions"]["native"]["status"] = "failed"
        self.save_manifest()
        with self.assertRaises(MillefeuilleContractError):
            self.run_import()
        self.assertFalse(self.output.exists())

    def test_existing_destination_not_overwritten(self):
        self.run_import()
        before = (self.output / "imported-evidence.json").read_bytes()
        with self.assertRaises(MillefeuilleContractError):
            self.run_import()
        self.assertEqual(before, (self.output / "imported-evidence.json").read_bytes())

    def test_invalid_page_and_content_tamper_rejected(self):
        self.run_import()
        with self.assertRaises(MillefeuilleContractError):
            retrieve_imported_pages(output=self.output, pages=[2])
        p = self.output / "imported-evidence.json"
        record = json.loads(p.read_text())
        record["pages"][-1]["text"] = "tampered"
        p.write_text(json.dumps(record))
        with self.assertRaises(MillefeuilleContractError):
            retrieve_imported_pages(output=self.output, pages=[1])

    def test_cli_import(self):
        out, err = StringIO(), StringIO()
        code = run_source_pack_cli(
            [
                "import-openkb",
                "--pack",
                str(self.pack),
                "--pdf",
                str(self.pdf),
                "--source-sha256",
                self.source_hash,
                "--manifest-sha256",
                self.manifest_hash,
                "--evidence-sha256",
                self.evidence_hash,
                "--protected-staging-root",
                str(self.protected_staging_root),
                "--output",
                str(self.output),
            ],
            stdout=out,
            stderr=err,
        )
        self.assertEqual(code, 0, err.getvalue())
        self.assertFalse(json.loads(out.getvalue())["accepted_run"])

    def test_native_transaction_failure_rolls_back(self):
        import millefeuille.domain.metadata_transaction as transaction

        original = transaction._rename_entry
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected publication failure")
            return original(*args, **kwargs)

        with (
            patch.object(transaction, "_rename_entry", side_effect=fail_once),
            self.assertRaises((MillefeuilleContractError, OSError)),
        ):
            self.run_import()
        self.assertFalse((self.output / "imported-evidence.json").exists())
        self.assertFalse((self.output / "converted-source-manifest.json").exists())
        self.assertTrue(
            list((self.output / ".millefeuille-metadata-history").iterdir())
        )

    def test_snapshot_drift_rejected_before_write(self):
        (self.pack / "pages/page-0001.md").write_text("Changed after pinning\n")
        with self.assertRaisesRegex(MillefeuilleContractError, "snapshot"):
            self.run_import()
        self.assertFalse(self.output.exists())

    def test_platform_refusal_precedes_input_reads(self):
        with (
            patch(
                "millefeuille.domain.openkb_import.require_metadata_write_support",
                side_effect=MillefeuilleContractError("unsupported platform"),
            ),
            patch("millefeuille.domain.openkb_import._read") as read,
            self.assertRaises(MillefeuilleContractError),
        ):
            self.run_import()
        read.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_materialize_imported_source_and_structure(self):
        from millefeuille.domain.openkb_source_materialization import (
            materialize_imported_source,
        )
        from millefeuille.domain.route_fixtures import load_route_selection_sidecar
        from millefeuille.domain.source_packs import load_source_pack_manifest
        from millefeuille.domain.structure_fixtures import load_structure_sidecar

        self.run_import()
        result = materialize_imported_source(
            protected_staging_root=self.protected_staging_root,
            imported=self.output,
            pdf=self.pdf,
            source_pack_root=self.root / "source-packs",
            structure_output=self.root / "structure-computed",
        )
        self.assertEqual(result["structure_page_count"], 1)
        self.assertFalse(result["accepted_run"])
        pack = Path(result["source_pack_dir"])
        self.assertEqual((pack / "source.pdf").read_bytes(), self.pdf.read_bytes())
        self.assertEqual(
            load_source_pack_manifest(pack / "manifest.json")["source_hash"],
            "sha256:" + self.source_hash,
        )
        self.assertEqual(
            load_route_selection_sidecar(pack / "selected/route.json")["page_count"], 1
        )
        self.assertEqual(
            load_structure_sidecar(pack / "structure/structure.json")["page_count"], 1
        )
        self.assertFalse((pack / "analyses/millefeuille/accepted-run").exists())

    def test_materialize_rejects_tampered_import_before_new_pack(self):
        from millefeuille.domain.openkb_source_materialization import (
            materialize_imported_source,
        )

        self.run_import()
        p = self.output / "converted-source-manifest.json"
        payload = json.loads(p.read_text())
        payload["source_hash"] = "sha256:" + "0" * 64
        p.write_text(json.dumps(payload))
        with self.assertRaises(MillefeuilleContractError):
            materialize_imported_source(
                protected_staging_root=self.protected_staging_root,
                imported=self.output,
                pdf=self.pdf,
                source_pack_root=self.root / "source-packs",
                structure_output=self.root / "structure-computed",
            )
        self.assertFalse((self.root / "source-packs/zotero/zotero-ITEM1").exists())

    def test_destination_created_during_staging_is_not_adopted(self):
        from millefeuille.domain import openkb_import as module

        real = module.reserve_directory

        def race(path, **kwargs):
            Path(path).mkdir()
            (Path(path) / "foreign.txt").write_text("foreign")
            return real(path, **kwargs)

        with (
            patch.object(module, "reserve_directory", side_effect=race),
            self.assertRaises(MillefeuilleContractError),
        ):
            self.run_import()
        self.assertEqual(list(self.output.iterdir()), [self.output / "foreign.txt"])

    def test_reserved_destination_replacement_is_not_adopted(self):
        from millefeuille.domain import openkb_import as module

        real = module.write_metadata_transaction
        moved = self.root / "owned-moved"

        def race(directory, outputs, **kwargs):
            Path(directory).rename(moved)
            Path(directory).mkdir()
            (Path(directory) / "foreign.txt").write_text("foreign")
            return real(directory, outputs, **kwargs)

        with (
            patch.object(module, "write_metadata_transaction", side_effect=race),
            self.assertRaises(MillefeuilleContractError),
        ):
            self.run_import()
        self.assertEqual(list(self.output.iterdir()), [self.output / "foreign.txt"])
        self.assertEqual(list(moved.iterdir()), [])

    def test_reserved_destination_symlink_replacement_is_rejected(self):
        from millefeuille.domain import openkb_import as module

        real = module.write_metadata_transaction
        foreign = self.root / "foreign"
        foreign.mkdir()

        def race(directory, outputs, **kwargs):
            Path(directory).rename(self.root / "owned-moved")
            Path(directory).symlink_to(foreign, target_is_directory=True)
            return real(directory, outputs, **kwargs)

        with (
            patch.object(module, "write_metadata_transaction", side_effect=race),
            self.assertRaises((MillefeuilleContractError, OSError)),
        ):
            self.run_import()
        self.assertEqual(list(foreign.iterdir()), [])

    def test_materialization_foreign_destination_is_not_adopted(self):
        from millefeuille.domain import openkb_source_materialization as module

        self.run_import()
        real = module.reserve_directory
        foreign = self.root / "packs/zotero/zotero-ITEM1"

        def race(path, **kwargs):
            Path(path).mkdir(parents=True)
            (Path(path) / "source.pdf").write_bytes(b"foreign")
            return real(path, **kwargs)

        with (
            patch.object(module, "reserve_directory", side_effect=race),
            self.assertRaises(MillefeuilleContractError),
        ):
            module.materialize_imported_source(
                protected_staging_root=self.protected_staging_root,
                imported=self.output,
                pdf=self.pdf,
                source_pack_root=self.root / "packs",
                structure_output=self.root / "structure",
            )
        self.assertEqual((foreign / "source.pdf").read_bytes(), b"foreign")
        self.assertEqual(len(list(foreign.iterdir())), 1)

    def test_materialization_replacement_before_publication_is_rejected(self):
        from millefeuille.domain import openkb_source_materialization as module

        self.run_import()
        real = module.write_metadata_transaction
        destination = self.root / "packs/zotero/zotero-ITEM1"

        def race(directory, outputs, **kwargs):
            if Path(directory) == destination:
                destination.rename(self.root / "owned-pack")
                destination.mkdir()
                (destination / "foreign.txt").write_text("foreign")
            return real(directory, outputs, **kwargs)

        with (
            patch.object(module, "write_metadata_transaction", side_effect=race),
            self.assertRaises(MillefeuilleContractError),
        ):
            module.materialize_imported_source(
                protected_staging_root=self.protected_staging_root,
                imported=self.output,
                pdf=self.pdf,
                source_pack_root=self.root / "packs",
                structure_output=self.root / "structure",
            )
        self.assertEqual(list(destination.iterdir()), [destination / "foreign.txt"])
        self.assertEqual(
            (self.root / "owned-pack/source.pdf").read_bytes(), self.pdf.read_bytes()
        )

    def test_import_rejects_symlink_parent_without_writes(self):
        foreign = self.root / "foreign-parent"
        foreign.mkdir()
        link = self.root / "linked-parent"
        link.symlink_to(foreign, target_is_directory=True)
        self.output = link / "import"
        with self.assertRaises((MillefeuilleContractError, OSError)):
            self.run_import()
        self.assertEqual(list(foreign.iterdir()), [])

    def test_materialization_structure_destination_is_exclusive(self):
        from millefeuille.domain.openkb_source_materialization import (
            materialize_imported_source,
        )

        self.run_import()
        structure = self.root / "foreign-structure"
        structure.mkdir()
        (structure / "route-evidence.json").write_text("foreign")
        with self.assertRaises(MillefeuilleContractError):
            materialize_imported_source(
                protected_staging_root=self.protected_staging_root,
                imported=self.output,
                pdf=self.pdf,
                source_pack_root=self.root / "packs",
                structure_output=structure,
            )
        self.assertEqual((structure / "route-evidence.json").read_text(), "foreign")
        self.assertEqual(len(list(structure.iterdir())), 1)
        self.assertFalse((self.root / "packs/zotero/zotero-ITEM1/source.pdf").exists())

    def test_original_mkdir_first_stat_schedule_cannot_reach_destination(self):
        import os

        from millefeuille.domain import fresh_directory as fresh

        real = os.mkdir
        touched = []

        def mkdir(path, *args, **kwargs):
            result = real(path, *args, **kwargs)
            if str(path) == self.output.name and kwargs.get("dir_fd") is not None:
                touched.append(path)
                self.output.rename(self.root / "reserved-moved")
                real(self.output)
                (self.output / "foreign.txt").write_text("foreign")
            return result

        with patch.object(fresh.os, "mkdir", side_effect=mkdir):
            self.run_import()
        self.assertEqual(touched, [])
        self.assertFalse((self.output / "foreign.txt").exists())
        self.assertTrue((self.output / "imported-evidence.json").exists())

    def test_foreign_entry_at_atomic_publication_is_never_adopted(self):
        from millefeuille.domain import fresh_directory as fresh

        original = fresh._publish_directory

        def race(source_fd, parent_fd, name):
            self.output.mkdir()
            (self.output / "foreign.txt").write_text("foreign")
            return original(source_fd, parent_fd, name)

        with (
            patch.object(fresh, "_publish_directory", side_effect=race),
            self.assertRaises(MillefeuilleContractError),
        ):
            self.run_import()
        self.assertEqual(list(self.output.iterdir()), [self.output / "foreign.txt"])

    def test_materialization_publication_replacement_rejected_before_adoption(self):
        from millefeuille.domain import fresh_directory as fresh
        from millefeuille.domain.openkb_source_materialization import (
            materialize_imported_source,
        )

        self.run_import()
        original = fresh._publish_directory
        target = self.root / "packs/zotero/zotero-ITEM1"

        def race(source_fd, parent_fd, name):
            original(source_fd, parent_fd, name)
            target.rename(self.root / "reserved-moved")
            target.mkdir()
            (target / "foreign.txt").write_text("foreign")

        with (
            patch.object(fresh, "_publish_directory", side_effect=race),
            self.assertRaises(MillefeuilleContractError),
        ):
            materialize_imported_source(
                protected_staging_root=self.protected_staging_root,
                imported=self.output,
                pdf=self.pdf,
                source_pack_root=self.root / "packs",
                structure_output=self.root / "structure",
            )
        self.assertEqual(list(target.iterdir()), [target / "foreign.txt"])
        self.assertEqual(list((self.root / "reserved-moved").iterdir()), [])

    def test_legal_json_paths_round_trip_without_markdown_substitution(self):
        from millefeuille.domain.openkb_source_materialization import (
            materialize_imported_source,
        )

        self.run_import()
        for i, suffix in enumerate(
            ('"quoted', "\\backslash", "\nnewline", "é unicode")
        ):
            root = self.root / (str(i) + suffix)
            structure = self.root / ("structure" + str(i) + suffix)
            result = materialize_imported_source(
                protected_staging_root=self.protected_staging_root,
                imported=self.output,
                pdf=self.pdf,
                source_pack_root=root,
                structure_output=structure,
            )
            pack = Path(result["source_pack_dir"])
            for directory in (pack, structure):
                for p in directory.rglob("*.json"):
                    if not any(
                        s.startswith(".millefeuille-metadata-") for s in p.parts
                    ):
                        json.loads(p.read_bytes())
            route = json.loads((structure / "route-evidence.json").read_bytes())
            self.assertEqual(
                route["markdown_path"], str(structure / "selected-with-page-markers.md")
            )
            self.assertTrue(Path(route["markdown_path"]).is_file())
            self.assertEqual(
                (pack / "selected/fulltext.md").read_bytes(),
                (structure / "selected-with-page-markers.md").read_bytes(),
            )

    def test_unavailable_atomic_primitive_and_fd_cleanup(self):
        import errno
        import os

        from millefeuille.domain import fresh_directory as fresh

        before = len(os.listdir("/proc/self/fd"))
        for i in range(20):
            target = self.root / ("fd-check-" + str(i))
            with fresh.reserve_directory(
                target, protected_staging_root=self.protected_staging_root
            ):
                pass
            with (
                self.assertRaises(MillefeuilleContractError),
                fresh.reserve_directory(
                    target, protected_staging_root=self.protected_staging_root
                ),
            ):
                pass
        for code in (errno.EXDEV, errno.ENOSYS, errno.EINVAL):
            target = self.root / str(code)
            with (
                patch.object(
                    fresh,
                    "_publish_directory",
                    side_effect=OSError(code, "unsupported"),
                ),
                self.assertRaises(MillefeuilleContractError),
                fresh.reserve_directory(
                    target, protected_staging_root=self.protected_staging_root
                ),
            ):
                pass
            self.assertFalse(target.exists())
        self.assertEqual(before, len(os.listdir("/proc/self/fd")))

    def test_final_parent_cannot_be_protected_acquisition_root(self):
        from millefeuille.domain.fresh_directory import reserve_directory

        with (
            self.assertRaises(MillefeuilleContractError),
            reserve_directory("/tmp/must-not-create-reservation"),
        ):
            pass

    def test_markdown_looking_like_staging_path_is_not_rewritten(self):
        from millefeuille.domain import openkb_source_materialization as module

        self.run_import()
        stage = module._stage_imported_source
        captured = []

        def prepare(**kwargs):
            result = stage(**kwargs)
            outline = (
                Path(kwargs["structure_output"]) / "computed/zotero-ITEM1.outline.md"
            )
            content = outline.read_bytes() + str(kwargs["source_pack_root"]).encode()
            outline.write_bytes(content)
            captured.append(content)
            return result

        with patch.object(module, "_stage_imported_source", side_effect=prepare):
            module.materialize_imported_source(
                protected_staging_root=self.protected_staging_root,
                imported=self.output,
                pdf=self.pdf,
                source_pack_root=self.root / "packs",
                structure_output=self.root / "structure",
            )
        self.assertEqual(
            (self.root / "structure/computed/zotero-ITEM1.outline.md").read_bytes(),
            captured[0],
        )

    def test_missing_renameat2_symbol_fails_without_destination(self):
        from millefeuille.domain import fresh_directory as fresh

        with (
            patch.object(fresh.ctypes, "CDLL", return_value=object()),
            self.assertRaises(MillefeuilleContractError),
        ):
            self.run_import()
        self.assertFalse(self.output.exists())

    def test_untrusted_or_overlapping_explicit_staging_is_refused(self):
        from millefeuille.domain.fresh_directory import reserve_directory

        staging = self.root / "untrusted"
        staging.mkdir(mode=0o777)
        staging.chmod(0o777)
        target = self.root / "destination/child"
        with (
            self.assertRaises(MillefeuilleContractError),
            reserve_directory(target, protected_staging_root=staging),
        ):
            pass
        staging.chmod(0o700)
        with (
            self.assertRaises(MillefeuilleContractError),
            reserve_directory(self.root / "child", protected_staging_root=staging),
        ):
            pass
        self.assertFalse(target.exists())

    def test_explicit_separate_protected_namespace_is_supported(self):
        from millefeuille.domain.fresh_directory import reserve_directory

        staging = self.root / "protected"
        staging.mkdir(mode=0o700)
        target = self.root / "destination/child"
        with reserve_directory(target, protected_staging_root=staging) as (
            fd,
            identity,
        ):
            import os

            self.assertEqual(identity, (os.fstat(fd).st_dev, os.fstat(fd).st_ino))
        self.assertEqual(list(staging.iterdir()), [])
