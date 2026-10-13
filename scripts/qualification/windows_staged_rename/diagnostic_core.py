"""Pure ten-case assessment, manifest and artifact contracts. No native dispatch."""

import hashlib
import io
import json
import re
import zipfile

from references.probe_contract import rename_rows
from staged_replacement import Refusal, safe_parts

ARTIFACT_CAP = 1048576
FILE_CAP = 131072
MAX_FILES = 128
SOURCE_FILES = (
    "diagnostic_core.py",
    "native_worker.py",
    "native_controller.py",
    "worker_diagnostics.py",
    "build_manifest.py",
    "staged_replacement.py",
    "native_contract.py",
    "references/__init__.py",
    "references/probe_contract.py",
    "references/sdk_rename_reference.c",
)
SCHEMA = "millefeuille.windows-rename-diagnostic-source-manifest/v1"


def validate_manifest(manifest, *, expected_head, source_reader, sdk_reader):
    if manifest.get("schema") != SCHEMA or not re.fullmatch(
        "[0-9a-f]{40}", expected_head
    ):
        raise Refusal("manifest schema/head invalid")
    if manifest.get("expected_head") != expected_head:
        raise Refusal("manifest head binding mismatch")
    records = manifest.get("files", [])
    if len(records) != len(SOURCE_FILES) or {r.get("path") for r in records} != set(
        SOURCE_FILES
    ):
        raise Refusal("manifest requires the exact diagnostic source files")
    for record in records:
        data = source_reader(record["path"])
        if (
            not 0 < len(data) <= FILE_CAP
            or len(data) != record["bytes"]
            or hashlib.sha256(data).hexdigest() != record["sha256"]
        ):
            raise Refusal("diagnostic source byte binding mismatch")
    sdk = sdk_reader()
    if (
        not 0 < len(sdk) <= 524288
        or len(sdk) != manifest["sdk"]["bytes"]
        or hashlib.sha256(sdk).hexdigest() != manifest["sdk"]["sha256"]
    ):
        raise Refusal("compiled SDK DLL byte binding mismatch")


def binding(snapshot):
    if snapshot is None:
        return None
    fields = (
        "volume",
        "file_id",
        "kind",
        "links",
        "bytes",
        "sha256",
        "reparse",
        "empty",
    )
    return {field: snapshot.get(field) for field in fields}


def plain(snapshot, kind):
    if (
        snapshot is None
        or snapshot.get("kind") != kind
        or snapshot.get("links") != 1
        or snapshot.get("reparse") is not False
    ):
        return False
    if (
        not isinstance(snapshot.get("volume"), int)
        or not isinstance(snapshot.get("file_id"), str)
        or not re.fullmatch("[0-9a-f]{32}", snapshot["file_id"])
    ):
        return False
    if kind == "empty-directory":
        return (
            snapshot.get("empty") is True
            and snapshot.get("bytes") == 0
            and snapshot.get("sha256") is None
        )
    return (
        isinstance(snapshot.get("bytes"), int)
        and snapshot["bytes"] >= 0
        and isinstance(snapshot.get("sha256"), str)
        and re.fullmatch("[0-9a-f]{64}", snapshot["sha256"]) is not None
    )


def call_succeeded(call):
    if call.get("api", "").startswith("NtSet"):
        return (
            call.get("completion_confirmed") is True
            and call.get("completion_status") == 0
            and call.get("iosb_status") == 0
        )
    return call.get("bool") is True and call.get("win32_error") is None


