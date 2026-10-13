"""Bounded diagnostic child custody/controller. No production adapter activation."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).absolute().parent))

from build_manifest import plain_path, read_plain
from diagnostic_core import (
    ARTIFACT_CAP,
    FILE_CAP,
    MAX_FILES,
    package_bytes,
    record_bytes,
    rename_rows,
    validate_manifest,
)
from staged_replacement import Refusal
from worker_diagnostics import (
    AdmissionRefusal,
    BoundedStreams,
    admission_record,
    safe_text,
)

U32 = ctypes.c_uint32
I32 = ctypes.c_int32
U64 = ctypes.c_uint64
HANDLE = ctypes.c_void_p
VOID = ctypes.c_void_p
MEMORY = 536870912
FLAGS = 0x8 | 0x10 | 0x100 | 0x200 | 0x400 | 0x2000
CONTROLLER_SECONDS = 300
WORKER_SECONDS = 290
CLEANUP_SECONDS = 10


class BasicLimit(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", U32),
        ("minimum_ws", ctypes.c_size_t),
        ("maximum_ws", ctypes.c_size_t),
        ("active_limit", U32),
        ("affinity", ctypes.c_size_t),
        ("priority", U32),
        ("scheduling", U32),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        (name, U64)
        for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")
    ]


class ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimit),
        ("io", IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process", ctypes.c_size_t),
        ("peak_job", ctypes.c_size_t),
    ]


class CpuRate(ctypes.Structure):
    _fields_ = [("flags", U32), ("rate", U32)]


class Accounting(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int64)
        for name in ("total_user", "total_kernel", "period_user", "period_kernel")
    ] + [
        (name, U32)
        for name in ("page_faults", "total_processes", "active", "terminated")
    ]


class Startup(ctypes.Structure):
    _fields_ = (
        [
            ("cb", U32),
            ("reserved", ctypes.c_wchar_p),
            ("desktop", ctypes.c_wchar_p),
            ("title", ctypes.c_wchar_p),
        ]
        + [
            (name, U32)
            for name in (
                "x",
                "y",
                "width",
                "height",
                "chars_x",
                "chars_y",
                "fill",
                "flags",
            )
        ]
        + [
            ("show", ctypes.c_uint16),
            ("reserved_size", ctypes.c_uint16),
            ("reserved_bytes", VOID),
            ("stdin", HANDLE),
            ("stdout", HANDLE),
            ("stderr", HANDLE),
        ]
    )


class ProcessInfo(ctypes.Structure):
    _fields_ = [("process", HANDLE), ("thread", HANDLE), ("pid", U32), ("tid", U32)]


class SecurityAttributes(ctypes.Structure):
    _fields_ = [("size", U32), ("descriptor", VOID), ("inherit", I32)]


class StartupEx(ctypes.Structure):
    _fields_ = [("startup", Startup), ("attributes", VOID)]


def validate_limits(limits, *, origin="controller-explicit", context=None):
    if (
        limits.get("memory") != MEMORY
        or limits.get("process_memory") != MEMORY
        or limits.get("active_limit") != 1
    ):
        raise AdmissionRefusal(
            "512MiB/one-process limit readback mismatch",
            limits,
            origin=origin,
            context=context,
        )
    affinity = limits.get("affinity", 0)
    if (
        not affinity
        or affinity & (affinity - 1)
        or limits.get("child_affinity") != affinity
    ):
        raise AdmissionRefusal(
            "one-logical-CPU affinity readback mismatch",
            limits,
            origin=origin,
            context=context,
        )
    if limits.get("flags", 0) & FLAGS != FLAGS or limits.get("flags", 0) & (
        0x800 | 0x1000
    ):
        raise AdmissionRefusal(
            "JobObject flags/breakaway readback mismatch",
            limits,
            origin=origin,
            context=context,
        )
    if (
        limits.get("cpu_flags") != 5
        or not 1 <= limits.get("cpu_rate", 0) <= 10000
        or limits.get("cpu_rate") != limits.get("expected_cpu_rate")
    ):
        raise AdmissionRefusal(
            "CPU hard-cap readback mismatch", limits, origin=origin, context=context
        )
    if (
        not limits.get("membership")
        or limits.get("active") != 1
        or limits.get("assigned") != 1
        or limits.get("pids") != [limits.get("pid")]
    ):
        raise AdmissionRefusal(
            "complete child membership/accounting/PID binding mismatch",
            limits,
            origin=origin,
            context=context,
        )


def execute_controller(backend):
    started = getattr(backend, "budget_start", backend.clock())
    result = {
        "schema": "millefeuille.ten-rename-controller/v1",
        "terminal": False,
        "native_execution": bool(backend.native_execution),
        "owned_operations_in_flight": 0,
        "worker_qualified": False,
        "application_candidate_qualified": False,
        "production_windows_writes_enabled": False,
        "controller_seconds": CONTROLLER_SECONDS,
        "worker_seconds": WORKER_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS,
        "worker_memory_bytes": MEMORY,
        "worker_active_process_limit": 1,
        "worker_logical_cpus": 1,
    }
    try:
        result["startup_timing_ns"] = {}
        backend.create_job()
        result["startup_timing_ns"]["job_created"] = time.monotonic_ns()
        backend.create_suspended()
        result["startup_timing_ns"]["child_created_suspended"] = time.monotonic_ns()
        result["owned_operations_in_flight"] = 1
        limits = backend.assign_and_readback()
        validate_limits(limits)
        result["limits_readback"] = limits
        result["startup_timing_ns"]["assignment_and_limits_validated"] = (
            time.monotonic_ns()
        )
        backend.resume()
        result["startup_timing_ns"]["resume_returned"] = time.monotonic_ns()
        pump = getattr(backend, "drain_worker_output", None)
        while not backend.wait_primary(100):
            if pump is not None:
                pump()
            if backend.clock() - started >= WORKER_SECONDS:
                raise Refusal("worker deadline reached; no rerun")
            if backend.owned_bytes() > ARTIFACT_CAP:
                raise Refusal("owned scratch byte cap exceeded")
        result["startup_timing_ns"]["primary_signaled"] = time.monotonic_ns()
        if pump is not None:
            pump()
        worker = backend.worker_result()
        result["worker_result"] = worker
        if (
            worker.get("native_execution") is not bool(backend.native_execution)
            or worker.get("expected_head") != backend.expected_head
            or worker.get("source_manifest_sha256") != backend.manifest_sha256
            or worker.get("sdk_sha256") != backend.sdk_sha256
            or worker.get("live_pending_request_buffers") != 0
        ):
            raise Refusal(
                "worker source/SDK/head/native-execution or pending custody mismatch"
            )
        cases = worker.get("cases", [])
        if (
            len(cases) != 10
            or {case.get("id") for case in cases}
            != {row["id"] for row in rename_rows()}
            or not all(case.get("passed") is True for case in cases)
        ):
            raise Refusal("worker receipt does not bind all exact ten mandatory rows")
        if (
            worker.get("pid") != backend.pid
            or worker.get("tests_run") != 10
            or worker.get("passed") != 10
            or worker.get("failed") != 0
            or worker.get("skips") != 0
            or worker.get("hardlink_probe_cases") != 0
            or worker.get("owned_file_handles_remaining") != 0
            or backend.exit_code() != 0
        ):
            raise Refusal(
                "exact ten-case native worker result/exit/identity requirement failed"
            )
        result["worker_qualified"] = True
    except BaseException as error:  # noqa: BLE001 - All failures preserve owned process custody.
        result["error"] = {"type": type(error).__name__, "message": safe_text(error)}
        if isinstance(error, AdmissionRefusal):
            result["job_admission"] = error.numeric
    finally:
        remaining = max(
            0, min(CLEANUP_SECONDS, CONTROLLER_SECONDS - (backend.clock() - started))
        )
        try:
            cleanup = backend.cleanup(remaining)
            result["cleanup"] = cleanup
            terminal = (
                cleanup.get("primary_signaled") is True
                and cleanup.get("active") == cleanup.get("assigned") == 0
                and cleanup.get("pids") == []
                and cleanup.get("closes")
                == {"process": True, "thread": True, "job": True}
            )
            # Pre-child admission still requires the owned job to close.
            if not backend.pid:
                terminal = (
                    cleanup.get("no_owned_process") is True
                    and cleanup.get("active") == cleanup.get("assigned") == 0
                    and cleanup.get("pids") == []
                    and cleanup.get("job_closed") is True
                )
            terminal = terminal and cleanup.get("stdio_closed", True) is True
            result["terminal"] = terminal
            result["owned_operations_in_flight"] = (
                0
                if terminal
                else 1
                if backend.pid
                or backend.has_job
                or backend.has_process_handles
                or getattr(backend, "has_stdio_handles", False)
                or getattr(backend, "attribute_delete_uncertain", False)
                else 0
            )
        except BaseException as error:  # noqa: BLE001 - Unknown cleanup is never reported terminal.
            result["cleanup_error"] = {
                "type": type(error).__name__,
                "message": safe_text(error),
            }
            result["owned_operations_in_flight"] = (
                1
                if backend.pid
                or backend.has_job
                or backend.has_process_handles
                or getattr(backend, "has_stdio_handles", False)
                or getattr(backend, "attribute_delete_uncertain", False)
                else 0
            )
        diagnostics = getattr(backend, "diagnostic_record", None)
        if diagnostics is not None:
            try:
                captured = diagnostics()
                result["worker_startup_diagnostics"] = captured
                if captured.get("overflow") or captured.get("capture_error"):
                    result["worker_qualified"] = False
            except BaseException as error:  # noqa: BLE001 - Reporting cannot skip cleanup.
                result["diagnostic_capture_error"] = {"type": type(error).__name__}
                result["worker_qualified"] = False
        if "worker_result" not in result:
            result["controller_observed_worker_failure"] = {
                "schema": "millefeuille.controller-observed-worker-failure/v1",
                "receipt_origin": "controller-observation-not-worker-receipt",
                "worker_result_present": False,
                "individual_case_outcomes": "not-established",
                "exit_code": result.get("cleanup", {}).get("exit_code"),
                "process_cleanup_terminal": result["terminal"],
                "native_qualified": False,
            }
    result["elapsed_seconds"] = backend.clock() - started
    if result["elapsed_seconds"] > CONTROLLER_SECONDS:
        result["deadline_exceeded"] = True
        result["worker_qualified"] = False
    result["passed"] = (
        result["worker_qualified"]
        and result["terminal"]
        and not result.get("deadline_exceeded")
    )
    return result


class NativeJobBackend:
    native_execution = True

    def __init__(self, root, argv, *, source_root):
        if os.name != "nt" or sys.platform != "win32" or sys.maxsize <= 2**32:
            raise Refusal("requires native Windows AMD64")
        self.root, self.argv, self.source_root = root, argv, source_root
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.job, self.process, self.assigned = None, ProcessInfo(), False
        self.samples = []
        self.stdio = {}
        self.failed_stdio_closes = set()
        self.attribute_pointer = None
        self.attribute_delete_uncertain = False
        self.capture_error = None
        self.capture = BoundedStreams()
        self.launch_record = {}
        definitions = (
            ("GetCurrentProcess", HANDLE, []),
            ("GetProcessId", U32, [HANDLE]),
            ("GetProcessAffinityMask", I32, [HANDLE, VOID, VOID]),
            ("GetActiveProcessorCount", U32, [ctypes.c_uint16]),
            ("CreateJobObjectW", HANDLE, [VOID, ctypes.c_wchar_p]),
            ("SetInformationJobObject", I32, [HANDLE, U32, VOID, U32]),
            ("QueryInformationJobObject", I32, [HANDLE, U32, VOID, U32, VOID]),
            (
                "CreateProcessW",
                I32,
                [
                    ctypes.c_wchar_p,
                    ctypes.c_wchar_p,
                    VOID,
                    VOID,
                    I32,
                    U32,
                    VOID,
                    ctypes.c_wchar_p,
                    VOID,
                    VOID,
                ],
            ),
            ("AssignProcessToJobObject", I32, [HANDLE, HANDLE]),
            ("IsProcessInJob", I32, [HANDLE, HANDLE, VOID]),
            ("ResumeThread", U32, [HANDLE]),
            ("WaitForSingleObject", U32, [HANDLE, U32]),
            ("GetExitCodeProcess", I32, [HANDLE, VOID]),
            ("TerminateJobObject", I32, [HANDLE, U32]),
            ("TerminateProcess", I32, [HANDLE, U32]),
            ("CloseHandle", I32, [HANDLE]),
            ("CreatePipe", I32, [VOID, VOID, VOID, U32]),
            ("SetHandleInformation", I32, [HANDLE, U32, U32]),
            ("PeekNamedPipe", I32, [HANDLE, VOID, U32, VOID, VOID, VOID]),
            ("ReadFile", I32, [HANDLE, VOID, U32, VOID, VOID]),
            (
                "CreateFileW",
                HANDLE,
                [ctypes.c_wchar_p, U32, U32, VOID, U32, U32, HANDLE],
            ),
            ("InitializeProcThreadAttributeList", I32, [VOID, U32, U32, VOID]),
            (
                "UpdateProcThreadAttribute",
                I32,
                [VOID, U32, ctypes.c_size_t, VOID, ctypes.c_size_t, VOID, VOID],
            ),
            ("DeleteProcThreadAttributeList", None, [VOID]),
            (
                "GetVolumeInformationW",
                I32,
                [ctypes.c_wchar_p, VOID, U32, VOID, VOID, VOID, ctypes.c_wchar_p, U32],
            ),
        )
        for name, restype, arguments in definitions:
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = restype, arguments

    @property
    def pid(self):
        return self.process.pid

    @property
    def has_job(self):
        return bool(self.job)

    @property
    def has_process_handles(self):
        return bool(self.process.process or self.process.thread)

    @property
    def has_stdio_handles(self):
        return bool(self.stdio or self.attribute_pointer)

    @staticmethod
    def clock():
        return time.monotonic()

    @staticmethod
    def checked(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def query(self, kind, structure):
        value = structure()
        self.last_query = {"kind": kind, "bytes": ctypes.sizeof(value), "ok": None}
        result = self.kernel.QueryInformationJobObject(
            self.job, kind, ctypes.byref(value), ctypes.sizeof(value), None
        )
        self.last_query.update(
            ok=bool(result), error=0 if result else ctypes.get_last_error()
        )
        self.checked(result)
        return value

    def pids(self):
        capacity = 1
        self.pid_query_attempts = 0
        self.last_pid_query = {}
        for _attempt in range(3):
            self.pid_query_attempts += 1
            data = ctypes.create_string_buffer(
                8 + capacity * ctypes.sizeof(ctypes.c_size_t)
            )
            used = U32()
            value = self.kernel.QueryInformationJobObject(
                self.job, 3, data, len(data), ctypes.byref(used)
            )
            error = 0 if value else ctypes.get_last_error()
            assigned, count = (
                U32.from_buffer(data, 0).value,
                U32.from_buffer(data, 4).value,
            )
            self.last_query = {
                "kind": 3,
                "bytes": len(data),
                "ok": bool(value),
                "error": error,
            }
            self.last_pid_query = {
                "capacity": capacity,
                "return_length": used.value,
                "assigned": assigned,
                "returned": count,
                "ok": int(bool(value)),
                "error": error,
            }
            if assigned > 32 or count > assigned:
                raise Refusal("unbounded or inconsistent owned-job PID query")
            if value and count == assigned and count <= capacity:
                ids = [
                    ctypes.c_size_t.from_buffer(
                        data, 8 + i * ctypes.sizeof(ctypes.c_size_t)
                    ).value
                    for i in range(count)
                ]
                return assigned, ids
            if not value and error != 234:
                raise ctypes.WinError(error)
            capacity = max(capacity * 2, assigned, 1)
        raise Refusal("complete owned-job PID query not established")

    def sample(self):
        value = self.query(1, Accounting)
        self.last_accounting = {
            "active": value.active,
            "total_processes": value.total_processes,
            "terminated": value.terminated,
        }
        assigned, ids = self.pids()
        result = {
            "active": value.active,
            "assigned": assigned,
            "pids": ids,
            "total_processes": value.total_processes,
            "terminated": value.terminated,
        }
        self.samples.append(result)
        return result

    def create_job(self):
        if (
            ctypes.sizeof(Accounting) != 48
            or ctypes.sizeof(ExtendedLimit) != 144
            or ctypes.sizeof(Startup) != 104
            or ctypes.sizeof(StartupEx) != 112
            or ctypes.sizeof(SecurityAttributes) != 24
        ):
            raise Refusal("native JobObject/process ABI mismatch")
        volume = ctypes.create_unicode_buffer(32)
        self.checked(
            self.kernel.GetVolumeInformationW(
                self.root.anchor, None, 0, None, None, None, volume, len(volume)
            )
        )
        if volume.value.upper() != "NTFS":
            raise Refusal("owned scratch requires native NTFS")
        process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
        self.checked(
            self.kernel.GetProcessAffinityMask(
                self.kernel.GetCurrentProcess(),
                ctypes.byref(process_mask),
                ctypes.byref(system_mask),
            )
        )
        if not process_mask.value or not system_mask.value:
            raise Refusal("CPU affinity admission failed")
        self.affinity = process_mask.value & -process_mask.value
        processors = self.kernel.GetActiveProcessorCount(0xFFFF)
        if not 1 <= processors <= 10000:
            raise Refusal("logical CPU count outside hard-cap domain")
        self.rate = 10000 // processors
        self.job = self.checked(self.kernel.CreateJobObjectW(None, None))
        limit = ExtendedLimit()
        limit.basic.flags, limit.basic.active_limit, limit.basic.affinity = (
            FLAGS,
            1,
            self.affinity,
        )
        limit.process_memory = limit.job_memory = MEMORY
        self.checked(
            self.kernel.SetInformationJobObject(
                self.job, 9, ctypes.byref(limit), ctypes.sizeof(limit)
            )
        )
        cpu = CpuRate(5, self.rate)
        self.checked(
            self.kernel.SetInformationJobObject(
                self.job, 15, ctypes.byref(cpu), ctypes.sizeof(cpu)
            )
        )

    def create_suspended(self):
        startup = StartupEx()
        startup.startup.cb = ctypes.sizeof(startup)
        self.prepare_stdio(startup)
        environment = {
            "SystemRoot": os.environ["SYSTEMROOT"],
            "WINDIR": os.environ["SYSTEMROOT"],
            "TEMP": str(self.root),
            "TMP": str(self.root),
            "PATH": str(Path(sys.executable).parent)
            + ";"
            + os.environ["SYSTEMROOT"]
            + "\\System32",
        }
        block = ctypes.create_unicode_buffer(
            "\0".join(key + "=" + value for key, value in sorted(environment.items()))
            + "\0\0"
        )
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(self.argv))
        self.launch_record = {
            "executable": sys.executable,
            "base_executable": getattr(sys, "_base_executable", None),
            "prefix": sys.prefix,
            "base_prefix": sys.base_prefix,
            "argv": self.argv,
            "raw_command_line": command.value,
            "environment": environment,
            "cwd": str(self.root),
            "inheritance": "explicit-stdin-stdout-stderr-handle-list-only",
            "startup_size": ctypes.sizeof(startup),
        }
        # ProcessInfo is retained even if a late call error reports failure.
        try:
            value = self.kernel.CreateProcessW(
                sys.executable,
                command,
                None,
                None,
                True,
                0x4 | 0x400 | 0x08000000 | 0x80000,
                ctypes.cast(block, VOID),
                str(self.root),
                ctypes.byref(startup),
                ctypes.byref(self.process),
            )
        finally:
            # Recover custody even if CreateProcessW itself reports a late error.
            try:
                if self.process.process and not self.process.pid:
                    self.process.pid = self.kernel.GetProcessId(self.process.process)
            finally:
                self.delete_startup_attributes()
                # Child writers alone permit EOF after an early interpreter exit.
                closed = self.close_stdio_names(
                    ("stdin", "stdout_write", "stderr_write")
                )
        if not closed or self.attribute_delete_uncertain:
            raise Refusal("startup parent-handle/attribute release uncertain")
        self.checked(value)
        self.launch_record["created_primary_pid"] = self.process.pid
        if self.process.pid == 0:
            raise Refusal("owned created PID/handle readback mismatch")
        observed_pid = self.kernel.GetProcessId(self.process.process)
        self.launch_record["created_primary_handle_pid"] = observed_pid
        self.launch_record["created_primary_tid"] = self.process.tid
        if observed_pid != self.process.pid:
            raise Refusal("owned created PID/handle readback mismatch")

    def prepare_stdio(self, startup):
        security = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, 1)
        for name in ("stdout", "stderr"):
            read, write = HANDLE(), HANDLE()
            value = self.kernel.CreatePipe(
                ctypes.byref(read), ctypes.byref(write), ctypes.byref(security), 4096
            )
            if read.value:
                self.stdio[name + "_read"] = read.value
            if write.value:
                self.stdio[name + "_write"] = write.value
            self.checked(value)
            self.checked(self.kernel.SetHandleInformation(read.value, 1, 0))
        null = self.kernel.CreateFileW(
            "NUL", 0x80000000, 3, ctypes.byref(security), 3, 0, None
        )
        if not null or null == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.stdio["stdin"] = null
        size = ctypes.c_size_t()
        self.kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if not 0 < size.value <= 65536:
            raise Refusal("bounded startup attribute-list size required")
        self.attribute_storage = ctypes.create_string_buffer(size.value)
        self.checked(
            self.kernel.InitializeProcThreadAttributeList(
                self.attribute_storage, 1, 0, ctypes.byref(size)
            )
        )
        startup.attributes = ctypes.cast(self.attribute_storage, VOID)
        self.attribute_pointer = startup.attributes
        self.inherited_stdio = (HANDLE * 3)(
            null, self.stdio["stdout_write"], self.stdio["stderr_write"]
        )
        self.checked(
            self.kernel.UpdateProcThreadAttribute(
                startup.attributes,
                0,
                0x20002,
                self.inherited_stdio,
                ctypes.sizeof(self.inherited_stdio),
                None,
                None,
            )
        )
        startup.startup.flags = 0x100
        startup.startup.stdin = null
        startup.startup.stdout = self.stdio["stdout_write"]
        startup.startup.stderr = self.stdio["stderr_write"]

    def close_stdio_names(self, names):
        # Attempt every known handle once; uncertain closes are never retried.
        for name in names:
            handle = self.stdio.get(name)
            if handle is None or name in self.failed_stdio_closes:
                continue
            try:
                self.checked(self.kernel.CloseHandle(handle))
            except BaseException:  # noqa: BLE001 - Preserve uncertainty while releasing other handles.
                self.failed_stdio_closes.add(name)
            else:
                del self.stdio[name]
        return not any(name in self.stdio for name in names)

    def delete_startup_attributes(self):
        pointer, self.attribute_pointer = self.attribute_pointer, None
        if pointer:
            try:
                self.kernel.DeleteProcThreadAttributeList(pointer)
            except BaseException:  # noqa: BLE001 - No retry after uncertain attribute release.
                self.attribute_delete_uncertain = True

    def finish_stdio(self):
        try:
            if not self.capture.overflow:
                self.drain_worker_output()
        except BaseException as error:  # noqa: BLE001 - Capture failure cannot strand known handles.
            self.capture_error = {
                "type": type(error).__name__,
                "message": safe_text(error),
            }
        self.delete_startup_attributes()
        return (
            self.close_stdio_names(tuple(self.stdio))
            and not self.attribute_delete_uncertain
        )

    def drain_worker_output(self):
        if self.capture.overflow:
            raise Refusal("capture overflow; no further pipe reads")
        for name in ("stdout", "stderr"):
            handle = self.stdio.get(name + "_read")
            if handle is None or self.capture.eof[name]:
                continue
            while True:
                available = U32()
                value = self.kernel.PeekNamedPipe(
                    handle, None, 0, None, ctypes.byref(available), None
                )
                if not value:
                    if ctypes.get_last_error() == 109:
                        self.capture.eof[name] = True
                        break
                    self.checked(value)
                if not available.value:
                    break
                count = min(4096, available.value, self.capture.allowance(name) + 1)
                block, used = ctypes.create_string_buffer(count), U32()
                self.checked(
                    self.kernel.ReadFile(handle, block, count, ctypes.byref(used), None)
                )
                if used.value > count:
                    raise Refusal("pipe read exceeded reserved diagnostic bytes")
                if not used.value:
                    break
                self.capture.accept(name, bytes(block.raw[: used.value]))

    def diagnostic_record(self):
        substitutions = (
            (self.root, "[owned-root]"),
            (self.source_root, "[source-root]"),
        )
        result = self.capture.record(substitutions)
        # These are only the fixed public interpreter/source/owned-root fields,
        # never the complete inherited host environment or private credentials.
        result["launch"] = self.launch_record
        result["capture_error"] = self.capture_error
        receipt = self.root / "worker-entry-error.json"
        if receipt.exists():
            result["worker_entry_error"] = json.loads(read_plain(receipt, FILE_CAP))
        return result

    def assign_and_readback(self):
        self.checked(
            self.kernel.AssignProcessToJobObject(self.job, self.process.process)
        )
        self.assigned = True
        member = I32()
        self.checked(
            self.kernel.IsProcessInJob(
                self.process.process, self.job, ctypes.byref(member)
            )
        )
        limit, cpu = self.query(9, ExtendedLimit), self.query(15, CpuRate)
        mask, system = ctypes.c_size_t(), ctypes.c_size_t()
        self.checked(
            self.kernel.GetProcessAffinityMask(
                self.process.process, ctypes.byref(mask), ctypes.byref(system)
            )
        )
        sample = self.sample()
        return {
            "memory": limit.job_memory,
            "process_memory": limit.process_memory,
            "active_limit": limit.basic.active_limit,
            "flags": limit.basic.flags,
            "affinity": limit.basic.affinity,
            "child_affinity": mask.value,
            "cpu_flags": cpu.flags,
            "cpu_rate": cpu.rate,
            "expected_cpu_rate": self.rate,
            "membership": bool(member.value),
            "pid": self.pid,
            **sample,
        }

    def resume(self):
        attempts = self.launch_record.get("resume_attempts", 0) + 1
        self.launch_record["resume_attempts"] = min(attempts, 2)
        if attempts != 1:
            raise Refusal("owned suspended child resume already attempted; no retry")
        self.launch_record["resume_call_begin_ns"] = time.monotonic_ns()
        previous = self.kernel.ResumeThread(self.process.thread)
        self.launch_record["resume_call_return_ns"] = time.monotonic_ns()
        self.launch_record["resume_previous_suspend_count"] = previous
        if previous != 1:
            raise Refusal("owned suspended child did not resume exactly once")

    def wait_primary(self, milliseconds):
        value = self.kernel.WaitForSingleObject(self.process.process, milliseconds)
        if value not in (0, 0x102):
            raise Refusal(f"owned process wait failed: {value}")
        return value == 0

    def exit_code(self):
        value = U32()
        self.checked(
            self.kernel.GetExitCodeProcess(self.process.process, ctypes.byref(value))
        )
        return value.value

    def worker_result(self):
        return json.loads(read_plain(self.root / "worker-result.json", FILE_CAP))

    def owned_bytes(self):
        # Monitor only no-follow metadata while the child may be writing receipts.
        total, files, directories = 0, 0, 0
        for path in self.root.rglob("*"):
            info = path.lstat()
            if path.is_dir():
                plain_path(path, directory=True)
                directories += 1
            else:
                plain_path(path)
                files += 1
                total += info.st_size
                if info.st_size > FILE_CAP:
                    raise Refusal("owned per-file byte cap")
            if files > MAX_FILES or directories > MAX_FILES:
                raise Refusal("owned evidence entry count cap")
        return total

    def cleanup(self, seconds):
        deadline = self.clock() + seconds
        closes = {"process": False, "thread": False, "job": False}
        if not self.pid:
            if self.has_process_handles:
                raise Refusal(
                    "returned process/thread handles lack a reconciled PID; "
                    "retain custody"
                )
            stdio_closed = self.finish_stdio()
            sample = (
                self.sample() if self.job else {"active": 0, "assigned": 0, "pids": []}
            )
            closed = not self.job or bool(self.kernel.CloseHandle(self.job))
            if closed:
                self.job = None
            return {
                "no_owned_process": True,
                "job_closed": closed,
                "stdio_closed": stdio_closed,
                **sample,
                "samples": list(self.samples),
            }
        signaled = self.wait_primary(0)
        if not self.assigned and not signaled:
            self.checked(self.kernel.TerminateProcess(self.process.process, 1))
        sample = self.sample() if self.job else {"active": 0, "assigned": 0, "pids": []}
        terminated_job = False
        if self.assigned and (
            not signaled or sample["active"] or sample["assigned"] or sample["pids"]
        ):
            self.checked(self.kernel.TerminateJobObject(self.job, 1))
            terminated_job = True
        while self.clock() < deadline:
            signaled = self.wait_primary(100)
            sample = self.sample() if self.job else sample
            if (
                signaled
                and sample["active"] == sample["assigned"] == 0
                and sample["pids"] == []
            ):
                break
        terminal = (
            signaled
            and sample["active"] == sample["assigned"] == 0
            and sample["pids"] == []
        )
        exit_code = self.exit_code() if signaled else None
        stdio_closed = self.finish_stdio() if terminal else False
        if terminal:
            for name, handle in (
                ("thread", self.process.thread),
                ("process", self.process.process),
                ("job", self.job),
            ):
                try:
                    closes[name] = (name == "job" and not handle) or bool(
                        self.kernel.CloseHandle(handle)
                    )
                except BaseException:  # noqa: BLE001 - Still attempt the other known terminal handles once.
                    closes[name] = False
            if closes["job"]:
                self.job = None
        return {
            "primary_pid": self.pid,
            "primary_signaled": signaled,
            "exit_code": exit_code,
            "termination_requested": terminated_job,
            "closes": closes,
            "stdio_closed": stdio_closed,
            **sample,
            "samples": list(self.samples),
        }


def require_worker_job():
    """Read-only diagnostics never waive the original bounded one-process check."""
    if os.name != "nt" or sys.platform != "win32" or sys.maxsize <= 2**32:
        raise Refusal("bounded worker requires native Windows AMD64")
    started = time.monotonic_ns()
    limits = {"pid": os.getpid()}
    context = {}
    backend = NativeJobBackend.__new__(NativeJobBackend)
    try:
        backend.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        backend.job, backend.samples = None, []
        for name, restype, args in (
            ("GetCurrentProcess", HANDLE, []),
            ("GetCurrentProcessId", U32, []),
            ("IsProcessInJob", I32, [HANDLE, HANDLE, VOID]),
            ("QueryInformationJobObject", I32, [HANDLE, U32, VOID, U32, VOID]),
            ("GetProcessAffinityMask", I32, [HANDLE, VOID, VOID]),
            ("GetActiveProcessorCount", U32, [ctypes.c_uint16]),
        ):
            function = getattr(backend.kernel, name)
            function.restype, function.argtypes = restype, args
        context["win32_pid"] = backend.kernel.GetCurrentProcessId()
        member = I32()
        backend.checked(
            backend.kernel.IsProcessInJob(
                backend.kernel.GetCurrentProcess(), None, ctypes.byref(member)
            )
        )
        context["membership_raw"] = member.value
        limits["membership"] = bool(member.value)
        limit, cpu = backend.query(9, ExtendedLimit), backend.query(15, CpuRate)
        mask, system = ctypes.c_size_t(), ctypes.c_size_t()
        backend.checked(
            backend.kernel.GetProcessAffinityMask(
                backend.kernel.GetCurrentProcess(),
                ctypes.byref(mask),
                ctypes.byref(system),
            )
        )
        processors = backend.kernel.GetActiveProcessorCount(0xFFFF)
        if not 1 <= processors <= 10000:
            raise Refusal("worker active CPU count outside cap domain")
        limits.update(
            {
                "memory": limit.job_memory,
                "process_memory": limit.process_memory,
                "active_limit": limit.basic.active_limit,
                "flags": limit.basic.flags,
                "affinity": limit.basic.affinity,
                "child_affinity": mask.value,
                "cpu_flags": cpu.flags,
                "cpu_rate": cpu.rate,
                "expected_cpu_rate": 10000 // processors,
            }
        )
        limits.update(backend.sample())
        problem = None
        try:
            validate_limits(limits, origin="worker-default-null", context=context)
        except AdmissionRefusal as error:
            problem = error
        if problem is None and context["win32_pid"] != limits["pid"]:
            problem = AdmissionRefusal(
                "Python/Win32 worker PID mismatch; no dispatch",
                limits,
                origin="worker-default-null",
                context=context,
            )
        # Accounting and PID-list reads are separate snapshots. A second bounded
        # accounting read exposes change; it never replaces or excuses the first.
        try:
            after = backend.query(1, Accounting)
            context.update(
                after_active=after.active,
                after_total=after.total_processes,
                after_terminated=after.terminated,
            )
            if problem is None and after.active != limits["active"]:
                problem = AdmissionRefusal(
                    "active accounting changed across PID-list snapshot; no dispatch",
                    limits,
                    origin="worker-default-null",
                    context=context,
                )
        except OSError as error:
            context["query_error_code"] = getattr(error, "winerror", None)
            if problem is None:
                problem = (
                    error  # An unavailable diagnostic read cannot advance dispatch.
                )
        if problem is not None:
            raise problem
    except Exception as error:  # noqa: BLE001 - Preserve complete/partial numeric readbacks on API or guard failure.
        if "active" not in limits:
            limits.update(getattr(backend, "last_accounting", {}))
        query, pid_query = (
            getattr(backend, "last_query", {}),
            getattr(backend, "last_pid_query", {}),
        )
        ended = time.monotonic_ns()
        context.update(
            started_ns=started,
            ended_ns=ended,
            window_ns=ended - started,
            last_query_kind=query.get("kind"),
            last_query_bytes=query.get("bytes"),
            query_error_code=context.get(
                "query_error_code", getattr(error, "winerror", None)
            ),
            pid_query_capacity=pid_query.get("capacity"),
            pid_query_return_length=pid_query.get("return_length"),
            pid_query_attempts=getattr(backend, "pid_query_attempts", None),
            pid_query_assigned=pid_query.get("assigned"),
            pid_query_returned=pid_query.get("returned"),
            pid_query_ok=pid_query.get("ok"),
        )
        if isinstance(error, AdmissionRefusal):
            error.numeric = admission_record(
                limits, origin="worker-default-null", context=context
            )
            raise
        raise AdmissionRefusal(
            safe_text(error), limits, origin="worker-default-null", context=context
        ) from error
    query, pid_query = backend.last_query, backend.last_pid_query
    ended = time.monotonic_ns()
    context.update(
        started_ns=started,
        ended_ns=ended,
        window_ns=ended - started,
        last_query_kind=query.get("kind"),
        last_query_bytes=query.get("bytes"),
        pid_query_capacity=pid_query.get("capacity"),
        pid_query_return_length=pid_query.get("return_length"),
        pid_query_attempts=backend.pid_query_attempts,
        pid_query_assigned=pid_query.get("assigned"),
        pid_query_returned=pid_query.get("returned"),
        pid_query_ok=pid_query.get("ok"),
    )
    return admission_record(limits, origin="worker-default-null", context=context)


def collect_owned(root):
    plain_path(root, directory=True)
    entries = {}
    for path in root.rglob("*"):
        info = path.lstat()
        if path.is_dir():
            plain_path(path, directory=True)
        else:
            if len(entries) >= MAX_FILES:
                raise Refusal("owned evidence file count cap")
            entries[path.relative_to(root).as_posix()] = read_plain(path, FILE_CAP)
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise Refusal("reparse evidence refused")
    if sum(len(data) for data in entries.values()) > ARTIFACT_CAP:
        raise Refusal("owned scratch exceeded 1MiB")
    return entries


def main():
    parser = argparse.ArgumentParser()
    for name in (
        "source-root",
        "scratch-parent",
        "sdk-dll",
        "source-manifest",
        "artifact",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    for name in (
        "source-manifest-sha256",
        "expected-head",
        "route-id",
        "owner-clearance-id",
    ):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if (
        os.name != "nt"
        or sys.platform != "win32"
        or sys.version_info[:2] != (3, 13)
        or sys.maxsize <= 2**32
    ):
        raise Refusal("native Windows AMD64/Python3.13 controller required")
    if not args.route_id.strip() or not args.owner_clearance_id.strip():
        raise Refusal("nonempty future publication custody bindings required")
    overall_started = time.monotonic()
    if (
        str(args.source_root).casefold()
        != str(Path(__file__).absolute().parent).casefold()
    ):
        raise Refusal("executed controller root must match pinned source root")
    plain_path(args.source_root, directory=True)
    plain_path(args.scratch_parent, directory=True)
    plain_path(args.artifact.parent, directory=True)
    if (
        args.artifact.parent != args.scratch_parent
        or args.artifact.exists()
        or args.artifact.suffix != ".zip"
    ):
        raise Refusal("fresh owned ZIP must be a direct child of scratch parent")
    raw = read_plain(args.source_manifest, FILE_CAP)
    if hashlib.sha256(raw).hexdigest() != args.source_manifest_sha256:
        raise Refusal("source manifest digest mismatch")
    manifest = json.loads(raw)
    if manifest["sdk"]["path"].casefold() != str(args.sdk_dll).casefold():
        raise Refusal("SDK DLL path mismatch")
    validate_manifest(
        manifest,
        expected_head=args.expected_head,
        source_reader=lambda relative: read_plain(
            args.source_root / relative, FILE_CAP
        ),
        sdk_reader=lambda: read_plain(args.sdk_dll, 524288),
    )
    root = Path(tempfile.mkdtemp(prefix="mf-win-rename-", dir=args.scratch_parent))
    info, nonce = root.lstat(), secrets.token_hex(32)
    marker = {
        "schema": "millefeuille.owned-native-diagnostic-root/v1",
        "nonce": nonce,
        "expected_head": args.expected_head,
        "root_identity": [info.st_dev, info.st_ino],
        "route_id": args.route_id,
        "owner_clearance_id": args.owner_clearance_id,
        "custody_label_is_not_human_authorization": True,
    }
    with (root / "owner-marker.json").open("xb") as output:
        output.write(record_bytes(marker))
    argv = [
        sys.executable,
        "-I",
        "-B",
        str(args.source_root / "native_worker.py"),
        "--authorize-owned-synthetic-native-diagnostics",
        "--synthetic-root",
        str(root),
        "--source-root",
        str(args.source_root),
        "--source-manifest",
        str(args.source_manifest),
        "--source-manifest-sha256",
        args.source_manifest_sha256,
        "--sdk-dll",
        str(args.sdk_dll),
        "--expected-head",
        args.expected_head,
        "--nonce",
        nonce,
    ]
    backend = NativeJobBackend(root, argv, source_root=args.source_root)
    backend.expected_head, backend.manifest_sha256, backend.sdk_sha256 = (
        args.expected_head,
        args.source_manifest_sha256,
        manifest["sdk"]["sha256"],
    )
    backend.budget_start = overall_started
    result = execute_controller(backend)
    result.update(
        {
            "evidence_root": str(root),
            "expected_head": args.expected_head,
            "route_id": args.route_id,
            "owner_clearance_id": args.owner_clearance_id,
            "custody_label_is_not_human_authorization": True,
            "artifact_retention_days": 7,
            "artifact_retrievals_authorized": 1,
            "source_manifest_sha256": args.source_manifest_sha256,
            "sdk_sha256": manifest["sdk"]["sha256"],
        }
    )
    with (root / "controller-result.json").open("xb") as output:
        output.write(record_bytes(result))
    if not result["terminal"]:
        # A live or uncertain writer is not safe to collect/package. Keep the
        # exact root and console custody receipt; do not pretend a ZIP is settled.
        result["artifact_blocked_by_pending_custody"] = True
        result["total_controller_seconds_including_packaging"] = (
            time.monotonic() - overall_started
        )
        print(json.dumps(result, sort_keys=True))
        return 1
    try:
        artifact = package_bytes(collect_owned(root))
        with args.artifact.open("xb") as output:
            output.write(artifact)
        result["artifact"] = {
            "path": str(args.artifact),
            "bytes": len(artifact),
            "sha256": hashlib.sha256(artifact).hexdigest(),
        }
    except Exception as error:  # noqa: BLE001 - No artifact success claim on unsafe/oversize evidence.
        result["artifact_error"] = str(error)
        result["passed"] = False
    result["total_controller_seconds_including_packaging"] = (
        time.monotonic() - overall_started
    )
    if result["total_controller_seconds_including_packaging"] >= CONTROLLER_SECONDS:
        result["passed"] = False
        result["deadline_exceeded_including_packaging"] = True
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
