"""Direct-generation JSON boundary with packaged subscription SSE default.

Legacy response attestations remain readable. New packaged responses additionally
retain actual completed SSE evidence. Top-level reasoning means client-requested,
not provider-observed. Credentials are environment-only; never API-key fallback.
"""

from __future__ import annotations

from contextlib import suppress
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

SCHEMA = "millefeuille-direct-generation/v1"
MODEL = "openai/gpt-6-astra"
MAX_IO = 2 * 1024 * 1024


class DirectGenerationError(ValueError):
    """Actionable, credential-free direct execution failure."""

    def __init__(self, message, diagnostic=None):
        super().__init__(message)
        self.diagnostic = safe_diagnostic(diagnostic)


# Failure observations only: never credentials, payloads, messages or proof of no effect.
DIAGNOSTIC_STAGES = frozenset(
    {
        "sdk-public-import",
        "sdk-request-validation",
        "sdk-prepare-selected-oauth",
        "sdk-single-science-completion",
        "sdk-provider-result-validation",
        "sdk-output-json-validation",
        "sdk-usage-validation",
        "sdk-response-serialization",
        "subscription-transport",
        "subscription-response-serialization",
        "executor-transport",
        "executor-response-validation",
    }
)


DIAGNOSTIC_RULES = frozenset(['node-request-identity', 'node-output-json','node-gate-001', 'node-gate-002', 'node-gate-003', 'node-gate-004', 'node-gate-005', 'node-gate-006', 'node-gate-007', 'node-gate-008', 'node-gate-009', 'node-gate-010', 'node-gate-011', 'node-gate-012', 'node-gate-013', 'node-gate-014', 'node-gate-015', 'node-gate-016', 'node-gate-017', 'node-gate-018', 'node-gate-019', 'node-gate-020', 'node-gate-021', 'node-gate-022', 'node-gate-023', 'node-gate-024', 'node-gate-025', 'node-gate-026', 'node-gate-027', 'node-gate-028', 'node-gate-029', 'evidence-gate-001', 'evidence-gate-002', 'evidence-gate-003', 'evidence-gate-004', 'evidence-gate-005', 'evidence-gate-006', 'evidence-gate-007', 'evidence-gate-008', 'evidence-gate-009', 'evidence-gate-010', 'evidence-gate-011', 'evidence-gate-012', 'evidence-gate-013', 'evidence-gate-014', 'evidence-gate-015', 'evidence-gate-016', 'evidence-gate-017', 'evidence-gate-018', 'evidence-gate-019', 'evidence-gate-020', 'evidence-gate-021', 'evidence-gate-022', 'evidence-gate-023', 'evidence-gate-024', 'evidence-gate-025', 'evidence-gate-026', 'evidence-gate-027', 'evidence-gate-028', 'evidence-gate-029', 'evidence-gate-030', 'evidence-gate-031', 'evidence-gate-032', 'evidence-gate-033', 'evidence-gate-034', 'evidence-gate-035', 'evidence-gate-036', 'evidence-gate-037', 'evidence-gate-038', 'adapter-gate-001', 'adapter-gate-002', 'adapter-gate-003', 'adapter-gate-004', 'adapter-gate-005', 'python-gate-001', 'python-gate-002', 'python-gate-003', 'python-gate-004', 'python-gate-005', 'python-gate-006', 'python-gate-007', 'python-gate-008', 'python-gate-009', 'python-gate-010', 'python-gate-011', 'python-gate-012', 'python-gate-013', 'python-gate-014', 'python-gate-015', 'python-gate-016', 'python-gate-017', 'python-gate-018', 'python-gate-019', 'python-gate-020', 'python-gate-021', 'unknown-rule', 'capture-binding', 'capture-storage', 'capture-write', 'capture-readback', 'capture-incomplete', 'capture-oversize'])
DIAGNOSTIC_LAYERS = frozenset({"python-inner-envelope", "python-inner-evidence", "python-outer-envelope", "python-outer-evidence", "adapter-serialization", "node-request", "node-prepare", "node-json", "node-usage", "node-serialization", "python-envelope", "python-evidence", "adapter", "node-result", "capture", "transport"})


