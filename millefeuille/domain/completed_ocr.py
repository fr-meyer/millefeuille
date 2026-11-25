"""Read-only canonical OCR evidence, never a fallback or dispatch authority."""
from millefeuille.domain import standalone_writeback as n

NAMES = {"fulltext_path": "fulltext.md", "evidence_path": "evidence.json",
         "raw_response_path": "provider-response.json"}
SCHEMA = "millefeuille-completed-d5-ocr/v1"


def completion(run, files, result, policy=None):
    n.require(policy is None)  # No D5/private-budget receipt can become ordinary.
    record = {"files": files, "result": result}
    resolve(run, record=record)
    return record


def resolve(run, *, record=None):
    """Authenticate receipt and exact namespace, including on offline resume.

    Stored immutable policy refs replace transient configuration for reading.
    No deadline observation, reservation, refund or dispatch occurs here.
    """
    run = n.safe_path(run)
    record = n.load(run / "03-ocr-completed.json") if record is None else record
    n.require("d5" not in record)
    fresh = "d5" in record
    n.require(set(record) == {"files", "result"})
    n.require(set(record["files"]) == set(NAMES))
    manifest = n.load(run.parent.parent / "manifest.json")
    from millefeuille.domain.manifest_lineage import validate
    authenticated, _, _ = validate(run, require_pdf=False)
    n.require(authenticated == manifest)
    source = manifest["source"]
    n.require(source["ref"] == "source.pdf")
    n.require(run.parent.name == "runs" and run.parent.parent.name == f"{manifest['library_type']}-{manifest['library_id']}-{manifest['item_key']}")
    started = n.load(run / "02-ocr-started.json")
    n.require(started["run_id"] == run.name and started["source_sha256"] == source["sha256"])
    base = run / "ocr" / "direct-mistral"
    n.require(all(not (run / name).exists() and not (run / name).is_symlink() for name in ("ocr-retry750-fresh","ocr-d5-fresh")))
    paths = {}
    for key, name in NAMES.items():
        path = base / name
        pin = record["files"][key]
        n.require(set(pin) == {"ref", "sha256"})
        n.require(pin["ref"] == str(path.relative_to(run)))
        n.require(record["result"][key] == str(path))
        n.require(n.sha(n.read_bytes(path, 64 * 1024 * 1024)) == pin["sha256"])
        paths[key] = path
    evidence = n.parse(n.read_bytes(paths["evidence_path"], 64 * 1024 * 1024))
    response = n.parse(n.read_bytes(paths["raw_response_path"], 64 * 1024 * 1024))
    journal = n.load(base / "reservation.json")
    n.require(all(evidence[k] == v for k, v in journal.items()))
    n.require(evidence["schema_version"] == "millefeuille-direct-ocr/v1")
    n.require(evidence["provider"] == "mistral")
    n.require(evidence["requested_model"] == record["result"]["requested_model"] == "mistral-ocr-latest")
    n.require(bool(evidence["returned_model"]) and evidence["returned_model"] == response["model"] == record["result"]["returned_model"])
    n.require(evidence["page_count"] == record["result"]["page_count"] == len(response["pages"]) == journal["input_page_count"])
    n.require(all(type(page["index"]) is int and page["index"] == i and isinstance(page["markdown"], str) for i, page in enumerate(response["pages"])))
    fulltext = "\n\n".join(f"<!-- page {i + 1} -->\n{page['markdown']}" for i, page in enumerate(response["pages"])).encode("utf-8")
    n.require(fulltext == n.read_bytes(paths["fulltext_path"], 64 * 1024 * 1024))
    n.require(evidence["source_sha256"] == source["sha256"] and evidence["input_bytes"] == source["byte_size"])
    n.require(evidence["raw_response_sha256"] == record["files"]["raw_response_path"]["sha256"])
    n.require(evidence["fulltext_sha256"] == record["files"]["fulltext_path"]["sha256"])
    n.require(evidence["original_pdf_path"] == str(base / "original.pdf"))
    n.require(evidence["cleanup"] == n.load(base / "cleanup.json")["status"] == "deleted")
    # PDF deletion belongs to the separately gated retention transaction.
    for pdf in (run.parent.parent / source["ref"], base / "original.pdf"):
        n.safe_path(pdf)
        if pdf.exists():
            raw = n.read_bytes(pdf, 100 * 1024 * 1024)
            n.require(len(raw) == source["byte_size"] and n.sha(raw) == source["sha256"])
    return paths


def review_paths(run):
    paths = resolve(run)
    return {"ocr_evidence": paths["evidence_path"], "ocr_response": paths["raw_response_path"]}
