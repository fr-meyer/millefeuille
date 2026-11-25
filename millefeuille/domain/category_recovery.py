"""Explicit one-call category replacement. No replay/reset/automatic retry."""
import hashlib
import os
import re
import stat
from pathlib import Path
from millefeuille.domain import clean_final as c
from millefeuille.domain.decisions_classification import canonical, load, read_bytes, require, safe_path, sha
from millefeuille.domain.final_durability import publish, reserve
from millefeuille.domain.standalone_writeback import save_new
from millefeuille.clients.direct_generation import digest, safe_diagnostic, validate_response

NAME = "category-recovery-v1"
SOURCE_FILES = (
    "domain/category_recovery.py", "domain/clean_final.py", "cli/clean_start.py",
    "clients/direct_generation.py", "clients/subscription_adapter.py",
    "clients/native_sdk_worker.mjs", "clients/sdk_science.mjs",
    "clients/native_sdk_evidence.py", "domain/native_sdk_bridge.py",
    "domain/pipeline_receipts.py", "domain/native_physical.py",
    "domain/clean_taxonomy.py", "domain/final_taxonomy.py",
)

def _identity(s):
    return {k: getattr(s, k) for k in ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")}

def _pin(path, *, private=False):
    path = safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid())
        require(before.st_nlink == 1 and not before.st_mode & (0o077 if private else 0o022))
        require(before.st_size <= 128 * 1024 * 1024)
        h = hashlib.sha256()
        while True:
            part = os.read(fd, 65536)
            if not part: break
            h.update(part)
        require(_identity(os.fstat(fd)) == _identity(before))
        return {"identity": _identity(before), "sha256": h.hexdigest()}
    finally:
        os.close(fd)

def _paths(context):
    root = c.directory(context["run"], context.get("revision", 0))
    index = context.get("recovery_attempt_index", 1)
    require(type(index) is int and 1 <= index <= 64)
    return root, root / NAME if index == 1 else root / "category-recovery-attempts" / f"{index:06d}"

def _base(context, approval, approval_sha256, *, current=False, historical=False):
    root, attempt = _paths(context)
    approval = safe_path(approval)
    require(re.fullmatch(r"[0-9a-f]{64}", approval_sha256 or ""))
    require(_pin(approval)["sha256"] == approval_sha256)
    request = load(root / "category-request.json")
    if historical:
        # History is the immutable original request, not today's prompt generator.
        require(request["request_id"] == digest(canonical({k: v for k, v in request.items() if k != "request_id"})))
    else:
        require(request == c.category_request(context))
    require(load(root / "intent.json") == context["binding"])
    require(read_bytes(root / "request.json") == context["payload"])
    first_raw = read_bytes(root / "response.json")
    require(load(root / "http.json") == {"status": 200, "response_sha256": sha(first_raw)})
    require(c.select(first_raw, context)["chosen_id"] == c.CREATE)
    failure = load(root / "category-failure-observation.json")
    require(failure["phase"] == "generation" and failure["provider_outcome"] == "unknown")
    require(failure["request_sha256"] == sha(read_bytes(root / "category-request.json")))
    require(not (root / "category-response.json").exists())
    old_marker = safe_path(root / "category-attempt")
    marker_stat = old_marker.stat(follow_symlinks=False)
    require(stat.S_ISDIR(marker_stat.st_mode) and marker_stat.st_uid == os.getuid())
    if current:
        require(c.taxonomy.current(context["store"])["content_identity"] == context["registry"]["content_identity"])
    package = Path(__file__).parents[1]
    return {
        "schema": "millefeuille-category-recovery-binding/v1",
        "run_dir": str(context["run"]), "store": str(context["store"]),
        "root": str(root), "binding": context["binding"],
        "payload_sha256": sha(context["payload"]),
        "selected_taxonomy_identity": context["registry"]["content_identity"],
        "original_request_id": request["request_id"],
        "original_request_file_sha256": sha(read_bytes(root / "category-request.json")),
        "original_failure_sha256": sha(read_bytes(root / "category-failure-observation.json")),
        "first_decisions_response_sha256": sha(read_bytes(root / "response.json")),
        "first_decisions_http_sha256": sha(read_bytes(root / "http.json")),
        "original_unknown_marker_identity": _identity(marker_stat),
        "approval_path": str(approval), "approval_sha256": approval_sha256,
        **({"source_pins": {n: _pin(package / n) for n in SOURCE_FILES}} if not historical else {}),
        "unknown_original_outcome": True, "replacement_call_limit": 1,
    }

