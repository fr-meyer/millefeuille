"""Windows in-place group prototype, pending native confinement qualification.

Existing objects are modified only through held exclusive non-truncating file
handles. Backups preserve raw bytes. Cooperative readers/writers use the same
run mutex and pending barrier. This is NOT a multi-file atomic rename, a promise
against every native metadata/alias operation, or power-loss atomicity. The
prototype is not production-dispatched until native Windows qualification.
"""

from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import (
    _read_opened_file_bytes,
    _stat_stability_snapshot,
)

PENDING = ".millefeuille-metadata-pending"
HISTORY = ".millefeuille-metadata-history"
MAX_BYTES = 16 * 1024 * 1024
MAX_GROUP_BYTES = 64 * 1024 * 1024
READ_WRITE_DELETE = 0x0013019F
DIRECTORY_READ = 0x001200A0
DIRECTORY_SHARE = 3  # READ | WRITE; still withhold DELETE for pinned directories.
FILE_OPEN = 1
FILE_CREATE = 2
FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_SYNCHRONOUS_IO_NONALERT = 0x20
FILE_WRITE_THROUGH = 2
FILE_NON_DIRECTORY_FILE = 0x40
FILE_DIRECTORY_FILE = 1
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_DIRECTORY = 0x10


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_uint16),
        ("MaximumLength", ctypes.c_uint16),
        ("Buffer", ctypes.c_void_p),
    ]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_uint32),
        ("RootDirectory", ctypes.c_void_p),
        ("ObjectName", ctypes.POINTER(_UnicodeString)),
        ("Attributes", ctypes.c_uint32),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _IoStatusUnion(ctypes.Union):
    _fields_ = [("Status", ctypes.c_int32), ("Pointer", ctypes.c_void_p)]


class _IoStatusBlock(ctypes.Structure):
    _fields_ = [("status", _IoStatusUnion), ("Information", ctypes.c_size_t)]


class _FileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", ctypes.c_uint32),
        ("creation", ctypes.c_uint32 * 2),
        ("access", ctypes.c_uint32 * 2),
        ("write", ctypes.c_uint32 * 2),
        ("volume", ctypes.c_uint32),
        ("size_high", ctypes.c_uint32),
        ("size_low", ctypes.c_uint32),
        ("links", ctypes.c_uint32),
        ("index_high", ctypes.c_uint32),
        ("index_low", ctypes.c_uint32),
    ]


class _FileIdInformation(ctypes.Structure):
    _fields_ = [("volume", ctypes.c_uint64), ("file_id", ctypes.c_ubyte * 16)]


class _RenameInformation(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("root", ctypes.c_void_p),
        ("length", ctypes.c_uint32),
        ("name", ctypes.c_uint16 * 1),
    ]


def _leaf(name):
    if (
        not name
        or name in {".", ".."}
        or any(c in name for c in "\\/\x00:")
        or name.endswith((".", " "))
    ):
        raise MillefeuilleContractError("unsafe Windows metadata leaf")
    return name


def _relative_name_information(parent, name):
    """Zero flags, byte-counted UTF-16 and full native structure allocation."""
    encoded = _leaf(name).encode("utf-16-le")
    if len(encoded) > 65532:
        raise MillefeuilleContractError("Windows metadata name exceeds bound")
    # Includes trailing structure padding, the variable name and a spare NUL.
    size = ctypes.sizeof(_RenameInformation) + len(encoded) + 2
    buffer = ctypes.create_string_buffer(size)
    value = _RenameInformation.from_buffer(buffer)
    value.flags, value.root, value.length = 0, parent, len(encoded)
    ctypes.memmove(
        ctypes.addressof(buffer) + _RenameInformation.name.offset, encoded, len(encoded)
    )
    return buffer


def _binding(snapshot):
    if snapshot is None:
        return None
    return {key: snapshot[key] for key in ("inode", "bytes", "sha256")}


