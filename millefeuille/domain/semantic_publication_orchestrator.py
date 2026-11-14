"""Approved exact snapshots in a private single-cooperating-writer namespace.

Per-file durability, not multi-file atomicity or hostile same-UID protection.
Approval identity is an assertion supplied by the authority boundary, not a hash
based authentication claim. Unknown effects require reconciliation, never retry.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import stat
import time

from millefeuille.domain.acceptance_output_plan import json_bytes
from millefeuille.domain.exclusive_publication import (
    CONTRACT,
    _namespace,
    _observe,
    _parent,
    _private,
    publish_snapshot,
    rollback_snapshot,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.publication_journal import validate_snapshot
from millefeuille.domain.semantic_acceptance import digest


def plan_identity(exact_plan):
    """Bind every byte and preservation field, without changing reviewer receipts."""
    return digest(
        json_bytes(
            {
                "outputs": [
                    {
                        k: (v.hex() if isinstance(v, bytes) else v)
                        for k, v in asdict(item).items()
                    }
                    for item in exact_plan
                ]
            }
        )
    )


def namespace_binding(root, refs):
    """Read-only identity capture; refuses privileged execution and unsafe layout."""
    if os.name != "posix" or os.geteuid() == 0:
        raise Error("normal-UID POSIX private namespace required; no escalation")
    with _namespace(root, CONTRACT) as (fd, check):
        result = {"root": _private(fd, True), "parents": {}, "outputs": {}}
        for ref in refs:
            parent, name = _parent(fd, ref)
            try:
                result["parents"][ref] = _private(parent, True)
                result["outputs"][ref] = _observe(parent, name, 16 * 1024 * 1024)[0]
            finally:
                os.close(parent)
        check()
        return result


def publish_reviewed_plan(
    *,
    exact_plan,
    approved_scope,
    namespace,
    journal,
    input_binding,
    code_binding,
    approved_scope_identity,
    fault=None,
    input_admission=None,
    code_admission=None,
):
    """Consume one exact transaction reservation, including on uncertain failure.

    Caller authenticates approval out of band. Scope binds concrete input/code
    identities; callers must compute those from current reviewed inputs/code.
    No mkdir, owner repair, automatic retry or implicit rollback is performed.
    """
    if digest(json_bytes(approved_scope)) != approved_scope_identity:
        raise Error("publication approval hash drift")
    outputs = tuple(exact_plan)
    refs = [x.ref for x in outputs]
    root = Path(namespace)
    scope = approved_scope
    required = {
        "schema",
        "approval_identity",
        "approved",
        "root",
        "journal",
        "plan_identity",
        "input_binding",
        "code_binding",
        "namespace_binding",
        "approved_refs",
        "namespace_contract",
        "limits",
        "uses",
    }
    migrated = input_admission is not None or code_admission is not None
    if migrated:
        required |= {"input_admission", "code_admission"}
    if not isinstance(scope, dict) or set(scope) != required:
        raise Error("exact approved publication scope required")
    if (
        scope["schema"]
        != (
            "millefeuille-reviewed-publication-scope/v2"
            if migrated
            else "millefeuille-reviewed-publication-scope/v1"
        )
        or scope["approved"] is not True
        or scope["uses"] != 1
        or not isinstance(scope["approval_identity"], str)
        or not scope["approval_identity"].strip()
        or scope["root"] != str(root)
        or scope["journal"] != journal
        or scope["plan_identity"] != plan_identity(outputs)
        or not input_binding
        or scope["input_binding"] != input_binding
        or not code_binding
        or scope["code_binding"] != code_binding
        or scope["approved_refs"] != refs
        or scope["namespace_contract"] != CONTRACT
    ):
        raise Error("publication approval/input/code/plan binding drift")
    if migrated:
        for kind, admission in [("input", input_admission), ("code", code_admission)]:
            if (
                not isinstance(admission, WorkloadAdmission)
                or scope[kind + "_admission"] != admission.binding()
            ):
                raise Error("trusted workload admission/scope binding drift")
    verify_bound_manifest(input_binding, admission=input_admission)
    verify_bound_manifest(code_binding, admission=code_admission)
    limits = scope["limits"]
    ceilings = {
        "max_files": 64,
        "max_bytes": 16 * 1024 * 1024,
        "max_aux_paths": 130,
        "max_aux_bytes": 64 * 1024 * 1024,
        "max_journal_bytes": 4 * 1024 * 1024,
    }
    if (
        not isinstance(limits, dict)
        or set(limits) != set(ceilings)
        or any(
            type(limits[k]) is not int or not 0 < limits[k] <= v
            for k, v in ceilings.items()
        )
    ):
        raise Error("invalid cumulative publication reservations")
    if namespace_binding(root, refs) != scope["namespace_binding"]:
        raise Error("approved namespace inode/owner/mode drift")
    validate_snapshot(
        root=root,
        outputs=outputs,
        approved_refs=refs,
        max_files=limits["max_files"],
        max_bytes=limits["max_bytes"],
    )
    posts = publish_snapshot(
        root=root,
        outputs=outputs,
        approved_refs=refs,
        transaction_ref=journal,
        namespace_contract=CONTRACT,
        fault=fault,
        **limits,
    )
    return {
        "schema": "millefeuille-reviewed-publication-receipt/v1",
        "scope_identity": digest(json_bytes(scope)),
        "plan_identity": plan_identity(outputs),
        "root": str(root),
        "journal": journal,
        "posts": list(posts),
        "status": "published-needs-independent-readback",
    }


def verify_publication_readback(
    *, receipt, approved_scope, input_admission=None, code_admission=None
):
    """Run independently as the bound normal UID; hashes do not authenticate."""
    _verify_admitted_scope(approved_scope, input_admission, code_admission)
    if receipt["scope_identity"] != digest(json_bytes(approved_scope)):
        raise Error("readback approval drift")
    observed = namespace_binding(receipt["root"], approved_scope["approved_refs"])
    expected = approved_scope["namespace_binding"]
    if (
        observed["root"] != expected["root"]
        or observed["parents"] != expected["parents"]
        or list(observed["outputs"].values()) != receipt["posts"]
    ):
        raise Error("independent readback drift")
    return {
        "status": "pass",
        "scope_identity": receipt["scope_identity"],
        "uid": os.geteuid(),
        "plan_identity": receipt["plan_identity"],
    }


def rollback_reviewed_plan(
    *,
    approved_scope,
    rollback_approval,
    approved_rollback_identity,
    fault=None,
    input_admission=None,
    code_admission=None,
):
    """Explicit rollback only; uncertain intent or interrupted rollback refuses."""
    _verify_admitted_scope(approved_scope, input_admission, code_admission)
    expected = rollback_binding(approved_scope)
    if (
        not isinstance(rollback_approval, dict)
        or digest(json_bytes(rollback_approval)) != approved_rollback_identity
        or rollback_approval.get("binding") != expected
        or rollback_approval.get("operation") != "rollback"
        or not rollback_approval.get("approval_identity")
    ):
        raise Error("separate exact rollback approval required")
    if (
        namespace_binding(approved_scope["root"], [])["root"]
        != approved_scope["namespace_binding"]["root"]
    ):
        raise Error("rollback root drift")
    return rollback_snapshot(
        root=approved_scope["root"],
        transaction_ref=approved_scope["journal"],
        namespace_contract=CONTRACT,
        max_bytes=approved_scope["limits"]["max_bytes"],
        max_journal_bytes=approved_scope["limits"]["max_journal_bytes"],
        fault=fault,
    )


def verify_bound_manifest(manifest, *, admission=None):
    """Rehash finite approved no-follow files with inode/owner/mode binding."""
    from millefeuille.domain.secure_io import read_bytes_no_follow

    if admission is not None:
        return _verify_workload(manifest, admission)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"files"}
        or not isinstance(manifest["files"], list)
        or not 0 < len(manifest["files"]) <= 64
    ):
        raise Error("finite approved input/code manifest required")
    total = 0
    seen = set()
    for record in manifest["files"]:
        if not isinstance(record, dict) or set(record) != {
            "path",
            "sha256",
            "identity",
        }:
            raise Error("invalid binding record")
        path = Path(record["path"])
        if not path.is_absolute() or ".." in path.parts or str(path) in seen:
            raise Error("explicit unique manifest path required")
        seen.add(str(path))
        before = path.lstat()
        identity = [
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ]
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > 2097152
            or total + before.st_size > 16777216
            or record["identity"] != identity
        ):
            raise Error("approved input/code manifest drift: metadata or allocation")
        data = read_bytes_no_follow(path, "bound input/code", max_bytes=2097152)
        after = path.lstat()
        total += len(data)
        if (
            total > 16777216
            or identity
            != [
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_gid,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ]
            or record["identity"] != identity
            or digest(data) != record["sha256"]
        ):
            raise Error("approved input/code manifest drift")
    return digest(json_bytes(manifest))


def capture_bound_manifest(paths, *, admission=None):
    """Capture finite no-follow identities, refusing before the 65th metadata/read.

    Legacy callers retain64; a scoped workload uses independent admission.
    """
    from millefeuille.domain.secure_io import read_bytes_no_follow

    if admission is not None:
        return _capture_workload(paths, admission)
    records, seen, total = [], set(), 0
    for path in paths:
        if len(records) >= 64:
            raise Error("finite approved input/code manifest exceeds 64 files")
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts or str(path) in seen:
            raise Error("explicit unique manifest path required")
        seen.add(str(path))
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > 2097152
            or total + before.st_size > 16777216
        ):
            raise Error("manifest capture allocation or regular-file bound")
        payload = read_bytes_no_follow(
            path, "manifest capture", max_bytes=before.st_size
        )
        after = path.lstat()

        def identity(value):
            return [
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_uid,
                value.st_gid,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            ]

        if identity(before) != identity(after) or len(payload) != before.st_size:
            raise Error("manifest capture source drift")
        total += len(payload)
        records.append(
            {"path": str(path), "sha256": digest(payload), "identity": identity(before)}
        )
    if not records:
        raise Error("finite approved input/code manifest required")
    return {"files": records}


def rollback_binding(scope):
    """Current journal and owned namespace identities for separate approval."""
    from millefeuille.domain.semantic_acceptance import safe_ref

    journal = scope["journal"] + "/journal.jsonl"
    safe_ref(journal)
    return {
        "publication_scope_identity": digest(json_bytes(scope)),
        "journal": capture_bound_manifest([Path(scope["root"]) / journal]),
        "namespace": namespace_binding(scope["root"], scope["approved_refs"]),
    }


@dataclass(frozen=True)
class WorkloadAdmission:
    """Trusted local preflight result, not an authentication token.

    The caller must independently authorize binding() in its exact scope.
    Immutable expected identities cannot be derived from an incoming manifest.
    Resource counters describe explicit payload opens/bytes and conservative
    working allocations, not host-wide interpreter/kernel accounting.
    """

    context: str
    roots: tuple
    entries: tuple
    reservations: tuple

    def manifest(self):
        return {
            "files": [
                {"path": p, "sha256": h, "identity": list(i)}
                for p, h, i in self.entries
            ]
        }

    def binding(self):
        return {
            "schema": "millefeuille-workload-admission/v1",
            "context": self.context,
            "roots": list(self.roots),
            "count": len(self.entries),
            "inventory_sha256": digest(json_bytes(self.manifest())),
            "reservations": dict(self.reservations),
        }


def _file_identity(value):
    return [
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    ]


def _reservation(values):
    keys = {"read_bytes", "opens", "metadata_bytes", "memory_bytes", "seconds"}
    ceilings = {
        "read_bytes": 16777216,
        "opens": 32768,
        "metadata_bytes": 16777216,
        "memory_bytes": 536870912,
        "seconds": 180,
    }
    if (
        not isinstance(values, dict)
        or set(values) != keys
        or any(
            type(values[k]) is not int or not 0 < values[k] <= ceilings[k] for k in keys
        )
    ):
        raise Error("explicit finite workload resource reservation required")
    return values


def prepare_workload_admission(paths, *, roots, context, reservations):
    """Trusted preflight only, using independently enumerated scoped paths.

    A tuple/list is mandatory: do not execute an untrusted/nonterminating next().
    Discovery must already be metered by the caller. No universal count limit.
    """
    from millefeuille.domain.secure_io import read_bytes_no_follow

    limits = _reservation(reservations)
    if (
        type(paths) not in (tuple, list)
        or not paths
        or type(roots) not in (tuple, list)
        or not roots
        or not isinstance(context, str)
        or not 0 < len(context) <= 1024
    ):
        raise Error("independently scoped workload inventory required")
    allowed = tuple(str(Path(r)) for r in roots)
    if any(not Path(r).is_absolute() or ".." in Path(r).parts for r in allowed):
        raise Error("absolute workload roots required")
    if len(paths) > limits["opens"] or len(paths) * 512 > limits["memory_bytes"]:
        raise Error("workload open/memory reservation exhausted")
    started = time.monotonic()
    entries, seen, total, metadata = [], set(), 0, 0
    for value in paths:
        path = Path(value)
        name = str(path)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or name in seen
            or not any(path.is_relative_to(Path(r)) for r in allowed)
        ):
            raise Error("unexpected/duplicate/out-of-scope workload path")
        # Reserve metadata before touching this path or allocating its payload.
        metadata += len(name.encode()) + 512
        if metadata > limits["metadata_bytes"] or metadata * 6 > limits["memory_bytes"]:
            raise Error("workload metadata/memory reservation exhausted")
        if time.monotonic() - started > limits["seconds"]:
            raise Error("workload deadline exhausted")
        seen.add(name)
        before = path.lstat()
        size = before.st_size
        if (
            not stat.S_ISREG(before.st_mode)
            or size > 2097152
            or total + size > min(16777216, limits["read_bytes"])
            or size * 3 + metadata * 6 > limits["memory_bytes"]
        ):
            raise Error("workload file/byte/memory reservation exhausted")
        raw = read_bytes_no_follow(path, "workload preflight", max_bytes=size)
        if _file_identity(path.lstat()) != _file_identity(before) or len(raw) != size:
            raise Error("workload inventory source drift")
        total += size
        entries.append((name, digest(raw), tuple(_file_identity(before))))
    if time.monotonic() - started > limits["seconds"]:
        raise Error("workload deadline exhausted")
    return WorkloadAdmission(
        context, allowed, tuple(entries), tuple(sorted(limits.items()))
    )


def _capture_workload(paths, admission):
    if not isinstance(admission, WorkloadAdmission) or type(paths) not in (tuple, list):
        raise Error("trusted workload admission and finite sequence required")
    if len(paths) != len(admission.entries):
        raise Error("unexpected/missing workload inventory entry")
    expected = tuple(row[0] for row in admission.entries)
    # Validate all names without touching ANY unexpected filesystem entry.
    if tuple(str(Path(p)) for p in paths) != expected:
        raise Error("unexpected/missing workload inventory entry")
    manifest = admission.manifest()
    _verify_workload(manifest, admission)
    return manifest


def decode_workload_manifest(payload, *, admission):
    """Bound raw metadata before JSON allocation; no incoming self-sizing."""
    if not isinstance(admission, WorkloadAdmission) or not isinstance(payload, bytes):
        raise Error("trusted workload metadata admission required")
    limits = dict(admission.reservations)
    if (
        len(payload) > limits["metadata_bytes"]
        or len(payload) * 6 > limits["memory_bytes"]
    ):
        raise Error("workload metadata allocation reservation exhausted")
    try:
        manifest = json.loads(payload)
    except (ValueError, RecursionError) as exc:
        raise Error("invalid workload metadata") from exc
    _verify_workload(manifest, admission)
    return manifest


def _verify_workload(manifest, admission):
    from millefeuille.domain.secure_io import read_bytes_no_follow

    if not isinstance(admission, WorkloadAdmission):
        raise Error("trusted workload admission required")
    limits = _reservation(dict(admission.reservations))
    if (
        type(manifest) is not dict
        or set(manifest) != {"files"}
        or type(manifest["files"]) is not list
        or len(manifest["files"]) != len(admission.entries)
    ):
        raise Error("unexpected/missing workload inventory entry")
    # Comparing to pinned records before I/O also rejects duplicate/order drift.
    if manifest != admission.manifest():
        raise Error("workload inventory identity/hash drift")
    metadata = sum(len(p.encode()) + 512 for p, _, _ in admission.entries)
    if (
        metadata > limits["metadata_bytes"]
        or len(admission.entries) > limits["opens"]
        or metadata * 6 > limits["memory_bytes"]
    ):
        raise Error("workload metadata/open/memory reservation exhausted")
    started, total = time.monotonic(), 0
    for name, expected_hash, expected_identity in admission.entries:
        if time.monotonic() - started > limits["seconds"]:
            raise Error("workload deadline exhausted")
        path = Path(name)
        before = path.lstat()
        size = before.st_size
        if (
            not stat.S_ISREG(before.st_mode)
            or tuple(_file_identity(before)) != expected_identity
            or size > 2097152
            or total + size > min(16777216, limits["read_bytes"])
            or 3 * size + 6 * metadata > limits["memory_bytes"]
        ):
            raise Error("workload identity or allocation drift")
        raw = read_bytes_no_follow(path, "workload verify", max_bytes=size)
        if (
            _file_identity(path.lstat()) != list(expected_identity)
            or len(raw) != size
            or digest(raw) != expected_hash
        ):
            raise Error("workload identity/hash drift")
        total += size
    if time.monotonic() - started > limits["seconds"]:
        raise Error("workload deadline exhausted")
    return digest(json_bytes(manifest))


def _verify_admitted_scope(scope, input_admission, code_admission):
    if scope.get("schema") != "millefeuille-reviewed-publication-scope/v2":
        if input_admission is not None or code_admission is not None:
            raise Error("unexpected workload admission for legacy scope")
        return
    for kind, admission in [("input", input_admission), ("code", code_admission)]:
        if (
            not isinstance(admission, WorkloadAdmission)
            or scope.get(kind + "_admission") != admission.binding()
            or scope.get(kind + "_binding") != admission.manifest()
        ):
            raise Error("trusted workload recovery scope drift")
        verify_bound_manifest(admission.manifest(), admission=admission)
