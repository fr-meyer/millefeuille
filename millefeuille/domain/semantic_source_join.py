"""Canonical native-v0.2 byte custody, not scientific or reviewer authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from millefeuille.domain.evidence_answer import canonical, seal
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.multi_source_summary import build_multi_source_summary_package
from millefeuille.domain.multi_source_upstream import _PAGE_MARKER
from millefeuille.domain.reviewed_summary_handoff import retrieve_reviewed_summary_refs
from millefeuille.domain.secure_io import RootArtifactReader
from millefeuille.domain.semantic_acceptance import (
    budgeted_read,
    digest,
    safe_ref,
    scoped_reads,
)
from millefeuille.domain.semantic_materials import build_material_binding


@dataclass(frozen=True)
class PageJoinV1:
    local_page: int
    global_page: int
    byte_start: int
    byte_end: int
    text_sha256: str


@dataclass(frozen=True)
class SectionSpanMapV1:
    canonical_section_id: str
    heading: str
    heading_level: int
    original_parent_id: str | None
    original_parent_kind: str
    wrapper_id: str | None
    wrapper_heading_sha256: str | None
    span_parent_id: str | None
    byte_start: int
    byte_end: int
    page_ranges: tuple[PageJoinV1, ...]
    attachment_key: str
    wrapper_attachment_key: str | None
    wrapper_global_page: int | None
    wrapper_selected_line: int | None
    original_scientific_parent_id: str | None
    semantics_version: str = "hierarchical-byte-interval/v1"


@dataclass(frozen=True)
class AttachmentJoinV1:
    attachment_key: str
    source_ref: str
    source_sha256: str
    extraction_ref: str
    extraction_sha256: str
    pages: tuple[PageJoinV1, ...]
    sections: tuple[SectionSpanMapV1, ...]


@dataclass(frozen=True)
class CanonicalSourceJoinV1:
    paper_id: str
    run_id: str
    source_hash: str
    material_binding_sha256: str
    reviewed_manifest_sha256: str
    attachments: tuple[AttachmentJoinV1, ...]

    def to_dict(self):
        return json.loads(canonical(asdict(self)))


class _BoundedReader(RootArtifactReader):
    def read_bytes(self, path, label, *, max_bytes=None):
        return super().read_bytes(
            path, label, max_bytes=16777216 if max_bytes is None else max_bytes
        )


def _require(ok, message):
    if not ok:
        raise Error(message)


def _pages(raw, count):
    """Match the upstream splitter while retaining exact UTF-8 trim offsets."""
    text = raw.decode("utf-8")
    markers = list(_PAGE_MARKER.finditer(text))
    _require(
        len(markers) == count and not text[: markers[0].start()].strip(),
        "native page custody drift",
    )
    result = []
    for i, marker in enumerate(markers):
        _require(int(marker.group(1)) == i + 1, "native page sequence drift")
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        fragment = text[marker.end() : end]
        first = marker.end() + len(fragment) - len(fragment.lstrip())
        last = first + len(fragment.strip())
        result.append(
            (len(text[:first].encode()), len(text[:last].encode()), fragment.strip())
        )
    return result


@scoped_reads
def _derive_canonical_source_join(*, resolved, reviewed_package):
    binding = build_material_binding(resolved)
    pack = Path(resolved.source_pack_dir)
    # Same cumulative scope reaches the whole-pack verifier, including PDFs.
    with _BoundedReader(pack) as reader:
        checked = build_multi_source_summary_package(
            route_evidence_path=pack / "selected/route.json",
            structure_evidence_path=pack / "structure/structure.json",
            output_dir=resolved.run_dir,
            artifact_reader=reader,
            validation_only=True,
        )
        _require(
            checked["paper_id"] == resolved.paper_id
            and checked["source_hash"] == resolved.source_hash,
            "canonical resolved source identity drift",
        )
        manifest = reader.load_json_object(pack / "manifest.json", "canonical manifest")
        _require(
            manifest == resolved.source_pack_manifest,
            "resolved manifest snapshot drift",
        )
        native = reader.load_json_object(
            pack / "extractions/native/evidence.json", "native evidence"
        )
        route = reader.load_json_object(pack / "selected/route.json", "route")
        structure = reader.load_json_object(
            pack / "structure/structure.json", "structure"
        )["structure"]
        selected = reader.read_bytes(pack / "selected/fulltext.md", "selected").decode(
            "utf-8"
        )
        offsets = [0]
        for line in selected.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        markers = list(_PAGE_MARKER.finditer(selected))
        title = manifest["identity"].get("item_title") or resolved.paper_id
        _require(
            selected[: markers[0].start()]
            == f"# {title} — native two-source text\n\n\n",
            "selected preamble drift",
        )
        attachments = []
        all_sections = structure["sections"]
        for row in sorted(native["sources"], key=lambda x: x["attachment_key"]):
            key = row["attachment_key"]
            raw = reader.read_bytes(
                pack / safe_ref(row["native_markdown_ref"]), "native source"
            )
            _require(
                digest(raw) == "sha256:" + row["native_markdown_sha256"],
                "native extraction drift",
            )
            slices = _pages(raw, row["page_count"])
            pages, mappings, wrapper_ids = [], [], {}
            wrapper_records = {}
            mapped = [x for x in route["page_map"] if x["attachment_key"] == key]
            for local, (mapping, (start, end, body)) in enumerate(
                zip(mapped, slices, strict=True), 1
            ):
                global_page = int(mapping["global_locator"][2:])
                marker = markers[global_page - 1]
                stop = (
                    markers[global_page].start()
                    if global_page < len(markers)
                    else len(selected)
                )
                fragment = selected[marker.end() : stop]
                first = marker.end() + len(fragment) - len(fragment.lstrip())
                if local == 1:
                    wrapper = re.match(
                        rf"# Source: [^\r\n#]+ \({re.escape(key)}\)\n\n",
                        selected[first:],
                    )
                    _require(wrapper is not None, "generated wrapper custody missing")
                    wrapper_section = [
                        s
                        for s in all_sections
                        if offsets[s["line"] - 1] == first
                        and s["page"] == global_page
                        and s["level"] == 1
                    ]
                    _require(
                        len(wrapper_section) == 1, "generated wrapper identity drift"
                    )
                    wrapper_ids[wrapper_section[0]["id"]] = digest(
                        wrapper.group().encode()
                    )
                    wrapper_records[wrapper_section[0]["id"]] = wrapper_section[0]
                    first += wrapper.end()
                _require(
                    selected[first : first + len(body)] == body,
                    "selected/native offset custody drift",
                )
                page = PageJoinV1(
                    local, global_page, start, end, digest(raw[start:end])
                )
                pages.append(page)
                mappings.append((page, first, first + len(body), start))
            scientific = []
            for section in all_sections:
                if section["id"] in wrapper_ids:
                    continue
                position = offsets[section["line"] - 1]
                matches = [
                    (page, first, start)
                    for page, first, end, start in mappings
                    if first <= position < end
                ]
                if not matches:
                    continue
                _require(len(matches) == 1, "ambiguous section custody")
                page, first, start = matches[0]
                local_offset = start + len(selected[first:position].encode())
                scientific.append((section, local_offset, page.local_page))
            stack, sections = [], []
            for i, (s, start, local_page) in enumerate(scientific):
                while stack and stack[-1]["level"] >= s["level"]:
                    stack.pop()
                parent = stack[-1]["id"] if stack else None
                original = s.get("parent_id")
                wrapper_hash = wrapper_ids.get(original)
                wrapper_record = wrapper_records.get(original)
                kind = (
                    "generated_source_wrapper"
                    if wrapper_hash
                    else "scientific"
                    if original is not None
                    else "absent"
                )
                if wrapper_hash:
                    _require(
                        local_page == 1 and parent is None,
                        "wrapper scientific-parent projection drift",
                    )
                elif original is not None:
                    _require(original == parent, "scientific parent drift")
                elif parent is not None:
                    _require(local_page > 1, "missing first-page scientific parent")
                end = next(
                    (
                        offset
                        for later, offset, _ in scientific[i + 1 :]
                        if later["level"] <= s["level"]
                    ),
                    len(raw),
                )
                intersections = tuple(
                    PageJoinV1(
                        pg.local_page,
                        pg.global_page,
                        max(start, pg.byte_start),
                        min(end, pg.byte_end),
                        digest(raw[max(start, pg.byte_start) : min(end, pg.byte_end)]),
                    )
                    for pg in pages
                    if max(start, pg.byte_start) < min(end, pg.byte_end)
                )
                _require(bool(intersections), "unrepresentable section interval")
                sections.append(
                    SectionSpanMapV1(
                        s["id"],
                        s["title"],
                        s["level"],
                        original,
                        kind,
                        original if wrapper_hash else None,
                        wrapper_hash,
                        parent,
                        start,
                        end,
                        intersections,
                        attachment_key=key,
                        wrapper_attachment_key=key if wrapper_hash else None,
                        wrapper_global_page=wrapper_record["page"]
                        if wrapper_record
                        else None,
                        wrapper_selected_line=wrapper_record["line"]
                        if wrapper_record
                        else None,
                        original_scientific_parent_id=None
                        if wrapper_hash
                        else original,
                    )
                )
                stack.append(s)
            _require(bool(sections), "scientific source sections missing")
            attachments.append(
                AttachmentJoinV1(
                    key,
                    row["source_ref"],
                    "sha256:" + row["source_sha256"],
                    row["native_markdown_ref"],
                    digest(raw),
                    tuple(pages),
                    tuple(sections),
                )
            )
        reader.revalidate_snapshot()
    if reviewed_package is None:
        return CanonicalSourceJoinV1(
            resolved.paper_id,
            resolved.run_id,
            resolved.source_hash,
            digest(canonical(binding)),
            "UNREVIEWED",
            tuple(attachments),
        )
    refs = retrieve_reviewed_summary_refs(
        package_root=reviewed_package["package_root"],
        expected_paper_id=resolved.paper_id,
        expected_run_id=resolved.run_id,
        expected_manifest_sha256=reviewed_package["manifest_sha256"],
    )
    expected = {
        (a.attachment_key, s.canonical_section_id): (a, s)
        for a in attachments
        for s in a.sections
    }
    _require(
        len(refs["matches"]) == len(expected), "reviewed section inventory incomplete"
    )
    for unit in refs["matches"]:
        pair = expected.get((unit["attachment_key"], unit["section_id"]))
        _require(pair is not None, "foreign reviewed section")
        a, s = pair
        _require(
            unit["source_sha256"] == a.source_sha256
            and unit["heading"] == s.heading
            and unit["local_pages"] == [pg.local_page for pg in s.page_ranges],
            "reviewed canonical section/page drift",
        )
    return CanonicalSourceJoinV1(
        resolved.paper_id,
        resolved.run_id,
        resolved.source_hash,
        digest(canonical(binding)),
        refs["manifest_sha256"],
        tuple(attachments),
    )


@scoped_reads
def build_canonical_source_join(*, resolved, reviewed_package):
    _require(type(reviewed_package) is dict, "strict reviewed package required")
    return _derive_canonical_source_join(
        resolved=resolved, reviewed_package=reviewed_package
    )


@scoped_reads
def validate_source_review(*, resolved, source_review):
    for field in ("paper_id", "run_id", "source_hash"):
        _require(
            source_review.get(field) == getattr(resolved, field),
            "scientific review resolved identity missing",
        )
    binding = build_material_binding(resolved)
    _require(
        source_review.get("material_binding") == binding,
        "scientific review material join missing",
    )
    join = build_canonical_source_join(
        resolved=resolved, reviewed_package=source_review["reviewed_package"]
    )
    _require(
        source_review.get("canonical_join") == join.to_dict(),
        "canonical source join drift",
    )
    corpus = source_review["corpus"]
    _require(
        corpus["paper_id"] == join.paper_id
        and corpus["run_id"] == join.run_id
        and corpus["manifest_sha256"] == seal(corpus, "manifest_sha256")
        and source_review["corpus_sha256"] == corpus["manifest_sha256"],
        "joined corpus identity drift",
    )
    expected_attachments = [
        {
            "key": a.attachment_key,
            "source_sha256": a.source_sha256,
            "extraction_sha256": a.extraction_sha256,
            "pages": [pg.local_page for pg in a.pages],
        }
        for a in join.attachments
    ]
    _require(
        corpus["attachments"] == expected_attachments, "corpus attachment custody drift"
    )
    _require(
        type(corpus["spans"]) is list and 1 <= len(corpus["spans"]) <= 256,
        "bounded scientific spans required",
    )
    excerpts, seen, raw_cache = {}, set(), {}
    for span in corpus["spans"]:
        sid = span["span_id"]
        _require(sid not in seen, "duplicate scientific span")
        seen.add(sid)
        a = next(
            (a for a in join.attachments if a.attachment_key == span["attachment_key"]),
            None,
        )
        _require(
            a is not None
            and span["source_ref"] == a.extraction_ref
            and span["source_sha256"] == a.source_sha256,
            "scientific source/extraction join drift",
        )
        s = next(
            (s for s in a.sections if s.canonical_section_id == span["section_id"]),
            None,
        )
        _require(
            s is not None and type(span["page"]) is int,
            "foreign scientific section/page",
        )
        start, end = span["byte_start"], span["byte_end"]
        _require(
            type(start) is int
            and type(end) is int
            and start < end
            and any(
                pg.local_page == span["page"]
                and pg.byte_start <= start < end <= pg.byte_end
                for pg in s.page_ranges
            ),
            "span outside scientific page/section interval",
        )
        if a.attachment_key not in raw_cache:
            raw_cache[a.attachment_key] = budgeted_read(
                Path(resolved.source_pack_dir) / a.extraction_ref,
                "scientific span extraction",
                max_bytes=16777216,
            )
        raw = raw_cache[a.attachment_key]
        _require(digest(raw) == a.extraction_sha256, "scientific extraction hash drift")
        text = raw[start:end].decode("utf-8")
        _require(
            digest(text.encode()) == span["text_sha256"], "scientific raw span drift"
        )
        if span["kind"] == "table":
            coordinates = span["table_coordinates"]
            _require(
                type(coordinates) is dict
                and set(coordinates)
                == {"table_id", "row_label", "column_label", "unit_literal"},
                "explicit literal table coordinates required",
            )
            first = raw.rfind(b"\n", 0, start) + 1
            last = raw.find(b"\n", end)
            row = (
                raw[first : len(raw) if last < 0 else last]
                .decode()
                .strip()
                .strip("|")
                .split("|")
            )
            row = [v.strip() for v in row]
            separators = list(
                re.finditer(rb"^\|[ |:\-]+\|[ \t]*\r?$", raw[:first], re.MULTILINE)
            )
            _require(bool(separators), "native table separator missing")
            separator = separators[-1]
            previous = raw[: separator.start()].rstrip(b"\r\n")
            header = (
                previous[previous.rfind(b"\n") + 1 :]
                .decode()
                .strip()
                .strip("|")
                .split("|")
            )
            header = [v.strip() for v in header]
            _require(
                coordinates["table_id"] == "T" + str(len(separators))
                and coordinates["row_label"] == row[0]
                and coordinates["column_label"] in header,
                "native table coordinate drift",
            )
            column = header.index(coordinates["column_label"])
            _require(
                len(row) == len(header)
                and row[column] == text
                and type(coordinates["unit_literal"]) is str
                and bool(coordinates["unit_literal"])
                and re.search(
                    r"(?<!\w)" + re.escape(coordinates["unit_literal"]) + r"(?!\w)",
                    text,
                ),
                "native value/unit/condition drift",
            )
        else:
            _require(span["table_coordinates"] is None, "non-table coordinate drift")
        excerpts[sid] = {
            k: span[k]
            for k in [
                "span_id",
                "attachment_key",
                "page",
                "section_id",
                "source_sha256",
                "byte_start",
                "byte_end",
                "text_sha256",
            ]
        }
        excerpts[sid].update(extraction_sha256=a.extraction_sha256, text_utf8=text)
    _require(
        excerpts == source_review["source_spans"], "reviewed source span custody drift"
    )
    expected_pages = sorted(
        [a.attachment_key, pg.local_page] for a in join.attachments for pg in a.pages
    )
    expected_sections = sorted(
        [a.attachment_key, s.canonical_section_id]
        for a in join.attachments
        for s in a.sections
    )
    _require(
        sorted({(s["attachment_key"], s["page"]) for s in excerpts.values()})
        == [tuple(x) for x in expected_pages],
        "span page coverage incomplete",
    )
    _require(
        sorted({(s["attachment_key"], s["section_id"]) for s in excerpts.values()})
        == [tuple(x) for x in expected_sections],
        "span section coverage incomplete",
    )
    assessments = source_review["material_review"]
    _require(
        type(assessments) is list and len(assessments) == len(binding["files"]),
        "reviewed material inventory incomplete",
    )
    claims = source_review["reviewed_claims"]
    claim_ids = {c["claim_id"] for c in claims}
    _require(
        len(claim_ids) == len(claims) and bool(claims),
        "reviewed claims repeated or missing",
    )
    for claim in claims:
        locators = claim["source_locators"]
        _require(
            type(locators) is list and bool(locators), "reviewed claim locators missing"
        )
        texts = []
        for locator in locators:
            span = excerpts.get(locator.get("span_id"))
            _require(
                span is not None
                and locator
                == {
                    "attachment_key": span["attachment_key"],
                    "sha256": span["source_sha256"],
                    "page": span["page"],
                    "section": span["section_id"],
                    "span_id": span["span_id"],
                },
                "reviewed claim source locator drift",
            )
            texts.append(span["text_utf8"])
        _require(
            claim["claim_text"] in texts
            and claim["claim_text_sha256"] == digest(claim["claim_text"].encode())
            and claim["verdict"] == "supported"
            and type(claim["qualification"]) is str
            and bool(claim["qualification"].strip()),
            "reviewed claim lacks exact source support",
        )

    for assessment, material in zip(assessments, binding["files"], strict=True):
        _require(
            assessment["name"] == material["name"]
            and assessment["sha256"] == material["sha256"]
            and type(assessment["claim_ids"]) is list
            and bool(assessment["claim_ids"])
            and set(assessment["claim_ids"]) <= claim_ids
            and type(assessment["qualification"]) is str
            and bool(assessment["qualification"].strip()),
            "reviewed material/claim assessment drift",
        )
    completeness = corpus["completeness"]
    _require(
        completeness
        == {
            "authorized_scope_sha256": digest(canonical(join.to_dict())),
            "expected_spans": len(excerpts),
            "expected_source_bytes": sum(
                s["byte_end"] - s["byte_start"] for s in excerpts.values()
            ),
            "all_selected_attachments_inventory_complete": True,
        },
        "scientific traversal inventory drift",
    )
    return join, expected_pages, expected_sections
