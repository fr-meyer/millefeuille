"""Explicit standalone, note-only Zotero writeback. No protected-mode fallback."""

from __future__ import annotations

import asyncio
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import time

import httpx

from millefeuille.domain.taxonomy import (
    validate_taxonomy_lock,
    validate_taxonomy_registry,
)

CAP = 2 * 1024 * 1024
KEY = re.compile(r"[A-Z0-9]{8}")


class WritebackError(ValueError):
    """Messages are deliberately not included in CLI output."""


def require(condition):
    if not condition:
        raise WritebackError("Standalone writeback precondition failed")


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(canonical(value))


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    def invalid(_):
        raise WritebackError("Nonfinite JSON")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def safe_path(path):
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        require(not part.is_symlink())
    return path


def read_bytes(path, cap=CAP):
    path = safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid())
        require(info.st_mode & 0o077 == 0 and info.st_size <= cap)
        raw = stream.read(cap + 1)
        require(len(raw) <= cap)
        return raw


def load(path):
    return parse(read_bytes(path))


def save_new(path, value):
    path = safe_path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class SafeHTML(HTMLParser):
    def handle_starttag(self, tag, attrs):
        require(
            tag
            in {"div", "h1", "h2", "h3", "p", "br", "ul", "ol", "li", "strong", "em"}
        )
        require(not attrs)

    def handle_endtag(self, tag):
        self.handle_starttag(tag, [])

    def handle_comment(self, data):
        raise WritebackError("Comments not allowed")

    def handle_decl(self, decl):
        raise WritebackError("Declarations not allowed")

    def handle_pi(self, data):
        raise WritebackError("Processing instructions not allowed")


def item_identity(item, key, library):
    require(item["key"] == key and item["data"]["key"] == key)
    require(item["library"]["type"] == "user" and str(item["library"]["id"]) == library)
    require(type(item["version"]) is int and item["version"] == item["data"]["version"])


def semantic(item):
    return {
        k: v for k, v in item["data"].items() if k not in {"version", "dateModified"}
    }


