"""Whole-pack run packaging preserves verified sources and append-only writes."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.multi_source_run_package import (
    plan_multi_source_run_package,
    publish_multi_source_run_package,
    recover_partial_multi_source_run_package,
)
from millefeuille.domain.multi_source_summary import build_multi_source_summary_package
from millefeuille.domain.published_summary_run_link import (
    LINK_SCHEMA_VERSION,
    VIEW_SCHEMA_VERSION,
)
from millefeuille.domain.stage_runtime import resolve_run_artifacts
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_multi_source_summary import _pack


def _saved_run(root: Path) -> tuple[Path, Path]:
    pack = _pack(root)
    source_root = root / "source-packs"
    run = source_root / "analyses/millefeuille/run-two-source"
    run.mkdir(parents=True)
    for ref, body in (
        ("summaries/hierarchical-summary.json", "{}\n"),
        ("summaries/texts/part.md", "saved section\n"),
        ("cards/paper-card.json", "{}\n"),
        ("cards/paper-card.md", "saved card\n"),
        ("index/index-status.json", "{}\n"),
    ):
        target = run / ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return pack, run


def _saved_output_refs(pack: Path, *, source_hash: str | None = None):
    expected_hash = json.loads((pack / "manifest.json").read_bytes())["source_hash"]
    return (
        patch(
            "millefeuille.domain.multi_source_run_package.load_hierarchical_summary",
            return_value={
                "schema_version": VIEW_SCHEMA_VERSION,
                "paper_id": "zotero-ITEM1234",
                "run_id": "run-two-source",
                "source_hash": source_hash or expected_hash,
            },
        ),
        patch(
            "millefeuille.domain.multi_source_run_package._resolve_summary_refs",
            return_value={
                "summary_artifact_ref": "summaries/hierarchical-summary.json",
                "summary_text_dir_ref": "summaries/texts",
            },
        ),
        patch(
            "millefeuille.domain.multi_source_run_package._resolve_card_refs",
            return_value={
                "paper_card_json_ref": "cards/paper-card.json",
                "paper_card_markdown_ref": "cards/paper-card.md",
            },
        ),
        patch(
            "millefeuille.domain.multi_source_run_package._resolve_index_refs",
            return_value={
                "retrieval_index_status_ref": "index/index-status.json",
                "index_records": [
                    {"lane": "openkb", "status": "written"},
                    {"lane": "pageindex", "status": "written"},
                ],
            },
        ),
    )


class MultiSourceRunPackageTests(unittest.TestCase):
    @requires_secure_nofollow_writes
    def test_linked_origin_text_change_during_write_preserves_new_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            source_root = root / "source-packs"
            origin = source_root / "analyses/millefeuille/run-origin/summaries"
            origin.mkdir(parents=True)
            original_text = origin / "part.md"
            original_text.write_text("approved original text")
            preparation = source_root / "preparation.json"
            preparation.write_text("{}")
            (run / "summaries/hierarchical-summary.json").write_text(
                json.dumps(
                    {
                        "schema_version": LINK_SCHEMA_VERSION,
                        "summary_publication": {"run_id": "run-origin"},
                        "summary_record_ref": (
                            "analyses/millefeuille/run-origin/summaries/"
                            "hierarchical-summary.json"
                        ),
                        "evidence_refs": {
                            "route_evidence_ref": (pack / "selected/route.json")
                            .relative_to(source_root)
                            .as_posix(),
                            "structure_evidence_ref": (
                                pack / "structure/structure.json"
                            )
                            .relative_to(source_root)
                            .as_posix(),
                            "preparation_ref": "preparation.json",
                        },
                    }
                )
            )
            loader, summary, card, index = _saved_output_refs(pack)
            from millefeuille.domain.secure_io import write_new_text_no_follow

            with loader, summary, card, index:
                plan = plan_multi_source_run_package(
                    source_pack_root=source_root,
                    item_key="ITEM1234",
                    run_id=run.name,
                )

                def mutate_origin(path, text, label, **kwargs):
                    write_new_text_no_follow(path, text, label, **kwargs)
                    original_text.write_text("unapproved original text")

                with (
                    patch(
                        "millefeuille.domain.multi_source_run_package.write_new_text_no_follow",
                        side_effect=mutate_origin,
                    ),
                    self.assertRaisesRegex(
                        MillefeuilleContractError, "input snapshot changed"
                    ),
                ):
                    publish_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )
            self.assertEqual(
                (run / "stage-manifest.json").read_text(), plan.stage_manifest_text
            )
            self.assertFalse((run / "artifact-index.json").exists())

    @requires_secure_nofollow_writes
    def test_real_published_link_inventory_binds_original_summary_and_preparation(self):
        from millefeuille.domain.multi_source_run_package import _inventory_inputs
        from millefeuille.domain.summary_fixtures import load_hierarchical_summary
        from tests.test_millefeuille_published_summary_run_link import (
            TestPublishedSummaryRunLink,
        )

        fixture = TestPublishedSummaryRunLink(
            "test_view_keeps_original_generation_and_uses_verified_original_files"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture._publish_fixture_link()
        (fixture.run / "index").mkdir()
        view = load_hierarchical_summary(fixture.link)
        raw_link = json.loads(fixture.link.read_bytes())
        pack = fixture.root / "zotero" / fixture.plan.paper_id
        before = _inventory_inputs(fixture.root, pack, fixture.run)
        refs = {ref for ref, _, _ in before}
        self.assertIn(view["source_summary_ref"], refs)
        self.assertTrue(set(raw_link["evidence_refs"].values()).issubset(refs))
        original_text = Path(
            os.path.abspath(fixture.link.parent / view["summaries"][0]["text_ref"])
        )
        self.assertIn(original_text.relative_to(fixture.root).as_posix(), refs)
        original_text.write_bytes(original_text.read_bytes() + b"changed")
        self.assertNotEqual(before, _inventory_inputs(fixture.root, pack, fixture.run))

    @requires_secure_nofollow_writes
    def test_destination_replacement_during_final_check_preserves_pinned_outputs(self):
        for recovery in (False, True):
            with self.subTest(recovery=recovery), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                pack, run = _saved_run(root)
                loader, summary, card, index = _saved_output_refs(pack)
                import millefeuille.domain.multi_source_run_package as package

                with loader, summary, card, index:
                    plan = plan_multi_source_run_package(
                        source_pack_root=root / "source-packs",
                        item_key="ITEM1234",
                        run_id=run.name,
                    )
                    if recovery:
                        (run / "stage-manifest.json").write_text(
                            plan.stage_manifest_text
                        )
                    original_inventory = package._inventory_inputs
                    displaced = run.with_name("displaced-run")

                    def replace_destination(
                        *args,
                        run=run,
                        displaced=displaced,
                        original_inventory=original_inventory,
                    ):
                        if (run / "artifact-index.json").exists():
                            run.rename(displaced)
                            shutil.copytree(
                                displaced,
                                run,
                                ignore=shutil.ignore_patterns(
                                    "stage-manifest.json", "artifact-index.json"
                                ),
                            )
                        return original_inventory(*args)

                    publish = (
                        recover_partial_multi_source_run_package
                        if recovery
                        else publish_multi_source_run_package
                    )
                    with (
                        patch.object(package, "_inventory_inputs", replace_destination),
                        self.assertRaisesRegex(
                            MillefeuilleContractError, "destination changed"
                        ),
                    ):
                        publish(plan, expected_preview_sha256=plan.preview_sha256)
                self.assertFalse((run / "stage-manifest.json").exists())
                self.assertFalse((run / "artifact-index.json").exists())
                self.assertEqual(
                    (displaced / "artifact-index.json").read_text(),
                    plan.artifact_index_text,
                )
                self.assertEqual(
                    (displaced / "stage-manifest.json").read_text(),
                    plan.stage_manifest_text,
                )

    @requires_secure_nofollow_writes
    def test_changed_first_output_during_second_write_blocks_success(self):
        for recovery, mutation in (
            (False, "change"),
            (True, "change"),
            (False, "remove"),
            (True, "remove"),
        ):
            with (
                self.subTest(recovery=recovery, mutation=mutation),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                pack, run = _saved_run(root)
                loader, summary, card, index = _saved_output_refs(pack)
                from millefeuille.domain.secure_io import write_new_text_no_follow

                with loader, summary, card, index:
                    plan = plan_multi_source_run_package(
                        source_pack_root=root / "source-packs",
                        item_key="ITEM1234",
                        run_id=run.name,
                    )
                    first = run / "stage-manifest.json"
                    if recovery:
                        first.write_text(plan.stage_manifest_text)

                    def tamper_first(
                        path, text, label, *, first=first, mutation=mutation, **kwargs
                    ):
                        write_new_text_no_follow(path, text, label, **kwargs)
                        if Path(path).name == "artifact-index.json":
                            if mutation == "remove":
                                first.unlink()
                            else:
                                first.write_text("unapproved output bytes")

                    publish = (
                        recover_partial_multi_source_run_package
                        if recovery
                        else publish_multi_source_run_package
                    )
                    with (
                        patch(
                            "millefeuille.domain.multi_source_run_package.write_new_text_no_follow",
                            side_effect=tamper_first,
                        ),
                        self.assertRaises(MillefeuilleContractError),
                    ):
                        publish(plan, expected_preview_sha256=plan.preview_sha256)
                if mutation == "remove":
                    self.assertFalse(first.exists())
                else:
                    self.assertEqual(first.read_text(), "unapproved output bytes")
                self.assertEqual(
                    (run / "artifact-index.json").read_text(), plan.artifact_index_text
                )

    def _assert_substituted_entry_preserved(self, *, fifo, input_drift=True):
        for recovery, failed_name in (
            (False, "stage-manifest.json"),
            (False, "artifact-index.json"),
            (True, "artifact-index.json"),
        ):
            with (
                self.subTest(recovery=recovery, failed_name=failed_name),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                pack, run = _saved_run(root)
                loader, summary, card, index = _saved_output_refs(pack)
                from millefeuille.domain.secure_io import write_new_text_no_follow

                with loader, summary, card, index:
                    plan = plan_multi_source_run_package(
                        source_pack_root=root / "source-packs",
                        item_key="ITEM1234",
                        run_id=run.name,
                    )
                    if recovery:
                        (run / "stage-manifest.json").write_text(
                            plan.stage_manifest_text
                        )

                    def substitute_after_write(
                        path, text, label, *, failed_name=failed_name, run=run, **kwargs
                    ):
                        write_new_text_no_follow(path, text, label, **kwargs)
                        if Path(path).name == failed_name:
                            # Retain the original inode so substitution cannot reuse it.
                            path.rename(run / "displaced-output")
                            if fifo:
                                os.mkfifo(path)
                            else:
                                path.write_text(text)
                            if input_drift:
                                (run / "summaries/texts/part.md").write_text(
                                    "changed input"
                                )

                    publish = (
                        recover_partial_multi_source_run_package
                        if recovery
                        else publish_multi_source_run_package
                    )

                    def fifo_deadline(*_):
                        self.fail(
                            "publication did not reject the substituted FIFO promptly"
                        )

                    if fifo:
                        previous_handler = signal.signal(signal.SIGALRM, fifo_deadline)
                        signal.setitimer(signal.ITIMER_REAL, 2)
                    try:
                        with (
                            patch(
                                "millefeuille.domain.multi_source_run_package.write_new_text_no_follow",
                                side_effect=substitute_after_write,
                            ),
                            self.assertRaisesRegex(
                                MillefeuilleContractError,
                                "input snapshot changed"
                                if input_drift
                                else "not a regular file",
                            ),
                        ):
                            publish(plan, expected_preview_sha256=plan.preview_sha256)
                    finally:
                        if fifo:
                            signal.setitimer(signal.ITIMER_REAL, 0)
                            signal.signal(signal.SIGALRM, previous_handler)
                substituted = run / failed_name
                self.assertNotEqual(
                    substituted.lstat().st_ino, (run / "displaced-output").stat().st_ino
                )
                if fifo:
                    self.assertTrue(stat.S_ISFIFO(substituted.lstat().st_mode))
                else:
                    self.assertEqual(
                        substituted.read_text(), (run / "displaced-output").read_text()
                    )
                if failed_name == "artifact-index.json":
                    self.assertEqual(
                        (run / "stage-manifest.json").read_text(),
                        plan.stage_manifest_text,
                    )
                else:
                    self.assertFalse((run / "artifact-index.json").exists())

    @requires_secure_nofollow_writes
    def test_input_drift_preserves_substituted_regular_output(self):
        self._assert_substituted_entry_preserved(fifo=False)

    @requires_secure_nofollow_writes
    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFOs")
    def test_input_drift_preserves_fifo_without_blocking_open(self):
        self._assert_substituted_entry_preserved(fifo=True)

    @requires_secure_nofollow_writes
    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFOs")
    def test_output_verification_preserves_fifo_without_blocking_open(self):
        self._assert_substituted_entry_preserved(fifo=True, input_drift=False)

    @requires_secure_nofollow_writes
    def test_write_failure_preserves_created_output_for_operator_resolution(self):
        for recovery, failed_name in (
            (False, "stage-manifest.json"),
            (False, "artifact-index.json"),
            (True, "artifact-index.json"),
        ):
            with (
                self.subTest(recovery=recovery, failed_name=failed_name),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                pack, run = _saved_run(root)
                loader, summary, card, index = _saved_output_refs(pack)
                original_fsync = os.fsync
                with loader, summary, card, index:
                    plan = plan_multi_source_run_package(
                        source_pack_root=root / "source-packs",
                        item_key="ITEM1234",
                        run_id=run.name,
                    )
                    if recovery:
                        (run / "stage-manifest.json").write_text(
                            plan.stage_manifest_text
                        )

                    def fail_output_fsync(
                        fd,
                        *,
                        run=run,
                        failed_name=failed_name,
                        original_fsync=original_fsync,
                    ):
                        target = run / failed_name
                        opened = os.fstat(fd)
                        if target.exists() and opened.st_ino == target.stat().st_ino:
                            raise OSError("synthetic output fsync failure")
                        return original_fsync(fd)

                    publish = (
                        recover_partial_multi_source_run_package
                        if recovery
                        else publish_multi_source_run_package
                    )
                    with (
                        patch("os.fsync", side_effect=fail_output_fsync),
                        self.assertRaisesRegex(
                            MillefeuilleContractError, "synthetic output fsync failure"
                        ),
                    ):
                        publish(plan, expected_preview_sha256=plan.preview_sha256)
                    expected = (
                        plan.stage_manifest_text
                        if failed_name == "stage-manifest.json"
                        else plan.artifact_index_text
                    )
                    self.assertEqual((run / failed_name).read_text(), expected)
                    # The retained entry is not a successful publication and
                    # cannot be silently replaced by another ordinary attempt.
                    with self.assertRaisesRegex(
                        MillefeuilleContractError, "target already exists"
                    ):
                        publish(plan, expected_preview_sha256=plan.preview_sha256)

    @requires_secure_nofollow_writes
    def test_plan_is_read_only_and_publication_is_exact_and_append_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            summary_loader_patch, summary_patch, card_patch, index_patch = (
                _saved_output_refs(pack)
            )
            with summary_loader_patch, summary_patch, card_patch, index_patch:
                plan = plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id="run-two-source",
                )
                self.assertFalse((run / "stage-manifest.json").exists())
                self.assertFalse((run / "artifact-index.json").exists())
                self.assertEqual(plan.preview()["provider_calls"], 0)
                self.assertEqual(len(plan.preview()["files"]), 2)
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "fingerprint drift"
                ):
                    publish_multi_source_run_package(
                        plan, expected_preview_sha256="0" * 64
                    )
                paths = publish_multi_source_run_package(
                    plan, expected_preview_sha256=plan.preview_sha256
                )
                self.assertEqual(
                    [path.name for path in paths],
                    ["stage-manifest.json", "artifact-index.json"],
                )
                resolved = resolve_run_artifacts(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id="run-two-source",
                    artifact_root=run,
                )
                self.assertEqual(
                    resolved.artifact_index.source_identity["pdf_count"], 2
                )
                self.assertEqual(len(resolved.stage_manifest.stages), 16)
                self.assertEqual(
                    json.loads(paths[1].read_bytes())["source_pack"]["source_hash"],
                    resolved.source_hash,
                )
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "target already exists"
                ):
                    publish_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )

    @requires_secure_nofollow_writes
    def test_saved_summary_with_matching_ids_but_stale_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            loader, summary, card, index = _saved_output_refs(
                pack, source_hash="sha256-aggregate:" + "0" * 64
            )
            with (
                loader,
                summary,
                card,
                index,
                self.assertRaisesRegex(
                    MillefeuilleContractError, "summary source lineage drift"
                ),
            ):
                plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id=run.name,
                )
            self.assertFalse((run / "stage-manifest.json").exists())

    @requires_secure_nofollow_writes
    def test_upstream_builder_rejects_ocr_or_mixed_evidence(self):
        for selected_route in ("ocr", "merged-dual"):
            with (
                self.subTest(selected_route=selected_route),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                pack, run = _saved_run(root)
                for ref in ("selected/route.json", "structure/structure.json"):
                    path = pack / ref
                    evidence = json.loads(path.read_bytes())
                    evidence["selected_route"] = selected_route
                    path.write_text(json.dumps(evidence))
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "route or structure ref drift"
                ):
                    plan_multi_source_run_package(
                        source_pack_root=root / "source-packs",
                        item_key="ITEM1234",
                        run_id=run.name,
                    )
                self.assertFalse((run / "stage-manifest.json").exists())

    @requires_secure_nofollow_writes
    def test_planner_rejects_non_native_upstream_before_generating_stage_metadata(self):
        for selected_route, structure_route in (
            ("ocr", "ocr"),
            ("merged-dual", "merged-dual"),
            ("native", "ocr"),
            ("ocr", "native"),
        ):
            with (
                self.subTest(
                    selected_route=selected_route, structure_route=structure_route
                ),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                pack, run = _saved_run(root)
                # Validate the complete fixture with the real builder first. That
                # builder currently only supports native; inject a future non-native
                # result at its boundary to exercise the planner's own guard.
                verified_upstream = build_multi_source_summary_package(
                    route_evidence_path=pack / "selected/route.json",
                    structure_evidence_path=pack / "structure/structure.json",
                    output_dir=run,
                    validation_only=True,
                )
                loader, summary, card, index = _saved_output_refs(pack)
                with (
                    loader,
                    summary,
                    card,
                    index,
                    patch(
                        "millefeuille.domain.multi_source_run_package.build_multi_source_summary_package",
                        return_value={
                            **verified_upstream,
                            "selected_route": selected_route,
                            "structure_route": structure_route,
                        },
                    ),
                    self.assertRaisesRegex(
                        MillefeuilleContractError,
                        "run package requires verified native-only extraction",
                    ),
                ):
                    plan_multi_source_run_package(
                        source_pack_root=root / "source-packs",
                        item_key="ITEM1234",
                        run_id=run.name,
                    )
                self.assertFalse((run / "stage-manifest.json").exists())
                self.assertFalse((run / "artifact-index.json").exists())

    @requires_secure_nofollow_writes
    def test_relative_root_cannot_retarget_publication_after_chdir(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "first").mkdir()
            (root / "second").mkdir()
            first_pack, first_run = _saved_run(root / "first")
            _, second_run = _saved_run(root / "second")
            loader, summary, card, index = _saved_output_refs(first_pack)
            initial_cwd = Path.cwd()
            try:
                os.chdir(root / "first")
                with loader, summary, card, index:
                    plan = plan_multi_source_run_package(
                        source_pack_root="source-packs",
                        item_key="ITEM1234",
                        run_id=first_run.name,
                    )
                    self.assertEqual(
                        plan.source_pack_root, (root / "first/source-packs").absolute()
                    )
                    os.chdir(root / "second")
                    paths = publish_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )
            finally:
                os.chdir(initial_cwd)
            self.assertEqual(paths[0], first_run / "stage-manifest.json")
            self.assertTrue(paths[0].is_file())
            self.assertFalse((second_run / "stage-manifest.json").exists())

    @requires_secure_nofollow_writes
    def test_replaced_run_directory_is_rejected_at_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            loader, summary, card, index = _saved_output_refs(pack)
            from millefeuille.domain.secure_io import write_new_text_no_follow

            with loader, summary, card, index:
                plan = plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id=run.name,
                )

                def replace_parent(path, text, label, **kwargs):
                    displaced = run.with_name("displaced-run")
                    run.rename(displaced)
                    run.mkdir()
                    write_new_text_no_follow(path, text, label, **kwargs)

                with (
                    patch(
                        "millefeuille.domain.multi_source_run_package.write_new_text_no_follow",
                        side_effect=replace_parent,
                    ),
                    self.assertRaisesRegex(
                        MillefeuilleContractError,
                        "parent directory changed after approval",
                    ),
                ):
                    publish_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )
            self.assertFalse((run / "stage-manifest.json").exists())
            self.assertFalse(
                (run.with_name("displaced-run") / "stage-manifest.json").exists()
            )

    @requires_secure_nofollow_writes
    def test_input_change_during_write_preserves_new_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            loader, summary, card, index = _saved_output_refs(pack)
            from millefeuille.domain.secure_io import write_new_text_no_follow

            with loader, summary, card, index:
                plan = plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id=run.name,
                )

                def mutate_after_first(path, text, label, **kwargs):
                    write_new_text_no_follow(path, text, label, **kwargs)
                    if Path(path).name == "stage-manifest.json":
                        (run / "summaries/texts/part.md").write_text("changed")

                with (
                    patch(
                        "millefeuille.domain.multi_source_run_package.write_new_text_no_follow",
                        side_effect=mutate_after_first,
                    ),
                    self.assertRaisesRegex(
                        MillefeuilleContractError,
                        "input snapshot changed during publication",
                    ),
                ):
                    publish_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )
            self.assertEqual(
                (run / "stage-manifest.json").read_text(), plan.stage_manifest_text
            )
            self.assertFalse((run / "artifact-index.json").exists())

    @requires_secure_nofollow_writes
    def test_excessive_empty_directory_depth_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            cursor = run / "summaries"
            for number in range(18):
                cursor = cursor / f"empty-{number}"
                cursor.mkdir()
            loader, summary, card, index = _saved_output_refs(pack)
            with (
                loader,
                summary,
                card,
                index,
                self.assertRaisesRegex(
                    MillefeuilleContractError, "node or depth limit"
                ),
            ):
                plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id=run.name,
                )

    @requires_secure_nofollow_writes
    def test_changed_supplement_pdf_blocks_planning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            with self.assertRaisesRegex(MillefeuilleContractError, "traversal-safe"):
                plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id="..",
                )
            manifest = json.loads((pack / "manifest.json").read_bytes())
            supplement = next(
                source
                for source in manifest["sources"]
                if source["identity"]["zotero_attachment_key"] == "SUPP5678"
            )
            (pack / supplement["ref"]).write_bytes(b"changed supplement")
            with self.assertRaises(MillefeuilleContractError):
                plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id=run.name,
                )

    @requires_secure_nofollow_writes
    def test_forged_plan_cannot_change_approved_bytes_or_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            summary_loader_patch, summary_patch, card_patch, index_patch = (
                _saved_output_refs(pack)
            )
            with summary_loader_patch, summary_patch, card_patch, index_patch:
                plan = plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id=run.name,
                )
                changed_text = replace(
                    plan, stage_manifest_text=plan.stage_manifest_text + " "
                )
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "fingerprint drift"
                ):
                    publish_multi_source_run_package(
                        changed_text, expected_preview_sha256=plan.preview_sha256
                    )
                changed_destination = replace(plan, run_dir=run / "other")
                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "approved run-package directory not found",
                ):
                    publish_multi_source_run_package(
                        changed_destination,
                        expected_preview_sha256=plan.preview_sha256,
                    )
            self.assertFalse((run / "stage-manifest.json").exists())

    @requires_secure_nofollow_writes
    def test_saved_summary_change_after_approval_blocks_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            summary_loader_patch, summary_patch, card_patch, index_patch = (
                _saved_output_refs(pack)
            )
            with summary_loader_patch, summary_patch, card_patch, index_patch:
                plan = plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id=run.name,
                )
                (run / "summaries/texts/part.md").write_text("changed section\n")
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "evidence changed after planning"
                ):
                    publish_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )
            self.assertFalse((run / "stage-manifest.json").exists())

    @requires_secure_nofollow_writes
    def test_partial_publication_requires_verified_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, run = _saved_run(root)
            summary_loader_patch, summary_patch, card_patch, index_patch = (
                _saved_output_refs(pack)
            )
            with summary_loader_patch, summary_patch, card_patch, index_patch:
                plan = plan_multi_source_run_package(
                    source_pack_root=root / "source-packs",
                    item_key="ITEM1234",
                    run_id=run.name,
                )
                from millefeuille.domain.secure_io import write_new_text_no_follow

                def fail_second(path, text, label, **kwargs):
                    if Path(path).name == "artifact-index.json":
                        raise OSError("synthetic second-write failure")
                    write_new_text_no_follow(path, text, label, **kwargs)

                with (
                    patch(
                        "millefeuille.domain.multi_source_run_package.write_new_text_no_follow",
                        side_effect=fail_second,
                    ),
                    self.assertRaisesRegex(OSError, "synthetic second-write failure"),
                ):
                    publish_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )
                first = run / "stage-manifest.json"
                second = run / "artifact-index.json"
                self.assertTrue(first.is_file())
                self.assertFalse(second.exists())
                first.write_text("tampered")
                with self.assertRaises(MillefeuilleContractError):
                    recover_partial_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )
                first.write_text(plan.stage_manifest_text)
                paths = recover_partial_multi_source_run_package(
                    plan, expected_preview_sha256=plan.preview_sha256
                )
                self.assertEqual(paths, (first, second))
                self.assertEqual(second.read_text(), plan.artifact_index_text)
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "target already exists"
                ):
                    recover_partial_multi_source_run_package(
                        plan, expected_preview_sha256=plan.preview_sha256
                    )


if __name__ == "__main__":
    unittest.main()
