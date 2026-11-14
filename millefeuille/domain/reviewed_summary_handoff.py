"""Strict read-only assistant-reviewed section refs, separate from GPT publication.

Hashes establish byte identity, not reviewer authenticity or approval. No provider,
semantic answer, publication, or acceptance operation is implemented here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import RootArtifactReader
from millefeuille.domain.semantic_acceptance import (
    _READ_BUDGET,
    scoped_reads,
)


class BudgetedPackageReader(RootArtifactReader):
    def __enter__(self):
        _READ_BUDGET.get().debit(opens=len(self.root.parts))
        return super().__enter__()

    def read_bytes(self, path, label, *, max_bytes=None):
        if max_bytes is None:
            raise MillefeuilleContractError("bounded package read required")
        budget = _READ_BUDGET.get()
        budget.debit(read_bytes=1, opens=1)
        limit = min(Path(path).lstat().st_size, max_bytes)
        budget.debit(read_bytes=limit + 1, opens=len(Path(path).parts))
        return super().read_bytes(path, label, max_bytes=limit)

    def list_directory_names(self, path, label, *, entry_limit=512):
        _READ_BUDGET.get().debit(opens=len(Path(path).parts), scans=1)
        names = super().list_directory_names(path, label, entry_limit=entry_limit)
        if len(names) > 513:
            raise MillefeuilleContractError("package inventory exceeds bound")
        return names

    def revalidate_snapshot(self):
        budget = _READ_BUDGET.get()
        files = len(self._file_snapshots) + len(self._missing_file_snapshots)
        directories = len(self._directory_snapshots)
        budget.debit(
            opens=len(self.root.parts) + 2 * files + directories, scans=directories
        )
        return super().revalidate_snapshot()


SCHEMA = "millefeuille-reviewed-sections/v1"
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _require(condition, message):
    if not condition:
        raise MillefeuilleContractError(message)


def _keys(value, keys):
    _require(
        type(value) is dict and set(value) == set(keys.split()),
        "reviewed package schema mismatch",
    )


def _string(value, pattern=_TOKEN):
    _require(
        isinstance(value, str) and pattern.fullmatch(value) is not None,
        "invalid reviewed package identifier/ref/digest",
    )
    return value


def _list(value, limit):
    _require(
        type(value) is list and 0 < len(value) <= limit,
        "reviewed package list exceeds bounds or is empty",
    )
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_pairs)
    except MillefeuilleContractError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise MillefeuilleContractError("invalid reviewed JSON") from exc


def _hash(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


@scoped_reads
def retrieve_reviewed_summary_refs(
    *,
    package_root: str | Path,
    expected_paper_id: str,
    expected_run_id: str,
    expected_manifest_sha256: str,
    attachment_key: str | None = None,
    section: str | None = None,
    local_page: int | None = None,
) -> dict:
    """Verify entire flat package before returning section-file refs only.

    Expected identity/digest must be supplied independently by the caller; this
    API does not establish their authority. Empty matches are valid ref results.
    Section is an exact section ID, not a semantic query. Pages are attachment-
    local; an unqualified page present in multiple attachments is an error.
    """
    _string(expected_paper_id)
    _string(expected_run_id)
    _string(expected_manifest_sha256, _DIGEST)
    for value in (attachment_key, section):
        if value is not None:
            _string(value)
    if local_page is not None:
        _require(
            type(local_page) is int and 1 <= local_page <= 100000, "invalid local page"
        )
    root = Path(package_root).absolute()
    with BudgetedPackageReader(root) as reader:
        raw = reader.read_bytes(
            root / "manifest.json", "reviewed manifest", max_bytes=262144
        )
        _require(_hash(raw) == expected_manifest_sha256, "manifest drift")
        manifest = _json(raw)
        _keys(manifest, "schema paper_id run_id files attachments units")
        _require(manifest["schema"] == SCHEMA, "unsupported reviewed schema")
        _require(
            manifest["paper_id"] == expected_paper_id
            and manifest["run_id"] == expected_run_id,
            "identity drift",
        )
        files = manifest["files"]
        _require(
            type(files) is dict and 3 <= len(files) <= 512, "invalid reviewed inventory"
        )
        for ref, digest in files.items():
            _string(ref, _REF)
            _require(ref != "manifest.json", "manifest cannot bind itself")
            _string(digest, _DIGEST)
        _require(
            set(
                reader.list_directory_names(root, "reviewed inventory", entry_limit=513)
            )
            == set(files) | {"manifest.json"},
            "unknown or missing entries",
        )
        data = {}
        total = len(raw)
        for ref, digest in files.items():
            payload = reader.read_bytes(root / ref, "reviewed file", max_bytes=1048576)
            total += len(payload)
            _require(total <= 16777216, "reviewed package exceeds total limit")
            _require(_hash(payload) == digest, "reviewed file drift")
            data[ref] = payload
        used = {"structure.json", "provenance.json"}
        _require(used <= set(files), "missing structure/provenance")
        provenance = _json(data["provenance.json"])
        _keys(provenance, "kind reviewer review_id original_generated_sha256")
        _require(
            provenance["kind"] == "current-assistant-review",
            "review provenance must be distinct from generated provenance",
        )
        _string(provenance["reviewer"])
        _string(provenance["review_id"])
        _string(provenance["original_generated_sha256"], _DIGEST)
        attachments = {}
        source_refs = {}
        for item in _list(manifest["attachments"], 64):
            _keys(item, "attachment_key source_ref local_page_count")
            key = _string(item["attachment_key"])
            ref = _string(item["source_ref"], _REF)
            count = item["local_page_count"]
            _require(
                type(count) is int and 1 <= count <= 100000,
                "invalid attachment page count",
            )
            _require(
                key not in attachments and ref in files and ref not in used,
                "duplicate/unbound attachment",
            )
            attachments[key] = count
            source_refs[key] = ref
            used.add(ref)
        structure = _json(data["structure.json"])
        _keys(structure, "sections")
        sections = {}
        for item in _list(structure["sections"], 256):
            _keys(item, "section_id attachment_key heading local_pages")
            sid = _string(item["section_id"])
            key = _string(item["attachment_key"])
            heading = item["heading"]
            _require(
                isinstance(heading, str)
                and 0 < len(heading) <= 512
                and heading.strip() == heading,
                "invalid heading",
            )
            pages = _list(item["local_pages"], 1000)
            _require(
                key in attachments
                and all(type(p) is int and 1 <= p <= attachments[key] for p in pages),
                "invalid pages",
            )
            _require(len(set(pages)) == len(pages), "duplicate pages")
            identity = (key, sid)
            _require(identity not in sections, "duplicate scientific section")
            sections[identity] = item
        units = []
        covered = set()
        for item in _list(manifest["units"], 256):
            _keys(item, "section_id attachment_key summary_ref")
            identity = (_string(item["attachment_key"]), _string(item["section_id"]))
            ref = _string(item["summary_ref"], _REF)
            _require(
                identity in sections
                and identity not in covered
                and ref in files
                and ref not in used,
                "duplicate, incomplete or unbound summary unit",
            )
            try:
                text = data[ref].decode("utf-8")
            except UnicodeError as exc:
                raise MillefeuilleContractError("summary must be UTF-8") from exc
            _require(bool(text.strip()), "empty summary")
            covered.add(identity)
            used.add(ref)
            units.append(
                {
                    **sections[identity],
                    "summary_ref": ref,
                    "summary_sha256": files[ref],
                    "source_ref": source_refs[identity[0]],
                    "source_sha256": files[source_refs[identity[0]]],
                }
            )
        _require(covered == set(sections), "incomplete scientific section coverage")
        _require(used == set(files), "unused/unbound package files")
        if local_page is not None and attachment_key is None:
            candidates = {
                key for key, count in attachments.items() if local_page <= count
            }
            _require(len(candidates) <= 1, "ambiguous attachment-local page")
        matches = [
            unit
            for unit in units
            if (attachment_key is None or unit["attachment_key"] == attachment_key)
            and (section is None or unit["section_id"] == section)
            and (local_page is None or local_page in unit["local_pages"])
        ]
        reader.revalidate_snapshot()
    return {
        "schema": SCHEMA,
        "paper_id": expected_paper_id,
        "run_id": expected_run_id,
        "manifest_sha256": expected_manifest_sha256,
        "provenance_kind": "current-assistant-review",
        "provenance_ref": "provenance.json",
        "provenance_sha256": files["provenance.json"],
        "structure_ref": "structure.json",
        "structure_sha256": files["structure.json"],
        "matches": matches,
        "status": "matched" if matches else "no-match",
        "semantic_answer": "unsupported",
        "writes_performed": 0,
        "provider_calls_performed": 0,
    }
