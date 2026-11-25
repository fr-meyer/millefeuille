from pathlib import Path
"""Strict local science-ready seal; no historical finalizer dispatch."""
import hashlib
from millefeuille.domain.standalone_writeback import save_new as _save
from millefeuille.domain.decisions_classification import load, require, safe_path
RECEIPT = "05-science-ready-not-final.json"

def _sha(path):
    return hashlib.sha256(safe_path(path).read_bytes()).hexdigest()


def _science_evidence(run):
    paper = run.parent.parent
    require(run.parent.name == "runs" and len(list(run.parent.iterdir())) == 1)
    binding = load(paper / "reservation.json")
    require(binding == load(run / "01-download-started.json"))
    require(binding["run_id"] == run.name and binding["max_items"] == 1)
    manifest = load(paper / "manifest.json")
    require(all(manifest[k] == v for k, v in binding.items()))
    require(manifest["source"]["sha256"] == _sha(paper / "source.pdf"))
    require((run / "analysis/scientific.json").is_file())
    from millefeuille.domain.clean_evidence import project

    from millefeuille.domain.completed_ocr import resolve as resolve_completed_ocr
    resolve_completed_ocr(run)
    evidence = project(run)
    require(evidence["provenance"]["kind"] == "taxonomy-free-scientific-analysis")
    for stage in ("03-ocr-completed.json", "05-analysis-completed.json"):
        for artifact in load(run / stage)["files"].values():
            ref = Path(artifact["ref"])
            require(not ref.is_absolute() and ".." not in ref.parts)
            path = safe_path(run / ref)
            require(_sha(path) == artifact["sha256"])
    paths = [p for p in run.rglob("*") if p.is_file() and p.name != RECEIPT]
    paths += [paper / "reservation.json", paper / "manifest.json", paper / "source.pdf"]
    return {
        "binding": binding,
        "files": {str(p.relative_to(paper)): _sha(safe_path(p)) for p in sorted(paths)},
    }


def _snapshot(run):
    # Default fresh science remains strict: no prior final/classification attempt.
    require(not (run / "06-completed.json").exists())
    require(not (run / "deferred-final-attempt.json").exists())
    require(not (run.parent.parent / "decisions").exists())
    return _science_evidence(run)


def seal(run_dir):
    run = safe_path(run_dir)
    receipt = {
        "schema_version": "millefeuille-deferred-science/v1",
        "status": "science-ready-not-final",
        "run_dir": str(run),
        "final_classification": "not-started",
        "zotero_writeback": "none",
        **_snapshot(run),
    }
    _save(run / RECEIPT, receipt)
    from millefeuille.domain.final_durability import sync

    sync(run)
    return receipt
