"""Synthetic adapter/fault protocol; never a native Windows passing claim."""

from contextlib import contextmanager, suppress
import ctypes
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest

import collagan_windows_adapter as adapter

from millefeuille.domain.metadata_transaction import _rename_entry
from millefeuille.domain.millefeuille import MillefeuilleContractError


class SyntheticIO:
    """Real synthetic POSIX FDs; Windows sharing is NOT emulated/proven.

    It exercises the identical group core and real truncate/write/readback/backup
    and no-replace recovery effects. Fault seams simulate native failures only.
    """

    locks = {}

    def __init__(self, root, *, fault=None):
        self.path = Path(root)
        self.dirs = {(): os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)}
        self.files = {}
        self.fault = fault
        self.faulted = False
        self.rollback_fault = False
        self._closed = False
        self.events = []
        self.current_journal = None
        self.initial = self.root_identity()

    def inject(self, operation, parts, *, rollback=False):
        self.events.append((operation, tuple(parts), rollback))
        if self.fault is not None:
            self.fault(self, operation, tuple(parts), rollback)

    @contextmanager
    def lock(self):
        lock = self.locks.setdefault(self.initial, threading.RLock())
        with lock:
            yield

    def root_identity(self):
        opened = os.fstat(self.dirs[()])
        named = self.path.lstat()
        if (opened.st_dev, opened.st_ino) != (
            named.st_dev,
            named.st_ino,
        ) or not stat.S_ISDIR(named.st_mode):
            raise MillefeuilleContractError("synthetic named run drift")
        return (opened.st_dev, opened.st_ino)

    def directory(self, parts, *, create=False, exclusive=False, delete=False):
        parts = tuple(parts)
        if parts in self.dirs:
            if exclusive:
                raise MillefeuilleContractError("synthetic directory collision")
            return self.dirs[parts]
        parent = self.directory(parts[:-1], create=create)
        if exclusive:
            os.mkdir(parts[-1], 0o700, dir_fd=parent)
        elif create:
            with suppress(FileExistsError):
                os.mkdir(parts[-1], 0o700, dir_fd=parent)
        fd = os.open(
            parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        self.dirs[parts] = fd
        if parts == (adapter.PENDING,):
            self.current_journal = parts
        self.inject("directory", parts)
        return fd

    def exists(self, parts):
        parts = tuple(parts)
        parent = self.directory(parts[:-1])
        try:
            info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if parts[-1] != adapter.PENDING and not stat.S_ISDIR(info.st_mode):
            raise MillefeuilleContractError("synthetic unsafe directory entry")
        return True

    def open_file(self, parts, *, create=False):
        parts = tuple(parts)
        parent = self.directory(parts[:-1], create=True)
        self.inject("before-create" if create else "before-open", parts)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(parts[-1], flags, 0o600, dir_fd=parent)
        except FileNotFoundError:
            if not create:
                return None
            raise
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise MillefeuilleContractError("synthetic unsafe file type/link")
        named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
            os.close(fd)
            raise MillefeuilleContractError("synthetic named file drift")
        self.files[fd] = parts
        return fd

    def snapshot(self, fd):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise MillefeuilleContractError("synthetic held file type/link drift")
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, adapter.MAX_BYTES + 1)
        if len(data) > adapter.MAX_BYTES:
            raise MillefeuilleContractError("synthetic file bound")
        return {
            "inode": [info.st_dev, info.st_ino],
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "data": data,
        }

    def overwrite(self, fd, data, *, rollback=False):
        parts = self.files[fd]
        self.inject("before-truncate", parts, rollback=rollback)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        self.inject("truncate", parts, rollback=rollback)
        midpoint = max(1, len(data) // 2)
        os.write(fd, data[:midpoint])
        self.inject("write", parts, rollback=rollback)
        os.write(fd, data[midpoint:])
        os.fsync(fd)
        self.inject("flush", parts, rollback=rollback)
        self.inject("readback", parts, rollback=rollback)
        if self.snapshot(fd)["data"] != data:
            raise MillefeuilleContractError("synthetic readback drift")

    def move_file(self, fd, destination):
        source = self.files[fd]
        destination = tuple(destination)
        self.inject("withdraw", source)
        _rename_entry(
            self.directory(source[:-1]),
            source[-1],
            self.directory(destination[:-1]),
            destination[-1],
        )
        self.files[fd] = destination

    def move_directory(self, source, destination):
        source, destination = tuple(source), tuple(destination)
        self.inject("before-archive", destination)
        _rename_entry(
            self.directory(source[:-1]),
            source[-1],
            self.directory(destination[:-1]),
            destination[-1],
        )
        self.dirs[destination] = self.dirs.pop(source)
        for fd, parts in list(self.files.items()):
            if parts[: len(source)] == source:
                self.files[fd] = destination + parts[len(source) :]
        self.current_journal = destination
        self.inject("archive", destination)
        return destination

    def journal_location(self):
        return self.current_journal

    def close(self):
        for fd in list(self.files):
            os.close(fd)
        self.files.clear()
        for fd in set(self.dirs.values()):
            os.close(fd)
        self.dirs.clear()
        self._closed = True


@unittest.skipUnless(os.name == "posix", "real POSIX synthetic FD fault environment")
class GroupProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sequence = 0

    def fixture(self):
        self.sequence += 1
        run = self.root / ("run-" + str(self.sequence))
        run.mkdir()
        before = {
            "stage-manifest.json": b'{"stage":"old"}\n',
            "artifact-index.json": b'{"index":"old"}\n',
        }
        for name, value in before.items():
            (run / name).write_bytes(value)
        outputs = {
            "stage-manifest.json": '{"stage":"new"}\n',
            "artifact-index.json": '{"index":"new"}\n',
            "reports/acceptance-summary.json": '{"status":"pass"}\n',
            "reports/acceptance-summary.md": "pass\n",
        }
        return run, before, outputs

    def assert_restored(self, run, before):
        for name, data in before.items():
            self.assertEqual((run / name).read_bytes(), data)
        self.assertFalse((run / "reports/acceptance-summary.json").exists())
        self.assertFalse((run / "reports/acceptance-summary.md").exists())

    def test_complete_group_preserves_raw_backups_and_original_inodes(self):
        run, before, outputs = self.fixture()
        identities = {name: (run / name).stat().st_ino for name in before}
        io = SyntheticIO(run)
        adapter.write_inplace_group(io, outputs)
        self.assertTrue(io._closed)
        self.assertTrue(io._committed)
        for name, text in outputs.items():
            self.assertEqual((run / name).read_bytes(), text.encode())
            self.assertEqual((run / name).stat().st_nlink, 1)
            if name in identities:
                self.assertEqual((run / name).stat().st_ino, identities[name])
        histories = list((run / adapter.HISTORY).iterdir())
        self.assertEqual(len(histories), 1)
        for number, data in enumerate(before.values()):
            self.assertEqual((histories[0] / f"before-{number}.txt").read_bytes(), data)
        self.assertFalse((run / adapter.PENDING).exists())

    def test_every_live_truncate_write_flush_readback_failure_restores_group(self):
        for target in (
            "stage-manifest.json",
            "artifact-index.json",
            "reports/acceptance-summary.json",
            "reports/acceptance-summary.md",
        ):
            for operation in ("truncate", "write", "flush", "readback"):
                with self.subTest(target=target, operation=operation):
                    run, before, outputs = self.fixture()

                    selected_parts = tuple(target.split("/"))

                    def fault(
                        io,
                        event,
                        parts,
                        rollback,
                        *,
                        selected=selected_parts,
                        phase=operation,
                    ):
                        if (
                            not io.faulted
                            and not rollback
                            and parts == selected
                            and event == phase
                        ):
                            io.faulted = True
                            raise OSError("synthetic live failure")

                    io = SyntheticIO(run, fault=fault)
                    with self.assertRaisesRegex(
                        MillefeuilleContractError, "rolled back"
                    ):
                        adapter.write_inplace_group(io, outputs)
                    self.assertTrue(io.faulted)
                    self.assertTrue(io._closed)
                    self.assert_restored(run, before)
                    self.assertFalse((run / adapter.PENDING).exists())

    def test_each_backup_plan_or_new_output_creation_failure_has_no_acceptance(self):
        for selected in (
            (adapter.PENDING, "before-0.txt"),
            (adapter.PENDING, "before-1.txt"),
            (adapter.PENDING, "plan.json"),
            ("reports", "acceptance-summary.json"),
            ("reports", "acceptance-summary.md"),
        ):
            with self.subTest(selected=selected):
                run, before, outputs = self.fixture()

                def fault(io, event, parts, rollback, *, selected=selected):
                    if (
                        event == "before-create"
                        and parts == selected
                        and not io.faulted
                    ):
                        io.faulted = True
                        raise OSError("synthetic exclusive creation failure")

                io = SyntheticIO(run, fault=fault)
                with self.assertRaises(MillefeuilleContractError):
                    adapter.write_inplace_group(io, outputs)
                self.assertTrue(io.faulted)
                self.assert_restored(run, before)
                self.assertTrue(io._closed)

    def test_preexisting_symlink_fifo_and_hardlink_are_preflight_refusals(self):
        for kind in ("symlink", "fifo", "hardlink"):
            with self.subTest(kind=kind):
                run, before, outputs = self.fixture()
                stage = run / "stage-manifest.json"
                stage.unlink()
                sentinel = self.root / (kind + "-outside")
                sentinel.write_bytes(b"outside sentinel")
                if kind == "symlink":
                    stage.symlink_to(sentinel)
                elif kind == "fifo":
                    os.mkfifo(stage)
                else:
                    os.link(sentinel, stage)
                io = SyntheticIO(run)
                with self.assertRaises((MillefeuilleContractError, OSError)):
                    adapter.write_inplace_group(io, outputs)
                self.assertEqual(sentinel.read_bytes(), b"outside sentinel")
                self.assertEqual(
                    (run / "artifact-index.json").read_bytes(),
                    before["artifact-index.json"],
                )
                self.assertFalse((run / adapter.PENDING).exists())
                self.assertTrue(io._closed)

    def test_expected_before_and_run_identity_refuse_before_journal(self):
        for changed in ("hash", "root"):
            with self.subTest(changed=changed):
                run, before, outputs = self.fixture()
                io = SyntheticIO(run)
                kwargs = (
                    {
                        "expected_before": {
                            "stage-manifest.json": {
                                "inode": [0, 0],
                                "bytes": 1,
                                "sha256": "bad",
                            }
                        }
                    }
                    if changed == "hash"
                    else {"expected_directory_identity": (0, 0)}
                )
                with self.assertRaisesRegex(MillefeuilleContractError, "expected"):
                    adapter.write_inplace_group(io, outputs, **kwargs)
                self.assert_restored(run, before)
                self.assertFalse((run / adapter.PENDING).exists())
                self.assertTrue(io._closed)

    def test_input_drift_after_first_write_restores_outputs_and_preserves_input(self):
        run, before, outputs = self.fixture()
        original_input = self.root / "original-input.md"
        original_input.write_text("old input")

        def fault(io, event, parts, rollback):
            if (
                event == "flush"
                and parts == ("stage-manifest.json",)
                and not rollback
                and not io.faulted
            ):
                io.faulted = True
                original_input.write_text("external input change")

        def check():
            if original_input.read_text() != "old input":
                raise MillefeuilleContractError("synthetic consumed input drift")

        io = SyntheticIO(run, fault=fault)
        with self.assertRaisesRegex(MillefeuilleContractError, "recovery required"):
            adapter.write_inplace_group(io, outputs, check_inputs=check)
        self.assert_restored(run, before)
        self.assertEqual(original_input.read_text(), "external input change")
        self.assertTrue((run / adapter.PENDING).exists())

    def test_run_replacement_before_live_write_preserves_displaced_journal(self):
        run, before, outputs = self.fixture()
        moved = self.root / "displaced-run"
        foreign = b"foreign run sentinel"
        checks = [0]

        def check():
            checks[0] += 1
            if checks[0] == 2:
                run.rename(moved)
                run.mkdir()
                (run / "sentinel").write_bytes(foreign)

        io = SyntheticIO(run)
        with self.assertRaisesRegex(MillefeuilleContractError, "recovery required"):
            adapter.write_inplace_group(io, outputs, check_inputs=check)
        self.assertEqual((run / "sentinel").read_bytes(), foreign)
        for name, data in before.items():
            self.assertEqual((moved / name).read_bytes(), data)
        self.assertTrue((moved / adapter.PENDING).exists())
        self.assertTrue(io._closed)

    def test_rollback_failure_preserves_exact_backup_and_reader_refusal(self):
        run, before, outputs = self.fixture()

        def fault(io, event, parts, rollback):
            if (
                event == "write"
                and parts == ("stage-manifest.json",)
                and not io.faulted
                and not rollback
            ):
                io.faulted = True
                raise OSError("synthetic partial live write")
            if event == "before-truncate" and rollback:
                raise OSError("synthetic rollback failure")

        io = SyntheticIO(run, fault=fault)
        with self.assertRaisesRegex(MillefeuilleContractError, "recovery required"):
            adapter.write_inplace_group(io, outputs)
        self.assertEqual(
            (run / adapter.PENDING / "before-0.txt").read_bytes(),
            before["stage-manifest.json"],
        )
        reader = SyntheticIO(run)
        try:
            with (
                self.assertRaisesRegex(MillefeuilleContractError, "pending"),
                adapter.prototype_read_guard(reader),
            ):
                pass
        finally:
            reader.close()
        self.assertFalse((run / "reports/acceptance-summary.json").exists())
        self.assertFalse((run / "reports/acceptance-summary.md").exists())

    def test_marker_commit_failure_restores_whole_group(self):
        run, before, outputs = self.fixture()

        def fault(io, event, parts, rollback):
            if (
                event == "before-archive"
                and not parts[-1].endswith("-rolled-back")
                and not io.faulted
            ):
                io.faulted = True
                raise OSError("synthetic marker commit failure")

        io = SyntheticIO(run, fault=fault)
        with self.assertRaisesRegex(MillefeuilleContractError, "rolled back"):
            adapter.write_inplace_group(io, outputs)
        self.assert_restored(run, before)
        self.assertFalse((run / adapter.PENDING).exists())

    def test_post_archive_fault_restores_owned_pending_then_group(self):
        run, before, outputs = self.fixture()

        def fault(io, event, parts, rollback):
            if (
                event == "archive"
                and not parts[-1].endswith("-rolled-back")
                and not io.faulted
            ):
                io.faulted = True
                raise OSError("synthetic post-move fault")

        io = SyntheticIO(run, fault=fault)
        with self.assertRaisesRegex(MillefeuilleContractError, "rolled back"):
            adapter.write_inplace_group(io, outputs)
        self.assert_restored(run, before)

    def test_failed_pending_recovery_never_claims_barrier(self):
        run, before, outputs = self.fixture()

        def fault(io, event, parts, rollback):
            if event == "archive" and not io.faulted:
                io.faulted = True
                raise OSError("synthetic post-archive fault")
            if event == "before-archive" and parts == (adapter.PENDING,) and io.faulted:
                raise OSError("synthetic pending restoration failure")

        io = SyntheticIO(run, fault=fault)
        with self.assertRaisesRegex(
            MillefeuilleContractError, "pending barrier uncertain"
        ):
            adapter.write_inplace_group(io, outputs)
        self.assert_restored(run, before)
        self.assertFalse((run / adapter.PENDING).exists())
        self.assertTrue(list((run / adapter.HISTORY).iterdir()))

    def test_owned_new_output_withdrawal_collision_keeps_foreign_and_pending(self):
        run, before, outputs = self.fixture()
        foreign = b"foreign recovery sentinel"

        def fault(io, event, parts, rollback):
            if (
                event == "flush"
                and parts == ("reports", "acceptance-summary.md")
                and not io.faulted
            ):
                io.faulted = True
                (run / adapter.PENDING / "failed-3.txt").write_bytes(foreign)
                raise OSError("synthetic last-output fault")

        io = SyntheticIO(run, fault=fault)
        with self.assertRaisesRegex(MillefeuilleContractError, "recovery required"):
            adapter.write_inplace_group(io, outputs)
        self.assertEqual((run / adapter.PENDING / "failed-3.txt").read_bytes(), foreign)
        self.assertTrue((run / adapter.PENDING).exists())

    def test_case_collision_and_unsafe_paths_are_refused_without_effects(self):
        for outputs in (
            {"report.json": "a", "REPORT.JSON": "b"},
            {"../out": "bad"},
            {"a:stream": "bad"},
            {"reports/trailing.": "bad"},
        ):
            with self.subTest(outputs=outputs):
                run, before, unused = self.fixture()
                io = SyntheticIO(run)
                with self.assertRaises(MillefeuilleContractError):
                    adapter.write_inplace_group(io, outputs)
                self.assert_restored(run, before)
                self.assertTrue(io._closed)

    def test_cooperative_reader_waits_for_verified_group(self):
        run, before, outputs = self.fixture()
        first = threading.Event()
        release = threading.Event()
        started = threading.Event()
        done = threading.Event()
        errors = []
        observations = []

        def fault(io, event, parts, rollback):
            if event == "flush" and parts == ("stage-manifest.json",) and not rollback:
                first.set()
                if not release.wait(5):
                    raise TimeoutError("synthetic cooperative reader deadline")

        def writer():
            try:
                adapter.write_inplace_group(SyntheticIO(run, fault=fault), outputs)
            except Exception as error:
                errors.append(error)

        def reader():
            io = SyntheticIO(run)
            started.set()
            try:
                with adapter.prototype_read_guard(io):
                    observations.append(
                        {name: (run / name).read_text() for name in outputs}
                    )
            except Exception as error:
                errors.append(error)
            finally:
                io.close()
                done.set()

        write_thread = threading.Thread(target=writer)
        read_thread = threading.Thread(target=reader)
        write_thread.start()
        self.assertTrue(first.wait(5))
        read_thread.start()
        self.assertTrue(started.wait(5))
        blocked = not done.wait(0.05)
        release.set()
        write_thread.join(5)
        read_thread.join(5)
        self.assertFalse(write_thread.is_alive())
        self.assertFalse(read_thread.is_alive())
        self.assertTrue(blocked)
        self.assertEqual(errors, [])
        self.assertEqual(observations, [outputs])

    def test_corrupt_or_unreadable_backup_never_claims_exact_retention(self):
        for selected in ("drift", "read-failure"):
            with self.subTest(selected=selected):
                run, before, outputs = self.fixture()

                def fault(io, event, parts, rollback, *, selected=selected):
                    if (
                        event == "write"
                        and parts == ("stage-manifest.json",)
                        and not rollback
                        and not io.faulted
                    ):
                        io.faulted = True
                        if selected == "drift":
                            backup = next(
                                fd
                                for fd, name in io.files.items()
                                if name == (adapter.PENDING, "before-0.txt")
                            )
                            os.lseek(backup, 0, os.SEEK_SET)
                            os.ftruncate(backup, 0)
                            os.write(backup, b"synthetic corrupted backup")
                            os.fsync(backup)
                        raise OSError(
                            "synthetic partial live write and retained backup failure"
                        )

                io = SyntheticIO(run, fault=fault)
                snapshot = io.snapshot

                def fail_read(fd, *, selected=selected, io=io, snapshot=snapshot):
                    if (
                        selected == "read-failure"
                        and io.faulted
                        and io.files[fd] == (adapter.PENDING, "before-0.txt")
                    ):
                        raise OSError("synthetic retained backup read failure")
                    return snapshot(fd)

                io.snapshot = fail_read
                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "created backup set; exact retained backups unverified",
                ) as raised:
                    adapter.write_inplace_group(io, outputs)
                self.assertNotIn(
                    "exact before backups reverified", str(raised.exception)
                )
                self.assertTrue(io.faulted)
                self.assertTrue(io._closed)
                self.assertTrue((run / adapter.PENDING).exists())
                self.assertFalse((run / "reports/acceptance-summary.json").exists())
                self.assertFalse((run / "reports/acceptance-summary.md").exists())
                if selected == "drift":
                    self.assertEqual(
                        (run / adapter.PENDING / "before-0.txt").read_bytes(),
                        b"synthetic corrupted backup",
                    )
                else:
                    self.assertEqual(
                        (run / adapter.PENDING / "before-0.txt").read_bytes(),
                        before["stage-manifest.json"],
                    )


