"""Pure request preparation. No Windows API dispatch or activation is supplied."""

import struct
from dataclasses import asdict, dataclass

from staged_replacement import PENDING, Refusal, safe_parts

DELETE = 0x00010000
FILE_READ_ATTRIBUTES = 0x00000080
SYNCHRONIZE = 0x00100000
OPERATION_KINDS = {
    "old-to-journal": "regular-file",
    "stage-to-live": "regular-file",
    "rollback-new-to-journal": "regular-file",
    "rollback-old-to-live": "regular-file",
    "barrier-to-committed-journal": "empty-directory",
    "barrier-to-rolled-back-journal": "empty-directory",
    "barrier-to-aborted-history": "empty-directory",
    "barrier-restore-to-run": "empty-directory",
}


@dataclass(frozen=True)
class NativeObjectBinding:
    volume_serial: int
    file_id_128: str
    kind: str
    links: int
    bytes: int
    sha256: str | None

    def validate(self):
        if not 0 <= self.volume_serial < 2**64:
            raise Refusal("invalid volume serial")
        if len(self.file_id_128) != 32 or any(
            c not in "0123456789abcdef" for c in self.file_id_128
        ):
            raise Refusal("expected 128-bit native file ID")
        if self.kind not in ("regular-file", "empty-directory") or self.links != 1:
            raise Refusal("unqualified native object kind/link binding")
        if self.kind == "regular-file" and (
            self.bytes < 0
            or self.sha256 is None
            or len(self.sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.sha256)
        ):
            raise Refusal("invalid regular-file byte binding")
        if self.kind == "empty-directory" and (
            self.bytes != 0 or self.sha256 is not None
        ):
            raise Refusal("directory move is restricted to an empty owned barrier")


@dataclass(frozen=True)
class NativeMoveRequest:
    operation: str
    api: str
    source_handle_token: int
    source_parent_handle_token: int
    destination_parent_handle_token: int
    source_binding: NativeObjectBinding
    source_parent_file_id_128: str
    destination_parent_file_id_128: str
    source_leaf: str
    destination_leaf: str
    run_root_handle_token: int = 0
    run_file_id_128: str = ""

    def validate(self):
        if OPERATION_KINDS.get(self.operation) != self.source_binding.kind:
            raise Refusal("operation must match a fixed staged protocol kind")
        if self.api not in (
            "NtSetInformationFile:10",
            "SDK:SetFileInformationByHandle:3",
        ):
            raise Refusal("unqualified rename API request")
        for token in (
            self.source_handle_token,
            self.source_parent_handle_token,
            self.destination_parent_handle_token,
            self.run_root_handle_token,
        ):
            if not 0 < token < 2**64:
                raise Refusal("nonzero AMD64 held handle token required")
        for side, leaf in (
            ("source", self.source_leaf),
            ("destination", self.destination_leaf),
        ):
            if leaf == PENDING:
                permitted = self.source_binding.kind == "empty-directory" and (
                    side == "source"
                    and self.operation.startswith("barrier-to-")
                    or side == "destination"
                    and self.operation == "barrier-restore-to-run"
                )
                if not permitted:
                    raise Refusal(
                        "pending leaf is reserved for the owned barrier transition"
                    )
                continue
            if len(safe_parts(leaf)) != 1:
                raise Refusal("native move requires one relative leaf")
        for identity in (
            self.source_parent_file_id_128,
            self.destination_parent_file_id_128,
            self.run_file_id_128,
        ):
            if len(identity) != 32 or any(
                c not in "0123456789abcdef" for c in identity
            ):
                raise Refusal("invalid expected parent native ID")
        self.source_binding.validate()

    def packet(self):
        self.validate()
        return {
            **asdict(self),
            "schema": "millefeuille.staged-windows-move-request/unqualified-v1",
            "replace_if_exists": False,
            "flags": 0,
            "destination_form": "held-root-leaf",
            "source_minimum_requested_access": DELETE
            | FILE_READ_ATTRIBUTES
            | SYNCHRONIZE,
            "source_granted_DELETE_must_be_read_back": True,
            "case_insensitive_destination_collision_must_refuse": True,
            "owned_barrier_creation_receipt_required": self.source_binding.kind
            == "empty-directory",
            "required_positive_baseline": "same API, kind, handle access, sharing, parent form and buffer layout",
            "required_readback": [
                "root and parent identities/reparse state stable",
                "source and destination bound to the admitted volume/run and cooperating-owner clearance",
                "empty directory must match its owned creation receipt and remain empty",
                "held source handle matches expected volume/FileIdInfo",
                "source name still binds expected object immediately before move",
                "destination absent before move and no replace flag used",
                "record BOOL/meaningful LastError or NTSTATUS and IO_STATUS_BLOCK",
                "STATUS_PENDING is unresolved, never success",
                "on every result reconcile source/destination identities, link counts and exact bytes",
                "late error after completed move remains owned uncertain state; never blind retry",
                "file/directory flush acknowledgements recorded separately from crash durability",
            ],
            "native_qualified": False,
            "native_execution_authorized": False,
            "production_windows_writes_enabled": False,
        }

    def candidate_amd64_buffer(self) -> bytes:
        """Unqualified byte packing; requires SDK/native readback before use.

        Expected offsets: root8, byte length16, UTF-16 name20, sizeof24. The
        retained SDK reference must independently establish the actual layout.
        A correctly packed buffer alone does not establish rename support.
        """
        self.validate()
        name = self.destination_leaf.encode("utf-16-le")
        payload = bytearray(24 + len(name) + 2)
        struct.pack_into("<I", payload, 0, 0)
        struct.pack_into("<Q", payload, 8, self.destination_parent_handle_token)
        struct.pack_into("<I", payload, 16, len(name))
        payload[20 : 20 + len(name)] = name
        return bytes(payload)


class UnqualifiedNativeAdapter:
    def move(self, request: NativeMoveRequest):
        request.validate()
        raise Refusal(
            "native staged replacement is unqualified; dispatch remains disabled"
        )

    def flush(self, *_args):
        raise Refusal("native file/directory durability contract remains unqualified")
