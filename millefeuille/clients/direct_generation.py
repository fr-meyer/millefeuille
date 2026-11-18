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
                raise DirectGenerationError("Duplicate JSON field")
            result[key] = value
        return result

    def constant(_):
        raise DirectGenerationError("Nonfinite JSON value")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DirectGenerationError("Executor/input is not strict JSON") from exc


def preflight(executor_argv, model=MODEL, reasoning="xhigh", timeout_seconds=300):
    if model != MODEL or reasoning != "xhigh":
        raise DirectGenerationError(
            "Required model is openai/gpt-6-astra, reasoning xhigh"
        )
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 1 <= timeout_seconds <= 600
    ):
        raise DirectGenerationError(
            "timeout_seconds must be finite and between 1 and 600"
        )
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
        )
    executable = shutil.which(executor_argv[0])
    if executable is None:
        raise DirectGenerationError(
            "Configured executor executable is missing; install/configure it separately"
        )
    return [executable, *executor_argv[1:]]


def generate(request, *, executor_argv, timeout_seconds=300):
    """One bounded attempt, no shell, no retries, no stderr/credential logging."""
    argv = preflight(
        executor_argv, request["model"], request["reasoning"], timeout_seconds
    )
    wire = canonical(request)
    if len(wire) > MAX_IO:
        raise DirectGenerationError(
            "Generation request exceeds 2 MiB; no truncation performed"
        )
    # Do not accidentally hand API-key credentials to a subscription-only adapter.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "CODEX_API_KEY"}
    }
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        shell=False,
        start_new_session=(os.name == "posix"),
    )
    output = bytearray()
    overflow = threading.Event()

    def receive():
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            if len(output) + len(chunk) > MAX_IO:
                overflow.set()
                break
            output.extend(chunk)

    def send():
        try:
            process.stdin.write(wire)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    reader = threading.Thread(target=receive, daemon=True)
    writer = threading.Thread(target=send, daemon=True)
    reader.start()
    writer.start()
    deadline = time.monotonic() + timeout_seconds
    try:
        while process.poll() is None or reader.is_alive() or writer.is_alive():
            if overflow.is_set() or time.monotonic() >= deadline:
                raise DirectGenerationError(
                    "Executor timeout/output bound reached; "
                    "outcome uncertain, do not retry"
                )
            time.sleep(0.01)
        if overflow.is_set():
            raise DirectGenerationError("Executor output limit exceeded; do not retry")
        if process.returncode:
            detail = "unknown"
            try:
                failure = strict_json(bytes(output))
                stage = failure.get("stage")
                if (
                    isinstance(stage, str)
                    and 1 <= len(stage) <= 64
                    and all(c in "abcdefghijklmnopqrstuvwxyz-" for c in stage)
                ):
                    detail = stage
            except Exception:
                pass
            raise DirectGenerationError(
                "Executor failed at " + detail + "; inspect retained evidence; "
                "no automatic retry"
            )
    finally:
        if os.name == "posix":
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=1)
        writer.join(timeout=1)
        process.stdout.close()
    response = strict_json(bytes(output))
    return validate_response(request, response)


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
        raise DirectGenerationError("Executor response fields invalid")
    if any(response[k] != v for k, v in expected.items()):
        raise DirectGenerationError(
            "Executor model/auth/request identity mismatch; no fallback allowed"
        )
    receipt = response["provider_request_id"]
    if not isinstance(receipt, str) or not receipt.strip() or len(receipt) > 256:
        raise DirectGenerationError(
            "Executor must return an observed provider request identity"
        )
    if not isinstance(response["output"], dict):
        raise DirectGenerationError("Executor output must be an object")
    if "transport_evidence" in response:
        evidence = response["transport_evidence"]
        if (
            evidence.get("endpoint")
            != "https://chatgpt.com/backend-api/codex/responses"
        ):
            raise DirectGenerationError("Subscription endpoint mismatch")
        completed = evidence["completed_response"]
        if (
            completed.get("model") != "gpt-6-astra"
            or completed.get("status") != "completed"
            or completed.get("id") != receipt
        ):
            raise DirectGenerationError("Completed provider identity mismatch")
        usage = completed.get("usage", {})
        if not all(
            type(usage.get(k)) is int and usage[k] >= 0
            for k in ("input_tokens", "output_tokens", "total_tokens")
        ):
            raise DirectGenerationError("Missing actual usage")
        text = []
        for item in completed["output"]:
            if item["type"] not in ("message", "reasoning"):
                raise DirectGenerationError("Unexpected provider output type")
            if item["type"] == "message":
                for part in item["content"]:
                    if part["type"] != "output_text":
                        raise DirectGenerationError("Refusal or unexpected content")
                    text.append(part["text"])
        if strict_json("".join(text)) != response["output"]:
            raise DirectGenerationError("Provider output binding mismatch")
        if evidence["provider_observed_reasoning"] != completed.get("reasoning"):
            raise DirectGenerationError("Reasoning evidence mismatch")
    return response
