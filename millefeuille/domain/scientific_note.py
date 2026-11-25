from millefeuille.domain.pipeline_receipts import save as save_artifact

"""Exact native scientific-note payload and truthful automated acceptance."""

import html

from millefeuille.domain import standalone_writeback as n
from millefeuille.domain.clean_evidence import project
from millefeuille.domain.clean_final import prepare, verify
from millefeuille.domain.manifest_lineage import validate
from millefeuille.domain.independent_review import review


def prepare_note(run, store, authority_path=None, *, revision=0):
    from millefeuille.domain.scientific_revision import note_directory

    evidence_name = "NOTE-EVIDENCE.json" if authority_path is None else "APPROVAL.json"
    existing = note_directory(run, revision) / evidence_name
    if existing.exists():
        from millefeuille.cli.standalone_writeback import prepare as native_prepare

        approval = n.load(existing)
        return native_prepare(
            run, approval["items"][0]["item_key"], approval["library_id"], existing
        )
    authority = n.load(authority_path) if authority_path is not None else None
    manifest, selected, lineage = validate(run)
    key = manifest["item_key"]
    library = str(manifest["library_id"])
    if authority is not None:
        n.require(authority["approved"] is True and authority["owner"] == "gateway-owner")
        n.require(authority["library_id"] == library and key in authority["keys"])
    e = project(run, revision=revision)
    final = verify(prepare(run, store, revision=revision))
    identity = "sha256:" + n.digest(
        {
            "operation": "clean-science-summary-note/v1",
            "library_id": library,
            "item_key": key,
            "run_id": run.name,
            "scientific_sha256": n.digest(e),
            "final_sha256": n.digest(final),
        }
    )
    texts = [
        selected["item"]["data"].get("title", ""),
        "Scientific summary (AI-assisted, source-backed)",
        e["summary"],
        "Final primary classification: " + final["label"],
        "Classifier: Decision API, gpt-6-luna. Scientific synthesis: openai/gpt-6-astra, requested xhigh, subscription OAuth; normalized public SDK receipt, not raw SSE. Provider-observed reasoning unavailable. Extraction: mistral-ocr-latest.",
        "Source PDF SHA256: " + manifest["source"]["sha256"],
        "Millefeuille writeback identity: " + identity,
    ]
    note = "".join("<p>" + html.escape(t) + "</p>" for t in texts if t)
    from millefeuille.domain.scientific_revision import note_directory, science_path

    dest = note_directory(run, revision)
    dest.mkdir(mode=0o700, exist_ok=True)
    save_artifact(
        dest / "note-payload.json",
        [
            {
                "itemType": "note",
                "parentItem": key,
                "note": note,
                "tags": [{"tag": "millefeuille-summary"}],
                "collections": [],
                "relations": {},
            }
        ],
    )
    from millefeuille.domain.final_durability import publish

    publish(dest / "note.html", note.encode())
    from millefeuille.domain.pipeline_guards import check_pause

    check_pause(run.parent.parent.parent / "PAUSE")
    review(run, dest / "note.html", dest / "note-payload.json", revision=revision, store=store)
    refs = {
        "00-selected.json",
        "03-ocr-completed.json",
        "analysis/scientific.json",
        "analysis/summary-request.json",
        "analysis/summary-response.json",
        "analysis/fulltext.txt",
    }
    refs.update(
        p["ref"] for p in n.load(run / "03-ocr-completed.json")["files"].values()
    )
    if revision:
        refs.update(
            str(p.relative_to(run))
            for p in science_path(run, revision).parent.iterdir()
            if p.is_file()
        )
    entry = {
        "run_dir": str(run),
        "run_id": run.name,
        "item_key": key,
        "attachment_key": selected["attachment"]["key"],
        "parent_version": selected["item"]["version"],
        "attachment_version": selected["attachment"]["version"],
        "parent_data_sha256": n.digest(selected["item"]["data"]),
        "attachment_data_sha256": n.digest(selected["attachment"]["data"]),
        "manifest_lineage": lineage,
        "manifest_sha256": lineage["manifest_sha256"],
        "source_sha256": manifest["source"]["sha256"],
        "scientific_sha256": n.digest(e),
        "taxonomy_store": str(store),
        "final_sha256": n.digest(final),
        "final_lock_identity": final["final_lock_identity"],
        "identity": identity,
        "run_files": {
            ref: n.sha(n.read_bytes(run / ref, 64 * 1024 * 1024))
            for ref in sorted(refs)
        },
        "payload_path": str(dest / "note-payload.json"),
        "payload_sha256": n.sha(n.read_bytes(dest / "note-payload.json")),
        "note_html_sha256": n.sha(note.encode()),
    }
    if revision:
        entry["revision"] = revision
    approval = {
        "schema": "millefeuille-scientific-note-evidence/v1",
        "library_type": "user",
        "library_id": library,
        "items": [entry],
        "acceptance_kind": "automated-independent-review",
        "review": str(dest / "review-pass.json"),
        "review_sha256": n.sha(n.read_bytes(dest / "review-pass.json")),
    }
    if authority is not None:
        approval.update(schema="millefeuille-clean-science-note-approval/v1",
                        approved=True, owner=authority["owner"],
                        owner_authority=str(authority_path),
                        owner_authority_sha256=n.sha(n.read_bytes(authority_path)))
    save_artifact(dest / evidence_name, approval)
    from millefeuille.cli.standalone_writeback import prepare as native_prepare

    return native_prepare(run, key, library, dest / evidence_name)
