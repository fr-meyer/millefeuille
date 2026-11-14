"""Finite mapped native runtime: Linux x86-64 only, zero post-filter FD I/O.

Trusted startup imports and capability probe precede payload access. Input is an
exact sealed memfd; output is a fixed 256 KiB shared mapping. After setup all FDs
close and seccomp denies ALL read/write/open/socket/clone/exec/dup/truncate and
file-backed mmap calls. Parent retains a hard 20-second kill/reap deadline.
"""

from dataclasses import dataclass
import hashlib
import json
import mmap
import os
import platform
import re
import subprocess
import sys
import time


@dataclass(frozen=True)
class NativeResourceLimits:
    cpu_seconds: int = 10
    wall_seconds: int = 20
    address_space_bytes: int = 268435456
    max_input_bytes: int = 1048576
    max_pages: int = 32
    max_read_bytes: int = 4194304
    max_write_bytes: int = 1048576
    max_output_bytes: int = 262144
    max_children: int = 1
    max_fds: int = 16


_CHILD = r"""import os, sys, mmap, ctypes, resource, hashlib, json, io, errno, time

try:
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
    import PyPDF2
    from PyPDF2.generic import ContentStream, NumberObject, FloatObject
    import math
    import zlib
    import encodings.utf_8
    import encodings.utf_16_be
    import encodings.latin_1
    import encodings.charmap

    def _scientific_number(value):
        number = float(value)
        if not math.isfinite(number) or abs(number) > 1e12:
            raise ValueError("scientific-number-out-of-bounds")
        return number

    hashlib.sha256(b"warmup").digest()
    json.dumps({"warmup": True}).encode("utf-8")
    control = int(sys.argv[1])
    infd = int(sys.argv[2])
    outfd = int(sys.argv[3])
    mode = sys.argv[4]
    out = mmap.mmap(outfd, 262144)

    class F(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        ]

    class P(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(F))]

    libc = ctypes.CDLL(None, use_errno=True)
    # Final filter: no FD I/O, filesystem, networking, processes, file-backed mmap.
    rules = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, 0xC000003E),
        (6, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
    ]
    # mmap: allow only MAP_PRIVATE|MAP_ANONYMOUS, fd=-1; disallow other flags.
    block = [
        (0x20, 0, 0, 40),
        (0x15, 0, 5, 34),
        (0x20, 0, 0, 48),
        (0x15, 0, 3, 0xFFFFFFFF),
        (0x20, 0, 0, 52),
        (0x15, 0, 1, 0xFFFFFFFF),
        (6, 0, 0, 0x7FFF0000),
        (6, 0, 0, 0x50001),
    ]
    rules += [(0x15, 0, len(block), 9)] + block + [(0x20, 0, 0, 0)]
    for nr in (
        10,
        11,
        12,
        13,
        14,
        15,
        24,
        28,
        60,
        96,
        158,
        186,
        202,
        218,
        228,
        231,
        273,
    ):
        rules += [(0x15, 0, 1, nr), (6, 0, 0, 0x7FFF0000)]
    rules += [(6, 0, 0, 0x50001)]
    arr = (F * len(rules))(*(F(*r) for r in rules))
    prog = P(len(rules), arr)
    if libc.prctl(38, 1, 0, 0, 0):
        raise RuntimeError()
    # Real disposable fork probe proves final filter installs BEFORE private bytes.
    probe = os.fork()
    if probe == 0:
        if libc.prctl(22, 2, ctypes.byref(prog), 0, 0):
            os._exit(78)
        os._exit(0)
    _, status = os.waitpid(probe, 0)
    if status:
        raise RuntimeError()
    os.write(control, b"CAPABLE\n")
    # Trusted setup receives only finite parent framing, not decoder input via IPC.
    header = b""
    while not header.endswith(b"\n") and len(header) < 100:
        b = os.read(control, 1)
        if not b:
            raise RuntimeError()
        header += b
    size, expected = header.decode("ascii").strip().split(" ")
    size = int(size)
    if (
        not header.endswith(b"\n")
        or not 0 < size <= 1048576
        or len(expected) != 64
        or any(c not in "0123456789abcdef" for c in expected)
    ):
        raise RuntimeError()
    import fcntl

    required = (
        fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    )
    if fcntl.fcntl(infd, fcntl.F_GET_SEALS) & required != required:
        raise RuntimeError()
    source = mmap.mmap(infd, size, access=mmap.ACCESS_READ)
    for key, value in (
        (resource.RLIMIT_CPU, 10),
        (resource.RLIMIT_AS, 268435456),
        (resource.RLIMIT_FSIZE, 0),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NPROC, 0),
    ):
        resource.setrlimit(key, (value, value))
    # Enumerate trusted startup FDs, then close ALL, including stdio/control/memfds.
    fds = [int(n) for n in os.listdir("/proc/self/fd")]
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass
    resource.setrlimit(resource.RLIMIT_NOFILE, (0, 0))
    if libc.prctl(22, 2, ctypes.byref(prog), 0, 0):
        os._exit(78)
    # READY is a memory marker, not a syscall or output pipe.
    out[:8] = b"ENFORCED"
    # No source content accessed before the final kernel filter is installed.
    raw = source[:]
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("source-hash-drift")
    if mode not in ("decode", "scientific"):
        denied = []
        for nr, args in (
            (0, (0, 0, 0)),
            (1, (1, 0, 0)),
            (2, (0, 0)),
            (41, (2, 1, 0)),
            (56, (0, 0, 0, 0, 0)),
            (32, (0,)),
            (77, (0, 0)),
            (9, (0, 4096, 3, 1, 0, 0)),
        ):
            ctypes.set_errno(0)
            value = libc.syscall(nr, *args)
            denied.append(value == -1 and ctypes.get_errno() == errno.EPERM)
        if mode == "memory":
            allocation = bytearray(536870912)
        if mode == "output":
            result = {"outcome": "probe", "oversized": "x" * 300000}
        elif mode == "spin":
            while True:
                pass
        else:
            result = {
                "outcome": "probe",
                "all_denied": all(denied),
                "probes": len(denied),
            }
    else:
        if not raw.startswith(b"%PDF-"):
            raise ValueError("unsupported-format")

        class BoundedMemory(io.BytesIO):
            charged = 0

            def read(self, n=-1):
                # Charge before decoder memory reads; no host filesystem I/O exists.
                amount = max(0, len(raw) - self.tell()) if n < 0 else n
                if self.charged + amount > 4194304:
                    raise ValueError("logical-read-budget-exhausted")
                self.charged += amount
                return super().read(n)

            def readinto(self, buffer):
                data = self.read(len(buffer))
                buffer[: len(data)] = data
                return len(data)

            def readline(self, n=-1):
                # PyPDF2 generally uses read/seek; still meter this alternate API.
                data = super().readline(n)
                self.charged += len(data)
                if self.charged > 4194304:
                    raise ValueError("logical-read-budget-exhausted")
                return data

        stream = BoundedMemory(raw)
        reader = PyPDF2.PdfReader(stream, strict=True)
        if reader.is_encrypted:
            raise ValueError("password-required")
        if not 1 <= len(reader.pages) <= 32:
            raise ValueError("page-count-out-of-bounds")
        pages = []
        total = 0
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            total += len(text.encode())
            if total > 131072:
                raise ValueError("extracted-text-too-large")
            pages.append(
                {
                    "page": number,
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
            )
        if mode == "scientific":
            # Authoritative operators, parsed only AFTER the kernel filter.
            observations = []
            for number, page in enumerate(reader.pages, 1):
                operations = []
                content = page.get_contents()
                if content is not None:
                    parsed_content = ContentStream(content, reader)
                    if len(parsed_content.operations) > 2048:
                        raise ValueError("scientific-operation-budget-exhausted")
                    for index, (operands, operator) in enumerate(
                        parsed_content.operations
                    ):
                        values = []
                        for value in operands:
                            if isinstance(
                                value, (int, float, NumberObject, FloatObject)
                            ):
                                values.append(_scientific_number(value))
                            elif isinstance(value, str):
                                values.append(str(value))
                            else:
                                values.append(None)
                        operations.append({
                            "index": index,
                            "operator": operator.decode("ascii"),
                            "operands": values,
                        })
                fonts = {}
                resources = page["/Resources"]
                for name, reference in resources.get("/Font", {}).items():
                    font = reference.get_object()
                    fonts[str(name)] = {
                        "base_font": str(font.get("/BaseFont", "")),
                        "encoding": str(font.get("/Encoding", "")),
                        "has_to_unicode": "/ToUnicode" in font,
                    }
                observations.append({
                    "page": number,
                    "rotation": int(page.get("/Rotate", 0)),
                    "media_box": [float(v) for v in page.mediabox],
                    "operations": operations,
                    "fonts": fonts,
                })
        result = {
            "outcome": "needs-review",
            "pages": pages,
            "source_sha256": expected,
            "tool": "PyPDF2/" + PyPDF2.__version__,
            "logical_read_bytes": stream.charged,
        }
        if mode == "scientific":
            result["scientific"] = {
                "schema": "millefeuille-pdf-operators/v1",
                "coordinate_system": "PDF user space, bottom-left; unnormalized",
                "pages": observations,
                "limitations": [
                    "operators are not rendered geometry",
                    "glyph semantics and mathematical meaning unresolved",
                    "no acceptance promotion",
                ],
            }
except BaseException as exc:
    safe = {
        "source-hash-drift",
        "unsupported-format",
        "password-required",
        "page-count-out-of-bounds",
        "extracted-text-too-large",
        "logical-read-budget-exhausted",
        "scientific-operation-budget-exhausted",
        "scientific-number-out-of-bounds",
    }
    reason = (
        str(exc) if type(exc) is ValueError and str(exc) in safe else "pdf-decode-error"
    )
    result = {"outcome": "refused", "reason": reason}
try:
    encoded = json.dumps(result, ensure_ascii=True).encode()
    if len(encoded) > 262096:
        encoded = b'{"outcome":"refused","reason":"output-budget-exhausted"}'
    out[48 : 48 + len(encoded)] = encoded
    out[12:44] = hashlib.sha256(encoded).digest()
    out[8:12] = len(encoded).to_bytes(4, "big")
    out[44:48] = b"DONE"
except BaseException:
    os._exit(79)
os._exit(0)
"""


