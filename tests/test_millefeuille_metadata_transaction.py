"""Synthetic regressions for native metadata transactions and reader barriers."""

from contextlib import suppress
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from millefeuille.domain import acceptance, stage_runtime
from millefeuille.domain.millefeuille import MillefeuilleContractError
from tests.platform_capabilities import requires_posix_batch_publication
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_handoff_jsonl,
)
from tests.test_millefeuille_status_observability import _prepare_complete_preview


def _resolved(root):
    return stage_runtime.resolve_run_artifacts(
        source_pack_root=root, paper_id=PAPER_ID, run_id=RUN_ID
    )


@requires_posix_batch_publication
class LegacyRegression(unittest.TestCase):
    def test_native_writer_refuses_symlink_without_truncating_outside(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "outside.json"
            sentinel.write_text("outside sentinel")
            stage = root / "stage-manifest.json"
            stage.symlink_to(sentinel)
            with suppress(MillefeuilleContractError, OSError):
                stage_runtime.write_json_object(stage, {"redirected": True})
            self.assertEqual(sentinel.read_text(), "outside sentinel")
            self.assertTrue(stage.is_symlink())

    def test_failed_acceptance_publishes_no_reports_or_partial_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            resolved = _resolved(root)
            summary = acceptance._build_acceptance_summary(
                resolved=resolved,
                handoff_path=_write_handoff_jsonl(temporary),
                duplicate_scan_path=None,
            )
            original = resolved.stage_manifest_path.read_bytes()
            resolved.artifact_index_path.unlink()
            resolved.artifact_index_path.mkdir()
            with self.assertRaises((MillefeuilleContractError, OSError)):
                acceptance._persist_acceptance_summary(
                    resolved=resolved, summary=summary
                )
            self.assertEqual(resolved.stage_manifest_path.read_bytes(), original)
            self.assertFalse((run / "reports/acceptance-summary.json").exists())
            self.assertFalse((run / "reports/acceptance-summary.md").exists())


@requires_posix_batch_publication
class MetadataTransactionTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module(
            "millefeuille.domain.metadata_transaction"
        )
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self, label="run"):
        run = self.root / label
        run.mkdir()
        before = {
            "stage-manifest.json": '{"stage":"old"}\n',
            "artifact-index.json": '{"index":"old"}\n',
        }
        for name, value in before.items():
            (run / name).write_text(value)
        outputs = {
            "stage-manifest.json": '{"stage":"new"}\n',
            "artifact-index.json": '{"index":"new"}\n',
            "reports/acceptance-summary.json": '{"status":"pass"}\n',
            "reports/acceptance-summary.md": "pass\n",
        }
        expected = {
            name: self.module.capture_metadata_binding(run / name) for name in before
        }
        return run, before, outputs, expected

    def assert_before(self, run, before):
        for name, value in before.items():
            self.assertEqual((run / name).read_text(), value)
        self.assertFalse((run / "reports/acceptance-summary.json").exists())
        self.assertFalse((run / "reports/acceptance-summary.md").exists())

    def test_complete_commit_retains_exact_single_link_backups(self):
        run, before, outputs, expected = self.fixture()
        self.module.write_metadata_transaction(run, outputs, expected_before=expected)
        for name, value in outputs.items():
            self.assertEqual((run / name).read_text(), value)
            self.assertEqual((run / name).stat().st_nlink, 1)
        histories = list((run / self.module.HISTORY).iterdir())
        self.assertEqual(len(histories), 1)
        for number, value in enumerate(before.values()):
            self.assertEqual((histories[0] / f"before-{number}.txt").read_text(), value)
            self.assertEqual((histories[0] / f"before-{number}.txt").stat().st_nlink, 1)
        self.assertFalse((run / self.module.PENDING).exists())
        with self.module.metadata_read_guard(run):
            pass

    def test_native_acceptance_commits_reports_and_both_metadata_files(self):
        root, run = _prepare_fixture_run(str(self.root))
        result = acceptance.write_acceptance_summary(
            source_pack_root=root,
            paper_id=PAPER_ID,
            run_id=RUN_ID,
            handoff_path=_write_handoff_jsonl(str(self.root)),
        )
        self.assertEqual(result.status, "pass")
        resolved = _resolved(root)
        self.assertEqual(
            resolved.artifact_index.stages["acceptance"]["status"], "passed"
        )
        self.assertEqual(json.loads(result.summary_path.read_text())["status"], "pass")
        self.assertFalse((run / self.module.PENDING).exists())

    def test_each_staging_or_backup_write_failure_has_no_live_effects(self):
        names = [
            "before-0.txt",
            "before-1.txt",
            "next-0.txt",
            "next-1.txt",
            "next-2.txt",
            "next-3.txt",
            "plan.json",
        ]
        writer = self.module.write_new_text_no_follow
        for number, failed_name in enumerate(names):
            with self.subTest(failed_name=failed_name):
                run, before, outputs, expected = self.fixture(f"case-{number}")

                def fail_write(path, text, label, *, selected=failed_name, **kwargs):
                    writer(path, text, label, **kwargs)
                    if path.name == selected:
                        raise OSError("synthetic post-write failure")

                with (
                    patch.object(self.module, "write_new_text_no_follow", fail_write),
                    self.assertRaises(MillefeuilleContractError),
                ):
                    self.module.write_metadata_transaction(
                        run, outputs, expected_before=expected
                    )
                self.assert_before(run, before)
                self.assertTrue(list((run / self.module.HISTORY).iterdir()))

    def test_each_live_rename_failure_restores_originals_and_removes_new_reports(self):
        rename = self.module._rename_entry
        for failed_step in range(1, 5):
            with self.subTest(failed_step=failed_step):
                run, before, outputs, expected = self.fixture(f"case-{failed_step}")
                counter = [0]

                def fail_commit(
                    sfd,
                    source,
                    dfd,
                    destination,
                    *,
                    selected=failed_step,
                    count=counter,
                    selected_outputs=outputs,
                    **kwargs,
                ):
                    rename(sfd, source, dfd, destination, **kwargs)
                    if source.startswith("next-") and destination in {
                        Path(p).name for p in selected_outputs
                    }:
                        count[0] += 1
                        if count[0] == selected:
                            raise OSError("synthetic error after rename")

                with (
                    patch.object(self.module, "_rename_entry", fail_commit),
                    self.assertRaises(MillefeuilleContractError),
                ):
                    self.module.write_metadata_transaction(
                        run, outputs, expected_before=expected
                    )
                self.assert_before(run, before)

    def test_input_drift_preserves_backups_and_keeps_reader_barrier(self):
        run, before, outputs, expected = self.fixture()
        source = self.root / "input.txt"
        source.write_text("before input")
        check = self.module.capture_input_files([source])
        rename = self.module._rename_entry
        changed = [False]

        def drift(sfd, src, dfd, dst, **kwargs):
            rename(sfd, src, dfd, dst, **kwargs)
            if dst == "stage-manifest.json" and not changed[0]:
                changed[0] = True
                source.write_text("synthetic changed input")

        with (
            patch.object(self.module, "_rename_entry", drift),
            self.assertRaisesRegex(MillefeuilleContractError, "recovery required"),
        ):
            self.module.write_metadata_transaction(
                run, outputs, expected_before=expected, check_inputs=check
            )
        self.assert_before(run, before)
        with (
            self.assertRaisesRegex(MillefeuilleContractError, "pending"),
            self.module.metadata_read_guard(run),
        ):
            pass

    def test_run_inode_drift_preserves_displaced_state(self):
        run, before, outputs, expected = self.fixture()
        displaced = self.root / "displaced"
        rename = self.module._rename_entry
        changed = [False]

        def drift(sfd, src, dfd, dst, **kwargs):
            rename(sfd, src, dfd, dst, **kwargs)
            if dst == "stage-manifest.json" and not changed[0]:
                changed[0] = True
                run.rename(displaced)
                run.mkdir()

        with (
            patch.object(self.module, "_rename_entry", drift),
            self.assertRaisesRegex(MillefeuilleContractError, "recovery required"),
        ):
            self.module.write_metadata_transaction(
                run, outputs, expected_before=expected
            )
        self.assert_before(displaced, before)
        self.assertTrue((displaced / self.module.PENDING).is_dir())
        self.assertEqual(list(run.iterdir()), [])

    def test_symlink_fifo_and_hardlink_outputs_refuse_without_blocking(self):
        for kind in ("symlink", "fifo", "hardlink"):
            with self.subTest(kind=kind):
                run, before, outputs, expected = self.fixture(kind)
                target = run / "reports/acceptance-summary.json"
                target.parent.mkdir()
                sentinel = self.root / (kind + "-outside.json")
                sentinel.write_text("outside sentinel")
                if kind == "symlink":
                    target.symlink_to(sentinel)
                elif kind == "fifo":
                    os.mkfifo(target)
                else:
                    os.link(sentinel, target)
                with self.assertRaises(MillefeuilleContractError):
                    self.module.write_metadata_transaction(
                        run, outputs, expected_before=expected
                    )
                self.assertEqual(sentinel.read_text(), "outside sentinel")
                self.assertEqual(
                    (run / "stage-manifest.json").read_text(),
                    before["stage-manifest.json"],
                )

    def test_last_instant_foreign_symlink_is_preserved_and_never_followed(self):
        run, before, outputs, expected = self.fixture()
        outside = self.root / "outside.json"
        outside.write_text("outside sentinel")
        rename = self.module._rename_entry
        changed = [False]

        def swap(sfd, src, dfd, dst, **kwargs):
            if dst == "stage-manifest.json" and not changed[0]:
                changed[0] = True
                (run / dst).unlink()
                (run / dst).symlink_to(outside)
            rename(sfd, src, dfd, dst, **kwargs)

        with (
            patch.object(self.module, "_rename_entry", swap),
            self.assertRaisesRegex(MillefeuilleContractError, "recovery required"),
        ):
            self.module.write_metadata_transaction(
                run, outputs, expected_before=expected
            )
        self.assertEqual(outside.read_text(), "outside sentinel")
        self.assertTrue((run / self.module.PENDING / "next-0.txt").is_symlink())
        self.assertEqual(
            (run / self.module.PENDING / "before-0.txt").read_text(),
            before["stage-manifest.json"],
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "pending"):
            stage_runtime.load_stage_manifest(run / "stage-manifest.json")

    def test_last_instant_rollback_swap_is_preserved_and_run_remains_blocked(self):
        run, before, outputs, expected = self.fixture()
        rename = self.module._rename_entry
        index_calls = [0]

        def swap(sfd, src, dfd, dst, **kwargs):
            if dst == "artifact-index.json":
                index_calls[0] += 1
                if index_calls[0] == 2:
                    (run / dst).unlink()
                    (run / dst).write_text("foreign rollback sentinel")
            rename(sfd, src, dfd, dst, **kwargs)
            if dst == "artifact-index.json" and index_calls[0] == 1:
                raise OSError("synthetic commit failure")

        with (
            patch.object(self.module, "_rename_entry", swap),
            self.assertRaisesRegex(MillefeuilleContractError, "recovery required"),
        ):
            self.module.write_metadata_transaction(
                run, outputs, expected_before=expected
            )
        self.assertEqual(
            (run / self.module.PENDING / "next-1.txt").read_text(),
            "foreign rollback sentinel",
        )
        with (
            self.assertRaisesRegex(MillefeuilleContractError, "pending"),
            self.module.metadata_read_guard(run),
        ):
            pass

    def test_failed_rollback_never_returns_acceptance_and_blocks_restart(self):
        run, before, outputs, expected = self.fixture()
        rename = self.module._rename_entry
        stage_calls = [0]

        def fail(sfd, src, dfd, dst, **kwargs):
            if dst == "artifact-index.json":
                raise OSError("synthetic second-entry failure")
            if dst == "stage-manifest.json":
                stage_calls[0] += 1
                if stage_calls[0] == 2:
                    raise OSError("synthetic rollback failure")
            rename(sfd, src, dfd, dst, **kwargs)

        with (
            patch.object(self.module, "_rename_entry", fail),
            self.assertRaisesRegex(MillefeuilleContractError, "recovery required"),
        ):
            self.module.write_metadata_transaction(
                run, outputs, expected_before=expected
            )
        self.assertFalse((run / "reports/acceptance-summary.json").exists())
        self.assertEqual(
            (run / self.module.PENDING / "before-0.txt").read_text(),
            before["stage-manifest.json"],
        )
        with (
            self.assertRaisesRegex(MillefeuilleContractError, "pending"),
            self.module.metadata_read_guard(run),
        ):
            pass

    def test_commit_marker_failure_rolls_back_without_acceptance_reports(self):
        run, before, outputs, expected = self.fixture()
        rename = self.module._rename_entry

        def fail(sfd, src, dfd, dst, **kwargs):
            if src == self.module.PENDING and not dst.endswith("-rolled-back"):
                raise OSError("synthetic marker clear failure")
            rename(sfd, src, dfd, dst, **kwargs)

        with (
            patch.object(self.module, "_rename_entry", fail),
            self.assertRaises(MillefeuilleContractError),
        ):
            self.module.write_metadata_transaction(
                run, outputs, expected_before=expected
            )
        self.assert_before(run, before)

    def test_post_commit_fsync_failure_rolls_back_and_never_returns_pass(self):
        run, before, outputs, expected = self.fixture()
        fsync = self.module.os.fsync
        failed = [False]

        def fail(fd):
            fsync(fd)
            history = run / self.module.HISTORY
            if (
                not failed[0]
                and not (run / self.module.PENDING).exists()
                and history.exists()
                and list(history.iterdir())
            ):
                failed[0] = True
                raise OSError("synthetic post-commit fsync failure")

        with (
            patch.object(self.module.os, "fsync", fail),
            self.assertRaises(MillefeuilleContractError),
        ):
            self.module.write_metadata_transaction(
                run, outputs, expected_before=expected
            )
        self.assertTrue(failed[0])
        self.assert_before(run, before)

    def test_pending_marker_and_recovery_history_collisions_preserve_existing(self):
        run, before, outputs, expected = self.fixture()
        marker = run / self.module.PENDING
        marker.mkdir()
        (marker / "sentinel").write_text("recovery sentinel")
        with self.assertRaisesRegex(MillefeuilleContractError, "pending"):
            self.module.write_metadata_transaction(
                run, outputs, expected_before=expected
            )
        self.assertEqual((marker / "sentinel").read_text(), "recovery sentinel")
        self.assert_before(run, before)

    def test_history_collision_preserves_existing_recovery_evidence(self):
        run, before, outputs, expected = self.fixture()
        packet = [
            {
                "path": name,
                "before": expected.get(name),
                "after": {
                    "bytes": len(value.encode()),
                    "sha256": hashlib.sha256(value.encode()).hexdigest(),
                },
            }
            for name, value in outputs.items()
        ]
        archive_name = hashlib.sha256(
            json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        existing = run / self.module.HISTORY / archive_name
        existing.mkdir(parents=True)
        (existing / "sentinel").write_text("history sentinel")
        with self.assertRaisesRegex(MillefeuilleContractError, "journal collision"):
            self.module.write_metadata_transaction(
                run, outputs, expected_before=expected
            )
        self.assertEqual((existing / "sentinel").read_text(), "history sentinel")
        self.assert_before(run, before)

    def test_expected_before_and_run_inode_drift_are_preflight_refusals(self):
        run, before, outputs, expected = self.fixture()
        (run / "stage-manifest.json").write_text("changed metadata")
        with self.assertRaisesRegex(MillefeuilleContractError, "expected-before"):
            self.module.write_metadata_transaction(
                run, outputs, expected_before=expected
            )
        with self.assertRaisesRegex(MillefeuilleContractError, "run directory drift"):
            self.module.write_metadata_transaction(
                run, outputs, expected_directory_identity=(0, 0)
            )
        self.assertFalse((run / self.module.PENDING).exists())

    def test_origin_helper_shapes_bind_only_the_validated_original_run(self):
        from millefeuille.domain import multi_source_run_package as package

        root = self.root / "source-packs"
        pack = root / "zotero/zotero-ITEM1"
        run = root / "analyses/millefeuille/run-card"
        origin = root / "analyses/millefeuille/run-original"
        unrelated = root / "analyses/millefeuille/run-unrelated"
        for directory in (
            pack,
            run / "summaries",
            run / "cards",
            run / "index",
            origin / "summaries",
            origin / "structure/inputs",
            unrelated,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (pack / "manifest.json").write_text("{}\n")
        (run / "summaries/hierarchical-summary.json").write_text("{}\n")
        (origin / "summaries/hierarchical-summary.json").write_text("{}\n")
        selected = origin / "structure/inputs/selected.md"
        os.mkfifo(unrelated / "must-not-open")
        for helper_tree in (origin / "summaries", origin):
            with self.subTest(helper_tree=helper_tree):
                selected.write_text("synthetic original snapshot")
                # Test-only topology stub: native link/ref validators are retained
                # in application code. This isolates both validated helper shapes.
                with patch.object(
                    package,
                    "_published_summary_inventory_paths",
                    return_value=((helper_tree,), ()),
                ):
                    check = self.module.capture_run_inputs(root, pack, run, [])
                    check()
                    selected.write_text("synthetic original mutation")
                    with self.assertRaisesRegex(
                        MillefeuilleContractError, "input drift"
                    ):
                        check()

    def test_native_pair_reader_waits_for_complete_transaction(self):
        root, run = _prepare_fixture_run(str(self.root))
        resolved = _resolved(root)
        stage_payload = resolved.stage_manifest.to_dict()
        stage_payload["stages"][0]["notes"].append("transaction fixture")
        outputs = {
            "stage-manifest.json": json.dumps(stage_payload, indent=2, sort_keys=True)
            + "\n",
            "artifact-index.json": json.dumps(
                resolved.artifact_index.to_dict(), indent=2, sort_keys=True
            )
            + "\n",
        }
        wrote_first = threading.Event()
        release_writer = threading.Event()
        reader_started = threading.Event()
        reader_done = threading.Event()
        errors = []
        observed = []
        rename = self.module._rename_entry

        def pause(sfd, src, dfd, dst, **kwargs):
            rename(sfd, src, dfd, dst, **kwargs)
            if dst == "stage-manifest.json":
                wrote_first.set()
                if not release_writer.wait(5):
                    raise TimeoutError("synthetic reader test")

        def writer():
            try:
                self.module.write_metadata_transaction(run, outputs)
            except Exception as error:
                errors.append(error)

        def reader():
            reader_started.set()
            try:
                observed.append(_resolved(root).stage_manifest.to_dict())
            except Exception as error:
                errors.append(error)
            finally:
                reader_done.set()

        with patch.object(self.module, "_rename_entry", pause):
            write_thread = threading.Thread(target=writer)
            read_thread = threading.Thread(target=reader)
            write_thread.start()
            self.assertTrue(wrote_first.wait(5))
            read_thread.start()
            self.assertTrue(reader_started.wait(5))
            self.assertFalse(reader_done.wait(0.05))
            release_writer.set()
            write_thread.join(5)
            read_thread.join(5)
            self.assertFalse(write_thread.is_alive())
            self.assertFalse(read_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(observed, [stage_payload])


@requires_posix_batch_publication
class ArtifactWriterRegression(unittest.TestCase):
    """Actual existing source-pack run; no serializer or destination-guard stubs."""

    def _export(self, root):
        from millefeuille.domain.artifact_writer import write_dry_run_artifacts
        from millefeuille.domain.config import ArtifactExportConfig
        from tests.test_millefeuille_source_pack_writer import (
            _make_handoff_row,
            _make_item,
        )

        return write_dry_run_artifacts(
            items=[_make_item()],
            handoff_rows=[_make_handoff_row()],
            config=ArtifactExportConfig(
                enabled=True,
                artifact_root="source-pack",
                source_pack_root=str(root),
                run_id=RUN_ID,
            ),
            handoff_enabled=False,
        )

    def test_existing_run_stage_symlink_never_redirects_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            sentinel = Path(temporary) / "outside.json"
            sentinel.write_text("outside sentinel")
            stage = run / "stage-manifest.json"
            index_before = (run / "artifact-index.json").read_bytes()
            stage.unlink()
            stage.symlink_to(sentinel)
            with suppress(MillefeuilleContractError, OSError):
                self._export(root)
            self.assertEqual(sentinel.read_text(), "outside sentinel")
            self.assertTrue(stage.is_symlink())
            self.assertEqual((run / "artifact-index.json").read_bytes(), index_before)

    def test_invalid_second_target_preserves_existing_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            stage_before = (run / "stage-manifest.json").read_bytes()
            index = run / "artifact-index.json"
            index.unlink()
            index.mkdir()
            with self.assertRaises((MillefeuilleContractError, OSError)):
                self._export(root)
            self.assertEqual((run / "stage-manifest.json").read_bytes(), stage_before)
            self.assertTrue(index.is_dir())

    def test_standalone_export_stage_writer_refuses_symlink(self):
        from millefeuille.domain.artifact_writer import write_stage_manifest

        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            manifest = _resolved(root).stage_manifest
            sentinel = Path(temporary) / "outside.json"
            sentinel.write_text("outside sentinel")
            stage = run / "stage-manifest.json"
            stage.unlink()
            stage.symlink_to(sentinel)
            with suppress(MillefeuilleContractError, OSError):
                write_stage_manifest(manifest, stage)
            self.assertEqual(sentinel.read_text(), "outside sentinel")
            self.assertTrue(stage.is_symlink())

    def test_export_second_commit_failure_rolls_back_the_whole_pair(self):
        from millefeuille.domain import metadata_transaction as transaction

        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            resolved = _resolved(root)
            stage = resolved.stage_manifest.to_dict()
            stage["stages"][0]["notes"].append("before-export rollback fixture")
            stage_runtime.persist_run_artifacts(
                resolved,
                stage_manifest=stage_runtime.StageManifest.from_dict(stage),
                artifact_index=resolved.artifact_index,
            )
            self.assertEqual(_resolved(root).stage_manifest.to_dict(), stage)
            before = {
                name: (run / name).read_bytes()
                for name in ("stage-manifest.json", "artifact-index.json")
            }
            rename = transaction._rename_entry
            failed = [False]

            def fail_second(source_fd, source, destination_fd, destination, **kwargs):
                rename(source_fd, source, destination_fd, destination, **kwargs)
                if destination == "artifact-index.json" and not failed[0]:
                    failed[0] = True
                    raise OSError("synthetic artifact export second-commit failure")

            with (
                patch.object(transaction, "_rename_entry", fail_second),
                self.assertRaises(MillefeuilleContractError),
            ):
                self._export(root)
            self.assertTrue(failed[0])
            for name, value in before.items():
                self.assertEqual((run / name).read_bytes(), value)
            self.assertFalse((run / transaction.PENDING).exists())
            # The actual resolver still accepts the restored pair, never a mix.
            self.assertEqual(_resolved(root).run_id, RUN_ID)


@requires_posix_batch_publication
class SharedProtocolRegression(unittest.TestCase):
    def test_nested_writer_refuses_pending_run_with_missing_pair_file(self):
        from millefeuille.domain import metadata_transaction as transaction

        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            (run / "artifact-index.json").unlink()
            (run / transaction.PENDING).mkdir()
            report = run / "reports/new.json"
            report.parent.mkdir(exist_ok=True)
            with self.assertRaisesRegex(MillefeuilleContractError, "pending"):
                stage_runtime.write_json_object(report, {"status": "pass"})
            self.assertFalse(report.exists())
            self.assertTrue((run / transaction.PENDING).is_dir())

    def test_progressive_status_refuses_actual_pending_pair(self):
        from millefeuille.domain import metadata_transaction as transaction
        from millefeuille.domain.status_observability import build_status_report

        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            (run / transaction.PENDING).mkdir()
            with self.assertRaisesRegex(MillefeuilleContractError, "pending"):
                build_status_report(index_path=run / "artifact-index.json")

    def test_status_reader_waits_for_whole_pair_in_both_modes(self):
        from millefeuille.domain import metadata_transaction as transaction
        from millefeuille.domain.status_observability import build_status_report

        for mode in ("progressive", "canonical"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root, run = _prepare_complete_preview(temporary)
                if mode == "progressive":
                    # Direct-index joins use the existing partial-index fixture.
                    # Its retrieval join requires a canonical source-pack boundary.
                    partial = _resolved(root)
                    index = partial.artifact_index.to_dict()
                    index["artifacts"].pop("retrieval_index_status")
                    stage_runtime.persist_run_artifacts(
                        partial,
                        stage_manifest=partial.stage_manifest,
                        artifact_index=stage_runtime.ArtifactIndex.from_dict(index),
                    )
                baseline_arguments = {
                    "index_path": run / "artifact-index.json",
                }
                canonical_arguments = {
                    "source_pack_root": root,
                    "paper_id": PAPER_ID,
                    "run_id": RUN_ID,
                }
                # The selected native API must accept its fixture before the race.
                build_status_report(
                    **(
                        baseline_arguments
                        if mode == "progressive"
                        else canonical_arguments
                    )
                )
                arguments = (
                    baseline_arguments if mode == "progressive" else canonical_arguments
                )
                resolved = _resolved(root)
                stage = resolved.stage_manifest.to_dict()
                stage["stages"][0]["notes"].append("pair-read fixture")
                outputs = {
                    "stage-manifest.json": json.dumps(stage, indent=2, sort_keys=True)
                    + "\n",
                    "artifact-index.json": json.dumps(
                        resolved.artifact_index.to_dict(), indent=2, sort_keys=True
                    )
                    + "\n",
                }
                first = threading.Event()
                release = threading.Event()
                started = threading.Event()
                done = threading.Event()
                errors = []
                observed = []
                rename = transaction._rename_entry

                def pause(
                    source_fd,
                    source,
                    destination_fd,
                    destination,
                    *,
                    rename=rename,
                    first=first,
                    release=release,
                    **kwargs,
                ):
                    rename(source_fd, source, destination_fd, destination, **kwargs)
                    if destination == "stage-manifest.json":
                        first.set()
                        if not release.wait(5):
                            raise TimeoutError("synthetic status-reader deadline")

                def writer(*, run=run, outputs=outputs, errors=errors):
                    try:
                        transaction.write_metadata_transaction(run, outputs)
                    except Exception as error:
                        errors.append(error)

                def reader(
                    *,
                    selected_mode=mode,
                    started=started,
                    root=root,
                    run=run,
                    observed=observed,
                    errors=errors,
                    done=done,
                ):
                    started.set()
                    try:
                        arguments = (
                            {"index_path": run / "artifact-index.json"}
                            if selected_mode == "progressive"
                            else {
                                "source_pack_root": root,
                                "paper_id": PAPER_ID,
                                "run_id": RUN_ID,
                            }
                        )
                        observed.append(build_status_report(**arguments))
                    except Exception as error:
                        errors.append(error)
                    finally:
                        done.set()

                with patch.object(transaction, "_rename_entry", pause):
                    write_thread = threading.Thread(target=writer)
                    read_thread = threading.Thread(target=reader)
                    write_thread.start()
                    self.assertTrue(first.wait(5))
                    read_thread.start()
                    self.assertTrue(started.wait(5))
                    blocked = not done.wait(0.05)
                    release.set()
                    write_thread.join(5)
                    read_thread.join(5)
                    self.assertFalse(write_thread.is_alive())
                    self.assertFalse(read_thread.is_alive())
                self.assertTrue(
                    blocked, "status read completed during partial pair transition"
                )
                self.assertEqual(errors, [])
                self.assertEqual(len(observed), 1)
                # The observer saw the committed pair; valid output claims survive.
                self.assertEqual(_resolved(root).stage_manifest.to_dict(), stage)
                self.assertEqual(
                    _resolved(root).artifact_index.to_dict(),
                    resolved.artifact_index.to_dict(),
                )
                self.assertEqual(observed, [build_status_report(**arguments)])

    def test_rollback_flushes_displaced_journal_before_archiving(self):
        from millefeuille.domain import metadata_transaction as transaction

        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            before = {
                name: (run / name).read_bytes()
                for name in ("stage-manifest.json", "artifact-index.json")
            }
            outputs = {name: value.decode() + " " for name, value in before.items()}
            rename = transaction._rename_entry
            fsync = transaction.os.fsync
            fault = [False]
            last_move_synced = [False]
            archive_proof = []

            def observe_sync(fd):
                fsync(fd)
                if fault[0] and (run / transaction.PENDING).exists():
                    journal = (run / transaction.PENDING).stat()
                    opened = os.fstat(fd)
                    if (journal.st_dev, journal.st_ino) == (
                        opened.st_dev,
                        opened.st_ino,
                    ):
                        last_move_synced[0] = True

            def fail_second(source_fd, source, destination_fd, destination, **kwargs):
                if destination.endswith("-rolled-back"):
                    archive_proof.append(last_move_synced[0])
                rename(source_fd, source, destination_fd, destination, **kwargs)
                if fault[0] and destination in before:
                    last_move_synced[0] = False
                if destination == "artifact-index.json" and not fault[0]:
                    fault[0] = True
                    raise OSError("synthetic second-commit failure")

            with (
                patch.object(transaction, "_rename_entry", fail_second),
                patch.object(transaction.os, "fsync", observe_sync),
                self.assertRaises(MillefeuilleContractError),
            ):
                transaction.write_metadata_transaction(run, outputs)
            self.assertEqual(archive_proof, [True])
            for name, value in before.items():
                self.assertEqual((run / name).read_bytes(), value)

    def test_failed_fallback_marker_does_not_claim_reader_barrier(self):
        from millefeuille.domain import metadata_transaction as transaction

        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            before = {
                name: (run / name).read_bytes()
                for name in ("stage-manifest.json", "artifact-index.json")
            }
            outputs = {name: value.decode() + " " for name, value in before.items()}
            rename = transaction._rename_entry
            mkdir = transaction.os.mkdir
            archived = [False]

            def post_archive_failure(
                source_fd, source, destination_fd, destination, **kwargs
            ):
                rename(source_fd, source, destination_fd, destination, **kwargs)
                if source == transaction.PENDING and not destination.endswith(
                    "-rolled-back"
                ):
                    archived[0] = True
                    raise OSError("synthetic post-archive failure")

            def refuse_fallback(path, *args, **kwargs):
                if path == transaction.PENDING and archived[0]:
                    raise OSError("synthetic fallback marker creation failure")
                return mkdir(path, *args, **kwargs)

            with (
                patch.object(transaction, "_rename_entry", post_archive_failure),
                patch.object(transaction.os, "mkdir", refuse_fallback),
                self.assertRaisesRegex(
                    MillefeuilleContractError, "pending barrier durability uncertain"
                ),
            ):
                transaction.write_metadata_transaction(run, outputs)
            self.assertTrue(archived[0])
            self.assertFalse((run / transaction.PENDING).exists())
            self.assertTrue(list((run / transaction.HISTORY).iterdir()))
            for name, value in before.items():
                self.assertEqual((run / name).read_bytes(), value)


@requires_posix_batch_publication
class OriginLayoutRegression(unittest.TestCase):
    def fixture(self, temporary):
        root = Path(temporary) / "source-packs"
        pack = root / "zotero" / "zotero-ITEM1"
        analysis = root / "analyses" / "millefeuille"
        run = analysis / "run-card"
        origin = analysis / "summaries"
        unrelated = analysis / "run-unrelated"
        for directory in (
            pack,
            run / "summaries",
            run / "cards",
            run / "index",
            origin / "summaries",
            origin / "structure" / "inputs",
            unrelated,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (pack / "manifest.json").write_text("{}\n")
        (run / "summaries" / "hierarchical-summary.json").write_text("{}\n")
        (origin / "summaries" / "hierarchical-summary.json").write_text("{}\n")
        selected = origin / "structure" / "inputs" / "selected.md"
        os.mkfifo(unrelated / "must-not-open")
        return (root, pack, run, origin, selected)

    def test_complete_run_named_summaries_and_legacy_subtree_stay_selected(self):
        from millefeuille.domain import metadata_transaction as transaction
        from millefeuille.domain import multi_source_run_package as package

        with tempfile.TemporaryDirectory() as temporary:
            (root, pack, run, origin, selected) = self.fixture(temporary)
            for helper_tree in (origin, origin / "summaries"):
                with self.subTest(helper_tree=helper_tree):
                    selected.write_text("synthetic original snapshot")
                    with patch.object(
                        package,
                        "_published_summary_inventory_paths",
                        return_value=((helper_tree,), ()),
                    ):
                        try:
                            check = transaction.capture_run_inputs(root, pack, run, [])
                        except MillefeuilleContractError as error:
                            self.fail(
                                "selected origin projected into unrelated runs: "
                                + str(error)
                            )
                        check()
                        selected.write_text("synthetic selected structure drift")
                        with self.assertRaisesRegex(
                            MillefeuilleContractError, "input drift"
                        ):
                            check()

    def test_other_inventory_layouts_are_rejected_before_traversal(self):
        from millefeuille.domain import metadata_transaction as transaction
        from millefeuille.domain import multi_source_run_package as package

        with tempfile.TemporaryDirectory() as temporary:
            (root, pack, run, origin, selected) = self.fixture(temporary)
            selected.write_text("synthetic original snapshot")
            analysis = root / "analyses" / "millefeuille"
            for tree in (
                analysis,
                origin / "structure",
                origin / "summaries" / "deep",
                root / "outside" / "summaries",
                analysis / "..",
            ):
                with (
                    self.subTest(tree=tree),
                    patch.object(
                        package,
                        "_published_summary_inventory_paths",
                        return_value=((tree,), ()),
                    ),
                    self.assertRaisesRegex(
                        MillefeuilleContractError, "origin inventory"
                    ),
                ):
                    transaction.capture_run_inputs(root, pack, run, [])


@requires_posix_batch_publication
class InputScopeCompatibilityRegression(unittest.TestCase):
    def test_prepared_guard_survives_native_acceptance_of_unconsumed_sibling(self):
        from tests.test_millefeuille_acceptance_batch import _clone_fixture_run

        with tempfile.TemporaryDirectory() as temporary:
            root, first_run = _prepare_fixture_run(temporary)
            second_id = "run-input-scope-second"
            _clone_fixture_run(first_run, second_id)
            second = stage_runtime.resolve_run_artifacts(
                source_pack_root=root, paper_id=PAPER_ID, run_id=second_id
            )
            handoff = _write_handoff_jsonl(temporary)
            check = acceptance._acceptance_input_guard(second, handoff)
            result = acceptance.write_acceptance_summary(
                source_pack_root=root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
                handoff_path=handoff,
            )
            self.assertEqual(result.status, "pass")
            errors = []
            try:
                check()
            except MillefeuilleContractError as error:
                errors.append(str(error))
            self.assertEqual(errors, [], "guard incorrectly consumes a sibling run")

    def test_unconsumed_sibling_fifo_is_not_read_and_selected_tree_is_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            unrelated = run.parent / "unconsumed-fifo-run"
            unrelated.mkdir()
            os.mkfifo(unrelated / "unconsumed.fifo")
            errors = []
            check = None
            try:
                check = acceptance._acceptance_input_guard(_resolved(root))
                check()
            except MillefeuilleContractError as error:
                errors.append(str(error))
            self.assertEqual(errors, [], "unconsumed sibling FIFO was traversed")
            self.assertIsNotNone(check)
            (run / "summaries/selected-new-input.txt").write_text("synthetic drift")
            with self.assertRaisesRegex(MillefeuilleContractError, "input tree drift"):
                check()

    def test_pending_run_missing_stage_still_refuses_resolution(self):
        from millefeuille.domain import metadata_transaction as transaction

        with tempfile.TemporaryDirectory() as temporary:
            root, run = _prepare_fixture_run(temporary)
            (run / "stage-manifest.json").unlink()
            (run / transaction.PENDING).mkdir()
            with self.assertRaisesRegex(MillefeuilleContractError, "pending"):
                _resolved(root)

    def test_absent_run_preserves_stage_manifest_missing_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _run = _prepare_fixture_run(temporary)
            with self.assertRaisesRegex(
                MillefeuilleContractError, "stage manifest not found"
            ):
                stage_runtime.resolve_run_artifacts(
                    source_pack_root=root,
                    paper_id=PAPER_ID,
                    run_id="absent-input-scope-run",
                )


@requires_posix_batch_publication
class ClassificationWritebackIdempotenceRegression(unittest.TestCase):
    def _complete_pipeline(self, temporary):
        from io import StringIO

        from millefeuille.cli.stages import run_stage_cli
        from tests.test_millefeuille_stage_cli import (
            _full_run_args,
            _prepare_source_pack_run,
            _write_classification_evidence_json,
            _write_offline_stage_evidence,
        )

        root, run = _prepare_source_pack_run(temporary)
        evidence = _write_offline_stage_evidence(temporary)
        classification_path = _write_classification_evidence_json(temporary)
        args = _full_run_args(
            source_pack_root=root,
            evidence=evidence,
            handoff_path=_write_handoff_jsonl(temporary),
            classification_path=classification_path,
        )
        self.assertEqual(run_stage_cli(args, stdout=StringIO()), 0)
        return root, run, classification_path

    def _classify(self, root, evidence):
        from millefeuille.domain.classification import (
            write_classification_from_evidence,
        )

        return write_classification_from_evidence(
            evidence_path=evidence,
            source_pack_root=root,
            paper_id=PAPER_ID,
            run_id=RUN_ID,
        )

    def test_unchanged_reclassification_keeps_exact_materialized_tree(self):
        from tests.test_millefeuille_stage_cli import _snapshot_files

        with tempfile.TemporaryDirectory() as temporary:
            root, _run, evidence = self._complete_pipeline(temporary)
            before = _snapshot_files(root)
            self.assertEqual(self._classify(root, evidence).status, "classified")
            self.assertEqual(
                _resolved(root).artifact_index.zotero_writeback["plan_ref"],
                "zotero-writeback-plan.json",
            )
            self.assertEqual(_snapshot_files(root), before)

    def test_changed_preview_rebinds_to_new_classification_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _run, evidence = self._complete_pipeline(temporary)
            payload = json.loads(evidence.read_text())
            payload["writeback_preview"]["add_tags"] = ["changed-classification"]
            evidence.write_text(json.dumps(payload))
            self.assertEqual(self._classify(root, evidence).status, "classified")
            self.assertEqual(
                _resolved(root).artifact_index.zotero_writeback["plan_ref"],
                "classification/zotero-writeback-preview.json",
            )

    def test_mismatched_materialized_plan_is_not_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, run, evidence = self._complete_pipeline(temporary)
            plan_path = run / "zotero-writeback-plan.json"
            payload = json.loads(plan_path.read_text())
            payload["add_tags"] = ["stale-materialized-plan"]
            stage_runtime.write_json_object(plan_path, payload)
            self.assertEqual(self._classify(root, evidence).status, "classified")
            self.assertEqual(
                _resolved(root).artifact_index.zotero_writeback["plan_ref"],
                "classification/zotero-writeback-preview.json",
            )

    def test_missing_materialized_plan_fails_before_metadata_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, run, evidence = self._complete_pipeline(temporary)
            before = {
                name: (run / name).read_bytes()
                for name in ("stage-manifest.json", "artifact-index.json")
            }
            (run / "zotero-writeback-plan.json").unlink()
            with self.assertRaises(MillefeuilleContractError):
                self._classify(root, evidence)
            self.assertEqual(
                {name: (run / name).read_bytes() for name in before}, before
            )

    def test_materialized_note_drift_blocks_final_metadata_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, run, evidence = self._complete_pipeline(temporary)
            before = {
                name: (run / name).read_bytes()
                for name in ("stage-manifest.json", "artifact-index.json")
            }
            original = stage_runtime.write_metadata_transaction
            note = run / "reports/zotero-writeback-note.md"
            injected = []

            def drift(directory, outputs, **kwargs):
                if "stage-manifest.json" in outputs:
                    note.write_text("uncooperative note drift\n")
                    injected.append(True)
                return original(directory, outputs, **kwargs)

            with (
                patch.object(stage_runtime, "write_metadata_transaction", drift),
                self.assertRaisesRegex(MillefeuilleContractError, "input drift"),
            ):
                self._classify(root, evidence)
            self.assertEqual(injected, [True])
            self.assertEqual(
                {name: (run / name).read_bytes() for name in before}, before
            )

    def test_note_changed_after_validation_is_rejected_before_classification_effects(
        self,
    ):
        from millefeuille.domain import classification
        from tests.test_millefeuille_stage_cli import _snapshot_files

        with tempfile.TemporaryDirectory() as temporary:
            root, run, evidence = self._complete_pipeline(temporary)
            note = run / "reports/zotero-writeback-note.md"
            self.assertTrue(note.is_file())
            expected = _snapshot_files(root)
            changed_note = "synthetic external note change after validation\n"
            expected[note.relative_to(root).as_posix()] = changed_note.encode()
            original_capture = classification.capture_input_files
            injected = []

            def capture(paths):
                actual_guard = original_capture(paths)

                def check():
                    if not injected:
                        note.write_text(changed_note)
                        injected.append(True)
                    actual_guard()

                return check

            with (
                patch.object(classification, "capture_input_files", capture),
                patch.object(
                    classification,
                    "write_json_object",
                    wraps=classification.write_json_object,
                ) as json_writer,
                self.assertRaisesRegex(MillefeuilleContractError, "input drift"),
            ):
                self._classify(root, evidence)
            self.assertEqual(injected, [True])
            json_writer.assert_not_called()
            # Only the deliberately injected note changes; no history is excluded.
            self.assertEqual(_snapshot_files(root), expected)

    def test_noncanonical_equal_preview_resets_binding_without_guard_self_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, run, evidence = self._complete_pipeline(temporary)
            preview = run / "classification/zotero-writeback-preview.json"
            payload = json.loads(preview.read_text())
            canonical = json.dumps(payload, indent=2, sort_keys=True) + "\n"
            noncanonical = json.dumps(payload, separators=(",", ":")) + "\n"
            self.assertNotEqual(noncanonical, canonical)
            stage_runtime.write_text(preview, noncanonical)
            self.assertEqual(json.loads(preview.read_text()), payload)
            self.assertEqual(self._classify(root, evidence).status, "classified")
            self.assertEqual(
                _resolved(root).artifact_index.zotero_writeback,
                {
                    "mode": "preview",
                    "status": "previewed",
                    "plan_ref": "classification/zotero-writeback-preview.json",
                },
            )
            self.assertEqual(preview.read_bytes(), canonical.encode())

    def test_incoming_review_status_resets_binding_with_unchanged_preview_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, run, evidence = self._complete_pipeline(temporary)
            preview = run / "classification/zotero-writeback-preview.json"
            plan = run / "zotero-writeback-plan.json"
            before_preview = preview.read_bytes()
            before_plan = plan.read_bytes()
            payload = json.loads(evidence.read_text())
            payload["review_reasons"] = ["synthetic classification review required"]
            evidence.write_text(json.dumps(payload))
            self.assertEqual(self._classify(root, evidence).status, "needs-review")
            resolved = _resolved(root)
            self.assertEqual(
                resolved.artifact_index.zotero_writeback,
                {
                    "mode": "preview",
                    "status": "previewed",
                    "plan_ref": "classification/zotero-writeback-preview.json",
                },
            )
            self.assertEqual(
                resolved.artifact_index.stages["classify"]["status"], "needs-review"
            )
            self.assertEqual(preview.read_bytes(), before_preview)
            self.assertEqual(plan.read_bytes(), before_plan)
