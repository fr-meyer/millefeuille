"""Versioned classification-only execution/publication approval contract."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from millefeuille.domain.classification_model import prepare_classification_model_plan
from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    LiveTarget,
    validate_approved_live_receipt_for_no_effect,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.operator_preflight import (
    OperatorPreflightPacket,
    compute_operator_authorization_context_digest,
    compute_operator_root_target_id,
)
from millefeuille.domain.secure_io import read_bytes_no_follow

OUTPUT_ROOT = Path("/var/lib/millefeuille/classification-output-v1")
INPUT_FIELDS = frozenset(
    {
        "source_pack_root",
        "run_id",
        "paper_id",
        "expected_selected_text_sha256",
        "taxonomy_lock_path",
        "handoff_path",
    }
)
STOPS = (
    "auth-failure",
    "model-mismatch",
    "provider-error",
    "schema-failure",
    "source-drift",
    "usage-missing",
)


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def prepare(inputs):
    if (
        not isinstance(inputs, dict)
        or set(inputs) != INPUT_FIELDS
        or any(not isinstance(x, str) or not x for x in inputs.values())
    ):
        raise Error("classification inputs must be exact path/identity strings")
    root = Path(inputs["source_pack_root"])
    if not root.is_absolute() or ".." in root.parts or str(root) != str(root.resolve()):
        raise Error("classification source root must be canonical")
    for key in ("taxonomy_lock_path", "handoff_path"):
        path = Path(inputs[key])
        if not path.is_absolute() or ".." in path.parts:
            raise Error("classification input path must be canonical")
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise Error("classification evidence outside corpus") from exc
        read_bytes_no_follow(path, "classification input", max_bytes=2 * 1024 * 1024)
    return prepare_classification_model_plan(**inputs)


def identity(plan):
    return digest(canonical(plan.to_dict()))


@dataclass(frozen=True)
class ClassificationScope:
    operation: str
    plan_sha256: str
    packet_digest: str
    receipt_digest: str
    receipt_id: str
    output_sha256: str | None


def validate_scope(*, inputs, packet, receipt, output_sha256=None, now=None):
    packet = OperatorPreflightPacket.from_dict(packet.to_dict())
    receipt = ApprovedLiveReceipt.from_dict(receipt.to_dict())
    plan = prepare(inputs)
    execution = output_sha256 is None
    operation = "model.classify" if execution else "classification.review"
    manifest = identity(plan)
    destination = LiveTarget(
        "artifact-root", compute_operator_root_target_id(str(OUTPUT_ROOT))
    )
    targets = [
        destination,
        LiveTarget("paper-id", plan.paper_id),
        LiveTarget("analysis-run", plan.run_id),
        LiveTarget("classification-plan-v1", manifest),
        LiveTarget("taxonomy-lock", plan.taxonomy_lock["content_identity"]),
        LiveTarget(
            "preflight-scope",
            compute_operator_authorization_context_digest(packet.to_dict()),
        ),
    ]
    if not execution:
        targets.append(LiveTarget("classification-output-v1", output_sha256))
    provider = packet.provider
    credentials = tuple(
        (c.credential_type, c.reference) for c in packet.credential_requirements
    )
    if (
        packet.mode != "approved-live"
        or packet.operations != (operation,)
        or packet.run_id != plan.run_id
        or packet.source.adapter != "local-fixture"
        or (packet.source.selector.kind, packet.source.selector.value)
        != ("batch-manifest", manifest)
        or packet.source.item_cap != 1
        or packet.source.resolved_item_count != 1
        or packet.targets != tuple(sorted(targets))
        or packet.destinations != (destination,)
        or packet.artifact_root != str(OUTPUT_ROOT)
        or packet.source_pack_root != inputs["source_pack_root"]
        or packet.max_provider_calls != (1 if execution else 0)
        or packet.max_cost_usd_micros != 0
        or packet.acceptance_status != "pass"
        or packet.stop_conditions != STOPS
        or packet.rollback_actions != ("stop-and-review",)
        or (
            packet.disposal_policy.pdfs,
            packet.disposal_policy.provider_payloads,
            packet.disposal_policy.temporary_files,
        )
        != (
            "not-applicable",
            "never-persist" if execution else "not-applicable",
            "delete-after-run",
        )
        or credentials
        != ((("model-oauth", "OPENCLAW_CODEX_OAUTH_READY"),) if execution else ())
        or packet.approval_receipt is None
        or packet.approval_receipt.receipt_id != receipt.receipt_id
        or packet.approval_receipt.content_digest != receipt.content_digest
        or receipt.approval.approver_id != "fr-meyer"
    ):
        raise Error("outside classification v1 scope")
    if execution:
        if provider is None or (
            provider.provider_id,
            provider.model_id,
            provider.profile_id,
        ) != ("openai", "gpt-6-astra", "openclaw-subscription-oauth"):
            raise Error("classification provider drift")
    elif provider is not None:
        raise Error("publication cannot authorize provider calls")
    validate_approved_live_receipt_for_no_effect(
        receipt, packet.to_approved_live_request(), now=now
    )
    return ClassificationScope(
        operation,
        manifest,
        packet.content_digest,
        receipt.content_digest,
        receipt.receipt_id,
        output_sha256,
    )
