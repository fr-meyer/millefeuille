"""Pure bounded exact-byte handoff, never publication authority."""

from __future__ import annotations

from dataclasses import dataclass, fields
import json
from pathlib import Path
import stat
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.semantic_acceptance import digest, safe_ref
from millefeuille.domain.stage_runtime import ensure_no_follow_directory


@dataclass(frozen=True)
class PlanningLimits:
    max_files: int = 64
    max_file_bytes: int = 2 * 1024 * 1024
    max_output_bytes: int = 16 * 1024 * 1024
    max_before_file_bytes: int = 2 * 1024 * 1024
    max_before_bytes: int = 16 * 1024 * 1024
    max_combined_bytes: int = 16 * 1024 * 1024

    def validate(self) -> None:
        ceiling = PlanningLimits()
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or not 0 < value <= getattr(ceiling, field.name):
                raise MillefeuilleContractError("invalid or excessive planning limit")


@dataclass(frozen=True)
class PlannedOutput:
    ref: str
    payload: bytes
    sha256: str
    before_bytes: bytes | None
    before_sha256: str | None
    before_mode: int | None
    before_uid: int | None
    before_gid: int | None


def validate_output_refs(refs) -> tuple[str, ...]:
    """Reject duplicate roles and impossible trees before dictionary insertion."""
    refs = tuple(refs)
    for ref in refs:
        safe_ref(ref)
    if len(refs) != len(set(refs)) or any(
        other.startswith(ref + "/") for ref in refs for other in refs if other != ref
    ):
        raise MillefeuilleContractError("overlapping output destinations")
    return refs


def plan_exact_outputs(
    *,
    root: Path,
    outputs: dict[str, bytes],
    limits: PlanningLimits | None = None,
    artifact_reader=None,
) -> tuple[PlannedOutput, ...]:
    """Preflight all bounded create/replace snapshots without even mkdir.

    Limits may only narrow the hard envelope. The publisher must separately bind
    new owner/mode, approval, journals and fresh inode identities before effects.
    A snapshot is not a lock or a crash-safe multi-file mutation executor.
    """
    limits = limits if limits is not None else PlanningLimits()
    if not isinstance(limits, PlanningLimits):
        raise MillefeuilleContractError("explicit validated planning limits required")
    limits.validate()
    if not isinstance(outputs, dict) or not 0 < len(outputs) <= limits.max_files:
        raise MillefeuilleContractError("output count exceeds planning bound")
    validate_output_refs(outputs)
    output_bytes = 0
    for payload in outputs.values():
        if not isinstance(payload, bytes):
            raise MillefeuilleContractError("planned payload must be bytes")
        if len(payload) > limits.max_file_bytes:
            raise MillefeuilleContractError("output payload exceeds planning bound")
        output_bytes += len(payload)
    if output_bytes > min(limits.max_output_bytes, limits.max_combined_bytes):
        raise MillefeuilleContractError("aggregate output exceeds planning bound")
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise MillefeuilleContractError("explicit absolute planning root required")
    if artifact_reader is None:
        ensure_no_follow_directory(root, "output planning root")
    else:
        from millefeuille.domain.secure_io import RootArtifactReader

        if not isinstance(artifact_reader, RootArtifactReader):
            raise MillefeuilleContractError(
                "shared planning reader capability required"
            )
        artifact_reader.require_budget_capability(root)
    result = []
    before_bytes = 0
    for ref, payload in sorted(outputs.items()):
        target = root / safe_ref(ref)
        before = None
        mode = uid = gid = None
        if artifact_reader is not None:
            remaining = min(
                limits.max_before_file_bytes,
                limits.max_before_bytes - before_bytes,
                limits.max_combined_bytes - output_bytes - before_bytes,
            )
            before, metadata = artifact_reader.read_before_image(
                target, "output before-image", max_bytes=remaining
            )
            if before is not None:
                before_bytes += len(before)
                mode, uid, gid = (
                    stat.S_IMODE(metadata.st_mode),
                    metadata.st_uid,
                    metadata.st_gid,
                )
        else:
            for parent in target.relative_to(root).parents:
                candidate = root / parent
                if candidate.exists() or candidate.is_symlink():
                    ensure_no_follow_directory(candidate, "output ancestor", root=root)
            before = None
            mode = uid = gid = None
            if target.exists() or target.is_symlink():
                metadata = target.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise MillefeuilleContractError("unsafe output target")
                if metadata.st_size > limits.max_before_file_bytes:
                    raise MillefeuilleContractError(
                        "before-image exceeds planning bound"
                    )
                if before_bytes + metadata.st_size > limits.max_before_bytes or (
                    output_bytes + before_bytes + metadata.st_size
                    > limits.max_combined_bytes
                ):
                    raise MillefeuilleContractError(
                        "aggregate before-images exceed planning bound"
                    )
                before = read_bytes_no_follow(
                    target,
                    "output before-image",
                    max_bytes=limits.max_before_file_bytes,
                )
                after = target.lstat()
                if (
                    any(
                        getattr(metadata, name) != getattr(after, name)
                        for name in (
                            "st_dev",
                            "st_ino",
                            "st_size",
                            "st_mtime_ns",
                            "st_ctime_ns",
                            "st_mode",
                            "st_uid",
                            "st_gid",
                        )
                    )
                    or len(before) != metadata.st_size
                ):
                    raise MillefeuilleContractError(
                        "output before-image identity drift"
                    )
                before_bytes += len(before)
                mode, uid, gid = (
                    stat.S_IMODE(metadata.st_mode),
                    metadata.st_uid,
                    metadata.st_gid,
                )
        result.append(
            PlannedOutput(
                ref,
                payload,
                digest(payload),
                before,
                digest(before) if before is not None else None,
                mode,
                uid,
                gid,
            )
        )
    if artifact_reader is not None:
        artifact_reader.revalidate_snapshot()
    return tuple(result)


def json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + chr(10)).encode("utf-8")
