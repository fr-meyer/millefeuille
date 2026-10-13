"""Explicitly invoked owned-synthetic-root native rename diagnostic; no app wiring."""

import argparse
import ctypes
import hashlib
import json
import os
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).absolute().parent))

from build_manifest import plain_path, read_plain
from diagnostic_core import FILE_CAP, record_bytes, run_ten_cases, validate_manifest
from references.probe_contract import pack_rename
from staged_replacement import Refusal

U32 = ctypes.c_uint32
I32 = ctypes.c_int32
U64 = ctypes.c_uint64
HANDLE = ctypes.c_void_p
VOID = ctypes.c_void_p
DELETE = 0x10000
READ_ATTRIBUTES = 0x80
SYNCHRONIZE = 0x100000
GENERIC_READ = 0x80000000
PARENT_TRANSITION = 0x2 | 0x4 | 0x20
OPEN_REPARSE = 0x00200000
BACKUP_SEMANTICS = 0x02000000
STATUS_PENDING = 0x103
LIVE_PENDING_REQUESTS = []


class IOSBStatus(ctypes.Union):
    _fields_ = [("status", I32), ("pointer", VOID)]


class IOSB(ctypes.Structure):
    _fields_ = [("value", IOSBStatus), ("information", ctypes.c_size_t)]


class FileIdInfo(ctypes.Structure):
    _fields_ = [("volume", U64), ("identity", ctypes.c_ubyte * 16)]


class FileStandardInfo(ctypes.Structure):
    _fields_ = [
        ("allocation", ctypes.c_int64),
        ("size", ctypes.c_int64),
        ("links", U32),
        ("delete_pending", ctypes.c_ubyte),
        ("directory", ctypes.c_ubyte),
    ]


class AttributeTag(ctypes.Structure):
    _fields_ = [("attributes", U32), ("tag", U32)]