class NativeRequestTests(unittest.TestCase):
    def test_nt_open_request_is_anchored_nontruncating_exclusive_no_reparse(self):
        recorded = []

        class NtShim:
            def nt_create_file(
                self,
                pointer,
                access,
                attributes_pointer,
                status,
                allocation,
                file_attributes,
                share,
                disposition,
                options,
                ea,
                ea_length,
            ):
                attributes = ctypes.cast(
                    attributes_pointer, ctypes.POINTER(adapter._ObjectAttributes)
                ).contents
                name = attributes.ObjectName.contents
                recorded.append(
                    {
                        "root": attributes.RootDirectory,
                        "name": ctypes.string_at(name.Buffer, name.Length).decode(
                            "utf-16-le"
                        ),
                        "access": access,
                        "share": share,
                        "disposition": disposition,
                        "options": options,
                    }
                )
                ctypes.cast(
                    pointer, ctypes.POINTER(ctypes.c_void_p)
                ).contents.value = 321
                return 0

        NtShim.NtCreateFile = NtShim.nt_create_file

        # Test-only API-request seam; no native library or sharing attestation.
        io = adapter.NativeWindowsIO.__new__(adapter.NativeWindowsIO)
        io.nt = NtShim()
        result = io._nt_open(
            123,
            "stage-manifest.json",
            access=adapter.READ_WRITE_DELETE,
            share=0,
            disposition=adapter.FILE_OPEN,
            options=adapter.FILE_NON_DIRECTORY_FILE
            | adapter.FILE_OPEN_REPARSE_POINT
            | adapter.FILE_SYNCHRONOUS_IO_NONALERT
            | adapter.FILE_WRITE_THROUGH,
        )
        self.assertEqual(result, 321)
        self.assertEqual(
            recorded,
            [
                {
                    "root": 123,
                    "name": "stage-manifest.json",
                    "access": adapter.READ_WRITE_DELETE,
                    "share": 0,
                    "disposition": 1,
                    "options": 0x00200062,
                }
            ],
        )

    def test_no_replace_rename_request_uses_held_source_and_destination(self):
        recorded = []

        class KernelShim:
            def set_file_information_by_handle(self, handle, kind, buffer, size):
                value = adapter._RenameInformation.from_buffer(buffer)
                name = ctypes.string_at(
                    ctypes.addressof(buffer) + adapter._RenameInformation.name.offset,
                    value.length,
                ).decode("utf-16-le")
                recorded.append((handle, kind, value.flags, value.root, name, size))
                return 1

        io = adapter.NativeWindowsIO.__new__(adapter.NativeWindowsIO)
        KernelShim.SetFileInformationByHandle = (
            KernelShim.set_file_information_by_handle
        )
        io.kernel = KernelShim()
        io.directory = lambda parts, create=False: 456
        io._rename_handle(123, (adapter.PENDING, "failed-2.txt"))
        self.assertEqual(recorded[0][:5], (123, 3, 0, 456, "failed-2.txt"))
        self.assertGreaterEqual(
            recorded[0][5],
            adapter._RenameInformation.name.offset
            + len("failed-2.txt".encode("utf-16-le")),
        )

    def test_native_adapter_refuses_linux_without_test_io_injection(self):
        if os.name == "nt":
            self.skipTest("Linux-only unsupported-platform refusal check")
        with self.assertRaisesRegex(MillefeuilleContractError, "requires Windows"):
            adapter.NativeWindowsIO(Path(tempfile.gettempdir()))


