"""Generic standalone tag-only transaction; ambiguous outcomes never retry."""

import os
import re

import httpx

from millefeuille.domain.decisions_classification import (
    canonical,
    load,
    require,
    sha,
)
from millefeuille.domain.final_classification import directory, verify
from millefeuille.domain.final_durability import publish as save_new
from millefeuille.domain.taxonomy import (
    validate_taxonomy_lock,
    validate_taxonomy_registry,
)


async def execute(context, authority_path, lineage_paths, *, live=False):
    final = verify(context)
    run = context["run"]
    manifest = load(run.parent.parent / "manifest.json")
    key, library, kind = (
        manifest[k] for k in ("item_key", "library_id", "library_type")
    )
    require(
        re.fullmatch(r"[A-Z0-9]{8}", key)
        and re.fullmatch(r"[1-9][0-9]*", str(library))
        and kind in {"user", "group"}
    )
    require(final["binding"]["paper_id"] == "zotero-" + key)
    require(
        run.parent.parent.name == f"{kind}-{library}-{key}"
        and manifest["run_id"] == run.name
    )
    lock = validate_taxonomy_lock(load(directory(run) / "taxonomy-lock.json"))
    registries = [validate_taxonomy_registry(load(p)) for p in lineage_paths]
    registries += [
        lock["registry_snapshot"],
        context["registry"],
        validate_taxonomy_registry(load(run / "taxonomy.json")),
    ]
    by_identity = {r["content_identity"]: r for r in registries}
    current = lock["registry_snapshot"]
    seen = set()
    while True:
        require(current["content_identity"] not in seen)
        seen.add(current["content_identity"])
        previous = current["previous_content_identity"]
        if previous is None:
            require(current["previous_version"] is None)
            break
        require(previous in by_identity)
        predecessor = by_identity[previous]
        require(
            predecessor["taxonomy_version"] == current["previous_version"]
            and predecessor["registry_id"] == current["registry_id"]
        )
        current = predecessor
    require(set(by_identity) <= seen)
    labels = {e["label"] for r in registries for e in r["entries"] if e["level"] == 2}
    binding = {
        "operation": "standalone-final-taxonomy-tag",
        "library_type": kind,
        "library_id": str(library),
        "item_key": key,
        "run_dir": str(run),
        "final_sha256": sha(canonical(final)),
        "final_lock_identity": final["final_lock_identity"],
        "lineage_identities": sorted(seen),
        "max_mutations": 1,
    }
    binding["lineage_sha256"] = {
        identity: sha(canonical(by_identity[identity])) for identity in sorted(seen)
    }
    authority = load(authority_path)
    require(
        authority["scope"] == binding
        and authority["approved"] is True
        and isinstance(authority["owner"], str)
        and bool(authority["owner"])
    )
    journal = directory(run) / "writeback"
    if journal.exists():
        intent = load(journal / "intent.json")
        require(intent["binding"] == binding)
        receipt = load(journal / "verified.json")
        before, after = load(journal / "before.json"), load(journal / "after.json")
        validate(before, after, intent["tags"], key, str(library), kind)
        require(
            receipt
            == {
                "status": "verified",
                "binding": binding,
                "after_sha256": sha(canonical(after)),
            }
        )
        return {"status": "replayed-read-only", "binding": binding}
    if not live:
        return {"status": "planned-read-only", "binding": binding}
    secret = os.environ.get("ZOTERO_WRITE_KEY")
    require(bool(secret))
    base = (
        "https://api.zotero.org/"
        + ("users" if kind == "user" else "groups")
        + "/"
        + str(library)
    )
    headers = {
        "Zotero-API-Key": secret,
        "Zotero-API-Version": "3",
        "Accept-Encoding": "identity",
    }
    async with httpx.AsyncClient(
        headers=headers,
        trust_env=False,
        follow_redirects=False,
        timeout=30,
        transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
    ) as client:

        async def get(path):
            async with client.stream("GET", base + path) as response:
                require(response.status_code == 200)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    require(len(data) <= 2_000_000)
                from millefeuille.domain.decisions_classification import parse

                return parse(bytes(data)), response.headers

        async def snapshot():
            parent, _ = await get("/items/" + key)
            children, h = await get("/items/" + key + "/children?limit=100")
            require(
                isinstance(children, list)
                and int(h["Total-Results"]) == len(children) <= 100
            )
            require(len({c["key"] for c in children}) == len(children))
            result = {
                "parent": parent,
                "children": sorted(children, key=lambda c: c["key"]),
            }
            identities(result, key, str(library), kind)
            return result

        before = await snapshot()
        tags = [t for t in before["parent"]["data"]["tags"] if t["tag"] not in labels]
        if not any(t["tag"] == "millefeuille" for t in tags):
            tags.append({"tag": "millefeuille"})
        tags.append({"tag": final["label"]})
        require(sum(t["tag"] in labels for t in tags) == 1)
        journal.mkdir(mode=0o700)
        sync_directory(journal.parent)
        save_new(journal / "before.json", canonical(before))
        save_new(journal / "intent.json", canonical({"binding": binding, "tags": tags}))
        fd = os.open(journal, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        sync_directory(journal.parent)
        if before["parent"]["data"]["tags"] != tags:
            save_new(journal / "mutation-started.json", canonical({"binding": binding}))
            sync_directory(journal)
            sync_directory(journal.parent)
            response = await client.patch(
                base + "/items/" + key,
                json={"tags": tags},
                headers={
                    "If-Unmodified-Since-Version": str(before["parent"]["version"])
                },
            )
            save_new(journal / "http.json", canonical({"status": response.status_code}))
            require(response.status_code == 204)
        after = await snapshot()
        save_new(journal / "after.json", canonical(after))
        validate(before, after, tags, key, str(library), kind)
        save_new(
            journal / "verified.json",
            canonical(
                {
                    "status": "verified",
                    "binding": binding,
                    "after_sha256": sha(canonical(after)),
                }
            ),
        )
        return {"status": "verified", "binding": binding}


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def identities(snapshot, key, library, kind):
    for item in [snapshot["parent"], *snapshot["children"]]:
        require(
            item["library"]["type"] == kind and str(item["library"]["id"]) == library
        )
        require(
            item["key"] == item["data"]["key"]
            and type(item["version"]) is int
            and item["version"] == item["data"]["version"]
        )
    require(
        snapshot["parent"]["key"] == key
        and snapshot["parent"]["data"]["itemType"] not in {"note", "attachment"}
    )
    require(all(c["data"]["parentItem"] == key for c in snapshot["children"]))


def validate(before, after, tags, key, library, kind):
    identities(before, key, library, kind)
    identities(after, key, library, kind)
    require(before["children"] == after["children"])

    def preserved(item):
        return {
            k: v
            for k, v in item["data"].items()
            if k not in {"tags", "version", "dateModified"}
        }

    require(preserved(before["parent"]) == preserved(after["parent"]))
    require(after["parent"]["data"]["tags"] == tags)
    require(after["parent"]["version"] >= before["parent"]["version"])
