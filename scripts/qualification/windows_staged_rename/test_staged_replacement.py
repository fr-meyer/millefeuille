"""Offline object/namespace, failure, recovery and request-packing tests."""

import hashlib
import struct
import threading
import unittest

from native_contract import (
    NativeMoveRequest,
    NativeObjectBinding,
    UnqualifiedNativeAdapter,
)
from staged_replacement import (
    MAX_EVIDENCE_BYTES,
    MAX_FILE_BYTES,
    MAX_HISTORY_JOURNALS,
    PENDING,
    PRODUCTION_WINDOWS_WRITES_ENABLED,
    Refusal,
    StagedReplacement,
    SyntheticStore,
    TransactionError,
    production_entrypoint,
)

FORWARD_FAULT_CASES = []
ROLLBACK_FAULT_CASES = []


def fixture():
    store = SyntheticStore()
    old = {
        "artifact-index.json": b'{"index":"old"}\n',
        "stage-manifest.json": b'{"stage":"old"}\n',
    }
    for path, data in old.items():
        parent, leaf, _ = store.resolve(path)
        store.create_file(parent, leaf, data)
    store.create_file(store.run, "input.json", b'{"input":"stable"}\n')
    outputs = {
        "artifact-index.json": b'{"index":"new"}\n',
        "stage-manifest.json": b'{"stage":"new"}\n',
        "reports/acceptance.json": b'{"acceptance":"pass"}\n',
    }
    before = {}
    for path in outputs:
        parent, leaf, _ = store.resolve(path)
        before[path] = store.named_binding(parent, leaf)
    inputs = {"input.json": store.named_binding(store.run, "input.json")}
    return store, outputs, before, inputs, old


def execute(store, outputs, before, inputs, fault=None):
    tx = StagedReplacement(store, fault=fault)
    return tx, tx.execute(
        outputs,
        expected_before=before,
        expected_inputs=inputs,
        expected_run_identity=store.run,
    )


def live(store, path):
    parent, leaf, _ = store.resolve(path)
    identity = store.named(parent, leaf)
    return None if identity is None else store.read_handle(identity)


