"""UNQUALIFIED offline Windows launcher proposal; no authorized executor exists.

Create a suspended child, assign/query a bounded JobObject, then resume. This
controller must itself receive native resource/failure qualification before a
production or application compatibility claim. It never installs or uploads.
"""

import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

MEMORY = 536870912
RESERVE = 33554432
SCRATCH_CEILING = 268435456
LOG_LIMIT = 131072
FLAGS = 0x8 | 0x10 | 0x100 | 0x200 | 0x400 | 0x2000


class BasicLimit(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", ctypes.c_uint32),
        ("minimum_ws", ctypes.c_size_t),
        ("maximum_ws", ctypes.c_size_t),
        ("active_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority", ctypes.c_uint32),
        ("scheduling", ctypes.c_uint32),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
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
    _fields_ = [("flags", ctypes.c_uint32), ("rate", ctypes.c_uint32)]


class Accounting(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int64)
        for name in ("total_user", "total_kernel", "period_user", "period_kernel")
    ] + [
        (name, ctypes.c_uint32)
        for name in ("page_faults", "total_processes", "active", "terminated")
    ]


class Startup(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
    ] + [
        (name, ctypes.c_uint32)
        for name in ("x", "y", "width", "height", "chars_x", "chars_y", "fill", "flags")
    ]
    _fields_ += [
        ("show", w.WORD),
        ("reserved_size", w.WORD),
        ("reserved_bytes", ctypes.c_void_p),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", w.HANDLE),
        ("thread", w.HANDLE),
        ("pid", ctypes.c_uint32),
        ("tid", ctypes.c_uint32),
    ]


class MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
        (name, ctypes.c_uint64)
        for name in (
            "total_phys",
            "available_phys",
            "total_page",
            "available_page",
            "total_virtual",
            "available_virtual",
            "available_extended",
        )
    ]


