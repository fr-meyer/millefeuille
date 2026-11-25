from millefeuille.domain.completed_ocr import review_paths as completed_ocr_paths
from millefeuille.domain.pipeline_receipts import save as save_artifact

"""Independent SDK assessment with exact evidence binding and typed verdicts."""


class ScientificUnresolved(Exception):
    """A validated scientific outcome, never an operational failure."""

    def __init__(self, receipt):
        super().__init__("scientific candidate unresolved")
        self.receipt = receipt


from millefeuille.clients.direct_generation import (
    MODEL,
    SCHEMA,
    canonical,
    digest,
    generate,
)
from millefeuille.domain.standalone_writeback import (
    load,
    read_bytes,
    require,
    save_new,
    sha,
)


def prepare_review_request(run, note_path, payload_path, *, revision=0):
    from millefeuille.domain.scientific_revision import science_path

    paths = {
        "fulltext": run / "analysis/fulltext.txt",
        "scientific": science_path(run, revision),
        **completed_ocr_paths(run),
        "note": note_path,
        "payload": payload_path,
    }
    raw = {k: read_bytes(p, 64 * 1024 * 1024) for k, p in paths.items()}
    hashes = {k: sha(v) for k, v in raw.items()}
    request = {
        "schema_version": SCHEMA,
        "model": MODEL,
        "reasoning": "xhigh",
        "auth_class": "subscription_oauth",
        "task": "independent-review",
        "input": {
            "instruction": "Independently review ALL supplied source/OCR/table evidence, scientific synthesis, exact note and payload. Reject unsupported claims, omissions affecting conclusions, wrong numbers/citations, classification contradictions or unsafe payloads. No rewriting. Return only JSON with decision PASS, REVISE or HOLD, complete_source_read boolean, complete_note_payload_read boolean, material_findings array of objects with exactly issue, affected_claim, source_references (nonempty string array), repairable (boolean), and hashes copied exactly. REVISE requires all findings safely repairable from supplied evidence. Irresolvable uncertainty means HOLD. PASS requires no findings.",
            "hashes": hashes,
            "byte_counts": {k: len(v) for k, v in raw.items()},
            "evidence": {k: v.decode("utf-8") for k, v in raw.items()},
        },
    }
    if revision:
        request["input"]["prior_findings_and_resolution"] = load(
            science_path(run, revision).parent / "resolution.json"
        )
    request["request_id"] = digest(canonical(request))
    require(len(canonical(request)) <= 2 * 1024 * 1024)
    return request, paths, hashes


def review(run, note_path, payload_path, *, revision=0, store=None):
    dest = note_path.parent
    request, paths, hashes = prepare_review_request(
        run, note_path, payload_path, revision=revision
    )
    from millefeuille.domain.pipeline_receipts import reserve_request, dispatch_reserved
    from millefeuille.domain.pipeline_resources import admit
    from millefeuille.domain import standalone_writeback as n

    request_path = dest / "review-request.json"
    response_path = dest / "review-response.json"
    from millefeuille.domain.request_recovery import resolve

    request_path, response_path, request = resolve(request_path, request)
    if response_path.exists():
        require(load(request_path) == request)
        response = load(response_path)
        from millefeuille.clients.direct_generation import validate_response

        validate_response(request, response)
    else:
        if store is None:
            store = n.safe_path(n.load(run.parent.parent.parent / "RUN-AUTHORITY.json")["taxonomy_store"])

        def admission():
            admit(run, n.safe_path(store))

        from millefeuille.domain.pipeline_guards import stage

        with stage(run, "review"):
            admission()
            from millefeuille.domain.clean_final import generation_preflight

            generation_preflight()
            reserve_request(request_path, request, admission)
            dispatch_reserved(request_path, admission)
            from millefeuille.domain.request_recovery import verify_dispatch

            verify_dispatch(request_path)
            try:
                response = generate(request, executor_argv=None)
            except Exception as error:
                from millefeuille.clients.direct_generation import DirectGenerationError
                from millefeuille.domain.pipeline_receipts import (
                    record_generation_failure,
                )

                if isinstance(error, DirectGenerationError):
                    record_generation_failure(request_path, error, "review")
                raise
            save_new(response_path, response)
    require("transport_evidence" in response)
    result = response["output"]
    require(
        set(result)
        == {
            "decision",
            "complete_source_read",
            "complete_note_payload_read",
            "material_findings",
            "hashes",
        }
    )
    require(result["hashes"] == hashes)
    require(result["decision"] in {"PASS", "REVISE", "HOLD"})
    require(
        result["complete_source_read"] is True
        and result["complete_note_payload_read"] is True
    )
    findings = result["material_findings"]
    require(isinstance(findings, list) and len(findings) <= 64)
    for finding in findings:
        require(
            isinstance(finding, dict)
            and set(finding)
            == {"issue", "affected_claim", "source_references", "repairable"}
        )
        require(
            all(
                isinstance(finding[k], str) and 0 < len(finding[k]) <= 8192
                for k in ("issue", "affected_claim")
            )
        )
        require(type(finding["repairable"]) is bool)
        require(
            isinstance(finding["source_references"], list)
            and finding["source_references"]
            and all(
                isinstance(v, str) and v.strip() for v in finding["source_references"]
            )
        )
    require((result["decision"] == "PASS") == (findings == []))
    require(result["decision"] != "REVISE" or all(f["repairable"] for f in findings))
    require(
        all(sha(read_bytes(paths[k], 64 * 1024 * 1024)) == h for k, h in hashes.items())
    )
    if result["decision"] != "PASS":
        receipt = {
            "schema": "millefeuille-automated-review/v2",
            **result,
            "request_sha256": sha(read_bytes(request_path)),
            "response_sha256": sha(read_bytes(response_path)),
            "provider_request_id": response["provider_request_id"],
        }
        save_artifact(dest / "review-outcome.json", receipt)
        raise ScientificUnresolved(receipt)
    receipt = {
        "schema": "millefeuille-automated-review/v1",
        "decision": "PASS",
        "reviewer_kind": "independent-model-request",
        "hashes": hashes,
        "request_sha256": sha(read_bytes(request_path)),
        "response_sha256": sha(read_bytes(response_path)),
        "provider_request_id": response["provider_request_id"],
    }
    save_artifact(dest / "review-pass.json", receipt)
    return receipt
