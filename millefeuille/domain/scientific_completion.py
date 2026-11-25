"""Ordinary reviewed note/tag publication with independent readback, PDFs retained."""
from millefeuille.credentials import credential_value
from millefeuille.domain.pipeline_receipts import save as save_artifact
import asyncio, time
from millefeuille.domain import standalone_writeback as n
from millefeuille.domain import clean_writeback as tags
from millefeuille.domain.clean_final import prepare, verify
from millefeuille.domain.clean_inventory import build
from millefeuille.domain.scientific_note import prepare_note


async def fresh_check(c, after_note, expected_tags, note_key):
    snapshot = await tags.fresh_snapshot(c["key"], c["library"], "user")
    tags.validate(after_note, snapshot, expected_tags, c["key"], c["library"], "user")
    reader = n.Transport(credential_value("ZOTERO_READ_KEY", stage="writeback"), time.monotonic() + 120)
    try:
        actual, _ = await reader.request(
            "GET", "/users/" + c["library"] + "/items/" + note_key
        )
        n.verify_note(actual, c)
        included = [v for v in snapshot["children"] if v["key"] == note_key]
        n.require(len(included) == 1 and included[0]["data"] == actual["data"])
        attachments = [
            v for v in snapshot["children"] if v["key"] == c["entry"]["attachment_key"]
        ]
        n.require(
            len(attachments) == 1 and attachments[0]["data"] == c["attachment"]["data"]
        )
        return snapshot
    finally:
        await reader.close()

def effective_lineage(context, inputs, authority, dest):
    """Follow only canonical immutable store predecessors, never scan files."""
    import re
    from millefeuille.domain.clean_taxonomy import current, validate

    store = n.safe_path(context["store"])
    if authority is not None:
        n.require(store == n.safe_path(authority["taxonomy_store"]))
    initial_path = store / "initialization.json"
    initial = validate(n.load(initial_path)["initial_registry"])
    node = current(store)  # validates native current->initial content links
    n.require(
        node["registry_id"] == initial["registry_id"]
        and (authority is None or node["registry_id"] == authority["taxonomy_root_id"])
    )
    chain = []
    pins = {}
    identities = set()
    versions = set()
    while True:
        version = node["taxonomy_version"]
        n.require(re.fullmatch(r"(empty|seed|version)-[0-9a-f]{32}", version))
        n.require(
            version not in versions and node["content_identity"] not in identities
        )
        versions.add(version)
        identities.add(node["content_identity"])
        path = store / (version + ".json")
        n.require(validate(n.load(path)) == node)
        chain.append(str(path))
        pins[str(path)] = n.sha(n.read_bytes(path))
        if node["previous_version"] is None:
            n.require(node == initial)
            break
        previous = node["previous_version"]
        n.require(re.fullmatch(r"(empty|seed|version)-[0-9a-f]{32}", previous))
        predecessor = validate(n.load(store / (previous + ".json")))
        n.require(predecessor["content_identity"] == node["previous_content_identity"])
        n.require(predecessor["registry_id"] == initial["registry_id"])
        node = predecessor
    # Every explicitly approved ancestor must be in this exact chain. No foreign
    # registry, branch, directory discovery or arbitrary successor is admitted.
    for path in inputs["lineage"]:
        if authority is not None:
            n.require(n.sha(n.read_bytes(path)) == authority["input_sha256"][str(path)])
        approved = validate(n.load(path))
        n.require(approved["registry_id"] == initial["registry_id"])
        n.require(approved["content_identity"] in identities)
    n.require(context["registry"]["content_identity"] in identities)
    for path in inputs["historical"]:
        if authority is not None:
            n.require(n.sha(n.read_bytes(path)) == authority["input_sha256"][str(path)])
    input_field = "approved_input_sha256" if authority is not None else "input_sha256"
    provenance = {
        "root_id": initial["registry_id"],
        "initialization_sha256": n.sha(n.read_bytes(initial_path)),
        input_field: (authority["input_sha256"] if authority is not None else
                      {str(path): n.sha(n.read_bytes(path)) for path in inputs["historical"] + inputs["lineage"]}),
        "effective_chain_paths": chain,
        "effective_chain_sha256": pins,
        "effective_chain_identities": sorted(identities),
    }
    saved_path = dest / "EFFECTIVE-LINEAGE.json"
    if saved_path.exists():
        saved = n.load(saved_path)
        n.require(
            saved["root_id"] == provenance["root_id"]
            and saved["initialization_sha256"] == provenance["initialization_sha256"]
            and saved[input_field] == provenance[input_field]
        )
        n.require(set(saved["effective_chain_identities"]) <= identities)
        for path, pin in saved["effective_chain_sha256"].items():
            n.require(path in pins and pins[path] == pin)
        n.require(
            set(saved["effective_chain_paths"]) == set(saved["effective_chain_sha256"])
        )
        return saved["effective_chain_paths"], saved
    save_artifact(saved_path, provenance)
    return chain, provenance

