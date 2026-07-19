"""Deterministic, no-call execution plans for model-backed summary stages."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE
from millefeuille.domain.secure_io import (
    load_json_object_no_follow,
    write_new_text_no_follow,
)

MODEL_EXECUTION_PLAN_SCHEMA_VERSION = "millefeuille-model-execution-plan/v0.1"
MODEL_EXECUTION_EVIDENCE_SCHEMA_VERSION = "millefeuille-model-execution-evidence/v0.1"
MODEL_PROVENANCE_SCHEMA_VERSION = "millefeuille-model-provenance/v0.1"
SUMMARY_MODEL_STAGES = (
    "summarize_page",
    "summarize_section",
    "summarize_full_paper",
)

_PROVENANCE_REQUIRED_FIELDS = (
    "profile",
    "stage",
    "requested_model",
    "resolved_model",
    "provider",
    "backend",
    "reasoning_effort",
    "fast_mode",
    "prompt_version",
    "input_refs",
    "output_refs",
    "usage",
    "quality_warnings",
)
_PARAMETER_FIELDS = (
    "temperature",
    "reasoning_effort",
    "fast_mode",
    "prompt_version",
    "record_usage",
)
_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_FAST_MODES = {"off", "on", "auto"}
_PLAN_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "profile",
        "stage",
        "backend",
        "provider",
        "requested_model",
        "requested_parameters",
        "authentication",
        "fallback",
        "execution",
        "provenance_contract",
    }
)
_AUTHENTICATION_FIELDS = frozenset({"lane", "credential_lookup_performed"})
_FALLBACK_FIELDS = frozenset({"policy", "model"})
_EXECUTION_FIELDS = frozenset(
    {
        "provider_call_permitted",
        "provider_call_performed",
        "fixture_only",
        "ready_for_approved_live_execution",
        "blockers",
    }
)
_PROVENANCE_CONTRACT_FIELDS = frozenset(
    {"schema_version", "required_fields", "actual_values_recorded"}
)
_EXECUTION_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        *_PROVENANCE_REQUIRED_FIELDS,
        "fallback_used",
    }
)
_PROVENANCE_RECORD_FIELDS = frozenset({"schema_version", *_PROVENANCE_REQUIRED_FIELDS})
_USAGE_FIELDS = frozenset({"input_tokens", "output_tokens", "total_tokens"})
_WARNING_FIELDS = frozenset({"code", "severity", "ref"})
_WARNING_SEVERITIES = frozenset({"info", "warning", "error"})
_RUN_PACKAGE_MARKERS = frozenset({"artifact-index.json", "stage-manifest.json"})
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\Z")
_SAFE_WARNING_CODE = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_FORBIDDEN_EVIDENCE_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "credential",
        "credentials",
        "headers",
        "paper_text",
        "payload",
        "prompt",
        "provider_payload",
        "raw_response",
        "secret",
        "text",
    }
)
_FORBIDDEN_EVIDENCE_MARKERS = (
    re.compile(r"Authorization:\s*(Bearer|Basic)", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}", re.IGNORECASE),
    re.compile(r"data:application/pdf", re.IGNORECASE),
    re.compile(r"%PDF-"),
)


def build_summary_execution_plan(
    *,
    profile: str,
    stage: str,
    bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one summary profile without calling a model or reading credentials."""

    profile_name = _required_string(profile, "profile")
    stage_name = _required_string(stage, "stage")
    if stage_name not in SUMMARY_MODEL_STAGES:
        raise MillefeuilleContractError(
            "summary model execution plan stage must be one of: "
            + ", ".join(SUMMARY_MODEL_STAGES)
        )

    selected_bundle = (
        DEFAULT_MODEL_PROFILE_BUNDLE if bundle is None else bundle
    )
    profiles = selected_bundle.get("profiles")
    if not isinstance(profiles, dict):
        raise MillefeuilleContractError(
            "model profile bundle profiles must be an object"
        )
    selected_profile = profiles.get(profile_name)
    if not isinstance(selected_profile, dict):
        raise MillefeuilleContractError(f"unknown model profile {profile_name!r}")
    stage_config = selected_profile.get(stage_name)
    if not isinstance(stage_config, dict):
        raise MillefeuilleContractError(
            f"model profile {profile_name!r} has no stage {stage_name!r}"
        )

    requested_model = _required_string(stage_config.get("model"), "model")
    provider = _required_string(stage_config.get("provider"), "provider")
    backend = _required_string(stage_config.get("backend"), "backend")
    auth_lane = _required_string(stage_config.get("auth_lane"), "auth_lane")
    _validate_execution_lane(
        provider=provider,
        backend=backend,
        auth_lane=auth_lane,
    )
    fallback_policy = _required_string(
        stage_config.get("fallback_policy", "none"),
        "fallback_policy",
    )
    if fallback_policy not in {"none", "explicit"}:
        raise MillefeuilleContractError(
            "fallback_policy must be 'none' or 'explicit'"
        )
    fallback_model = stage_config.get("fallback_model")
    if fallback_policy == "none" and fallback_model is not None:
        raise MillefeuilleContractError(
            "fallback_model must be absent when fallback_policy is 'none'"
        )
    if fallback_policy == "explicit":
        fallback_model = _required_string(fallback_model, "fallback_model")

    _validate_requested_parameters(stage_config)
    requested_parameters = {
        name: stage_config[name]
        for name in _PARAMETER_FIELDS
        if name in stage_config
    }
    fixture_only = provider == "none"
    blockers = [] if fixture_only else [
        "provider call requires a separately approved live execution path",
        "approved-live model execution is not implemented by this command",
    ]

    return {
        "schema_version": MODEL_EXECUTION_PLAN_SCHEMA_VERSION,
        "status": "planned-offline",
        "profile": profile_name,
        "stage": stage_name,
        "backend": backend,
        "provider": provider,
        "requested_model": requested_model,
        "requested_parameters": requested_parameters,
        "authentication": {
            "lane": auth_lane,
            "credential_lookup_performed": False,
        },
        "fallback": {
            "policy": fallback_policy,
            "model": fallback_model,
        },
        "execution": {
            "provider_call_permitted": False,
            "provider_call_performed": False,
            "fixture_only": fixture_only,
            "ready_for_approved_live_execution": False,
            "blockers": blockers,
        },
        "provenance_contract": {
            "schema_version": MODEL_PROVENANCE_SCHEMA_VERSION,
            "required_fields": list(_PROVENANCE_REQUIRED_FIELDS),
            "actual_values_recorded": False,
        },
    }


