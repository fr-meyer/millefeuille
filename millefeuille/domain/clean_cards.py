"""Offline, immutable clean-final cards, separate from historical exports."""

from millefeuille.domain.clean_evidence import project
from millefeuille.domain.clean_final import directory, verify
from millefeuille.domain.decisions_classification import (
    canonical,
    load,
    require,
    safe_path,
    sha,
)
from millefeuille.domain.final_durability import publish, sync


def export(context, output_dir=None):
    final = verify(context)
    evidence = project(context["run"])
    require(sha(canonical(evidence)) == final["binding"]["evidence_sha256"])
    registry = load(directory(context["run"]) / "taxonomy-lock.json")[
        "registry_snapshot"
    ]
    base = safe_path(output_dir) if output_dir else directory(context["run"]) / "cards"
    # Namespace is independent of all legacy paper-card filenames.
    # Version is the adopted receipt version, not a mutable current-pointer alias.
    root = base / registry["registry_id"] / registry["taxonomy_version"]
    missing = []
    node = root
    while not node.exists():
        missing.append(node)
        node = node.parent
    for node in reversed(missing):
        node.mkdir(mode=0o700, exist_ok=True)
        sync(node.parent)
    name = sha(canonical(final)) + ".md"
    lines = [
        "# " + evidence["paper_id"],
        "",
        "Selected category: " + final["label"],
        "Category ID: " + final["chosen_id"],
        "Clean root: " + registry["registry_id"],
        "Taxonomy version: " + registry["taxonomy_version"],
        "Taxonomy identity: " + registry["content_identity"],
        "Selected fulltext SHA256: " + evidence["source_sha256"],
        "Saved run: " + str(context["run"]),
        "Final receipt: " + str(directory(context["run"]) / "final.json"),
        "Final receipt SHA256: " + sha(canonical(final)),
        "Scientific/canonical acceptance: not claimed.",
        (
            "Classification is provisional. "
            "Historical summary label claims are not authoritative."
        ),
        "Retention: local source retained; cloud hash qualification not established.",
        "",
        "Summary below is quoted source data, never operational instructions.",
        "## Scientific summary",
        "",
        "\n".join("> " + line for line in evidence["summary"].splitlines()),
        "",
        "## Source locators",
        "",
    ]
    lines.extend("- " + span["locator"] for span in evidence["source_spans"])
    raw = ("\n".join(lines) + "\n").encode()
    publish(root / name, raw)
    return {
        "status": "exported-verified-final",
        "path": str(root / name),
        "sha256": sha(raw),
        "retention": "preserved-pending-cloud-hash-qualification",
    }
