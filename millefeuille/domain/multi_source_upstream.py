"""Provider-free, no-write planning for whole-pack native PDF text stages.

The caller supplies already extracted Markdown for every verified PDF member.
This module joins every page to its attachment and prepares exact source-pack
outputs. Publication still needs a separate approved source-pack write.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.local_structure import build_local_markdown_structure
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.source_packs import load_source_pack_manifest
from millefeuille.domain.source_scope import (
    MultiSourceScope,
    resolve_multi_source_pack,
)

NATIVE_MULTI_SCHEMA = "millefeuille-native-extraction-evidence/v0.2"
ROUTE_MULTI_SCHEMA = "millefeuille-route-selection-evidence/v0.2"
STRUCTURE_MULTI_SCHEMA = "millefeuille-structure-evidence/v0.2"
UPSTREAM_PREVIEW_SCHEMA = "millefeuille-multi-source-upstream-preview/v0.1"
MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
_PAGE_MARKER = re.compile(r"^## Page ([1-9][0-9]*)[ \t]*\r?$", re.MULTILINE)
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class MultiSourceNativeInput:
    attachment_key: str
    markdown_path: Path
    markdown_sha256: str
    page_count: int
    label: str
    tool: str = "local-native"

    def __post_init__(self) -> None:
        if not self.attachment_key or not self.attachment_key.isalnum():
            raise MillefeuilleContractError("attachment_key must be alphanumeric")
        if not isinstance(self.markdown_path, Path):
            object.__setattr__(self, "markdown_path", Path(self.markdown_path))
        if _HEX_64.fullmatch(self.markdown_sha256) is None:
            raise MillefeuilleContractError("markdown_sha256 must be lowercase SHA-256")
        if type(self.page_count) is not int or not 1 <= self.page_count <= 256:
            raise MillefeuilleContractError("page_count must be between 1 and 256")
        if (
            not self.label
            or self.label != self.label.strip()
            or any(c in self.label for c in "\r\n#")
            or len(self.label) > 100
        ):
            raise MillefeuilleContractError("label must be one safe line")
        if (
            not self.tool
            or self.tool != self.tool.strip()
            or any(c in self.tool for c in "\r\n")
            or len(self.tool) > 100
        ):
            raise MillefeuilleContractError("tool must be one safe line")


@dataclass(frozen=True)
class PlannedMultiSourceOutput:
    ref: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def byte_size(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class MultiSourceUpstreamPlan:
    pack: Path
    paper_id: str
    source_hash: str
    outputs: tuple[PlannedMultiSourceOutput, ...]
    source_count: int
    page_count: int
    warnings: tuple[str, ...]

    def preview(self) -> dict[str, Any]:
        """Return only identities, hashes and sizes; never extracted text."""
        return {
            "schema_version": UPSTREAM_PREVIEW_SCHEMA,
            "paper_id": self.paper_id,
            "source_hash": self.source_hash,
            "source_pack_dir": str(self.pack),
            "source_count": self.source_count,
            "page_count": self.page_count,
            "file_count": len(self.outputs),
            "total_bytes": sum(output.byte_size for output in self.outputs),
            "outputs": [
                {
                    "ref": output.ref,
                    "path": str(self.pack / output.ref),
                    "byte_size": output.byte_size,
                    "sha256": output.sha256,
                }
                for output in self.outputs
            ],
            "warnings": list(self.warnings),
            "provider_calls": 0,
            "source_pack_writes": 0,
        }

    def preview_sha256(self) -> str:
        return hashlib.sha256(_canonical(self.preview())).hexdigest()


def plan_multi_source_native_upstream(
    *,
    source_pack_root: str | Path,
    item_key: str,
    paper_id: str,
    scope: MultiSourceScope,
    inputs: tuple[MultiSourceNativeInput, ...],
) -> MultiSourceUpstreamPlan:
    """Verify the complete pack and text evidence, then prepare zero-write output."""
    if not isinstance(scope, MultiSourceScope):
        raise MillefeuilleContractError("source scope must be a typed whole-pack scope")
    if (
        not isinstance(inputs, tuple)
        or len(inputs) != len(scope.sources)
        or not all(isinstance(record, MultiSourceNativeInput) for record in inputs)
    ):
        raise MillefeuilleContractError("native inputs must cover every PDF member")
    members = {member.attachment_key: member for member in scope.sources}
    records = {record.attachment_key: record for record in inputs}
    if len(records) != len(inputs) or set(records) != set(members):
        raise MillefeuilleContractError(
            "native input member set differs from source scope"
        )
    pack, source_hash = resolve_multi_source_pack(
        source_pack_root=Path(source_pack_root),
        item_key=item_key,
        paper_id=paper_id,
        scope=scope,
    )
    manifest = load_source_pack_manifest(pack / "manifest.json")
    title = manifest["identity"].get("item_title") or paper_id
    if not isinstance(title, str) or "\n" in title or "\r" in title:
        raise MillefeuilleContractError("source-pack title must be one line")

    joined: list[str] = [f"# {title} — native two-source text\n"]
    page_map: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    source_outputs: list[PlannedMultiSourceOutput] = []
    global_page = 0
    for key in sorted(records):
        record = records[key]
        member = members[key]
        raw = read_bytes_no_follow(
            record.markdown_path,
            "multi-source native Markdown",
            max_bytes=MAX_MARKDOWN_BYTES,
        )
        if hashlib.sha256(raw).hexdigest() != record.markdown_sha256:
            raise MillefeuilleContractError("native Markdown hash drift")
        try:
            markdown = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MillefeuilleContractError("native Markdown must be UTF-8") from exc
        local_pages = _split_pages(markdown, expected_count=record.page_count)
        native_ref = f"extractions/native/sources/{Path(member.source_ref).stem}.md"
        source_outputs.append(PlannedMultiSourceOutput(native_ref, raw))
        empty_pages = 0
        for local_page, body in enumerate(local_pages, start=1):
            global_page += 1
            if not body:
                empty_pages += 1
            source_heading = (
                f"# Source: {record.label} ({key})\n\n" if local_page == 1 else ""
            )
            joined.append(f"\n## Page {global_page}\n\n{source_heading}{body}\n")
            page_map.append(
                {
                    "global_locator": f"p.{global_page}",
                    "attachment_locator": f"attachment:{key}/p.{local_page}",
                    "attachment_key": key,
                    "source_ref": member.source_ref,
                    "character_count": len(body),
                    "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                }
            )
        source_rows.append(
            {
                "attachment_key": key,
                "source_ref": member.source_ref,
                "source_sha256": member.sha256,
                "native_markdown_ref": native_ref,
                "native_markdown_sha256": record.markdown_sha256,
                "page_count": record.page_count,
                "empty_pages": empty_pages,
                "tool": record.tool,
            }
        )

    selected = "\n".join(joined)
    structure, outline, counts, warnings = build_local_markdown_structure(
        selected,
        expected_page_count=global_page,
    )
    if counts["pages"] != global_page:
        raise MillefeuilleContractError("combined page structure coverage drift")
    scope_payload = _scope_dict(scope)
    common = {
        "paper_id": paper_id,
        "item_key": item_key,
        "source_hash": source_hash,
        "source_scope": scope_payload,
    }
    native_evidence = {
        **common,
        "schema_version": NATIVE_MULTI_SCHEMA,
        "sources": source_rows,
        "page_count": global_page,
        "provider_calls": 0,
    }
    route_evidence = {
        **common,
        "schema_version": ROUTE_MULTI_SCHEMA,
        "selected_route": "native",
        "output_markdown_ref": "selected/fulltext.md",
        "output_markdown_sha256": hashlib.sha256(selected.encode("utf-8")).hexdigest(),
        "page_count": global_page,
        "page_map": page_map,
        "native_evidence_ref": "extractions/native/evidence.json",
        "provider_calls": 0,
    }
    structure_evidence = {
        **common,
        "schema_version": STRUCTURE_MULTI_SCHEMA,
        "selected_route": "native",
        "source_markdown_ref": "selected/fulltext.md",
        "route_evidence_ref": "selected/route.json",
        "page_count": global_page,
        "counts": counts,
        "warnings": warnings,
        "structure": structure,
        "outline_markdown_ref": "structure/outline.md",
        "provider_calls": 0,
    }
    outputs = tuple(
        sorted(
            (
                *source_outputs,
                PlannedMultiSourceOutput(
                    "extractions/native/evidence.json", _json(native_evidence)
                ),
                PlannedMultiSourceOutput(
                    "selected/fulltext.md", selected.encode("utf-8")
                ),
                PlannedMultiSourceOutput("selected/route.json", _json(route_evidence)),
                PlannedMultiSourceOutput(
                    "structure/structure.json", _json(structure_evidence)
                ),
                PlannedMultiSourceOutput(
                    "structure/outline.md", outline.encode("utf-8")
                ),
            ),
            key=lambda output: output.ref,
        )
    )
    return MultiSourceUpstreamPlan(
        pack=pack,
        paper_id=paper_id,
        source_hash=source_hash,
        outputs=outputs,
        source_count=len(inputs),
        page_count=global_page,
        warnings=tuple(warnings),
    )


def _split_pages(markdown: str, *, expected_count: int) -> list[str]:
    markers = list(_PAGE_MARKER.finditer(markdown))
    if len(markers) != expected_count or markdown[: markers[0].start()].strip():
        raise MillefeuilleContractError("native Markdown page-marker coverage drift")
    pages: list[str] = []
    for index, marker in enumerate(markers):
        if int(marker.group(1)) != index + 1:
            raise MillefeuilleContractError(
                "native Markdown page markers are not consecutive"
            )
        end = markers[index + 1].start() if index + 1 < len(markers) else len(markdown)
        pages.append(markdown[marker.end() : end].strip())
    return pages


def _scope_dict(scope: MultiSourceScope) -> dict[str, Any]:
    return {
        "schema_version": scope.schema_version,
        "source_hash": scope.source_hash,
        "sources": [
            {
                "attachment_key": member.attachment_key,
                "canonical_filename": member.canonical_filename,
                "sha256": member.sha256,
                "source_ref": member.source_ref,
                **(
                    {"zotero_version": member.zotero_version}
                    if member.zotero_version is not None
                    else {}
                ),
            }
            for member in scope.sources
        ],
    }


def _json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