def prepare(run_dir, item_key, library_id, approval_manifest):
    require(KEY.fullmatch(item_key) and re.fullmatch(r"[1-9][0-9]*", library_id))
    run = safe_path(run_dir)
    paper = run.parent.parent
    require(run.parent.name == "runs" and paper.name == f"user-{library_id}-{item_key}")
    require(stat.S_IMODE(paper.stat().st_mode) & 0o077 == 0)
    approval = load(approval_manifest)
    require(approval["schema"] == "millefeuille-standalone-note-approval/v1")
    require(approval["library_type"] == "user" and approval["library_id"] == library_id)
    entries = approval["items"]
    require(len(entries) == 3 and len({e["item_key"] for e in entries}) == 3)
    require(all(KEY.fullmatch(e["item_key"]) for e in entries))
    entry = next(e for e in entries if e["item_key"] == item_key)
    require(entry["run_dir"] == str(run) and entry["run_id"] == run.name)
    required = {
        "00-selected.json",
        "03-ocr-completed.json",
        "05-analysis-completed.json",
        "06-completed.json",
        "analysis/summary.json",
        "analysis/classification.json",
        "analysis/evidence.json",
        "analysis/taxonomy-lock.json",
        "taxonomy.json",
    }
    require(required <= set(entry["run_files"]))
    for ref, pin in entry["run_files"].items():
        relative = Path(ref)
        require(not relative.is_absolute() and ".." not in relative.parts)
        require(sha(read_bytes(run / relative, 32 * CAP)) == pin)
    manifest_raw = read_bytes(paper / "manifest.json")
    require(sha(manifest_raw) == entry["manifest_sha256"])
    manifest = parse(manifest_raw)
    done = load(run / "06-completed.json")
    require(manifest["schema_version"] == "millefeuille-standalone-source-pack/v1")
    require(done["status"] == "local-processing-complete")
    require(done["canonical_stage_acceptance"] == "not-claimed")
    for field, expected in {
        "run_id": run.name,
        "library_id": library_id,
        "library_type": "user",
        "item_key": item_key,
        "attachment_key": entry["attachment_key"],
        "zotero_writeback": "none",
        "max_items": 1,
    }.items():
        require(manifest[field] == expected and done[field] == expected)
    require(done["run_dir"] == str(run))
    require(
        manifest["provenance"]
        == {
            "intake_method": "live-zotero-api",
            "selection_ref": f"runs/{run.name}/00-selected.json",
        }
    )
    source_path = safe_path(paper / "source.pdf")
    pdf = None
    if source_path.exists():
        pdf = read_bytes(source_path, 100 * 1024 * 1024)
        require(pdf.startswith(b"%PDF-"))
        source_hash = sha(pdf)
        require(len(pdf) == manifest["source"]["byte_size"])
    else:
        from millefeuille.domain.standalone_retention import (
            validate_completed_retention,
        )

        retained = validate_completed_retention(run)
        source_hash = retained["source"]["sha256"]
    require(manifest["source"]["ref"] == "source.pdf")
    require(
        source_hash
        == manifest["source"]["sha256"]
        == done["source_sha256"]
        == entry["source_sha256"]
    )
    selected = load(run / "00-selected.json")
    original, attachment = selected["item"], selected["attachment"]
    item_identity(original, item_key, library_id)
    item_identity(attachment, entry["attachment_key"], library_id)
    require(original["version"] == manifest["item_version"] == entry["parent_version"])
    require(digest(original["data"]) == entry["parent_data_sha256"])
    require(
        attachment["version"]
        == manifest["attachment_version"]
        == entry["attachment_version"]
    )
    require(digest(attachment["data"]) == entry["attachment_data_sha256"])
    require(attachment["data"]["parentItem"] == item_key)
    require(attachment["data"]["contentType"] == "application/pdf")
    if pdf is not None:
        require(
            hashlib.md5(pdf, usedforsecurity=False).hexdigest()
            == attachment["data"]["md5"]
        )
    else:
        require(retained["attachment_md5"] == attachment["data"]["md5"])
    records = {}
    for stage in ("03-ocr-completed.json", "05-analysis-completed.json"):
        record = load(run / stage)
        records[stage] = record
        for pin in record["files"].values():
            require(pin["ref"] in entry["run_files"])
            require(sha(read_bytes(run / pin["ref"], 32 * CAP)) == pin["sha256"])
    ocr_record = records["03-ocr-completed.json"]
    fulltext_hash = ocr_record["files"]["fulltext_path"]["sha256"]
    ocr = parse(read_bytes(run / ocr_record["files"]["evidence_path"]["ref"], 32 * CAP))
    require(ocr["source_sha256"] == entry["source_sha256"])
    require(ocr["requested_model"] == "mistral-ocr-latest" and ocr["returned_model"])
    summary = load(run / "analysis/summary.json")
    classification = load(run / "analysis/classification.json")
    evidence = load(run / "analysis/evidence.json")
    require(
        summary["paper_id"]
        == classification["paper_id"]
        == evidence["paper_id"]
        == f"zotero-{item_key}"
    )
    require(
        evidence["model"] == "openai/gpt-6-astra" and evidence["reasoning"] == "xhigh"
    )
    require(evidence["auth_class"] == "subscription_oauth")
    require(evidence["source_hash_kind"] == "selected-fulltext-not-original-pdf")
    require(evidence["status"] == "generated-proposals-require-scientific-review")
    require(
        evidence["source_sha256"]
        == classification["source_hash"]
        == "sha256:" + fulltext_hash
    )
    require(evidence["summary_sha256"] == "sha256:" + digest(summary))
    require(evidence["classification_sha256"] == "sha256:" + digest(classification))
    lock = validate_taxonomy_lock(load(run / "analysis/taxonomy-lock.json"))
    registry = validate_taxonomy_registry(load(run / "taxonomy.json"))
    require(lock["registry_content_identity"] == registry["content_identity"])
    require(
        lock["content_identity"]
        == evidence["taxonomy_lock_identity"]
        == classification["taxonomy_lock_identity"]
        == entry["taxonomy_lock_identity"]
    )
    require(classification["run_id"] == lock["scope_id"])
    payload_raw = read_bytes(entry["payload_path"])
    require(sha(payload_raw) == entry["payload_sha256"])
    payload = parse(payload_raw)
    require(isinstance(payload, list) and len(payload) == 1)
    note = payload[0]
    require(
        set(note)
        == {"itemType", "parentItem", "note", "tags", "collections", "relations"}
    )
    require(note["collections"] == [] and note["relations"] == {})
    require(note["itemType"] == "note" and note["parentItem"] == item_key)
    require(note["tags"] == [{"tag": "millefeuille-summary"}])
    require(sha(note["note"].encode()) == entry["note_html_sha256"])
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", entry["identity"]))
    marker = "Millefeuille writeback identity: " + entry["identity"]
    require(note["note"].count(marker) == 1)
    parser = SafeHTML(convert_charrefs=True)
    parser.feed(note["note"])
    parser.close()
    return {
        "run": run,
        "paper": paper,
        "entry": entry,
        "payload": payload,
        "original": original,
        "attachment": attachment,
        "marker": marker,
        "binding": digest(entry),
        "library": library_id,
        "key": item_key,
    }


