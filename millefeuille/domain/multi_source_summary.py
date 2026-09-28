"""Whole-pack, provider-free preparation of two-or-more-source summaries.

The route, structure, selected text, native extracts and verified PDF manifest
are rechecked as one unit. No model call or source-pack write occurs here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.local_structure import build_local_markdown_structure
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import (
    SUMMARY_MODEL_STAGES,
    build_summary_execution_plan,
)
from millefeuille.domain.multi_source_upstream import (
    NATIVE_MULTI_SCHEMA,
    ROUTE_MULTI_SCHEMA,
    STRUCTURE_MULTI_SCHEMA,
    _split_pages,
)
from millefeuille.domain.secure_io import RootArtifactReader, read_bytes_no_follow
from millefeuille.domain.source_scope import MultiSourceScope, verify_multi_source_pack

SUMMARY_MULTI_SCHEMA = "millefeuille-summary-preparation/v0.2"
_PAGE = re.compile(r"^## Page ([1-9][0-9]*)[ \t]*\r?$", re.MULTILINE)
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def build_multi_source_summary_package(
    *,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    output_dir: str | Path,
    profile: str,
    artifact_reader: RootArtifactReader | None = None,
) -> tuple[Path, bytes, dict[str, int], tuple[str, ...]]:
    """Return a deterministic package in memory after whole-pack verification."""

    reader = (
        artifact_reader.read_bytes
        if artifact_reader is not None
        else read_bytes_no_follow
    )
    route_path = Path(route_evidence_path)
    structure_path = Path(structure_evidence_path)
    pack = route_path.parent.parent
    if (
        route_path != pack / "selected/route.json"
        or structure_path != pack / "structure/structure.json"
        or pack.parent.name != "zotero"
    ):
        raise MillefeuilleContractError(
            "multi-source summary evidence paths are not canonical"
        )
    route = _object(reader(route_path, "multi-source summary route"), "route")
    structure = _object(
        reader(structure_path, "multi-source summary structure"), "structure"
    )
    native_path = pack / "extractions/native/evidence.json"
    native = _object(
        reader(native_path, "multi-source summary native evidence"), "native"
    )
    if (
        route.get("schema_version") != ROUTE_MULTI_SCHEMA
        or structure.get("schema_version") != STRUCTURE_MULTI_SCHEMA
        or native.get("schema_version") != NATIVE_MULTI_SCHEMA
    ):
        raise MillefeuilleContractError("multi-source summary evidence schema drift")
    paper_id = route.get("paper_id")
    item_key = route.get("item_key")
    page_count = route.get("page_count")
    if (
        not isinstance(paper_id, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]+", paper_id)
        or paper_id != pack.name
        or not isinstance(item_key, str)
        or not re.fullmatch(r"[A-Za-z0-9]+", item_key)
        or type(page_count) is not int
        or page_count < 1
    ):
        raise MillefeuilleContractError(
            "multi-source summary paper or page identity drift"
        )
    scope = MultiSourceScope.from_dict(route.get("source_scope"))
    verify_multi_source_pack(pack, item_key=item_key, paper_id=paper_id, scope=scope)
    for record in (route, structure, native):
        if (
            record.get("paper_id") != paper_id
            or record.get("item_key") != item_key
            or record.get("source_hash") != scope.source_hash
            or record.get("source_scope") != route["source_scope"]
            or record.get("page_count") != page_count
            or record.get("provider_calls") != 0
        ):
            raise MillefeuilleContractError(
                "multi-source summary whole-pack join drift"
            )
    if (
        route.get("selected_route") != "native"
        or structure.get("selected_route") != "native"
        or route.get("native_evidence_ref") != "extractions/native/evidence.json"
        or route.get("output_markdown_ref") != "selected/fulltext.md"
        or structure.get("route_evidence_ref") != "selected/route.json"
        or structure.get("source_markdown_ref") != "selected/fulltext.md"
        or structure.get("outline_markdown_ref") != "structure/outline.md"
    ):
        raise MillefeuilleContractError(
            "multi-source summary route or structure ref drift"
        )
    markdown_path = pack / "selected/fulltext.md"
    markdown = reader(markdown_path, "multi-source summary selected Markdown")
    markdown_sha = hashlib.sha256(markdown).hexdigest()
    if route.get("output_markdown_sha256") != markdown_sha:
        raise MillefeuilleContractError(
            "multi-source summary selected Markdown hash drift"
        )
    try:
        text = markdown.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MillefeuilleContractError(
            "multi-source summary text is not UTF-8"
        ) from exc
    if [int(number) for number in _PAGE.findall(text)] != list(
        range(1, page_count + 1)
    ):
        raise MillefeuilleContractError(
            "multi-source summary page-marker coverage drift"
        )

    members = {member.attachment_key: member for member in scope.sources}
    native_rows = native.get("sources")
    if (
        not isinstance(native_rows, list)
        or len(native_rows) != len(members)
        or any(
            not isinstance(row, dict) or not isinstance(row.get("attachment_key"), str)
            for row in native_rows
        )
        or {row["attachment_key"] for row in native_rows} != set(members)
    ):
        raise MillefeuilleContractError(
            "multi-source summary native member coverage drift"
        )
    local_page_counts: dict[str, int] = {}
    native_pages: dict[str, list[str]] = {}
    for row in native_rows:
        if not isinstance(row, dict):
            raise MillefeuilleContractError(
                "multi-source summary native member is invalid"
            )
        key = row["attachment_key"]
        member = members[key]
        ref = f"extractions/native/sources/{Path(member.source_ref).stem}.md"
        if (
            row.get("source_ref") != member.source_ref
            or row.get("source_sha256") != member.sha256
            or row.get("native_markdown_ref") != ref
            or type(row.get("page_count")) is not int
            or row["page_count"] < 1
            or row.get("empty_pages") != 0
        ):
            raise MillefeuilleContractError("multi-source summary native member drift")
        source_markdown = reader(pack / ref, "multi-source summary native Markdown")
        if hashlib.sha256(source_markdown).hexdigest() != row.get(
            "native_markdown_sha256"
        ):
            raise MillefeuilleContractError(
                "multi-source summary native Markdown hash drift"
            )
        try:
            native_text = source_markdown.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MillefeuilleContractError(
                "multi-source summary native Markdown is not UTF-8"
            ) from exc
        native_pages[key] = _split_pages(native_text, expected_count=row["page_count"])
        local_page_counts[key] = row["page_count"]
    if sum(local_page_counts.values()) != page_count:
        raise MillefeuilleContractError(
            "multi-source summary native page coverage drift"
        )
    page_map = route.get("page_map")
    if not isinstance(page_map, list) or len(page_map) != page_count:
        raise MillefeuilleContractError("multi-source summary page map is incomplete")
    markers = list(_PAGE.finditer(text))
    selected_pages = [
        text[
            marker.end() : markers[index + 1].start()
            if index + 1 < len(markers)
            else len(text)
        ].strip()
        for index, marker in enumerate(markers)
    ]
    expected_pages = [
        (key, body) for key in sorted(native_pages) for body in native_pages[key]
    ]
    if len(expected_pages) != page_count:
        raise MillefeuilleContractError(
            "multi-source summary native page coverage drift"
        )
    seen: dict[str, int] = dict.fromkeys(members, 0)
    for number, row in enumerate(page_map, 1):
        if not isinstance(row, dict) or set(row) != {
            "global_locator",
            "attachment_locator",
            "attachment_key",
            "source_ref",
            "character_count",
            "text_sha256",
        }:
            raise MillefeuilleContractError(
                "multi-source summary page map fields drift"
            )
        key = row["attachment_key"]
        if not isinstance(key, str) or key not in members:
            raise MillefeuilleContractError("multi-source summary page member drift")
        seen[key] += 1
        expected_key, body = expected_pages[number - 1]
        selected_page = selected_pages[number - 1]
        if seen[key] == 1:
            heading = re.match(
                rf"^# Source: [^\r\n#]+ \({re.escape(key)}\)\n\n",
                selected_page,
            )
            if heading is None:
                raise MillefeuilleContractError(
                    "multi-source summary selected source heading drift"
                )
            selected_page = selected_page[heading.end() :]
        if key != expected_key or selected_page != body:
            raise MillefeuilleContractError(
                "multi-source summary selected page content drift"
            )
        if (
            row["character_count"] != len(body)
            or row["text_sha256"] != hashlib.sha256(body.encode("utf-8")).hexdigest()
        ):
            raise MillefeuilleContractError(
                "multi-source summary page body digest drift"
            )
        if (
            row["global_locator"] != f"p.{number}"
            or row["attachment_locator"] != f"attachment:{key}/p.{seen[key]}"
            or row["source_ref"] != members[key].source_ref
            or type(row["character_count"]) is not int
            or row["character_count"] < 0
            or not isinstance(row["text_sha256"], str)
            or _HEX.fullmatch(row["text_sha256"]) is None
        ):
            raise MillefeuilleContractError(
                "multi-source summary page attribution drift"
            )
    if seen != local_page_counts:
        raise MillefeuilleContractError(
            "multi-source summary attachment page coverage drift"
        )

    inner = structure.get("structure")
    if not isinstance(inner, dict):
        raise MillefeuilleContractError("multi-source summary structure is invalid")
    expected_structure, expected_outline, counts, warnings = (
        build_local_markdown_structure(text, expected_page_count=page_count)
    )
    if (
        inner != expected_structure
        or structure.get("counts") != counts
        or structure.get("warnings") != warnings
        or reader(pack / "structure/outline.md", "multi-source summary outline")
        != expected_outline.encode("utf-8")
    ):
        raise MillefeuilleContractError("multi-source summary structure content drift")
    if [row.get("locator") for row in inner["pages"]] != [
        row["global_locator"] for row in page_map
    ]:
        raise MillefeuilleContractError("multi-source summary structure page map drift")

    # The existing dispatcher only needs unit ids and global locators. The
    # complete attachment mapping remains bound in the preparation identity.
    from millefeuille.domain.summary_preparation import _build_work_units

    work_units = _build_work_units(inner)
    page_ids = [unit["unit_id"] for unit in work_units["summarize_page"]]
    plans = {
        stage: build_summary_execution_plan(profile=profile, stage=stage)
        for stage in SUMMARY_MODEL_STAGES
    }
    blockers = tuple(
        sorted(
            {"summary outputs are not generated by this preparation command"}
            | {
                blocker
                for plan in plans.values()
                for blocker in plan["execution"]["blockers"]
            }
            | (
                {"structure page coverage incomplete"}
                if page_ids != [f"page-{n}" for n in range(1, page_count + 1)]
                else set()
            )
        )
    )
    structure_bytes = reader(structure_path, "multi-source summary structure")
    package = {
        "schema_version": SUMMARY_MULTI_SCHEMA,
        "status": "prepared-no-call",
        "paper_id": paper_id,
        "identity": {
            "source_type": "zotero",
            "item_key": item_key,
            "source_hash": scope.source_hash,
            "source_scope": route["source_scope"],
            "page_count": page_count,
            "page_map": page_map,
            "selected_route": "native",
        },
        "inputs": {
            "selected_markdown": {
                "path": str(markdown_path),
                "sha256": markdown_sha,
                "bytes": len(markdown),
            },
            "structure": {
                "path": str(structure_path),
                "sha256": hashlib.sha256(structure_bytes).hexdigest(),
                "bytes": len(structure_bytes),
                "backend": inner["backend"],
                "coverage_source": inner["coverage"]["source"],
            },
        },
        "profile": profile,
        "execution_plans": plans,
        "work_units": work_units,
        "execution": {
            "provider_call_permitted": False,
            "provider_call_performed": False,
            "summary_outputs_generated": 0,
            "ready_for_approved_live_execution": False,
            "blockers": list(blockers),
        },
        "writes": {"source_pack": False, "zotero": False},
    }
    package_path = Path(output_dir) / f"{paper_id}.summary-preparation.json"
    encoded = (json.dumps(package, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return (
        package_path,
        encoded,
        {stage: len(units) for stage, units in work_units.items()},
        blockers,
    )


def _object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise MillefeuilleContractError(
            f"multi-source summary {label} is not JSON"
        ) from exc
    if not isinstance(value, dict):
        raise MillefeuilleContractError(
            f"multi-source summary {label} must be an object"
        )
    return value
