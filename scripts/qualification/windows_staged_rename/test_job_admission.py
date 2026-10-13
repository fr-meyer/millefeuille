"""Memory-only Job API/snapshot/identity diagnostics; never Windows evidence."""

import ctypes
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).absolute().parent))

import native_controller as nc
import native_worker as nw
from staged_replacement import Refusal
from test_diagnostic_executor import MemoryController, limits
from worker_diagnostics import (
    ADMISSION_LINE_CAP,
    MESSAGE_CAP,
    AdmissionRefusal,
    BoundedStreams,
    admission_line,
    admission_record,
    emit_error,
    exception_record,
)


def put(pointer, kind, value):
    ctypes.cast(pointer, ctypes.POINTER(kind))[0] = value


class Function:
    def __init__(self, handler):
        self.handler = handler

    def __call__(self, *args):
        return self.handler(*args)


class JobKernel:
    """ctypes ABI objects contain only Python-owned fake bytes/handles."""

    def __init__(
        self,
        *,
        active=1,
        after_active=1,
        assigned=1,
        ids=(123,),
        member=1,
        win_pid=123,
        fail_kind=None,
        fail_after=False,
        before_total=2,
        after_total=2,
    ):
        self.active, self.after_active, self.assigned, self.ids = (
            active,
            after_active,
            assigned,
            ids,
        )
        self.member, self.win_pid = member, win_pid
        self.fail_kind, self.fail_after = fail_kind, fail_after
        self.before_total, self.after_total = before_total, after_total
        self.accounting_calls, self.error, self.calls = 0, 0, []
        self.GetCurrentProcess = Function(lambda: -1)
        self.GetCurrentProcessId = Function(lambda: self.win_pid)
        self.IsProcessInJob = Function(self.membership)
        self.GetProcessAffinityMask = Function(self.affinity)
        self.GetActiveProcessorCount = Function(lambda _group: 4)
        self.QueryInformationJobObject = Function(self.query)

    def membership(self, _process, job, output):
        assert job is None  # Current default-job selection is retained.
        put(output, nc.I32, self.member)
        return 1

    @staticmethod
    def affinity(_process, output, system):
        put(output, ctypes.c_size_t, 1)
        put(system, ctypes.c_size_t, 15)
        return 1

    def query(self, job, kind, output, size, used):
        assert job is None
        self.calls.append(kind)
        if self.fail_kind == kind:
            self.error = 87
            return 0
        if kind == 9:
            value = ctypes.cast(output, ctypes.POINTER(nc.ExtendedLimit)).contents
            value.job_memory = value.process_memory = nc.MEMORY
            value.basic.flags, value.basic.active_limit, value.basic.affinity = (
                nc.FLAGS,
                1,
                1,
            )
        elif kind == 15:
            value = ctypes.cast(output, ctypes.POINTER(nc.CpuRate)).contents
            value.flags, value.rate = 5, 2500
        elif kind == 1:
            self.accounting_calls += 1
            if self.accounting_calls == 2 and self.fail_after:
                self.error = 5
                return 0
            value = ctypes.cast(output, ctypes.POINTER(nc.Accounting)).contents
            value.active = (
                self.active if self.accounting_calls == 1 else self.after_active
            )
            value.total_processes = (
                self.before_total if self.accounting_calls == 1 else self.after_total
            )
            value.terminated = 0
        elif kind == 3:
            assert size >= 16
            capacity = (size - 8) // ctypes.sizeof(ctypes.c_size_t)
            data = ctypes.cast(output, ctypes.POINTER(ctypes.c_ubyte * size)).contents
            nc.U32.from_buffer(data, 0).value = self.assigned
            nc.U32.from_buffer(data, 4).value = len(self.ids)
            if used is not None:
                put(used, nc.U32, 8 + len(self.ids) * 8)
            for index, pid in enumerate(self.ids[:capacity]):
                ctypes.c_size_t.from_buffer(data, 8 + index * 8).value = pid
            if len(self.ids) > capacity:
                self.error = 234
                return 0
        else:
            raise AssertionError("unexpected fake information class")
        self.error = 0
        return 1


