"""INTERNAL parent-only writeback core, NOT a trusted/live entry point.

Only a future reviewed root adapter may supply authoritative materialization,
remote snapshots and verified receipt-derived _Authority. These Python objects
are NOT authentication. No credentials, HTTP, root issuance or public CLI here.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
import time

from millefeuille.domain.millefeuille import MillefeuilleContractError as Error


def _wire(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _hash(value):
    return "sha256:" + hashlib.sha256(_wire(value)).hexdigest()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise Error("invalid commitment")
    return value


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{8}", value):
        raise Error("immutable Zotero key required")
    return value


def _snapshot(value):
    if set(value) != {"library_type", "library_id", "key", "version", "data"}:
        raise Error("snapshot envelope fields")
    if value["library_type"] not in ("user", "group") or not re.fullmatch(
        r"[1-9][0-9]*", str(value["library_id"])
    ):
        raise Error("explicit library identity required")
    _key(value["key"])
    if type(value["version"]) is not int or value["version"] < 1:
        raise Error("positive item version required")
    data = value["data"]
    if not isinstance(data, dict) or data.get("itemType") in (
        None,
        "note",
        "attachment",
    ):
        raise Error("parent bibliographic item required")
    tags = data.get("tags")
    collections = data.get("collections")
    if (
        not isinstance(tags, list)
        or not isinstance(collections, list)
        or len(tags) > 100
        or len(collections) > 100
    ):
        raise Error("bounded tags/collections required")
    names = []
    for tag in tags:
        if (
            not isinstance(tag, dict)
            or set(tag) - {"tag", "type"}
            or not isinstance(tag.get("tag"), str)
            or not tag["tag"]
            or len(tag["tag"]) > 128
            or (
                "type" in tag
                and (type(tag["type"]) is not int or tag["type"] not in (0, 1))
            )
        ):
            raise Error("tag objects must be preserved")
        names.append(tag["tag"])
    if len(names) != len(set(names)) or len(collections) != len(set(collections)):
        raise Error("duplicate parent metadata")
    for key in collections:
        _key(key)
    if len(_wire(value)) > 65536:
        raise Error("snapshot byte cap")
    result = json.loads(_wire(value))
    result["data"]["tags"].sort(key=lambda tag: tag["tag"])
    result["data"]["collections"].sort()
    return result


@dataclass(frozen=True)
class _Plan:
    # Canonical bytes avoid caller mutation of a frozen dataclass's dictionaries.
    payload: bytes

    @property
    def digest(self):
        return "sha256:" + hashlib.sha256(self.payload).hexdigest()

    def value(self):
        return json.loads(self.payload)


def _plan(
    *, materialized, before, collection, add_tags=(), remove_tags=(), note_html=None
):
    """Pure: callers supply checked snapshots; no filesystem/network effects."""
    if note_html is not None:
        raise Error("notes unsupported in parent-only core")
    if len(_wire(materialized)) > 1024 * 1024:
        raise Error("materialization byte cap")
    before = _snapshot(before)
    if set(materialized) != {
        "index",
        "manifest",
        "decision",
        "taxonomy_lock",
        "source",
        "acceptance",
    }:
        raise Error("complete materialization bundle required")
    idx, manifest, decision, lock, source, acceptance = (
        materialized[k]
        for k in (
            "index",
            "manifest",
            "decision",
            "taxonomy_lock",
            "source",
            "acceptance",
        )
    )
    paper, run, source_hash = (
        decision.get("paper_id"),
        decision.get("run_id"),
        decision.get("source_hash"),
    )
    _sha(source_hash)
    if (
        decision.get("schema_version") != "millefeuille-classification-decision/v0.1"
        or decision.get("status") != "classified"
        or decision.get("mode") != "single"
        or decision.get("review_reasons")
        or decision.get("confidence") not in ("high", "medium")
        or not paper
        or not run
    ):
        raise Error("scientifically accepted materialized classification required")
    if (
        idx.get("schema_version") != "millefeuille-artifact-index/v0.1"
        or idx.get("paper_id") != paper
        or idx.get("run_id") != run
        or idx.get("source_pack", {}).get("source_hash") != source_hash
    ):
        raise Error("artifact identity mismatch")
    for stage in ("acceptance", "classify"):
        if idx.get("stages", {}).get(stage, {}).get("status") != "passed":
            raise Error("materialized stage missing")
    if (
        manifest.get("schema_version") != "millefeuille-stage-manifest/v0.1"
        or manifest.get("run_id") != run
        or manifest.get("source_type") != "zotero"
    ):
        raise Error("manifest identity mismatch")
    stages = manifest.get("stages", [])
    if not isinstance(stages, list) or any(
        not any(s.get("name") == name and s.get("status") == "passed" for s in stages)
        for name in ("acceptance", "classify")
    ):
        raise Error("manifest materialization absent")
    expected_ref = "classification/decision-records/" + paper + ".json"
    if (
        idx.get("artifacts", {}).get("classification_decision", {}).get("ref")
        != expected_ref
        or decision.get("writeback_preview_ref")
        != "classification/zotero-writeback-preview.json"
    ):
        raise Error("classification artifact linkage absent")
    if (
        acceptance.get("paper_id") != paper
        or acceptance.get("run_id") != run
        or acceptance.get("status") != "pass"
        or acceptance.get("source_hash") != source_hash
    ):
        raise Error("accepted evidence missing")
    from millefeuille.domain.taxonomy import validate_taxonomy_lock

    lock = validate_taxonomy_lock(lock)
    if lock["scope_type"] != "single-run" or lock["scope_id"] != run:
        raise Error("taxonomy lock run mismatch")
    if decision.get("taxonomy_version") != lock["taxonomy_version"]:
        raise Error("taxonomy mismatch")
    if set(collection) != {
        "library_type",
        "library_id",
        "key",
        "entry_id",
        "primary_path",
        "taxonomy_lock_identity",
    }:
        raise Error("explicit collection mapping required")
    if (
        any(collection[k] != before[k] for k in ("library_type", "library_id"))
        or collection["primary_path"] != decision["primary_path"]
        or collection["taxonomy_lock_identity"] != lock["content_identity"]
    ):
        raise Error("collection identity mismatch")
    entries = {e["entry_id"]: e for e in lock["registry_snapshot"]["entries"]}
    entry = entries.get(collection["entry_id"], {})
    if (
        entry.get("level") != 2
        or entry.get("status") != "active"
        or entries.get(entry.get("parent_id"), {}).get("label", "")
        + " > "
        + entry.get("label", "")
        != decision["primary_path"]
    ):
        raise Error("locked taxonomy mapping mismatch")
    _key(collection["key"])
    if (
        set(source)
        != {"library_type", "library_id", "key", "source_item_version", "source_hash"}
        or any(source[k] != before[k] for k in ("library_type", "library_id", "key"))
        or source["source_hash"] != source_hash
        or idx.get("source_identity", {}).get("zotero_item_key") != before["key"]
        or type(source["source_item_version"]) is not int
        or not 1 <= source["source_item_version"] <= before["version"]
    ):
        raise Error("original source and fresh snapshot identity mismatch")
    add, remove = list(add_tags), list(remove_tags)
    if (
        any(not isinstance(t, str) or not t or len(t) > 128 for t in add + remove)
        or len(add) + len(remove) > 10
        or len(set(add + remove)) != len(add + remove)
    ):
        raise Error("exact bounded tag delta required")
    after = json.loads(_wire(before["data"]))
    after["tags"] = [t for t in after["tags"] if t["tag"] not in remove]
    existing = {t["tag"] for t in after["tags"]}
    after["tags"] += [{"tag": t, "type": 0} for t in add if t not in existing]
    if collection["key"] not in after["collections"]:
        after["collections"].append(collection["key"])
    after["tags"].sort(key=lambda tag: tag["tag"])
    after["collections"].sort()
    value = {
        "schema": "millefeuille-writeback-core-plan/v1",
        "paper_id": paper,
        "run_id": run,
        "source_hash": source_hash,
        "source_item_version": source["source_item_version"],
        "classification_ref": expected_ref,
        "materialization_digest": _hash(materialized),
        "mapping": collection,
        "before": before,
        "before_digest": _hash(before),
        "after_data": after,
        "add_tags": add,
        "remove_tags": remove,
        "note_scope": None,
        "limits": {"mutations": 1, "reads": 2, "seconds": 10, "bytes": 65536},
    }
    return _Plan(_wire(value))


@dataclass(frozen=True)
class _Authority:
    """VERIFIED receipt projection supplied by future trusted root route ONLY."""

    receipt_id: str
    receipt_digest: str
    plan_digest: str
    operation: str
    expires_at: str
    execution_audit_digest: str | None = None

    def check(self, plan, operation):
        _sha(self.receipt_digest)
        _sha(self.plan_digest)
        if (
            not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", self.receipt_id)
            or self.plan_digest != plan.digest
            or self.operation != operation
            or operation not in ("execute", "rollback")
        ):
            raise Error("authority scope mismatch")
        expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= datetime.now(UTC):
            raise Error("authority expired")
        if operation == "rollback":
            _sha(self.execution_audit_digest)
        elif self.execution_audit_digest is not None:
            raise Error("execution cannot borrow rollback scope")


def _remaining(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("writeback deadline")
