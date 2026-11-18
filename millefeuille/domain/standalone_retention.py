"""Standalone PDF retention, with immutable intents and cloud readback proof.

Trusted private local filesystem, cooperating writers only. No cloud mutations.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from millefeuille.domain.standalone_writeback import (
    digest,
    load,
    read_bytes,
    require,
    safe_path,
    save_new,
    sha,
)

PDF_CAP = 50_000_000
SCHEMA = "millefeuille-standalone-retention/v1"


def _identity(row, key, manifest):
    require(row["key"] == row["data"]["key"] == key)
    require(row["library"]["type"] == manifest["library_type"])
    require(str(row["library"]["id"]) == manifest["library_id"])
    require(type(row["version"]) is int and row["version"] > 0)
    require(row["version"] == row["data"]["version"])


def _local(run_dir):
    run = safe_path(run_dir)
    paper = run.parent.parent
    require(run.parent.name == "runs")
    for directory in (paper, run.parent, run):
        info = directory.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid())
        require(info.st_mode & 0o077 == 0)
    manifest = load(paper / "manifest.json")
    done = load(run / "06-completed.json")
    require(manifest["schema_version"] == "millefeuille-standalone-source-pack/v1")
    require(manifest["library_type"] in {"user", "group"})
    require(re.fullmatch(r"[1-9][0-9]{0,14}", manifest["library_id"]))
    for field in ("item_key", "attachment_key"):
        require(re.fullmatch(r"[A-Z0-9]{8}", manifest[field]))
    require(
        paper.name
        == f"{manifest['library_type']}-{manifest['library_id']}-{manifest['item_key']}"
    )
    require(done["status"] == "local-processing-complete")
    require(done["run_dir"] == str(run))
    reservation = load(paper / "reservation.json")
    for field in (
        "run_id",
        "library_type",
        "library_id",
        "item_key",
        "attachment_key",
        "item_version",
        "attachment_version",
        "max_items",
        "zotero_writeback",
    ):
        require(manifest[field] == done[field] == reservation[field])
    require(manifest["run_id"] == run.name and manifest["max_items"] == 1)
    require(manifest["zotero_writeback"] == "none")
    require(
        manifest["provenance"]
        == {
            "intake_method": "live-zotero-api",
            "selection_ref": f"runs/{run.name}/00-selected.json",
        }
    )
    source = manifest["source"]
    require(source["ref"] == "source.pdf")
    require(re.fullmatch(r"[a-f0-9]{64}", source["sha256"]))
    require(source["sha256"] == done["source_sha256"])
    require(type(source["byte_size"]) is int and 0 < source["byte_size"] <= PDF_CAP)
    selected = load(run / "00-selected.json")
    _identity(selected["item"], manifest["item_key"], manifest)
    attachment = selected["attachment"]
    _identity(attachment, manifest["attachment_key"], manifest)
    require(selected["item"]["version"] == manifest["item_version"])
    require(attachment["version"] == manifest["attachment_version"])
    require(attachment["data"]["parentItem"] == manifest["item_key"])
    require(attachment["data"]["contentType"] == "application/pdf")
    require(attachment["data"]["linkMode"] in {"imported_file", "imported_url"})
    require(re.fullmatch(r"[a-f0-9]{32}", attachment["data"]["md5"]))
    pins = {}
    for name in (
        "00-selected.json",
        "01-download-started.json",
        "02-ocr-started.json",
        "03-ocr-completed.json",
        "04-analysis-started.json",
        "05-analysis-completed.json",
        "06-completed.json",
        "taxonomy.json",
        "analysis/taxonomy-lock.json",
        "ocr/direct-mistral/reservation.json",
        "ocr/direct-mistral/cleanup.json",
    ):
        pins[name] = sha(read_bytes(run / name, 100_000_000))
    for stage, names in (
        (
            "03-ocr-completed.json",
            {"fulltext_path", "evidence_path", "raw_response_path"},
        ),
        (
            "05-analysis-completed.json",
            {"summary_path", "classification_path", "evidence_path"},
        ),
    ):
        record = load(run / stage)
        require(set(record["files"]) == names)
        for pin in record["files"].values():
            ref = Path(pin["ref"])
            require(
                not ref.is_absolute() and ".." not in ref.parts and ref.suffix != ".pdf"
            )
            require(sha(read_bytes(run / ref, 100_000_000)) == pin["sha256"])
            pins[ref.as_posix()] = pin["sha256"]
    require(
        {
            "ocr/direct-mistral/evidence.json",
            "ocr/direct-mistral/fulltext.md",
            "ocr/direct-mistral/provider-response.json",
            "analysis/summary.json",
            "analysis/classification.json",
            "analysis/evidence.json",
        }
        <= set(pins)
    )
    ocr = load(run / "ocr/direct-mistral/evidence.json")
    require(
        ocr["raw_response_sha256"] == pins["ocr/direct-mistral/provider-response.json"]
    )
    require(ocr["fulltext_sha256"] == pins["ocr/direct-mistral/fulltext.md"])
    analysis = load(run / "analysis/evidence.json")
    require(analysis["source_sha256"] == "sha256:" + ocr["fulltext_sha256"])
    require(
        analysis["summary_sha256"]
        == "sha256:" + digest(load(run / "analysis/summary.json"))
    )
    require(
        analysis["classification_sha256"]
        == "sha256:" + digest(load(run / "analysis/classification.json"))
    )
    require(ocr["source_sha256"] == source["sha256"])
    require(ocr["original_pdf_path"] == str(run / "ocr/direct-mistral/original.pdf"))
    require(ocr["input_bytes"] == source["byte_size"] and ocr["cleanup"] == "deleted")
    require(load(run / "ocr/direct-mistral/cleanup.json")["status"] == "deleted")
    binding = {
        "run_dir": str(run),
        "manifest_sha256": sha(read_bytes(paper / "manifest.json")),
        "reservation_sha256": sha(read_bytes(paper / "reservation.json")),
        "artifacts": pins,
        "source": source,
        "library_type": manifest["library_type"],
        "library_id": manifest["library_id"],
        "item_key": manifest["item_key"],
        "attachment_key": manifest["attachment_key"],
        "attachment_md5": attachment["data"]["md5"],
    }
    return run, manifest, binding


def _pdf_paths(run):
    return (run.parent.parent / "source.pdf", run / "ocr/direct-mistral/original.pdf")


def _pdf(path, binding):
    before = path.stat(follow_symlinks=False)
    raw = read_bytes(path, PDF_CAP)
    require(raw.startswith(b"%PDF-") and len(raw) == binding["source"]["byte_size"])
    require(sha(raw) == binding["source"]["sha256"])
    require(
        hashlib.md5(raw, usedforsecurity=False).hexdigest() == binding["attachment_md5"]
    )
    info = path.stat(follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    require(
        (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        == (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    )
    return {
        "dev": info.st_dev,
        "ino": info.st_ino,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


async def _cloud(manifest, binding):
    from millefeuille.cli.standalone import _get, _json

    secret = os.environ.get("ZOTERO_READ_KEY", "")
    require(re.fullmatch(r"[A-Za-z0-9]{1,128}", secret))
    require(os.environ.get("ZOTERO_LIBRARY_ID") == manifest["library_id"])
    prefix = "users" if manifest["library_type"] == "user" else "groups"
    base = f"https://api.zotero.org/{prefix}/{manifest['library_id']}"
    async with asyncio.timeout(120):
        parent_raw = await _get(
            f"{base}/items/{manifest['item_key']}", secret, cap=1_000_000
        )
        parent = _json(parent_raw)
        _identity(parent, manifest["item_key"], manifest)
        require(parent["data"]["itemType"] not in {"attachment", "note"})
        raw = await _get(
            f"{base}/items/{manifest['attachment_key']}", secret, cap=1_000_000
        )
        attachment = _json(raw)
        _identity(attachment, manifest["attachment_key"], manifest)
        data = attachment["data"]
        require(data["parentItem"] == manifest["item_key"])
        require(data["contentType"] == "application/pdf")
        require(data["linkMode"] in {"imported_file", "imported_url"})
        require(data["md5"] == binding["attachment_md5"])
        pdf = await _get(
            f"{base}/items/{manifest['attachment_key']}/file",
            secret,
            cap=PDF_CAP,
            pdf=True,
        )
        require(pdf.startswith(b"%PDF-") and len(pdf) == binding["source"]["byte_size"])
        require(sha(pdf) == binding["source"]["sha256"])
        require(hashlib.md5(pdf, usedforsecurity=False).hexdigest() == data["md5"])
        # Catch attachment changes during download; do not pin parent version.
        after = await _get(
            f"{base}/items/{manifest['attachment_key']}", secret, cap=1_000_000
        )
        require(_json(after) == attachment)
    receipt = {
        "method": "fresh-zotero-metadata-and-bounded-download/v1",
        "verified_at": datetime.now(UTC).isoformat(),
        "binding_sha256": digest(binding),
        "parent_sha256": sha(parent_raw),
        "attachment_sha256": sha(raw),
        "parent_version": parent["version"],
        "attachment_version": attachment["version"],
        "pdf_sha256": sha(pdf),
        "pdf_md5": data["md5"],
        "byte_size": len(pdf),
    }
    return receipt, pdf


def _receipt(receipt, binding):
    require(receipt["method"] == "fresh-zotero-metadata-and-bounded-download/v1")
    require(receipt["binding_sha256"] == digest(binding))
    require(receipt["pdf_sha256"] == binding["source"]["sha256"])
    require(receipt["pdf_md5"] == binding["attachment_md5"])
    require(receipt["byte_size"] == binding["source"]["byte_size"])
    for field in ("parent_sha256", "attachment_sha256"):
        require(re.fullmatch(r"[a-f0-9]{64}", receipt[field]))
    require(datetime.fromisoformat(receipt["verified_at"]).tzinfo is not None)


def validate_completed_retention(run_dir):
    """Local historical receipt validation, not a claim of current cloud existence."""
    run, _, binding = _local(run_dir)
    intent = load(run / "07-retention-intent.json")
    done = load(run / "08-retention-completed.json")
    require(intent["schema"] == done["schema"] == SCHEMA)
    require(intent["binding"] == binding)
    require(done["intent_sha256"] == sha(read_bytes(run / "07-retention-intent.json")))
    require(
        done["status"] == "no-local-pdf" and done["binding_sha256"] == digest(binding)
    )
    _receipt(intent["cloud_readback"], binding)
    _receipt(done["cloud_readback"], binding)
    require(set(intent["files"]) == {str(p) for p in _pdf_paths(run)})
    for path in _pdf_paths(run):
        safe_path(path)
        require(not path.exists())
    return binding


def retain(run_dir, *, live=False, reconcile=False, verify_cloud=False):
    run, manifest, binding = _local(run_dir)
    intent_path = run / "07-retention-intent.json"
    done_path = run / "08-retention-completed.json"
    if done_path.exists():
        validate_completed_retention(run)
        if verify_cloud:
            receipt, pdf = asyncio.run(_cloud(manifest, binding))
            del pdf
            return {
                "status": "no-local-pdf-cloud-reverified",
                "cloud_readback": receipt,
            }
        return {
            "status": "no-local-pdf",
            "cloud_verification": "historical-not-current",
        }
    require(not verify_cloud)  # Verification never silently performs retention.
    safe_path(intent_path)
    safe_path(done_path)
    existing = load(intent_path) if intent_path.exists() else None
    if existing:
        require(existing["schema"] == SCHEMA and existing["binding"] == binding)
        _receipt(existing["cloud_readback"], binding)
        require(set(existing["files"]) == {str(p) for p in _pdf_paths(run)})
    files = {}
    for path in _pdf_paths(run):
        safe_path(path)
        if path.exists():
            files[str(path)] = _pdf(path, binding)
            if existing:
                require(files[str(path)] == existing["files"][str(path)])
        else:
            require(existing is not None)
    if not live:
        return {
            "status": "reconciliation-required" if existing else "retention-plan",
            "files_present": list(files),
            "source_sha256": binding["source"]["sha256"],
            "network_calls": 0,
            "files_written": 0,
        }
    require(not existing or reconcile)
    require(not live or not verify_cloud)
    receipt, pdf = asyncio.run(_cloud(manifest, binding))
    del pdf
    # Revalidate all retained evidence after network I/O, before intent or unlink.
    require(_local(run)[2] == binding)
    if not existing:
        save_new(
            intent_path,
            {
                "schema": SCHEMA,
                "binding": binding,
                "files": files,
                "cloud_readback": receipt,
            },
        )
    # Validate the entire deletion set before the first destructive operation.
    for path in _pdf_paths(run):
        if str(path) in files:
            require(_pdf(path, binding) == files[str(path)])
        else:
            safe_path(path)
            require(not path.exists())
    for path in _pdf_paths(run):
        if str(path) in files:
            require(_pdf(path, binding) == files[str(path)])
            path.unlink()
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    save_new(
        done_path,
        {
            "schema": SCHEMA,
            "status": "no-local-pdf",
            "intent_sha256": sha(read_bytes(intent_path)),
            "binding_sha256": digest(binding),
            "cloud_readback": receipt,
        },
    )
    validate_completed_retention(run)
    return {"status": "no-local-pdf", "source_sha256": binding["source"]["sha256"]}


@contextmanager
def verified_pdf(run_dir):
    """Explicit cloud read for visual checks; temporary PDF removed on context exit.

    No OCR/model call. Caller must not copy/retain the file. Abrupt process death
    may leave an OS temporary file; this is not crash-proof secure erasure.
    """
    _, manifest, binding = _local(run_dir)
    _, pdf = asyncio.run(_cloud(manifest, binding))
    with tempfile.TemporaryDirectory(prefix="millefeuille-visual-") as directory:
        path = Path(directory) / "verified.pdf"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(pdf)
        del pdf
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)
