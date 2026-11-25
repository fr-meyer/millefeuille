"""Packaged public OpenClaw SDK science bridge, one completion/no fallback."""

import os
import sys

from millefeuille.clients.direct_generation import (
    DirectGenerationError,
    MAX_IO,
    MODEL,
    SCHEMA,
    canonical,
    safe_diagnostic,
    strict_json,
)

ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
TASKS = ("summary", "taxonomy-category", "independent-review")


def require(value, *, rule):
    if not value:
        raise DirectGenerationError("subscription protocol refused", diagnostic={"layer": "adapter", "rule": rule})


def capabilities():
    from millefeuille.domain.native_sdk_bridge import selected, PROFILE
    if selected():
        return {"schema_version": SCHEMA, "tasks": list(TASKS), "model": MODEL, "reasoning": "xhigh", "endpoint": ENDPOINT, "tools": False, "execution_profile": PROFILE, "logical_calls": 1, "physical_http_requests": None, "sdk_internal_retries": "UNKNOWN", "hard_token_ceiling": False}
    return {
        "schema_version": SCHEMA,
        "tasks": list(TASKS),
        "model": MODEL,
        "reasoning": "xhigh",
        "endpoint": ENDPOINT,
        "tools": False,
        "retries": 0,
    }


def complete(request, *, _capture_fds=None, _budget_fds=None):
    from pathlib import Path

    from millefeuille.clients.direct_generation import generate

    require(_capture_fds is None and _budget_fds is None, rule="adapter-native-overrides")
    require(request["task"] in ("summary", "taxonomy-category", "independent-review"), rule='adapter-gate-001')
    from millefeuille.clients.native_profile import configured_profile
    configured_profile()  # Explicit selection; SDK checks selected profile before completion.
    from millefeuille.domain.native_sdk_bridge import selected

    if selected():
        require(_capture_fds is None and _budget_fds is None, rule='adapter-native-overrides')
        return generate(request, executor_argv=None)
    asset = Path(__file__).with_name("sdk_science.mjs")
    return generate(request, executor_argv=["node", str(asset)], _capture_fds=_capture_fds, _budget_fds=_budget_fds)


def main():
    result = None
    fds = None
    try:
        if sys.argv[1:] == ["--capabilities"]:
            result = capabilities()
        else:
            from millefeuille.domain.native_budget_ipc import split_flags
            flags, budget_fds = split_flags(sys.argv[1:])
            fds = None
            require(not flags and budget_fds is None, rule='adapter-native-overrides')
            raw = sys.stdin.buffer.read(MAX_IO + 1)
            require(len(raw) <= MAX_IO, rule='adapter-gate-005')
            result = complete(strict_json(raw), _capture_fds=fds, _budget_fds=budget_fds)
        wire = canonical(result)
        require(len(wire) <= MAX_IO, rule='adapter-gate-003')
        sys.stdout.buffer.write(wire)
    except Exception as error:
        diagnostic = safe_diagnostic(getattr(error, "diagnostic", None))
        if not diagnostic and isinstance(result, dict):
            diagnostic = safe_diagnostic(result.get("transport_evidence"))
            diagnostic["stage"] = "subscription-response-serialization"
        diagnostic.setdefault("rule", "unknown-rule")
        diagnostic.setdefault("layer", "adapter-serialization")
        wrappers = diagnostic.get("wrappers", [])
        if "adapter" not in wrappers and len(wrappers) < 3:
            diagnostic["wrappers"] = wrappers + ["adapter"]
        if diagnostic:
            sys.stdout.buffer.write(canonical(diagnostic))
            raise SystemExit(1) from None
        sys.stdout.write('{"stage":"subscription-transport","error":"failed-no-retry"}')
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
