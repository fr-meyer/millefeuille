"Standalone clean-root schema. Never accepted by legacy canonical validators."

import copy
import re
import uuid

from millefeuille.domain.decisions_classification import (
    canonical,
    load,
    require,
    safe_path,
    sha,
)
from millefeuille.domain.final_durability import publish, reserve
from millefeuille.domain.final_taxonomy import normalize

SCHEMA = "millefeuille-clean-taxonomy/v1"
BASIS = (
    "Reusable primary intellectual contribution, not title, "
    "method/application compound or historical label."
)


def seal(value):
    value = copy.deepcopy(value)
    value.pop("content_identity", None)
    value["content_identity"] = "sha256:" + sha(canonical(value))
    return validate(value)


def semantic(value):
    require(
        set(value)
        == {"label", "definition", "include_when", "exclude_when", "boundary_notes"}
    )
    require(isinstance(value["label"], str) and 3 <= len(value["label"]) <= 180)
    require(
        isinstance(value["definition"], str)
        and 12 <= len(value["definition"].split()) <= 300
    )
    for field in ("include_when", "exclude_when", "boundary_notes"):
        require(isinstance(value[field], list) and 1 <= len(value[field]) <= 32)
        require(all(isinstance(x, str) and 10 <= len(x) <= 4096 for x in value[field]))