def materialize_model_provenance_record(
    *,
    execution_plan: dict[str, Any],
    execution_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Validate execution evidence and return one safe provenance record.

    This function is deliberately a no-call materializer. It accepts only the
    control and accounting fields needed for downstream provenance and rejects
    prompts, provider payloads, private paper text, credentials, and unknown
    evidence fields before returning a record.
    """

    _reject_forbidden_evidence_payload(execution_plan, "execution plan")
    _reject_forbidden_evidence_payload(execution_evidence, "execution evidence")
    _require_exact_fields(
        execution_evidence,
        _EXECUTION_EVIDENCE_FIELDS,
        "model execution evidence",
    )
    _validate_plan_shape(execution_plan)
    _validate_canonical_plan(execution_plan)
    if execution_evidence["schema_version"] != MODEL_EXECUTION_EVIDENCE_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "model execution evidence schema_version must be "
            f"{MODEL_EXECUTION_EVIDENCE_SCHEMA_VERSION!r}"
        )

    profile = _required_string(execution_evidence.get("profile"), "profile")
    stage = _required_string(execution_evidence.get("stage"), "stage")
    requested_model = _required_string(
        execution_evidence.get("requested_model"),
        "requested_model",
    )
    resolved_model = _required_string(
        execution_evidence.get("resolved_model"),
        "resolved_model",
    )
    provider = _required_string(execution_evidence.get("provider"), "provider")
    backend = _required_string(execution_evidence.get("backend"), "backend")
    reasoning_effort = _nullable_control(
        execution_evidence.get("reasoning_effort"),
        "reasoning_effort",
        _REASONING_EFFORTS,
    )
    fast_mode = _nullable_control(
        execution_evidence.get("fast_mode"),
        "fast_mode",
        _FAST_MODES,
    )
    prompt_version = _nullable_required_string(
        execution_evidence.get("prompt_version"),
        "prompt_version",
    )
    fallback_used = execution_evidence.get("fallback_used")
    if not isinstance(fallback_used, bool):
        raise MillefeuilleContractError("fallback_used must be a boolean")

    _require_equal(profile, execution_plan.get("profile"), "profile")
    _require_equal(stage, execution_plan.get("stage"), "stage")
    _require_equal(
        requested_model,
        execution_plan.get("requested_model"),
        "requested_model",
    )
    _require_equal(provider, execution_plan.get("provider"), "provider")
    _require_equal(backend, execution_plan.get("backend"), "backend")
    requested_parameters = _require_object(
        execution_plan.get("requested_parameters"),
        "requested_parameters",
    )
    _require_equal(
        reasoning_effort,
        requested_parameters.get("reasoning_effort"),
        "reasoning_effort",
    )
    _require_equal(fast_mode, requested_parameters.get("fast_mode"), "fast_mode")
    _require_equal(
        prompt_version,
        requested_parameters.get("prompt_version"),
        "prompt_version",
    )
    _validate_fallback_resolution(
        execution_plan=execution_plan,
        requested_model=requested_model,
        resolved_model=resolved_model,
        fallback_used=fallback_used,
    )

    input_refs = _validate_refs(execution_evidence.get("input_refs"), "input_refs")
    output_refs = _validate_refs(execution_evidence.get("output_refs"), "output_refs")
    usage = _validate_usage(execution_evidence.get("usage"))
    quality_warnings = _validate_quality_warnings(
        execution_evidence.get("quality_warnings")
    )

    record = {
        "schema_version": MODEL_PROVENANCE_SCHEMA_VERSION,
        "profile": profile,
        "stage": stage,
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "provider": provider,
        "backend": backend,
        "reasoning_effort": reasoning_effort,
        "fast_mode": fast_mode,
        "prompt_version": prompt_version,
        "input_refs": input_refs,
        "output_refs": output_refs,
        "usage": usage,
        "quality_warnings": quality_warnings,
    }
    _require_exact_fields(record, _PROVENANCE_RECORD_FIELDS, "model provenance record")
    return record


def materialize_model_provenance_record_from_files(
    *,
    execution_plan_path: str | Path,
    execution_evidence_path: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load, validate, and optionally write one model provenance record."""

    execution_plan = load_json_object_no_follow(
        execution_plan_path,
        "model execution plan",
    )
    execution_evidence = load_json_object_no_follow(
        execution_evidence_path,
        "model execution evidence",
    )
    record = materialize_model_provenance_record(
        execution_plan=execution_plan,
        execution_evidence=execution_evidence,
    )
    if output_path is not None:
        write_new_text_no_follow(
            output_path,
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            "model provenance output",
            forbidden_ancestor_markers=_RUN_PACKAGE_MARKERS,
        )
    return record


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MillefeuilleContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _nullable_required_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field_name)