def safe_diagnostic(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    wrappers = value.get("wrappers")
    if type(wrappers) is list:
        clean_wrappers = [x for x in wrappers[:3] if type(x) is str and x in {"python-inner", "python-outer", "adapter"}]
        if clean_wrappers:
            result["wrappers"] = clean_wrappers
    if isinstance(value.get("rule"), str) and value["rule"] in DIAGNOSTIC_RULES:
        result["rule"] = value["rule"]
    if isinstance(value.get("layer"), str) and value["layer"] in DIAGNOSTIC_LAYERS:
        result["layer"] = value["layer"]
    if isinstance(value.get("capture_status"), str) and value["capture_status"] in {"write-failed", "readback-failed", "incomplete", "truncated", "no-result", "complete-projection"}:
        result["capture_status"] = value["capture_status"]
    if isinstance(value.get("stage"), str) and value["stage"] in DIAGNOSTIC_STAGES:
        result["stage"] = value["stage"]
    for key in ("payload_count", "response_count"):
        count = value.get(key)
        if type(count) is int and 0 <= count <= 1_000_000:
            result[key] = count
    if "http_status" in value:
        status = value["http_status"]
        if status is None or (type(status) is int and 100 <= status <= 599):
            result["http_status"] = status
    identity = value.get("provider_request_id")
    if isinstance(identity, str) and re.fullmatch(
        r"(?:req|resp)_[A-Za-z0-9_-]{1,120}", identity
    ):
        result["provider_request_id"] = identity
    if isinstance(value.get("stop_reason"), str) and value["stop_reason"] in {
        "stop",
        "length",
        "toolUse",
        "error",
        "aborted",
    }:
        result["stop_reason"] = value["stop_reason"]
    usage = value.get("usage")
    if isinstance(usage, dict):
        clean = {
            k: usage[k]
            for k in ("input", "output", "cacheRead", "cacheWrite", "totalTokens")
            if type(usage.get(k)) is int and 0 <= usage[k] <= 2**53 - 1
        }
        if clean:
            result["usage"] = clean
    if type(value.get("caller_signal_aborted")) is bool:
        result["caller_signal_aborted"] = value["caller_signal_aborted"]
    if type(value.get("caller_signal_reason")) is str and value["caller_signal_reason"] in {"TimeoutError", "AbortError", "unknown"}:
        result["caller_signal_reason"] = value["caller_signal_reason"]
    for key in ("elapsed_ms", "completion_timeout_ms", "caller_signal_timeout_ms", "owned_node_timeout_ms", "python_timeout_ms"):
        number = value.get(key)
        if type(number) is int and 0 <= number <= 3_600_000:
            result[key] = number
    return result


def _wrap_diagnostic(value, role):
    result = safe_diagnostic(value)
    layer = result.get("layer")
    if layer in {"python-envelope", "python-evidence"}:
        result["layer"] = layer.replace("python-", role + "-")
    result.setdefault("rule", "unknown-rule")
    result.setdefault("layer", role + "-envelope")
    wrappers = result.get("wrappers", [])
    if role not in wrappers and len(wrappers) < 3:
        result["wrappers"] = wrappers + [role]
    return result


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise DirectGenerationError("Duplicate JSON field", diagnostic={"layer": "python-envelope", "rule": 'python-gate-018'})
            result[key] = value
        return result

    def constant(_):
        raise DirectGenerationError("Nonfinite JSON value", diagnostic={"layer": "python-envelope", "rule": 'python-gate-001'})

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except DirectGenerationError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DirectGenerationError("Executor/input is not strict JSON", diagnostic={"layer": "python-envelope", "rule": 'python-gate-011'}) from exc


def preflight(executor_argv, model=MODEL, reasoning="xhigh", timeout_seconds=300, *, task=None):
    from millefeuille.domain.postrun_reuse import forbid_new_provider

    forbid_new_provider()
    if model != MODEL or reasoning != "xhigh":
        raise DirectGenerationError(
            "Required model is openai/gpt-6-astra, reasoning xhigh"
        , diagnostic={"layer": "python-envelope", "rule": 'python-gate-002'})
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 1 <= timeout_seconds <= (950 if task == "taxonomy-category" else 600)
    ):
        raise DirectGenerationError(
            "timeout_seconds is outside the bounded task deadline"
        , diagnostic={"layer": "python-envelope", "rule": 'python-gate-003'})
    if executor_argv is None:
        executor_argv = [
            sys.executable,
            "-m",
            "millefeuille.clients.subscription_adapter",
        ]
    if (
        not isinstance(executor_argv, (list, tuple))
        or not executor_argv
        or len(executor_argv) > 64
        or any(
            not isinstance(a, str) or not a or "\x00" in a or len(a) > 4096
            for a in executor_argv
        )
    ):
        raise DirectGenerationError(
            "Supply executor_argv for a reviewed JSON-protocol OAuth adapter. "
            "No built-in standalone provider is installed; "
            "codex alone is not this adapter."
        , diagnostic={"layer": "python-envelope", "rule": 'python-gate-004'})
    executable = shutil.which(executor_argv[0])
    if executable is None:
        raise DirectGenerationError(
            "Configured executor executable is missing; install/configure it separately"
        , diagnostic={"layer": "python-envelope", "rule": 'python-gate-005'})
    return [executable, *executor_argv[1:]]