class ProcessIds(ctypes.Structure):
    # Active-process admission is one; leave bounded room for inconsistent readback.
    _fields_ = [
        ("assigned", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
        ("ids", ctypes.c_size_t * 16),
    ]


def _api_error(reason):
    return OSError(getattr(ctypes, "get_last_error", lambda: 0)(), reason)


def _job_query(kernel, job, kind, structure):
    value = structure()
    if not kernel.QueryInformationJobObject(
        job, kind, ctypes.byref(value), ctypes.sizeof(value), None
    ):
        raise _api_error("job information query failed; class=" + str(kind))
    return value


def _job_membership(kernel, job):
    accounting = _job_query(kernel, job, 1, Accounting)
    ids = _job_query(kernel, job, 3, ProcessIds)
    if ids.count > 16 or ids.count != ids.assigned:
        raise RuntimeError("job PID list incomplete or exceeds bounded capacity")
    return {
        "active": accounting.active,
        "assigned": ids.assigned,
        "pids": list(ids.ids[: ids.count]),
    }


def finalize_owned_worker(
    kernel,
    job,
    process,
    assigned,
    report,
    deadline,
    *,
    clock=time.monotonic,
    pause=time.sleep,
):
    """Release exited-worker references before bounded accounting/PID drain.

    A signaled primary alone is insufficient. Unknown membership, failed close,
    or a nonzero final sample remains unresolved; never erase an old receipt.
    """
    receipt = report["cleanup_readback"] = {
        "worker_pid": process.pid,
        "handles_released": {},
        "samples": [],
        "membership_qualified": False,
    }
    report["owned_operations_in_flight"] = 1
    state = kernel.WaitForSingleObject(process.process, 0)
    if state == 0x102:
        terminated = (
            kernel.TerminateJobObject(job, 73)
            if assigned
            else kernel.TerminateProcess(process.process, 73)
        )
        if not terminated:
            raise _api_error("owned worker termination failed")
        remaining = max(0, min(10000, int((deadline - clock()) * 1000)))
        state = kernel.WaitForSingleObject(process.process, remaining)
    if state != 0:
        raise RuntimeError(f"owned worker not signaled; wait=0x{state:08x}")
    code = ctypes.c_uint32()
    if not kernel.GetExitCodeProcess(process.process, ctypes.byref(code)):
        raise _api_error("owned worker exit readback failed")
    if code.value == 259:
        raise RuntimeError("owned worker still reports STILL_ACTIVE")
    receipt["worker_signaled"] = True
    report["terminal_worker_exit"] = code.value
    if assigned:
        limits = _job_query(kernel, job, 9, ExtendedLimit)
        report["worker_peak_committed_memory_bytes"] = limits.peak_job
        receipt["before_release"] = _job_membership(kernel, job)
    for name in ("thread", "process"):
        handle = getattr(process, name)
        if handle:
            if not kernel.CloseHandle(handle):
                raise _api_error("owned worker handle close failed: " + name)
            setattr(process, name, None)
            receipt["handles_released"][name] = True
    if not assigned:
        # No job assignment occurred; this sole owned process is signaled and closed.
        report["owned_operations_in_flight"] = 0
        receipt["unassigned_worker_terminal"] = True
        return
    terminated_residue = False
    while True:
        sample = _job_membership(kernel, job)
        receipt["samples"].append(sample)
        report["worker_job_active_processes"] = sample["active"]
        report["worker_job_process_ids"] = sample["pids"]
        report["owned_operations_in_flight"] = max(sample["active"], sample["assigned"])
        if not sample["active"] and not sample["assigned"] and not sample["pids"]:
            receipt["membership_qualified"] = True
            return
        if not terminated_residue:
            if not kernel.TerminateJobObject(job, 73):
                raise _api_error("owned job residue termination failed")
            terminated_residue = True
            receipt["residue_termination_requested"] = True
        if clock() >= deadline or len(receipt["samples"]) >= 101:
            raise RuntimeError("owned job membership did not drain before deadline")
        pause(min(0.1, max(0, deadline - clock())))


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            value.update(chunk)
    return value.hexdigest()


def require_plain(path, directory=False):
    assert path.is_absolute() and not str(path).startswith("\\\\"), (
        "input path must be absolute and local"
    )
    assert ".." not in path.parts, "input path contains traversal"
    for parent in reversed((path.parent, *path.parent.parents)):
        info = parent.lstat()
        assert stat.S_ISDIR(info.st_mode), (
            f"input ancestor is not a directory: {parent}"
        )
        assert not info.st_file_attributes & 0x400, "reparse ancestor"
    info = path.lstat()
    assert not info.st_file_attributes & 0x400, "reparse input"
    assert stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), (
        f"input kind mismatch: {path}"
    )
    return [info.st_dev, info.st_ino, info.st_size]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", required=True)
    p.add_argument("--scratch-parent", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--python", required=True)
    p.add_argument("--python-sha256", required=True)
    p.add_argument("--route-id", required=True)
    p.add_argument("--owner-clearance-id", required=True)
    p.add_argument("--carried-scratch-bytes", type=int, required=True)
    args = p.parse_args()
    started = time.monotonic()
    report = {
        "schema": "millefeuille.native-windows-controller-proposal/v1",
        "status": "stopped-preserve-and-reconcile",
        "native_qualification": False,
        "production_dispatch": False,
        "application_candidate_qualified": False,
        "route_id_operator_provided": args.route_id,
        "owner_clearance_id_operator_provided": args.owner_clearance_id,
        "external_permission_verified_by_this_code": False,
        "private_provider_broker_model_operations": 0,
        "owned_operations_in_flight": 0,
        "failures": [],
    }
    job = scratch = None
    process = ProcessInfo()
    kernel = None
    assigned = resumed = False
    exit_code = None
    phase = "route-and-platform-admission"
    try:
        assert os.name == "nt" and sys.platform == "win32", "requires native Windows"
        assert sys.maxsize > 2**32 and ctypes.sizeof(ExtendedLimit) == 144, (
            f"Windows ABI mismatch: maxsize={sys.maxsize}, "
            f"ExtendedLimit={ctypes.sizeof(ExtendedLimit)}"
        )
        assert args.route_id.strip() and args.owner_clearance_id.strip(), (
            "route or clearance binding is empty"
        )
        assert args.carried_scratch_bytes >= 204000000, (
            "carried scratch below retained floor"
        )
        assert args.carried_scratch_bytes + RESERVE <= SCRATCH_CEILING, (
            "carried scratch plus reserve exceeds ceiling"
        )
        source = Path(args.source_root)
        parent = Path(args.scratch_parent)
        manifest_path = Path(args.manifest)
        python = Path(args.python)
        require_plain(source, True)
        require_plain(parent, True)
        require_plain(manifest_path)
        require_plain(python)
        assert manifest_path.stat().st_size <= 131072, (
            "source manifest exceeds size bound"
        )
        assert digest(manifest_path) == args.manifest_sha256, (
            "source manifest hash mismatch"
        )
        assert digest(python) == args.python_sha256, "Python executable hash mismatch"
        manifest = json.loads(manifest_path.read_text())
        assert (
            manifest["schema"]
            == "millefeuille.native-windows-public-source-manifest/v1"
        ), "source manifest schema mismatch"
        expected_sources = {}
        for record in manifest["sources"] + [manifest["worker"]]:
            relative = Path(record["path"])
            assert not relative.is_absolute() and ".." not in relative.parts, (
                "source record path is absolute or contains traversal"
            )
            path = source / relative
            require_plain(path)
            assert path.stat().st_size == record["bytes"] <= 1048576, (
                f"source size mismatch: {relative}; actual={path.stat().st_size}, "
                f"expected={record['bytes']}"
            )
            assert digest(path) == record["sha256"], f"source hash mismatch: {relative}"
            expected_sources[str(path)] = record["sha256"]
        assert sum(record["bytes"] for record in manifest["sources"]) <= 1048576, (
            "aggregate source exceeds size bound"
        )
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        def bind(name, restype, types):
            value = getattr(kernel, name)
            value.restype, value.argtypes = restype, types
            return value

        handle, void, uint = w.HANDLE, ctypes.c_void_p, ctypes.c_uint32
        bind("CreateJobObjectW", handle, [void, w.LPCWSTR])
        bind("SetInformationJobObject", w.BOOL, [handle, ctypes.c_int, void, uint])
        bind(
            "QueryInformationJobObject",
            w.BOOL,
            [handle, ctypes.c_int, void, uint, ctypes.POINTER(uint)],
        )
        bind("AssignProcessToJobObject", w.BOOL, [handle, handle])
        bind("IsProcessInJob", w.BOOL, [handle, handle, ctypes.POINTER(w.BOOL)])
        bind("GetCurrentProcess", handle, [])
        bind(
            "GetProcessAffinityMask",
            w.BOOL,
            [handle, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)],
        )
        bind("GetActiveProcessorCount", uint, [w.WORD])
        bind(
            "CreateProcessW",
            w.BOOL,
            [
                w.LPCWSTR,
                w.LPWSTR,
                void,
                void,
                w.BOOL,
                uint,
                void,
                w.LPCWSTR,
                ctypes.POINTER(Startup),
                ctypes.POINTER(ProcessInfo),
            ],
        )
        bind("ResumeThread", uint, [handle])
        bind("WaitForSingleObject", uint, [handle, uint])
        bind("GetExitCodeProcess", w.BOOL, [handle, ctypes.POINTER(uint)])
        bind("TerminateJobObject", w.BOOL, [handle, uint])
        bind("TerminateProcess", w.BOOL, [handle, uint])
        bind("CloseHandle", w.BOOL, [handle])
        bind("GlobalMemoryStatusEx", w.BOOL, [ctypes.POINTER(MemoryStatus)])
        bind(
            "GetVolumeInformationW",
            w.BOOL,
            [
                w.LPCWSTR,
                w.LPWSTR,
                uint,
                ctypes.POINTER(uint),
                ctypes.POINTER(uint),
                ctypes.POINTER(uint),
                w.LPWSTR,
                uint,
            ],
        )

        def checked(value):
            if not value:
                raise ctypes.WinError(ctypes.get_last_error())
            return value

        def query(info_class, structure):
            value = structure()
            checked(
                kernel.QueryInformationJobObject(
                    job, info_class, ctypes.byref(value), ctypes.sizeof(value), None
                )
            )
            return value

        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        checked(kernel.GlobalMemoryStatusEx(ctypes.byref(memory)))
        assert memory.available_phys >= 1073741824
        import shutil

        free = shutil.disk_usage(parent).free
        assert free >= 67108864
        fs = ctypes.create_unicode_buffer(32)
        checked(
            kernel.GetVolumeInformationW(
                parent.anchor, None, 0, None, None, None, fs, len(fs)
            )
        )
        assert fs.value.upper() == "NTFS"
        process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
        checked(
            kernel.GetProcessAffinityMask(
                kernel.GetCurrentProcess(),
                ctypes.byref(process_mask),
                ctypes.byref(system_mask),
            )
        )
        assert process_mask.value and system_mask.value
        affinity = process_mask.value & -process_mask.value
        processors = kernel.GetActiveProcessorCount(0xFFFF)
        assert 1 <= processors <= 10000
        report["admission"] = {
            "available_phys_bytes": memory.available_phys,
            "scratch_volume": fs.value,
            "disk_free_bytes": free,
            "active_processors": processors,
            "one_logical_cpu_affinity": affinity,
            "carried_scratch_bytes": args.carried_scratch_bytes,
            "new_scratch_reservation_bytes": RESERVE,
            "Windows_swap0_qualified": False,
        }
        job = checked(kernel.CreateJobObjectW(None, None))
        limit = ExtendedLimit()
        limit.basic.flags, limit.basic.active_limit = FLAGS, 1
        limit.basic.affinity = affinity
        limit.process_memory = limit.job_memory = MEMORY
        checked(
            kernel.SetInformationJobObject(
                job, 9, ctypes.byref(limit), ctypes.sizeof(limit)
            )
        )
        cpu = CpuRate(5, 10000 // processors)
        checked(
            kernel.SetInformationJobObject(
                job, 15, ctypes.byref(cpu), ctypes.sizeof(cpu)
            )
        )
        observed, observed_cpu = query(9, ExtendedLimit), query(15, CpuRate)
        assert observed.basic.flags & FLAGS == FLAGS
        assert not observed.basic.flags & (0x800 | 0x1000)
        assert observed.basic.affinity == affinity and observed.basic.active_limit == 1
        assert observed.process_memory == observed.job_memory == MEMORY
        assert observed_cpu.flags == 5 and observed_cpu.rate == cpu.rate
        report["job_limits_readback"] = {
            "flags": observed.basic.flags,
            "memory_commit_bytes": observed.job_memory,
            "active_process_limit": 1,
            "affinity": affinity,
            "cpu_rate": cpu.rate,
            "launcher_included_in_child_job_memory_limit": False,
        }
        scratch = Path(tempfile.mkdtemp(prefix="mf-native-win3-", dir=parent))
        require_plain(scratch, True)
        report["evidence_root"] = str(scratch)
        command = subprocess.list2cmdline(
            [
                str(python),
                "-I",
                "-B",
                str(source / manifest["worker"]["path"]),
                str(source),
                str(scratch),
                str(manifest_path),
                args.manifest_sha256,
            ]
        )
        environment = {
            "SystemRoot": os.environ["SYSTEMROOT"],
            "WINDIR": os.environ["SYSTEMROOT"],
            "TEMP": str(scratch),
            "TMP": str(scratch),
            "PATH": str(python.parent) + ";" + os.environ["SYSTEMROOT"] + "\\System32",
        }
        env = ctypes.create_unicode_buffer(
            "\0".join(key + "=" + value for key, value in sorted(environment.items()))
            + "\0\0"
        )
        startup = Startup()
        startup.cb = ctypes.sizeof(startup)
        phase = "suspended-create-and-verified-job-assignment"
        checked(
            kernel.CreateProcessW(
                str(python),
                ctypes.create_unicode_buffer(command),
                None,
                None,
                False,
                0x4 | 0x400 | 0x08000000,
                ctypes.cast(env, void),
                str(scratch),
                ctypes.byref(startup),
                ctypes.byref(process),
            )
        )
        report["owned_operations_in_flight"] = 1
        checked(kernel.AssignProcessToJobObject(job, process.process))
        assigned = True
        belongs = w.BOOL()
        checked(kernel.IsProcessInJob(process.process, job, ctypes.byref(belongs)))
        assert belongs.value and query(1, Accounting).active == 1
        child_mask, child_system = ctypes.c_size_t(), ctypes.c_size_t()
        checked(
            kernel.GetProcessAffinityMask(
                process.process, ctypes.byref(child_mask), ctypes.byref(child_system)
            )
        )
        assert child_mask.value == affinity
        assert time.monotonic() - started < 285, "setup exceeded worker window"
        assert kernel.ResumeThread(process.thread) == 1
        resumed = True
        phase = "native-three-controls"
        while True:
            wait = kernel.WaitForSingleObject(process.process, 100)
            if wait == 0:
                break
            assert wait == 0x102, f"native worker wait failed: 0x{wait:08x}"
            assert time.monotonic() - started < 290, "native worker deadline"
            size = sum(
                path.lstat().st_size
                for path in scratch.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            assert size <= RESERVE, "scratch reservation exceeded"
            for name in ("stdout.log", "stderr.log", "worker-result.json"):
                path = scratch / name
                assert not path.exists() or path.stat().st_size <= LOG_LIMIT
        code = uint()
        checked(kernel.GetExitCodeProcess(process.process, ctypes.byref(code)))
        exit_code = code.value
        report["worker_exit"] = exit_code
        result_path = scratch / "worker-result.json"
        assert result_path.stat().st_size <= LOG_LIMIT
        result = json.loads(result_path.read_text())
        report["worker_result"] = result
        assert result["platform"] == "win32" and result["tests_run"] == 3
        assert result["failures"] == result["errors"] == result["skips"] == 0, (
            f"native controls: tests={result['tests_run']} "
            f"failures={result['failures']} "
            f"errors={result['errors']} skips={result['skips']}"
        )
        assert exit_code == 0
        assert {
            path: digest(Path(path)) for path in expected_sources
        } == expected_sources
        report["source_inputs_unchanged"] = True
        report["native_qualification"] = "three prototype controls only"
        report["status"] = "native-three-controls-pass-production-unqualified"
    except Exception as error:
        report["failures"].append(
            {"phase": phase, "type": type(error).__name__, "reason": str(error)[:600]}
        )
    finally:
        if kernel is not None and process.process:
            try:
                finalize_owned_worker(
                    kernel,
                    job,
                    process,
                    assigned,
                    report,
                    min(started + 300, time.monotonic() + 10),
                )
            except Exception as error:
                report["failures"].append(
                    {"phase": "termination/readback", "reason": str(error)[:600]}
                )
        if kernel is not None:
            for handle in (process.thread, process.process, job):
                if handle and not kernel.CloseHandle(handle):
                    report["failures"].append(
                        {"phase": "handle-close", "reason": "owned handle close failed"}
                    )
                    report["owned_operations_in_flight"] = max(
                        1, report["owned_operations_in_flight"]
                    )
        if scratch is not None:
            report["logs"] = []
            for name in ("stdout.log", "stderr.log", "worker-result.json"):
                path = scratch / name
                if path.exists():
                    report["logs"].append(
                        {
                            "path": str(path),
                            "bytes": path.stat().st_size,
                            "sha256": digest(path),
                        }
                    )
                    if path.stat().st_size > LOG_LIMIT:
                        report["failures"].append(
                            {
                                "phase": "final-resource-accounting",
                                "reason": name + " exceeded final log limit",
                            }
                        )
            used = sum(
                path.lstat().st_size
                for path in scratch.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            report["retained_scratch_bytes"] = used
            report["cumulative_scratch_upper_bytes"] = args.carried_scratch_bytes + used
            report["scratch_reservation_met"] = used <= RESERVE
            if used > RESERVE or args.carried_scratch_bytes + used > SCRATCH_CEILING:
                report["failures"].append(
                    {
                        "phase": "final-resource-accounting",
                        "reason": "final scratch limit exceeded",
                    }
                )
        if report["failures"]:
            report["status"] = "stopped-preserve-and-reconcile"
            report["native_qualification"] = False
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["child_resumed"] = resumed
        report["terminal"] = report["owned_operations_in_flight"] == 0
        report["resource_controller_native_qualification_pending"] = True
        encoded = json.dumps(report, sort_keys=True)
        assert len(encoded.encode()) <= LOG_LIMIT
        print(encoded)
    return 0 if not report["failures"] and report["native_qualification"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
