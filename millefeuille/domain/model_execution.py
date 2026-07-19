"""Deterministic, no-call execution plans for model-backed summary stages."""

from __future__ import annotations

from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE

MODEL_EXECUTION_PLAN_SCHEMA_VERSION = "millefeuille-model-execution-plan/v0.1"
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


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MillefeuilleContractError(f"{field_name} must be a non-empty string")
    return value.strip()


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
