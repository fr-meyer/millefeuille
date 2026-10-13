"""Bounded diagnostic child custody/controller. No production adapter activation."""

import argparse
import ctypes
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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


def validate_limits(limits):
    if (
        limits.get("memory") != MEMORY
        or limits.get("process_memory") != MEMORY
        or limits.get("active_limit") != 1
    ):
        raise Refusal("512MiB/one-process limit readback mismatch")
    affinity = limits.get("affinity", 0)
    if (
        not affinity
        or affinity & (affinity - 1)
        or limits.get("child_affinity") != affinity
    ):
        raise Refusal("one-logical-CPU affinity readback mismatch")
    if limits.get("flags", 0) & FLAGS != FLAGS or limits.get("flags", 0) & (
        0x800 | 0x1000
    ):
        raise Refusal("JobObject flags/breakaway readback mismatch")
    if (
        limits.get("cpu_flags") != 5
        or not 1 <= limits.get("cpu_rate", 0) <= 10000
        or limits.get("cpu_rate") != limits.get("expected_cpu_rate")
    ):
        raise Refusal("CPU hard-cap readback mismatch")
    if (
        not limits.get("membership")
        or limits.get("active") != 1
        or limits.get("assigned") != 1
        or limits.get("pids") != [limits.get("pid")]
    ):
        raise Refusal("complete child membership/accounting/PID binding mismatch")


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
        backend.create_job()
        backend.create_suspended()
        result["owned_operations_in_flight"] = 1
        limits = backend.assign_and_readback()
        validate_limits(limits)
        result["limits_readback"] = limits
        backend.resume()
        while not backend.wait_primary(100):
            if backend.clock() - started >= WORKER_SECONDS:
                raise Refusal("worker deadline reached; no rerun")
            if backend.owned_bytes() > ARTIFACT_CAP:
                raise Refusal("owned scratch byte cap exceeded")
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
    except Exception as error:  # noqa: BLE001 - All failures preserve owned process custody.
        result["error"] = {"type": type(error).__name__, "message": str(error)}
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
            # Pre-child admission failures have no owned process; still require owned job close.
            if not backend.pid:
                terminal = (
                    cleanup.get("no_owned_process") is True
                    and cleanup.get("active") == cleanup.get("assigned") == 0
                    and cleanup.get("pids") == []
                    and cleanup.get("job_closed") is True
                )
            result["terminal"] = terminal
            result["owned_operations_in_flight"] = (
                0
                if terminal
                else 1
                if backend.pid or backend.has_job or backend.has_process_handles
                else 0
            )
        except Exception as error:  # noqa: BLE001 - Unknown cleanup is never reported terminal.
            result["cleanup_error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            result["owned_operations_in_flight"] = (
                1
                if backend.pid or backend.has_job or backend.has_process_handles
                else 0
            )
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
        self.checked(
            self.kernel.QueryInformationJobObject(
                self.job, kind, ctypes.byref(value), ctypes.sizeof(value), None
            )
        )
        return value

    def pids(self):
        capacity = 1
        for _attempt in range(3):
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
        if ctypes.sizeof(ExtendedLimit) != 144 or ctypes.sizeof(Startup) != 104:
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
        startup = Startup()
        startup.cb = ctypes.sizeof(startup)
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
        # ProcessInfo is retained even if a late call error reports failure.
        value = self.kernel.CreateProcessW(
            sys.executable,
            command,
            None,
            None,
            False,
            0x4 | 0x400 | 0x08000000,
            ctypes.cast(block, VOID),
            str(self.root),
            ctypes.byref(startup),
            ctypes.byref(self.process),
        )
        # Reconcile any returned handle before trusting the BOOL; no retry.
        if self.process.process:
            recovered_pid = self.kernel.GetProcessId(self.process.process)
            if not self.process.pid and recovered_pid:
                self.process.pid = recovered_pid
        self.checked(value)
        if (
            self.process.pid == 0
            or self.kernel.GetProcessId(self.process.process) != self.process.pid
        ):
            raise Refusal("owned created PID/handle readback mismatch")

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
        if self.kernel.ResumeThread(self.process.thread) != 1:
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
                    "returned process/thread handles lack a reconciled PID; retain custody"
                )
            sample = (
                self.sample() if self.job else {"active": 0, "assigned": 0, "pids": []}
            )
            closed = not self.job or bool(self.kernel.CloseHandle(self.job))
            if closed:
                self.job = None
            return {
                "no_owned_process": True,
                "job_closed": closed,
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
        if terminal:
            closes["thread"] = bool(self.kernel.CloseHandle(self.process.thread))
            closes["process"] = bool(self.kernel.CloseHandle(self.process.process))
            closes["job"] = not self.job or bool(self.kernel.CloseHandle(self.job))
            if closes["job"]:
                self.job = None
        return {
            "primary_pid": self.pid,
            "primary_signaled": signaled,
            "exit_code": exit_code,
            "termination_requested": terminated_job,
            "closes": closes,
            **sample,
            "samples": list(self.samples),
        }


def require_worker_job():
    """Refuse raw worker invocations outside a matching bounded one-process job."""
    if os.name != "nt" or sys.platform != "win32" or sys.maxsize <= 2**32:
        raise Refusal("bounded worker requires native Windows AMD64")
    backend = NativeJobBackend.__new__(NativeJobBackend)
    backend.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    backend.job, backend.samples = None, []
    for name, restype, args in (
        ("GetCurrentProcess", HANDLE, []),
        ("IsProcessInJob", I32, [HANDLE, HANDLE, VOID]),
        ("QueryInformationJobObject", I32, [HANDLE, U32, VOID, U32, VOID]),
        ("GetProcessAffinityMask", I32, [HANDLE, VOID, VOID]),
        ("GetActiveProcessorCount", U32, [ctypes.c_uint16]),
    ):
        function = getattr(backend.kernel, name)
        function.restype, function.argtypes = restype, args
    member = I32()
    backend.checked(
        backend.kernel.IsProcessInJob(
            backend.kernel.GetCurrentProcess(), None, ctypes.byref(member)
        )
    )
    limit, cpu = backend.query(9, ExtendedLimit), backend.query(15, CpuRate)
    mask, system = ctypes.c_size_t(), ctypes.c_size_t()
    backend.checked(
        backend.kernel.GetProcessAffinityMask(
            backend.kernel.GetCurrentProcess(), ctypes.byref(mask), ctypes.byref(system)
        )
    )
    processors = backend.kernel.GetActiveProcessorCount(0xFFFF)
    if not 1 <= processors <= 10000:
        raise Refusal("worker active CPU count outside cap domain")
    validate_limits(
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
            "membership": bool(member.value),
            "pid": os.getpid(),
            **backend.sample(),
        }
    )


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