def _generate(request, *, executor_argv, timeout_seconds=300, _capture_token=None, _capture_fds=None, _budget_fds=None):
    """One bounded attempt, no shell, no retries, no stderr/credential logging."""
    from millefeuille.domain.postrun_reuse import forbid_new_provider

    forbid_new_provider()
    from millefeuille.domain.native_physical import pending_active
    if any(x is not None for x in (_capture_token, _capture_fds, _budget_fds)):
        raise DirectGenerationError("Historical native descriptors are unavailable")
    if pending_active() or _budget_fds is not None:
        if any(os.environ.get(key) for key in ("NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD", "LD_AUDIT")):
            raise DirectGenerationError("Unqualified active startup loader environment", diagnostic={"layer":"python-envelope", "rule":"startup-loader"})
    from millefeuille.domain import native_sdk_bridge as bridge
    if bridge.selected():
        expected_timeout = 950 if request.get("task") == "taxonomy-category" else 300
        if timeout_seconds not in (300, expected_timeout):
            raise DirectGenerationError("Bridge process contract requires its task deadline")
        timeout_seconds = expected_timeout
        if executor_argv is not None or any(x is not None for x in (_capture_token, _capture_fds, _budget_fds)):
            raise DirectGenerationError("Bridge requires owned direct worker; no custom executor/capture/strict IPC")
        executor_argv = bridge.worker_argv()
    argv = preflight(
        executor_argv, request["model"], request["reasoning"], timeout_seconds,
        task=request.get("task") if bridge.selected() else None,
    )
    budget_argv = tuple(argv)
    wire = canonical(request)
    if len(wire) > MAX_IO:
        raise DirectGenerationError(
            "Generation request exceeds 2 MiB; no truncation performed"
        , diagnostic={"layer": "python-envelope", "rule": 'python-gate-006'})
    # Do not accidentally hand API-key credentials to a subscription-only adapter.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "CODEX_API_KEY"}
    }
    if bridge.selected():
        for key in ("MILLEFEUILLE_CHATGPT_ACCESS_TOKEN", "MILLEFEUILLE_CHATGPT_ACCOUNT_ID",
                    "MISTRAL_API_KEY", "ZOTERO_READ_KEY", "ZOTERO_WRITE_KEY"):
            env.pop(key, None)
    pass_fds = (); budget = None; process = None
    reader = writer = None; capture = None; capture_entered = False
    output = bytearray(); overflow = threading.Event(); io_stop = threading.Event()
    capture_deadline = time.monotonic() + timeout_seconds
    deadline = capture_deadline
    try:
        if _capture_token is not None or _capture_fds is not None:
            raise DirectGenerationError("Historical capture context is unavailable")
        # Historical physical/qualification contexts cannot execute as ordinary.
        if pending_active() or bridge.qualification_state() is not None or _budget_fds is not None:
            raise DirectGenerationError("Historical native budget context is unavailable")
        process_pass_fds = tuple(pass_fds) + (() if budget is None else tuple(budget.child))
        if len(set(process_pass_fds)) != len(process_pass_fds):
            raise DirectGenerationError("Inherited descriptor collision; no dispatch", diagnostic={"layer": "python-envelope", "rule": "descriptor-collision"})
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, shell=False, close_fds=True,
            **({"pass_fds": process_pass_fds} if process_pass_fds else {}),
            start_new_session=(os.name == "posix" and _capture_fds is None and _budget_fds is None),
        )
        if budget is not None:
            budget.start(process)
        # _capture_fds are borrowed. Their adapter owner closes them, never us.
        import select
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        def receive():
            fd = process.stdout.fileno()
            while not io_stop.is_set():
                if not select.select([fd], [], [], 0.1)[0]: continue
                try: chunk = os.read(fd, 65536)
                except BlockingIOError: continue
                if not chunk: break
                if len(output) + len(chunk) > MAX_IO:
                    overflow.set(); break
                output.extend(chunk)
        def send():
            fd = process.stdin.fileno(); offset = 0
            try:
                while offset < len(wire) and not io_stop.is_set():
                    if not select.select([], [fd], [], 0.1)[1]: continue
                    try: count = os.write(fd, wire[offset:offset+65536])
                    except BlockingIOError: continue
                    if count <= 0: break
                    offset += count
            except (BrokenPipeError, OSError): pass
            finally:
                with suppress(OSError): process.stdin.close()
        reader = threading.Thread(target=receive, daemon=True)
        writer = threading.Thread(target=send, daemon=True)
        reader.start(); writer.start()
        while process.poll() is None or reader.is_alive() or writer.is_alive():
            if overflow.is_set() or time.monotonic() >= deadline:
                raise DirectGenerationError("Executor timeout/output bound reached; outcome uncertain, do not retry", diagnostic={"layer":"python-envelope", "rule":"python-gate-019"})
            time.sleep(0.01)
        if overflow.is_set():
            raise DirectGenerationError("Executor output limit exceeded; do not retry", diagnostic={"layer":"python-envelope", "rule":"python-gate-012"})
        if process.returncode:
            diagnostic = {}
            try: diagnostic = safe_diagnostic(strict_json(bytes(output)))
            except DirectGenerationError: pass
            raise DirectGenerationError("Executor failed; no automatic retry", diagnostic=diagnostic)
    finally:
        io_stop.set()
        active_error = sys.exc_info()[0] is not None
        cleanup_errors = []
        def clean(operation):
            try: operation()
            except BaseException as error: cleanup_errors.append(error)
        # Revoke first, even if process/pump/stream cleanup subsequently fails.
        if budget is not None: clean(budget.close)
        if process is not None:
            if os.name == "posix" and _capture_fds is None and _budget_fds is None:
                def kill_group():
                    with suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGKILL)
                clean(kill_group)
            elif process.poll() is None: clean(process.kill)
            clean(lambda: process.wait(timeout=5))
            for thread in (reader, writer):
                if thread is not None and thread.ident is not None: clean(lambda t=thread: t.join(timeout=1))
            for stream in (process.stdin, process.stdout):
                if stream is not None: clean(stream.close)
        if cleanup_errors and not active_error:
            raise DirectGenerationError("Owned transport cleanup incomplete; do not retry", diagnostic={"layer":"python-envelope", "rule":"owned-cleanup"})
    response = None
    try:
        response = strict_json(bytes(output))
        is_bridge = isinstance(response, dict) and response.get("transport_evidence", {}).get("kind") == "openclaw-native-sdk-process/v1"
        if is_bridge != bridge.selected():
            raise DirectGenerationError("Execution profile/result mismatch; no fallback")
        return validate_response(request, response)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        # Unvalidated adapter metadata is an observation, never accepted success.
        evidence = (
            response.get("transport_evidence") if isinstance(response, dict) else None
        )
        observed = safe_diagnostic(getattr(error, "diagnostic", None)) or safe_diagnostic(evidence)
        if "rule" not in observed:
            observed.update(layer="python-envelope", rule="unknown-rule")
        observed["stage"] = "executor-response-validation"
        raise DirectGenerationError(
            "Executor response validation failed; no automatic retry",
            diagnostic=observed,
        ) from error


