"""Explicit OpenKB conversion, not accepted-run or model-execution evidence.

All input is bounded and no-follow read. Caller pins both the PDF and the
upstream manifest; provenance retains the provider alias without guessing a
backend version. Writes use the existing native source/extraction writers in a
private staging directory, then the existing guarded metadata transaction.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from millefeuille.domain.extraction_fixtures import (
    write_native_extractions_from_evidence,
    write_ocr_extractions_from_evidence,
)
from millefeuille.domain.fresh_directory import checked_staging_root, reserve_directory
from millefeuille.domain.metadata_transaction import (
    metadata_read_guard,
    require_metadata_write_support,
    write_metadata_transaction,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    write_source_pack_from_recovered_pdf,
)

SCHEMA = "millefeuille-openkb-import/v0.1"
MAX_BYTES = 32 * 1024 * 1024


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    return read_bytes_no_follow(Path(path), "OpenKB import input", max_bytes=MAX_BYTES)


def _json(data):
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _require(condition, message):
    if not condition:
        raise MillefeuilleContractError(message)


def import_openkb(
    *,
    pack,
    pdf,
    source_sha256,
    manifest_sha256,
    evidence_sha256,
    output,
    protected_staging_root=None,
):
    """Convert already-produced evidence; never OCR, classify, or assert acceptance."""
    require_metadata_write_support()
    pack, pdf, output = Path(pack), Path(pdf), Path(output)
    stage_root = checked_staging_root(protected_staging_root, [output])
    manifest_bytes = _read(pack / "manifest.json")
    _require(
        _hash(manifest_bytes) == manifest_sha256, "upstream manifest hash mismatch"
    )
    source = _read(pdf)
    _require(_hash(source) == source_sha256, "source PDF hash mismatch")
    m = json.loads(manifest_bytes)
    _require(
        m.get("schema_version") == "openkb-docai-source-pack/v0.1",
        "unsupported OpenKB schema",
    )
    _require(
        m.get("source_hash") == {"algorithm": "sha256", "value": source_sha256},
        "OpenKB source identity mismatch",
    )
    _require(m.get("source_bytes") == len(source), "OpenKB source size mismatch")
    count = m.get("page_count")
    _require(type(count) is int and 0 < count <= 500, "invalid page count")
    metadata = m.get("metadata", {})
    _require(
        metadata.get("expected_sha256") == source_sha256,
        "metadata source identity mismatch",
    )
    total_bytes = len(source) + len(manifest_bytes)
    input_snapshots = [(pack / "manifest.json", manifest_bytes), (pdf, source)]
    payloads = {}
    page_inventory = []
    for route, prefix in (
        ("native", "extractions/native"),
        ("mistral-ocr", "extractions/mistral-ocr"),
        ("selected", ""),
    ):
        info = (
            m.get("extractions", {}).get(route)
            if route != "selected"
            else m.get("selected_reconstruction")
        )
        _require(
            isinstance(info, dict)
            and info.get("status") == "ok"
            and info.get("page_count") == count,
            "incomplete extraction",
        )
        texts = []
        for page in range(1, count + 1):
            ref = f"{prefix + '/' if prefix else ''}pages/page-{page:04d}.md"
            data = _read(pack / ref)
            total_bytes += len(data)
            _require(
                total_bytes <= 64 * 1024 * 1024, "import exceeds cumulative byte budget"
            )
            input_snapshots.append((pack / ref, data))
            _require(bool(data.strip()), "empty imported page")
            text = data.decode("utf-8")
            texts.append(text)
            page_inventory.append(
                {"route": route, "page": page, "sha256": _hash(data), "text": text}
            )
        payloads[route] = "\n\n".join(texts)
    raw = _read(pack / "raw/provider-response.json")
    input_snapshots.append((pack / "raw/provider-response.json", raw))
    _require(
        total_bytes + len(raw) <= 64 * 1024 * 1024,
        "import exceeds cumulative byte budget",
    )
    inventory = {
        str(path.relative_to(pack)): _hash(data)
        for path, data in input_snapshots
        if path != pdf
    }
    snapshot = _hash(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    )
    _require(snapshot == evidence_sha256, "extraction snapshot hash mismatch")
    provider = json.loads(raw)
    _require(len(provider.get("pages", [])) == count, "provider page count mismatch")
    _require(
        [p.get("index") for p in provider["pages"]] == list(range(count)),
        "provider page order mismatch",
    )
    returned = provider.get("model")
    requested = m["extractions"]["mistral-ocr"].get("ocr_model")
    _require(
        isinstance(returned, str)
        and bool(returned)
        and isinstance(requested, str)
        and bool(requested),
        "missing provider model provenance",
    )
    _require(
        provider.get("usage_info", {}).get("pages_processed") == count,
        "provider usage page mismatch",
    )
    # Raw responses remain only at the original private path, never copied.
    # The imported texts are private source content, not instructions.
    _require(
        not output.exists() and not output.is_symlink(),
        "import destination already exists",
    )
    with tempfile.TemporaryDirectory(
        dir=stage_root, prefix="millefeuille-openkb-"
    ) as temporary:
        temp = Path(temporary)
        staged_pdf = temp / "source.pdf"
        staged_pdf.write_bytes(source)
        evidence = RecoveredPdfEvidence(
            item_key=metadata["item_key"],
            attachment_key=metadata["attachment_key"],
            canonical_filename=m["canonical_filename"],
            recovered_pdf_path=staged_pdf,
            expected_sha256=source_sha256,
            item_title=metadata.get("item_title"),
            file_size_bytes=len(source),
        )
        native_root = temp / "source-packs"
        result = write_source_pack_from_recovered_pdf(
            evidence=evidence, source_pack_root=native_root
        )
        common = {
            "item_key": evidence.item_key,
            "attachment_key": evidence.attachment_key,
            "canonical_filename": evidence.canonical_filename,
            "expected_sha256": source_sha256,
            "page_count": count,
            "warnings": [
                "Imported existing OpenKB extraction; not a native provider execution."
            ],
        }
        for route, writer in (
            ("native", write_native_extractions_from_evidence),
            ("mistral-ocr", write_ocr_extractions_from_evidence),
        ):
            markdown = temp / f"{route}.md"
            markdown.write_text(payloads[route], encoding="utf-8")
            row = dict(common, markdown_path=str(markdown))
            if route == "native":
                row["tool"] = m["extractions"]["native"]["tool"]
            else:
                row.update(
                    provider_version=returned,
                    requested_model=requested,
                    provider_payload_disposition="retained-outside-committed-files",
                    raw_provider_json_retained_private=True,
                )
            entry = temp / f"{route}.json"
            entry.write_text(_json(row), encoding="utf-8")
            writer(evidence_path=entry, source_pack_root=native_root)
        native_manifest = json.loads(result.manifest_path.read_text())
        # Explicit conversion provenance, no fabricated run/stage/model outcomes.
        native_manifest["provenance"] = {
            "intake_method": "validated-openkb-import",
            "upstream_manifest_sha256": manifest_sha256,
        }
        outputs = {"converted-source-manifest.json": _json(native_manifest)}
        for ref in (
            "extractions/native/fulltext.md",
            "extractions/native/evidence.json",
            "extractions/mistral-ocr/fulltext.md",
            "extractions/mistral-ocr/evidence.json",
        ):
            outputs[ref] = (result.source_pack_dir / ref).read_text()
    record = {
        "schema_version": SCHEMA,
        "source_hash": "sha256:" + source_sha256,
        "paper_id": result.paper_id,
        "page_count": count,
        "upstream_manifest_sha256": manifest_sha256,
        "evidence_sha256": snapshot,
        "raw_provider_sha256": _hash(raw),
        "requested_model": requested,
        "returned_model": returned,
        "immutable_backend_version": None,
        "usage_info": provider.get("usage_info"),
        "doi": metadata.get("recovery", {}).get("DOI"),
        "origin": "OpenKB helper native + Mistral OCR",
        "provider_calls": 0,
        "accepted_run": False,
        "pages": page_inventory,
    }
    outputs["imported-evidence.json"] = _json(record)
    outputs["selected/fulltext.md"] = payloads["selected"]
    # The original PDF is hash-bound but not duplicated by this text transaction.
    # A source pack requiring source.pdf must continue through native intake.
    outputs["import-status.json"] = _json(
        {
            "status": "extraction-evidence-imported",
            "source_pdf_copied": False,
            "accepted_run": False,
        }
    )
    _require(
        _read(pack / "manifest.json") == manifest_bytes and _read(pdf) == source,
        "source changed during import",
    )

    def check_inputs():
        for path, expected in input_snapshots:
            _require(_read(path) == expected, "input changed during import")

    reservation_options = (
        {"protected_staging_root": protected_staging_root}
        if protected_staging_root is not None
        else {}
    )
    with reserve_directory(output, **reservation_options) as (_, identity):
        write_metadata_transaction(
            output,
            outputs,
            expected_before=dict.fromkeys(outputs),
            expected_directory_identity=identity,
            check_inputs=check_inputs,
        )
    return {
        "status": "extraction-evidence-imported",
        "paper_id": result.paper_id,
        "page_count": count,
        "source_hash": record["source_hash"],
        "accepted_run": False,
        "provider_calls": 0,
    }


def retrieve_imported_pages(*, output, pages):
    """Hash-checked page selection, explicitly not accepted-run retrieval."""
    with metadata_read_guard(output):
        record = json.loads(_read(Path(output) / "imported-evidence.json"))
    _require(record.get("schema_version") == SCHEMA, "unsupported import schema")
    _require(
        bool(pages)
        and all(type(p) is int and 1 <= p <= record["page_count"] for p in pages),
        "invalid page selection",
    )
    selected = [
        p for p in record["pages"] if p["route"] == "selected" and p["page"] in pages
    ]
    for page in selected:
        _require(
            _hash(page["text"].encode()) == page["sha256"],
            "imported page hash mismatch",
        )
    _require(len(selected) == len(set(pages)), "missing imported page")
    return {
        "source_hash": record["source_hash"],
        "accepted_run": False,
        "selection_kind": "imported-extraction-pages",
        "pages": selected,
    }
