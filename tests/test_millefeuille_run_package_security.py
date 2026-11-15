"""Synthetic qualification of existing package no-follow and recovery guards."""

from contextlib import ExitStack
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import multi_source_run_package as package
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import write_new_text_no_follow
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_multi_source_run_package import (
    _saved_output_refs,
    _saved_run,
)


@requires_secure_nofollow_writes
class RunPackageSecurityTests(unittest.TestCase):
    def test_inventory_rejects_unsafe_extra_entries(self):
        for kind in ("file-symlink", "directory-symlink", "dangling", "fifo", "socket"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                pack, run = _saved_run(root)
                entry = run / "cards/unexpected"
                with ExitStack() as stack:
                    if kind == "fifo":
                        os.mkfifo(entry)
                    elif kind == "socket":
                        sock = stack.enter_context(socket.socket(socket.AF_UNIX))
                        sock.bind(str(entry))
                    else:
                        target = {
                            "file-symlink": run / "cards/paper-card.md",
                            "directory-symlink": run / "summaries",
                            "dangling": root / "absent",
                        }[kind]
                        entry.symlink_to(target)
                    for guard in _saved_output_refs(pack):
                        stack.enter_context(guard)
                    with self.assertRaises(MillefeuilleContractError):
                        package.plan_multi_source_run_package(
                            source_pack_root=root / "source-packs",
                            item_key="ITEM1234",
                            run_id=run.name,
                        )
                self.assertFalse((run / "stage-manifest.json").exists())
                self.assertFalse((run / "artifact-index.json").exists())

    def test_ancestor_symlink_cannot_redirect_planner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            alias = root / "alias"
            alias.symlink_to(root / "source-packs", target_is_directory=True)
            with ExitStack() as stack:
                for guard in _saved_output_refs(pack):
                    stack.enter_context(guard)
                with self.assertRaises(MillefeuilleContractError):
                    package.plan_multi_source_run_package(
                        source_pack_root=alias, item_key="ITEM1234", run_id=run.name
                    )
            self.assertFalse((run / "stage-manifest.json").exists())

    def test_recovery_rejects_nonregular_first_output_without_reading_it(self):
        for kind in ("fifo", "symlink", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                pack, run = _saved_run(root)
                with ExitStack() as stack:
                    for guard in _saved_output_refs(pack):
                        stack.enter_context(guard)
                    plan = package.plan_multi_source_run_package(
                        source_pack_root=root / "source-packs",
                        item_key="ITEM1234",
                        run_id=run.name,
                    )
                    first = run / "stage-manifest.json"
                    if kind == "fifo":
                        os.mkfifo(first)
                    elif kind == "directory":
                        first.mkdir()
                    else:
                        target = root / "approved-first"
                        target.write_text(plan.stage_manifest_text)
                        first.symlink_to(target)
                    with self.assertRaises(MillefeuilleContractError):
                        package.recover_partial_multi_source_run_package(
                            plan, expected_preview_sha256=plan.preview_sha256
                        )
                self.assertTrue(first.exists())
                self.assertFalse((run / "artifact-index.json").exists())

    def test_recovery_detects_first_output_substitution_after_second_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            with ExitStack() as stack:
                for guard in _saved_output_refs(pack):
                    stack.enter_context(guard)
                plan = package.plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id=run.name,
                )
                first = run / "stage-manifest.json"
                first.write_text(plan.stage_manifest_text)

                def substitute(path, text, label, **kwargs):
                    write_new_text_no_follow(path, text, label, **kwargs)
                    first.unlink()
                    first.symlink_to(root / "missing")

                stack.enter_context(
                    patch.object(
                        package, "write_new_text_no_follow", side_effect=substitute
                    )
                )
                with self.assertRaises(MillefeuilleContractError):
                    package.recover_partial_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )
            self.assertTrue(first.is_symlink())
            self.assertEqual(
                (run / "artifact-index.json").read_text(), plan.artifact_index_text
            )

    def test_new_input_after_approval_blocks_publication_and_recovery(self):
        for recovery in (False, True):
            with self.subTest(recovery=recovery), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                pack, run = _saved_run(root)
                with ExitStack() as stack:
                    for guard in _saved_output_refs(pack):
                        stack.enter_context(guard)
                    plan = package.plan_multi_source_run_package(
                        source_pack_root=root / "source-packs",
                        item_key="ITEM1234",
                        run_id=run.name,
                    )
                    if recovery:
                        (run / "stage-manifest.json").write_text(
                            plan.stage_manifest_text
                        )
                    (run / "cards/new-input.md").write_text("new synthetic input")
                    operation = (
                        package.recover_partial_multi_source_run_package
                        if recovery
                        else package.publish_multi_source_run_package
                    )
                    with self.assertRaisesRegex(
                        MillefeuilleContractError, "evidence changed"
                    ):
                        operation(plan, expected_preview_sha256=plan.preview_sha256)
                self.assertEqual((run / "stage-manifest.json").exists(), recovery)
                self.assertFalse((run / "artifact-index.json").exists())
