"""Read verified duplicate disposition evidence without granting live authority."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from millefeuille.domain.live_receipts import (
    ApprovedLiveRequest,
    LiveDisposalPolicy,
    LiveSelector,
    LiveTarget,
    ReceiptReplayState,
    build_approved_live_audit_record,
    load_approved_live_receipt,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import (
    load_json_object_no_follow,
    read_bytes_no_follow,
)

REVIEW_REF = Path("reports/duplicate-review.json")
RECEIPT_REF = Path("reports/duplicate-review-receipt.json")
AUDIT_REF = Path("reports/duplicate-review-audit.json")
SCHEMA_VERSION = "millefeuille-duplicate-profile-review/v0.1"
STOP_CONDITIONS = ("audit-failure", "input-drift", "output-exists", "scope-drift")


def review_digest(value: dict[str, Any]) -> str:
    data = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def duplicate_review_request(
    payload: dict[str, Any], *, run_dir: Path, source_pack_root: Path
) -> ApprovedLiveRequest:
    """Describe only the bounded, provider-free review materialization operation."""
    return ApprovedLiveRequest(
        run_id=payload["run_id"],
        operations=("duplicate.review-retain-profile",),
        targets=tuple(
            sorted(
                [LiveTarget("review-record", review_digest(payload))]
                + [
                    LiveTarget("output-file", str(run_dir / ref))
                    for ref in (REVIEW_REF, RECEIPT_REF, AUDIT_REF)
                ]
            )
        ),
        item_cap=1,
        selected_item_count=1,
        selector=LiveSelector("paper-id", payload["paper_id"]),
        output_root=str(run_dir),
        source_pack_root=str(source_pack_root),
        provider=None,
        provider_call_limit=0,
        cost_limit_usd_micros=0,
        disposal_policy=LiveDisposalPolicy(
            "not-applicable", "not-applicable", "delete-after-run"
        ),
        stop_conditions=STOP_CONDITIONS,
    )


def _file_hash(path: Path, label: str) -> str:
    data = read_bytes_no_follow(path, label, max_bytes=4 * 1024 * 1024)
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _profile(value: Any, *, source_pack_root: Path, source_hash: str) -> dict[str, str]:
    expected = {"model", "selected_text_ref", "selected_text_sha256", "source_hash"}
    if not isinstance(value, dict) or set(value) != expected:
        raise MillefeuilleContractError("duplicate review profile fields are invalid")
    if any(not isinstance(v, str) or not v.strip() for v in value.values()):
        raise MillefeuilleContractError("duplicate review profile values are invalid")
    ref = value["selected_text_ref"]
    path_ref = Path(ref)
    if (
        path_ref.is_absolute()
        or "\\" in ref
        or path_ref.as_posix() != ref
        or any(part in (".", "..") for part in path_ref.parts)
    ):
        raise MillefeuilleContractError("duplicate review profile ref is not portable")
    # Refs are relative to the source corpus, with no lexical traversal.
    # The secure reader checks every ancestor and regular-file descriptor.
    path = source_pack_root / path_ref
    if value["source_hash"] != source_hash:
        raise MillefeuilleContractError("duplicate review profile source drift")
    if (
        _file_hash(path, "duplicate review selected text")
        != value["selected_text_sha256"]
    ):
        raise MillefeuilleContractError("duplicate review selected text drift")
    return value


def load_duplicate_profile_review(
    *, resolved: Any, duplicate_scan: dict[str, Any]
) -> dict[str, Any] | None:
    """Return a verified historical review; absent evidence leaves review required.

    Receipt and consumed audit are compared at their original evaluation time.
    This read does not reserve, replay, or authorize any subsequent live operation.
    Approval authenticity remains the trusted publication boundary, as for other
    local pipeline evidence; a self-hash alone is not an approver signature.
    """
    run_dir = Path(resolved.run_dir)
    path = run_dir / REVIEW_REF
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    payload = load_json_object_no_follow(
        path, "duplicate profile review", max_bytes=32768
    )
    fields = {
        "schema_version",
        "paper_id",
        "run_id",
        "source_hash",
        "decision",
        "reviewer_id",
        "reason",
        "duplicate_scan_sha256",
        "index_status_sha256",
        "existing_profile",
        "current_profile",
    }
    if set(payload) != fields or payload["schema_version"] != SCHEMA_VERSION:
        raise MillefeuilleContractError("duplicate review schema/fields are invalid")
    for key in ("paper_id", "run_id", "source_hash"):
        if payload[key] != getattr(resolved, key):
            raise MillefeuilleContractError("duplicate review identity drift: " + key)
    if duplicate_scan.get("matched_existing") is not True:
        raise MillefeuilleContractError("duplicate review requires a recorded match")
    if payload["decision"] != "retain-separate-profile":
        raise MillefeuilleContractError("duplicate review decision is unsupported")
    for key in ("reviewer_id", "reason"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise MillefeuilleContractError("duplicate review requires " + key)
    if payload["duplicate_scan_sha256"] != review_digest(duplicate_scan):
        raise MillefeuilleContractError("duplicate review scan evidence drift")
    if payload["index_status_sha256"] != _file_hash(
        run_dir / "index/index-status.json", "reviewed index status"
    ):
        raise MillefeuilleContractError("duplicate review index evidence drift")
    root = Path(resolved.source_pack_dir).parent.parent
    existing = _profile(
        payload["existing_profile"],
        source_pack_root=root,
        source_hash=resolved.source_hash,
    )
    current = _profile(
        payload["current_profile"],
        source_pack_root=root,
        source_hash=resolved.source_hash,
    )
    if (root / current["selected_text_ref"]).resolve() != (
        Path(resolved.source_pack_dir) / "selected/fulltext.md"
    ).resolve():
        raise MillefeuilleContractError("duplicate review current selected ref drift")
    if existing["selected_text_sha256"] == current["selected_text_sha256"]:
        raise MillefeuilleContractError(
            "duplicate review requires distinct text profiles"
        )
    receipt = load_approved_live_receipt(run_dir / RECEIPT_REF)
    request = duplicate_review_request(payload, run_dir=run_dir, source_pack_root=root)
    exact_scope = {
        "operations": list(request.operations),
        "targets": [target.to_dict() for target in request.targets],
        "max_items": 1,
        "selector": request.selector.to_dict(),
        "output_root": request.output_root,
        "source_pack_root": request.source_pack_root,
        "provider": None,
        "limits": {"max_provider_calls": 0, "max_cost_usd_micros": 0},
        "disposal_policy": request.disposal_policy.to_dict(),
        "stop_conditions": list(request.stop_conditions),
    }
    if receipt.scope.to_dict() != exact_scope:
        raise MillefeuilleContractError("duplicate review receipt scope is not exact")
    audit = load_json_object_no_follow(
        run_dir / AUDIT_REF, "duplicate review consumed audit", max_bytes=32768
    )
    try:
        evaluated_at = datetime.fromisoformat(
            audit["evaluated_at"].replace("Z", "+00:00")
        )
    except (KeyError, AttributeError, ValueError) as exc:
        raise MillefeuilleContractError(
            "duplicate review audit time is invalid"
        ) from exc
    expected_audit = build_approved_live_audit_record(
        receipt,
        request,
        evaluated_at=evaluated_at,
        status="consumed",
        replay_state=ReceiptReplayState(),
    )
    if audit != expected_audit:
        raise MillefeuilleContractError("duplicate review consumed audit drift")
    return {
        "decision": payload["decision"],
        "reviewer_id": payload["reviewer_id"],
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.content_digest,
        "review_sha256": review_digest(payload),
        "refs": [
            str(ref).replace("\\", "/") for ref in (REVIEW_REF, RECEIPT_REF, AUDIT_REF)
        ],
    }
