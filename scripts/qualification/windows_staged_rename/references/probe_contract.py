"""Offline diagnostic contract; it grants no Windows write qualification."""

import ctypes

STATUS_PENDING = 0x103
STATUS_SHARING_VIOLATION = 0xC0000043


class RenameInformation(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("root", ctypes.c_void_p),
        ("length", ctypes.c_uint32),
        ("name", ctypes.c_uint16 * 1),
    ]


def rename_rows():
    rows = []
    for kind in ("regular-file", "empty-directory"):
        for api, form, builder in (
            ("SetFileInformationByHandle:3", "root-null-absolute", "ctypes"),
            ("SetFileInformationByHandle:3", "held-root-leaf", "ctypes"),
            ("SetFileInformationByHandle:3", "held-root-leaf", "compiled-SDK"),
            ("NtSetInformationFile:10", "held-root-leaf", "ctypes"),
        ):
            rows.append(
                {
                    "id": f"{kind}:{api}:{form}:{builder}",
                    "kind": kind,
                    "api": api,
                    "destination_form": form,
                    "builder": builder,
                    "flags": 0,
                    "fresh_fixture": True,
                    "target_exists": False,
                }
            )
    for api in ("SetFileInformationByHandle:3", "NtSetInformationFile:10"):
        rows.append(
            {
                "id": f"collision:{api}",
                "kind": "regular-file",
                "api": api,
                "destination_form": "held-root-leaf",
                "builder": "ctypes",
                "flags": 0,
                "fresh_fixture": True,
                "target_exists": True,
                "requires_matching_positive_baseline": True,
            }
        )
    return rows


def alias_rows():
    variants = (
        ("CreateHardLinkW", "implementation-selected"),
        ("NtSetInformationFile:11", "READ_ATTRIBUTES|SYNCHRONIZE"),
        ("NtSetInformationFile:11", "zero-access-no-synchronous-open-option"),
    )
    return [
        {
            "id": f"alias:{api}:{access}:{hold}",
            "api": api,
            "data_handle_held": hold,
            "fresh_fixture": True,
            "source_access": access,
            "target_is_owned_synthetic_outside_run": True,
            "flags": 0,
        }
        for api, access in variants
        for hold in (False, True)
    ]


def pack_rename(root, name):
    """Byte-counted, zero replacement flags. Absolute forms are diagnostic only."""
    if not name or "\0" in name:
        raise ValueError("empty or NUL name")
    if root and (
        name in {".", ".."}
        or name.endswith((".", " "))
        or any(c in name for c in "\\/:")
    ):
        raise ValueError("held root requires one leaf")
    encoded = name.encode("utf-16-le")
    if len(encoded) > 65532:
        raise ValueError("bounded UTF-16 name exceeded")
    buf = ctypes.create_string_buffer(
        ctypes.sizeof(RenameInformation) + len(encoded) + 2
    )
    value = RenameInformation.from_buffer(buf)
    value.flags, value.root, value.length = 0, root, len(encoded)
    ctypes.memmove(
        ctypes.addressof(buf) + RenameInformation.name.offset, encoded, len(encoded)
    )
    return buf


def alias_assessment(record):
    """An unsafe success remains a counterexample, never an expected PASS."""
    status = record.get("ntstatus")
    if isinstance(status, int):
        status &= 0xFFFFFFFF
    if status == STATUS_PENDING:
        return "UNKNOWN_PENDING"
    if not record.get("data_handle_held"):
        return "BASELINE_OBSERVATION"
    if record.get("api_success"):
        if (
            record.get("source_file_id") == record.get("alias_file_id")
            and record.get("source_file_id") is not None
        ):
            return "COUNTEREXAMPLE_SAME_FILE"
        return "COUNTEREXAMPLE_OR_UNRESOLVED_BINDING"
    required = (
        "positive_baseline_passed",
        "data_open_denied_while_held",
        "granted_access_verified",
        "alias_absent_verified",
    )
    if all(record.get(k) is True for k in required) and (
        status == STATUS_SHARING_VIOLATION
        or (record.get("api") == "CreateHardLinkW" and record.get("win32_error") == 32)
    ):
        return "OBSERVED_REFUSAL_ONLY"
    return "UNKNOWN_REFUSAL_REASON_OR_MISSING_EVIDENCE"


def result_contract(records):
    assessments = [alias_assessment(r) for r in records]
    return {
        "diagnostic_assessments": assessments,
        "application_candidate_qualified": False,
        "native_namespace_exclusion_qualified": False,
        "counterexample_observed": any(
            s.startswith("COUNTEREXAMPLE") for s in assessments
        ),
        "execution_authorized": False,
    }
