"""Ordinary post-classification note/review preparation; never publication."""

from millefeuille.domain import standalone_writeback as n


def _paths(run, revision):
    from millefeuille.domain.scientific_revision import note_directory
    from millefeuille.domain.request_recovery import chosen_paths

    dest = note_directory(run, revision)
    request, response = chosen_paths(dest / "review-request.json")
    return dest, request, response


def _ready(run, store, revision):
    from millefeuille.domain.clean_final import prepare, verify

    verify(prepare(run, store, revision=revision))


def _validated_note(run, store, revision):
    from millefeuille.cli.standalone_writeback import prepare as native_prepare

    dest, request_path, response_path = _paths(run, revision)
    evidence_path = dest / "NOTE-EVIDENCE.json"
    evidence = n.load(evidence_path)
    n.require(evidence["schema"] == "millefeuille-scientific-note-evidence/v1")
    n.require(evidence["acceptance_kind"] == "automated-independent-review")
    n.require(evidence["review"] == str(dest / "review-pass.json"))
    n.require(evidence["review_sha256"] == n.sha(n.read_bytes(dest / "review-pass.json")))
    # Native validator checks genuine response/PASS/empty findings, all bindings,
    # provenance, hashes, taxonomy final, source, immutable payload and safeHTML.
    plan = native_prepare(run, evidence["items"][0]["item_key"], evidence["library_id"], evidence_path)
    n.require(n.safe_path(plan["entry"]["taxonomy_store"]) == store)
    receipt = n.load(dest / "review-pass.json")
    response = n.load(response_path)
    sdk = response.get("transport_evidence", {}).get("sdk_result", {})
    import math
    usage = sdk.get("usage") or {}
    def number(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    counts = {k: usage[k] for k in ("input", "output", "reasoningTokens", "cacheRead", "cacheWrite", "totalTokens") if k in usage and number(usage[k])}
    cost = usage.get("cost") or {}
    counts["estimated_cost"] = {k: cost[k] for k in ("input", "output", "total") if k in cost and number(cost[k])}
    return {
        "status": "SCIENTIFIC_NOTE_READY_LOCAL_NO_WRITEBACK",
        "run_dir": str(run), "revision": revision,
        "item_key": plan["key"], "library_id": plan["library"],
        "evidence_path": str(evidence_path), "evidence_sha256": n.sha(n.read_bytes(evidence_path)),
        "payload_path": plan["entry"]["payload_path"], "payload_sha256": plan["entry"]["payload_sha256"],
        "review_request_path": str(request_path), "review_response_path": str(response_path),
        "review_decision": receipt["decision"], "provider_request_id": response["provider_request_id"],
        "review_response_sha256": n.sha(n.read_bytes(response_path)),
        "SDK": {**{k:sdk[k] for k in ("responseModel", "stopReason") if k in sdk}, "usage": counts},
        "billing": "SDK estimate only; prior attempted outcomes unchanged",
        "external_writeback": "not-attempted", "full_cycle": "not-claimed",
    }


def status(run, store, *, revision=0):
    """Strict existing-note validation or honest pending state; writes NOTHING."""
    run, store = n.safe_path(run), n.safe_path(store)
    _ready(run, store, revision)
    dest, request_path, response_path = _paths(run, revision)
    if (dest / "NOTE-EVIDENCE.json").exists():
        return _validated_note(run, store, revision)
    result = {"run_dir": str(run), "revision": revision,
              "review_request_path": str(request_path), "review_response_path": str(response_path),
              "external_writeback": "not-attempted", "automatic_retry": False}
    if response_path.exists():
        from millefeuille.clients.direct_generation import validate_response
        request, response = n.load(request_path), n.load(response_path)
        validate_response(request, response)
        n.require(request["task"] == "independent-review")
        return {**result, "status": "REVIEW_RESPONSE_RETAINED_LOCAL_VALIDATION_REQUIRED", "new_review_needed": False}
    if request_path.exists():
        from millefeuille.domain.pipeline_receipts import proven_not_started
        if proven_not_started(request_path):
            return {**result, "status": "REVIEW_NOT_STARTED_NATIVE_ADMISSION_REPLAY_ELIGIBLE", "new_review_needed": True}
        return {**result, "status": "CONSUMED_REVIEW_NO_RESPONSE_UNKNOWN_DO_NOT_RETRY", "new_review_needed": False, "provider_outcome": "unknown"}
    n.require(not (dest / "review-request.physical-attempts").exists())
    return {**result, "status": "READY_NEW_INDEPENDENT_REVIEW_REQUIRED", "new_review_needed": True}


def command(args):
    """One explicit invocation, existing native review fence/cache, bounded output."""
    from millefeuille.credentials import CredentialError, command_credential_scope
    from millefeuille.clients.direct_generation import DirectGenerationError, safe_diagnostic
    from millefeuille.domain.independent_review import ScientificUnresolved

    try:
        run, store = n.safe_path(args.run_dir), n.safe_path(args.store)
        current = status(run, store, revision=args.revision)
        if args.operation == "scientific-note-status":
            return current, 0
        n.require(args.operation == "prepare-scientific-note")
        if current["status"] == "CONSUMED_REVIEW_NO_RESPONSE_UNKNOWN_DO_NOT_RETRY":
            return current, 2
        from millefeuille.domain.scientific_note import prepare_note
        if current.get("new_review_needed"):
            # Parsed scope grants science ONLY; no writeback/discovery/Decisions.
            with command_credential_scope(args, "clean-start"):
                prepare_note(run, store, revision=args.revision)
        else:
            # Retained genuine response/evidence: strict native local replay,
            # NO CredentialSession, refresh or new native SDK invocation.
            prepare_note(run, store, revision=args.revision)
        return _validated_note(run, store, args.revision), 0
    except CredentialError:
        raise
    except ScientificUnresolved as error:
        receipt = error.receipt
        return {"status": "SCIENTIFIC_REVIEW_UNRESOLVED", "review_decision": receipt["decision"],
                "material_finding_count": len(receipt["material_findings"]), "provider_request_id": receipt["provider_request_id"],
                "request_sha256": receipt["request_sha256"], "response_sha256": receipt["response_sha256"],
                "automatic_retry": False, "external_writeback": "not-attempted"}, 2
    except DirectGenerationError as error:
        return {"status": "INDEPENDENT_REVIEW_FAILED_INSPECT_NATIVE_JOURNAL",
                "diagnostic": safe_diagnostic(error.diagnostic),
                "generation_failure_sha256": getattr(error, "generation_failure_sha256", None),
                "generation_failure_capture_failed": getattr(error, "generation_failure_capture_failed", None),
                "generation_failure_capture_error_class": getattr(error, "generation_failure_capture_error_class", None),
                "provider_outcome": "unknown", "automatic_retry": False, "external_writeback": "not-attempted"}, 2
    except Exception:
        return {"status": "blocked", "reason": "Invalid native note proof, consumed review or local preparation failure. Inspect retained artifacts; never retry an unknown provider outcome.",
                "automatic_retry": False, "external_writeback": "not-attempted"}, 2