def generate(request, *, executor_argv, timeout_seconds=300, _capture_token=None, _capture_fds=None, _budget_fds=None):
    from millefeuille.domain.postrun_reuse import forbid_new_provider

    forbid_new_provider()
    role = "python-inner" if executor_argv is not None else "python-outer"
    try:
        return _generate(
            request, executor_argv=executor_argv, timeout_seconds=timeout_seconds,
            _capture_token=_capture_token, _capture_fds=_capture_fds, _budget_fds=_budget_fds
        )
    except DirectGenerationError as error:
        error.diagnostic = {
            "stage": "executor-transport",
            **_wrap_diagnostic(error.diagnostic, role),
        }
        raise


def validate_response(request, response):
    expected = {
        "schema_version": SCHEMA,
        "request_id": request["request_id"],
        "model": request["model"],
        "reasoning": request["reasoning"],
        "auth_class": "subscription_oauth",
        "provider": "openai",
    }
    if not isinstance(response, dict) or (set(response) - {"transport_evidence"}) != (
        set(expected) | {"provider_request_id", "output"}
    ):
        raise DirectGenerationError("Executor response fields invalid", diagnostic={"layer": "python-envelope", "rule": 'python-gate-007'})
    if any(response[k] != v for k, v in expected.items()):
        raise DirectGenerationError(
            "Executor model/auth/request identity mismatch; no fallback allowed"
        , diagnostic={"layer": "python-envelope", "rule": 'python-gate-008'})
    receipt = response["provider_request_id"]
    if not isinstance(receipt, str) or not receipt.strip() or len(receipt) > 256:
        raise DirectGenerationError(
            "Executor must return an observed provider request identity"
        , diagnostic={"layer": "python-envelope", "rule": 'python-gate-009'})
    if not isinstance(response["output"], dict):
        raise DirectGenerationError("Executor output must be an object", diagnostic={"layer": "python-envelope", "rule": 'python-gate-010'})
    if "transport_evidence" in response:
        evidence = response["transport_evidence"]
        if (
            evidence.get("endpoint")
            != "https://chatgpt.com/backend-api/codex/responses"
        ):
            raise DirectGenerationError("Subscription endpoint mismatch", diagnostic={"layer": "python-envelope", "rule": 'python-gate-013'})
        if evidence.get("kind") == "openclaw-native-sdk-process/v1":
            from millefeuille.clients.native_sdk_evidence import validate
            return validate(request, response)
        if evidence.get("kind") == "openclaw-public-sdk-science/v1":
            from millefeuille.clients.sdk_science_evidence import validate

            return validate(request, response)
        completed = evidence["completed_response"]
        if (
            completed.get("model") != "gpt-6-astra"
            or completed.get("status") != "completed"
            or completed.get("id") != receipt
        ):
            raise DirectGenerationError("Completed provider identity mismatch", diagnostic={"layer": "python-envelope", "rule": 'python-gate-014'})
        usage = completed.get("usage", {})
        if not all(
            type(usage.get(k)) is int and usage[k] >= 0
            for k in ("input_tokens", "output_tokens", "total_tokens")
        ):
            raise DirectGenerationError("Missing actual usage", diagnostic={"layer": "python-envelope", "rule": 'python-gate-015'})
        text = []
        for item in completed["output"]:
            if item["type"] not in ("message", "reasoning"):
                raise DirectGenerationError("Unexpected provider output type", diagnostic={"layer": "python-envelope", "rule": 'python-gate-020'})
            if item["type"] == "message":
                for part in item["content"]:
                    if part["type"] != "output_text":
                        raise DirectGenerationError("Refusal or unexpected content", diagnostic={"layer": "python-envelope", "rule": 'python-gate-021'})
                    text.append(part["text"])
        if strict_json("".join(text)) != response["output"]:
            raise DirectGenerationError("Provider output binding mismatch", diagnostic={"layer": "python-envelope", "rule": 'python-gate-016'})
        if evidence["provider_observed_reasoning"] != completed.get("reasoning"):
            raise DirectGenerationError("Reasoning evidence mismatch", diagnostic={"layer": "python-envelope", "rule": 'python-gate-017'})
    return response
