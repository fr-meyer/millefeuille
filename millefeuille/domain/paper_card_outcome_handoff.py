"""Strict bounded transient evidence transport for a validated GPT card."""

import base64
import binascii
from dataclasses import asdict
import json
from pathlib import Path

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.paper_card_execution_scope import GptCardApprovalPreview
from millefeuille.domain.paper_card_inputs import plan_published_gpt_paper_card_request
from millefeuille.domain.paper_card_live_execution import TrustedGptCardOutcome
from millefeuille.domain.paper_card_results import (
    validate_published_gpt_paper_card_execution,
)
from millefeuille.domain.summary_outcome_handoff import (
    _canonical_json,
    _reject_constant,
    _require_bounded_json_shape,
    _unique_object,
)
from millefeuille.domain.summary_published_handoff import GptSummaryPublicationIdentity

_SCHEMA = "millefeuille-gpt-card-outcome-handoff/v0.1"
_MAX_BYTES = 4 * 1024 * 1024
_FIELDS = frozenset({"schema_version", "approval", "result", "output_base64"})


def encode_gpt_card_outcome_handoff(outcome: TrustedGptCardOutcome) -> bytes:
    """Encode successful transient evidence without writing it to disk."""
    if (
        not isinstance(outcome, TrustedGptCardOutcome)
        or not isinstance(outcome.approval, GptCardApprovalPreview)
        or not isinstance(outcome.execution, OpenClawModelExecution)
        or not isinstance(outcome.execution.output, bytes)
        or not isinstance(outcome.execution.result, dict)
    ):
        raise MillefeuilleContractError("GPT card handoff outcome is invalid")
    try:
        payload = {
            "schema_version": _SCHEMA,
            "approval": asdict(outcome.approval),
            "result": outcome.execution.result,
            "output_base64": base64.b64encode(outcome.execution.output).decode("ascii"),
        }
        _require_bounded_json_shape(payload)
        encoded = _canonical_json(payload)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise MillefeuilleContractError("GPT card handoff is not JSON") from exc
    if len(encoded) > _MAX_BYTES:
        raise MillefeuilleContractError("GPT card handoff is too large")
    return encoded


def decode_gpt_card_outcome_handoff(
    encoded: bytes,
    *,
    expected_approval: GptCardApprovalPreview,
    source_pack_root: str | Path,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    publication: GptSummaryPublicationIdentity,
) -> TrustedGptCardOutcome:
    """Replan the approved source and strictly validate returned bytes/result."""
    if (
        not isinstance(encoded, bytes)
        or len(encoded) > _MAX_BYTES
        or not isinstance(expected_approval, GptCardApprovalPreview)
    ):
        raise MillefeuilleContractError("GPT card handoff input is invalid")
    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        _require_bounded_json_shape(payload)
        canonical = _canonical_json(payload)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise MillefeuilleContractError("GPT card handoff JSON is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != _FIELDS
        or payload["schema_version"] != _SCHEMA
        or canonical != encoded
        or payload["approval"] != asdict(expected_approval)
        or not isinstance(payload["result"], dict)
        or not isinstance(payload["output_base64"], str)
        or type(expected_approval.request_count) is not int
        or expected_approval.request_count != 1
        or type(expected_approval.provider_calls_performed) is not int
        or expected_approval.provider_calls_performed != 0
        or type(expected_approval.writes_performed) is not int
        or expected_approval.writes_performed != 0
    ):
        raise MillefeuilleContractError("GPT card handoff identity drift")
    evidence = {
        "source_pack_root": source_pack_root,
        "route_evidence_path": route_evidence_path,
        "structure_evidence_path": structure_evidence_path,
        "preparation_path": preparation_path,
        "publication": publication,
    }
    plan = plan_published_gpt_paper_card_request(
        **evidence, run_id=expected_approval.run_id
    )
    if (
        plan.paper_id != expected_approval.paper_id
        or plan.manifest_sha256 != expected_approval.manifest_sha256
        or plan.publication_manifest_sha256
        != expected_approval.summary_publication_sha256
    ):
        raise MillefeuilleContractError("GPT card handoff source or scope drift")
    try:
        output = base64.b64decode(payload["output_base64"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MillefeuilleContractError(
            "GPT card handoff output encoding is invalid"
        ) from exc
    if base64.b64encode(output).decode("ascii") != payload["output_base64"]:
        raise MillefeuilleContractError("GPT card handoff output is not canonical")
    execution = OpenClawModelExecution(payload["result"], output)
    validated = validate_published_gpt_paper_card_execution(
        plan=plan, execution=execution, **evidence
    )
    return TrustedGptCardOutcome(expected_approval, plan, validated, execution)
