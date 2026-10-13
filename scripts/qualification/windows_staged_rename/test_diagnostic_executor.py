"""Offline memory-only syscall/controller/packaging simulations, not native proof."""

import copy
import ctypes
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import build_manifest as bm
import diagnostic_core as dc
import native_controller as nc
import native_worker as nw
from references.probe_contract import RenameInformation, rename_rows
from staged_replacement import Refusal


def object_binding(number, kind="regular-file", data=b"source"):
    return {
        "volume": 1,
        "file_id": f"{number:032x}",
        "kind": kind,
        "links": 1,
        "bytes": len(data) if kind == "regular-file" else 0,
        "sha256": hashlib.sha256(data).hexdigest() if kind == "regular-file" else None,
        "empty": kind == "empty-directory",
        "reparse": False,
    }


class MemoryRename:
    native_execution = False

    def __init__(self, mode=None):
        self.mode, self.calls, self.closes, self.case_evidence = mode, [], 0, {}

    def prepare(self, number, row):
        source = object_binding(number + 1, row["kind"])
        target = (
            object_binding(number + 100, data=b"foreign")
            if row["target_exists"]
            else None
        )
        return {
            "row": row,
            "source": source,
            "target": target,
            "held": copy.deepcopy(source),
            "moved": False,
        }

    def inspect(self, fixture):
        parents = {
            name: {
                "volume": 1,
                "file_id": f"{number:032x}",
                "links": 1,
                "reparse": False,
            }
            for number, name in ((999, "source_parent"), (998, "destination_parent"))
        }
        return {
            "source": fixture["source"],
            "target": fixture["target"],
            "held_source": fixture["held"],
            "parents": parents,
            "granted_delete": True,
            "granted_parent_transition": True,
            "settings": {"requested": 0x100080, "granted": 0x100080, "sharing": 7},
        }

    def rename(self, fixture, row):
        self.calls.append(row["id"])
        if self.mode == "sdk-mismatch" and row["builder"] == "compiled-SDK":
            raise Refusal("simulated installed SDK layout mismatch")
        collision = row["target_exists"]
        if not collision:
            fixture["target"], fixture["source"], fixture["moved"] = (
                fixture["source"],
                None,
                True,
            )
        if self.mode == "collision-clobber" and collision:
            fixture["target"], fixture["source"] = fixture["source"], None
        nt = row["api"].startswith("NtSet")
        error = self.mode == "late-error" and not collision
        return {
            "api": row["api"],
            "bool": None if nt else not (collision or error),
            "win32_error": None
            if nt or not (collision or error)
            else 183
            if collision
            else 5,
            "ntstatus": (0xC0000035 if collision else 0xC0000022 if error else 0)
            if nt
            else None,
            "iosb_status": (0xC0000035 if collision else 0xC0000022 if error else 0)
            if nt
            else None,
            "completion_status": (
                0xC0000035 if collision else 0xC0000022 if error else 0
            )
            if nt
            else None,
            "completion_confirmed": nt,
            "unresolved_pending": False,
        }

    def close_fixture(self, _fixture):
        self.closes += 1


def limits():
    return {
        "memory": nc.MEMORY,
        "process_memory": nc.MEMORY,
        "active_limit": 1,
        "flags": nc.FLAGS,
        "affinity": 2,
        "child_affinity": 2,
        "cpu_flags": 5,
        "cpu_rate": 5000,
        "expected_cpu_rate": 5000,
        "membership": True,
        "active": 1,
        "assigned": 1,
        "pids": [123],
        "pid": 123,
    }


