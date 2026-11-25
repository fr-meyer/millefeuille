"""Validate retained-source retry lineage without rewriting historical manifests."""

from pathlib import Path
import re

from millefeuille.domain.standalone_writeback import (
    load,
    parse,
    read_bytes,
    require,
    safe_path,
    sha,
)


def validate(run_dir, *, require_pdf=True):
    """Authenticate lineage; only read-only evidence consumers may allow removed PDF."""
    require(type(require_pdf) is bool)
    run = safe_path(run_dir)
    paper = run.parent.parent
    raw = read_bytes(paper / "manifest.json")
    manifest = parse(raw)
    require(manifest["schema_version"] == "millefeuille-standalone-source-pack/v1")
    require(run.parent.name == "runs")
    require(
        paper.name
        == f"{manifest['library_type']}-{manifest['library_id']}-{manifest['item_key']}"
    )
    old = manifest["run_id"]
    require(bool(re.fullmatch(r"[a-f0-9]{32}", old)))
    require(
        manifest["provenance"]
        == {
            "intake_method": "live-zotero-api",
            "selection_ref": f"runs/{old}/00-selected.json",
        }
    )
    selected = load(run / "00-selected.json")
    for field, version in (
        ("item", "item_version"),
        ("attachment", "attachment_version"),
    ):
        item = selected[field]
        require(
            item["key"] == manifest["item_key" if field == "item" else "attachment_key"]
        )
        require(item["version"] == item["data"]["version"] == manifest[version])
        require(item["library"]["type"] == manifest["library_type"])
        require(str(item["library"]["id"]) == str(manifest["library_id"]))
    require(selected["attachment"]["data"]["parentItem"] == manifest["item_key"])
    binding = {"manifest_sha256": sha(raw), "original_run_id": old, "run_id": run.name}
    if old != run.name:
        receipt_raw = read_bytes(paper / "upload-401-retry.json")
        receipt = parse(receipt_raw)
        require(receipt["run_id"] == run.name and receipt["original_run_id"] == old)
        require(
            receipt["manifest_sha256"] == sha(raw)
            and receipt["automatic_retry"] is False
        )
        for field in (
            "library_type",
            "library_id",
            "item_key",
            "attachment_key",
            "item_version",
            "attachment_version",
            "tag",
            "max_items",
            "zotero_writeback",
        ):
            require(receipt[field] == manifest[field])
        retained = load(run / "01-retained-source.json")
        require(
            retained == {**receipt, "intake_method": "verified-retained-original-pdf"}
        )
        require(selected == load(paper / manifest["provenance"]["selection_ref"]))
        pins = receipt["original_evidence_sha256"]
        require(pins["manifest.json"] == sha(raw))
        require(manifest["provenance"]["selection_ref"] in pins)
        require(
            pins["source.pdf"]
            == receipt["source_sha256"]
            == manifest["source"]["sha256"]
        )
        failure = f"runs/{old}/ocr/direct-mistral/failure.json"
        require(pins[failure] == receipt["failure_sha256"])
        for ref, pin in pins.items():
            relative = Path(ref)
            require(not relative.is_absolute() and ".." not in relative.parts)
            evidence_path = safe_path(paper / relative)
            if ref == "source.pdf" and not require_pdf and not evidence_path.exists():
                # The retained receipt above still binds this exact source hash.
                # Only local PDF bytes may be absent after separately gated retention.
                continue
            require(sha(read_bytes(evidence_path, 100 * 1024 * 1024)) == pin)
        binding["retry_receipt_sha256"] = sha(receipt_raw)
        binding["retained_receipt_sha256"] = sha(
            read_bytes(run / "01-retained-source.json")
        )
    require(manifest["source"]["ref"] == "source.pdf")
    source_path = safe_path(paper / "source.pdf")
    if not require_pdf and not source_path.exists():
        return manifest, selected, binding
    pdf = read_bytes(source_path, 100 * 1024 * 1024)
    require(pdf.startswith(b"%PDF-") and len(pdf) == manifest["source"]["byte_size"])
    require(sha(pdf) == manifest["source"]["sha256"])
    import hashlib

    require(
        hashlib.md5(pdf, usedforsecurity=False).hexdigest()
        == selected["attachment"]["data"]["md5"]
    )
    return manifest, selected, binding