def validate(value):
    require(
        set(value) - {"seed_provenance"}
        == {
            "schema_version",
            "registry_id",
            "taxonomy_version",
            "previous_version",
            "previous_content_identity",
            "governing_basis",
            "entries",
            "content_identity",
        }
    )
    require(value["schema_version"] == SCHEMA and value["governing_basis"] == BASIS)
    require(re.fullmatch(r"clean-[0-9a-f]{32}", value["registry_id"]) is not None)
    require(
        re.fullmatch(r"(empty|seed|version)-[0-9a-f]{32}", value["taxonomy_version"])
        is not None
    )
    require(
        value["content_identity"]
        == "sha256:"
        + sha(canonical({k: v for k, v in value.items() if k != "content_identity"}))
    )
    entries = value["entries"]
    require(isinstance(entries, list) and len(entries) <= 508)
    seeded = value.get("seed_provenance")
    seed_entries = {}
    if seeded is not None:
        require(set(seeded) == {"raw_sha256", "semantic_sha256", "registry"})
        from millefeuille.domain.taxonomy import validate_taxonomy_registry

        original = validate_taxonomy_registry(seeded["registry"])
        require(re.fullmatch(r"[0-9a-f]{64}", seeded["raw_sha256"]))
        require(seeded["semantic_sha256"] == sha(canonical(original)))
        seed_entries = {
            e["entry_id"]: {k: v for k, v in e.items() if k != "replacement_id"}
            for e in original["entries"]
            if e["status"] == "active"
        }
    by_id = {}
    labels = set()
    for e in entries:
        require(
            set(e)
            == {
                "entry_id",
                "parent_id",
                "level",
                "status",
                "label",
                "definition",
                "include_when",
                "exclude_when",
                "boundary_notes",
            }
        )
        if e["entry_id"] in seed_entries:
            require(e == seed_entries[e["entry_id"]])
        else:
            semantic(
                {
                    k: v
                    for k, v in e.items()
                    if k not in {"entry_id", "parent_id", "level", "status"}
                }
            )
            require(
                re.fullmatch(r"(parent|leaf)-[0-9a-f]{24}", e["entry_id"]) is not None
            )
        require(e["entry_id"] not in by_id and normalize(e["label"]) not in labels)
        require(e["status"] == "active" and e["level"] in (1, 2))
        by_id[e["entry_id"]] = e
        labels.add(normalize(e["label"]))
    for e in entries:
        require(
            (e["level"] == 1 and e["parent_id"] is None)
            or (
                e["level"] == 2
                and e["parent_id"] in by_id
                and by_id[e["parent_id"]]["level"] == 1
            )
        )
    if value["previous_version"] is None:
        require(
            value["previous_content_identity"] is None
            and (
                (
                    not entries
                    and seeded is None
                    and value["taxonomy_version"].startswith("empty-")
                )
                or (
                    seeded is not None
                    and entries == list(seed_entries.values())
                    and any(e["level"] == 2 for e in entries)
                    and value["taxonomy_version"].startswith("seed-")
                )
            )
        )
    else:
        require(
            isinstance(value["previous_version"], str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", value["previous_content_identity"])
            is not None
        )
        require(
            any(e["level"] == 2 for e in entries)
            and value["taxonomy_version"].startswith("version-")
        )
    return value


def initial_value(seed=None):
    ident = uuid.uuid4().hex
    value = {
        "schema_version": SCHEMA,
        "registry_id": "clean-" + ident,
        "taxonomy_version": "empty-" + ident,
        "previous_version": None,
        "previous_content_identity": None,
        "governing_basis": BASIS,
        "entries": [],
    }
    if seed is not None:
        from millefeuille.domain.decisions_classification import parse, read_bytes
        from millefeuille.domain.taxonomy import validate_taxonomy_registry

        raw = read_bytes(seed)
        original = validate_taxonomy_registry(parse(raw))
        value["seed_provenance"] = {
            "raw_sha256": sha(raw),
            "semantic_sha256": sha(canonical(original)),
            "registry": original,
        }
        value["entries"] = [
            {k: v for k, v in e.items() if k != "replacement_id"}
            for e in original["entries"]
            if e["status"] == "active"
        ]
        value["taxonomy_version"] = "seed-" + ident
    return seal(value)


def initialize(store, seed=None):
    root = safe_path(store)
    require(root.parent.is_dir())
    value = initial_value(seed)
    reserve(root)
    publish(
        root / "initialization.json",
        canonical(
            {
                "operation": "explicit-provisional-seed"
                if seed is not None
                else "explicit-empty-root",
                "initial_registry": value,
                "legacy_ancestry": False,
            }
        ),
    )
    publish(root / (value["taxonomy_version"] + ".json"), canonical(value))
    publish(root / "current.json", canonical(pointer(value)))
    return value


def pointer(value):
    return {k: value[k] for k in ("taxonomy_version", "content_identity")}


def current(store):
    root = safe_path(store)
    p = load(root / "current.json")
    require(set(p) == {"taxonomy_version", "content_identity"})
    require(
        re.fullmatch(r"(empty|seed|version)-[0-9a-f]{32}", p["taxonomy_version"])
        is not None
    )
    value = validate(load(root / (p["taxonomy_version"] + ".json")))
    initial = validate(load(root / "initialization.json")["initial_registry"])
    require(
        initial["previous_version"] is None
        and value["registry_id"] == initial["registry_id"]
        and pointer(value) == p
    )
    node = value
    seen = set()
    while node["previous_version"] is not None:
        require(node["content_identity"] not in seen)
        seen.add(node["content_identity"])
        previous = node["previous_version"]
        require(re.fullmatch(r"(empty|seed|version)-[0-9a-f]{32}", previous))
        predecessor = validate(load(root / (previous + ".json")))
        require(predecessor["content_identity"] == node["previous_content_identity"])
        require(predecessor["registry_id"] == initial["registry_id"])
        node = predecessor
    require(node == initial)
    return value


def derive(registry, proposal):
    validate(registry)
    require(
        set(proposal)
        == {
            "parent_id",
            "new_parent",
            "leaf",
            "research_question",
            "parent_insufficiency",
            "nearest_contrasts",
        }
    )
    for k in ("research_question", "parent_insufficiency"):
        require(isinstance(proposal[k], str) and 12 <= len(proposal[k].split()) <= 300)
    semantic(proposal["leaf"])
    entries = registry["entries"]
    leaves = {e["entry_id"] for e in entries if e["level"] == 2}
    contrasts = proposal["nearest_contrasts"]
    require(isinstance(contrasts, list) and len(contrasts) == len(leaves))
    seen = set()
    for c in contrasts:
        require(
            set(c)
            == {
                "entry_id",
                "shared_question",
                "distinguishing_boundary",
                "why_insufficient",
            }
        )
        require(c["entry_id"] in leaves and c["entry_id"] not in seen)
        seen.add(c["entry_id"])
        for k in ("shared_question", "distinguishing_boundary", "why_insufficient"):
            require(isinstance(c[k], str) and 12 <= len(c[k].split()) <= 300)
    require(
        len({normalize(c["distinguishing_boundary"]) for c in contrasts})
        == len(contrasts)
    )
    successor = copy.deepcopy(registry)
    parent_id = proposal["parent_id"]
    if parent_id is None:
        semantic(proposal["new_parent"])
        parent_id = (
            "parent-"
            + sha(
                canonical(
                    [
                        registry["registry_id"],
                        normalize(proposal["new_parent"]["label"]),
                    ]
                )
            )[:24]
        )
        successor["entries"].append(
            dict(
                proposal["new_parent"],
                entry_id=parent_id,
                parent_id=None,
                level=1,
                status="active",
            )
        )
    else:
        require(
            proposal["new_parent"] is None
            and any(e["entry_id"] == parent_id and e["level"] == 1 for e in entries)
        )
    leaf = proposal["leaf"]
    matches = [e for e in entries if normalize(e["label"]) == normalize(leaf["label"])]
    if matches:
        require(
            len(matches) == 1
            and matches[0]["level"] == 2
            and matches[0]["parent_id"] == parent_id
        )
        return registry, matches[0]["entry_id"]
    ident = (
        "leaf-"
        + sha(
            canonical([registry["registry_id"], parent_id, normalize(leaf["label"])])
        )[:24]
    )
    successor["entries"].append(
        dict(leaf, entry_id=ident, parent_id=parent_id, level=2, status="active")
    )
    successor.update(
        previous_version=registry["taxonomy_version"],
        previous_content_identity=registry["content_identity"],
        taxonomy_version="version-"
        + sha(canonical([registry["content_identity"], proposal]))[:32],
    )
    return seal(successor), ident