async def write_and_verify(c, inputs, authority=None, *, operator_live=False):
    n.require(authority is None and operator_live is True)
    from millefeuille.domain.request_recovery import _normal_stage_scope, _root
    _normal_stage_scope(_root(c["run"]))
    from millefeuille.domain.scientific_revision import note_directory

    revision = c["entry"].get("revision", 0)
    dest = note_directory(c["run"], revision)
    if (dest / "TAG-RESULT.json").exists():
        result = n.load(dest / "NOTE-RESULT.json")
        after_note = n.load(dest / "AFTER-NOTE.json")
        plan = n.load(dest / "TAG-PREPLAN.json")
        await fresh_check(c, after_note, plan["tags"], result["note_key"])
        return result, after_note, plan
    context = prepare(c["run"], c["entry"]["taxonomy_store"], revision=revision)
    verify(context)
    lineage, provenance = effective_lineage(context, inputs, authority, dest)
    scope = {
        "library_type": "user",
        "library_id": c["library"],
        "item_keys": [c["key"]],
    }
    inventory = build(inputs["historical"], context["registry"]["registry_id"], scope)
    save_artifact(dest / "inventory.json", inventory)
    before_note = (
        n.load(dest / "BEFORE-NOTE.json")
        if (dest / "BEFORE-NOTE.json").exists()
        else await tags.fresh_snapshot(c["key"], c["library"], "user")
    )
    save_artifact(dest / "BEFORE-NOTE.json", before_note)
    args = {
        "operator_live": operator_live,
        "inventory_path": dest / "inventory.json",
        "historical_paths": inputs["historical"],
        "snapshot_path": dest / "BEFORE-NOTE.json",
    }
    # Exercise all native deterministic ancestry/inventory/tag checks BEFORE POST.
    plan = await tags.execute(
        context, (None if operator_live else dest / "TAG-AUTHORITY.json"), lineage, live=False, **args
    )
    if plan["status"] == "replayed-read-only":
        saved_plan = n.load(dest / "TAG-PREPLAN.json")
        n.require(
            plan["binding"] == saved_plan["binding"]
            and plan["tags"] == saved_plan["tags"]
        )
        result = n.load(dest / "NOTE-RESULT.json")
        after_note = n.load(dest / "AFTER-NOTE.json")
        save_artifact(dest / "TAG-RESULT.json", plan)
        fresh = await fresh_check(c, after_note, saved_plan["tags"], result["note_key"])
        if not (dest / "FRESH-WRITEBACK.json").exists():
            save_artifact(dest / "FRESH-WRITEBACK.json", fresh)
        return result, after_note, saved_plan
    n.require(plan["status"] == "planned-read-only")
    save_artifact(dest / "TAG-PREPLAN.json", plan)
    n.require(
        all(
            n.sha(n.read_bytes(path)) == pin
            for path, pin in provenance["effective_chain_sha256"].items()
        )
    )
    from millefeuille.domain.pipeline_guards import check_pause

    check_pause(c["run"].parent.parent.parent / "PAUSE")
    from millefeuille.domain.pipeline_resources import admit

    admit(c["run"], n.safe_path(c["entry"]["taxonomy_store"]))
    result = (
        n.load(dest / "NOTE-RESULT.json")
        if (dest / "NOTE-RESULT.json").exists()
        else await n.execute(c, True)
    )
    n.require(result["status"] in {"written-verified", "already-written-verified"})
    save_artifact(dest / "NOTE-RESULT.json", result)
    after_note = (
        n.load(dest / "AFTER-NOTE.json")
        if (dest / "AFTER-NOTE.json").exists()
        else await tags.fresh_snapshot(c["key"], c["library"], "user")
    )
    save_artifact(dest / "AFTER-NOTE.json", after_note)
    args["snapshot_path"] = dest / "AFTER-NOTE.json"
    after_plan = await tags.execute(
        context, (None if operator_live else dest / "TAG-AUTHORITY.json"), lineage, live=False, **args
    )
    n.require(
        after_plan["binding"] == plan["binding"] and after_plan["tags"] == plan["tags"]
    )
    if authority is not None:
        save_artifact(
            dest / "TAG-AUTHORITY.json",
            {
                "approved": True,
                "complete_historical_inventory": True,
                "owner": authority["owner"],
                "scope": plan["binding"],
                "acceptance_kind": "automated-independent-review",
                "review_sha256": n.sha(n.read_bytes(dest / "review-pass.json")),
                "effective_lineage_sha256": n.sha(
                    n.read_bytes(dest / "EFFECTIVE-LINEAGE.json")
                ),
            },
        )
    check_pause(c["run"].parent.parent.parent / "PAUSE")
    from millefeuille.domain.pipeline_resources import admit

    admit(c["run"], n.safe_path(c["entry"]["taxonomy_store"]))
    tagged = await tags.execute(
        context, (None if operator_live else dest / "TAG-AUTHORITY.json"), lineage, live=True, **args
    )
    n.require(tagged["status"] in {"verified", "replayed-read-only"})
    save_artifact(dest / "TAG-RESULT.json", tagged)
    if not (dest / "FRESH-WRITEBACK.json").exists():
        save_artifact(
            dest / "FRESH-WRITEBACK.json",
            await fresh_check(c, after_note, plan["tags"], result["note_key"]),
        )
    return result, after_note, plan

def complete(run, store, authority_path=None, inputs=None, *, prepared=None, retain_pdfs=False, operator_live=False):
    n.require(authority_path is None and operator_live is True and inputs is not None)
    from millefeuille.domain.request_recovery import _normal_stage_scope, _root
    _normal_stage_scope(_root(run))
    c = prepared if prepared is not None else prepare_note(run, store)
    result, after_note, plan = asyncio.run(write_and_verify(c, inputs, operator_live=True))
    from millefeuille.domain.scientific_revision import note_directory
    dest = note_directory(run, c["entry"].get("revision", 0))
    receipt = {"status": "publication-verified-pdf-retained", "item_key": c["key"],
               "run_dir": str(run), "note_key": result["note_key"],
               "review_sha256": n.sha(n.read_bytes(dest / "review-pass.json")),
               "cloud_originals_preserved": True}
    save_artifact(dest / "PUBLICATION.json", receipt)
    return receipt
