"""Independently derive semantic binding from canonical resolved material refs.

No globbing, corpus discovery, reviewer authentication or external effects.
Only run and already resolved source-pack roots are supported. Referenced
files are read with no-follow checks, not trusted because JSON lists them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import (
    budgeted_read,
    digest,
    safe_ref,
    scoped_reads,
)

MATERIAL_STAGES = frozenset(
    {
        "extract-native",
        "extract-ocr",
        "route",
        "structure",
        "summarize",
        "card",
        "index",
    }
)
REQUIRED_ARTIFACTS = frozenset(
    {
        "hierarchical_summary",
        "paper_card_json",
        "paper_card_markdown",
        "retrieval_index_status",
    }
)


@scoped_reads
def build_material_binding(resolved):
    """Return the complete declared material snapshot or refuse semantic pass.

    Source/attachment count, identity and expected hashes come from the resolved
    manifest, never from the reviewer's inventory. Declared extraction/structure,
    every hierarchy text, both card forms and the local index status are bound.
    No claim is made about external backend contents or reviewer authenticity.
    """
    manifest = getattr(resolved, "source_pack_manifest", None)
    index = getattr(resolved, "artifact_index", None)
    artifacts = getattr(index, "artifacts", None)
    pack = getattr(resolved, "source_pack_dir", None)
    if (
        not isinstance(manifest, dict)
        or not isinstance(artifacts, dict)
        or pack is None
    ):
        raise MillefeuilleContractError("resolved material inventory unavailable")
    if not REQUIRED_ARTIFACTS.issubset(artifacts):
        raise MillefeuilleContractError("canonical semantic material artifact missing")
    roots = {"run": Path(resolved.run_dir), "source-pack": Path(pack)}
    if any(not p.is_absolute() or ".." in p.parts for p in roots.values()):
        raise MillefeuilleContractError("unsafe resolved material root")
    files, cache = [], {}
    total = 0

    def locate(base, ref):
        if (
            not isinstance(ref, str)
            or not ref
            or chr(92) in ref
            or ":" in ref
            or chr(0) in ref
        ):
            raise MillefeuilleContractError("unsafe material reference")
        if Path(ref).is_absolute():
            raise MillefeuilleContractError("absolute material reference forbidden")
        # Lexically confine historical ../ refs before any no-follow file read.
        candidate = Path(os.path.abspath(base / ref))
        for name, boundary in roots.items():
            try:
                relative = candidate.relative_to(boundary)
            except ValueError:
                continue
            safe_ref(relative.as_posix())
            return name, relative.as_posix(), candidate
        raise MillefeuilleContractError("material reference escapes resolved roots")

    def add(name, base, ref, expected=None):
        nonlocal total
        root_name, normalized, path = locate(base, ref)
        key = root_name, normalized
        if key not in cache:
            if len(cache) >= 256:
                raise MillefeuilleContractError("material file count exceeds bound")
            data = budgeted_read(
                path, "semantic canonical material", max_bytes=16 * 1024 * 1024
            )
            total += len(data)
            if total > 64 * 1024 * 1024:
                raise MillefeuilleContractError("material byte count exceeds bound")
            cache[key] = data
        data = cache[key]
        actual = digest(data)
        if expected is not None and actual != expected:
            raise MillefeuilleContractError("canonical semantic source hash drift")
        files.append(
            {"name": name, "root": root_name, "ref": normalized, "sha256": actual}
        )
        return data, path

    sources = manifest.get("sources")
    if sources is None and isinstance(manifest.get("source"), dict):
        sources = [manifest["source"]]
    if not isinstance(sources, list) or not sources or len(sources) > 64:
        raise MillefeuilleContractError("canonical semantic source inventory missing")
    source_refs, attachment_keys = set(), set()
    for source in sources:
        if not isinstance(source, dict):
            raise MillefeuilleContractError("invalid canonical semantic source")
        ref, expected = source.get("ref"), source.get("sha256")
        safe_ref(ref)
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or any(c not in "0123456789abcdef" for c in expected)
        ):
            raise MillefeuilleContractError("invalid canonical source hash")
        identity = source.get("identity", manifest.get("identity", {}))
        key = (
            identity.get("zotero_attachment_key")
            if isinstance(identity, dict)
            else None
        )
        if (
            not isinstance(key, str)
            or not key
            or key in attachment_keys
            or ref in source_refs
        ):
            raise MillefeuilleContractError(
                "canonical attachment identity missing or repeated"
            )
        attachment_keys.add(key)
        source_refs.add(ref)
        data, _ = add("source:" + key, roots["source-pack"], ref, "sha256:" + expected)
        if type(source.get("byte_size")) is not int or source["byte_size"] != len(data):
            raise MillefeuilleContractError("canonical source size drift")

    declarations = {}
    hierarchy_path = hierarchy_data = None
    for name, record in sorted(artifacts.items()):
        if not isinstance(record, dict):
            raise MillefeuilleContractError("invalid canonical artifact declaration")
        if record.get("stage") not in MATERIAL_STAGES:
            continue
        declarations[name] = record
        if record.get("format") == "directory":
            if name != "summary_texts":
                raise MillefeuilleContractError(
                    "unbounded material directory unsupported"
                )
            continue
        data, path = add("artifact:" + name, roots["run"], record.get("ref"))
        if name == "hierarchical_summary":
            hierarchy_data, hierarchy_path = data, path
    if not REQUIRED_ARTIFACTS.issubset(declarations) or hierarchy_path is None:
        raise MillefeuilleContractError("canonical material stage binding missing")
    try:
        hierarchy = json.loads(hierarchy_data)
    except (ValueError, TypeError) as exc:
        raise MillefeuilleContractError("invalid canonical semantic hierarchy") from exc
    entries = hierarchy.get("summaries") if isinstance(hierarchy, dict) else None
    if not isinstance(entries, list) or not entries or len(entries) > 256:
        raise MillefeuilleContractError("canonical summary text inventory missing")
    summary_ids = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("summary_id"), str)
            or entry["summary_id"] in summary_ids
        ):
            raise MillefeuilleContractError("invalid or duplicate canonical summary")
        summary_ids.add(entry["summary_id"])
        add(
            "summary-text:" + entry["summary_id"],
            hierarchy_path.parent,
            entry.get("text_ref"),
        )
    if manifest.get("schema_version") == "millefeuille-source-pack-manifest/v0.2":
        raw, _ = add(
            "native-source-evidence",
            roots["source-pack"],
            "extractions/native/evidence.json",
        )
        native = json.loads(raw)
        for row in native["sources"]:
            add(
                "extraction:" + row["attachment_key"],
                roots["source-pack"],
                row["native_markdown_ref"],
                "sha256:" + row["native_markdown_sha256"],
            )
    return {
        "schema": "millefeuille-resolved-semantic-materials/v1",
        "source_manifest_sha256": digest(json.dumps(manifest, sort_keys=True).encode()),
        "artifact_declarations_sha256": digest(
            json.dumps(declarations, sort_keys=True).encode()
        ),
        "files": sorted(files, key=lambda x: x["name"]),
    }


@scoped_reads
def require_material_binding(resolved, decision):
    expected = build_material_binding(resolved)
    if decision.get("material_binding") != expected:
        raise MillefeuilleContractError(
            "semantic resolved material binding missing or stale"
        )
    return expected
