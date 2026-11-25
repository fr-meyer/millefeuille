"Generic standalone tag-only transaction; ambiguous outcomes never retry."
from millefeuille.credentials import credential_value

import os
import re

import httpx

from millefeuille.domain.clean_final import directory, verify
from millefeuille.domain.decisions_classification import (
    canonical,
    load,
    read_bytes,
    require,
    sha,
)
from millefeuille.domain.final_durability import publish as save_new


async def execute(
    context,
    authority_path,
    lineage_paths,
    *,
    inventory_path,
    historical_paths,
    snapshot_path=None,
    live=False,
    reconcile=False,
    operator_live=False,
):
    require(not (live and reconcile))
    final = verify(context)
    run = context["run"]
    from millefeuille.domain.manifest_lineage import validate as source_lineage

    manifest, _, manifest_binding = source_lineage(run)
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
        and manifest_binding["run_id"] == run.name
    )
    from millefeuille.domain.clean_inventory import plan as tag_plan
    from millefeuille.domain.clean_inventory import validate as inventory_validate
    from millefeuille.domain.clean_taxonomy import validate as clean_validate

    lock = load(directory(run, context.get("revision", 0)) / "taxonomy-lock.json")
    registries = [clean_validate(load(p)) for p in lineage_paths]
    registries += [clean_validate(lock["registry_snapshot"]), context["registry"]]
    by_identity = {r["content_identity"]: r for r in registries}
    from millefeuille.domain.clean_taxonomy import current as active_registry

    current = active_registry(context["store"])
    predecessors = {
        r["previous_content_identity"]
        for r in by_identity.values()
        if r["previous_content_identity"] is not None
    }
    heads = [r for identity, r in by_identity.items() if identity not in predecessors]
    require(len(heads) == 1)
    head = heads[0]
    descendant_seen = set()
    while current["content_identity"] != head["content_identity"]:
        require(current["content_identity"] not in descendant_seen)
        descendant_seen.add(current["content_identity"])
        previous = current["previous_version"]
        require(
            isinstance(previous, str)
            and re.fullmatch(r"(empty|seed|version)-[0-9a-f]{32}", previous)
        )
        predecessor = clean_validate(load(context["store"] / (previous + ".json")))
        require(
            predecessor["content_identity"] == current["previous_content_identity"]
            and predecessor["registry_id"] == current["registry_id"]
        )
        entries = {entry["entry_id"]: entry for entry in current["entries"]}
        require(
            all(
                entries.get(entry["entry_id"]) == entry
                for entry in predecessor["entries"]
            )
        )
        current = predecessor
    bound = lock["registry_snapshot"]
    chosen = final["chosen_id"]
    chosen_definition = [
        entry for entry in bound["entries"] if entry["entry_id"] == chosen
    ]
    require(len(chosen_definition) == 1)
    require(
        [entry for entry in current["entries"] if entry["entry_id"] == chosen]
        == chosen_definition
    )
    seen = set()
    while True:
        require(current["content_identity"] not in seen)
        seen.add(current["content_identity"])
        previous = current["previous_content_identity"]
        if previous is None:
            require(current["previous_version"] is None)
            require(
                current
                == load(context["store"] / "initialization.json")["initial_registry"]
            )
            break
        require(previous in by_identity)
        predecessor = by_identity[previous]
        require(
            predecessor["taxonomy_version"] == current["previous_version"]
            and predecessor["registry_id"] == current["registry_id"]
        )
        successor_entries = {entry["entry_id"]: entry for entry in current["entries"]}
        require(
            all(
                successor_entries.get(entry["entry_id"]) == entry
                for entry in predecessor["entries"]
            )
        )
        current = predecessor
    require(
        bound["content_identity"] in seen
        and context["registry"]["content_identity"] in seen
    )
    require(set(by_identity) <= seen)
    inventory = load(inventory_path)
    scope = inventory["scope"]
    require(
        scope["library_type"] == kind
        and scope["library_id"] == str(library)
        and key in scope["item_keys"]
    )
    labels = inventory_validate(
        inventory, historical_paths, current["registry_id"], scope
    )
    seed = current.get("seed_provenance")
    if seed is not None:
        require(
            any(
                source["source_sha256"] == seed["raw_sha256"]
                and source["semantic_sha256"] == seed["semantic_sha256"]
                for source in inventory["sources"]
            )
        )
    labels |= {e["label"] for r in registries for e in r["entries"]}
    binding = {
        "operation": "standalone-clean-taxonomy-tag",
        "manifest_lineage": manifest_binding,
        "historical_inventory_sha256": sha(canonical(inventory)),
        "root_id": current["registry_id"],
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
    require(type(operator_live) is bool)
    if operator_live:
        require(authority_path is None and not reconcile)
    if (live or reconcile) and not operator_live:
        authority = load(authority_path)
        require(
            canonical(authority["scope"]) == canonical(binding)
            and authority["complete_historical_inventory"] is True
            and authority["approved"] is True
            and isinstance(authority["owner"], str)
            and bool(authority["owner"])
        )
    journal = directory(run, context.get("revision", 0)) / "writeback"
    if reconcile:
        require(journal.exists())
    if journal.exists():
        reconciled_now = False
        intent = load(journal / "intent.json")
        require(canonical(intent["binding"]) == canonical(binding))
        before, after = load(journal / "before.json"), load(journal / "after.json")
        require(tags_equal(intent["tags"], tag_plan(before, final, labels)["tags"]))
        validate(before, after, intent["tags"], key, str(library), kind)
        if not (journal / "verified.json").exists():
            require(reconcile)
            reconciled_now = True
            await reconcile_recorded(
                journal, binding, before, after, key, str(library), kind
            )
        receipt = load(journal / "verified.json")
        require(
            receipt
            == {
                "status": "verified",
                "binding": binding,
                "after_sha256": sha(canonical(after)),
            }
        )
        return {
            "status": "reconciled-read-only"
            if reconciled_now
            else "replayed-read-only",
            "binding": binding,
            "tags": intent["tags"],
        }
    if not live:
        require(snapshot_path is not None)
        snapshot = load(snapshot_path)
        identities(snapshot, key, str(library), kind)
        return {
            "status": "planned-read-only",
            "binding": binding,
            **tag_plan(snapshot, final, labels),
        }
    secret = credential_value("ZOTERO_WRITE_KEY", stage="writeback")
    read_secret = credential_value("ZOTERO_READ_KEY", stage="writeback")
    require(bool(secret) and bool(read_secret))
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
    async with (
        httpx.AsyncClient(
            headers=headers,
            trust_env=False,
            follow_redirects=False,
            timeout=30,
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        ) as client,
        httpx.AsyncClient(
            headers={**headers, "Zotero-API-Key": read_secret},
            trust_env=False,
            follow_redirects=False,
            timeout=30,
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        ) as reader,
    ):
        for transport, writable in ((reader, False), (client, True)):
            response = await transport.get("https://api.zotero.org/keys/current")
            require(response.status_code == 200)
            metadata = response.json()
            require(str(metadata["userID"]) == str(library))
            access = (
                metadata["access"]["user"]
                if kind == "user"
                else metadata["access"]["groups"][str(library)]
            )
            require(access.get("library") is True)
            require(
                access.get("write") is True if writable else access.get("notes") is True
            )
            del metadata

        async def get(path):
            async with reader.stream("GET", base + path) as response:
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
        tags = tag_plan(before, final, labels)["tags"]
        from millefeuille.domain.request_recovery import _normal_stage_scope, _root
        from millefeuille.domain.postrun_reuse import forbid_new_provider
        _normal_stage_scope(_root(run))
        forbid_new_provider()
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
        if not tags_equal(before["parent"]["data"]["tags"], tags):
            save_new(journal / "mutation-started.json", canonical({"binding": binding}))
            sync_directory(journal)
            sync_directory(journal.parent)
            response = await client.patch(
                base + "/items/" + key,
                json={"tags": tags},
                headers={"If-Unmodified-Since-Version": str(before["parent"]["version"])},
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
    require(canonical(before["children"]) == canonical(after["children"]))

    def preserved(item):
        return {
            k: v
            for k, v in item["data"].items()
            if k not in {"tags", "version", "dateModified"}
        }

    require(
        canonical(preserved(before["parent"])) == canonical(preserved(after["parent"]))
    )
    require(tags_equal(after["parent"]["data"]["tags"], tags))
    require(after["parent"]["version"] >= before["parent"]["version"])


def tags_equal(left, right):
    """Exact JSON tag-dictionary multiset; order alone is irrelevant."""

    def values(tags):
        require(type(tags) is list)
        for tag in tags:
            require(type(tag) is dict and type(tag.get("tag")) is str)
            require(all(type(key) is str for key in tag))
        return sorted(canonical(tag) for tag in tags)

    return values(left) == values(right)


async def fresh_snapshot(key, library, kind):
    """Reader only: permission metadata and complete exact item/children snapshot."""
    secret = credential_value("ZOTERO_READ_KEY", stage="writeback")
    require(bool(secret))
    base = (
        "https://api.zotero.org/"
        + ("users" if kind == "user" else "groups")
        + "/"
        + library
    )
    async with httpx.AsyncClient(
        headers={
            "Zotero-API-Key": secret,
            "Zotero-API-Version": "3",
            "Accept-Encoding": "identity",
        },
        trust_env=False,
        follow_redirects=False,
        timeout=30,
        transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
    ) as reader:

        async def get(url):
            async with reader.stream("GET", url) as response:
                require(response.status_code == 200)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    require(len(data) <= 2_000_000)
                from millefeuille.domain.decisions_classification import parse

                return parse(bytes(data)), response.headers

        metadata, _ = await get("https://api.zotero.org/keys/current")
        require(str(metadata["userID"]) == library)
        access = (
            metadata["access"]["user"]
            if kind == "user"
            else metadata["access"]["groups"][library]
        )
        require(access.get("library") is True and access.get("notes") is True)
        del metadata
        parent, _ = await get(base + "/items/" + key)
        children, headers = await get(base + "/items/" + key + "/children?limit=100")
        require(
            type(children) is list
            and int(headers["Total-Results"]) == len(children) <= 100
        )
        require(len({child["key"] for child in children}) == len(children))
        snapshot = {
            "parent": parent,
            "children": sorted(children, key=lambda child: child["key"]),
        }
        identities(snapshot, key, library, kind)
        return snapshot


async def reconcile_recorded(journal, binding, before, after, key, library, kind):
    """One completed HTTP204 plus strict fresh readback; never calls mutation APIs."""
    intent = load(journal / "intent.json")
    require(canonical(intent["binding"]) == canonical(binding))
    require(load(journal / "mutation-started.json") == {"binding": binding})
    require(load(journal / "http.json") == {"status": 204})
    require(
        load(journal / "before.json") == before
        and load(journal / "after.json") == after
    )
    require(not (journal / "verified.json").exists())
    validate(before, after, intent["tags"], key, library, kind)
    pins = {
        name: sha(read_bytes(journal / name))
        for name in (
            "before.json",
            "intent.json",
            "mutation-started.json",
            "http.json",
            "after.json",
        )
    }
    fresh = await fresh_snapshot(key, library, kind)
    # Entire wrapper/data/version/children must match; no volatile-field allowances.
    require(canonical(fresh) == canonical(after))
    validate(before, fresh, intent["tags"], key, library, kind)
    for name, pin in pins.items():
        require(sha(read_bytes(journal / name)) == pin)
    receipt = {
        "schema": "millefeuille-clean-tag-reconciliation/v1",
        "status": "fresh-read-only-verified",
        "binding": binding,
        "journal_sha256": pins,
        "fresh_snapshot": fresh,
        "fresh_snapshot_sha256": sha(canonical(fresh)),
        "mutation_http_status": 204,
        "new_mutations": 0,
    }
    path = journal / "reconciliation.json"
    if path.exists():
        require(load(path) == receipt)
    else:
        save_new(path, canonical(receipt))
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
    sync_directory(journal)
