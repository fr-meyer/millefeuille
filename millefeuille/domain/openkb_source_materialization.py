"""Materialize a real imported extraction into a separate native source-pack root.

This does not synthesize summary, index, acceptance, or classification evidence.
Each stage uses its existing product writer, with explicit import provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from millefeuille.domain.extraction_fixtures import (
    NATIVE_EVIDENCE_REF,
    NATIVE_MARKDOWN_REF,
    OCR_EVIDENCE_REF,
    OCR_MARKDOWN_REF,
)
from millefeuille.domain.fresh_directory import checked_staging_root, reserve_directory
from millefeuille.domain.local_structure import (
    prepare_local_structures_from_route_evidence,
)
from millefeuille.domain.metadata_transaction import write_metadata_transaction
from millefeuille.domain.openkb_import import _read, _require, retrieve_imported_pages
from millefeuille.domain.route_fixtures import write_route_selections_from_evidence
from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    load_source_pack_manifest,
    write_source_pack_from_recovered_pdf,
)
from millefeuille.domain.structure_fixtures import write_structures_from_evidence


def _stage_imported_source(*, imported, pdf, source_pack_root, structure_output):
    imported, pdf, root, structure_output = map(
        Path, (imported, pdf, source_pack_root, structure_output)
    )
    record = json.loads(_read(imported / "imported-evidence.json"))
    source_hash = record["source_hash"].removeprefix("sha256:")
    _require(
        hashlib.sha256(_read(pdf)).hexdigest() == source_hash,
        "source changed since import",
    )
    _require(
        not (root / "zotero" / record["paper_id"]).exists(),
        "destination source pack already exists",
    )
    native_manifest = json.loads(_read(imported / "converted-source-manifest.json"))
    _require(
        native_manifest.get("source_hash") == record["source_hash"],
        "converted source hash mismatch",
    )
    _require(
        native_manifest.get("paper_id") == record["paper_id"],
        "converted paper identity mismatch",
    )
    ident = native_manifest["identity"]
    evidence = RecoveredPdfEvidence(
        item_key=ident["zotero_item_key"],
        attachment_key=ident["zotero_attachment_key"],
        canonical_filename=ident["canonical_filename"],
        recovered_pdf_path=pdf,
        expected_sha256=source_hash,
        file_size_bytes=ident["file_size_bytes"],
        item_title=ident.get("item_title"),
        zotero_version=ident.get("zotero_version"),
    )
    result = write_source_pack_from_recovered_pdf(
        evidence=evidence, source_pack_root=root
    )
    source_pack = result.source_pack_dir
    _require(result.paper_id == record["paper_id"], "paper identity mismatch")
    _require(
        _read(result.source_path) == _read(pdf), "source pack PDF readback mismatch"
    )
    # Existing native source-pack schema and identity are retained; imported
    # extraction sidecars were produced by the existing product writers.
    extraction_outputs = {}
    for ref in (
        NATIVE_EVIDENCE_REF,
        NATIVE_MARKDOWN_REF,
        OCR_EVIDENCE_REF,
        OCR_MARKDOWN_REF,
    ):
        extraction_outputs[str(ref)] = _read(imported / ref).decode("utf-8")
    write_metadata_transaction(
        source_pack,
        extraction_outputs,
        expected_before=dict.fromkeys(extraction_outputs),
    )
    retrieved = retrieve_imported_pages(
        output=imported, pages=list(range(1, record["page_count"] + 1))
    )
    _require(
        len(retrieved["pages"]) == record["page_count"], "incomplete imported pages"
    )
    joined = "\n\n".join(page["text"] for page in retrieved["pages"])
    _require(
        _read(imported / "selected/fulltext.md").decode("utf-8") == joined,
        "selected reconstruction drift",
    )
    marked = structure_output / "selected-with-page-markers.md"
    marked.parent.mkdir(parents=True, exist_ok=True)
    marked.write_text(
        "\n\n".join(
            f"# Page {page['page']}\n\n{page['text']}" for page in retrieved["pages"]
        ),
        encoding="utf-8",
    )
    common = {
        "item_key": evidence.item_key,
        "attachment_key": evidence.attachment_key,
        "canonical_filename": evidence.canonical_filename,
        "expected_sha256": source_hash,
        "page_count": record["page_count"],
        "selected_route": "merged-dual",
        "paper_id": result.paper_id,
        "reason": "Existing OpenKB merged-dual text, validated import",
        "markdown_path": str(marked),
    }
    route_evidence = structure_output / "route-evidence.json"
    route_evidence.parent.mkdir(parents=True, exist_ok=True)
    route_evidence.write_text(json.dumps(common, sort_keys=True) + "\n")
    route = write_route_selections_from_evidence(
        evidence_path=route_evidence, source_pack_root=root
    )[0]
    prepared = prepare_local_structures_from_route_evidence(
        route_evidence_paths=[route_evidence], output_dir=structure_output / "computed"
    )
    structure = write_structures_from_evidence(
        evidence_path=prepared.documents[0].evidence_path, source_pack_root=root
    )[0]
    _require(
        load_source_pack_manifest(source_pack / "manifest.json")["source_hash"]
        == record["source_hash"],
        "source manifest readback mismatch",
    )
    _require(
        prepared.documents[0].page_count == record["page_count"],
        "structure page coverage incomplete",
    )
    return {
        "status": "source-route-structure-materialized",
        "source_pack_dir": str(source_pack),
        "source_hash": record["source_hash"],
        "route_status": route.status,
        "structure_status": structure.status,
        "structure_page_count": prepared.documents[0].page_count,
        "verified_pages": len(retrieved["pages"]),
        "accepted_run": False,
    }


def materialize_imported_source(
    *, imported, pdf, source_pack_root, structure_output, protected_staging_root=None
):
    """Compute privately, then publish only into exclusively reserved destinations.

    Failure retains owned partial outputs and metadata recovery journals.
    """
    stage_root = checked_staging_root(
        protected_staging_root,
        [Path(source_pack_root) / "zotero" / "pending-paper", structure_output],
    )
    with tempfile.TemporaryDirectory(
        prefix="millefeuille-materialize-", dir=stage_root
    ) as temp:
        staged_root = Path(temp) / "source-packs"
        staged_structure = Path(temp) / "structure"
        result = _stage_imported_source(
            imported=imported,
            pdf=pdf,
            source_pack_root=staged_root,
            structure_output=staged_structure,
        )
        staged_pack = Path(result["source_pack_dir"])
        destination = Path(source_pack_root) / staged_pack.relative_to(staged_root)
        structure_output = Path(structure_output)

        def text_outputs(directory):
            outputs = {}
            for p in directory.rglob("*"):
                if (
                    not p.is_file()
                    or p.name == "source.pdf"
                    or any(
                        part.startswith(".millefeuille-metadata-") for part in p.parts
                    )
                ):
                    continue
                ref = p.relative_to(directory).as_posix()
                text = _read(p).decode("utf-8")
                if p.suffix == ".json":
                    payload = json.loads(text)
                    if directory == staged_structure and ref == "route-evidence.json":
                        _require(
                            payload["markdown_path"]
                            == str(staged_structure / "selected-with-page-markers.md"),
                            "unexpected route markdown path",
                        )
                        payload["markdown_path"] = str(
                            structure_output / "selected-with-page-markers.md"
                        )
                        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
                    elif (
                        directory == staged_structure and ref == "computed/summary.json"
                    ):
                        _require(
                            payload["output_dir"] == str(staged_structure / "computed"),
                            "unexpected structure output path",
                        )
                        payload["output_dir"] = str(structure_output / "computed")
                        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
                outputs[ref] = text
            return outputs

        outputs = text_outputs(staged_pack)
        structure_outputs = text_outputs(staged_structure)
        source = _read(staged_pack / "source.pdf")
        reservation_options = (
            {"protected_staging_root": protected_staging_root}
            if protected_staging_root is not None
            else {}
        )
        with (
            reserve_directory(destination, **reservation_options) as (fd, identity),
            reserve_directory(structure_output, **reservation_options) as (
                _,
                structure_identity,
            ),
        ):
            # Binary source uses the retained owned descriptor.
            pdf_fd = os.open(
                "source.pdf",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=fd,
            )
            with os.fdopen(pdf_fd, "wb") as stream:
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
            write_metadata_transaction(
                destination,
                outputs,
                expected_before=dict.fromkeys(outputs),
                expected_directory_identity=identity,
            )
            write_metadata_transaction(
                structure_output,
                structure_outputs,
                expected_before=dict.fromkeys(structure_outputs),
                expected_directory_identity=structure_identity,
            )
            _require(
                _read(destination / "source.pdf") == source,
                "published PDF readback mismatch",
            )
            for directory, expected in (
                (destination, outputs),
                (structure_output, structure_outputs),
            ):
                for ref, content in expected.items():
                    raw = _read(directory / ref)
                    _require(
                        raw == content.encode("utf-8"),
                        "published artifact readback mismatch",
                    )
                    if ref.endswith(".json"):
                        json.loads(raw)
            route = json.loads(_read(structure_output / "route-evidence.json"))
            _require(
                _read(Path(route["markdown_path"]))
                == _read(staged_structure / "selected-with-page-markers.md"),
                "published route reference mismatch",
            )
            summary = json.loads(_read(structure_output / "computed/summary.json"))
            for document in summary["documents"]:
                for key in ("structure_path", "evidence_path", "outline_path"):
                    _require(
                        _read(Path(summary["output_dir"]) / document[key])
                        == _read(staged_structure / "computed" / document[key]),
                        "published structure reference mismatch",
                    )
        result["source_pack_dir"] = str(destination)
        return result
