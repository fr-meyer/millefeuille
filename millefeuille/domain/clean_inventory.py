"Explicit sealed-registry historical inventory, never semantic new-root ancestry."

import re

from millefeuille.domain.decisions_classification import (
    canonical,
    load,
    read_bytes,
    require,
    sha,
)
from millefeuille.domain.taxonomy import validate_taxonomy_registry


def build(paths, root_id, scope):
    require(set(scope) == {"library_type", "library_id", "item_keys"})
    require(scope["library_type"] in {"user", "group"} and scope["item_keys"])
    require(re.fullmatch(r"clean-[0-9a-f]{32}", root_id))
    require(re.fullmatch(r"[1-9][0-9]*", scope["library_id"]))
    require(isinstance(scope["item_keys"], list))
    require(len(set(scope["item_keys"])) == len(scope["item_keys"]))
    require(all(re.fullmatch(r"[A-Z0-9]{8}", key) for key in scope["item_keys"]))
    sources = []
    labels = set()
    for path in paths:
        raw = read_bytes(path)
        registry = validate_taxonomy_registry(load(path))
        sources.append(
            {
                "taxonomy_version": registry["taxonomy_version"],
                "registry_id": registry["registry_id"],
                "content_identity": registry["content_identity"],
                "source_sha256": sha(raw),
                "semantic_sha256": sha(canonical(registry)),
            }
        )
        labels.update(e["label"] for e in registry["entries"])
    require(sources and len({s["content_identity"] for s in sources}) == len(sources))
    return {
        "schema_version": "millefeuille-historical-inventory/v1",
        "root_id": root_id,
        "scope": scope,
        "sources": sorted(sources, key=lambda s: s["content_identity"]),
        "labels": sorted(labels),
        "completeness": "owner-must-attest-complete-for-explicit-scope",
    }


def validate(inventory, paths, root_id, scope):
    require(inventory == build(paths, root_id, scope))
    return set(inventory["labels"])


def plan(snapshot, final, labels):
    tags = snapshot["parent"]["data"]["tags"]
    recognized = [t for t in tags if t["tag"] in labels]
    result = [t for t in tags if t["tag"] not in labels]
    result.append({"tag": final["label"]})
    require(sum(t["tag"] in labels for t in result) == 1)
    return {
        "recognized_old_tags": recognized,
        "new_label": final["label"],
        "tags": result,
    }