class MemoryController:
    native_execution = False
    expected_head, manifest_sha256, sdk_sha256 = "a" * 40, "b" * 64, "c" * 64

    def __init__(self, fail=None):
        self.fail, self.now, self.pid, self.has_job, self.has_process_handles = (
            fail,
            0,
            0,
            False,
            False,
        )
        self.events, self.waits = [], 0

    def clock(self):
        return self.now

    def step(self, name):
        self.events.append(name)
        if self.fail == name:
            raise Refusal("injected " + name)

    def create_job(self):
        self.step("before-job")
        self.has_job = True
        self.step("after-job")

    def create_suspended(self):
        self.step("before-create")
        self.pid, self.has_process_handles = 123, True
        self.step("after-create")

    def assign_and_readback(self):
        self.step("assign")
        return limits()

    def resume(self):
        self.step("resume")

    def wait_primary(self, _milliseconds):
        self.step("wait")
        self.waits += 1
        self.now += 291 if self.fail == "deadline" else 0.01
        return self.fail not in {"deadline", "bytes"}

    def owned_bytes(self):
        return dc.ARTIFACT_CAP + 1 if self.fail == "bytes" else 1

    def worker_result(self):
        self.step("receipt")
        value = dc.run_ten_cases(MemoryRename())
        value.update(
            {
                "pid": self.pid,
                "owned_file_handles_remaining": 0,
                "expected_head": self.expected_head,
                "source_manifest_sha256": self.manifest_sha256,
                "sdk_sha256": self.sdk_sha256,
                "live_pending_request_buffers": 0,
            }
        )
        return value

    def exit_code(self):
        return 0

    def cleanup(self, seconds):
        self.events.append("cleanup")
        self.cleanup_seconds = seconds
        if self.fail == "cleanup":
            raise Refusal("uncertain cleanup")
        return {
            "primary_signaled": True,
            "active": 0,
            "assigned": 0,
            "pids": [],
            "closes": {"process": True, "thread": True, "job": True},
            "no_owned_process": not self.has_process_handles,
            "job_closed": True,
        }


