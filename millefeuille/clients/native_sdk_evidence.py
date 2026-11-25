"""Process-profile observations, not native HTTP accounting or token enforcement."""

from millefeuille.clients.direct_generation import (
    DirectGenerationError,
    canonical,
    digest,
    strict_json,
)


def validate(request, response):
    def need(value, *, rule):
        if not value:
            raise DirectGenerationError("SDK science evidence mismatch", diagnostic={"layer": "python-evidence", "rule": rule})

    identity = {key: value for key, value in request.items() if key != "request_id"}
    need(request["request_id"] == digest(canonical(identity)), rule='evidence-gate-001')
    e = response["transport_evidence"]
    need(
        (set(e) - {"local_runtime"})
        == {
            "kind",
            "execution",
            "endpoint",
            "sdk_payload_sha256",
            "client_requested_reasoning",
            "provider_observed_reasoning",
            "sdk_payload",
            "http_status",
            "payload_count",
            "response_count",
            "sdk_result",
            "sdk_result_sha256",
            "raw_completed_response_available",
        }
    , rule='evidence-gate-002')
    runtime = e.get("local_runtime")
    invocation = request.get("input", {}).get("category_recovery", {})
    if not isinstance(invocation, dict):
        invocation = {}
    if invocation.get("schema") == "millefeuille-category-replacement-invocation/v2":
        need(runtime is not None, rule='evidence-gate-002')
    if "local_runtime" in e:
        need(request["task"] == "taxonomy-category" and isinstance(runtime, dict), rule='evidence-gate-002')
        need(set(runtime) == {"caller_signal_aborted", "caller_signal_reason", "elapsed_ms", "completion_timeout_ms", "caller_signal_timeout_ms", "owned_node_timeout_ms", "python_timeout_ms"}, rule='evidence-gate-002')
        need(type(runtime["caller_signal_aborted"]) is bool and type(runtime["caller_signal_reason"]) is str and runtime["caller_signal_reason"] in {"TimeoutError", "AbortError", "unknown"}, rule='evidence-gate-002')
        need(type(runtime["elapsed_ms"]) is int and 0 <= runtime["elapsed_ms"] <= 3_600_000, rule='evidence-gate-002')
        for key, expected in {"completion_timeout_ms": 900000, "caller_signal_timeout_ms": 915000, "owned_node_timeout_ms": 930000, "python_timeout_ms": 950000}.items():
            need(type(runtime[key]) is int and runtime[key] == expected, rule='evidence-gate-002')
    need(e["kind"] == "openclaw-native-sdk-process/v1", rule='evidence-gate-003')
    need(request["task"] in ("summary", "taxonomy-category", "independent-review"), rule='evidence-gate-004')
    need(e["endpoint"] == "https://chatgpt.com/backend-api/codex/responses", rule='evidence-gate-005')
    need(e["client_requested_reasoning"] == "xhigh", rule='evidence-gate-006')
    need(e["provider_observed_reasoning"] is None, rule='evidence-gate-007')
    need(e["raw_completed_response_available"] is False, rule='evidence-gate-008')
    need(type(e["http_status"]) is int and 200 <= e["http_status"] < 300, rule='evidence-gate-009')
    need(type(e["payload_count"]) is int and e["payload_count"] == 1, rule='evidence-gate-010')
    need(type(e["response_count"]) is int and e["response_count"] == 1, rule='evidence-gate-011')
    wire = e["sdk_payload"]
    need(isinstance(wire, dict) and digest(canonical(wire)) == e["sdk_payload_sha256"], rule='evidence-gate-012')
    need(wire.get("model") == "gpt-6-astra", rule='evidence-gate-013')
    need(wire.get("reasoning", {}).get("effort") == "xhigh", rule='evidence-gate-014')
    need(wire.get("tools") == [] and wire.get("tool_choice") == "none", rule='evidence-gate-015')
    need(wire.get("store") is False and not wire.get("previous_response_id"), rule='evidence-gate-016')
    need("include" not in wire, rule='evidence-gate-017')
    user_text = []
    for item in wire["input"]:
        need(item.get("type") not in ("compaction", "reasoning", "function_call"), rule='evidence-gate-034')
        if item.get("role") == "user":
            for part in item["content"]:
                need(part["type"] == "input_text", rule='evidence-gate-038')
                user_text.append(part["text"])
    need(len(user_text) == 1, rule='evidence-gate-018')
    need(user_text[0] == canonical(request["input"]).decode("utf8"), rule='evidence-gate-019')
    r = e["sdk_result"]
    need(
        isinstance(r, dict)
        and set(r)
        == {
            "role",
            "provider",
            "responseId",
            "responseModel",
            "stopReason",
            "content",
            "usage",
        }
    , rule='evidence-gate-020')
    need(digest(canonical(r)) == e["sdk_result_sha256"], rule='evidence-gate-021')
    need(r["role"] == "assistant" and r["provider"] == "openai", rule='evidence-gate-022')
    need(r["responseId"] == response["provider_request_id"], rule='evidence-gate-023')
    need(r["responseModel"] == "gpt-6-astra" and r["stopReason"] == "stop", rule='evidence-gate-024')
    need(isinstance(r["content"], list) and bool(r["content"]), rule='evidence-gate-025')
    texts = []
    for part in r["content"]:
        need(set(part) == {"type", "text"} and part["type"] == "text", rule='evidence-gate-035')
        need(isinstance(part["text"], str), rule='evidence-gate-036')
        texts.append(part["text"])
    need(strict_json("".join(texts)) == response["output"], rule='evidence-gate-026')
    from millefeuille.domain.native_sdk_bridge import PROFILE
    execution = e["execution"]
    need(execution == {"profile": PROFILE, "logical_calls": 1,
        "physical_http_requests": None, "hard_token_ceiling": False,
        "remote_cancellation_guaranteed": False, "sdk_internal_retries": "UNKNOWN",
        "usage_source": "UNKNOWN" if r["usage"] is None else "SDK_RETURNED",
        "task": request["task"]}, rule='evidence-gate-027')
    usage = r["usage"]
    need(usage is None or isinstance(usage, dict), rule='evidence-gate-028')
    if usage is not None:
        for key in ("input", "output", "cacheRead", "cacheWrite", "totalTokens"):
            if key in usage:
                need(type(usage[key]) is int and 0 <= usage[key] <= 2**53 - 1, rule='evidence-gate-037')
    return response
