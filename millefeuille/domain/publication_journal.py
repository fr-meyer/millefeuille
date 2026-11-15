"""Conservative reservation/recovery interface, deliberately NO output executor.

A reservation is consumed even if its creation fails after entry creation.
No resume, replay, rollback mutation, privilege or production authority is granted.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
import stat

from millefeuille.domain.acceptance_output_plan import json_bytes, plan_exact_outputs
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import (
    _open_directory_path_no_follow,
    read_bytes_no_follow,
    write_new_text_no_follow,
)
from millefeuille.domain.semantic_acceptance import digest, safe_ref


def validate_snapshot(*, root, outputs, approved_refs, max_files, max_bytes):
    """Validate a finite snapshot, including owners, before any journal effect.

    All output parents must already exist. No automatic discovery or mkdir.
    This is not a lock; concurrent writers are unsupported.
    """
    root = Path(root)
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise MillefeuilleContractError("publication requires POSIX no-follow support")
    if not root.is_absolute() or ".." in root.parts:
        raise MillefeuilleContractError("workspace must be an absolute explicit root")
    fd = _open_directory_path_no_follow(root, label="publication workspace")
    try:
        meta = os.fstat(fd)
        if meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o700:
            raise MillefeuilleContractError("workspace must be caller-owned mode 0700")
    finally:
        os.close(fd)
    if any(type(v) is not int or v < 1 for v in (max_files, max_bytes)):
        raise MillefeuilleContractError("positive finite output limits required")
    outputs = tuple(outputs)
    refs = tuple(item.ref for item in outputs)
    if not refs or len(refs) != len(set(refs)) or set(refs) != set(approved_refs):
        raise MillefeuilleContractError("exact finite approved refs required")
    if (
        len(refs) > max_files
        or sum(len(x.payload) + len(x.before_bytes or b"") for x in outputs) > max_bytes
    ):
        raise MillefeuilleContractError("output snapshot exceeds limits")
    for ref in refs:
        if safe_ref(ref).as_posix() != ref:
            raise MillefeuilleContractError("noncanonical output ref")
        if any(other.startswith(ref + "/") for other in refs):
            raise MillefeuilleContractError("overlapping output refs")
        fd = _open_directory_path_no_follow((root / ref).parent, label="output parent")
        try:
            parent = os.fstat(fd)
            if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
                raise MillefeuilleContractError(
                    "unsafe output parent ownership or mode"
                )
        finally:
            os.close(fd)
    current = plan_exact_outputs(root=root, outputs={x.ref: x.payload for x in outputs})
    for expected, observed in zip(
        sorted(outputs, key=lambda x: x.ref), current, strict=True
    ):
        if expected != observed or digest(expected.payload) != expected.sha256:
            raise MillefeuilleContractError("stale or malformed output snapshot")
        if expected.before_bytes is not None:
            meta = (root / expected.ref).lstat()
            if (
                meta.st_nlink != 1
                or meta.st_uid != os.geteuid()
                or meta.st_gid != os.getegid()
                or stat.S_IMODE(meta.st_mode) & 0o077
            ):
                raise MillefeuilleContractError(
                    "output preservation requires unsupported authority"
                )
    return outputs


def reserve_publication(
    *,
    root,
    outputs,
    approved_refs,
    journal_ref,
    max_files,
    max_bytes,
    max_journal_bytes,
):
    """Create one durable intent with embedded before-images; never write outputs.

    Existing, partial or uncertain journal names block re-reservation. Failure
    preserves the entry. Separate approval must enumerate this journal effect.
    """
    outputs = validate_snapshot(
        root=root,
        outputs=outputs,
        approved_refs=approved_refs,
        max_files=max_files,
        max_bytes=max_bytes,
    )
    root = Path(root)
    if safe_ref(journal_ref).as_posix() != journal_ref:
        raise MillefeuilleContractError("noncanonical journal ref")
    for item in outputs:
        if (
            journal_ref == item.ref
            or journal_ref.startswith(item.ref + "/")
            or item.ref.startswith(journal_ref + "/")
        ):
            raise MillefeuilleContractError("journal overlaps output")
    parent = (root / journal_ref).parent
    fd = _open_directory_path_no_follow(parent, label="journal parent")
    try:
        meta = os.fstat(fd)
        if meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o700:
            raise MillefeuilleContractError("journal parent must be caller-private")
        identity = (meta.st_dev, meta.st_ino)
    finally:
        os.close(fd)
    payload = json_bytes(
        {
            "schema": "publication-reservation/v1",
            "state": "reserved-no-executor",
            "root_identity": [root.stat().st_dev, root.stat().st_ino],
            "outputs": [
                {
                    "ref": x.ref,
                    "sha256": x.sha256,
                    "before_sha256": x.before_sha256,
                    "before_mode": x.before_mode,
                    "before_uid": x.before_uid,
                    "before_gid": x.before_gid,
                    "before_base64": base64.b64encode(x.before_bytes).decode()
                    if x.before_bytes is not None
                    else None,
                }
                for x in outputs
            ],
        }
    )
    if type(max_journal_bytes) is not int or max_journal_bytes < len(payload):
        raise MillefeuilleContractError("journal exceeds explicit byte limit")
    write_new_text_no_follow(
        root / journal_ref,
        payload.decode(),
        "publication reservation",
        expected_parent_identity=identity,
        preserve_created_on_failure=True,
    )
    return digest(payload)


def validate_rollback_postimages(*, root, outputs):
    """Read-only guard, NOT rollback authorization or an inode identity receipt.

    Return exact refs eligible for subsequent operator identity review. A real
    executor must additionally record and compare post-write inode identities.
    """
    outputs = tuple(outputs)
    root = Path(root)
    fd = _open_directory_path_no_follow(root, label="rollback workspace")
    os.close(fd)
    for item in outputs:
        safe_ref(item.ref)
        current = read_bytes_no_follow(
            Path(root) / item.ref, "rollback postimage", max_bytes=len(item.payload)
        )
        meta = (Path(root) / item.ref).lstat()
        if current != item.payload or meta.st_uid != os.geteuid() or meta.st_nlink != 1:
            raise MillefeuilleContractError("rollback refuses unrelated changes")
    return tuple(x.ref for x in outputs)


def execute_publication(*args, **kwargs):
    """Fail closed: no crash-safe mutation executor is implemented here."""
    raise MillefeuilleContractError(
        "publication executor unsupported; operator reconciliation required"
    )


def execute_reserved_publication(*args, **kwargs):
    """Explicitly refuse until an identity-safe mutation protocol is qualified.

    This is distinct from the legacy reservation API. It deliberately does not
    consume a reservation or touch outputs on an unsupported execution route.
    """
    raise MillefeuilleContractError(
        "bounded executor not qualified: POSIX dirfd rename cannot conditionally "
        "replace an exact inode; namespace exclusion and durable per-effect "
        "postidentity recovery protocol remain unproven; no effects performed"
    )


def rollback_reserved_publication(*args, **kwargs):
    """Never treat a hash-only reservation as an inode-bound rollback receipt."""
    raise MillefeuilleContractError(
        "rollback refused: reservation has no durable exact postimage inode "
        "receipt; preserve journal and before-images for explicit reconciliation"
    )