class Transport:
    def __init__(self, secret, deadline):
        self.deadline = deadline
        self.client = httpx.AsyncClient(
            headers={
                "Zotero-API-Key": secret,
                "Zotero-API-Version": "3",
                "Cache-Control": "no-cache",
            },
            trust_env=False,
            follow_redirects=False,
            timeout=30,
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        )

    async def request(self, method, path, **kwargs):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0)
        async with asyncio.timeout(min(30, remaining)):
            async with self.client.stream(
                method, "https://api.zotero.org" + path, **kwargs
            ) as response:
                require(response.status_code == 200)
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    require(len(chunks) <= CAP)
                return parse(bytes(chunks)), response.headers

    async def close(self):
        await self.client.aclose()


async def inventory(client, base, key):
    values, headers = await client.request(
        "GET", f"{base}/items/{key}/children?limit=100"
    )
    require(isinstance(values, list) and len(values) <= 100)
    require(int(headers["Total-Results"]) == len(values))
    require(len({v["key"] for v in values}) == len(values))
    return values


def verify_note(note, context):
    item_identity(note, note["key"], context["library"])
    require(KEY.fullmatch(note["key"]))
    expected = context["payload"][0]
    for field in ("itemType", "parentItem", "note"):
        require(note["data"][field] == expected[field])
    require(
        note["data"]["tags"]
        in (expected["tags"], [{"tag": "millefeuille-summary", "type": 0}])
    )
    require(not note["data"].get("collections") and not note["data"].get("relations"))


