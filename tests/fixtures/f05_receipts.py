"""Offline, stdlib-only custody for the two synthetic ordinary F05 tests.

Snapshots count visible filesystem effects, not syscalls or durable commits.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from typing import TypedDict

from tests.fixtures.evidence_transport import frame_receipt, parse

MAX_FILES = 160
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 512 * 1024
MAX_RECEIPT_BYTES = 3 * 1024 * 1024
ALLOWED_SUFFIXES = {".json", ".jsonl", ".md", ".pdf"}
SOURCE_PATHS = (
    "tests/test_index_refresh_crash_recovery.py",
    "tests/fixtures/f05_receipts.py",
    "tests/test_f05_receipts.py",
    "tests/fixtures/evidence_transport.py",
    "tests/test_evidence_transport.py",
    "scripts/validate_evidence_archive.py",
    "tests/test_millefeuille_source_pack_writer.py",
    "millefeuille/domain/index_fixtures.py",
    "millefeuille/domain/card_index_contract.py",
)


class FileImage(TypedDict):
    sha256: str
    size: int
    identity: list[int]
    bytes_b64: str


def _relative(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or ":" in value
    ):
        raise ValueError("non-relative receipt path")
    if path.as_posix() != value:
        raise ValueError("non-normalized receipt path")


def _image(path: Path, *, limit: int = MAX_FILE_BYTES) -> FileImage:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError("unsafe or oversized fixture file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("file identity changed")
        payload = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(payload) > limit or len(payload) != before.st_size:
        raise ValueError("file size changed or exceeded bound")
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise ValueError("file changed during capture")
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "identity": [before.st_dev, before.st_ino],
        "bytes_b64": base64.b64encode(payload).decode("ascii"),
    }


def snapshot(root: Path) -> dict:
    """Capture only a caller-owned synthetic fixture, rejecting unknown file kinds."""
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError("fixture root is not a real directory")
    files = {}
    directories = []
    total = 0
    pending = [root]
    entries = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                if entries > MAX_FILES:
                    raise ValueError("fixture entry limit")
                path = Path(child.path)
                relative = path.relative_to(root).as_posix()
                _relative(relative)
                mode = child.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    directories.append(relative)
                    pending.append(path)
                elif stat.S_ISREG(mode) and (
                    path.suffix in ALLOWED_SUFFIXES
                    or (
                        path.name == ".card-index.lock"
                        and len(PurePosixPath(relative).parts) == 6
                        and PurePosixPath(relative).parts[0] == "source-packs"
                        and PurePosixPath(relative).parts[2:5]
                        == ("analyses", "millefeuille", "run-fixture")
                    )
                ):
                    size = child.stat(follow_symlinks=False).st_size
                    if total + size > MAX_TOTAL_BYTES:
                        raise ValueError("fixture aggregate limit")
                    files[relative] = _image(path)
                    total += files[relative]["size"]
                else:
                    raise ValueError("unknown fixture data or symlink")
    return {
        "files": dict(sorted(files.items())),
        "directories": sorted(directories),
        "file_count": len(files),
        "total_bytes": total,
    }


def effects(before: dict, after: dict) -> dict:
    """Measured net path/byte/identity changes, NOT a write-call counter."""
    old, new = before["files"], after["files"]
    common = old.keys() & new.keys()
    return {
        "created_files": len(new.keys() - old.keys()),
        "removed_files": len(old.keys() - new.keys()),
        "changed_bytes": sum(old[p]["sha256"] != new[p]["sha256"] for p in common),
        "changed_identities": sum(
            old[p]["identity"] != new[p]["identity"] for p in common
        ),
        "created_directories": len(
            set(after["directories"]) - set(before["directories"])
        ),
    }


def source_binding(repo: Path) -> dict:
    return {
        name: _image(repo / name, limit=512 * 1024)["sha256"] for name in SOURCE_PATHS
    }


def validate(receipt: dict) -> None:
    """Independently recompute byte integrity, inventories and net-effect counts."""
    if receipt.get("schema") != "millefeuille-f05-receipt/v1":
        raise ValueError("receipt schema")
    if receipt["fault"]["returncode"] != 71:
        raise ValueError("not the selected process death")
    if not isinstance(receipt["source_sha256"], dict):
        raise ValueError("source binding")
    for path, digest in receipt["source_sha256"].items():
        _relative(path)
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("source hash")
    for name in ("before", "after_death", "after_retry"):
        snap = receipt[name]
        if len(snap["files"]) > MAX_FILES or len(snap["directories"]) > MAX_FILES:
            raise ValueError("inventory bound")
        if snap["directories"] != sorted(set(snap["directories"])) or set(
            snap["directories"]
        ) & set(snap["files"]):
            raise ValueError("inventory directory schema")
        total = 0
        for path, image in snap["files"].items():
            _relative(path)
            if not 0 <= image["size"] <= MAX_FILE_BYTES:
                raise ValueError("image bound")
            if len(image["bytes_b64"]) > 4 * ((MAX_FILE_BYTES + 2) // 3):
                raise ValueError("encoded image bound")
            payload = base64.b64decode(image["bytes_b64"], validate=True)
            if len(payload) != image["size"]:
                raise ValueError("image size")
            if hashlib.sha256(payload).hexdigest() != image["sha256"]:
                raise ValueError("image hash")
            if (
                not isinstance(image["identity"], list)
                or len(image["identity"]) != 2
                or any(type(n) is not int or n < 0 for n in image["identity"])
            ):
                raise ValueError("filesystem identity")
            total += len(payload)
        for path in snap["directories"]:
            _relative(path)
        if total > MAX_TOTAL_BYTES or total != snap["total_bytes"]:
            raise ValueError("inventory bytes")
        if len(snap["files"]) != snap["file_count"]:
            raise ValueError("inventory count")
    for stage, start, end in (
        ("death", "before", "after_death"),
        ("retry", "after_death", "after_retry"),
    ):
        if receipt["net_effects"][stage] != effects(receipt[start], receipt[end]):
            raise ValueError("effect count mismatch")

    paths = receipt["paths"]
    for path in paths.values():
        _relative(path)
    before, death, retry = (
        receipt[n]["files"] for n in ("before", "after_death", "after_retry")
    )
    card, index, recovery = (paths[n] for n in ("card", "index", "recovery_index"))
    if death[index]["sha256"] != death[recovery]["sha256"]:
        raise ValueError("recovery index differs")
    if death[index]["identity"] == death[recovery]["identity"]:
        raise ValueError("recovery index aliases canonical")
    if retry[index]["sha256"] != death[index]["sha256"]:
        raise ValueError("retry changed index")
    for path, digest in receipt["protected_sha256"].items():
        if any(images[path]["sha256"] != digest for images in (before, death, retry)):
            raise ValueError("protected fixture changed")
    for path in receipt["custody"]["transaction_files"]:
        if death[path] != retry[path]:
            raise ValueError("transaction custody changed")
    seam = receipt["fault"]["seam"]
    if seam == "_ensure_expected_index":
        if death[card]["sha256"] != before[card]["sha256"]:
            raise ValueError("planned card changed before exchange")
        expected = "refused:MillefeuilleContractError"
    elif seam == "_atomic_capture_replace":
        expected = "accepted"
        retained = [
            death[p]["sha256"]
            for p in receipt["custody"]["transaction_files"]
            if p != recovery
        ]
        if before[card]["sha256"] not in retained:
            raise ValueError("original card not retained")
    else:
        raise ValueError("unknown fault seam")
    if receipt["readback"] != {"after_death": expected, "after_retry": "observed"}:
        raise ValueError("readback observations")
    if receipt["retry_statuses"] != ["existing"]:
        raise ValueError("retry results")
    for stage in ("before", "after_death", "after_retry"):
        parent = str(PurePosixPath(paths["transaction"]).parent)
        count = sum(
            str(PurePosixPath(p).parent) == parent
            for p in receipt[stage]["directories"]
        )
        if count != receipt["custody"]["transaction_counts"][stage]:
            raise ValueError("transaction count mismatch")


METHOD_SEAMS = {
    "tests.test_index_refresh_crash_recovery.TestIndexRefreshCrashRecovery."
    "test_death_after_index_publish_refuses_join_then_recovers": (
        "_ensure_expected_index"
    ),
    "tests.test_index_refresh_crash_recovery.TestIndexRefreshCrashRecovery."
    "test_death_after_card_exchange_keeps_truthful_join_and_original": (
        "_atomic_capture_replace"
    ),
}


def accept(
    payload: bytes,
    *,
    source_sha256: dict,
    runtime: dict,
    method: str,
    worker_sha256: str,
) -> dict:
    """Archive acceptance; validate alone checks synthetic low-level integrity."""
    receipt = parse(payload)
    if set(source_sha256) != set(SOURCE_PATHS) or not source_sha256:
        raise ValueError("expected exact source set")
    if receipt["source_sha256"] != source_sha256:
        raise ValueError("external source mismatch")
    if any(
        len(v) != 64 or any(c not in "0123456789abcdef" for c in v)
        for v in source_sha256.values()
    ):
        raise ValueError("external source hash")
    if set(runtime) != {
        "GITHUB_SHA",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "RUNNER_OS",
        "python",
    }:
        raise ValueError("runtime keys")
    if any(not isinstance(v, str) or not v for v in runtime.values()):
        raise ValueError("blank runtime")
    if receipt["runtime"] != runtime or receipt["test"] != method:
        raise ValueError("runtime/test mismatch")
    if method not in METHOD_SEAMS or receipt["fault"]["seam"] != METHOD_SEAMS[method]:
        raise ValueError("method seam")
    if (
        len(worker_sha256) != 64
        or receipt["fault"]["worker_sha256"] != worker_sha256
        or receipt["fault"]["position"] != "original seam returned, then os._exit(71)"
    ):
        raise ValueError("worker context")
    validate(receipt)
    paths = receipt["paths"]
    before, death, retry = (
        receipt[n]["files"] for n in ("before", "after_death", "after_retry")
    )
    run = PurePosixPath(paths["card"]).parent.parent.as_posix()
    if run == ".":
        raise ValueError("incomplete run context")
    if (
        not paths["index"].startswith(run + "/")
        or not paths["transaction"].startswith(run + "/")
        or not paths["recovery_index"].startswith(paths["transaction"] + "/")
    ):
        raise ValueError("path context")
    if not before or not death or not retry or paths["card"] not in before:
        raise ValueError("missing captured material")
    for snap in (before, death, retry):
        for path in before:
            if (
                path != paths["card"]
                and path != paths["index"]
                and (path not in snap or snap[path] != before[path])
            ):
                raise ValueError("retained initial fixture changed")
    protected = {
        p: v["sha256"] for p, v in before.items() if not p.startswith(run + "/")
    }
    if not protected or receipt["protected_sha256"] != protected:
        raise ValueError("protected inventory coverage")
    txn = paths["transaction"] + "/"
    captured = sorted(p for p in death if p.startswith(txn))
    if not captured or receipt["custody"]["transaction_files"] != captured:
        raise ValueError("transaction inventory coverage")
    if paths["transaction"] not in receipt["after_death"]["directories"]:
        raise ValueError("transaction directory missing")
    if sorted(p for p in retry if p.startswith(txn)) != captured:
        raise ValueError("retry transaction inventory")
    journal = receipt["custody"]["journal"]
    if journal != {
        "kind": "transaction file inventory, not a write journal",
        "journal_named_paths": [
            p for p in captured if "journal" in PurePosixPath(p).name.lower()
        ],
    }:
        raise ValueError("journal inventory")
    for stage in ("before", "after_death", "after_retry"):
        snap = receipt[stage]
        dirs = snap["directories"]
        if len(dirs) != len(set(dirs)) or len(dirs) + len(snap["files"]) > MAX_FILES:
            raise ValueError("actual entry count")
    return receipt


def emit(receipt: dict) -> None:
    validate(receipt)
    block = "\n".join(frame_receipt("F05", receipt)) + "\n"
    sys.stdout.write(block)
    sys.stdout.flush()