def _require_object(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MillefeuilleContractError(f"{field_name} must be an object")
    return value


def _require_exact_fields(
    payload: dict[str, Any],
    expected_fields: frozenset[str],
    label: str,
) -> None:
    actual_fields = set(payload)
    extra = sorted(actual_fields - expected_fields)
    missing = sorted(expected_fields - actual_fields)
    if extra:
        raise MillefeuilleContractError(
            f"{label} contains unknown fields: {', '.join(extra)}"
        )
    if missing:
        raise MillefeuilleContractError(
            f"{label} is missing required fields: {', '.join(missing)}"
        )


def _require_equal(actual: object, expected: object, field_name: str) -> None:
    if actual != expected:
        raise MillefeuilleContractError(
            f"model provenance {field_name} drift: expected {expected!r}, "
            f"got {actual!r}"
        )


def _nullable_control(
    value: object,
    field_name: str,
    allowed_values: frozenset[str],
) -> str | None:
    if value is None:
        return None
    text = _required_string(value, field_name)
    if text not in allowed_values:
        raise MillefeuilleContractError(f"{field_name} is unsupported")
    return text


def _validate_plan_shape(execution_plan: dict[str, Any]) -> None:
    _require_exact_fields(execution_plan, _PLAN_FIELDS, "model execution plan")
    if execution_plan.get("schema_version") != MODEL_EXECUTION_PLAN_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "model execution plan schema_version must be "
            f"{MODEL_EXECUTION_PLAN_SCHEMA_VERSION!r}"
        )
    if execution_plan.get("status") != "planned-offline":
        raise MillefeuilleContractError(
            "model execution plan status must be planned-offline"
        )
    _required_string(execution_plan.get("profile"), "profile")
    stage = _required_string(execution_plan.get("stage"), "stage")
    if stage not in SUMMARY_MODEL_STAGES:
        raise MillefeuilleContractError("model execution plan stage is unsupported")
    backend = _required_string(execution_plan.get("backend"), "backend")
    provider = _required_string(execution_plan.get("provider"), "provider")
    _required_string(execution_plan.get("requested_model"), "requested_model")

    requested_parameters = _require_object(
        execution_plan.get("requested_parameters"),
        "requested_parameters",
    )
    _require_allowed_and_required_fields(
        requested_parameters,
        allowed=frozenset(_PARAMETER_FIELDS),
        required=frozenset({"record_usage"}),
        label="requested_parameters",
    )
    _validate_requested_parameters(requested_parameters)

    authentication = _require_object(
        execution_plan.get("authentication"),
        "authentication",
    )
    _require_exact_fields(
        authentication,
        _AUTHENTICATION_FIELDS,
        "authentication",
    )
    auth_lane = _required_string(authentication.get("lane"), "authentication.lane")
    if authentication.get("credential_lookup_performed") is not False:
        raise MillefeuilleContractError(
            "model execution plan must not record credential lookup"
        )

    fallback = _require_object(execution_plan.get("fallback"), "fallback")
    _require_exact_fields(fallback, _FALLBACK_FIELDS, "fallback")
    fallback_policy = fallback.get("policy")
    fallback_model = fallback.get("model")
    if fallback_policy == "none":
        if fallback_model is not None:
            raise MillefeuilleContractError(
                "fallback model must be null when fallback policy is 'none'"
            )
    elif fallback_policy == "explicit":
        _required_string(fallback_model, "fallback.model")
    else:
        raise MillefeuilleContractError(
            "fallback policy must be 'none' or 'explicit'"
        )

    execution = _require_object(execution_plan.get("execution"), "execution")
    _require_exact_fields(execution, _EXECUTION_FIELDS, "execution")
    if execution.get("provider_call_permitted") is not False:
        raise MillefeuilleContractError(
            "model execution plan must not permit a provider call"
        )
    if execution.get("provider_call_performed") is not False:
        raise MillefeuilleContractError(
            "model execution plan must not record a provider call"
        )
    if execution.get("ready_for_approved_live_execution") is not False:
        raise MillefeuilleContractError(
            "model execution plan must not be ready for live execution"
        )
    fixture_only = execution.get("fixture_only")
    if not isinstance(fixture_only, bool):
        raise MillefeuilleContractError("execution.fixture_only must be a boolean")
    blockers = execution.get("blockers")
    if not isinstance(blockers, list):
        raise MillefeuilleContractError("execution.blockers must be a list")
    normalized_blockers = [
        _required_string(value, f"execution.blockers[{index}]")
        for index, value in enumerate(blockers, start=1)
    ]
    if len(normalized_blockers) != len(set(normalized_blockers)):
        raise MillefeuilleContractError("execution.blockers must be unique")

    _validate_execution_lane(
        provider=provider,
        backend=backend,
        auth_lane=auth_lane,
    )
    expected_fixture_only = provider == "none"
    if fixture_only is not expected_fixture_only:
        raise MillefeuilleContractError(
            "execution.fixture_only does not match the execution lane"
        )
    if expected_fixture_only and blockers:
        raise MillefeuilleContractError(
            "fixture execution must not declare live blockers"
        )
    if not expected_fixture_only and not blockers:
        raise MillefeuilleContractError(
            "live execution plans must declare at least one blocker"
        )

    provenance_contract = _require_object(
        execution_plan.get("provenance_contract"),
        "provenance_contract",
    )
    _require_exact_fields(
        provenance_contract,
        _PROVENANCE_CONTRACT_FIELDS,
        "provenance_contract",
    )
    if provenance_contract.get("schema_version") != MODEL_PROVENANCE_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "provenance_contract.schema_version is unsupported"
        )
    if provenance_contract.get("required_fields") != list(
        _PROVENANCE_REQUIRED_FIELDS
    ):
        raise MillefeuilleContractError(
            "provenance_contract.required_fields does not match the strict contract"
        )
    if provenance_contract.get("actual_values_recorded") is not False:
        raise MillefeuilleContractError(
            "provenance_contract must not claim actual values"
        )