async def execute(context, live):
    c = context
    journal = c["paper"] / "standalone-writeback-NOTE.json"
    result_path = c["paper"] / "standalone-writeback-NOTE-result.json"
    old = load(journal) if journal.exists() else None
    if old:
        require(old["binding"] == c["binding"])
        if not result_path.exists():
            return {"status": "needs-review", "note_key": None}
        previous = load(result_path)
        if previous["status"] != "written-verified":
            return {"status": "needs-review", "note_key": previous.get("note_key")}
    elif not live:
        return {"status": "planned-read-only", "note_key": None}
    read_key = os.environ.get("ZOTERO_READ_KEY", "")
    require(read_key)
    write_key = os.environ.get("ZOTERO_WRITE_KEY", "") if not old else ""
    require(old or write_key)
    deadline = time.monotonic() + 120
    reader = Transport(read_key, deadline)
    writer = Transport(write_key, deadline) if not old else None
    base = f"/users/{c['library']}"
    reserved = False
    note_key = None
    try:
        for client, writable in [(reader, False)] + (
            [(writer, True)] if writer else []
        ):
            metadata, _ = await client.request("GET", "/keys/current")
            require(str(metadata["userID"]) == c["library"])
            access = metadata["access"]["user"]
            require(access.get("library") is True)
            require(
                access.get("write") is True if writable else access.get("notes") is True
            )
            del metadata
        parent, _ = await reader.request("GET", f"{base}/items/{c['key']}")
        attachment, _ = await reader.request(
            "GET", f"{base}/items/{c['entry']['attachment_key']}"
        )
        item_identity(parent, c["key"], c["library"])
        item_identity(attachment, c["entry"]["attachment_key"], c["library"])
        require(attachment["data"] == c["attachment"]["data"])
        children = await inventory(reader, base, c["key"])
        matches = [v for v in children if c["marker"] in v["data"].get("note", "")]
        if old:
            note_key = previous["note_key"]
            require(len(matches) == 1 and matches[0]["key"] == note_key)
            note, _ = await reader.request("GET", f"{base}/items/{note_key}")
            verify_note(note, c)
            require(note["version"] == previous["note_version"])
            require(semantic(parent) == semantic(old["parent_before"]))
            original_children = old["children_before"]
            require(
                {v["key"]: v["data"] for v in children if v["key"] != note_key}
                == {v["key"]: v["data"] for v in original_children}
            )
            return {"status": "already-written-verified", "note_key": note_key}
        require(parent["data"] == c["original"]["data"] and not matches)
        require(
            any(
                v["key"] == attachment["key"] and v["data"] == attachment["data"]
                for v in children
            )
        )
        # O_EXCL reservation is also the durable pre-dispatch intent. Never removed.
        save_new(
            journal,
            {
                "schema": "millefeuille-standalone-note-journal/v1",
                "status": "mutation-intent",
                "binding": c["binding"],
                "approval": c["entry"],
                "parent_before": parent,
                "attachment_before": attachment,
                "children_before": children,
                "payload": c["payload"],
                "model_calls": 0,
            },
        )
        reserved = True
        # Exactly one POST, with no retry or library-version concurrency claim.
        ack, ack_headers = await writer.request(
            "POST",
            base + "/items",
            json=c["payload"],
            headers={"Zotero-Write-Token": c["binding"][:32]},
        )
        save_new(c["paper"] / "standalone-writeback-NOTE-ack.json", ack)
        require(set(ack.get("successful", {})) == {"0"})
        require(not ack.get("unchanged") and not ack.get("failed"))
        acknowledged = ack["successful"]["0"]
        note_key = acknowledged["key"]
        require(KEY.fullmatch(note_key))
        require(type(acknowledged["version"]) is int)
        require(int(ack_headers["Last-Modified-Version"]) == acknowledged["version"])
        if "library" in acknowledged:
            require(acknowledged["library"]["type"] == "user")
            require(str(acknowledged["library"]["id"]) == c["library"])
        for field in ("parentItem", "note", "itemType"):
            if field in acknowledged.get("data", {}):
                require(acknowledged["data"][field] == c["payload"][0][field])
        note, _ = await reader.request("GET", f"{base}/items/{note_key}")
        verify_note(note, c)
        require(note["version"] == acknowledged["version"])
        after, _ = await reader.request("GET", f"{base}/items/{c['key']}")
        after_attachment, _ = await reader.request(
            "GET", f"{base}/items/{attachment['key']}"
        )
        item_identity(after, c["key"], c["library"])
        require(semantic(after) == semantic(parent))
        require(after_attachment["data"] == attachment["data"])
        after_children = await inventory(reader, base, c["key"])
        require(len(after_children) == len(children) + 1)
        require(
            {v["key"]: v["data"] for v in after_children if v["key"] != note_key}
            == {v["key"]: v["data"] for v in children}
        )
        found = [v for v in after_children if v["key"] == note_key]
        require(len(found) == 1 and found[0]["data"] == note["data"])
        save_new(
            result_path,
            {
                "status": "written-verified",
                "binding": c["binding"],
                "note_key": note_key,
                "note_version": note["version"],
                "note": note,
                "parent_after": after,
                "attachment_after": after_attachment,
                "children_after": after_children,
                "model_calls": 0,
                "post_attempts": 1,
                "canonical_stage_acceptance": "not-claimed",
                "automatic_rollback": False,
            },
        )
        return {"status": "written-verified", "note_key": note_key}
    except Exception as exc:
        if reserved:
            if not result_path.exists():
                save_new(
                    result_path,
                    {
                        "status": "needs-review",
                        "note_key": note_key,
                        "error_class": type(exc).__name__,
                        "model_calls": 0,
                        "post_attempts": 1,
                    },
                )
            return {"status": "needs-review", "note_key": note_key}
        raise
    finally:
        await reader.close()
        if writer:
            await writer.close()
