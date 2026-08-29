"""Strict provider-neutral contracts for bounded non-OCR model execution.

The request and result envelopes intentionally exclude prompts, paper text, provider
payloads, credentials, and model output. Callers pass provider input separately and
bind it to the request by SHA-256; successful results bind transient output the same
way. Provider adapters must validate these envelopes before and after every call.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import (
    SUMMARY_MODEL_STAGES,
    build_summary_execution_plan,
)

MODEL_EXECUTOR_REQUEST_SCHEMA_VERSION = "millefeuille-model-executor-request/v0.1"
MODEL_EXECUTOR_RESULT_SCHEMA_VERSION = "millefeuille-model-executor-result/v0.1"

ALLOWED_EXECUTOR_MODELS = frozenset(
    {
        "openai/gpt-5.6-sol",
        "xai/grok-4.6",
    }
)
ALLOWED_EXECUTOR_TASKS = frozenset(
    {
        "structure",
        "summarize_page",
        "summarize_section",
        "summarize_full_paper",
    }
)
REQUIRED_EXECUTOR_THINKING = "xhigh"
RETRYABLE_FAILURE_CODES = frozenset(
    {
        "empty_output",
        "model_unavailable",
        "provider_error",
        "timeout",
    }
)
ATTEMPT_FAILURE_CODES = frozenset(
    {
        *RETRYABLE_FAILURE_CODES,
        "actual_model_mismatch",
        "auth_not_oauth",
        "auth_unavailable",
        "input_drift",
        "invalid_response",
        "schema_validation_failed",
        "transport_ambiguous",
    }
)

_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "task",
        "requested_model",
        "thinking",
        "authentication",
        "prompt_template",
        "input",
        "output_contract",
        "timeout_seconds",
        "retry",
        "fallback",
        "idempotency_key",
    }
)
_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "request_sha256",
        "idempotency_key",
        "requested_model",
        "actual_model",
        "thinking",
        "authentication",
        "started_at",
        "completed_at",
        "attempts",
        "fallback",
        "schema_validation",
        "output",
        "usage",
        "cost",
    }
)
_TASK_FIELDS = frozenset(
    {
        "kind",
        "unit_id",
        "source_locator_count",
        "source_locators_sha256",
    }
)
_AUTH_REQUEST_FIELDS = frozenset(
    {"control_plane", "required_class", "api_key_allowed"}
)
_TEMPLATE_FIELDS = frozenset({"id", "version"})
_HASHED_PAYLOAD_FIELDS = frozenset({"sha256", "bytes"})
_OUTPUT_CONTRACT_FIELDS = frozenset({"schema_id", "schema_version"})
_RETRY_FIELDS = frozenset({"max_attempts", "retry_on"})
_FALLBACK_REQUEST_FIELDS = frozenset({"policy", "models"})
_AUTH_RESULT_FIELDS = frozenset({"class", "profile_ref"})
_ATTEMPT_FIELDS = frozenset(
    {
        "sequence",
        "model",
        "actual_model",
        "status",
        "started_at",
        "completed_at",
        "auth_class",
        "auth_profile_ref",
        "failure_code",
    }
)
_FALLBACK_RESULT_FIELDS = frozenset({"used", "reason"})
_SCHEMA_VALIDATION_FIELDS = frozenset(
    {"status", "schema_id", "schema_version"}
)
_OUTPUT_RESULT_FIELDS = frozenset({"sha256", "bytes"})
_USAGE_FIELDS = frozenset({"input_tokens", "output_tokens", "total_tokens"})
_COST_FIELDS = frozenset({"currency", "micro_usd"})
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_SCHEMA_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_SAFE_PROFILE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z\Z"
)
_JSON_SAFE_INTEGER_MAX = (1 << 53) - 1


def build_model_executor_request(
    *,
    task_kind: str,
    unit_id: str,
    source_locators: list[str],
    requested_model: str,
    thinking: str,
    prompt_template_id: str,
    prompt_template_version: str,
    input_payload: bytes,
    output_schema_id: str,
    output_schema_version: str,
    timeout_seconds: int,
    max_attempts: int,
    retry_on: list[str],
    fallback_models: list[str],
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Build a payload-free request bound to one exact transient input.

    Source locators are represented only by count and a canonical hash. The input
    bytes are represented only by length and hash. Omitting ``idempotency_key``
    derives a stable key from every other request field.
    """

    locator_values = _validate_source_locators(source_locators)
    payload = _required_bytes(input_payload, "input_payload")
    fallback_policy = "explicit" if fallback_models else "none"
    request: dict[str, Any] = {
        "schema_version": MODEL_EXECUTOR_REQUEST_SCHEMA_VERSION,
        "task": {
            "kind": task_kind,
            "unit_id": unit_id,
            "source_locator_count": len(locator_values),
            "source_locators_sha256": _sha256_bytes(
                _canonical_json_bytes(locator_values)
            ),
        },
        "requested_model": requested_model,
        "thinking": thinking,
        "authentication": {
            "control_plane": "openclaw",
            "required_class": "subscription_oauth",
            "api_key_allowed": False,
        },
        "prompt_template": {
            "id": prompt_template_id,
            "version": prompt_template_version,
        },
        "input": {
            "sha256": _sha256_bytes(payload),
            "bytes": len(payload),
        },
        "output_contract": {
            "schema_id": output_schema_id,
            "schema_version": output_schema_version,
        },
        "timeout_seconds": timeout_seconds,
        "retry": {
            "max_attempts": max_attempts,
            "retry_on": retry_on,
        },
        "fallback": {
            "policy": fallback_policy,
            "models": fallback_models,
        },
        "idempotency_key": idempotency_key or "pending",
    }
    if idempotency_key is None:
        request["idempotency_key"] = _derived_idempotency_key(request)
    return validate_model_executor_request(request)