class DiagnosticTests(unittest.TestCase):
    def test_exact_ten_memory_cases_no_hardlink_dispatch(self):
        backend = MemoryRename()
        with patch(
            "references.probe_contract.alias_rows",
            side_effect=AssertionError("unused hardlink probes"),
        ):
            result = dc.run_ten_cases(backend)
        self.assertEqual(
            (result["tests_run"], result["passed"], result["failed"], result["skips"]),
            (10, 10, 0, 0),
        )
        self.assertEqual(len(backend.calls), 10)
        self.assertEqual(backend.closes, 10)
        self.assertEqual(sum(not row["target_exists"] for row in rename_rows()), 8)
        self.assertFalse(result["native_primitive_baselines_qualified"])
        self.assertFalse(result["application_candidate_qualified"])

    def test_layout_failures_are_failed_mandatory_cases(self):
        result = dc.run_ten_cases(MemoryRename("sdk-mismatch"))
        self.assertEqual(
            (result["tests_run"], result["failed"], result["skips"]), (10, 2, 0)
        )

    def test_late_error_reconciles_completed_moves_without_passing_or_retry(self):
        backend = MemoryRename("late-error")
        result = dc.run_ten_cases(backend)
        self.assertEqual(len(backend.calls), 10)
        for record in result["cases"]:
            if not record["row"]["target_exists"]:
                self.assertEqual(record["reconciliation"], "late-error-completed-move")
            self.assertFalse(record["passed"])

    def test_collision_preserves_foreign_binding_and_requires_baseline(self):
        backend = MemoryRename()
        row = rename_rows()[8]
        fixture = backend.prepare(8, row)
        before = backend.inspect(fixture)
        call = backend.rename(fixture, row)
        after = backend.inspect(fixture)
        self.assertFalse(dc.assess_case(row, before, call, after)["passed"])
        result = dc.run_ten_cases(MemoryRename("collision-clobber"))
        self.assertEqual(result["failed"], 2)
        self.assertTrue(all(not r["passed"] for r in result["cases"][-2:]))

    def test_parent_alias_access_identity_drift_fail_admission(self):
        row = rename_rows()[1]
        fixture = MemoryRename().prepare(1, row)
        backend = MemoryRename()
        before = backend.inspect(fixture)
        call = backend.rename(fixture, row)
        after = backend.inspect(fixture)
        mutations = [
            ("granted_delete", False),
            ("granted_parent_transition", False),
            ("parents", {}),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                changed = copy.deepcopy(before)
                changed[field] = value
                self.assertFalse(dc.assess_case(row, changed, call, after)["passed"])
        for field, value in (("links", 2), ("file_id", "bad"), ("reparse", True)):
            with self.subTest(object_field=field):
                changed = copy.deepcopy(before)
                changed["source"][field] = value
                self.assertFalse(dc.assess_case(row, changed, call, after)["passed"])

    def test_fixed_windows_widths_and_amd64_layout(self):
        self.assertEqual(
            (ctypes.sizeof(nw.U32), ctypes.sizeof(nw.I32), ctypes.sizeof(nw.IOSB)),
            (4, 4, 16),
        )
        self.assertEqual(
            (
                ctypes.sizeof(nw.FileIdInfo),
                ctypes.sizeof(nw.FileStandardInfo),
                ctypes.sizeof(nw.AttributeTag),
            ),
            (24, 24, 8),
        )
        self.assertEqual(
            (
                ctypes.sizeof(nc.ExtendedLimit),
                ctypes.sizeof(nc.Startup),
                ctypes.sizeof(nc.Accounting),
            ),
            (144, 104, 48),
        )
        self.assertEqual(
            (RenameInformation.root.offset, RenameInformation.name.offset), (8, 20)
        )

    def fake_native(self, *, ntstatus=0, iosb_status=0, wait=0, win32=True):
        backend = nw.NativeBackend.__new__(nw.NativeBackend)
        backend.deadline, backend.poisoned = float("inf"), False
        backend.owned_handles, backend.closed_handles, backend.failed_handles = (
            [11],
            [],
            set(),
        )

        def nt_call(_handle, iosb, _buffer, _length, kind):
            self.assertEqual(kind, 10)
            ctypes.cast(
                iosb, ctypes.POINTER(nw.IOSB)
            ).contents.value.status = ctypes.c_int32(iosb_status).value
            return ctypes.c_int32(ntstatus).value

        backend.nt = SimpleNamespace(NtSetInformationFile=nt_call)
        backend.kernel = SimpleNamespace(
            SetFileInformationByHandle=lambda *_args: win32,
            WaitForSingleObject=lambda *_args: wait,
            CloseHandle=lambda *_args: 1,
        )
        backend.sdk_layout = {
            "status": 0,
            "values": [24, 8, 16, 20, 8, 2, 4],
            "expected": [24, 8, 16, 20, 8, 2, 4],
        }
        return backend, {
            "source_handle": 11,
            "parent_handle": 22,
            "target": Path("/owned/target"),
        }

    def test_win32_success_does_not_capture_stale_last_error(self):
        backend, fixture = self.fake_native()
        with patch.object(
            ctypes,
            "get_last_error",
            side_effect=AssertionError("success LastError"),
            create=True,
        ):
            call = backend.rename(fixture, rename_rows()[1])
        self.assertTrue(call["bool"])
        self.assertIsNone(call["win32_error"])

    def test_sdk_mismatch_blocks_call_and_sdk_success_error_is_not_meaningful(self):
        backend, fixture = self.fake_native()
        invoked = []

        def sdk_call(_source, _parent, _name, _name_bytes, error, count):
            invoked.append(True)
            ctypes.cast(error, ctypes.POINTER(nw.U32)).contents.value = 12345
            ctypes.cast(count, ctypes.POINTER(nw.U32)).contents.value = 38
            return 1

        backend.sdk = SimpleNamespace(mf_sdk_rename=sdk_call)
        row = rename_rows()[2]
        call = backend.rename(fixture, row)
        self.assertIsNone(call["win32_error"])
        self.assertIsNone(call["sdk_output_error"])
        backend.sdk_layout["values"] = [0]
        with self.assertRaises(Refusal):
            backend.rename(fixture, row)
        self.assertEqual(len(invoked), 1)

    def test_pending_retains_iosb_buffer_handle_and_stops_case_progression(self):
        backend, fixture = self.fake_native(
            ntstatus=nw.STATUS_PENDING, iosb_status=nw.STATUS_PENDING, wait=0x102
        )
        try:
            call = backend.rename(fixture, rename_rows()[3])
            self.assertTrue(call["unresolved_pending"])
            self.assertEqual(len(nw.LIVE_PENDING_REQUESTS), 1)
            self.assertEqual(nw.LIVE_PENDING_REQUESTS[0]["source_handle"], 11)
            self.assertIsInstance(nw.LIVE_PENDING_REQUESTS[0]["iosb"], nw.IOSB)
            self.assertGreater(len(nw.LIVE_PENDING_REQUESTS[0]["buffer"]), 20)
            backend.close_fixture(fixture)
            self.assertEqual(backend.owned_handles, [11])
            backend.sdk_layout = {}
            with self.assertRaises(Refusal):
                backend.prepare(4, rename_rows()[4])
        finally:
            # These are Python fake requests; no kernel has referenced this storage.
            nw.LIVE_PENDING_REQUESTS.clear()

    def test_pending_completed_retains_only_until_confirmed_completion(self):
        backend, fixture = self.fake_native(
            ntstatus=nw.STATUS_PENDING, iosb_status=0, wait=0
        )
        call = backend.rename(fixture, rename_rows()[3])
        self.assertTrue(call["completion_confirmed"])
        self.assertFalse(call["unresolved_pending"])
        self.assertEqual(nw.LIVE_PENDING_REQUESTS, [])

    def test_pending_wait_exception_blocks_all_further_prepare_and_dispatch(self):
        backend, fixture = self.fake_native(
            ntstatus=nw.STATUS_PENDING, iosb_status=nw.STATUS_PENDING
        )

        def failed_wait(*_args):
            raise RuntimeError("simulated pending wait exception")

        backend.kernel.WaitForSingleObject = failed_wait
        try:
            with self.assertRaises(RuntimeError):
                backend.rename(fixture, rename_rows()[3])
            self.assertEqual(len(nw.LIVE_PENDING_REQUESTS), 1)
            with self.assertRaises(Refusal):
                backend.prepare(4, rename_rows()[4])
            with (
                patch.object(
                    backend.nt,
                    "NtSetInformationFile",
                    side_effect=AssertionError("no second dispatch"),
                ),
                self.assertRaises(Refusal),
            ):
                backend.rename(fixture, rename_rows()[3])
            backend.close_fixture(fixture)
            self.assertEqual(backend.owned_handles, [11])
        finally:
            nw.LIVE_PENDING_REQUESTS.clear()

    def test_request_retention_failure_precedes_every_nt_kernel_effect(self):
        backend, fixture = self.fake_native()

        class FailedRetention(list):
            def append(self, _request):
                raise MemoryError("injected retention allocation failure")

        with (
            patch.object(nw, "LIVE_PENDING_REQUESTS", FailedRetention()),
            patch.object(
                backend.nt,
                "NtSetInformationFile",
                side_effect=AssertionError("kernel must not be called"),
            ) as native_call,
            self.assertRaises(MemoryError),
        ):
            backend.rename(fixture, rename_rows()[3])
        native_call.assert_not_called()

    def test_post_dispatch_exception_keeps_pre_registered_request_owned(self):
        backend, fixture = self.fake_native()

        def failed_dispatch(*_args):
            raise RuntimeError("simulated uncertain syscall return")

        backend.nt.NtSetInformationFile = failed_dispatch
        try:
            with self.assertRaises(RuntimeError):
                backend.rename(fixture, rename_rows()[3])
            self.assertEqual(len(nw.LIVE_PENDING_REQUESTS), 1)
            self.assertEqual(nw.LIVE_PENDING_REQUESTS[0]["source_handle"], 11)
            with self.assertRaises(Refusal):
                backend.prepare(4, rename_rows()[4])
        finally:
            nw.LIVE_PENDING_REQUESTS.clear()

    def test_pending_exit_is_unconditional_across_every_reporting_failure(self):
        class ExitObserved(BaseException):
            pass

        for failure in (
            "run",
            "serialization",
            "open",
            "write",
            "receipt-flush",
            "stdout-flush",
            "stderr-flush",
        ):
            with self.subTest(failure=failure):

                class Receipt:
                    def __enter__(self):
                        return self

                    def __exit__(self, *_arguments):
                        return False

                    def write(self, _data, failure=failure):
                        if failure == "write":
                            raise OSError("injected write")

                    def flush(self, failure=failure):
                        if failure == "receipt-flush":
                            raise OSError("injected receipt flush")

                    def open(self, _mode, failure=failure):
                        if failure == "open":
                            raise OSError("injected open")
                        return self

                    def __truediv__(self, _leaf):
                        return self

                def run(_backend, failure=failure):
                    nw.LIVE_PENDING_REQUESTS.append(
                        {
                            "iosb": nw.IOSB(),
                            "buffer": ctypes.create_string_buffer(38),
                            "source_handle": 11,
                        }
                    )
                    if failure == "run":
                        raise RuntimeError("injected run reporting failure")
                    return {"failed": 10}

                def serialize(_result, failure=failure):
                    if failure == "serialization":
                        raise Refusal("injected serialization")
                    return b"receipt"

                def flush(name, failure=failure):
                    if failure == name + "-flush":
                        raise KeyboardInterrupt("injected stream flush")

                backend = SimpleNamespace(sdk_layout={}, owned_handles=[11])
                try:
                    with (
                        patch.object(nw, "run_ten_cases", side_effect=run),
                        patch.object(nw, "record_bytes", side_effect=serialize),
                        patch.object(
                            nw.os, "_exit", side_effect=ExitObserved
                        ) as exit_call,
                        patch.object(
                            nw.sys,
                            "stdout",
                            SimpleNamespace(flush=lambda: flush("stdout")),
                        ),
                        patch.object(
                            nw.sys,
                            "stderr",
                            SimpleNamespace(flush=lambda: flush("stderr")),
                        ),
                        self.assertRaises(ExitObserved),
                    ):
                        nw.execute_worker(
                            backend, Receipt(), started=0, receipt_bindings={}
                        )
                    exit_call.assert_called_once_with(1)
                    self.assertEqual(len(nw.LIVE_PENDING_REQUESTS), 1)
                finally:
                    # Python fakes; no native kernel request.
                    nw.LIVE_PENDING_REQUESTS.clear()

    def test_controller_happy_path_is_simulated_only(self):
        backend = MemoryController()
        result = nc.execute_controller(backend)
        self.assertTrue(result["passed"])
        self.assertTrue(result["terminal"])
        self.assertFalse(result["native_execution"])
        self.assertFalse(result["production_windows_writes_enabled"])
        self.assertLessEqual(backend.cleanup_seconds, 10)

    def test_controller_failure_boundaries_always_cleanup_no_retry(self):
        for fail in (
            "before-job",
            "after-job",
            "before-create",
            "after-create",
            "assign",
            "resume",
            "wait",
            "receipt",
            "deadline",
            "bytes",
            "cleanup",
        ):
            with self.subTest(fail=fail):
                backend = MemoryController(fail)
                result = nc.execute_controller(backend)
                self.assertFalse(result["passed"])
                self.assertEqual(backend.events.count("cleanup"), 1)
                self.assertLessEqual(backend.events.count("after-create"), 1)
                if fail == "cleanup":
                    self.assertFalse(result["terminal"])
                    self.assertEqual(result["owned_operations_in_flight"], 1)

    def test_limits_reject_each_bad_readback(self):
        changes = {
            "memory": 1,
            "process_memory": 1,
            "active_limit": 2,
            "affinity": 3,
            "child_affinity": 4,
            "flags": nc.FLAGS | 0x800,
            "cpu_flags": 0,
            "cpu_rate": 10000,
            "membership": False,
            "active": 0,
            "assigned": 2,
            "pids": [123, 456],
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                bad = limits()
                bad[field] = value
                with self.assertRaises(Refusal):
                    nc.validate_limits(bad)

    def test_worker_pin_pending_and_row_identity_drift_refused(self):
        for field, value in (
            ("expected_head", "f" * 40),
            ("sdk_sha256", "f" * 64),
            ("source_manifest_sha256", "f" * 64),
            ("native_execution", True),
            ("live_pending_request_buffers", 1),
            ("owned_file_handles_remaining", 1),
            ("hardlink_probe_cases", 6),
            ("skips", 1),
        ):
            with self.subTest(field=field):
                backend = MemoryController()
                original = backend.worker_result

                def changed(original=original, field=field, value=value):
                    result = original()
                    result[field] = value
                    return result

                backend.worker_result = changed
                result = nc.execute_controller(backend)
                self.assertFalse(result["worker_qualified"])
                self.assertTrue(result["terminal"])

    def test_native_cleanup_terminates_job_residue_after_primary_exit(self):
        backend = nc.NativeJobBackend.__new__(nc.NativeJobBackend)
        backend.process, backend.job, backend.assigned, backend.samples = (
            nc.ProcessInfo(10, 11, 123, 124),
            99,
            True,
            [],
        )
        calls = []
        backend.finish_stdio = lambda: True
        backend.kernel = SimpleNamespace(
            TerminateJobObject=lambda handle, _code: (
                calls.append(("terminate-job", handle)) or 1
            ),
            CloseHandle=lambda handle: calls.append(("close", handle)) or 1,
        )
        samples = iter(
            [
                {"active": 1, "assigned": 1, "pids": [456]},
                {"active": 0, "assigned": 0, "pids": []},
            ]
        )
        backend.sample = lambda: next(samples)
        backend.clock, backend.wait_primary, backend.exit_code = (
            lambda: 0,
            lambda _ms: True,
            lambda: 0,
        )
        result = backend.cleanup(10)
        self.assertTrue(result["termination_requested"])
        self.assertEqual(
            calls, [("terminate-job", 99), ("close", 11), ("close", 10), ("close", 99)]
        )

    def test_partial_created_handles_without_pid_cannot_be_no_owned_process(self):
        backend = nc.NativeJobBackend.__new__(nc.NativeJobBackend)
        backend.process, backend.job, backend.samples = (
            nc.ProcessInfo(10, 11, 0, 0),
            99,
            [],
        )
        backend.clock = lambda: 0
        with self.assertRaises(Refusal):
            backend.cleanup(10)
        simulated = MemoryController()

        def late_create():
            simulated.has_process_handles = True
            raise Refusal("late failed create with no PID")

        simulated.create_suspended = late_create
        simulated.cleanup = lambda _seconds: {
            "no_owned_process": False,
            "active": 0,
            "assigned": 0,
            "pids": [],
            "job_closed": False,
        }
        result = nc.execute_controller(simulated)
        self.assertFalse(result["terminal"])
        self.assertEqual(result["owned_operations_in_flight"], 1)

    def test_late_create_error_reconciles_returned_pid_without_retry(self):
        backend = nc.NativeJobBackend.__new__(nc.NativeJobBackend)
        backend.process, backend.argv, backend.root = (
            nc.ProcessInfo(),
            ["python", "-I"],
            Path("/owned"),
        )
        creates = []
        backend.prepare_stdio = lambda _startup: None
        backend.delete_startup_attributes = lambda: None
        backend.close_stdio_names = lambda _names: True
        backend.attribute_delete_uncertain = False

        def created(*arguments):
            creates.append(True)
            process = ctypes.cast(
                arguments[-1], ctypes.POINTER(nc.ProcessInfo)
            ).contents
            process.process, process.thread, process.pid = 10, 11, 0
            return 0

        backend.kernel = SimpleNamespace(
            CreateProcessW=created,
            GetProcessId=lambda handle: 123 if handle == 10 else 0,
        )

        def checked(value):
            if not value:
                raise Refusal("simulated late CreateProcess BOOL failure")
            return value

        backend.checked = checked
        with (
            patch.dict(nc.os.environ, {"SYSTEMROOT": "C:\\Windows"}),
            self.assertRaises(Refusal),
        ):
            backend.create_suspended()
        self.assertEqual(backend.pid, 123)
        self.assertTrue(backend.has_process_handles)
        self.assertEqual(len(creates), 1)

    def test_complete_pid_query_grows_and_refuses_unbounded_or_inconsistent_lists(self):
        for assigned, count, expected in (
            (2, 2, [123, 456]),
            (33, 0, None),
            (1, 2, None),
        ):
            with self.subTest(assigned=assigned, count=count):
                backend = nc.NativeJobBackend.__new__(nc.NativeJobBackend)
                backend.job = 99
                calls = []

                def query(
                    _job,
                    kind,
                    data,
                    size,
                    _used,
                    assigned=assigned,
                    count=count,
                    calls=calls,
                ):
                    calls.append(size)
                    self.assertEqual(kind, 3)
                    nc.U32.from_buffer(data, 0).value = assigned
                    nc.U32.from_buffer(data, 4).value = count
                    capacity = (size - 8) // ctypes.sizeof(ctypes.c_size_t)
                    for i, pid in enumerate((123, 456)[: min(count, capacity)]):
                        ctypes.c_size_t.from_buffer(
                            data, 8 + i * ctypes.sizeof(ctypes.c_size_t)
                        ).value = pid
                    return int(capacity >= count)

                backend.kernel = SimpleNamespace(QueryInformationJobObject=query)
                with patch.object(
                    ctypes, "get_last_error", return_value=234, create=True
                ):
                    if expected is None:
                        with self.assertRaises(Refusal):
                            backend.pids()
                    else:
                        self.assertEqual(backend.pids(), (assigned, expected))
                        self.assertEqual(len(calls), 2)

    def test_manifest_exact_runtime_byte_pins_head_and_sdk(self):
        sources = {name: name.encode() for name in dc.SOURCE_FILES}
        self.assertEqual(len(sources), 10)
        self.assertIn("references/__init__.py", sources)
        sdk = b"compiled-sdk-synthetic"
        manifest = {
            "schema": dc.SCHEMA,
            "expected_head": "a" * 40,
            "files": [
                {
                    "path": name,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for name, data in sources.items()
            ],
            "sdk": {"bytes": len(sdk), "sha256": hashlib.sha256(sdk).hexdigest()},
        }
        dc.validate_manifest(
            manifest,
            expected_head="a" * 40,
            source_reader=sources.__getitem__,
            sdk_reader=lambda: sdk,
        )
        for change in ("head", "source", "sdk", "missing", "duplicate"):
            with self.subTest(change=change):
                bad = copy.deepcopy(manifest)
                if change == "head":
                    bad["expected_head"] = "b" * 40
                elif change == "missing":
                    bad["files"].pop()
                elif change == "duplicate":
                    bad["files"][-1] = bad["files"][0]
                elif change == "source":
                    bad["files"][0]["sha256"] = "0" * 64
                else:
                    bad["sdk"]["sha256"] = "0" * 64
                with self.assertRaises(Refusal):
                    dc.validate_manifest(
                        bad,
                        expected_head="a" * 40,
                        source_reader=sources.__getitem__,
                        sdk_reader=lambda: sdk,
                    )

    def test_package_combined_cap_determinism_and_safe_names(self):
        entries = {
            "worker-result.json": dc.record_bytes({"passed": True}),
            "case-00/source": b"source",
        }
        data = dc.package_bytes(entries)
        self.assertEqual(data, dc.package_bytes(entries))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(
                {name: archive.read(name) for name in archive.namelist()}, entries
            )
        for bad in (
            {"../foreign": b"x"},
            {"Result": b"a", "result": b"b"},
            {"large": b"x" * (dc.FILE_CAP + 1)},
            {f"file-{i}": b"x" for i in range(129)},
            {f"file-{i}": b"x" * dc.FILE_CAP for i in range(4)},
        ):
            with self.subTest(names=list(bad)[:2]), self.assertRaises(Refusal):
                dc.package_bytes(bad)

    def test_read_plain_retains_descriptor_and_never_reads_beyond_cap_plus_one(self):
        info = SimpleNamespace(
            st_dev=1, st_ino=2, st_size=3, st_mtime_ns=4, st_nlink=1, st_mode=0o100600
        )

        class Reader(io.BytesIO):
            def read(self, count=-1):
                self.requested = count
                self_test.assertEqual(count, 5)
                return super().read(count)

        self_test, closed = self, []
        for data, passes in ((b"abc", True), (b"abcdef", False)):
            with self.subTest(data=data):
                reader = Reader(data)
                with (
                    patch.object(bm, "plain_path", return_value=info),
                    patch.object(bm.os, "open", return_value=7),
                    patch.object(bm.os, "fstat", return_value=info),
                    patch.object(bm.os, "fdopen", return_value=reader),
                    patch.object(bm.os, "close", side_effect=closed.append),
                ):
                    if passes:
                        self.assertEqual(bm.read_plain(Path("/source"), 4), b"abc")
                    else:
                        with self.assertRaises(Refusal):
                            bm.read_plain(Path("/source"), 4)
        self.assertEqual(closed, [7, 7])

    def test_native_entrypoints_fail_closed_on_this_offline_host(self):
        with self.assertRaises(Refusal):
            nw.NativeBackend(Path("/not-read"), Path("/not-read.dll"), deadline=0)
        with self.assertRaises(Refusal):
            nc.NativeJobBackend(Path("/not-read"), [], source_root=Path("/not-read"))
        with self.assertRaises(Refusal):
            nc.require_worker_job()


if __name__ == "__main__":
    unittest.main()
