"""Offline API-request and fault evidence; never native Windows qualification."""

import ctypes
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / ".github/scripts/native_windows"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


adapter = load("collagan_windows_adapter", ROOT / "adapter.py")
controls = load("collagan_windows_offline_controls", ROOT / "native_controls.py")
controller = load("collagan_windows_controller", ROOT / "controller.py")
GroupProtocolTests = controls.GroupProtocolTests
NativeRequestTests = controls.NativeRequestTests


class NativeLayoutTests(unittest.TestCase):
    def test_amd64_requests_have_windows_widths_and_full_variable_buffer(self):
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            self.skipTest("AMD64 request layout")
        self.assertEqual(ctypes.sizeof(adapter._RenameInformation), 24)
        self.assertEqual(adapter._RenameInformation.root.offset, 8)
        self.assertEqual(adapter._RenameInformation.length.offset, 16)
        self.assertEqual(adapter._RenameInformation.name.offset, 20)
        self.assertEqual(ctypes.sizeof(controller.Accounting), 48)
        self.assertEqual(controller.Accounting.active.offset, 40)
        self.assertEqual(controller.ProcessIds.ids.offset, 8)
        self.assertEqual(ctypes.sizeof(controller.ExtendedLimit), 144)
        for name in ("a", "failed-2.txt", "x" * 64, "astral-\U0001f600"):
            with self.subTest(name=name):
                buffer = adapter._relative_name_information(456, name)
                value = adapter._RenameInformation.from_buffer(buffer)
                encoded = name.encode("utf-16-le")
                self.assertEqual(
                    (value.flags, value.root, value.length), (0, 456, len(encoded))
                )
                self.assertGreaterEqual(ctypes.sizeof(buffer), 24 + len(encoded))
                self.assertEqual(
                    ctypes.string_at(ctypes.addressof(buffer) + 20, len(encoded)),
                    encoded,
                )

    def test_directory_target_sharing_withholds_delete(self):
        calls = []
        io = adapter.NativeWindowsIO.__new__(adapter.NativeWindowsIO)
        io._nt_open = lambda *args, **kw: calls.append(kw) or 456
        io._directory_info = lambda handle: (1, 2)
        for exclusive in (False, True):
            io._open_directory(123, "journal", exclusive=exclusive, delete=True)
        self.assertEqual([v["share"] for v in calls], [3, 3])
        self.assertTrue(all(v["access"] & 0x10000 for v in calls))
        self.assertEqual(adapter.DIRECTORY_SHARE & 4, 0)

    def test_rename_failure_retains_immediate_error_and_abi_context(self):
        io = adapter.NativeWindowsIO.__new__(adapter.NativeWindowsIO)
        io.directory = lambda parts, create=False: 456
        io.kernel = type(
            "Kernel", (), {"SetFileInformationByHandle": staticmethod(lambda *args: 0)}
        )()
        with (
            patch.object(ctypes, "get_last_error", return_value=87, create=True),
            self.assertRaisesRegex(
                OSError, "class=3 flags=0 size=154 name_bytes=128 name_offset=20"
            ) as caught,
        ):
            io._rename_handle(123, (adapter.HISTORY, "x" * 64))
        self.assertEqual(caught.exception.errno, 87)


@unittest.skipUnless(sys.platform != "win32", "real POSIX fault environment")
class EmptyBarrierTests(controls.GroupProtocolTests):
    # Run only added controls here; inherited cases run through GroupProtocolTests.
    def test_commit_and_rollback_move_no_open_child_files(self):
        for failing in (False, True):
            with self.subTest(failing=failing):
                run, before, outputs = self.fixture()
                moves = []
                io = controls.SyntheticIO(run)
                move = io.move_directory

                def empty_move(
                    source, destination, *, io=io, run=run, moves=moves, move=move
                ):
                    self.assertFalse(
                        any(
                            parts[: len(source)] == tuple(source)
                            for parts in io.files.values()
                        )
                    )
                    self.assertEqual(list((run.joinpath(*source)).iterdir()), [])
                    moves.append((source, destination))
                    return move(source, destination)

                io.move_directory = empty_move
                if failing:

                    def fault(shim, event, parts, rollback):
                        if (
                            event == "write"
                            and parts == ("stage-manifest.json",)
                            and not rollback
                            and not shim.faulted
                        ):
                            shim.faulted = True
                            raise OSError("offline partial write")

                    io.fault = fault
                    with self.assertRaisesRegex(
                        adapter.MillefeuilleContractError, "rolled back"
                    ):
                        adapter.write_inplace_group(io, outputs)
                    self.assert_restored(run, before)
                else:
                    adapter.write_inplace_group(io, outputs)
                self.assertEqual(len(moves), 1)
                self.assertEqual(moves[0][0], (adapter.PENDING,))
                self.assertEqual(
                    moves[0][1][-1],
                    "rolled-back-barrier" if failing else "committed-barrier",
                )


# Avoid duplicating the inherited matrix of fault controls.
for _name in dir(controls.GroupProtocolTests):
    if _name.startswith("test_"):
        setattr(EmptyBarrierTests, _name, None)


