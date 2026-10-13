"""Portable startup/custody fault injection; no Windows qualification claim."""


# ruff: noqa: N802 - Fake methods match the Windows API names exactly.

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
from test_diagnostic_executor import MemoryController
from worker_diagnostics import (
    MESSAGE_CAP,
    STREAM_CAP,
    BoundedStreams,
    exception_record,
    safe_text,
)


def put(pointer, kind, value):
    ctypes.cast(pointer, ctypes.POINTER(kind))[0] = value


class Kernel:
    """All handles and pipes are Python data, never OS resources."""

    def __init__(self, failure=None):
        self.failure = failure
        self.closed = []
        self.deleted = 0
        self.created = 0
        self.pipes = {}
        self.noninherit = []
        self.handle_list = []
        self.reads = 0
        self.flags = None

    def CreatePipe(self, read, write, security, _size):
        number = 10 + len(self.pipes)
        self.pipes[number] = bytearray()
        put(read, nc.HANDLE, number)
        put(write, nc.HANDLE, number + 20)
        self.assert_security(security)
        return self.failure != "pipe"

    @staticmethod
    def assert_security(pointer):
        value = ctypes.cast(pointer, ctypes.POINTER(nc.SecurityAttributes)).contents
        assert value.inherit == 1 and value.size == 24

    def SetHandleInformation(self, handle, mask, flags):
        self.noninherit.append((handle, mask, flags))
        return self.failure != "noninherit"

    def CreateFileW(self, name, _access, _share, security, *_args):
        assert name == "NUL"
        self.assert_security(security)
        return ctypes.c_void_p(-1).value if self.failure == "null" else 40

    def InitializeProcThreadAttributeList(self, storage, _count, _flags, size):
        if storage is None:
            put(size, ctypes.c_size_t, 64)
            return 0
        return self.failure != "attribute-init"

    def UpdateProcThreadAttribute(
        self, _storage, flags, attribute, value, size, *_args
    ):
        assert flags == 0 and attribute == 0x20002 and size == 24
        self.handle_list = list(value)
        return self.failure != "attribute-update"

    def DeleteProcThreadAttributeList(self, _pointer):
        self.deleted += 1
        if self.failure == "attribute-delete":
            raise RuntimeError("injected attribute release error")

    def CreateProcessW(
        self, _exe, _cmd, _ps, _ts, inherit, flags, _env, _cwd, startup, process
    ):
        self.created += 1
        self.flags = flags
        value = ctypes.cast(startup, ctypes.POINTER(nc.StartupEx)).contents
        assert inherit is True and value.startup.cb == 112
        assert value.startup.flags == 0x100
        assert [
            value.startup.stdin,
            value.startup.stdout,
            value.startup.stderr,
        ] == self.handle_list
        info = ctypes.cast(process, ctypes.POINTER(nc.ProcessInfo)).contents
        info.process, info.thread, info.pid = 50, 51, 0
        if self.failure == "create-raise":
            raise RuntimeError("injected late CreateProcess exception")
        return self.failure != "create-bool"

    def GetProcessId(self, _handle):
        return 123

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return not (self.failure == "close" and handle == 30)

    def PeekNamedPipe(self, handle, _buffer, _size, _used, available, _remaining):
        if self.failure == "peek":
            raise OSError("injected pipe peek")
        if not self.pipes[handle]:
            return 0  # ERROR_BROKEN_PIPE is supplied by the test.
        put(available, nc.U32, len(self.pipes[handle]))
        return 1

    def ReadFile(self, handle, block, count, used, _overlapped):
        self.reads += 1
        data = bytes(self.pipes[handle][:count])
        del self.pipes[handle][:count]
        ctypes.memmove(block, data, len(data))
        put(used, nc.U32, len(data))
        return 1


def backend(failure=None):
    value = nc.NativeJobBackend.__new__(nc.NativeJobBackend)
    value.kernel = Kernel(failure)
    value.process, value.job, value.assigned, value.samples = (
        nc.ProcessInfo(),
        None,
        False,
        [],
    )
    value.root, value.source_root, value.argv = (
        Path("/owned"),
        Path("/source"),
        ["python", "-I"],
    )
    value.stdio, value.failed_stdio_closes = {}, set()
    value.attribute_pointer, value.attribute_delete_uncertain = None, False
    value.capture, value.capture_error, value.launch_record = BoundedStreams(), None, {}

    def checked(result):
        if not result:
            raise Refusal("injected native BOOL failure")
        return result

    value.checked = checked
    return value


