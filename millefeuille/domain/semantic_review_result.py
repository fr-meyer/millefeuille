"""Validate explicit source-reviewed obligations, not reviewer authenticity."""

from copy import deepcopy

from millefeuille.domain.evidence_answer import canonical
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import digest, scoped_reads
from millefeuille.domain.semantic_materials import build_material_binding


@scoped_reads
def validate_review_result(record, resolved_materials):
    r = deepcopy(record)
    if r.get("schema_version") != "millefeuille-semantic-review-result/v1":
        raise MillefeuilleContractError("invalid review-result schema")
    for k in ("paper_id", "run_id", "source_hash"):
        if r.get(k) != getattr(resolved_materials, k):
            raise MillefeuilleContractError("review source identity drift")
    if r.get("material_binding") != build_material_binding(resolved_materials):
        raise MillefeuilleContractError("review material binding drift")
    method = r["review_method"]
    if any(
        not isinstance(method.get(k), str) or not method[k].strip()
        for k in ("method", "reviewer_assertion", "independent_review_record_sha256")
    ):
        raise MillefeuilleContractError("separate reviewer assertion/method required")
    sources = {
        f["name"][7:]: f["sha256"]
        for f in r["material_binding"]["files"]
        if f["name"].startswith("source:")
    }
    bindings = r["source_bindings"]
    if not bindings or {b["attachment_key"] for b in bindings} != set(sources):
        raise MillefeuilleContractError("source bindings incomplete")
    for b in bindings:
        if (
            b["sha256"] != sources[b["attachment_key"]]
            or type(b["page"]) is not int
            or b["page"] < 1
            or not b["section"]
        ):
            raise MillefeuilleContractError("invalid reviewed source locator")
    c = r["coverage"]
    for observed, covered in [
        ("observed_pages", "covered_pages"),
        ("observed_sections", "covered_sections"),
    ]:
        if not c[observed] or c[observed] != c[covered]:
            raise MillefeuilleContractError("incomplete observed scientific coverage")
    if c["uncovered_material_claims"] or r.get("versions") != "resolved":
        raise MillefeuilleContractError("unresolved material claims or versions")
    claims = r["claims"]
    if not claims or len({x["claim_id"] for x in claims}) != len(claims):
        raise MillefeuilleContractError("material claims required")
    for claim in claims:
        if (
            claim["verdict"] != "supported"
            or not claim["source_locators"]
            or any(x not in bindings for x in claim["source_locators"])
            or not isinstance(claim["qualification"], str)
            or claim["claim_text_sha256"] != digest(claim["claim_text"].encode())
        ):
            raise MillefeuilleContractError("unsupported material claim/locator")
    if not isinstance(method["limitations"], list) or any(
        type(x) is not str for x in method["limitations"]
    ):
        raise MillefeuilleContractError("review limitations required")
    r["review_result_sha256"] = digest(canonical(record))
    return r