def _inventory(context):
    root, attempt = _paths(context)
    paper = context["run"].parent.parent
    result = {}
    size = 0
    for path in paper.rglob("*"):
        require(not path.is_symlink())
        if path == attempt or attempt in path.parents: continue
        if not path.is_file(): continue
        require(len(result) < 1024)
        pin = _pin(path, private=True)
        size += pin["identity"]["st_size"]
        require(size <= 128 * 1024 * 1024)
        result[str(path.relative_to(paper))] = pin
    return result

def _protected(context, pins):
    paper = context["run"].parent.parent
    for relative, pin in pins.items():
        path = Path(relative)
        require(not path.is_absolute() and ".." not in path.parts)
        require(_pin(paper / path, private=True) == pin)

def _index(context):
    return context.get("recovery_attempt_index", 1)

def _replacement(context, binding, proof_sha256, predecessors=()):
    root, _ = _paths(context)
    request = load(root / "category-request.json")
    index = _index(context)
    metadata = {
        "schema": "millefeuille-category-replacement-invocation/v1",
        "original_request_id": request["request_id"],
        "invocation_id": proof_sha256, "approval_sha256": binding["approval_sha256"],
        "original_outcome": "unknown", "replacement_index": index,
    }
    if index > 1:
        metadata.update(schema="millefeuille-category-replacement-invocation/v2", predecessors=list(predecessors))
    request = {**request, "input": {**request["input"], "category_recovery": metadata}}
    request.pop("request_id")
    request["request_id"] = digest(canonical(request))
    require(request["request_id"] != binding["original_request_id"])
    return request

def _dispatch(request, binding, index, predecessors):
    value = {"replacement_request_id": request["request_id"], "original_request_id": binding["original_request_id"], "original_outcome": "unknown", "automatic_retry": False}
    if index > 1:
        value.update(attempt_index=index, predecessor_chain_sha256=sha(canonical(predecessors)))
    return value

def _metadata(context, predecessors):
    root, attempt = _paths(context)
    intent = load(attempt / "intent.json")
    plan = intent["plan"]
    require(plan.get("attempt_index", 1) == _index(context))
    require(plan.get("predecessors", []) == predecessors)
    binding = plan["binding"]
    historical = {k: v for k, v in binding.items() if k != "source_pins"}
    require(historical == _base(context, binding["approval_path"], binding["approval_sha256"], historical=True))
    require(intent["proof_sha256"] == sha(canonical(plan)))
    _protected(context, plan["protected_files"])
    request = _replacement(context, binding, intent["proof_sha256"], predecessors)
    require(load(attempt / "category-request.json") == request)
    require(load(attempt / "dispatch-intent.json") == _dispatch(request, binding, _index(context), predecessors))
    return intent, request

def _predecessors(context):
    chain = []
    root, _ = _paths(context)
    for index in range(1, _index(context)):
        prior = {**context, "recovery_attempt_index": index}
        _, path = _paths(prior)
        intent, request = _metadata(prior, chain)
        require(not (path / "response.json").exists() and not (path / "candidate-response.json").exists())
        failure = load(path / "failure-observation.json")
        require(failure["original_provider_outcome"] == "unknown" and failure["replacement_provider_outcome"] == "unknown" and failure["automatic_retry"] is False)
        require(failure["phase"] in {"admission", "generation", "response-validation"})
        files = {}
        for p in path.rglob("*"):
            require(not p.is_symlink())
            if p.is_file(): files[str(p.relative_to(path))] = _pin(p, private=True)
            require(len(files) <= 1024)
        chain.append({"attempt_index": index, "relative_path": str(path.relative_to(root)), "request_id": request["request_id"], "proof_sha256": intent["proof_sha256"], "files_manifest_sha256": sha(canonical(files)), "provider_outcome": "unknown"})
    return chain

def _indexes(context):
    root, _ = _paths({**context, "recovery_attempt_index": 1})
    indexes = [1] if (root / NAME).exists() else []
    parent = root / "category-recovery-attempts"
    if parent.exists():
        safe_path(parent)
        for p in parent.iterdir():
            require(not p.is_symlink() and p.is_dir() and re.fullmatch(r"[0-9]{6}", p.name))
            index = int(p.name)
            require(2 <= index <= 64)
            indexes.append(index)
    indexes.sort()
    require(indexes == list(range(1, len(indexes) + 1)))
    return indexes

def _selected(context):
    indexes = _indexes(context)
    require(bool(indexes))
    index = context.get("recovery_attempt_index", indexes[-1])
    require(index in indexes)
    return {**context, "recovery_attempt_index": index}

def _plan(context, approval, approval_sha256):
    binding = _base(context, approval, approval_sha256, current=True)
    plan = {"binding": binding, "protected_files": _inventory(context)}
    if _index(context) > 1:
        plan.update(attempt_index=_index(context), predecessors=_predecessors(context))
    return plan