class StartupTests(unittest.TestCase):
    def test_capture_redacts_split_tokens_controls_and_paths(self):
        capture = BoundedStreams()
        capture.accept("stderr", b"/owned/ ghp_")
        capture.accept("stderr", b"abcd Bearer private\x1b\x00\n")
        result = capture.record((("/owned", "[owned-root]"),))
        text = result["streams"]["stderr"]["text"]
        self.assertNotIn("abcd", text)
        self.assertNotIn("private", text)
        self.assertNotIn("\x1b", text)
        self.assertIn("[owned-root]", text)
        self.assertFalse(result["case_outcomes_established"])
        self.assertEqual(len(safe_text("x" * 9999)), MESSAGE_CAP)

    def test_broken_exception_formatting_remains_bounded(self):
        class BrokenError(Exception):
            def __str__(self):
                raise KeyboardInterrupt

        self.assertEqual(safe_text(BrokenError()), "[unprintable diagnostic]")

    def test_exception_frame_metadata_is_bounded_without_source_reads(self):
        def recurse(depth):
            if depth:
                return recurse(depth - 1)
            raise RuntimeError("synthetic exception")

        try:
            recurse(20)
        except RuntimeError as error:
            record = exception_record(error, "native-backend-startup")
        self.assertEqual(len(record["frames"]), 6)
        self.assertTrue(record["frames_truncated"])
        self.assertTrue(
            all(
                len(frame["file"]) <= 256 and len(frame["function"]) <= 64
                for frame in record["frames"]
            )
        )
        self.assertFalse(record["native_qualified"])

    def test_uncertain_stdio_custody_blocks_terminal_even_without_child(self):
        value = MemoryController("before-create")
        value.has_stdio_handles = True
        value.cleanup = lambda _seconds: {
            "no_owned_process": True,
            "job_closed": True,
            "active": 0,
            "assigned": 0,
            "pids": [],
            "stdio_closed": False,
        }
        result = nc.execute_controller(value)
        self.assertFalse(result["terminal"])
        self.assertEqual(result["owned_operations_in_flight"], 1)

    def test_handle_whitelist_abi_suspension_and_parent_release(self):
        value = backend()
        with patch.dict(nc.os.environ, {"SYSTEMROOT": "C:\\Windows"}):
            value.create_suspended()
        self.assertEqual(ctypes.sizeof(nc.StartupEx), 112)
        self.assertEqual(value.kernel.handle_list, [40, 30, 31])
        self.assertEqual(value.kernel.noninherit, [(10, 1, 0), (11, 1, 0)])
        self.assertEqual(value.kernel.flags, 0x4 | 0x400 | 0x08000000 | 0x80000)
        self.assertEqual(value.kernel.closed, [40, 30, 31])
        self.assertEqual(value.pid, 123)
        self.assertEqual(value.kernel.deleted, 1)
        self.assertEqual(value.launch_record["argv"], ["python", "-I"])
        self.assertEqual(
            set(value.launch_record["environment"]),
            {"SystemRoot", "WINDIR", "TEMP", "TMP", "PATH"},
        )

    def test_partial_admission_closes_known_stdio_once(self):
        for failure in (
            "pipe",
            "noninherit",
            "null",
            "attribute-init",
            "attribute-update",
        ):
            with self.subTest(failure=failure):
                value = backend(failure)
                with (
                    patch.dict(nc.os.environ, {"SYSTEMROOT": "C:\\Windows"}),
                    patch.object(ctypes, "WinError", side_effect=Refusal, create=True),
                    patch.object(
                        ctypes, "get_last_error", return_value=109, create=True
                    ),
                ):
                    with self.assertRaises(Refusal):
                        value.create_suspended()
                    result = value.cleanup(10)
                self.assertTrue(result["no_owned_process"])
                self.assertTrue(result["stdio_closed"])
                self.assertFalse(value.stdio)
                self.assertEqual(
                    len(value.kernel.closed), len(set(value.kernel.closed))
                )
                self.assertEqual(value.kernel.created, 0)
                self.assertEqual(
                    value.kernel.deleted, int(failure == "attribute-update")
                )

    def test_late_create_error_retains_pid_and_releases_parent_writers(self):
        for failure in ("create-raise", "create-bool"):
            with self.subTest(failure=failure):
                value = backend(failure)
                with (
                    patch.dict(nc.os.environ, {"SYSTEMROOT": "C:\\Windows"}),
                    self.assertRaises((RuntimeError, Refusal)),
                ):
                    value.create_suspended()
                self.assertEqual(value.pid, 123)
                self.assertEqual(value.kernel.created, 1)
                self.assertEqual(value.kernel.closed, [40, 30, 31])
                self.assertEqual(set(value.stdio), {"stdout_read", "stderr_read"})

    def test_uncertain_stdio_close_never_retried_other_handles_close(self):
        value = backend("close")
        with (
            patch.dict(nc.os.environ, {"SYSTEMROOT": "C:\\Windows"}),
            self.assertRaises(Refusal),
        ):
            value.create_suspended()
        with patch.object(ctypes, "get_last_error", return_value=109, create=True):
            self.assertFalse(value.finish_stdio())
        self.assertEqual(value.kernel.closed.count(30), 1)
        self.assertEqual(set(value.kernel.closed), {10, 11, 30, 31, 40})
        self.assertEqual(value.failed_stdio_closes, {"stdout_write"})

    def test_attribute_delete_uncertainty_is_preserved_without_retry(self):
        value = backend("attribute-delete")
        with (
            patch.dict(nc.os.environ, {"SYSTEMROOT": "C:\\Windows"}),
            self.assertRaises(Refusal),
        ):
            value.create_suspended()
        with patch.object(ctypes, "get_last_error", return_value=109, create=True):
            self.assertFalse(value.finish_stdio())
        self.assertEqual(value.kernel.deleted, 1)
        self.assertFalse(value.stdio)

    def test_pipe_overflow_stops_reads_then_all_handles_release(self):
        value = backend()
        value.prepare_stdio(nc.StartupEx())
        value.kernel.pipes[10].extend(b"x" * (STREAM_CAP + 99))
        with self.assertRaises(RuntimeError):
            value.drain_worker_output()
        self.assertEqual(len(value.capture.buffers["stdout"]), STREAM_CAP)
        self.assertEqual(len(value.kernel.pipes[10]), 98)
        reads = value.kernel.reads
        with self.assertRaises(Refusal):
            value.drain_worker_output()
        self.assertTrue(value.finish_stdio())
        self.assertEqual(value.kernel.reads, reads)

    def test_quick_exit_captures_stderr_and_emits_controller_observation(self):
        value = MemoryController()
        capture = BoundedStreams()
        pumps = []

        def drain():
            pumps.append(True)
            capture.accept("stderr", b"ImportError: missing synthetic dependency")

        value.wait_primary = lambda _ms: True
        value.drain_worker_output = drain
        value.worker_result = lambda: (_ for _ in ()).throw(
            FileNotFoundError("missing worker receipt")
        )
        value.diagnostic_record = capture.record
        result = nc.execute_controller(value)
        self.assertEqual(len(pumps), 1)
        self.assertTrue(result["terminal"])
        self.assertFalse(result["worker_qualified"])
        self.assertNotIn("worker_result", result)
        observation = result["controller_observed_worker_failure"]
        self.assertEqual(observation["individual_case_outcomes"], "not-established")
        self.assertIn(
            "ImportError",
            result["worker_startup_diagnostics"]["streams"]["stderr"]["text"],
        )

    def test_pipe_error_cannot_skip_known_terminal_process_closes(self):
        value = backend("peek")
        value.prepare_stdio(nc.StartupEx())
        value.process = nc.ProcessInfo(50, 51, 123, 124)
        value.job, value.assigned = 99, True
        value.wait_primary, value.exit_code = lambda _ms: True, lambda: 1
        value.sample = lambda: {"active": 0, "assigned": 0, "pids": []}
        result = value.cleanup(10)
        self.assertTrue(result["stdio_closed"])
        self.assertEqual(
            result["closes"], {"process": True, "thread": True, "job": True}
        )
        self.assertEqual(value.capture_error["type"], "OSError")
        self.assertTrue({50, 51, 99}.issubset(value.kernel.closed))

    def test_capture_failure_blocks_qualification_even_with_simulated_valid_worker(
        self,
    ):
        for issue in ({"overflow": True}, {"capture_error": {"type": "OSError"}}):
            with self.subTest(issue=issue):
                value = MemoryController()
                value.diagnostic_record = lambda issue=issue: issue
                result = nc.execute_controller(value)
                self.assertTrue(result["terminal"])
                self.assertFalse(result["passed"])

    def test_argparse_failure_and_help_do_not_write_error_receipts(self):
        for code in (0, 2):
            with self.subTest(code=code):
                stream = io.StringIO()
                with (
                    patch.object(
                        nw.sys,
                        "argv",
                        ["native_worker.py"] + (["--help"] if code == 0 else []),
                    ),
                    patch.object(nw.sys, "__stderr__", stream),
                    patch.object(nw, "write_entry_error") as write,
                    patch.object(nw, "NativeBackend") as native,
                    patch.object(nw.sys, "stdout", io.StringIO()),
                    patch.object(nw.sys, "stderr", io.StringIO()),
                ):
                    self.assertEqual(nw.guarded_main(), code)
                native.assert_not_called()
                if code == 0:
                    write.assert_not_called()
                    self.assertEqual(stream.getvalue(), "")
                else:
                    record = json.loads(stream.getvalue())
                    self.assertEqual(record["phase"], "argument-parser")
                    self.assertEqual(record["type"], "SystemExit")
                    self.assertFalse(record["case_outcomes_established"])
                    self.assertNotIn("cases", record)

    def test_actual_main_job_admission_failure_reports_phase_without_cases(self):
        # Exercise the real parser-to-job path with pure metadata/native API fakes.
        class Root:
            def __truediv__(self, leaf):
                return path_type("/owned") / leaf

            def lstat(self):
                return SimpleNamespace(st_dev=1, st_ino=2)

        source = Path(nw.__file__).absolute().parent
        marker = {
            "schema": "millefeuille.owned-native-diagnostic-root/v1",
            "root_identity": [1, 2],
            "nonce": "n",
            "expected_head": "a" * 40,
        }
        manifest = {"sdk": {"path": "/sdk", "sha256": "b" * 64}}
        raw = json.dumps(manifest).encode()
        args = SimpleNamespace(
            authorize_owned_synthetic_native_diagnostics=True,
            synthetic_root=Root(),
            source_root=source,
            source_manifest=Path("/manifest"),
            source_manifest_sha256=nc.hashlib.sha256(raw).hexdigest(),
            sdk_dll=Path("/sdk"),
            expected_head="a" * 40,
            nonce="n",
        )
        stream = io.StringIO()
        # PosixPath is fixed before the synthetic os.name/platform patch.
        path_type = type(source)
        with (
            patch.object(nw.argparse.ArgumentParser, "parse_args", return_value=args),
            patch.object(nw.os, "name", "nt"),
            patch.object(nw.sys, "platform", "win32"),
            patch.object(nw.sys, "version_info", (3, 13)),
            patch.object(nw, "Path", path_type),
            patch.object(nw, "plain_path"),
            patch.object(
                nw, "read_plain", side_effect=[json.dumps(marker).encode(), raw]
            ),
            patch.object(nw, "validate_manifest"),
            patch.object(
                nc,
                "require_worker_job",
                side_effect=Refusal("synthetic JobObject refusal"),
            ),
            patch.object(nw, "NativeBackend") as native,
            patch.object(nw, "write_entry_error") as write,
            patch.object(nw.sys, "__stderr__", stream),
        ):
            self.assertEqual(nw.guarded_main(), 1)
        native.assert_not_called()
        record = json.loads(stream.getvalue())
        self.assertEqual(record["phase"], "bounded-job-admission")
        self.assertFalse(record["native_qualified"])
        self.assertNotIn("tests_run", record)
        write.assert_called_once()

    def test_original_stderr_survives_log_redirection(self):
        def failure(entry):
            entry["phase"] = "native-backend-startup"
            nw.sys.stderr = io.StringIO()
            raise Refusal("synthetic backend failure")

        stream = io.StringIO()
        with (
            patch.object(nw, "main", side_effect=failure),
            patch.object(nw, "write_entry_error"),
            patch.object(nw.sys, "__stderr__", stream),
            patch.object(nw.sys, "stderr", io.StringIO()),
        ):
            self.assertEqual(nw.guarded_main(), 1)
        self.assertEqual(
            json.loads(stream.getvalue())["phase"], "native-backend-startup"
        )

    def test_pending_exit_survives_pipe_and_receipt_reporting_failure(self):
        class ExitObserved(BaseException):
            pass

        def failure(_entry):
            nw.LIVE_PENDING_REQUESTS.append(
                object()
            )  # Python sentinel, no kernel request.
            raise KeyboardInterrupt("synthetic early failure")

        try:
            with (
                patch.object(nw, "main", side_effect=failure),
                patch.object(nw, "emit_error", side_effect=OSError("broken pipe")),
                patch.object(
                    nw, "write_entry_error", side_effect=OSError("receipt failure")
                ),
                patch.object(nw.os, "_exit", side_effect=ExitObserved) as terminate,
                self.assertRaises(ExitObserved),
            ):
                nw.guarded_main()
            terminate.assert_called_once_with(1)
            self.assertEqual(len(nw.LIVE_PENDING_REQUESTS), 1)
        finally:
            nw.LIVE_PENDING_REQUESTS.clear()

    def test_error_receipt_refuses_unadmitted_or_replaced_root(self):
        nw.write_entry_error({}, {})
        root = SimpleNamespace(__truediv__=lambda _self, _leaf: None)

        # A directory identity mismatch refuses before any output path is opened.
        class Root:
            def __truediv__(self, _leaf):
                return root

        entry = {
            "root": Root(),
            "root_identity": [1, 2],
            "nonce": "n",
            "expected_head": "a" * 40,
        }
        marker = {
            "root_identity": [1, 2],
            "nonce": "n",
            "expected_head": "a" * 40,
            "schema": "millefeuille.owned-native-diagnostic-root/v1",
        }
        with (
            patch.object(
                nw, "plain_path", return_value=SimpleNamespace(st_dev=1, st_ino=3)
            ),
            patch.object(nw, "read_plain", return_value=json.dumps(marker).encode()),
            self.assertRaises(Refusal),
        ):
            nw.write_entry_error(entry, {})

    def test_bound_owned_error_receipt_is_separate_from_worker_result(self):
        output = io.BytesIO()

        class Output:
            def __enter__(self):
                return output

            def __exit__(self, *_arguments):
                pass

        class Root:
            def __init__(self):
                self.leaves = []
                self.mode = None

            def __truediv__(self, leaf):
                self.leaves.append(leaf)
                return self

            def open(self, mode):
                self.mode = mode
                return Output()

        root = Root()
        entry = {
            "root": root,
            "root_identity": [1, 2],
            "nonce": "n",
            "expected_head": "a" * 40,
        }
        marker = {
            "schema": "millefeuille.owned-native-diagnostic-root/v1",
            "root_identity": [1, 2],
            "nonce": "n",
            "expected_head": "a" * 40,
        }
        record = {
            "schema": "millefeuille.worker-entry-error/v1",
            "native_qualified": False,
        }
        with (
            patch.object(
                nw, "plain_path", return_value=SimpleNamespace(st_dev=1, st_ino=2)
            ),
            patch.object(nw, "read_plain", return_value=json.dumps(marker).encode()),
        ):
            nw.write_entry_error(entry, record)
        self.assertEqual(root.leaves, ["owner-marker.json", "worker-entry-error.json"])
        self.assertEqual(root.mode, "xb")
        self.assertEqual(json.loads(output.getvalue()), record)
        self.assertNotIn("cases", json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