def assess_case(row, before, call, after, *, positive_baseline=None):
    source, held = before.get("source"), before.get("held_source")
    after_held = after.get("held_source")
    is_collision = row["target_exists"]
    base = {
        "id": row["id"],
        "row": row,
        "before": before,
        "call": call,
        "after": after,
        "passed": False,
        "reconciliation": "ambiguous",
        "no_retry": True,
        "application_candidate_qualified": False,
    }
    same_held = binding(source) == binding(held) == binding(after_held)
    parents = before.get("parents", {})
    parent_bindings_valid = set(parents) == {
        "source_parent",
        "destination_parent",
    } and all(
        isinstance(p, dict)
        and p.get("links") == 1
        and p.get("reparse") is False
        and isinstance(p.get("file_id"), str)
        and re.fullmatch("[0-9a-f]{32}", p["file_id"])
        and p.get("volume") == (source or {}).get("volume")
        for p in parents.values()
    )
    parents_stable = parent_bindings_valid and parents == after.get("parents")
    admitted = (
        plain(source, row["kind"])
        and same_held
        and parents_stable
        and before.get("granted_delete") is True
        and before.get("granted_parent_transition") is True
    )
    committed = after.get("source") is None and binding(after.get("target")) == binding(
        source
    )
    unchanged = binding(after.get("source")) == binding(source) and binding(
        after.get("target")
    ) == binding(before.get("target"))
    if committed:
        base["reconciliation"] = (
            "completed-move" if call_succeeded(call) else "late-error-completed-move"
        )
    elif unchanged:
        base["reconciliation"] = "unchanged"
    if call.get("unresolved_pending"):
        base["reason"] = (
            "STATUS_PENDING not completed; preserve owned evidence without retry"
        )
    elif not is_collision:
        base["passed"] = (
            admitted
            and before.get("target") is None
            and committed
            and call_succeeded(call)
        )
        base["reason"] = (
            "positive baseline passed"
            if base["passed"]
            else "positive API/layout/access/binding baseline failed"
        )
    else:
        matching = (
            positive_baseline is not None
            and positive_baseline["passed"]
            and positive_baseline["before"].get("settings") == before.get("settings")
        )
        collision_status = (
            call.get("ntstatus") == 0xC0000035
            if call["api"].startswith("NtSet")
            else call.get("bool") is False and call.get("win32_error") in (80, 183)
        )
        base["positive_baseline_id"] = (
            None if positive_baseline is None else positive_baseline["id"]
        )
        target = before.get("target")
        foreign = plain(target, row["kind"]) and (
            target["volume"],
            target["file_id"],
        ) != (source["volume"], source["file_id"])
        base["passed"] = (
            admitted
            and matching
            and foreign
            and collision_status
            and unchanged
            and not call_succeeded(call)
        )
        base["reason"] = (
            "no-clobber collision passed"
            if base["passed"]
            else "collision lacks same-form positive proof or preserved bindings"
        )
    return base


def run_ten_cases(backend):
    records, positives = [], {}
    for number, row in enumerate(rename_rows()):
        fixture = None
        try:
            fixture = backend.prepare(number, row)
            before = backend.inspect(fixture)
            call = backend.rename(fixture, row)
            after = backend.inspect(fixture)
            baseline = positives.get(row["api"])
            record = assess_case(row, before, call, after, positive_baseline=baseline)
            if (
                not row["target_exists"]
                and row["kind"] == "regular-file"
                and row["destination_form"] == "held-root-leaf"
                and row["builder"] == "ctypes"
            ):
                positives[row["api"]] = record
        except Exception as error:  # noqa: BLE001 - Every missing native case is a failed case.
            record = {
                "id": row["id"],
                "row": row,
                "passed": False,
                "reason": f"{type(error).__name__}: {error}",
                "application_candidate_qualified": False,
            }
            record["admission_evidence"] = getattr(backend, "case_evidence", {})
        finally:
            try:
                backend.close_fixture(fixture)
            except Exception as error:  # noqa: BLE001 - Handle cleanup is mandatory evidence.
                record["passed"] = False
                record["handle_cleanup_error"] = f"{type(error).__name__}: {error}"
        records.append(record)
    if len(records) != 10:
        raise Refusal("exact ten-case contract changed")
    return {
        "schema": "millefeuille.ten-native-rename-cases/v1",
        "cases": records,
        "tests_run": 10,
        "passed": sum(r["passed"] for r in records),
        "failed": sum(not r["passed"] for r in records),
        "skips": 0,
        "native_execution": bool(backend.native_execution),
        "native_primitive_baselines_qualified": bool(
            backend.native_execution and all(r["passed"] for r in records)
        ),
        "application_candidate_qualified": False,
        "production_windows_writes_enabled": False,
        "hardlink_probe_cases": 0,
    }


def package_bytes(entries):
    if not entries or len(entries) > MAX_FILES:
        raise Refusal("artifact file count bound")
    if len({name.casefold() for name in entries}) != len(entries):
        raise Refusal("artifact case-insensitive collision")
    total = 0
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_STORED, allowZip64=False
    ) as archive:
        for name, data in sorted(entries.items()):
            safe_parts(name)
            if not isinstance(data, bytes) or len(data) > FILE_CAP:
                raise Refusal("artifact per-file bound")
            total += len(data)
            if total > ARTIFACT_CAP:
                raise Refusal("owned scratch byte cap")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            archive.writestr(info, data)
    data = output.getvalue()
    if total + len(data) > ARTIFACT_CAP:
        raise Refusal("combined owned scratch plus artifact exceeds 1MiB")
    return data


def record_bytes(value):
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(data) > FILE_CAP:
        raise Refusal("diagnostic receipt byte cap")
    return data