def status(context, approval, approval_sha256):
    _, attempt = _paths(context)
    indexes = _indexes(context)
    if attempt.exists():
        chain = _predecessors(context)
        _metadata(context, chain)
        return {"status": "CONSUMED_NO_NEW_PROVIDER_CALL", "attempt_index": _index(context), "response_retained": (attempt / "response.json").exists(), "original_outcome": "unknown"}
    require(_index(context) == len(indexes) + 1)
    plan = _plan(context, approval, approval_sha256)
    proof = sha(canonical(plan))
    return {"status": "READY_METADATA_ONLY_NO_PROVIDER", "attempt_index": _index(context), "proof_sha256": proof, "protected_file_count": len(plan["protected_files"]), "predecessors": plan.get("predecessors", []), "original_request_id": plan["binding"]["original_request_id"], "replacement_request_id": _replacement(context, plan["binding"], proof, plan.get("predecessors", []))["request_id"], "maximum_new_calls_this_invocation": 1}

def material(context, root):
    context = _selected(context)
    actual_root, attempt = _paths(context)
    require(actual_root == root)
    chain = _predecessors(context)
    _, request = _metadata(context, chain)
    validate_response(request, load(attempt / "response.json"))
    return request, attempt / "response.json"

def has_response(context, root):
    indexes = _indexes(context)
    if not indexes: return False
    selected = _selected(context)
    _, attempt = _paths(selected)
    if not (attempt / "response.json").exists(): return False
    material(selected, root)
    return True

def finalize(context):
    context = _selected(context)
    root, attempt = _paths(context)
    material(context, root)
    result = c.finish(context)
    _protected(context, load(attempt / "intent.json")["plan"]["protected_files"])
    return {"status": "CATEGORY_RECOVERED_LOCAL_FINALIZATION", "attempt_index": _index(context), "final": result, "predecessor_provider_outcomes": "unknown", "new_provider_calls_in_finalize": 0}

def recover(context, approval, approval_sha256, proof_sha256, acknowledge):
    require(acknowledge is True)
    _, attempt = _paths(context)
    require(not attempt.exists())
    require(_index(context) == len(_indexes(context)) + 1)
    plan = _plan(context, approval, approval_sha256)
    require(proof_sha256 == sha(canonical(plan)))
    c.generation_preflight()
    if _index(context) > 1:
        attempt.parent.mkdir(mode=0o700, exist_ok=True)
        parent = safe_path(attempt.parent).stat(follow_symlinks=False)
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and not parent.st_mode & 0o077)
    reserve(attempt)  # Irreversible exclusive one-dispatch fence for THIS index.
    save_new(attempt / "intent.json", {"schema": "millefeuille-category-replacement-intent/v1", "proof_sha256": proof_sha256, "plan": plan})
    predecessors = plan.get("predecessors", [])
    binding = plan["binding"]
    request = _replacement(context, binding, proof_sha256, predecessors)
    from millefeuille.domain.pipeline_resources import admit
    from millefeuille.domain.pipeline_receipts import reserve_request, dispatch_reserved
    from millefeuille.clients.subscription_adapter import complete
    request_path = attempt / "category-request.json"
    reserve_request(request_path, request, lambda: admit(context["run"], context["store"]))
    phase = "admission"
    try:
        require(binding == _base(context, approval, approval_sha256, current=True))
        require(predecessors == _predecessors(context))
        _protected(context, plan["protected_files"])
        save_new(attempt / "dispatch-intent.json", _dispatch(request, binding, _index(context), predecessors))
        dispatch_reserved(request_path, lambda: admit(context["run"], context["store"]))
        phase = "generation"
        response = complete(request)  # ONE call, no provider loop/automatic retry.
        phase = "response-validation"
        publish(attempt / "candidate-response.json", canonical(response))
        validate_response(request, response)
        publish(attempt / "response.json", canonical(response))
        phase = "local-finalization"
        return finalize(context)
    except Exception as error:
        save_new(attempt / "failure-observation.json", {"schema": "millefeuille-category-replacement-failure/v1", "phase": phase, "original_provider_outcome": "unknown", "replacement_provider_outcome": "validated-response-retained" if (attempt / "response.json").exists() else "unknown", "automatic_retry": False, "diagnostic": safe_diagnostic(getattr(error, "diagnostic", None))})
        raise

def command(args):
    context = {**c.prepare(args.run_dir, args.store), "recovery_attempt_index": args.attempt_index}
    if args.operation == "category-recovery-status": return status(context, args.approval, args.approval_sha256)
    if args.operation == "category-recovery-finalize": return finalize(context)
    require(args.operation == "category-recovery-live")
    return recover(context, args.approval, args.approval_sha256, args.proof_sha256, args.acknowledge_unknown_outcome)
