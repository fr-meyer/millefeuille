"""Finite source spans: saved identities, no discovery, pre-debited duplicate I/O."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import stat
import threading
import time
from typing import TypedDict

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import digest, safe_ref


class EvidenceCorpusManifestV1(TypedDict):
    schema_version: str
    paper_id: str
    run_id: str
    manifest_sha256: str
    attachments: list[dict]
    spans: list[dict]
    completeness: dict


class BudgetExhaustedError(MillefeuilleContractError):
    pass


@dataclass
class ReadBudget:
    max_read_bytes: int = 67108864
    max_opens: int = 1024
    max_scans: int = 1024
    max_seconds: float = 90
    read_bytes: int = field(default=0, init=False)
    opens: int = field(default=0, init=False)
    scans: int = field(default=0, init=False)
    started: float = field(default_factory=time.monotonic, init=False)

    owner_thread: int = field(default_factory=threading.get_ident, init=False)
    exhausted: bool = field(default=False, init=False)

    def debit(self, *, read_bytes=0, opens=0, scans=0):
        if threading.get_ident() != self.owner_thread:
            raise BudgetExhaustedError("cross-thread source budget prohibited")
        if self.exhausted:
            raise BudgetExhaustedError("cumulative source budget exhausted")
        if (
            any(
                type(v) is not int or v < 0
                for v in (
                    read_bytes,
                    opens,
                    scans,
                    self.max_read_bytes,
                    self.max_opens,
                    self.max_scans,
                )
            )
            or type(self.max_seconds) not in (int, float)
            or not math.isfinite(self.max_seconds)
            or not 0 < self.max_seconds <= 3600
        ):
            raise MillefeuilleContractError("invalid read budget")
        if (
            self.read_bytes + read_bytes > self.max_read_bytes
            or self.opens + opens > self.max_opens
            or self.scans + scans > self.max_scans
            or time.monotonic() - self.started > self.max_seconds
        ):
            self.exhausted = True
            raise BudgetExhaustedError("cumulative source budget exhausted")
        self.read_bytes += read_bytes
        self.opens += opens
        self.scans += scans


def saved_identity(path):
    """Capture only metadata, never discover or open payloads."""
    s = Path(path).lstat()
    return (
        s.st_dev,
        s.st_ino,
        s.st_mode,
        s.st_uid,
        s.st_gid,
        s.st_size,
        s.st_mtime_ns,
        s.st_ctime_ns,
    )


class ScopedReadOnlyReader:
    def __init__(self, *, root, files, ancestors, reviewed_package=None):
        self.root = Path(root)
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise MillefeuilleContractError("absolute source root required")
        self.reviewed_package = reviewed_package
        self.files = files
        self.ancestors = ancestors

    def read(self, ref, budget):
        relative = safe_ref(ref)
        entry = self.files.get(ref)
        if not isinstance(entry, dict):
            raise MillefeuilleContractError("source outside saved scope")
        path = self.root / relative
        # Every ancestor, including filesystem root, must have a saved identity.
        parents = list(reversed(path.parents))
        budget.debit(opens=len(parents) + 1, read_bytes=entry["identity"][5])
        fds = []
        try:
            for parent in parents:
                key = str(parent)
                expected = self.ancestors.get(key)
                if expected is None or saved_identity(parent)[:5] != tuple(
                    expected[:5]
                ):
                    raise MillefeuilleContractError("saved ancestor identity drift")
                fd = os.open(
                    parent.name if fds else "/",
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=fds[-1] if fds else None,
                )
                fds.append(fd)
                s = os.fstat(fd)
                if (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid) != tuple(
                    expected[:5]
                ):
                    raise MillefeuilleContractError("opened ancestor drift")
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fds[-1])
            fds.append(fd)
            expected = tuple(entry["identity"])
            if saved_identity(path) != expected or not stat.S_ISREG(expected[2]):
                raise MillefeuilleContractError("source identity drift")
            s = os.fstat(fd)
            actual = (
                s.st_dev,
                s.st_ino,
                s.st_mode,
                s.st_uid,
                s.st_gid,
                s.st_size,
                s.st_mtime_ns,
                s.st_ctime_ns,
            )
            if actual != expected:
                raise MillefeuilleContractError("opened source identity drift")
            data = bytearray()
            while len(data) < s.st_size:
                budget.debit()
                chunk = os.read(fd, min(65536, s.st_size - len(data)))
                if not chunk:
                    raise MillefeuilleContractError("short source read")
                data.extend(chunk)
            if saved_identity(path) != expected or digest(data) != entry["sha256"]:
                raise MillefeuilleContractError("source hash/identity drift")
            return bytes(data)
        finally:
            for fd in reversed(fds):
                os.close(fd)


def iter_verified_spans(*, corpus, selection, budget, reader) -> Iterator[dict]:
    from millefeuille.domain.reviewed_summary_handoff import (
        retrieve_reviewed_summary_refs,
    )

    package = getattr(reader, "reviewed_package", None)
    if not isinstance(package, dict):
        raise MillefeuilleContractError("registered reviewed package required")
    refs = retrieve_reviewed_summary_refs(
        package_root=package["package_root"],
        expected_paper_id=corpus["paper_id"],
        expected_run_id=corpus["run_id"],
        expected_manifest_sha256=package["manifest_sha256"],
        budget=budget,
    )
    if not refs["matches"]:
        raise MillefeuilleContractError("reviewed package has no scientific refs")
    units = {(u["attachment_key"], u["section_id"]): u for u in refs["matches"]}
    for span in corpus["spans"]:
        unit = units.get((span["attachment_key"], span["section_id"]))
        if (
            unit is None
            or span["page"] not in unit["local_pages"]
            or span["source_sha256"] != unit["source_sha256"]
        ):
            raise MillefeuilleContractError("span outside strict reviewed package")
    attachments = {a["key"]: a for a in corpus["attachments"]}
    # Alias attachment keys must not manufacture independent evidence. Refuse
    # the corpus rather than silently drop spans and falsify completeness.
    source_hashes = [a["source_sha256"] for a in corpus["attachments"]]
    if len(set(source_hashes)) != len(source_hashes):
        raise MillefeuilleContractError("duplicate source evidence requires review")
    for span in sorted(
        corpus["spans"], key=lambda s: (s["attachment_key"], s["page"], s["span_id"])
    ):
        if (
            selection["attachment_keys"] is not None
            and span["attachment_key"] not in selection["attachment_keys"]
        ):
            continue
        if (
            selection["local_page"] is not None
            and span["page"] != selection["local_page"]
        ):
            continue
        if (
            selection["section_ids"] is not None
            and span["section_id"] not in selection["section_ids"]
        ):
            continue
        budget.debit(scans=1)
        raw = reader.read(span["source_ref"], budget)
        attachment = attachments[span["attachment_key"]]
        if digest(raw) != attachment["extraction_sha256"]:
            raise MillefeuilleContractError("extraction hash drift")
        start, end = span["byte_start"], span["byte_end"]
        if not (
            type(start) is int and type(end) is int and 0 <= start < end <= len(raw)
        ):
            raise MillefeuilleContractError("invalid source span bounds")
        data = raw[start:end]
        if (
            digest(data) != span["text_sha256"]
            or span["source_sha256"] != attachment["source_sha256"]
        ):
            raise MillefeuilleContractError("source span hash drift")
        yield dict(
            span,
            text_utf8=data.decode("utf-8"),
            extraction_sha256=attachment["extraction_sha256"],
        )