def _validate_canonical_plan(execution_plan: dict[str, Any]) -> None:
    expected = build_summary_execution_plan(
        profile=_required_string(execution_plan.get("profile"), "profile"),
        stage=_required_string(execution_plan.get("stage"), "stage"),
    )
    if execution_plan != expected:
        raise MillefeuilleContractError(
            "model execution plan does not match the canonical bundled profile"
        )


def _require_allowed_and_required_fields(
    payload: dict[str, Any],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> None:
    actual = set(payload)
    extra = sorted(actual - allowed)
    missing = sorted(required - actual)
    if extra:
        raise MillefeuilleContractError(
            f"{label} contains unknown fields: {', '.join(extra)}"
        )
    if missing:
        raise MillefeuilleContractError(
            f"{label} is missing required fields: {', '.join(missing)}"
        )


def _validate_fallback_resolution(
    *,
    execution_plan: dict[str, Any],
    requested_model: str,
    resolved_model: str,
    fallback_used: bool,
) -> None:
    fallback = _require_object(execution_plan.get("fallback"), "fallback")
    policy = fallback.get("policy")
    fallback_model = fallback.get("model")
    if policy == "none":
        if fallback_used:
            raise MillefeuilleContractError(
                "fallback_used must be false when fallback policy is 'none'"
            )
        if fallback_model is not None:
            raise MillefeuilleContractError(
                "fallback model must be null when fallback policy is 'none'"
            )
        if resolved_model != requested_model:
            raise MillefeuilleContractError(
                "resolved_model must equal requested_model when fallback policy "
                "is 'none'"
            )
        return
    if policy != "explicit":
        raise MillefeuilleContractError("fallback policy must be 'none' or 'explicit'")
    fallback_model = _required_string(fallback_model, "fallback.model")
    expected = fallback_model if fallback_used else requested_model
    if resolved_model != expected:
        raise MillefeuilleContractError(
            "resolved_model does not match the declared fallback resolution"
        )


def _validate_refs(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise MillefeuilleContractError(f"{field_name} must be a non-empty list")
    refs: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value, start=1):
        ref = _required_string(item, f"{field_name}[{index}]")
        if (
            not _SAFE_REF.fullmatch(ref)
            or ref.startswith("/")
            or "//" in ref
            or "\\" in ref
            or ":" in ref
            or any(part in {"", ".", ".."} for part in ref.split("/"))
        ):
            raise MillefeuilleContractError(
                f"{field_name}[{index}] must be a safe relative artifact ref"
            )
        if ref in seen:
            raise MillefeuilleContractError(
                f"{field_name} contains duplicate ref {ref!r}"
            )
        seen.add(ref)
        refs.append(ref)
    return refs


def _validate_usage(value: object) -> dict[str, int]:
    usage = _require_object(value, "usage")
    _require_exact_fields(usage, _USAGE_FIELDS, "usage")
    result: dict[str, int] = {}
    for field_name in ("input_tokens", "output_tokens", "total_tokens"):
        token_count = usage.get(field_name)
        if isinstance(token_count, bool) or not isinstance(token_count, int):
            raise MillefeuilleContractError(f"usage.{field_name} must be an integer")
        if token_count < 0:
            raise MillefeuilleContractError(f"usage.{field_name} must be non-negative")
        result[field_name] = token_count
    if result["total_tokens"] != result["input_tokens"] + result["output_tokens"]:
        raise MillefeuilleContractError(
            "usage.total_tokens must equal input_tokens + output_tokens"
        )
    return result


def _validate_quality_warnings(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise MillefeuilleContractError("quality_warnings must be a list")
    warnings: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for index, item in enumerate(value, start=1):
        warning = _require_object(item, f"quality_warnings[{index}]")
        _require_exact_fields(
            warning,
            _WARNING_FIELDS if "ref" in warning else frozenset({"code", "severity"}),
            f"quality_warnings[{index}]",
        )
        code = _required_string(warning.get("code"), f"quality_warnings[{index}].code")
        if not _SAFE_WARNING_CODE.fullmatch(code):
            raise MillefeuilleContractError(
                f"quality_warnings[{index}].code must be slug-safe"
            )
        severity = _required_string(
            warning.get("severity"),
            f"quality_warnings[{index}].severity",
        )
        if severity not in _WARNING_SEVERITIES:
            raise MillefeuilleContractError(
                f"quality_warnings[{index}].severity is unsupported"
            )
        normalized: dict[str, str] = {"code": code, "severity": severity}
        if "ref" in warning:
            normalized["ref"] = _validate_refs(
                [warning["ref"]],
                f"quality_warnings[{index}].ref",
            )[0]
        signature = tuple(sorted(normalized.items()))
        if signature in seen:
            raise MillefeuilleContractError("quality_warnings contains duplicates")
        seen.add(signature)
        warnings.append(normalized)
    return warnings


def _reject_forbidden_evidence_payload(value: object, label: str) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in _FORBIDDEN_EVIDENCE_FIELDS:
                raise MillefeuilleContractError(
                    f"{label} must not include forbidden field {key!r}"
                )
            _reject_forbidden_evidence_payload(nested, f"{label}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value, start=1):
            _reject_forbidden_evidence_payload(nested, f"{label}[{index}]")
    elif isinstance(value, str):
        for pattern in _FORBIDDEN_EVIDENCE_MARKERS:
            if pattern.search(value):
                raise MillefeuilleContractError(
                    f"{label} contains forbidden payload marker"
                )


def _validate_execution_lane(*, provider: str, backend: str, auth_lane: str) -> None:
    fixture_markers = (provider == "none", backend == "fixture", auth_lane == "none")
    if any(fixture_markers) and not all(fixture_markers):
        raise MillefeuilleContractError(
            "fixture execution requires provider='none', backend='fixture', "
            "and auth_lane='none'; live execution requires none of those markers"
        )


def _validate_requested_parameters(stage_config: dict[str, Any]) -> None:
    record_usage = stage_config.get("record_usage")
    if not isinstance(record_usage, bool):
        raise MillefeuilleContractError("record_usage must be a boolean")

    reasoning_effort = stage_config.get("reasoning_effort")
    if reasoning_effort is not None and reasoning_effort not in _REASONING_EFFORTS:
        raise MillefeuilleContractError("reasoning_effort is unsupported")

    fast_mode = stage_config.get("fast_mode")
    if fast_mode is not None and fast_mode not in _FAST_MODES:
        raise MillefeuilleContractError("fast_mode is unsupported")

    prompt_version = stage_config.get("prompt_version")
    if prompt_version is not None:
        _required_string(prompt_version, "prompt_version")

    temperature = stage_config.get("temperature")
    if temperature is not None and (
        isinstance(temperature, bool) or not isinstance(temperature, (int, float))
    ):
        raise MillefeuilleContractError("temperature must be a number")