def inspect_bounded_pdf(input_manifest, resource_limits=None, *, scientific=False):
    return _inspect_mapped_pdf(
        input_manifest, resource_limits, _probe="scientific" if scientific else "decode"
    )


def _inspect_mapped_pdf(input_manifest, resource_limits=None, *, _probe="decode"):
    result = {
        "schema": "millefeuille-native-decode-result/v1",
        "outcome": "refused",
        "reason": "full-native-isolation-adapter-unavailable",
        "limits_enforced": False,
        "exit_code": None,
        "signal": None,
        "child_reaped": True,
        "charged_read_bytes": 0,
        "charged_write_bytes": 0,
        "output_sha256": None,
        "evidence": {
            "input_sha256": None,
            "child_runtime": 0,
            "resource_setup_receipt": "unsupported-before-input",
            "termination_receipt": "no-child-created",
        },
    }
    if (
        sys.platform != "linux"
        or platform.machine() != "x86_64"
        or not hasattr(os, "memfd_create")
    ):
        return result
    if resource_limits is not None and resource_limits != NativeResourceLimits():
        result["reason"] = "unsupported-resource-policy"
        return result
    if (
        type(input_manifest) is not dict
        or set(input_manifest) != {"path", "sha256"}
        or not isinstance(input_manifest["sha256"], str)
        or re.fullmatch("[0-9a-f]{64}", input_manifest["sha256"]) is None
        or _probe
        not in ("decode", "scientific", "syscalls", "spin", "memory", "output")
    ):
        result["reason"] = "invalid-input-manifest"
        return result
    import fcntl

    from millefeuille.domain.secure_io import read_bytes_no_follow

    started = time.monotonic()
    child = None
    descriptors = []
    out = None

    def remaining():
        value = 20 - (time.monotonic() - started)
        if value <= 0:
            raise TimeoutError()
        return value

    try:
        infd = os.memfd_create("native-input", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        outfd = os.memfd_create("native-output", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        descriptors += [infd, outfd]
        os.ftruncate(outfd, 262144)
        fcntl.fcntl(
            outfd,
            fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL,
        )
        out = mmap.mmap(outfd, 262144)
        # A socketpair is trusted pre-filter framing only. Closed before decoding.
        import socket

        left, right = socket.socketpair()
        try:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    _CHILD,
                    str(right.fileno()),
                    str(infd),
                    str(outfd),
                    _probe,
                ],
                pass_fds=(right.fileno(), infd, outfd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={},
                close_fds=True,
                start_new_session=True,
            )
            right.close()
            left.settimeout(remaining())
            receipt = b""
            while not receipt.endswith(b"\n") and len(receipt) < 16:
                chunk = left.recv(1)
                if not chunk:
                    raise ValueError("sandbox-unavailable-before-input")
                receipt += chunk
            if receipt != b"CAPABLE\n":
                raise ValueError("sandbox-unavailable-before-input")
            raw = read_bytes_no_follow(
                input_manifest["path"], "native PDF", max_bytes=1048576
            )
            digest = hashlib.sha256(raw).hexdigest()
            result["charged_read_bytes"] = len(raw)
            result["evidence"]["input_sha256"] = digest
            if digest != input_manifest["sha256"]:
                raise ValueError("source-hash-drift")
            if not raw:
                raise ValueError("unsupported-format")
            offset = 0
            while offset < len(raw):
                offset += os.write(infd, raw[offset:])
            fcntl.fcntl(
                infd,
                fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE
                | fcntl.F_SEAL_GROW
                | fcntl.F_SEAL_SHRINK
                | fcntl.F_SEAL_SEAL,
            )
            left.settimeout(remaining())
            left.sendall(str(len(raw)).encode() + b" " + digest.encode() + b"\n")
        finally:
            left.close()
            right.close()
        child.wait(timeout=remaining())
        if out[:8] != b"ENFORCED":
            raise ValueError("sandbox-enforcement-failed")
        result["limits_enforced"] = True
        result["evidence"]["resource_setup_receipt"] = "seccomp-zero-io-mapped/v1"
        if child.returncode != 0:
            raise ValueError("decoder-resource-or-process-failure")
        size = int.from_bytes(out[8:12], "big")
        if out[44:48] != b"DONE" or not 0 < size <= 262096:
            raise ValueError("invalid-output-frame")
        encoded = out[48 : 48 + size]
        if hashlib.sha256(encoded).digest() != out[12:44]:
            raise ValueError("invalid-output-frame")
        parsed = json.loads(encoded.decode("utf-8"))
        result.update(parsed)
        result["charged_write_bytes"] = size + 48
        result["output_sha256"] = hashlib.sha256(encoded).hexdigest()
        if parsed["outcome"] != "refused":
            result.pop("reason", None)
    except (TimeoutError, subprocess.TimeoutExpired):
        result["reason"] = "wall-budget-exhausted"
    except BaseException as exc:
        safe = {
            "sandbox-unavailable-before-input",
            "source-hash-drift",
            "unsupported-format",
            "sandbox-enforcement-failed",
            "decoder-resource-or-process-failure",
            "invalid-output-frame",
        }
        result["reason"] = (
            str(exc)
            if type(exc) is ValueError and str(exc) in safe
            else "native-boundary-failure"
        )
    finally:
        if child is not None:
            killed = child.poll() is None
            if killed:
                child.kill()
            child.wait()
            result["exit_code"] = child.returncode if child.returncode >= 0 else None
            result["signal"] = -child.returncode if child.returncode < 0 else None
            result["evidence"]["termination_receipt"] = (
                "killed-and-reaped" if killed else "exited-and-reaped"
            )
            if out is not None and out[:8] == b"ENFORCED":
                result["limits_enforced"] = True
                result["evidence"]["resource_setup_receipt"] = (
                    "seccomp-zero-io-mapped/v1"
                )
        if out is not None:
            out.close()
        for fd in descriptors:
            os.close(fd)
        result["evidence"]["child_runtime"] = time.monotonic() - started
        result["evidence"]["post_filter_fd_io_bytes"] = 0
        result["evidence"]["io_policy"] = (
            "sealed-input<=1MiB;fixed-output<=256KiB;no-syscall-io"
        )
    return result
