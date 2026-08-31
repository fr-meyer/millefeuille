"""Deterministic provider-free structure preparation from selected Markdown."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.route_fixtures import (
    RouteSelectionFixtureEvidence,
    load_route_selection_evidence_batch,
)
from millefeuille.domain.secure_io import (
    read_bytes_no_follow,
    read_text_no_follow,
    write_new_text_no_follow,
)
from millefeuille.domain.source_packs import paper_id_for_zotero_item_key
from millefeuille.domain.structure_fixtures import STRUCTURE_EVIDENCE_SCHEMA_VERSION

LOCAL_STRUCTURE_SCHEMA_VERSION = "millefeuille-local-markdown-structure/v0.1"
LOCAL_STRUCTURE_SUMMARY_SCHEMA_VERSION = (
    "millefeuille-local-structure-preparation-summary/v0.1"
)
LOCAL_STRUCTURE_BACKEND = "local-markdown-headings-v0.1"

_PAGE_HEADING_RE = re.compile(r"^#{1,6}\s+Page\s+(\d+)\s*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_FIGURE_CAPTION_RE = re.compile(r"^\s*(?:figure|fig\.)\s+([A-Za-z0-9.-]+)\b", re.I)
_TABLE_CAPTION_RE = re.compile(r"^\s*table\s+([A-Za-z0-9.-]+)\b", re.I)
_REFERENCE_ENTRY_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)[.)])\s+\S")
_REFERENCE_TITLES = frozenset({"references", "bibliography", "works cited"})


@dataclass(frozen=True)
class LocalStructureDocumentResult:
    paper_id: str
    status: str
    structure_path: Path
    outline_path: Path
    evidence_path: Path
    page_count: int
    section_count: int
    table_count: int
    figure_count: int
    reference_count: int
    locator_count: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "status": self.status,
            "structure_path": str(self.structure_path),
            "outline_path": str(self.outline_path),
            "evidence_path": str(self.evidence_path),
            "page_count": self.page_count,
            "section_count": self.section_count,
            "table_count": self.table_count,
            "figure_count": self.figure_count,
            "reference_count": self.reference_count,
            "locator_count": self.locator_count,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class LocalStructureBatchResult:
    status: str
    output_dir: Path
    summary_path: Path
    documents: tuple[LocalStructureDocumentResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_dir": str(self.output_dir),
            "summary_path": str(self.summary_path),
            "documents": [document.to_dict() for document in self.documents],
        }


@dataclass(frozen=True)
class _PreparedDocument:
    record: RouteSelectionFixtureEvidence
    paper_id: str
    structure_path: Path
    outline_path: Path
    evidence_path: Path
    structure_bytes: bytes
    outline_bytes: bytes
    evidence_bytes: bytes
    counts: dict[str, int]
    warnings: tuple[str, ...]


def build_local_markdown_structure(
    markdown: str,
    *,
    expected_page_count: int,
) -> tuple[dict[str, Any], str, dict[str, int], list[str]]:
    """Build conservative page and heading structure without a provider call."""

    if expected_page_count < 0:
        raise MillefeuilleContractError("expected_page_count must be non-negative")
    if not isinstance(markdown, str):
        raise MillefeuilleContractError("selected markdown must be a string")

    lines = markdown.splitlines()
    pages: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    warnings: list[str] = []
    section_stack: list[tuple[int, str]] = []
    current_page = 1
    current_page_record: dict[str, Any] | None = None
    reference_heading_level: int | None = None
    in_fence = False

    def ensure_page(line_number: int, page_number: int) -> dict[str, Any]:
        nonlocal current_page_record
        if (
            current_page_record is not None
            and current_page_record["page"] == page_number
        ):
            return current_page_record
        if current_page_record is not None:
            current_page_record["line_end"] = max(
                current_page_record["line_start"], line_number - 1
            )
        current_page_record = {
            "page": page_number,
            "locator": f"p.{page_number}",
            "line_start": line_number,
            "line_end": line_number,
            "section_refs": [],
        }
        pages.append(current_page_record)
        return current_page_record

    for index, line in enumerate(lines):
        line_number = index + 1
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            ensure_page(line_number, current_page)["line_end"] = line_number
            continue
        if in_fence:
            ensure_page(line_number, current_page)["line_end"] = line_number
            continue

        page_match = _PAGE_HEADING_RE.fullmatch(stripped)
        if page_match:
            page_number = int(page_match.group(1))
            if pages and page_number <= pages[-1]["page"]:
                warnings.append(
                    f"non-monotonic page marker p.{page_number} at line {line_number}"
                )
            current_page = page_number
            ensure_page(line_number, current_page)
            section_stack.clear()
            reference_heading_level = None
            continue

        page = ensure_page(line_number, current_page)
        page["line_end"] = line_number
        heading_match = _HEADING_RE.fullmatch(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()[:500]
            normalized_title = re.sub(r"\s+", " ", title).casefold()
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            parent_id = section_stack[-1][1] if section_stack else None
            section_id = f"s{len(sections) + 1}"
            locator = f"p.{current_page}#{section_id}"
            section = {
                "id": section_id,
                "title": title,
                "level": level,
                "page": current_page,
                "locator": locator,
                "line": line_number,
            }
            if parent_id is not None:
                section["parent_id"] = parent_id
            sections.append(section)
            page["section_refs"].append(section_id)
            section_stack.append((level, section_id))
            if normalized_title in _REFERENCE_TITLES:
                reference_heading_level = level
            elif (
                reference_heading_level is not None
                and level <= reference_heading_level
            ):
                reference_heading_level = None

        if index + 1 < len(lines) and "|" in line:
            next_line = lines[index + 1]
            if _TABLE_SEPARATOR_RE.fullmatch(next_line):
                tables.append(
                    {
                        "id": f"t{len(tables) + 1}",
                        "page": current_page,
                        "locator": f"p.{current_page}#t{len(tables) + 1}",
                        "line": line_number,
                        "kind": "markdown-table",
                    }
                )

        table_caption = _TABLE_CAPTION_RE.match(stripped)
        if table_caption and not heading_match:
            tables.append(
                {
                    "id": f"t{len(tables) + 1}",
                    "label": table_caption.group(1),
                    "page": current_page,
                    "locator": f"p.{current_page}#t{len(tables) + 1}",
                    "line": line_number,
                    "kind": "caption",
                }
            )

        image_match = _IMAGE_RE.search(line)
        figure_caption = _FIGURE_CAPTION_RE.match(stripped)
        if image_match or (figure_caption and not heading_match):
            figure: dict[str, Any] = {
                "id": f"f{len(figures) + 1}",
                "page": current_page,
                "locator": f"p.{current_page}#f{len(figures) + 1}",
                "line": line_number,
                "kind": "markdown-image" if image_match else "caption",
            }
            if image_match and image_match.group(1).strip():
                figure["label"] = image_match.group(1).strip()[:500]
            elif figure_caption:
                figure["label"] = figure_caption.group(1)
            figures.append(figure)

        if reference_heading_level is not None:
            reference_match = _REFERENCE_ENTRY_RE.match(line)
            if reference_match:
                label = reference_match.group(1) or reference_match.group(2)
                references.append(
                    {
                        "id": f"r{len(references) + 1}",
                        "label": label,
                        "page": current_page,
                        "locator": f"p.{current_page}#r{len(references) + 1}",
                        "line": line_number,
                    }
                )

    if current_page_record is not None:
        current_page_record["line_end"] = max(
            current_page_record["line_start"], len(lines)
        )

    detected_pages = [page["page"] for page in pages]
    if expected_page_count and len(set(detected_pages)) != expected_page_count:
        warnings.append(
            "page marker coverage mismatch: "
            f"expected {expected_page_count}, detected {len(set(detected_pages))}"
        )
    if detected_pages and expected_page_count:
        expected_pages = set(range(1, expected_page_count + 1))
        missing_pages = sorted(expected_pages.difference(detected_pages))
        if missing_pages:
            preview = ", ".join(str(page) for page in missing_pages[:10])
            suffix = "…" if len(missing_pages) > 10 else ""
            warnings.append(f"missing page markers: {preview}{suffix}")
    if not sections:
        warnings.append("no non-page Markdown headings detected")

    locator_count = (
        len(pages) + len(sections) + len(tables) + len(figures) + len(references)
    )
    structure = {
        "schema_version": LOCAL_STRUCTURE_SCHEMA_VERSION,
        "backend": LOCAL_STRUCTURE_BACKEND,
        "pages": pages,
        "sections": sections,
        "tables": tables,
        "figures": figures,
        "references": references,
        "coverage": {
            "expected_pages": expected_page_count,
            "detected_pages": len(set(detected_pages)),
            "locators": locator_count,
            "source": "selected-markdown",
        },
    }
    outline_lines = ["# Provider-free structure outline", ""]
    if sections:
        for section in sections:
            indent = "  " * max(0, section["level"] - 1)
            outline_lines.append(
                f"{indent}- {section['title']} ({section['locator']})"
            )
    else:
        outline_lines.append("- No section headings detected.")
    outline_lines.extend(
        [
            "",
            f"Pages: {len(set(detected_pages))}",
            f"Sections: {len(sections)}",
            f"Tables: {len(tables)}",
            f"Figures: {len(figures)}",
            f"References: {len(references)}",
        ]
    )
    if warnings:
        outline_lines.extend(["", "## Warnings", ""])
        outline_lines.extend(f"- {warning}" for warning in warnings)
    outline = "\n".join(outline_lines) + "\n"
    counts = {
        "pages": len(set(detected_pages)),
        "sections": len(sections),
        "tables": len(tables),
        "figures": len(figures),
        "references": len(references),
        "locators": locator_count,
    }
    return structure, outline, counts, warnings


def prepare_local_structures_from_route_evidence(
    *,
    route_evidence_paths: list[str | Path],
    output_dir: str | Path,
) -> LocalStructureBatchResult:
    """Prepare structure fixtures locally, preflighting all outputs before writes."""

    if not route_evidence_paths:
        raise MillefeuilleContractError("at least one route evidence path is required")
    output_root = Path(output_dir)
    records: list[RouteSelectionFixtureEvidence] = []
    for path in route_evidence_paths:
        records.extend(load_route_selection_evidence_batch(path))
    _reject_duplicate_route_records(records)

    prepared = [
        _prepare_document(record, output_root)
        for record in sorted(
            records,
            key=lambda candidate: (candidate.item_key, candidate.attachment_key),
        )
    ]
    summary_path = output_root / "summary.json"
    summary_payload = _summary_payload(output_root, prepared)
    summary_bytes = _canonical_json_bytes(summary_payload)

    statuses: list[str] = []
    document_statuses: dict[str, tuple[str, str, str]] = {}
    for document in prepared:
        output_statuses = (
            _preflight_output(document.structure_path, document.structure_bytes),
            _preflight_output(document.outline_path, document.outline_bytes),
            _preflight_output(document.evidence_path, document.evidence_bytes),
        )
        document_statuses[document.paper_id] = output_statuses
        statuses.extend(output_statuses)
    summary_status = _preflight_output(summary_path, summary_bytes)
    statuses.append(summary_status)

    for document in prepared:
        _write_new_output(document.structure_path, document.structure_bytes)
        _write_new_output(document.outline_path, document.outline_bytes)
        _write_new_output(document.evidence_path, document.evidence_bytes)
    _write_new_output(summary_path, summary_bytes)

    document_results = tuple(
        LocalStructureDocumentResult(
            paper_id=document.paper_id,
            status=(
                "existing"
                if all(
                    status == "existing"
                    for status in document_statuses[document.paper_id]
                )
                else "created"
            ),
            structure_path=document.structure_path,
            outline_path=document.outline_path,
            evidence_path=document.evidence_path,
            page_count=document.counts["pages"],
            section_count=document.counts["sections"],
            table_count=document.counts["tables"],
            figure_count=document.counts["figures"],
            reference_count=document.counts["references"],
            locator_count=document.counts["locators"],
            warnings=document.warnings,
        )
        for document in prepared
    )
    batch_status = (
        "existing" if all(status == "existing" for status in statuses) else "created"
    )
    return LocalStructureBatchResult(
        status=batch_status,
        output_dir=output_root,
        summary_path=summary_path,
        documents=document_results,
    )


def _prepare_document(
    record: RouteSelectionFixtureEvidence,
    output_root: Path,
) -> _PreparedDocument:
    markdown = read_text_no_follow(
        record.markdown_path,
        "selected markdown for local structure preparation",
    )
    structure, outline, counts, warnings = build_local_markdown_structure(
        markdown,
        expected_page_count=record.page_count,
    )
    paper_id = record.paper_id or paper_id_for_zotero_item_key(record.item_key)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", paper_id):
        raise MillefeuilleContractError("paper_id is not safe for local output")
    structure_path = output_root / f"{paper_id}.structure.json"
    outline_path = output_root / f"{paper_id}.outline.md"
    evidence_path = output_root / f"{paper_id}.evidence.json"
    evidence = {
        "schema_version": STRUCTURE_EVIDENCE_SCHEMA_VERSION,
        "source_type": record.source_type,
        "item_key": record.item_key,
        "attachment_key": record.attachment_key,
        "canonical_filename": record.canonical_filename,
        "structure_path": structure_path.name,
        "outline_path": outline_path.name,
        "expected_sha256": record.expected_sha256,
        "page_count": record.page_count,
        "selected_route": record.selected_route.value,
        "structure_backend": LOCAL_STRUCTURE_BACKEND,
        "coverage_source": "selected-markdown",
        "sections": counts["sections"],
        "tables": counts["tables"],
        "figures": counts["figures"],
        "references": counts["references"],
        "locators": counts["locators"],
        "warnings": warnings,
        "paper_id": paper_id,
    }
    if record.zotero_version is not None:
        evidence["zotero_version"] = record.zotero_version
    return _PreparedDocument(
        record=record,
        paper_id=paper_id,
        structure_path=structure_path,
        outline_path=outline_path,
        evidence_path=evidence_path,
        structure_bytes=_canonical_json_bytes(structure),
        outline_bytes=outline.encode("utf-8"),
        evidence_bytes=_canonical_json_bytes(evidence),
        counts=counts,
        warnings=tuple(warnings),
    )


def _summary_payload(
    output_root: Path,
    prepared: list[_PreparedDocument],
) -> dict[str, Any]:
    totals = {
        key: sum(document.counts[key] for document in prepared)
        for key in ("pages", "sections", "tables", "figures", "references", "locators")
    }
    return {
        "schema_version": LOCAL_STRUCTURE_SUMMARY_SCHEMA_VERSION,
        "backend": LOCAL_STRUCTURE_BACKEND,
        "output_dir": str(output_root),
        "documents": [
            {
                "paper_id": document.paper_id,
                "structure_path": document.structure_path.name,
                "outline_path": document.outline_path.name,
                "evidence_path": document.evidence_path.name,
                **document.counts,
                "warnings": list(document.warnings),
            }
            for document in prepared
        ],
        "totals": {"documents": len(prepared), **totals},
        "provider_calls": 0,
        "source_pack_writes": 0,
        "zotero_writes": 0,
    }


def _reject_duplicate_route_records(
    records: list[RouteSelectionFixtureEvidence],
) -> None:
    seen: set[tuple[str, str]] = set()
    seen_paper_ids: set[str] = set()
    duplicates: list[str] = []
    duplicate_paper_ids: list[str] = []
    for record in records:
        key = (record.item_key, record.attachment_key)
        if key in seen:
            duplicates.append(":".join(key))
        seen.add(key)
        paper_id = record.paper_id or paper_id_for_zotero_item_key(record.item_key)
        if paper_id in seen_paper_ids:
            duplicate_paper_ids.append(paper_id)
        seen_paper_ids.add(paper_id)
    if duplicates:
        raise MillefeuilleContractError(
            "duplicate route evidence for local structure preparation: "
            + ", ".join(sorted(set(duplicates)))
        )
    if duplicate_paper_ids:
        raise MillefeuilleContractError(
            "duplicate paper_id for local structure preparation: "
            + ", ".join(sorted(set(duplicate_paper_ids)))
        )


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _preflight_output(path: Path, expected: bytes) -> str:
    try:
        target_stat = path.lstat()
    except FileNotFoundError:
        return "created"
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not inspect local structure output {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(target_stat.st_mode):
        raise MillefeuilleContractError(f"local structure output is not a file: {path}")
    existing = read_bytes_no_follow(path, "local structure output")
    if existing != expected:
        raise MillefeuilleContractError(f"local structure output drift: {path}")
    return "existing"


def _write_new_output(path: Path, expected: bytes) -> None:
    if _preflight_output(path, expected) == "existing":
        return
    write_new_text_no_follow(
        path,
        expected.decode("utf-8"),
        "local structure output",
    )