class NativeWindowsIO:
    """Actual Win32/NT handle operations; constructed only on native Windows.

    Linux synthetic qualification injects a separate IO shim. It cannot qualify
    these native sharing, filesystem, directory or kernel mutex guarantees.
    """

    def __init__(self, root, *, create=False):
        if os.name != "nt":
            raise MillefeuilleContractError("native Windows adapter requires Windows")
        import msvcrt

        self.crt = msvcrt
        self.path = Path(root).absolute()
        self.handles = []
        self.files = {}
        self.directories = {}
        self.mutex_handle = None
        self.rename_observations = []
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.nt = ctypes.WinDLL("ntdll", use_last_error=True)
        prototypes = {
            "CreateFileW": (
                [
                    ctypes.c_wchar_p,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.c_void_p,
                ],
                ctypes.c_void_p,
            ),
            "CloseHandle": ([ctypes.c_void_p], ctypes.c_int32),
            "GetFileInformationByHandle": (
                [ctypes.c_void_p, ctypes.POINTER(_FileInformation)],
                ctypes.c_int32,
            ),
            "GetFileInformationByHandleEx": (
                [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32],
                ctypes.c_int32,
            ),
            "SetFileInformationByHandle": (
                [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32],
                ctypes.c_int32,
            ),
            "FlushFileBuffers": ([ctypes.c_void_p], ctypes.c_int32),
            "CreateMutexW": (
                [ctypes.c_void_p, ctypes.c_int32, ctypes.c_wchar_p],
                ctypes.c_void_p,
            ),
            "WaitForSingleObject": (
                [ctypes.c_void_p, ctypes.c_uint32],
                ctypes.c_uint32,
            ),
            "ReleaseMutex": ([ctypes.c_void_p], ctypes.c_int32),
        }
        for name, (arguments, result) in prototypes.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.nt.NtCreateFile.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint32,
            ctypes.POINTER(_ObjectAttributes),
            ctypes.POINTER(_IoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self.nt.NtCreateFile.restype = ctypes.c_int32
        try:
            # Only the volume/share anchor is opened by absolute path. Every
            # following child is relative to its already held parent handle.
            anchor = self.kernel.CreateFileW(
                str(self.path.anchor),
                DIRECTORY_READ,
                DIRECTORY_SHARE,
                None,
                3,
                0x02000000 | FILE_OPEN_REPARSE_POINT,
                None,
            )
            if anchor in (None, ctypes.c_void_p(-1).value):
                raise OSError(ctypes.get_last_error(), "Windows metadata anchor open")
            self.handles.append(anchor)
            self._directory_info(anchor)
            parent = anchor
            for part in self.path.parts[1:]:
                _leaf(part)
                child = self._open_directory(parent, part, create=create)
                self.handles.append(child)
                parent = child
            self.root = parent
            self.directories[()] = parent
            self.native_root_identity = self._directory_info(parent)
            self.stat_root_identity = self.root_identity()
        except Exception:
            self.close()
            raise

    def _info(self, handle):
        result = _FileInformation()
        if not self.kernel.GetFileInformationByHandle(handle, ctypes.byref(result)):
            raise OSError(ctypes.get_last_error(), "Windows metadata file information")
        identifier = _FileIdInformation()
        if not self.kernel.GetFileInformationByHandleEx(
            handle, 18, ctypes.byref(identifier), ctypes.sizeof(identifier)
        ):
            raise OSError(ctypes.get_last_error(), "Windows metadata stable identity")
        identity = (
            int(identifier.volume),
            int.from_bytes(bytes(identifier.file_id), "little"),
        )
        if (
            not identity[0]
            or not identity[1]
            or result.attributes & FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise MillefeuilleContractError("Windows metadata identity/reparse refusal")
        return result, identity

    def _directory_info(self, handle):
        result, identity = self._info(handle)
        if not result.attributes & FILE_ATTRIBUTE_DIRECTORY:
            raise MillefeuilleContractError(
                "Windows metadata parent is not a directory"
            )
        return identity

    def _nt_open(self, parent, name, *, access, share, disposition, options):
        encoded = _leaf(name).encode("utf-16-le")
        if len(encoded) > 65532:
            raise MillefeuilleContractError("Windows metadata name exceeds bound")
        buffer = ctypes.create_string_buffer(encoded + b"\0\0")
        unicode_name = _UnicodeString(
            len(encoded), len(encoded) + 2, ctypes.cast(buffer, ctypes.c_void_p)
        )
        attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes),
            parent,
            ctypes.pointer(unicode_name),
            0x40,
            None,
            None,
        )
        status_block = _IoStatusBlock()
        handle = ctypes.c_void_p()
        status = self.nt.NtCreateFile(
            ctypes.byref(handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(status_block),
            None,
            0x80,
            share,
            disposition,
            options,
            None,
            0,
        )
        if status < 0:
            code = status & 0xFFFFFFFF
            if code in (0xC0000034, 0xC000003A):
                raise FileNotFoundError("Windows metadata relative name absent")
            raise OSError(code, "Windows metadata relative handle open")
        if not handle.value:
            raise MillefeuilleContractError("Windows metadata empty handle")
        return handle.value

    def _open_directory(
        self, parent, name, *, create=False, exclusive=False, delete=False
    ):
        access = DIRECTORY_READ | (0x10000 if delete else 0)
        if exclusive:
            # Exclusive creation cannot follow a pre-existing reparse object.
            handle = self._nt_open(
                parent,
                name,
                access=access,
                share=DIRECTORY_SHARE,
                disposition=FILE_CREATE,
                options=FILE_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT,
            )
        else:
            try:
                # Open without FILE_DIRECTORY_FILE; inspect the held object.
                # This avoids the undocumented DIRECTORY|REPARSE option mix.
                handle = self._nt_open(
                    parent,
                    name,
                    access=access,
                    share=DIRECTORY_SHARE,
                    disposition=FILE_OPEN,
                    options=FILE_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT,
                )
            except FileNotFoundError:
                if not create:
                    raise
                return self._open_directory(parent, name, exclusive=True, delete=delete)
        try:
            self._directory_info(handle)
            return handle
        except Exception:
            self.kernel.CloseHandle(handle)
            raise

    def directory(self, parts, *, create=False, exclusive=False, delete=False):
        parts = tuple(parts)
        if parts in self.directories:
            if exclusive:
                raise MillefeuilleContractError("Windows metadata directory collision")
            self._directory_info(self.directories[parts])
            return self.directories[parts]
        parent = self.directory(parts[:-1], create=create)
        handle = self._open_directory(
            parent, parts[-1], create=create, exclusive=exclusive, delete=delete
        )
        self.handles.append(handle)
        self.directories[parts] = handle
        return handle

    def exists(self, parts):
        parts = tuple(parts)
        parent = self.directory(parts[:-1])
        try:
            handle = self._nt_open(
                parent,
                parts[-1],
                access=DIRECTORY_READ,
                share=7,
                disposition=FILE_OPEN,
                options=FILE_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT,
            )
        except FileNotFoundError:
            return False
        try:
            # Any entry blocks a pending name; other lookups require a safe dir.
            if parts[-1] != PENDING:
                self._directory_info(handle)
            return True
        finally:
            self.kernel.CloseHandle(handle)

    def open_file(self, parts, *, create=False):
        parts = tuple(parts)
        parent = self.directory(parts[:-1], create=True)
        try:
            handle = self._nt_open(
                parent,
                parts[-1],
                access=READ_WRITE_DELETE,
                share=0,
                disposition=FILE_CREATE if create else FILE_OPEN,
                options=FILE_NON_DIRECTORY_FILE
                | FILE_OPEN_REPARSE_POINT
                | FILE_SYNCHRONOUS_IO_NONALERT
                | FILE_WRITE_THROUGH,
            )
        except FileNotFoundError:
            if not create:
                return None
            raise
        try:
            info, _ = self._info(handle)
            if info.attributes & FILE_ATTRIBUTE_DIRECTORY or info.links != 1:
                raise MillefeuilleContractError(
                    "Windows metadata regular single-link file required"
                )
            fd = self.crt.open_osfhandle(
                handle, os.O_RDWR | os.O_BINARY | os.O_NOINHERIT
            )
        except Exception:
            self.kernel.CloseHandle(handle)
            raise
        self.files[fd] = parts
        return fd

    def snapshot(self, fd):
        before = os.fstat(fd)
        info, _ = self._info(self.crt.get_osfhandle(fd))
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or info.links != 1:
            raise MillefeuilleContractError("Windows metadata file type/link drift")
        os.lseek(fd, 0, os.SEEK_SET)
        data = _read_opened_file_bytes(
            fd,
            before,
            path=self.path.joinpath(*self.files[fd]),
            label="Windows held metadata",
            max_bytes=MAX_BYTES,
        )
        after = os.fstat(fd)
        if _stat_stability_snapshot(before) != _stat_stability_snapshot(after):
            raise MillefeuilleContractError("Windows metadata changed while read")
        return {
            "inode": [int(after.st_dev), int(after.st_ino)],
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "data": data,
        }

    def overwrite(self, fd, data, *, rollback=False):
        if len(data) > MAX_BYTES:
            raise MillefeuilleContractError("Windows metadata output exceeds bound")
        self.snapshot(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("Windows metadata zero-length write")
            view = view[written:]
        if not self.kernel.FlushFileBuffers(self.crt.get_osfhandle(fd)):
            raise OSError(ctypes.get_last_error(), "Windows metadata flush")
        if self.snapshot(fd)["data"] != data:
            raise MillefeuilleContractError("Windows metadata write readback drift")

    def _rename_handle(self, handle, destination):
        destination = tuple(destination)
        parent = self.directory(destination[:-1], create=True)
        buffer = _relative_name_information(parent, destination[-1])
        size = ctypes.sizeof(buffer)
        success = self.kernel.SetFileInformationByHandle(handle, 3, buffer, size)
        error = ctypes.get_last_error() if not success else 0
        if not hasattr(self, "rename_observations"):
            self.rename_observations = []
        if len(self.rename_observations) < 16:
            self.rename_observations.append(
                {
                    "api": "SetFileInformationByHandle",
                    "class": 3,
                    "flags": 0,
                    "size": size,
                    "name_bytes": _RenameInformation.from_buffer(buffer).length,
                    "name_offset": _RenameInformation.name.offset,
                    "structure_bytes": ctypes.sizeof(_RenameInformation),
                    "directory_share": DIRECTORY_SHARE,
                    "success": bool(success),
                    "GetLastError": error,
                }
            )
        if not success:
            raise OSError(
                error,
                "Windows metadata no-replace handle rename; "
                f"class=3 flags=0 size={size} "
                f"name_bytes={_RenameInformation.from_buffer(buffer).length} "
                f"name_offset={_RenameInformation.name.offset}",
            )

    def move_file(self, fd, destination):
        self._rename_handle(self.crt.get_osfhandle(fd), destination)
        self.files[fd] = tuple(destination)

    def move_directory(self, source, destination):
        source, destination = tuple(source), tuple(destination)
        handle = self.directories[source]
        self._rename_handle(handle, destination)
        self.directories[destination] = self.directories.pop(source)
        for fd, parts in list(self.files.items()):
            if parts[: len(source)] == source:
                self.files[fd] = destination + parts[len(source) :]
        return destination

    def root_identity(self):
        # READ|WRITE sharing permits target opens. Recheck every held ancestor
        # for an in-place reparse change; DELETE remains denied.
        for handle in self.handles:
            self._directory_info(handle)
        if self._directory_info(self.root) != self.native_root_identity:
            raise MillefeuilleContractError("Windows metadata pinned run drift")
        info = self.path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise MillefeuilleContractError("Windows metadata named run drift")
        return (int(info.st_dev), int(info.st_ino))

    @contextmanager
    def lock(self):
        name = (
            "Global\\MillefeuilleMetadata-"
            + hashlib.sha256(repr(self.native_root_identity).encode()).hexdigest()
        )
        mutex = self.kernel.CreateMutexW(None, 0, name)
        if not mutex:
            raise OSError(ctypes.get_last_error(), "Windows metadata run mutex")
        acquired = False
        try:
            status = self.kernel.WaitForSingleObject(mutex, 30000)
            acquired = status in (0, 0x80)
            if status != 0:
                # Abandonment is a recovery signal, not a consistency attestation.
                raise MillefeuilleContractError(
                    "Windows metadata mutex abandoned/timed out/failed"
                )
            yield
        finally:
            if acquired and not self.kernel.ReleaseMutex(mutex):
                self.kernel.CloseHandle(mutex)
                raise OSError(ctypes.get_last_error(), "Windows metadata mutex release")
            self.kernel.CloseHandle(mutex)

    def close(self):
        errors = []
        for fd in list(self.files):
            try:
                os.close(fd)
            except OSError:
                errors.append("file-close")
            del self.files[fd]
        for handle in reversed(self.handles):
            if not self.kernel.CloseHandle(handle):
                errors.append("directory-close")
        self.handles.clear()
        self._closed = True
        if errors:
            raise MillefeuilleContractError("Windows metadata cleanup uncertain")


def _write_inplace_group_open(
    io,
    outputs,
    *,
    expected_before=None,
    expected_directory_identity=None,
    check_inputs=None,
):
    """Private prototype core; IO injection is exclusively a synthetic seam.

    Native sharing/alias confinement is not established by synthetic execution.
    Ordinary pre-commit failures restore held originals and withdraw only owned
    new outputs. Failed rollback retains pending and exact backups. A successful
    marker archive is the read-visible commit; cleanup failures are post-commit.
    """
    if not outputs or len(outputs) > 16:
        raise MillefeuilleContractError("Windows metadata output set invalid")
    entries = []
    total_bytes = 0
    for name, text in outputs.items():
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or relative.parts[0].startswith(".millefeuille-metadata-")
        ):
            raise MillefeuilleContractError("Windows metadata output path unsafe")
        for part in relative.parts:
            _leaf(part)
        data = text.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise MillefeuilleContractError("Windows metadata output exceeds bound")
        total_bytes += len(data)
        if total_bytes > MAX_GROUP_BYTES:
            raise MillefeuilleContractError("Windows metadata group exceeds bound")
        entries.append({"name": name, "parts": relative.parts, "data": data})
    if len({entry["name"].casefold() for entry in entries}) != len(entries):
        raise MillefeuilleContractError(
            "Windows metadata case-insensitive output collision"
        )
    journal = None
    barrier = None
    io._committed = False
    initial = io.root_identity()
    if (
        expected_directory_identity is not None
        and initial != expected_directory_identity
    ):
        raise MillefeuilleContractError("Windows metadata expected run drift")
    if io.exists((PENDING,)):
        raise MillefeuilleContractError("Windows metadata pending; recovery required")

    def check():
        if io.root_identity() != initial:
            raise MillefeuilleContractError("Windows metadata named run drift")
        if check_inputs is not None:
            check_inputs()
        if io.root_identity() != initial:
            raise MillefeuilleContractError("Windows metadata named run drift")

    try:
        for entry in entries:
            entry["fd"] = io.open_file(entry["parts"])
            entry["before"] = None if entry["fd"] is None else io.snapshot(entry["fd"])
            if (
                expected_before is not None
                and entry["name"] in expected_before
                and _binding(entry["before"]) != expected_before[entry["name"]]
            ):
                raise MillefeuilleContractError(
                    "Windows metadata expected-before drift"
                )
        if (
            sum(
                entry["before"]["bytes"]
                for entry in entries
                if entry["before"] is not None
            )
            > MAX_GROUP_BYTES
        ):
            raise MillefeuilleContractError(
                "Windows metadata before group exceeds bound"
            )
        check()
        if all(
            entry["before"] is not None and entry["before"]["data"] == entry["data"]
            for entry in entries
        ):
            return
        packet = [
            {
                "path": entry["name"],
                "before": _binding(entry["before"]),
                "after": {
                    "bytes": len(entry["data"]),
                    "sha256": hashlib.sha256(entry["data"]).hexdigest(),
                },
            }
            for entry in entries
        ]
        encoded = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
        archive = hashlib.sha256(encoded).hexdigest()
        io.directory((HISTORY,), create=True)
        if io.exists((HISTORY, archive)):
            raise MillefeuilleContractError("Windows metadata journal collision")
        # Never rename a directory containing held backup/plan files on Windows.
        # Allocate the immutable journal first; only its empty barrier moves.
        io.directory((HISTORY, archive), exclusive=True)
        io.directory((PENDING,), exclusive=True, delete=True)
        journal = (HISTORY, archive)
        barrier = (PENDING,)
        for number, entry in enumerate(entries):
            if entry["before"] is not None:
                backup = io.open_file(journal + (f"before-{number}.txt",), create=True)
                io.overwrite(backup, entry["before"]["data"])
                entry["backup"] = backup
        plan = io.open_file(journal + ("plan.json",), create=True)
        io.overwrite(plan, encoded + b"\n")
        for entry in entries:
            if entry["fd"] is None:
                entry["fd"] = io.open_file(entry["parts"], create=True)
                entry["created"] = True
        check()
        for entry in entries:
            entry["attempted"] = True
            io.overwrite(entry["fd"], entry["data"])
            observed = io.snapshot(entry["fd"])
            if (
                entry["before"] is not None
                and observed["inode"] != entry["before"]["inode"]
            ):
                raise MillefeuilleContractError("Windows metadata held inode drift")
            entry["after"] = _binding(observed)
            check()
        for entry in entries:
            if io.snapshot(entry["fd"])["data"] != entry["data"]:
                raise MillefeuilleContractError("Windows metadata final readback drift")
            if (
                "backup" in entry
                and io.snapshot(entry["backup"])["data"] != entry["before"]["data"]
            ):
                raise MillefeuilleContractError("Windows metadata exact backup drift")
        check()
        barrier = io.move_directory(barrier, journal + ("committed-barrier",))
        io._committed = True
    except Exception as cause:
        if journal is None:
            raise
        errors = []
        if not io.exists((PENDING,)):
            try:
                actual = (
                    io.journal_location()
                    if hasattr(io, "journal_location")
                    else journal + ("committed-barrier",)
                )
                barrier = io.move_directory(actual, (PENDING,))
            except Exception:
                errors.append("pending-barrier-uncertain")
        try:
            check()
        except Exception:
            errors.append("input-or-run-drift")
        try:
            root_stable = io.root_identity() == initial
        except Exception:
            root_stable = False
        if root_stable:
            for number, entry in reversed(list(enumerate(entries))):
                try:
                    if entry.get("created"):
                        io.move_file(entry["fd"], journal + (f"failed-{number}.txt",))
                    elif entry.get("attempted"):
                        if (
                            io.snapshot(entry["backup"])["data"]
                            != entry["before"]["data"]
                        ):
                            raise MillefeuilleContractError(
                                "Windows rollback exact backup drift"
                            )
                        io.overwrite(
                            entry["fd"], entry["before"]["data"], rollback=True
                        )
                        if _binding(io.snapshot(entry["fd"])) != _binding(
                            entry["before"]
                        ):
                            raise MillefeuilleContractError(
                                "Windows rollback result drift"
                            )
                except Exception:
                    errors.append("rollback-failed")
        else:
            errors.append("run-drift-no-automatic-rollback")
        if not errors:
            try:
                barrier = io.move_directory(barrier, journal + ("rolled-back-barrier",))
            except Exception:
                errors.append("rollback-journal-archive-failed")
        if errors:
            created_backups = all(
                entry["before"] is None or "backup" in entry for entry in entries
            )
            verified_backups = created_backups
            if created_backups:
                for entry in entries:
                    if entry["before"] is None:
                        continue
                    try:
                        if (
                            io.snapshot(entry["backup"])["data"]
                            != entry["before"]["data"]
                        ):
                            verified_backups = False
                    except Exception:
                        verified_backups = False
            message = "Windows metadata recovery required; journal retained"
            if verified_backups:
                message += "; exact before backups reverified"
            elif created_backups:
                message += "; created backup set; exact retained backups unverified"
            else:
                message += "; backup set incomplete"
            if "pending-barrier-uncertain" in errors:
                message += "; pending barrier uncertain"
            else:
                message += "; pending retained"
        else:
            message = "Windows metadata rolled back; journal retained"
        raise MillefeuilleContractError(message) from cause


def write_inplace_group(io, outputs, **kwargs):
    try:
        with io.lock():
            try:
                return _write_inplace_group_open(io, outputs, **kwargs)
            finally:
                try:
                    io.close()
                except Exception as error:
                    state = (
                        "committed"
                        if getattr(io, "_committed", False)
                        else "not committed"
                    )
                    raise MillefeuilleContractError(
                        "Windows metadata " + state + "; cleanup uncertain"
                    ) from error
    except Exception:
        # lock acquisition can fail before the inner cleanup scope.
        if not getattr(io, "_closed", False):
            io.close()
        raise


@contextmanager
def prototype_read_guard(io):
    with io.lock():
        identity = io.root_identity()
        if io.exists((PENDING,)):
            raise MillefeuilleContractError(
                "Windows metadata pending; recovery required"
            )
        yield identity
        if io.exists((PENDING,)) or io.root_identity() != identity:
            raise MillefeuilleContractError(
                "Windows metadata observation drift/pending"
            )
