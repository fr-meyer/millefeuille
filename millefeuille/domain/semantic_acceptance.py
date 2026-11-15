"""Snapshot-specific source review evidence, never approval or remote authority."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import threading
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import _READ_BUDGET
from millefeuille.domain.secure_io import read_bytes_no_follow as _secure_read


@contextmanager
def semantic_read_scope(budget=None):
    from millefeuille.domain.evidence_source_adapter import ReadBudget

    if threading.current_thread() is not threading.main_thread():
        raise MillefeuilleContractError("semantic read scopes are main-thread only")
    current = _READ_BUDGET.get()
    if current is not None and budget is not None and budget is not current:
        raise MillefeuilleContractError("cannot replace active semantic budget")
    selected = current if current is not None else budget
    if selected is None:
        selected = ReadBudget()
    if not isinstance(selected, ReadBudget):
        raise MillefeuilleContractError("explicit ReadBudget required")
    selected.debit()
    token = _READ_BUDGET.set(selected)
    try:
        yield selected
    finally:
        _READ_BUDGET.reset(token)


def budgeted_read(path, label, *, max_bytes):
    budget = _READ_BUDGET.get()
    if budget is None:
        raise MillefeuilleContractError("semantic read requires cumulative scope")
    budget.debit(read_bytes=1, opens=1)
    limit = min(Path(path).lstat().st_size, max_bytes)
    budget.debit(read_bytes=limit + 1, opens=len(Path(path).absolute().parts))
    return _secure_read(path, label, max_bytes=limit)


def scoped_reads(function):
    from functools import wraps

    @wraps(function)
    def wrapped(*args, budget=None, **kwargs):
        with semantic_read_scope(budget):
            return function(*args, **kwargs)

    return wrapped


SEMANTIC_DECISION_REF = Path("reports/semantic-decision.json")
SCHEMA = "millefeuille-semantic-decision/v1"
ROLES = frozenset(
    {"source", "summary", "card", "index", "duplicate-review", "retrieval"}
)


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def safe_ref(ref: str) -> Path:
    if (
        not isinstance(ref, str)
        or not ref
        or chr(0) in ref
        or ref != ref.strip()
        or "\\" in ref
        or ":" in ref
        or ref.startswith("/")
        or any(part in {"", ".", ".."} for part in ref.split("/"))
    ):
        raise MillefeuilleContractError("unsafe semantic evidence reference")
    return Path(*PurePosixPath(ref).parts)


@scoped_reads
def validate_semantic_decision(
    decision: dict[str, Any],
    *,
    root: Path,
    paper_id: str,
    run_id: str,
    source_hash: str,
    required_evidence: list[dict[str, str]],
) -> dict[str, Any]:
    """Validate an externally reviewed inventory. JSON cannot authorize publication.

    The caller supplies the independently enumerated material inventory. Merely
    listing a convenient subset in the decision does not establish coverage.
    """
    if not isinstance(decision, dict) or decision.get("schema_version") != SCHEMA:
        raise MillefeuilleContractError("invalid semantic decision schema")
    for field, expected in (
        ("paper_id", paper_id),
        ("run_id", run_id),
        ("source_hash", source_hash),
    ):
        if decision.get(field) != expected:
            raise MillefeuilleContractError("semantic decision identity drift")
    reviewer = decision.get("reviewer")
    if not isinstance(reviewer, dict) or any(
        not isinstance(reviewer.get(key), str) or not reviewer[key].strip()
        for key in ("identity", "method")
    ):
        raise MillefeuilleContractError(
            "semantic reviewer identity and method required"
        )
    if decision.get("outcome") != "pass" or any(
        decision.get(key) != "pass"
        for key in ("coverage", "citations", "claims", "versions", "retrieval")
    ):
        raise MillefeuilleContractError("semantic source review did not pass")
    if not isinstance(decision.get("limitations"), list) or any(
        not isinstance(value, str) for value in decision["limitations"]
    ):
        raise MillefeuilleContractError("semantic limitations required")
    evidence = decision.get("evidence")
    if not isinstance(evidence, list) or not evidence or evidence != required_evidence:
        raise MillefeuilleContractError("semantic material evidence inventory drift")
    if len(evidence) > 256:
        raise MillefeuilleContractError("semantic evidence count exceeds bound")
    roles, refs = set(), set()
    total_bytes = 0
    for entry in evidence:
        if not isinstance(entry, dict) or set(entry) != {"role", "ref", "sha256"}:
            raise MillefeuilleContractError("invalid semantic evidence entry")
        ref = safe_ref(entry["ref"])
        if (
            not isinstance(entry["role"], str)
            or entry["role"] not in ROLES
            or entry["ref"] in refs
        ):
            raise MillefeuilleContractError(
                "invalid semantic evidence role or duplicate ref"
            )
        refs.add(entry["ref"])
        roles.add(entry["role"])
        payload = budgeted_read(
            root / ref, "semantic evidence", max_bytes=16 * 1024 * 1024
        )
        total_bytes += len(payload)
        if total_bytes > 64 * 1024 * 1024:
            raise MillefeuilleContractError("semantic evidence bytes exceed bound")
        if digest(payload) != entry["sha256"]:
            raise MillefeuilleContractError("stale semantic evidence hash")
    if roles != ROLES:
        raise MillefeuilleContractError("semantic evidence roles incomplete")
    return decision


@scoped_reads
def require_semantic_companion(resolved: Any) -> dict[str, Any]:
    """New multi-source completion requires the sealed companion and inventory.

    Historical single-source structural records remain readable and structural.
    The separate sealed review inventory is not external authority.
    """
    decision = json.loads(
        budgeted_read(
            resolved.run_dir / SEMANTIC_DECISION_REF,
            "semantic decision",
            max_bytes=262144,
        )
    )
    inventory_record = json.loads(
        budgeted_read(
            resolved.run_dir / "reports/semantic-review-inventory.json",
            "semantic review inventory",
            max_bytes=262144,
        )
    )
    if not isinstance(inventory_record, dict) or not isinstance(decision, dict):
        raise MillefeuilleContractError("invalid semantic companion object")
    inventory = inventory_record.get("evidence")
    if digest(json.dumps(inventory_record, sort_keys=True).encode()) != decision.get(
        "inventory_sha256"
    ):
        raise MillefeuilleContractError("semantic inventory binding drift")
    if not isinstance(inventory, list):
        raise MillefeuilleContractError(
            "independent semantic review inventory required"
        )
    structural = budgeted_read(
        resolved.run_dir / "reports/acceptance-summary.json",
        "semantic structural acceptance",
        max_bytes=2 * 1024 * 1024,
    )
    if digest(structural) != decision.get("structural_sha256"):
        raise MillefeuilleContractError("semantic structural acceptance drift")
    from millefeuille.domain.semantic_materials import require_material_binding

    require_material_binding(resolved, decision)
    return validate_semantic_decision(
        decision,
        root=resolved.run_dir,
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
        required_evidence=inventory,
    )