class ProtocolTests(unittest.TestCase):
    def test_mixed_case_reserved_history_output_is_refused_before_effects(self):
        store = SyntheticStore()
        for path in (
            ".MILLEFEUILLE-METADATA-HISTORY/injected.json",
            ".Millefeuille-Metadata-Pending/injected.json",
        ):
            with self.subTest(path=path), self.assertRaises(TransactionError):
                execute(store, {path: b"{}"}, {path: None}, {})
            self.assertEqual(store.directory(store.history).children, {})
            self.assertIsNone(store.named(store.run, PENDING))
            self.assertEqual(store.writes, [])

    def test_history_capacity_refuses_changed_write_before_effects_but_allows_noop(
        self,
    ):
        store, outputs, before, inputs, old = fixture()
        for index in range(MAX_HISTORY_JOURNALS):
            identity = store.mkdir(
                store.history, f"terminal-{index}", owner="owned-fixture"
            )
            store.nodes[identity].transaction_state = "committed"
        serial = store._serial
        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs)
        self.assertEqual(failure.exception.receipt.status, "preflight-refused")
        self.assertIn("capacity exhausted", str(failure.exception))
        self.assertEqual(store._serial, serial)
        self.assertIsNone(store.named(store.run, PENDING))
        no_op_before = {path: before[path] for path in old}
        _, receipt = execute(store, old, no_op_before, inputs)
        self.assertEqual(receipt.status, "noop")
        self.assertEqual(store._serial, serial)
        self.assertEqual(
            store.read_group(list(old), expected_run_identity=store.run), old
        )

    def test_success_replaces_identity_preserves_old_handles_objects_and_copies(self):
        store, outputs, before, inputs, old = fixture()
        tx, receipt = execute(store, outputs, before, inputs)
        self.assertEqual(receipt.status, "committed")
        self.assertFalse(receipt.pending)
        self.assertFalse(receipt.native_windows_qualified)
        self.assertEqual(
            store.read_group(list(outputs), expected_run_identity=store.run), outputs
        )
        for e in tx.entries:
            self.assertEqual(store.named_binding(e.parent, e.leaf), e.after)
            if e.before:
                self.assertNotEqual(e.after.identity, e.before.identity)
                self.assertNotEqual(e.backup.identity, e.before.identity)
                self.assertEqual(store.read_handle(e.before.identity), old[e.path])
                self.assertEqual(store.read_handle(e.backup.identity), old[e.path])
                self.assertNotIn(e.before.identity, store.writes)
        self.assertLessEqual(receipt.retained_bytes, MAX_EVIDENCE_BYTES)

    def test_byte_identical_repeat_is_noop_without_new_ids_or_evidence(self):
        store, outputs, before, inputs, _ = fixture()
        execute(store, outputs, before, inputs)
        after = {
            path: store.named_binding(*store.resolve(path)[:2]) for path in outputs
        }
        serial, writes, size = store._serial, list(store.writes), store.retained_bytes()
        _, result = execute(store, outputs, after, inputs)
        self.assertEqual(result.status, "noop")
        self.assertEqual(store._serial, serial)
        self.assertEqual(store.writes, writes)
        self.assertEqual(store.retained_bytes(), size)

    def test_noop_rechecks_changed_identity_bytes_and_links_at_return(self):
        for at in ("before:noop.verify", "after:noop.verify"):
            for kind in ("replace", "mutate", "alias"):
                with self.subTest(at=at, kind=kind):
                    store, _, _, inputs, old = fixture()
                    before = {
                        p: store.named_binding(*store.resolve(p)[:2]) for p in old
                    }

                    def hook(
                        point,
                        _tx,
                        at=at,
                        kind=kind,
                        before=before,
                        store=store,
                        old=old,
                    ):
                        if point != at:
                            return
                        identity = before["stage-manifest.json"].identity
                        if kind == "replace":
                            store.raw_replace(
                                store.run,
                                "stage-manifest.json",
                                old["stage-manifest.json"],
                            )
                        elif kind == "mutate":
                            store.nodes[identity].data = b"foreign changed bytes"
                        else:
                            store.raw_link(identity, store.outside, "alias")

                    with self.assertRaises(TransactionError) as failure:
                        execute(store, old, before, inputs, hook)
                    self.assertEqual(
                        failure.exception.receipt.status, "preflight-refused"
                    )
                    self.assertFalse(store.writes)

    def test_existing_hardlink_refuses_before_any_transaction_effect(self):
        store, outputs, before, inputs, old = fixture()
        identity = before["stage-manifest.json"].identity
        store.raw_link(identity, store.outside, "alias")
        size, serial = store.retained_bytes(), store._serial
        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs)
        self.assertEqual(failure.exception.receipt.status, "preflight-refused")
        self.assertEqual(store.retained_bytes(), size)
        self.assertEqual(store._serial, serial)
        self.assertEqual(store.read_handle(identity), old["stage-manifest.json"])

    def test_old_outside_alias_never_receives_inplace_byte_changes(self):
        store, outputs, before, inputs, old = fixture()
        old_id = before["artifact-index.json"].identity
        observed = []

        def hook(point, _tx):
            if point == "after:old.move.0":
                store.raw_link(old_id, store.outside, "transient-alias")
                observed.append(store.read_handle(old_id))
                store.raw_unlink(store.outside, "transient-alias")

        _, result = execute(store, outputs, before, inputs, hook)
        self.assertEqual(result.status, "committed")
        self.assertEqual(observed, [old["artifact-index.json"]])
        self.assertNotIn(old_id, store.writes)
        # This success does NOT establish that hostile alias creation was excluded.

    def test_alias_to_stage_before_write_refuses_without_modifying_alias(self):
        store, outputs, before, inputs, old = fixture()
        aliases = []

        def hook(point, tx):
            if point == "after:stage.create.0":
                identity = tx.entries[0].after.identity
                store.raw_link(identity, store.outside, "staged-alias")
                aliases.append(identity)

        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs, hook)
        self.assertEqual(failure.exception.receipt.status, "recovery-required")
        self.assertTrue(failure.exception.receipt.pending)
        self.assertEqual(store.read_handle(aliases[0]), b"")
        self.assertNotIn(aliases[0], store.writes)
        for path, data in old.items():
            self.assertEqual(live(store, path), data)

    def test_alias_added_after_staging_is_observed_and_barrier_remains(self):
        store, outputs, before, inputs, _ = fixture()

        def hook(point, tx):
            if point == "after:stage.write.0":
                store.raw_link(
                    tx.entries[0].after.identity, store.outside, "stage-alias"
                )

        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs, hook)
        self.assertEqual(failure.exception.receipt.status, "recovery-required")
        self.assertTrue(failure.exception.receipt.pending)
        with self.assertRaisesRegex(Refusal, "pending"):
            store.read_group(list(outputs), expected_run_identity=store.run)

    def test_live_collision_after_old_move_is_not_overwritten(self):
        store, outputs, before, inputs, old = fixture()
        foreign = []

        def hook(point, tx):
            if point == "after:old.move.0":
                foreign.append(
                    store.raw_replace(
                        tx.entries[0].parent, tx.entries[0].leaf, b"foreign"
                    )
                )

        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs, hook)
        self.assertEqual(failure.exception.receipt.status, "recovery-required")
        self.assertTrue(failure.exception.receipt.pending)
        self.assertEqual(live(store, "artifact-index.json"), b"foreign")
        self.assertEqual(
            store.read_handle(before["artifact-index.json"].identity),
            old["artifact-index.json"],
        )
        self.assertNotIn(foreign[0], store.writes)

    def test_foreign_installed_replacement_is_never_withdrawn_or_overwritten(self):
        store, outputs, before, inputs, _ = fixture()
        foreign = []

        def hook(point, tx):
            if point == "after:new.install.0":
                foreign.append(
                    store.raw_replace(
                        tx.entries[0].parent, tx.entries[0].leaf, b"foreign-live"
                    )
                )

        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs, hook)
        self.assertEqual(failure.exception.receipt.status, "recovery-required")
        self.assertEqual(store.named(store.run, "artifact-index.json"), foreign[0])
        self.assertEqual(store.read_handle(foreign[0]), b"foreign-live")
        self.assertTrue(failure.exception.receipt.pending)

    def test_plan_binding_drift_before_or_after_final_verification_blocks_success(self):
        for at in ("before:final.verify", "after:commit.verify"):
            with self.subTest(at=at):
                store, outputs, before, inputs, _ = fixture()
                foreign = []

                def hook(point, tx, at=at, foreign=foreign, store=store):
                    if point == at:
                        foreign.append(
                            store.raw_replace(tx.journal, "plan.json", b"foreign-plan")
                        )

                with self.assertRaises(TransactionError) as failure:
                    execute(store, outputs, before, inputs, hook)
                self.assertEqual(failure.exception.receipt.status, "recovery-required")
                self.assertTrue(failure.exception.receipt.pending)
                self.assertEqual(store.read_handle(foreign[0]), b"foreign-plan")

    def test_rollback_backup_drift_preserves_old_object_and_requires_recovery(self):
        store, outputs, before, inputs, old = fixture()
        foreign = []

        def hook(point, tx):
            if point == "after:new.install.0":
                foreign.append(
                    store.raw_replace(tx.journal, "before-0", b"foreign-backup")
                )
                raise Refusal("fail after install with substituted backup")

        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs, hook)
        self.assertEqual(failure.exception.receipt.status, "recovery-required")
        self.assertTrue(failure.exception.receipt.pending)
        self.assertEqual(
            store.read_handle(before["artifact-index.json"].identity),
            old["artifact-index.json"],
        )
        self.assertEqual(store.read_handle(foreign[0]), b"foreign-backup")

    def test_root_identity_drift_does_not_write_into_replacement_run(self):
        store, outputs, before, inputs, _ = fixture()
        foreign_run = []

        def hook(point, _tx):
            if point == "after:new.install.0":
                store.move(
                    store.volume, "run", store.outside, "original-run", store.run
                )
                foreign_run.append(store.mkdir(store.volume, "run", owner="foreign"))

        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs, hook)
        self.assertEqual(failure.exception.receipt.status, "recovery-required")
        self.assertEqual(store.nodes[foreign_run[0]].children, {})
        self.assertTrue(failure.exception.receipt.pending)  # On admitted original root.
        with self.assertRaisesRegex(Refusal, "run identity drift"):
            store.read_group(list(outputs), expected_run_identity=store.run)

    def test_parent_identity_drift_keeps_foreign_directory_untouched(self):
        store, outputs, before, inputs, _ = fixture()
        foreign_parent = []

        def hook(point, _tx):
            if point == "after:new.install.0":
                original = store.named(store.run, "reports")
                store.move(
                    store.run, "reports", store.outside, "original-reports", original
                )
                foreign_parent.append(
                    store.mkdir(store.run, "reports", owner="foreign")
                )

        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs, hook)
        self.assertEqual(failure.exception.receipt.status, "recovery-required")
        self.assertEqual(store.nodes[foreign_parent[0]].children, {})
        self.assertTrue(failure.exception.receipt.pending)

    def test_same_byte_input_identity_drift_remains_a_recovery_blocker(self):
        store, outputs, before, inputs, _ = fixture()
        foreign = []

        def hook(point, _tx):
            if point == "after:new.install.0":
                foreign.append(
                    store.raw_replace(
                        store.run,
                        "input.json",
                        store.read_handle(inputs["input.json"].identity),
                    )
                )

        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs, hook)
        self.assertEqual(failure.exception.receipt.status, "recovery-required")
        self.assertEqual(store.named(store.run, "input.json"), foreign[0])
        self.assertNotIn(foreign[0], store.writes)
        self.assertTrue(failure.exception.receipt.pending)

    def test_pending_or_foreign_history_prevents_restart_without_auto_recovery(self):
        for where in ("pending", "history"):
            with self.subTest(where=where):
                store, outputs, before, inputs, _ = fixture()
                store.mkdir(
                    store.run if where == "pending" else store.history,
                    PENDING if where == "pending" else "foreign-journal",
                    owner="foreign",
                )
                serial = store._serial
                with self.assertRaises(TransactionError) as failure:
                    execute(store, outputs, before, inputs)
                self.assertEqual(failure.exception.receipt.status, "preflight-refused")
                self.assertEqual(store._serial, serial)
                with self.assertRaises(Refusal):
                    store.read_group(list(outputs), expected_run_identity=store.run)

    def test_pending_collision_after_archive_preserves_foreign_barrier(self):
        store, outputs, before, inputs, _ = fixture()
        foreign = []

        def hook(point, _tx):
            if point == "after:barrier.archive":
                foreign.append(store.mkdir(store.run, PENDING.upper(), owner="foreign"))
                raise Refusal("late commit error and foreign barrier")

        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs, hook)
        self.assertEqual(failure.exception.receipt.status, "recovery-required")
        self.assertEqual(store.named(store.run, PENDING), foreign[0])
        self.assertEqual(store.nodes[foreign[0]].children, {})
        self.assertTrue(failure.exception.receipt.pending)

    def test_barrier_restore_and_reassert_failure_still_blocks_through_journal(self):
        store, outputs, before, inputs, _ = fixture()

        def hook(point, _tx):
            if point in (
                "after:root.flush",
                "before:rollback.barrier.restore",
                "before:recovery.barrier.create",
            ):
                raise Refusal("injected boundary failure")

        with self.assertRaises(TransactionError) as failure:
            execute(store, outputs, before, inputs, hook)
        self.assertEqual(failure.exception.receipt.status, "recovery-required")
        self.assertFalse(failure.exception.receipt.pending)
        self.assertTrue(
            any(
                "barrier-uncertain" in e
                for e in failure.exception.receipt.rollback_errors
            )
        )
        with self.assertRaisesRegex(Refusal, "unfinished or foreign history"):
            store.read_group(list(outputs), expected_run_identity=store.run)
        with self.assertRaises(TransactionError):
            execute(store, outputs, before, inputs)

    def test_case_insensitive_file_parent_and_move_collisions_preserve_foreign(self):
        store = SyntheticStore()
        foreign = store.create_file(store.run, "Stage-Manifest.JSON", b"foreign")
        with self.assertRaises(TransactionError):
            execute(
                store,
                {"stage-manifest.json": b"new"},
                {"stage-manifest.json": None},
                {},
            )
        self.assertEqual(store.named(store.run, "STAGE-MANIFEST.json"), foreign)
        self.assertEqual(store.read_handle(foreign), b"foreign")
        self.assertEqual(
            store.resolve("REPORTS/acceptance.json")[0],
            store.named(store.run, "reports"),
        )
        source = store.create_file(store.outside, "source", b"owned")
        with self.assertRaisesRegex(Refusal, "collision"):
            store.move(
                store.outside, "source", store.run, "stage-manifest.json", source
            )
        self.assertEqual(store.named(store.outside, "source"), source)
        self.assertEqual(store.named(store.run, "stage-manifest.json"), foreign)

    def test_case_insensitive_journal_collision_is_preserved(self):
        store, outputs, before, inputs, _ = fixture()
        tx = StagedReplacement(store)
        tx.preflight(outputs, before, inputs, store.run, "trusted-cooperating-owner/v1")
        foreign = store.mkdir(store.history, tx.request.upper(), owner="foreign")
        serial = store._serial
        with self.assertRaises(TransactionError):
            execute(store, outputs, before, inputs)
        self.assertEqual(store.named(store.history, tx.request), foreign)
        self.assertEqual(store._serial, serial)

    def test_all_successful_forward_boundaries_before_and_after_fail_without_false_success(
        self,
    ):
        store, outputs, before, inputs, _ = fixture()
        _, result = execute(store, outputs, before, inputs)
        points = list(result.trace)
        for target in points:
            with self.subTest(target=target):
                store, outputs, before, inputs, old = fixture()
                fired = []

                def hook(point, _tx, target=target, fired=fired):
                    if point == target and not fired:
                        fired.append(point)
                        raise Refusal(
                            "forward injected error, including late-success errors"
                        )

                with self.assertRaises(TransactionError) as failure:
                    execute(store, outputs, before, inputs, hook)
                self.assertEqual(fired, [target])
                receipt = failure.exception.receipt
                self.assertIn(receipt.status, ("preflight-refused", "rolled-back"))
                self.assertFalse(receipt.pending)
                for path, data in old.items():
                    self.assertEqual(live(store, path), data)
                    parent, leaf, _ = store.resolve(path)
                    self.assertEqual(store.named_binding(parent, leaf), before[path])
                self.assertIsNone(live(store, "reports/acceptance.json"))
                self.assertLessEqual(receipt.retained_bytes, MAX_EVIDENCE_BYTES)
                FORWARD_FAULT_CASES.append(target)

    def test_every_reached_rollback_boundary_failure_preserves_pending_and_never_succeeds(
        self,
    ):
        for primary in ("after:new.install.2", "after:root.flush"):
            store, outputs, before, inputs, _ = fixture()

            def baseline(point, _tx, primary=primary):
                if point == primary:
                    raise Refusal("primary failure")

            with self.assertRaises(TransactionError) as initial:
                execute(store, outputs, before, inputs, baseline)
            rollback_points = [
                p for p in initial.exception.receipt.trace if ":rollback." in p
            ]
            for secondary in rollback_points:
                with self.subTest(primary=primary, secondary=secondary):
                    store, outputs, before, inputs, old = fixture()
                    fired = []

                    def hook(
                        point, _tx, primary=primary, secondary=secondary, fired=fired
                    ):
                        if point == primary or point == secondary:
                            fired.append(point)
                            raise Refusal("primary or rollback injected failure")

                    with self.assertRaises(TransactionError) as failure:
                        execute(store, outputs, before, inputs, hook)
                    self.assertIn(primary, fired)
                    self.assertIn(secondary, fired)
                    receipt = failure.exception.receipt
                    self.assertEqual(receipt.status, "recovery-required")
                    self.assertTrue(receipt.pending)
                    for path, binding in before.items():
                        if binding:
                            self.assertEqual(
                                store.read_handle(binding.identity), old[path]
                            )
                            self.assertNotIn(binding.identity, store.writes)
                    with self.assertRaises(Refusal):
                        store.read_group(list(outputs), expected_run_identity=store.run)
                    ROLLBACK_FAULT_CASES.append((primary, secondary))

    def test_cooperating_reader_observes_pending_or_complete_group_never_gap(self):
        store, outputs, before, inputs, _ = fixture()
        checked = []

        def hook(point, _tx):
            if point == "after:old.move.0":
                with self.assertRaisesRegex(Refusal, "pending"):
                    store.read_group(list(outputs), expected_run_identity=store.run)
                checked.append(point)

        execute(store, outputs, before, inputs, hook)
        self.assertEqual(checked, ["after:old.move.0"])
        self.assertEqual(
            store.read_group(list(outputs), expected_run_identity=store.run), outputs
        )

    def test_cooperating_thread_waits_for_writer_lock_and_reopens_new_generation(self):
        store, outputs, before, inputs, _ = fixture()
        started = threading.Event()
        done = threading.Event()
        received, failures = [], []

        def reader():
            started.set()
            try:
                received.append(
                    store.read_group(list(outputs), expected_run_identity=store.run)
                )
            except Exception as error:  # noqa: BLE001 - Preserve every thread failure for assertion.
                failures.append(str(error))
            finally:
                done.set()

        thread = threading.Thread(target=reader)

        def hook(point, _tx):
            if point == "after:old.move.0":
                thread.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(done.is_set())

        execute(store, outputs, before, inputs, hook)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(received, [outputs])

    def test_reparse_nonregular_unsafe_names_bounds_and_stronger_contract_refuse(self):
        for path in (
            "../x",
            "/x",
            "a//x",
            "a/./x",
            "a\\x",
            "x:stream",
            "CON.json",
            "x.",
            "x ",
            "café.json",
            PENDING + "/x",
        ):
            with self.subTest(path=path):
                store = SyntheticStore()
                with self.assertRaises(TransactionError):
                    execute(store, {path: b"x"}, {path: None}, {})
                self.assertFalse(store.writes)
                self.assertIsNone(store.named(store.run, PENDING))
        for kind in ("reparse", "fifo"):
            store, outputs, before, inputs, _ = fixture()
            store.nodes[before["stage-manifest.json"].identity].kind = kind
            with self.assertRaises(TransactionError):
                execute(store, outputs, before, inputs)
            self.assertFalse(store.writes)
        store = SyntheticStore()
        with self.assertRaises(TransactionError):
            execute(store, {"x": b"x" * (MAX_FILE_BYTES + 1)}, {"x": None}, {})
        with self.assertRaises(TransactionError):
            StagedReplacement(store).execute(
                {"x": b"x"},
                expected_before={"x": None},
                expected_run_identity=store.run,
                contract="hostile-same-owner",
            )
        with self.assertRaises(TransactionError):
            execute(store, {"x": b"a", "X": b"b"}, {"x": None, "X": None}, {})

    def test_expected_bindings_must_cover_outputs_and_match_admitted_identity(self):
        store, outputs, before, inputs, _ = fixture()
        with self.assertRaises(TransactionError):
            execute(store, outputs, {}, inputs)
        store.raw_replace(store.run, "artifact-index.json", b'{"index":"old"}\n')
        with self.assertRaises(TransactionError):
            execute(store, outputs, before, inputs)
        self.assertIsNone(store.named(store.run, PENDING))

    def test_model_has_no_production_activation_or_automatic_retry(self):
        self.assertFalse(PRODUCTION_WINDOWS_WRITES_ENABLED)
        with self.assertRaisesRegex(Refusal, "disabled"):
            production_entrypoint()
        store, outputs, before, inputs, _ = fixture()
        tx, _ = execute(store, outputs, before, inputs)
        with self.assertRaisesRegex(Refusal, "single use"):
            tx.execute(outputs, expected_before=before, expected_run_identity=store.run)