def build_structure_work_unit_request(
    *,
    unit_id: str,
    source_locators: list[str],
    input_payload: bytes,
    requested_model: str,
    thinking: str,
    prompt_template_version: str,
    output_schema_id: str,
    output_schema_version: str,
    timeout_seconds: int = 180,
    fallback_models: list[str] | None = None,
    retry_on: list[str] | None = None,
) -> dict[str, Any]:
    """Map one structure work unit without inheriting a provider default."""

    fallbacks, retry_codes = _normalize_optional_fallback(
        fallback_models=fallback_models,
        retry_on=retry_on,
        label="structure",
    )
    return build_model_executor_request(
        task_kind="structure",
        unit_id=unit_id,
        source_locators=source_locators,
        requested_model=requested_model,
        thinking=thinking,
        prompt_template_id="structure",
        prompt_template_version=prompt_template_version,
        input_payload=input_payload,
        output_schema_id=output_schema_id,
        output_schema_version=output_schema_version,
        timeout_seconds=timeout_seconds,
        max_attempts=1 + len(fallbacks),
        retry_on=retry_codes,
        fallback_models=fallbacks,
    )


def build_summary_work_unit_request(
    *,
    execution_plan: dict[str, Any],
    work_unit: dict[str, Any],
    input_payload: bytes,
    output_schema_id: str,
    output_schema_version: str,
    requested_model: str | None = None,
    thinking: str | None = None,
    timeout_seconds: int = 180,
    fallback_models: list[str] | None = None,
    retry_on: list[str] | None = None,
) -> dict[str, Any]:
    """Map one prepared summary work unit onto the executor contract.

    The no-call execution plan remains the source of the logical profile and
    prompt version. A per-job model override is explicit and allow-listed; it
    never mutates the profile bundle or inherits a package/provider default.
    """

    if not isinstance(execution_plan, dict):
        raise MillefeuilleContractError("summary execution plan must be an object")
    profile = _safe_id(execution_plan.get("profile"), "execution_plan.profile")
    stage = _required_string(execution_plan.get("stage"), "execution_plan.stage")
    if stage not in SUMMARY_MODEL_STAGES:
        raise MillefeuilleContractError("summary execution plan stage is unsupported")
    canonical_plan = build_summary_execution_plan(profile=profile, stage=stage)
    if execution_plan != canonical_plan:
        raise MillefeuilleContractError("summary execution plan drift")

    unit = _required_object(work_unit, "summary work unit")
    _require_exact_fields(
        unit,
        frozenset({"unit_id", "source_locators"}),
        "summary work unit",
    )
    unit_id = _safe_id(unit.get("unit_id"), "summary work unit unit_id")
    source_locators = _validate_source_locators(unit.get("source_locators"))
    parameters = _required_object(
        canonical_plan.get("requested_parameters"),
        "execution_plan.requested_parameters",
    )
    prompt_version = _safe_id(
        parameters.get("prompt_version"),
        "execution_plan prompt_version",
    )
    selected_model = (
        canonical_plan["requested_model"]
        if requested_model is None
        else requested_model
    )
    selected_thinking = (
        parameters.get("reasoning_effort") if thinking is None else thinking
    )
    fallbacks, retry_codes = _normalize_optional_fallback(
        fallback_models=fallback_models,
        retry_on=retry_on,
        label="summary",
    )
    return build_model_executor_request(
        task_kind=stage,
        unit_id=unit_id,
        source_locators=source_locators,
        requested_model=selected_model,
        thinking=selected_thinking,
        prompt_template_id=stage.replace("_", "-"),
        prompt_template_version=prompt_version,
        input_payload=input_payload,
        output_schema_id=output_schema_id,
        output_schema_version=output_schema_version,
        timeout_seconds=timeout_seconds,
        max_attempts=1 + len(fallbacks),
        retry_on=retry_codes,
        fallback_models=fallbacks,
    )


