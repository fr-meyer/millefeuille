"""Local completion of genuinely finalized clean runs; no dispatch authority."""

from millefeuille.domain.decisions_classification import load, require, safe_path

BINDING = ("run_id", "library_type", "library_id", "item_key", "attachment_key",
           "item_version", "attachment_version", "tag", "max_items", "zotero_writeback")


def _snapshot(paper):
    # Reuse the established private single-link identity/hash fence, not a service.
    from millefeuille.domain.category_recovery import _pin

    pins = {}
    total = 0
    for path in paper.rglob("*"):
        require(not path.is_symlink())
        if not path.is_file():
            continue
        pin = _pin(path, private=True)
        pins[str(path.relative_to(paper))] = pin
        total += pin["identity"]["st_size"]
        require(len(pins) <= 1024 and total <= 128 * 1024 * 1024)
    return pins


def _unchanged(paper, pins):
    from millefeuille.domain.category_recovery import _pin

    for ref, pin in pins.items():
        require(_pin(paper / ref, private=True) == pin)


def _inputs(run):
    from millefeuille.domain.manifest_lineage import validate
    from millefeuille.domain.paper_intake import _artifact
    from millefeuille.domain.completed_ocr import resolve

    manifest, selected, _ = validate(run)  # Actual source PDF size/SHA/MD5 and lineage.
    paper = run.parent.parent
    reservation = load(paper / "reservation.json")
    require(set(reservation) == set(BINDING))
    require(reservation == {key: manifest[key] for key in BINDING})
    require(manifest["max_items"] == 1 and manifest["zotero_writeback"] == "none")
    require(manifest["verification"] == "download-size-and-md5-match-fresh-zotero-metadata")
    started = load(run / "02-ocr-started.json")
    binding = {key: started[key] for key in BINDING}
    require(binding["run_id"] == run.name)
    require(all(binding[key] == manifest[key] for key in BINDING if key != "run_id"))
    require(started == {**binding, "source_sha256": manifest["source"]["sha256"], "model": "mistral-ocr-latest"})
    # For retained-source lineage, authenticate the ORIGINAL verified download too.
    original = paper / "runs" / manifest["run_id"]
    require(load(original / "01-download-started.json") == reservation)
    verified = load(original / "01-download-verified.json")
    require(verified == {**reservation, "verification": "matches-fresh-zotero-size-and-md5", "source_identity": {"byte_size": manifest["source"]["byte_size"], "md5": selected["attachment"]["data"]["md5"]}})
    require(load(run / "04-analysis-started.json") == {**binding, "model": "openai/gpt-6-astra", "reasoning": "xhigh"})
    ocr = load(run / "03-ocr-completed.json")
    resolved = resolve(run, record=ocr)
    require(all(resolved[key] == safe_path(ocr["result"][key]) for key in resolved))
    require(ocr["result"]["requested_model"] == "mistral-ocr-latest" and bool(ocr["result"]["returned_model"]))
    stages = ((ocr, ("fulltext_path", "evidence_path", "raw_response_path")),
              (load(run / "05-analysis-completed.json"), ("summary_path", "evidence_path")))
    analysis = stages[1][0]
    require(set(analysis) == {"files", "result"})
    require(analysis["result"]["model"] == "openai/gpt-6-astra" and analysis["result"]["reasoning"] == "xhigh")
    for record, names in stages:
        require(set(record["files"]) == set(names))
        for name in names:
            # Exact actual native artifact refs and content hashes, not copied receipts.
            require(record["files"][name] == _artifact(record["result"], name, run))
    return manifest, binding


def complete(run_dir, store, output_dir=None):
    """Proof -> normal shared constructor -> immutable06. Never run a stage."""
    from millefeuille.domain.clean_final import prepare, verify
    from millefeuille.domain.clean_cards import plan_export
    from millefeuille.domain.paper_intake import clean_completion_result, publish_clean_completion

    run = safe_path(run_dir)
    require(run.is_dir() and run.parent.name == "runs")
    paper = run.parent.parent
    pins = _snapshot(paper)  # Includes old stages, all failed attempts, final and cards.
    manifest, binding = _inputs(run)
    context = prepare(run, store)  # Genuine source/summary/SDK-response projection.
    final = verify(context)  # Original Decisions + selected actual category + CAS/lock.
    require(context["run"] == run and final["binding"] == context["binding"])
    # Verify existing selected card if present; missing card can be exported locally.
    path, raw, _ = plan_export(context, output_dir)
    if path.exists():
        from millefeuille.domain.decisions_classification import read_bytes
        require(read_bytes(path) == raw)
    _unchanged(paper, pins)
    result = clean_completion_result(context, binding, manifest["source"]["sha256"], output_dir)
    _unchanged(paper, pins)  # No pre-existing artifact may change before06publication.
    publish_clean_completion(run, result)
    _unchanged(paper, pins)
    return result