class NativeBackend:
    native_execution = True

    def __init__(self, root, sdk_dll, *, deadline):
        if os.name != "nt" or sys.platform != "win32" or sys.maxsize <= 2**32:
            raise Refusal("requires native Windows AMD64")
        self.root, self.deadline = root, deadline
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.nt = ctypes.WinDLL("ntdll")
        try:
            self.sdk = ctypes.WinDLL(str(sdk_dll), use_last_error=True)
        except OSError as error:
            self.sdk = None
            self.sdk_load_error = str(error)
        self.owned_handles = []
        self.closed_handles = []
        self.failed_handles = set()
        self.poisoned = False
        self.case_evidence = {}
        self.bind(
            self.kernel,
            "CreateFileW",
            HANDLE,
            [ctypes.c_wchar_p, U32, U32, VOID, U32, U32, HANDLE],
        )
        self.bind(self.kernel, "CloseHandle", I32, [HANDLE])
        self.bind(
            self.kernel, "GetFileInformationByHandleEx", I32, [HANDLE, I32, VOID, U32]
        )
        self.bind(
            self.kernel, "ReadFile", I32, [HANDLE, VOID, U32, ctypes.POINTER(U32), VOID]
        )
        self.bind(
            self.kernel, "SetFilePointerEx", I32, [HANDLE, ctypes.c_int64, VOID, U32]
        )
        self.bind(
            self.kernel, "SetFileInformationByHandle", I32, [HANDLE, I32, VOID, U32]
        )
        self.bind(self.kernel, "WaitForSingleObject", U32, [HANDLE, U32])
        self.bind(
            self.nt, "NtQueryObject", I32, [HANDLE, U32, VOID, U32, ctypes.POINTER(U32)]
        )
        self.bind(
            self.nt,
            "NtSetInformationFile",
            I32,
            [HANDLE, ctypes.POINTER(IOSB), VOID, U32, U32],
        )
        self.sdk_layout = {
            "status": "load-failed",
            "values": [],
            "expected": [24, 8, 16, 20, 8, 2, 4],
            "load_error": getattr(self, "sdk_load_error", None),
        }
        if self.sdk is not None:
            try:
                self.bind(self.sdk, "mf_sdk_layout", U32, [ctypes.POINTER(U32), U32])
                self.bind(
                    self.sdk,
                    "mf_sdk_rename",
                    I32,
                    [
                        HANDLE,
                        HANDLE,
                        ctypes.c_wchar_p,
                        U32,
                        ctypes.POINTER(U32),
                        ctypes.POINTER(U32),
                    ],
                )
                layout = (U32 * 7)()
                code = self.sdk.mf_sdk_layout(layout, 7)
                self.sdk_layout.update({"status": int(code), "values": list(layout)})
            except AttributeError as error:
                self.sdk_layout["load_error"] = str(error)

    @staticmethod
    def bind(library, name, restype, argtypes):
        function = getattr(library, name)
        function.restype, function.argtypes = restype, argtypes

    @staticmethod
    def checked(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def open(self, path, *, directory=False, move=False, parent_transition=False):
        plain_path(path, directory=directory)
        access = (
            READ_ATTRIBUTES
            | SYNCHRONIZE
            | (DELETE if move else 0)
            | (GENERIC_READ if not directory else 0)
            | (PARENT_TRANSITION if parent_transition else 0)
        )
        flags = OPEN_REPARSE | (BACKUP_SEMANTICS if directory else 0)
        handle = self.kernel.CreateFileW(str(path), access, 7, None, 3, flags, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        self.owned_handles.append(handle)
        raw = ctypes.create_string_buffer(56)
        used = U32()
        status = (
            self.nt.NtQueryObject(handle, 0, raw, len(raw), ctypes.byref(used))
            & 0xFFFFFFFF
        )
        if status != 0 or used.value < 8 or used.value > len(raw):
            raise Refusal(f"granted-access query failed: {status:08x}")
        granted = U32.from_buffer(raw, 4).value
        self.case_evidence.setdefault("access_readbacks", []).append(
            {
                "path": str(path),
                "requested": access,
                "granted": granted,
                "query_ntstatus": status,
                "query_bytes": used.value,
                "sharing": 7,
                "flags": flags,
            }
        )
        return handle, {
            "requested": access,
            "granted": granted,
            "query_ntstatus": status,
            "query_bytes": used.value,
            "sharing": 7,
            "flags": flags,
        }

    def close(self, handle):
        if handle is not None and handle in self.owned_handles:
            value = self.kernel.CloseHandle(handle)
            if not value:
                self.failed_handles.add(handle)
                self.poisoned = True
                raise ctypes.WinError(ctypes.get_last_error())
            self.owned_handles.remove(handle)
            self.closed_handles.append(handle)

    def snapshot(self, handle, path, *, directory=False):
        identity, standard, attributes = (
            FileIdInfo(),
            FileStandardInfo(),
            AttributeTag(),
        )
        for kind, value in ((18, identity), (1, standard), (9, attributes)):
            self.checked(
                self.kernel.GetFileInformationByHandleEx(
                    handle, kind, ctypes.byref(value), ctypes.sizeof(value)
                )
            )
        self.case_evidence["last_raw_object_information"] = {
            "volume": identity.volume,
            "file_id": bytes(identity.identity).hex(),
            "links": standard.links,
            "size": standard.size,
            "directory": bool(standard.directory),
            "attributes": attributes.attributes,
            "reparse_tag": attributes.tag,
        }
        if attributes.attributes & 0x400 or attributes.tag:
            raise Refusal("reparse object refused")
        if (
            standard.links != 1
            or bool(standard.directory) != directory
            or standard.size < 0
        ):
            raise Refusal("object kind/link binding refused")
        if directory:
            data = None
            empty = not any(path.iterdir())
        else:
            if standard.size > 4096:
                raise Refusal("synthetic fixture exceeded 4096 bytes")
            self.checked(self.kernel.SetFilePointerEx(handle, 0, None, 0))
            buffer, used = ctypes.create_string_buffer(4097), U32()
            self.checked(
                self.kernel.ReadFile(handle, buffer, 4097, ctypes.byref(used), None)
            )
            data, empty = buffer.raw[: used.value], None
            if len(data) != standard.size:
                raise Refusal("held file read size mismatch")
        return {
            "volume": identity.volume,
            "file_id": bytes(identity.identity).hex(),
            "kind": "empty-directory" if directory else "regular-file",
            "links": standard.links,
            "bytes": 0 if directory else len(data),
            "sha256": None if directory else hashlib.sha256(data).hexdigest(),
            "reparse": False,
            "empty": empty,
            "attributes": attributes.attributes,
            "delete_pending": bool(standard.delete_pending),
        }

    def by_name(self, path, *, directory=False):
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        handle, _access = self.open(path, directory=directory)
        try:
            return self.snapshot(handle, path, directory=directory)
        finally:
            self.close(handle)

    def prepare(self, number, row):
        self.case_evidence = {"sdk_layout": self.sdk_layout}
        if self.poisoned or LIVE_PENDING_REQUESTS:
            raise Refusal(
                "prior unresolved operation/handle custody; no further native cases"
            )
        if time.monotonic() >= self.deadline:
            raise Refusal("native worker deadline reached")
        parent = self.root / f"case-{number:02d}"
        parent.mkdir()
        source_parent, destination_parent = (
            parent / "source-parent",
            parent / "destination-parent",
        )
        source_parent.mkdir()
        destination_parent.mkdir()
        source, target = source_parent / "source", destination_parent / "target"
        directory = row["kind"] == "empty-directory"
        if directory:
            source.mkdir()
        else:
            with source.open("xb") as output:
                output.write(("owned-source:" + row["id"] + "\n").encode())
        if row["target_exists"]:
            with (destination_parent / "TaRgEt").open("xb") as output:
                output.write(b"owned-foreign-collision-target\n")
        source_parent_handle, source_parent_access = self.open(
            source_parent, directory=True, parent_transition=True
        )
        parent_handle, parent_access = self.open(
            destination_parent, directory=True, parent_transition=True
        )
        source_handle, source_access = self.open(source, directory=directory, move=True)
        return {
            "parent": destination_parent,
            "source_parent": source_parent,
            "source": source,
            "target": target,
            "directory": directory,
            "parent_handle": parent_handle,
            "source_parent_handle": source_parent_handle,
            "source_handle": source_handle,
            "source_access": source_access,
            "parent_access": parent_access,
            "source_parent_access": source_parent_access,
            "row": row,
        }

    def inspect(self, fixture):
        if LIVE_PENDING_REQUESTS:
            return {
                "unresolved_pending": True,
                "reconciliation_deferred_until_owned_process_termination": True,
            }
        held_path = (
            fixture["source"] if fixture["source"].exists() else fixture["target"]
        )
        parent = self.snapshot(
            fixture["parent_handle"], fixture["parent"], directory=True
        )
        source_parent = self.snapshot(
            fixture["source_parent_handle"], fixture["source_parent"], directory=True
        )
        return {
            "source": self.by_name(fixture["source"], directory=fixture["directory"]),
            "target": self.by_name(fixture["target"], directory=fixture["directory"]),
            "held_source": self.snapshot(
                fixture["source_handle"], held_path, directory=fixture["directory"]
            ),
            "parents": {
                "source_parent": binding_parent(source_parent),
                "destination_parent": binding_parent(parent),
            },
            "granted_delete": bool(fixture["source_access"]["granted"] & DELETE),
            "granted_parent_transition": fixture["parent_access"]["granted"]
            & PARENT_TRANSITION
            == PARENT_TRANSITION,
            "settings": {
                "source_access": fixture["source_access"],
                "parent_access": fixture["parent_access"],
                "source_parent_access": fixture["source_parent_access"],
            },
            "source_path": str(fixture["source"]),
            "destination_path": str(fixture["target"]),
            "collision_created_spelling": "TaRgEt"
            if fixture["row"]["target_exists"]
            else None,
            "sdk_layout": self.sdk_layout,
        }

    def rename(self, fixture, row):
        if self.poisoned or LIVE_PENDING_REQUESTS:
            raise Refusal(
                "prior unresolved operation/handle custody; no native dispatch"
            )
        if time.monotonic() >= self.deadline:
            raise Refusal("native worker deadline reached")
        relative = row["destination_form"] == "held-root-leaf"
        root = fixture["parent_handle"] if relative else 0
        name = "target" if relative else str(fixture["target"])
        payload = pack_rename(root, name)
        call = {
            "api": row["api"],
            "builder": row["builder"],
            "buffer_bytes": len(payload),
            "buffer_sha256": hashlib.sha256(payload.raw).hexdigest(),
            "flags": 0,
            "root_handle_supplied": bool(root),
            "name_utf16_bytes": len(name.encode("utf-16-le")),
            "bool": None,
            "win32_error": None,
            "ntstatus": None,
            "iosb_status": None,
            "completion_status": None,
            "completion_confirmed": False,
            "unresolved_pending": False,
        }
        if row["builder"] == "compiled-SDK":
            if (
                self.sdk_layout["status"] != 0
                or self.sdk_layout["values"] != self.sdk_layout["expected"]
            ):
                raise Refusal("installed SDK layout did not match AMD64 candidate")
            error, count = U32(), U32()
            value = self.sdk.mf_sdk_rename(
                fixture["source_handle"],
                root,
                name,
                len(name.encode("utf-16-le")),
                ctypes.byref(error),
                ctypes.byref(count),
            )
            call.update(
                {
                    "bool": bool(value),
                    "win32_error": None if value else error.value,
                    "sdk_buffer_bytes": count.value,
                    "sdk_output_error": None if value else error.value,
                }
            )
        elif row["api"].startswith("SetFile"):
            value = self.kernel.SetFileInformationByHandle(
                fixture["source_handle"], 3, payload, len(payload)
            )
            error = None if value else ctypes.get_last_error()
            call.update({"bool": bool(value), "win32_error": error})
        else:
            iosb = IOSB()
            iosb.value.status = -559038737
            # Registration/allocation must happen before any kernel effect.
            # A dispatch/wait/status exception leaves this uncertain request
            # retained for unconditional owned-process exit, without retry.
            request = {
                "iosb": iosb,
                "buffer": payload,
                "source_handle": fixture["source_handle"],
            }
            LIVE_PENDING_REQUESTS.append(request)
            value = (
                self.nt.NtSetInformationFile(
                    fixture["source_handle"],
                    ctypes.byref(iosb),
                    payload,
                    len(payload),
                    10,
                )
                & 0xFFFFFFFF
            )
            wait = None
            if value == STATUS_PENDING:
                wait = self.kernel.WaitForSingleObject(fixture["source_handle"], 1000)
            status = iosb.value.status & 0xFFFFFFFF
            complete = (
                value != STATUS_PENDING
                or wait == 0
                and status != STATUS_PENDING
                and status != 0xDEADBEEF
            )
            if complete:
                LIVE_PENDING_REQUESTS.remove(request)
            else:
                self.poisoned = True
            call.update(
                {
                    "ntstatus": value,
                    "iosb_status": status,
                    "iosb_information": iosb.information,
                    "pending_wait_result": wait,
                    "completion_confirmed": complete,
                    "completion_status": status if value == STATUS_PENDING else value,
                    "unresolved_pending": not complete,
                }
            )
        return call

    def close_fixture(self, fixture):
        # Also reconciles handles opened before prepare failed to return.
        if LIVE_PENDING_REQUESTS:
            return  # Kernel may still use retained IOSB/buffer; process termination owns settlement.
        errors = []
        for handle in list(reversed(self.owned_handles)):
            if handle in self.failed_handles:
                errors.append("prior close remains uncertain; no retry")
                continue
            try:
                self.close(handle)
            except Exception as error:  # noqa: BLE001 - Preserve every owned handle failure.
                errors.append(str(error))
        if errors:
            raise Refusal("owned handle closes failed: " + "; ".join(errors))


def binding_parent(value):
    return {key: value[key] for key in ("volume", "file_id", "links", "reparse")}


def execute_worker(backend, evidence_root, *, started, receipt_bindings):
    """Pending request storage must outlive every reporting failure."""
    try:
        result = run_ten_cases(backend)
        result.update(receipt_bindings)
        result.update(
            {
                "pid": os.getpid(),
                "python": sys.version,
                "platform": sys.platform,
                "elapsed_seconds": time.monotonic() - started,
                "sdk_layout": backend.sdk_layout,
                "owned_file_handles_remaining": len(backend.owned_handles),
                "owner_label_is_not_human_authorization": True,
                "live_pending_request_buffers": len(LIVE_PENDING_REQUESTS),
            }
        )
        with (evidence_root / "worker-result.json").open("xb") as output:
            output.write(record_bytes(result))
            output.flush()
        return (
            0
            if result["failed"] == 0
            and not backend.owned_handles
            and time.monotonic() - started < 290
            else 1
        )
    finally:
        if LIVE_PENDING_REQUESTS:
            # Serialization/open/write/flush failures cannot unwind Python and
            # destroy buffers still referenced by a pending kernel request.
            try:
                for stream in (sys.stdout, sys.stderr):
                    try:
                        stream.flush()
                    except BaseException:  # noqa: BLE001, S110 - Logging can fail too; exit custody is unconditional.
                        pass
            finally:
                os._exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--authorize-owned-synthetic-native-diagnostics", action="store_true"
    )
    parser.add_argument("--synthetic-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-manifest-sha256", required=True)
    parser.add_argument("--sdk-dll", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    if (
        not args.authorize_owned_synthetic_native_diagnostics
        or os.name != "nt"
        or sys.platform != "win32"
        or sys.version_info[:2] != (3, 13)
        or sys.maxsize <= 2**32
    ):
        raise Refusal(
            "explicit authorized owned-synthetic-root Windows AMD64/Python3.13 invocation required"
        )
    if (
        str(args.source_root).casefold()
        != str(Path(__file__).absolute().parent).casefold()
    ):
        raise Refusal("executed worker root must match pinned source root")
    started = time.monotonic()
    plain_path(args.synthetic_root, directory=True)
    marker = json.loads(read_plain(args.synthetic_root / "owner-marker.json", FILE_CAP))
    if (
        marker.get("nonce") != args.nonce
        or marker.get("expected_head") != args.expected_head
        or marker.get("schema") != "millefeuille.owned-native-diagnostic-root/v1"
    ):
        raise Refusal("owned synthetic root invocation binding mismatch")
    root_info = args.synthetic_root.lstat()
    if marker.get("root_identity") != [root_info.st_dev, root_info.st_ino]:
        raise Refusal("synthetic root identity changed")
    raw = read_plain(args.source_manifest, FILE_CAP)
    if hashlib.sha256(raw).hexdigest() != args.source_manifest_sha256:
        raise Refusal("manifest digest mismatch")
    manifest = json.loads(raw)
    if manifest["sdk"]["path"].casefold() != str(args.sdk_dll).casefold():
        raise Refusal("SDK DLL path binding mismatch")
    validate_manifest(
        manifest,
        expected_head=args.expected_head,
        source_reader=lambda relative: read_plain(
            args.source_root / relative, FILE_CAP
        ),
        sdk_reader=lambda: read_plain(args.sdk_dll, 524288),
    )
    from native_controller import require_worker_job

    require_worker_job()
    sys.stdout = (args.synthetic_root / "stdout.log").open("x", encoding="utf-8")
    sys.stderr = (args.synthetic_root / "stderr.log").open("x", encoding="utf-8")

    def audit(event, _arguments):
        if event.startswith(("socket.", "subprocess.")) or event in {
            "os.system",
            "os.spawn",
            "os.exec",
            "os.posix_spawn",
        }:
            raise Refusal("worker forbids network and additional processes")

    sys.addaudithook(audit)
    backend = NativeBackend(args.synthetic_root, args.sdk_dll, deadline=started + 290)
    return execute_worker(
        backend,
        args.synthetic_root,
        started=started,
        receipt_bindings={
            "source_manifest_sha256": args.source_manifest_sha256,
            "expected_head": args.expected_head,
            "sdk_sha256": manifest["sdk"]["sha256"],
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