class JobShim:
    """Models reference-retained membership; cannot qualify a Windows kernel."""

    def __init__(self, *, residue=0, wait=0, failure=None, stubborn=False):
        self.references = {101, 102}
        self.residue, self.wait, self.failure, self.stubborn = (
            residue,
            wait,
            failure,
            stubborn,
        )
        self.events = []

    def wait_for_single_object(self, handle, millis):
        self.events.append(("wait", millis))
        return self.wait

    def get_exit_code_process(self, handle, pointer):
        if self.failure == "exit":
            return 0
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32)).contents.value = 1
        return 1

    def close_handle(self, handle):
        self.events.append(("close", handle))
        if self.failure == "close":
            return 0
        self.references.discard(handle)
        return 1

    def terminate_job_object(self, job, code):
        self.events.append(("terminate-job", job))
        if self.failure == "terminate":
            return 0
        if not self.stubborn:
            self.residue = 0
        self.wait = 0
        return 1

    def terminate_process(self, *args):
        raise AssertionError("assigned job must use owned job termination")

    def query_information_job_object(self, job, kind, pointer, size, returned):
        self.events.append(("query", kind, bool(self.references)))
        if self.failure == "query" and not self.references:
            return 0
        members = ([55] if self.references else []) + ([66] if self.residue else [])
        if kind == 9:
            value = ctypes.cast(
                pointer, ctypes.POINTER(controller.ExtendedLimit)
            ).contents
            value.peak_job = 12345
        elif kind == 1:
            value = ctypes.cast(pointer, ctypes.POINTER(controller.Accounting)).contents
            value.active = len(members)
        elif kind == 3:
            value = ctypes.cast(pointer, ctypes.POINTER(controller.ProcessIds)).contents
            value.assigned = len(members)
            value.count = 0 if self.failure == "incomplete" else len(members)
            for i, member in enumerate(members):
                value.ids[i] = member
        return 1


for _api in (
    "WaitForSingleObject",
    "GetExitCodeProcess",
    "CloseHandle",
    "TerminateJobObject",
    "TerminateProcess",
    "QueryInformationJobObject",
):
    _method = "".join("_" + c.lower() if c.isupper() else c for c in _api).lstrip("_")
    setattr(JobShim, _api, getattr(JobShim, _method))


class CleanupTests(unittest.TestCase):
    def finalize(self, kernel):
        report = {}
        process = controller.ProcessInfo(101, 102, 55, 56)
        ticks = [0.0]

        def pause(value):
            ticks[0] += value

        controller.finalize_owned_worker(
            kernel, 200, process, True, report, 0.3, clock=lambda: ticks[0], pause=pause
        )
        return report, process

    def test_held_exited_reference_drains_after_both_handles_close(self):
        kernel = JobShim()
        report, process = self.finalize(kernel)
        self.assertEqual(report["cleanup_readback"]["before_release"]["active"], 1)
        self.assertEqual(report["owned_operations_in_flight"], 0)
        self.assertEqual(report["worker_job_process_ids"], [])
        self.assertTrue(report["cleanup_readback"]["membership_qualified"])
        self.assertIsNone(process.process)
        self.assertIsNone(process.thread)
        self.assertFalse(any(e[0] == "terminate-job" for e in kernel.events))

    def test_real_residue_terminated_and_zero_readback_required(self):
        kernel = JobShim(residue=1)
        report, unused = self.finalize(kernel)
        self.assertEqual(
            [s["pids"] for s in report["cleanup_readback"]["samples"]], [[66], []]
        )
        self.assertTrue(report["cleanup_readback"]["residue_termination_requested"])

    def test_failure_paths_never_qualify_unknown_membership(self):
        for options in (
            {"failure": "query"},
            {"failure": "close"},
            {"failure": "incomplete"},
            {"failure": "exit"},
            {"wait": 0xFFFFFFFF},
            {"residue": 1, "failure": "terminate"},
            {"residue": 1, "stubborn": True},
        ):
            with self.subTest(options=options):
                kernel = JobShim(**options)
                report = {}
                process = controller.ProcessInfo(101, 102, 55, 56)
                ticks = [0.0]

                def pause(value, *, ticks=ticks):
                    ticks[0] += value

                with self.assertRaises((OSError, RuntimeError)):
                    controller.finalize_owned_worker(
                        kernel,
                        200,
                        process,
                        True,
                        report,
                        0.3,
                        clock=lambda ticks=ticks: ticks[0],
                        pause=pause,
                    )
                self.assertGreater(report["owned_operations_in_flight"], 0)
                self.assertFalse(report["cleanup_readback"]["membership_qualified"])

    def test_live_owned_worker_is_terminated_before_handle_release(self):
        kernel = JobShim(wait=0x102)
        report, unused = self.finalize(kernel)
        self.assertTrue(report["cleanup_readback"]["worker_signaled"])
        self.assertEqual(report["terminal_worker_exit"], 1)
        self.assertEqual(report["owned_operations_in_flight"], 0)
        self.assertEqual(kernel.events[1], ("terminate-job", 200))