def validate_model_executor_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one executor request without reading credentials."""

    request = _required_object(payload, "model executor request")
    _require_exact_fields(request, _REQUEST_FIELDS, "model executor request")
    if request.get("schema_version") != MODEL_EXECUTOR_REQUEST_SCHEMA_VERSION:
        raise MillefeuilleContractError("model executor request schema is unsupported")

    task = _required_object(request.get("task"), "task")
    _require_exact_fields(task, _TASK_FIELDS, "task")
    kind = _required_string(task.get("kind"), "task.kind")
    if kind not in ALLOWED_EXECUTOR_TASKS:
        raise MillefeuilleContractError("task.kind is unsupported")
    unit_id = _safe_id(task.get("unit_id"), "task.unit_id")
    locator_count = _bounded_integer(
        task.get("source_locator_count"),
        "task.source_locator_count",
        minimum=1,
        maximum=10000,
    )
    locator_hash = _sha256(task.get("source_locators_sha256"), "task hash")

    requested_model = _allowed_model(request.get("requested_model"))
    thinking = _required_string(request.get("thinking"), "thinking")
    if thinking != REQUIRED_EXECUTOR_THINKING:
        raise MillefeuilleContractError("thinking must be xhigh")

    authentication = _required_object(request.get("authentication"), "authentication")
    _require_exact_fields(authentication, _AUTH_REQUEST_FIELDS, "authentication")
    if authentication != {
        "control_plane": "openclaw",
        "required_class": "subscription_oauth",
        "api_key_allowed": False,
    }:
        raise MillefeuilleContractError(
            "authentication must require OpenClaw subscription OAuth"
        )

    prompt_template = _required_object(
        request.get("prompt_template"), "prompt_template"
    )
    _require_exact_fields(prompt_template, _TEMPLATE_FIELDS, "prompt_template")
    template_id = _safe_id(prompt_template.get("id"), "prompt_template.id")
    template_version = _safe_id(
        prompt_template.get("version"), "prompt_template.version"
    )

    input_binding = _validate_hashed_payload(request.get("input"), "input")
    output_contract = _required_object(
        request.get("output_contract"), "output_contract"
    )
    _require_exact_fields(
        output_contract, _OUTPUT_CONTRACT_FIELDS, "output_contract"
    )
    output_schema_id = _safe_schema_id(
        output_contract.get("schema_id"), "output_contract.schema_id"
    )
    output_schema_version = _safe_id(
        output_contract.get("schema_version"), "output_contract.schema_version"
    )

    timeout_seconds = _bounded_integer(
        request.get("timeout_seconds"),
        "timeout_seconds",
        minimum=1,
        maximum=3600,
    )
    retry = _required_object(request.get("retry"), "retry")
    _require_exact_fields(retry, _RETRY_FIELDS, "retry")
    max_attempts = _bounded_integer(
        retry.get("max_attempts"), "retry.max_attempts", minimum=1, maximum=8
    )
    retry_on = _validate_retry_codes(retry.get("retry_on"))

    fallback = _required_object(request.get("fallback"), "fallback")
    _require_exact_fields(fallback, _FALLBACK_REQUEST_FIELDS, "fallback")
    policy = fallback.get("policy")
    models = fallback.get("models")
    if not isinstance(models, list):
        raise MillefeuilleContractError("fallback.models must be a list")
    normalized_models = [_allowed_model(model) for model in models]
    if len(normalized_models) != len(set(normalized_models)):
        raise MillefeuilleContractError("fallback.models must be unique")
    if requested_model in normalized_models:
        raise MillefeuilleContractError(
            "fallback.models must not repeat requested_model"
        )
    if policy == "none":
        if normalized_models or max_attempts != 1 or retry_on:
            raise MillefeuilleContractError(
                "no-fallback requests require one attempt and no retry codes"
            )
    elif policy == "explicit":
        if not normalized_models:
            raise MillefeuilleContractError(
                "explicit fallback requires at least one model"
            )
        if max_attempts != 1 + len(normalized_models):
            raise MillefeuilleContractError(
                "retry.max_attempts must match the explicit model chain"
            )
        if not retry_on:
            raise MillefeuilleContractError(
                "explicit fallback requires retry failure codes"
            )
    else:
        raise MillefeuilleContractError("fallback.policy is unsupported")

    idempotency_key = _safe_id(request.get("idempotency_key"), "idempotency_key")
    normalized = {
        "schema_version": MODEL_EXECUTOR_REQUEST_SCHEMA_VERSION,
        "task": {
            "kind": kind,
            "unit_id": unit_id,
            "source_locator_count": locator_count,
            "source_locators_sha256": locator_hash,
        },
        "requested_model": requested_model,
        "thinking": thinking,
        "authentication": dict(authentication),
        "prompt_template": {"id": template_id, "version": template_version},
        "input": input_binding,
        "output_contract": {
            "schema_id": output_schema_id,
            "schema_version": output_schema_version,
        },
        "timeout_seconds": timeout_seconds,
        "retry": {"max_attempts": max_attempts, "retry_on": retry_on},
        "fallback": {"policy": policy, "models": normalized_models},
        "idempotency_key": idempotency_key,
    }
    if idempotency_key != _derived_idempotency_key(normalized):
        raise MillefeuilleContractError(
            "model executor request idempotency key drift"
        )
    return normalized


def model_executor_request_sha256(request: dict[str, Any]) -> str:
    """Return the canonical identity of a validated executor request."""

    normalized = validate_model_executor_request(request)
    return _sha256_bytes(_canonical_json_bytes(normalized))


def verify_model_executor_input(request: dict[str, Any], payload: bytes) -> None:
    """Fail closed unless transient provider input matches its request binding."""

    normalized = validate_model_executor_request(request)
    input_payload = _required_bytes(payload, "input_payload")
    if (
        len(input_payload) != normalized["input"]["bytes"]
        or _sha256_bytes(input_payload) != normalized["input"]["sha256"]
    ):
        raise MillefeuilleContractError("model executor input drift")


def materialize_model_executor_result(
    *,
    request: dict[str, Any],
    status: str,
    actual_model: str | None,
    auth_profile_ref: str | None,
    started_at: str,
    completed_at: str,
    attempts: list[dict[str, Any]],
    fallback_reason: str | None,
    schema_validation_status: str,
    output_sha256: str | None,
    output_bytes: int | None,
    usage: dict[str, int] | None,
    cost_micro_usd: int | None,
) -> dict[str, Any]:
    """Create a payload-free result and validate it against its exact request."""

    normalized_request = validate_model_executor_request(request)
    result = {
        "schema_version": MODEL_EXECUTOR_RESULT_SCHEMA_VERSION,
        "status": status,
        "request_sha256": model_executor_request_sha256(normalized_request),
        "idempotency_key": normalized_request["idempotency_key"],
        "requested_model": normalized_request["requested_model"],
        "actual_model": actual_model,
        "thinking": normalized_request["thinking"],
        "authentication": {
            "class": "subscription_oauth" if auth_profile_ref is not None else None,
            "profile_ref": auth_profile_ref,
        },
        "started_at": started_at,
        "completed_at": completed_at,
        "attempts": attempts,
        "fallback": {
            "used": len(attempts) > 1,
            "reason": fallback_reason,
        },
        "schema_validation": {
            "status": schema_validation_status,
            "schema_id": normalized_request["output_contract"]["schema_id"],
            "schema_version": normalized_request["output_contract"][
                "schema_version"
            ],
        },
        "output": {"sha256": output_sha256, "bytes": output_bytes},
        "usage": usage,
        "cost": (
            {"currency": "USD", "micro_usd": cost_micro_usd}
            if cost_micro_usd is not None
            else None
        ),
    }
    return validate_model_executor_result(
        request=normalized_request,
        result=result,
    )


def validate_model_executor_result(
    *, request: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """Validate execution evidence and reject hidden routing or auth drift."""

    normalized_request = validate_model_executor_request(request)
    payload = _required_object(result, "model executor result")
    _require_exact_fields(payload, _RESULT_FIELDS, "model executor result")
    if payload.get("schema_version") != MODEL_EXECUTOR_RESULT_SCHEMA_VERSION:
        raise MillefeuilleContractError("model executor result schema is unsupported")
    status = payload.get("status")
    if status not in {"succeeded", "failed"}:
        raise MillefeuilleContractError("model executor result status is unsupported")
    if payload.get("request_sha256") != model_executor_request_sha256(
        normalized_request
    ):
        raise MillefeuilleContractError("model executor result request drift")
    if payload.get("idempotency_key") != normalized_request["idempotency_key"]:
        raise MillefeuilleContractError("model executor result idempotency drift")
    if payload.get("requested_model") != normalized_request["requested_model"]:
        raise MillefeuilleContractError("model executor result requested model drift")
    if payload.get("thinking") != normalized_request["thinking"]:
        raise MillefeuilleContractError("model executor result thinking drift")

    started_at = _timestamp(payload.get("started_at"), "started_at")
    completed_at = _timestamp(payload.get("completed_at"), "completed_at")
    if _parse_timestamp(completed_at) < _parse_timestamp(started_at):
        raise MillefeuilleContractError("model executor result time order is invalid")

    chain = [
        normalized_request["requested_model"],
        *normalized_request["fallback"]["models"],
    ]
    attempts = _validate_attempts(payload.get("attempts"), chain=chain)
    if len(attempts) > normalized_request["retry"]["max_attempts"]:
        raise MillefeuilleContractError("model executor attempt limit exceeded")
    if (
        _parse_timestamp(attempts[0]["started_at"])
        < _parse_timestamp(started_at)
        or _parse_timestamp(attempts[-1]["completed_at"])
        > _parse_timestamp(completed_at)
    ):
        raise MillefeuilleContractError(
            "model executor attempts exceed the result time bounds"
        )
    for previous, following in zip(attempts, attempts[1:], strict=False):
        if _parse_timestamp(following["started_at"]) < _parse_timestamp(
            previous["completed_at"]
        ):
            raise MillefeuilleContractError("model executor attempts overlap")
        if (
            previous["status"] != "failed"
            or previous["failure_code"]
            not in normalized_request["retry"]["retry_on"]
        ):
            raise MillefeuilleContractError(
                "model executor fallback transition was not authorized"
            )
        if following["model"] == previous["model"]:
            raise MillefeuilleContractError(
                "model executor model attempts must not repeat"
            )

    fallback = _required_object(payload.get("fallback"), "result fallback")
    _require_exact_fields(fallback, _FALLBACK_RESULT_FIELDS, "result fallback")
    used = fallback.get("used")
    if not isinstance(used, bool) or used is not (len(attempts) > 1):
        raise MillefeuilleContractError("model executor fallback usage drift")
    reason = fallback.get("reason")
    if used:
        if reason != attempts[0]["failure_code"]:
            raise MillefeuilleContractError("model executor fallback reason drift")
    elif reason is not None:
        raise MillefeuilleContractError(
            "model executor fallback reason must be null when unused"
        )

    authentication = _validate_result_authentication(payload.get("authentication"))
    final_attempt_authentication = {
        "class": attempts[-1]["auth_class"],
        "profile_ref": attempts[-1]["auth_profile_ref"],
    }
    if authentication != final_attempt_authentication:
        raise MillefeuilleContractError("model executor final auth evidence drift")
    schema_validation = _validate_schema_validation(
        payload.get("schema_validation"), normalized_request
    )
    output = _validate_result_output(payload.get("output"))
    usage = _validate_usage(payload.get("usage"))
    cost = _validate_cost(payload.get("cost"))

    actual_model = payload.get("actual_model")
    succeeded_attempts = [
        attempt for attempt in attempts if attempt["status"] == "succeeded"
    ]
    if status == "succeeded":
        if len(succeeded_attempts) != 1 or succeeded_attempts[0] is not attempts[-1]:
            raise MillefeuilleContractError(
                "successful execution requires one final successful attempt"
            )
        actual_model = _allowed_model(actual_model)
        if actual_model != attempts[-1]["actual_model"]:
            raise MillefeuilleContractError("model executor actual model drift")
        if actual_model != attempts[-1]["model"]:
            raise MillefeuilleContractError("provider returned an unexpected model")
        if authentication["class"] != "subscription_oauth":
            raise MillefeuilleContractError(
                "successful execution requires subscription OAuth"
            )
        if schema_validation["status"] != "passed":
            raise MillefeuilleContractError(
                "successful execution requires passed schema validation"
            )
        if output["sha256"] is None or output["bytes"] is None:
            raise MillefeuilleContractError(
                "successful execution requires an output binding"
            )
    else:
        if succeeded_attempts:
            raise MillefeuilleContractError(
                "failed execution must not include a successful attempt"
            )
        if actual_model is not None:
            raise MillefeuilleContractError(
                "failed execution actual_model must be null"
            )
        if output != {"sha256": None, "bytes": None}:
            raise MillefeuilleContractError(
                "failed execution must not claim an output binding"
            )
        if schema_validation["status"] == "passed":
            raise MillefeuilleContractError(
                "failed execution must not claim passed schema validation"
            )

    return {
        "schema_version": MODEL_EXECUTOR_RESULT_SCHEMA_VERSION,
        "status": status,
        "request_sha256": payload["request_sha256"],
        "idempotency_key": payload["idempotency_key"],
        "requested_model": payload["requested_model"],
        "actual_model": actual_model,
        "thinking": payload["thinking"],
        "authentication": authentication,
        "started_at": started_at,
        "completed_at": completed_at,
        "attempts": attempts,
        "fallback": {"used": used, "reason": reason},
        "schema_validation": schema_validation,
        "output": output,
        "usage": usage,
        "cost": cost,
    }


def _validate_attempts(value: object, *, chain: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise MillefeuilleContractError("attempts must be a non-empty list")
    if len(value) > len(chain):
        raise MillefeuilleContractError("attempts exceed the declared model chain")
    normalized: list[dict[str, Any]] = []
    for index, raw_attempt in enumerate(value, start=1):
        attempt = _required_object(raw_attempt, f"attempts[{index}]")
        _require_exact_fields(attempt, _ATTEMPT_FIELDS, f"attempts[{index}]")
        if attempt.get("sequence") != index:
            raise MillefeuilleContractError("attempt sequence is not contiguous")
        model = _allowed_model(attempt.get("model"))
        if model != chain[index - 1]:
            raise MillefeuilleContractError("attempt model order drift")
        attempt_status = attempt.get("status")
        if attempt_status not in {"succeeded", "failed"}:
            raise MillefeuilleContractError("attempt status is unsupported")
        started_at = _timestamp(attempt.get("started_at"), "attempt started_at")
        completed_at = _timestamp(
            attempt.get("completed_at"), "attempt completed_at"
        )
        if _parse_timestamp(completed_at) < _parse_timestamp(started_at):
            raise MillefeuilleContractError("attempt time order is invalid")
        actual_model = attempt.get("actual_model")
        if actual_model is not None:
            actual_model = _safe_schema_id(actual_model, "attempt actual_model")
        auth_class = attempt.get("auth_class")
        profile_ref = attempt.get("auth_profile_ref")
        if auth_class is None:
            if profile_ref is not None:
                raise MillefeuilleContractError(
                    "attempt auth profile requires an auth class"
                )
        elif auth_class == "subscription_oauth":
            profile_ref = _profile_ref(profile_ref)
        else:
            raise MillefeuilleContractError(
                "attempt auth class must be subscription OAuth"
            )
        failure_code = attempt.get("failure_code")
        if attempt_status == "succeeded":
            if actual_model != model:
                raise MillefeuilleContractError(
                    "successful attempt actual model mismatch"
                )
            if auth_class != "subscription_oauth":
                raise MillefeuilleContractError(
                    "successful attempt requires subscription OAuth"
                )
            if failure_code is not None:
                raise MillefeuilleContractError(
                    "successful attempt failure_code must be null"
                )
        else:
            if failure_code not in ATTEMPT_FAILURE_CODES:
                raise MillefeuilleContractError(
                    "failed attempt requires a supported failure_code"
                )
        normalized.append(
            {
                "sequence": index,
                "model": model,
                "actual_model": actual_model,
                "status": attempt_status,
                "started_at": started_at,
                "completed_at": completed_at,
                "auth_class": auth_class,
                "auth_profile_ref": profile_ref,
                "failure_code": failure_code,
            }
        )
    return normalized


def _validate_result_authentication(value: object) -> dict[str, str | None]:
    authentication = _required_object(value, "result authentication")
    _require_exact_fields(
        authentication, _AUTH_RESULT_FIELDS, "result authentication"
    )
    auth_class = authentication.get("class")
    profile_ref = authentication.get("profile_ref")
    if auth_class is None:
        if profile_ref is not None:
            raise MillefeuilleContractError(
                "result auth profile requires an auth class"
            )
        return {"class": None, "profile_ref": None}
    if auth_class != "subscription_oauth":
        raise MillefeuilleContractError(
            "result auth class must be subscription OAuth"
        )
    return {"class": auth_class, "profile_ref": _profile_ref(profile_ref)}


def _validate_schema_validation(
    value: object, request: dict[str, Any]
) -> dict[str, str]:
    validation = _required_object(value, "schema_validation")
    _require_exact_fields(
        validation, _SCHEMA_VALIDATION_FIELDS, "schema_validation"
    )
    status = validation.get("status")
    if status not in {"passed", "failed", "not_run"}:
        raise MillefeuilleContractError("schema validation status is unsupported")
    if validation.get("schema_id") != request["output_contract"]["schema_id"]:
        raise MillefeuilleContractError("output schema identity drift")
    if (
        validation.get("schema_version")
        != request["output_contract"]["schema_version"]
    ):
        raise MillefeuilleContractError("output schema version drift")
    return {
        "status": status,
        "schema_id": validation["schema_id"],
        "schema_version": validation["schema_version"],
    }


def _validate_result_output(value: object) -> dict[str, str | int | None]:
    output = _required_object(value, "result output")
    _require_exact_fields(output, _OUTPUT_RESULT_FIELDS, "result output")
    sha256 = output.get("sha256")
    byte_count = output.get("bytes")
    if sha256 is None and byte_count is None:
        return {"sha256": None, "bytes": None}
    if sha256 is None or byte_count is None:
        raise MillefeuilleContractError("output hash and bytes must be set together")
    return {
        "sha256": _sha256(sha256, "output hash"),
        "bytes": _bounded_integer(
            byte_count, "output bytes", minimum=1, maximum=_JSON_SAFE_INTEGER_MAX
        ),
    }


def _validate_usage(value: object) -> dict[str, int] | None:
    if value is None:
        return None
    usage = _required_object(value, "usage")
    _require_exact_fields(usage, _USAGE_FIELDS, "usage")
    normalized = {
        field: _bounded_integer(
            usage.get(field),
            f"usage.{field}",
            minimum=0,
            maximum=_JSON_SAFE_INTEGER_MAX,
        )
        for field in ("input_tokens", "output_tokens", "total_tokens")
    }
    if normalized["total_tokens"] != (
        normalized["input_tokens"] + normalized["output_tokens"]
    ):
        raise MillefeuilleContractError("usage token total is invalid")
    return normalized


def _validate_cost(value: object) -> dict[str, str | int] | None:
    if value is None:
        return None
    cost = _required_object(value, "cost")
    _require_exact_fields(cost, _COST_FIELDS, "cost")
    if cost.get("currency") != "USD":
        raise MillefeuilleContractError("cost currency must be USD")
    return {
        "currency": "USD",
        "micro_usd": _bounded_integer(
            cost.get("micro_usd"),
            "cost.micro_usd",
            minimum=0,
            maximum=_JSON_SAFE_INTEGER_MAX,
        ),
    }


def _validate_hashed_payload(value: object, label: str) -> dict[str, str | int]:
    payload = _required_object(value, label)
    _require_exact_fields(payload, _HASHED_PAYLOAD_FIELDS, label)
    return {
        "sha256": _sha256(payload.get("sha256"), f"{label} hash"),
        "bytes": _bounded_integer(
            payload.get("bytes"),
            f"{label} bytes",
            minimum=1,
            maximum=_JSON_SAFE_INTEGER_MAX,
        ),
    }


def _normalize_optional_fallback(
    *,
    fallback_models: list[str] | None,
    retry_on: list[str] | None,
    label: str,
) -> tuple[list[str], list[str]]:
    fallbacks = [] if fallback_models is None else fallback_models
    retry_codes = [] if retry_on is None else retry_on
    if fallbacks and not retry_codes:
        raise MillefeuilleContractError(
            f"explicit {label} fallback requires retry failure codes"
        )
    if not fallbacks and retry_codes:
        raise MillefeuilleContractError(
            f"{label} retry codes require an explicit fallback model"
        )
    return fallbacks, retry_codes


def _validate_source_locators(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise MillefeuilleContractError("source_locators must be a non-empty list")
    locators: list[str] = []
    for locator in value:
        text = _required_string(locator, "source locator")
        if len(text) > 500 or any(character in text for character in "\r\n\0"):
            raise MillefeuilleContractError("source locator is unsafe")
        locators.append(text)
    if len(locators) != len(set(locators)):
        raise MillefeuilleContractError("source_locators must be unique")
    return locators


def _validate_retry_codes(value: object) -> list[str]:
    if not isinstance(value, list):
        raise MillefeuilleContractError("retry.retry_on must be a list")
    codes = [_required_string(item, "retry code") for item in value]
    if any(code not in RETRYABLE_FAILURE_CODES for code in codes):
        raise MillefeuilleContractError("retry.retry_on contains an unsupported code")
    if codes != sorted(set(codes)):
        raise MillefeuilleContractError(
            "retry.retry_on must be sorted and unique"
        )
    return codes


def _allowed_model(value: object) -> str:
    model = _required_string(value, "model")
    if model not in ALLOWED_EXECUTOR_MODELS:
        raise MillefeuilleContractError("model is not allowed")
    return model


def _required_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MillefeuilleContractError(f"{label} must be an object")
    return value


def _require_exact_fields(
    payload: dict[str, Any], expected: frozenset[str], label: str
) -> None:
    if set(payload) != expected:
        raise MillefeuilleContractError(f"{label} fields do not match the contract")


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MillefeuilleContractError(f"{label} must be a non-empty unpadded string")
    return value


def _safe_id(value: object, label: str) -> str:
    text = _required_string(value, label)
    if not _SAFE_ID.fullmatch(text):
        raise MillefeuilleContractError(f"{label} is not a safe identifier")
    return text


def _safe_schema_id(value: object, label: str) -> str:
    text = _required_string(value, label)
    if not _SAFE_SCHEMA_ID.fullmatch(text):
        raise MillefeuilleContractError(f"{label} is not safe")
    return text


def _profile_ref(value: object) -> str:
    text = _required_string(value, "auth profile reference")
    if not _SAFE_PROFILE_REF.fullmatch(text):
        raise MillefeuilleContractError("auth profile reference is not safe")
    return text


def _sha256(value: object, label: str) -> str:
    text = _required_string(value, label)
    if not _SHA256.fullmatch(text):
        raise MillefeuilleContractError(f"{label} must be a lowercase SHA-256")
    return text


def _timestamp(value: object, label: str) -> str:
    text = _required_string(value, label)
    if not _TIMESTAMP.fullmatch(text):
        raise MillefeuilleContractError(f"{label} must be a UTC timestamp")
    _parse_timestamp(text)
    return text


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise MillefeuilleContractError("timestamp is invalid") from exc


def _bounded_integer(
    value: object, label: str, *, minimum: int, maximum: int
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise MillefeuilleContractError(f"{label} is outside its allowed range")
    return value


def _required_bytes(value: object, label: str) -> bytes:
    if not isinstance(value, bytes) or not value:
        raise MillefeuilleContractError(f"{label} must be non-empty bytes")
    return value


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _derived_idempotency_key(request: dict[str, Any]) -> str:
    identity_payload = dict(request)
    identity_payload.pop("idempotency_key", None)
    digest = hashlib.sha256(_canonical_json_bytes(identity_payload)).hexdigest()
    return f"mfexec-{digest[:32]}"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
