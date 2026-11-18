"""Read-only complete mapping plan, no automatic scientific-quality claim."""

from millefeuille.domain.clean_taxonomy import validate
from millefeuille.domain.decisions_classification import canonical, require, sha


def plan(before, after, mapping, rationale):
    validate(before)
    validate(after)
    require(after["registry_id"] == before["registry_id"])
    require(after["previous_version"] == before["taxonomy_version"])
    require(after["previous_content_identity"] == before["content_identity"])
    old = {e["entry_id"]: e for e in before["entries"] if e["level"] == 2}
    new = {e["entry_id"]: e for e in after["entries"] if e["level"] == 2}
    require(set(mapping) == set(old) and set(mapping.values()) <= set(new))
    require(isinstance(rationale, str) and len(rationale.split()) >= 20)
    # Unmerged concepts keep their IDs even when renamed. A merge maps to
    # an existing survivor, never silently reassigns an ID to another concept.
    require(all(target in old for target in mapping.values()))
    return {
        "operation": "manual-consolidation-review-required",
        "before": before["content_identity"],
        "after": after["content_identity"],
        "mapping": mapping,
        "rationale": rationale,
        "successor_sha256": sha(canonical(after)),
        "required_review": [
            "order-bias",
            "synonyms",
            "one-paper-fragmentation",
            "primary-contribution-boundaries",
            "stable-ID-meaning",
        ],
        "ocr_or_analysis_reexecution": False,
        "automatic_adoption": False,
    }