class NativeRequestTests(unittest.TestCase):
    def test_mixed_case_reserved_native_names_are_refused(self):
        for kind in ("regular-file", "empty-directory"):
            base = self.request(kind=kind)
            for side in ("source_leaf", "destination_leaf"):
                for leaf in (
                    ".MILLEFEUILLE-METADATA-PENDING",
                    ".Millefeuille-Metadata-History",
                ):
                    with (
                        self.subTest(kind=kind, side=side, leaf=leaf),
                        self.assertRaises(Refusal),
                    ):
                        NativeMoveRequest(**{**base.__dict__, side: leaf}).packet()

    def request(self, *, kind="regular-file", api="NtSetInformationFile:10"):
        return NativeMoveRequest(
            "old-to-journal"
            if kind == "regular-file"
            else "barrier-to-committed-journal",
            api,
            0x1111,
            0x2222,
            0x3333,
            NativeObjectBinding(
                123,
                "1" * 32,
                kind,
                1,
                3 if kind == "regular-file" else 0,
                hashlib.sha256(b"old").hexdigest() if kind == "regular-file" else None,
            ),
            "2" * 32,
            "3" * 32,
            "source",
            "target",
            run_root_handle_token=0x4444,
            run_file_id_128="4" * 32,
        )

    def test_file_and_empty_directory_packets_are_relative_no_replace_unqualified(self):
        for kind in ("regular-file", "empty-directory"):
            for api in ("NtSetInformationFile:10", "SDK:SetFileInformationByHandle:3"):
                with self.subTest(kind=kind, api=api):
                    request = self.request(kind=kind, api=api)
                    packet = request.packet()
                    self.assertFalse(packet["replace_if_exists"])
                    self.assertEqual(packet["flags"], 0)
                    self.assertFalse(packet["native_qualified"])
                    self.assertFalse(packet["native_execution_authorized"])
                    self.assertFalse(packet["production_windows_writes_enabled"])
                    buf = request.candidate_amd64_buffer()
                    self.assertEqual(struct.unpack_from("<I", buf, 0)[0], 0)
                    self.assertEqual(struct.unpack_from("<Q", buf, 8)[0], 0x3333)
                    self.assertEqual(struct.unpack_from("<I", buf, 16)[0], 12)
                    self.assertEqual(buf[20:32], "target".encode("utf-16-le"))
                    self.assertEqual(len(buf), 38)

    def test_native_dispatch_and_flush_refuse_on_every_platform(self):
        adapter = UnqualifiedNativeAdapter()
        with self.assertRaisesRegex(Refusal, "dispatch remains disabled"):
            adapter.move(self.request())
        with self.assertRaisesRegex(Refusal, "durability contract"):
            adapter.flush(1)

    def test_actual_pending_archive_restore_packets_and_reserved_name_refusal(self):
        base = self.request(kind="empty-directory")
        for operation, source, target in (
            ("barrier-to-committed-journal", PENDING, "committed-barrier"),
            ("barrier-to-rolled-back-journal", PENDING, "rolled-back-barrier"),
            ("barrier-restore-to-run", "committed-barrier", PENDING),
            ("barrier-restore-to-run", "rolled-back-barrier", PENDING),
        ):
            with self.subTest(operation=operation):
                request = NativeMoveRequest(
                    **{
                        **base.__dict__,
                        "operation": operation,
                        "source_leaf": source,
                        "destination_leaf": target,
                    }
                )
                packet = request.packet()
                self.assertTrue(packet["owned_barrier_creation_receipt_required"])
                self.assertFalse(packet["replace_if_exists"])
                self.assertIn(
                    target.encode("utf-16-le"), request.candidate_amd64_buffer()
                )
        for change in (
            {"operation": "arbitrary-operation"},
            {"operation": "stage-to-live"},
            {"source_leaf": ".millefeuille-metadata-other"},
            {"destination_leaf": PENDING},
        ):
            with self.subTest(change=change), self.assertRaises(Refusal):
                NativeMoveRequest(**{**base.__dict__, **change}).packet()

    def test_unsafe_native_leaf_zero_handle_and_link_binding_refuse(self):
        baseline = self.request()
        for change in (
            {"destination_leaf": "../target"},
            {"destination_leaf": "target:stream"},
            {"destination_leaf": "NUL"},
            {"destination_parent_handle_token": 0},
            {
                "source_binding": NativeObjectBinding(
                    123, "1" * 32, "regular-file", 2, 3, "a" * 64
                )
            },
        ):
            values = {**baseline.__dict__, **change}
            with self.subTest(change=change), self.assertRaises(Refusal):
                NativeMoveRequest(**values).packet()


if __name__ == "__main__":
    unittest.main(verbosity=2)