@unittest.skipUnless(os.name == "nt", "actual native Windows qualification required")
class NativeWindowsQualification(unittest.TestCase):
    """These must run on real Windows before any production dispatch proposal.

    Linux source/fault tests cannot grant or waive this qualification. Lower-level
    alias success is an explicit failure: preserve the evidence and choose a
    replacement/confinement repair rather than claim in-place safety.
    """

    def fixture(self, temporary):
        run = Path(temporary) / "approved-run"
        run.mkdir()
        before = {
            "stage-manifest.json": b'{"stage":"old"}\n',
            "artifact-index.json": b'{"index":"old"}\n',
        }
        for name, data in before.items():
            (run / name).write_bytes(data)
        outputs = {
            "stage-manifest.json": '{"stage":"new"}\n',
            "artifact-index.json": '{"index":"new"}\n',
            "reports/acceptance-summary.json": '{"status":"pass"}\n',
            "reports/acceptance-summary.md": "pass\n",
        }
        return run, before, outputs

    def test_native_group_preserves_inode_and_exact_backups(self):
        with tempfile.TemporaryDirectory() as temporary:
            run, before, outputs = self.fixture(temporary)
            identities = {name: (run / name).stat().st_ino for name in before}
            io = adapter.NativeWindowsIO(run)
            adapter.write_inplace_group(io, outputs)
            for name, text in outputs.items():
                self.assertEqual((run / name).read_bytes(), text.encode())
                if name in identities:
                    self.assertEqual((run / name).stat().st_ino, identities[name])
            history = list((run / adapter.HISTORY).iterdir())
            self.assertEqual(len(history), 1)
            for number, data in enumerate(before.values()):
                self.assertEqual(
                    (history[0] / f"before-{number}.txt").read_bytes(), data
                )
            self.assertFalse((run / adapter.PENDING).exists())

    def test_win32_hardlink_creation_cannot_escape_held_single_link_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            run, before, unused = self.fixture(temporary)
            io = adapter.NativeWindowsIO(run)
            try:
                create = io.kernel.CreateHardLinkW
                create.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p]
                create.restype = ctypes.c_int32
                alias = Path(temporary) / "outside-approved-run-alias.json"
                self.assertTrue(
                    create(str(alias), str(run / "stage-manifest.json"), None),
                    (
                        "positive Win32 hardlink capability control failed; "
                        "confinement is unqualified"
                    ),
                )
                self.assertEqual(
                    alias.stat().st_ino, (run / "stage-manifest.json").stat().st_ino
                )
                alias.unlink()  # Own synthetic alias, before acquiring data hold.
                io.open_file(("stage-manifest.json",))
                success = bool(
                    create(str(alias), str(run / "stage-manifest.json"), None)
                )
                self.assertFalse(
                    success,
                    (
                        "Win32 alias escaped exclusive data hold; "
                        "refuse production dispatch"
                    ),
                )
                self.assertFalse(alias.exists())
            finally:
                io.close()

    def test_attribute_handle_native_link_path_does_not_escape_sharing(self):
        with tempfile.TemporaryDirectory() as temporary:
            run, before, unused = self.fixture(temporary)
            outside = Path(temporary) / "outside-approved-run"
            outside.mkdir()
            io = adapter.NativeWindowsIO(run)
            destination = adapter.NativeWindowsIO(outside)
            attribute_handle = None
            try:

                def open_attributes():
                    return io._nt_open(
                        io.root,
                        "stage-manifest.json",
                        access=0x00100080,
                        share=7,
                        disposition=adapter.FILE_OPEN,
                        options=adapter.FILE_OPEN_REPARSE_POINT
                        | adapter.FILE_SYNCHRONOUS_IO_NONALERT,
                    )

                def native_link(handle):
                    # Adversarial native capability test.
                    # This ABI is not a production dependency.
                    name = "alias.json".encode("utf-16-le")
                    size = max(
                        ctypes.sizeof(adapter._RenameInformation),
                        adapter._RenameInformation.name.offset + len(name) + 2,
                    )
                    buffer = ctypes.create_string_buffer(size)
                    link = adapter._RenameInformation.from_buffer(buffer)
                    link.flags, link.root, link.length = 0, destination.root, len(name)
                    ctypes.memmove(
                        ctypes.addressof(buffer)
                        + adapter._RenameInformation.name.offset,
                        name,
                        len(name),
                    )
                    native_set = io.nt.NtSetInformationFile
                    native_set.argtypes = [
                        ctypes.c_void_p,
                        ctypes.POINTER(adapter._IoStatusBlock),
                        ctypes.c_void_p,
                        ctypes.c_uint32,
                        ctypes.c_uint32,
                    ]
                    native_set.restype = ctypes.c_int32
                    status_block = adapter._IoStatusBlock()
                    return native_set(
                        handle, ctypes.byref(status_block), buffer, size, 11
                    )

                # Prove this exact ABI/rights/filesystem path works before relying
                # on its later refusal as an exclusive-hold confinement signal.
                attribute_handle = open_attributes()
                baseline = native_link(attribute_handle)
                self.assertGreaterEqual(
                    baseline,
                    0,
                    (
                        "positive native attribute-handle alias control failed; "
                        "confinement remains unqualified"
                    ),
                )
                alias = outside / "alias.json"
                self.assertEqual(
                    alias.stat().st_ino, (run / "stage-manifest.json").stat().st_ino
                )
                io.kernel.CloseHandle(attribute_handle)
                attribute_handle = None
                alias.unlink()  # Own synthetic alias, before acquiring data hold.
                io.open_file(("stage-manifest.json",))
                try:
                    attribute_handle = open_attributes()
                except OSError as error:
                    self.assertEqual(
                        error.errno,
                        0xC0000043,
                        "attribute-open failed for an unqualified reason",
                    )
                    return
                status = native_link(attribute_handle)
                self.assertLess(
                    status,
                    0,
                    (
                        "native attribute-handle hardlink escaped data hold; "
                        "refuse production dispatch"
                    ),
                )
                self.assertIn(
                    status & 0xFFFFFFFF,
                    (0xC0000043, 0xC0000022),
                    "native alias failed for an unqualified reason",
                )
                self.assertFalse(alias.exists())
            finally:
                if attribute_handle is not None:
                    io.kernel.CloseHandle(attribute_handle)
                destination.close()
                io.close()


if __name__ == "__main__":
    unittest.main()