def run_fake(kernel):
    def winerror(code):
        error = OSError("synthetic Job query failed")
        error.winerror = code
        return error

    with (
        patch.object(nc.os, "name", "nt"),
        patch.object(nc.sys, "platform", "win32"),
        patch.object(nc.os, "getpid", return_value=123),
        patch.object(ctypes, "WinDLL", return_value=kernel, create=True),
        patch.object(
            ctypes, "get_last_error", side_effect=lambda: kernel.error, create=True
        ),
        patch.object(ctypes, "WinError", side_effect=winerror, create=True),
    ):
        return nc.require_worker_job()


class AdmissionTests(unittest.TestCase):
    def test_each_composite_operand_is_recorded_and_still_refuses(self):
        for key, value in (
            ("membership", False),
            ("active", 0),
            ("active", 2),
            ("assigned", 0),
            ("assigned", 2),
            ("pids", []),
            ("pids", [999]),
            ("pids", [123, 999]),
        ):
            with self.subTest(key=key, value=value):
                raw = limits()
                raw[key] = value
                with self.assertRaises(AdmissionRefusal) as caught:
                    nc.validate_limits(raw)
                record = caught.exception.numeric
                self.assertEqual(record["observed"][key], value)
                self.assertIn(key, record["mismatches"])
                self.assertEqual(
                    record["expected"],
                    {"membership": True, "active": 1, "assigned": 1, "pids": [123]},
                )
                self.assertFalse(record["native_qualified"])

    def test_all_failed_components_are_recorded_together(self):
        raw = limits()
        raw.update(membership=False, active=2, assigned=2, pids=[444, 555])
        with self.assertRaises(AdmissionRefusal) as caught:
            nc.validate_limits(raw)
        self.assertEqual(
            set(caught.exception.numeric["mismatches"]),
            {"membership", "active", "assigned", "pids"},
        )

    def test_historical_totals_are_not_active_membership_or_a_gate(self):
        raw = limits()
        raw.update(total_processes=17, terminated=16)
        nc.validate_limits(raw)
        record = run_fake(JobKernel(before_total=2, after_total=8))
        self.assertEqual(record["mismatches"], [])
        self.assertEqual(record["accounting"]["before_total"], 2)
        self.assertEqual(record["accounting"]["after_total"], 8)
        self.assertTrue(record["accounting"]["active_stable"])

    def test_worker_default_query_origin_and_dual_pid_are_explicit(self):
        record = run_fake(JobKernel(win_pid=123))
        self.assertEqual(record["origin"], "worker-default-null")
        self.assertEqual(record["observed"]["python_or_created_pid"], 123)
        self.assertEqual(record["observed"]["win32_pid"], 123)
        self.assertEqual(record["observed"]["pids"], [123])
        self.assertEqual(record["context"]["pid_query_return_length"], 16)
        self.assertEqual(record["context"]["pid_query_attempts"], 1)
        self.assertEqual(
            record["context"]["window_ns"],
            record["context"]["ended_ns"] - record["context"]["started_ns"],
        )
        self.assertFalse(record["native_qualified"])

    def test_python_win32_pid_disagreement_refuses_before_dispatch(self):
        with self.assertRaises(AdmissionRefusal) as caught:
            run_fake(JobKernel(win_pid=456))
        record = caught.exception.numeric
        self.assertIn("Python/Win32 worker PID mismatch", str(caught.exception))
        self.assertEqual(record["observed"]["python_or_created_pid"], 123)
        self.assertEqual(record["observed"]["win32_pid"], 456)
        self.assertFalse(record["observed"]["python_win32_pid_equal"])

    def test_worker_failure_retains_full_composite_numbers_and_after_snapshot(self):
        with self.assertRaises(AdmissionRefusal) as caught:
            run_fake(JobKernel(active=2, after_active=2, assigned=2, ids=(123, 999)))
        record = caught.exception.numeric
        self.assertEqual(record["observed"]["active"], 2)
        self.assertEqual(record["observed"]["assigned"], 2)
        self.assertEqual(record["observed"]["pids"], [123, 999])
        self.assertEqual(record["accounting"]["after_active"], 2)
        self.assertEqual(record["context"]["pid_query_attempts"], 2)
        self.assertEqual(set(record["mismatches"]), {"active", "assigned", "pids"})

    def test_later_good_snapshot_never_excuses_initial_failure(self):
        with self.assertRaises(AdmissionRefusal) as caught:
            run_fake(JobKernel(active=2, after_active=1))
        record = caught.exception.numeric
        self.assertEqual(record["observed"]["active"], 2)
        self.assertEqual(record["accounting"]["after_active"], 1)
        self.assertFalse(record["accounting"]["active_stable"])
        self.assertIn("active", record["mismatches"])

    def test_later_changed_active_snapshot_refuses_without_wait_or_retry(self):
        kernel = JobKernel(active=1, after_active=2)
        with self.assertRaises(AdmissionRefusal) as caught:
            run_fake(kernel)
        self.assertIn("active accounting changed", str(caught.exception))
        self.assertEqual(caught.exception.numeric["accounting"]["after_active"], 2)
        self.assertEqual(kernel.accounting_calls, 2)
        self.assertEqual(kernel.calls.count(3), 1)

    def test_followup_query_failure_never_hides_first_guard_failure(self):
        with self.assertRaises(AdmissionRefusal) as caught:
            run_fake(JobKernel(active=2, fail_after=True))
        self.assertIn("complete child", str(caught.exception))
        self.assertEqual(caught.exception.numeric["observed"]["active"], 2)
        self.assertEqual(caught.exception.numeric["context"]["query_error_code"], 5)
        self.assertIsNone(caught.exception.numeric["accounting"]["after_active"])

    def test_followup_query_failure_cannot_advance_an_initially_good_worker(self):
        with self.assertRaises(AdmissionRefusal) as caught:
            run_fake(JobKernel(fail_after=True))
        self.assertEqual(caught.exception.numeric["mismatches"], [])
        self.assertEqual(caught.exception.numeric["context"]["query_error_code"], 5)

    def test_initial_api_failure_is_partial_not_fabricated_mismatch(self):
        for kind in (9, 15, 1, 3):
            with self.subTest(kind=kind):
                kernel = JobKernel(fail_kind=kind)
                with self.assertRaises(AdmissionRefusal) as caught:
                    run_fake(kernel)
                record = caught.exception.numeric
                self.assertEqual(record["context"]["query_error_code"], 87)
                self.assertEqual(record["context"]["last_query_kind"], kind)
                self.assertIn("pids", record["unavailable"])
                self.assertFalse(record["observed"]["pids_complete"])
                if kind == 3:
                    self.assertEqual(record["observed"]["active"], 1)
                else:
                    self.assertIsNone(record["observed"]["active"])

    def test_pid_query_cap_and_partial_counts_are_not_relaxed(self):
        with self.assertRaises(AdmissionRefusal) as caught:
            run_fake(JobKernel(active=33, assigned=33, ids=()))
        record = caught.exception.numeric
        self.assertEqual(record["observed"]["active"], 33)
        self.assertEqual(record["context"]["pid_query_assigned"], 33)
        self.assertEqual(record["context"]["pid_query_returned"], 0)
        self.assertIsNone(record["observed"]["pids"])

    def test_max_pid_record_fits_first_captured_segment(self):
        raw = dict.fromkeys(
            ("memory", "process_memory", "flags", "affinity", "child_affinity"),
            (1 << 64) - 1,
        )
        raw.update(
            membership=False,
            active=(1 << 32) - 1,
            assigned=(1 << 32) - 1,
            pid=(1 << 32) - 1,
            pids=[(1 << 32) - 1] * 32,
            total_processes=(1 << 32) - 1,
            terminated=(1 << 32) - 1,
            cpu_flags=(1 << 32) - 1,
            cpu_rate=(1 << 32) - 1,
            expected_cpu_rate=(1 << 32) - 1,
            active_limit=(1 << 32) - 1,
        )
        context = dict.fromkeys(
            (
                "window_ns",
                "membership_raw",
                "query_error_code",
                "last_query_kind",
                "last_query_bytes",
                "pid_query_capacity",
                "pid_query_return_length",
                "pid_query_attempts",
            ),
            (1 << 64) - 1,
        )
        context.update(
            started_ns=(1 << 64) - 1,
            ended_ns=(1 << 64) - 1,
            membership_raw=(1 << 31) - 1,
            query_error_code=(1 << 32) - 1,
            last_query_kind=(1 << 32) - 1,
            last_query_bytes=(1 << 32) - 1,
            pid_query_capacity=64,
            pid_query_return_length=(1 << 32) - 1,
            pid_query_attempts=3,
            pid_query_assigned=(1 << 32) - 1,
            pid_query_returned=(1 << 32) - 1,
            pid_query_ok=1,
            win32_pid=(1 << 32) - 1,
            after_active=(1 << 32) - 1,
            after_total=(1 << 32) - 1,
            after_terminated=(1 << 32) - 1,
        )
        error = AdmissionRefusal(
            "synthetic composite mismatch",
            raw,
            origin="worker-default-null",
            context=context,
        )
        stream = io.StringIO()
        emit_error(error, "bounded-job-admission", stream)
        first = stream.getvalue().splitlines()[0]
        self.assertLessEqual(len(first.encode()), ADMISSION_LINE_CAP)
        captured = BoundedStreams()
        captured.accept("stderr", stream.getvalue().replace("\n", "\r\n").encode())
        text = captured.record()["streams"]["stderr"]["text"]
        record = json.loads(text.splitlines()[0])
        self.assertEqual(len(record["observed"]["pids"]), 32)
        self.assertEqual(record["expected"]["pids"], [(1 << 32) - 1])
        self.assertLessEqual(len(text.encode()), MESSAGE_CAP)
        self.assertEqual(
            exception_record(error, "bounded-job-admission")["job_admission"],
            error.numeric,
        )

    def test_untrusted_non_numeric_values_are_not_serialized(self):
        raw = {
            "pid": "Bearer SECRET",
            "membership": "ghp_private",
            "active": True,
            "assigned": 1.5,
            "pids": ["password", object()],
            "memory": "/private/paper",
        }
        record = admission_record(
            raw, origin="Bearer SECRET", context={"win32_pid": "SECRET"}
        )
        line = admission_line(record)
        for value in ("Bearer", "SECRET", "ghp_private", "password", "/private"):
            self.assertNotIn(value, line)
        self.assertEqual(record["origin"], "unspecified")
        self.assertIsNone(record["observed"]["active"])
        self.assertFalse(record["observed"]["pids_complete"])

    def test_numeric_widths_match_bool_dword_size_t_contract(self):
        record = admission_record(
            limits(),
            origin="worker-default-null",
            context={
                "membership_raw": -1,
                "win32_pid": 1 << 32,
                "pid_query_ok": 2,
                "window_ns": 1 << 64,
            },
        )
        self.assertEqual(record["context"]["membership_raw"], -1)
        self.assertIsNone(record["observed"]["win32_pid"])
        self.assertIsNone(record["context"]["pid_query_ok"])
        self.assertIsNone(record["context"]["window_ns"])

    def test_parent_error_preserves_numbers_and_does_not_resume(self):
        backend = MemoryController()
        raw = limits()
        raw["pids"] = [444]
        backend.assign_and_readback = lambda: raw
        result = nc.execute_controller(backend)
        self.assertFalse(result["passed"])
        self.assertEqual(result["job_admission"]["observed"]["pids"], [444])
        self.assertNotIn("resume", backend.events)
        self.assertTrue(result["terminal"])

    def test_parent_timing_orders_creation_assignment_and_single_resume(self):
        backend = MemoryController()
        result = nc.execute_controller(backend)
        times = result["startup_timing_ns"]
        order = [
            "job_created",
            "child_created_suspended",
            "assignment_and_limits_validated",
            "resume_returned",
            "primary_signaled",
        ]
        self.assertEqual(sorted(times.values()), [times[key] for key in order])
        self.assertEqual(backend.events.count("resume"), 1)

    def test_resume_previous_count_is_recorded_and_not_retried(self):
        for previous in (1, 0, 2, 0xFFFFFFFF):
            with self.subTest(previous=previous):
                backend = nc.NativeJobBackend.__new__(nc.NativeJobBackend)
                backend.launch_record = {}
                backend.process = nc.ProcessInfo(10, 11, 123, 124)
                calls = []
                backend.kernel = SimpleNamespace(
                    ResumeThread=lambda handle, calls=calls, previous=previous: (
                        calls.append(handle) or previous
                    )
                )
                if previous == 1:
                    backend.resume()
                else:
                    with self.assertRaises(Refusal):
                        backend.resume()
                self.assertEqual(
                    backend.launch_record["resume_previous_suspend_count"], previous
                )
                with self.assertRaises(Refusal):
                    backend.resume()
                self.assertEqual(calls, [11])

    def test_worker_can_begin_between_resume_call_and_return_marker(self):
        backend = nc.NativeJobBackend.__new__(nc.NativeJobBackend)
        backend.launch_record = {}
        backend.process = nc.ProcessInfo(10, 11, 123, 124)
        child = []

        def resume(_handle):
            child.append(nc.time.monotonic_ns())
            return 1

        backend.kernel = SimpleNamespace(ResumeThread=resume)
        with patch.object(nc.time, "monotonic_ns", side_effect=[100, 150, 200]):
            backend.resume()
        self.assertLess(backend.launch_record["resume_call_begin_ns"], child[0])
        self.assertLess(child[0], backend.launch_record["resume_call_return_ns"])

    def test_worker_reporting_keeps_numeric_prefix_if_owned_receipt_fails(self):
        raw = limits()
        raw["assigned"] = 2
        error = AdmissionRefusal(
            "synthetic mismatch", raw, origin="worker-default-null"
        )
        stream = io.StringIO()
        with (
            patch.object(nw, "main", side_effect=error),
            patch.object(nw.sys, "__stderr__", stream),
            patch.object(
                nw,
                "write_entry_error",
                side_effect=OSError("synthetic receipt failure"),
            ),
        ):
            self.assertEqual(nw.guarded_main(), 1)
        self.assertEqual(
            json.loads(stream.getvalue().splitlines()[0])["observed"]["assigned"], 2
        )

    def test_reporting_failure_keeps_successful_readback_in_owned_error_receipt(self):
        record = admission_record(limits(), origin="worker-default-null")

        def report_failure(entry):
            entry["phase"] = "job-admission-diagnostic-report"
            entry["job_admission"] = record
            raise OSError("synthetic stderr failure")

        with (
            patch.object(nw, "main", side_effect=report_failure),
            patch.object(nw, "emit_error", side_effect=OSError("broken stderr")),
            patch.object(nw, "write_entry_error") as write,
        ):
            self.assertEqual(nw.guarded_main(), 1)
        self.assertEqual(write.call_args.args[1]["job_admission"], record)
        self.assertEqual(
            write.call_args.args[1]["phase"], "job-admission-diagnostic-report"
        )

    def test_accounting_and_pid_array_offsets_match_the_amd64_contract(self):
        self.assertEqual(ctypes.sizeof(nc.Accounting), 48)
        self.assertEqual(
            [
                getattr(nc.Accounting, name).offset
                for name in ("page_faults", "total_processes", "active", "terminated")
            ],
            [32, 36, 40, 44],
        )
        self.assertEqual(ctypes.sizeof(ctypes.c_size_t), 8)
        kernel = JobKernel(assigned=2, ids=(123, 456))
        backend = nc.NativeJobBackend.__new__(nc.NativeJobBackend)
        backend.kernel = kernel
        backend.job = None
        with patch.object(
            ctypes, "get_last_error", side_effect=lambda: kernel.error, create=True
        ):
            self.assertEqual(backend.pids(), (2, [123, 456]))
        self.assertEqual(backend.last_pid_query["return_length"], 24)
        self.assertEqual(backend.pid_query_attempts, 2)


if __name__ == "__main__":
    unittest.main()
