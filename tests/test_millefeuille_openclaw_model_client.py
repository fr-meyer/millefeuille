"""Tests for the tool-free OpenClaw subscription-OAuth model boundary."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from jsonschema import ValidationError

from millefeuille.clients.openclaw_model_client import (
    OpenClawModelClient,
    _bounded_run,
    _OutputLimitExceeded,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_executor import build_model_executor_request


class _QueuedRunner:
    def __init__(self, responses: list[subprocess.CompletedProcess[str]]) -> None:
        self.responses = responses
        self.commands: list[list[str]] = []
        self.kwargs: list[dict[str, object]] = []
        self.runtime_configs: list[dict[str, object]] = []

    def __call__(self, command: list[str], **kwargs):
        self.commands.append(command)
        self.kwargs.append(kwargs)
        if "--config" in command:
            config_path = command[command.index("--config") + 1]
            with open(config_path, encoding="utf-8") as handle:
                self.runtime_configs.append(json.load(handle))
        return self.responses.pop(0)


def _completed(payload: object, *, returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=json.dumps(payload),
        stderr="",
    )


def _auth_status(provider: str) -> dict[str, object]:
    profile_id = f"{provider}:research"
    return {
        "agentId": "franck",
        "agentDir": str(
            Path(Path.cwd().anchor) / "openclaw" / "agents" / "franck" / "agent"
        ),
        "auth": {
            "providers": [
                {
                    "provider": provider,
                    "effective": {"kind": "profiles"},
                    "profiles": {
                        "count": 1,
                        "oauth": 1,
                        "token": 0,
                        "apiKey": 0,
                        "labels": [f"{profile_id}=OAuth (research)"],
                    },
                }
            ],
            "oauth": {
                "profiles": [
                    {
                        "profileId": profile_id,
                        "provider": provider,
                        "type": "oauth",
                        "status": "expiring",
                        "source": "store",
                    }
                ]
            },
            "shellEnvFallback": {"enabled": False, "appliedKeys": []},
        },
    }


def _model_response(provider: str, model: str, text: str) -> dict[str, object]:
    return {
        "ok": True,
        "status": "ok",
        "provider": provider,
        "model": model,
        "final": text,
        "payloads": [{"text": text}],
        "toolSummary": {"calls": 0, "tools": []},
        "bridgeCalls": {"search": 0, "describe": 0, "call": 0},
        "codeModeEngaged": False,
        "assistantTurns": 1,
    }


class TestOpenClawModelClient(unittest.TestCase):
    def _request(
        self,
        *,
        model: str = "openai/gpt-5.6-sol",
        payload: bytes = b'{"return":{"canary":"ok"}}',
        fallback_models: list[str] | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, object]:
        fallbacks = fallback_models or []
        return build_model_executor_request(
            task_kind="summarize_page",
            unit_id="canary-page-1",
            source_locators=["canary:p.1"],
            requested_model=model,
            thinking="xhigh",
            prompt_template_id="canary",
            prompt_template_version="v1",
            input_payload=payload,
            output_schema_id="canary",
            output_schema_version="v1",
            timeout_seconds=timeout_seconds,
            max_attempts=1 + len(fallbacks),
            retry_on=["provider_error"] if fallbacks else [],
            fallback_models=fallbacks,
        )

    @staticmethod
    def _validate_canary(value: object) -> None:
        if value != {"canary": "ok"}:
            raise ValueError("invalid canary")

    def test_success_uses_exact_model_thinking_and_oauth_profile(self):
        payload = b'{"return":{"canary":"ok"}}'
        runner = _QueuedRunner(
            [
                _completed(_auth_status("openai")),
                _completed(
                    _model_response(
                        "openai",
                        "gpt-5.6-sol",
                        '{"canary":"ok"}',
                    )
                ),
            ]
        )
        client = OpenClawModelClient(command_runner=runner)

        with patch.dict(
            os.environ,
            {
                "CODEX_API_KEY": "test-only",
                "OPENAI_API_KEY": "test-only",
                "OPENAI_API_KEYS": "test-only",
                "OPENAI_API_KEY_1": "test-only",
                "HOME": "/live/home",
                "OPENCLAW_HOME": "/live/openclaw-home",
                "OPENCLAW_STATE_DIR": "/live/openclaw-state",
                "XDG_CONFIG_HOME": "/live/xdg-config",
                "XDG_DATA_HOME": "/live/xdg-data",
                "OPENCLAW_AUTH_PROFILE_SECRET_DIR": "/oauth/secret-reference",
            },
        ):
            execution = client.execute(
                request=self._request(payload=payload),
                input_payload=payload,
                output_validator=self._validate_canary,
            )

        self.assertEqual(execution.output, b'{"canary":"ok"}')
        self.assertEqual(execution.result["status"], "succeeded")
        self.assertEqual(execution.result["actual_model"], "openai/gpt-5.6-sol")
        self.assertEqual(
            execution.result["authentication"],
            {
                "class": "subscription_oauth",
                "profile_ref": "openai:research",
            },
        )
        command = runner.commands[1]
        self.assertEqual(command[:3], ["openclaw", "agent", "exec"])
        self.assertEqual(command[command.index("--message-file") + 1], "-")
        self.assertEqual(command[command.index("--model") + 1], "openai/gpt-5.6-sol")
        self.assertEqual(command[command.index("--thinking") + 1], "xhigh")
        self.assertNotIn(payload.decode(), command)
        self.assertEqual(runner.kwargs[1]["input"], payload.decode())
        self.assertEqual(
            runner.runtime_configs[0]["tools"],
            {"deny": ["*"], "codeMode": {"enabled": False}},
        )
        self.assertEqual(runner.runtime_configs[0]["plugins"], {"enabled": False})
        self.assertEqual(
            runner.runtime_configs[0]["auth"],
            {
                "profiles": {
                    "openai:research": {"provider": "openai", "mode": "oauth"}
                },
                "order": {"openai": ["openai:research"]},
            },
        )
        self.assertEqual(
            runner.runtime_configs[0]["agents"]["defaults"]["models"][
                "openai/gpt-5.6-sol"
            ]["agentRuntime"],
            {"id": "openclaw"},
        )
        self.assertEqual(
            runner.runtime_configs[0]["agents"]["defaults"]["model"]["fallbacks"],
            [],
        )
        child_env = runner.kwargs[1]["env"]
        self.assertFalse("CODEX_API_KEY" in child_env)
        self.assertFalse("OPENAI_API_KEY" in child_env)
        self.assertFalse("OPENAI_API_KEYS" in child_env)
        self.assertFalse("OPENAI_API_KEY_1" in child_env)
        auth_env = runner.kwargs[0]["env"]
        self.assertEqual(auth_env["OPENCLAW_STATE_DIR"], "/live/openclaw-state")
        self.assertEqual(
            child_env["OPENCLAW_AUTH_PROFILE_SECRET_DIR"],
            "/oauth/secret-reference",
        )
        temporary_root = os.path.dirname(command[command.index("--config") + 1])
        for key in (
            "HOME",
            "OPENCLAW_HOME",
            "OPENCLAW_STATE_DIR",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "TMPDIR",
            "TMP",
            "TEMP",
        ):
            self.assertEqual(
                os.path.commonpath((temporary_root, child_env[key])),
                temporary_root,
            )
            self.assertFalse(child_env[key].startswith("/live/"))

    def test_xai_success_is_attributed_to_exact_requested_model(self):
        payload = b'{"return":{"canary":"ok"}}'
        runner = _QueuedRunner(
            [
                _completed(_auth_status("xai")),
                _completed(_model_response("xai", "grok-4.6", '{"canary":"ok"}')),
            ]
        )
        client = OpenClawModelClient(command_runner=runner)

        execution = client.execute(
            request=self._request(model="xai/grok-4.6", payload=payload),
            input_payload=payload,
            output_validator=self._validate_canary,
        )

        self.assertEqual(execution.result["status"], "succeeded")
        self.assertEqual(execution.result["actual_model"], "xai/grok-4.6")
        self.assertEqual(
            execution.result["authentication"]["profile_ref"],
            "xai:research",
        )

    def test_auth_pin_is_unchanged_when_profile_store_drifts(self):
        payload = b'{"return":{"canary":"ok"}}'

        class DriftRunner(_QueuedRunner):
            def __call__(self, command: list[str], **kwargs):
                if "--config" not in command:
                    response = super().__call__(command, **kwargs)
                    self.available_profile_ids = {"openai:replacement"}
                    return response
                config_path = command[command.index("--config") + 1]
                with open(config_path, encoding="utf-8") as handle:
                    config = json.load(handle)
                self.asserted_pin = config["auth"]["order"]["openai"]
                self.commands.append(command)
                self.kwargs.append(kwargs)
                selected_profile = self.asserted_pin[0]
                if selected_profile in self.available_profile_ids:
                    return _completed(
                        _model_response("openai", "gpt-5.6-sol", '{"canary":"ok"}')
                    )
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=1,
                    stdout="",
                    stderr="selected profile unavailable",
                )

        runner = DriftRunner([_completed(_auth_status("openai"))])
        execution = OpenClawModelClient(command_runner=runner).execute(
            request=self._request(payload=payload),
            input_payload=payload,
            output_validator=self._validate_canary,
        )
        self.assertEqual(runner.available_profile_ids, {"openai:replacement"})
        self.assertEqual(runner.asserted_pin, ["openai:research"])
        self.assertEqual(execution.result["status"], "failed")
        self.assertIsNone(execution.output)

    def test_non_oauth_profile_fails_before_provider_execution(self):
        status = _auth_status("openai")
        status["auth"]["providers"][0]["profiles"]["oauth"] = 0
        status["auth"]["providers"][0]["profiles"]["apiKey"] = 1
        runner = _QueuedRunner([_completed(status)])
        client = OpenClawModelClient(command_runner=runner)
        payload = b'{"return":{"canary":"ok"}}'

        execution = client.execute(
            request=self._request(payload=payload),
            input_payload=payload,
            output_validator=self._validate_canary,
        )

        self.assertEqual(len(runner.commands), 1)
        self.assertEqual(execution.result["status"], "failed")
        self.assertEqual(
            execution.result["attempts"][0]["failure_code"],
            "auth_unavailable",
        )
        self.assertIsNone(execution.result["authentication"]["profile_ref"])

    def test_openclaw_ok_oauth_status_is_usable(self):
        payload = b'{"return":{"canary":"ok"}}'
        auth = _auth_status("openai")
        auth["auth"]["oauth"]["profiles"][0]["status"] = "ok"
        runner = _QueuedRunner(
            [
                _completed(auth),
                _completed(_model_response("openai", "gpt-5.6-sol", '{"canary":"ok"}')),
            ]
        )
        execution = OpenClawModelClient(command_runner=runner).execute(
            request=self._request(payload=payload),
            input_payload=payload,
            output_validator=self._validate_canary,
        )
        self.assertEqual(execution.result["status"], "succeeded")

    def test_missing_or_unknown_oauth_status_fails_before_model_execution(self):
        payload = b'{"return":{"canary":"ok"}}'
        for status in (None, "future-unknown"):
            with self.subTest(status=status):
                auth = _auth_status("openai")
                profile = auth["auth"]["oauth"]["profiles"][0]
                if status is None:
                    del profile["status"]
                else:
                    profile["status"] = status
                runner = _QueuedRunner([_completed(auth)])
                execution = OpenClawModelClient(command_runner=runner).execute(
                    request=self._request(payload=payload),
                    input_payload=payload,
                    output_validator=self._validate_canary,
                )
                self.assertEqual(len(runner.commands), 1)
                self.assertEqual(execution.result["status"], "failed")
                self.assertEqual(
                    execution.result["attempts"][0]["failure_code"],
                    "auth_unavailable",
                )
                self.assertIsNone(execution.output)

    def test_actual_model_mismatch_fails_closed_without_output_binding(self):
        payload = b'{"return":{"canary":"ok"}}'
        runner = _QueuedRunner(
            [
                _completed(_auth_status("openai")),
                _completed(_model_response("openai", "gpt-5.5", '{"canary":"ok"}')),
            ]
        )
        client = OpenClawModelClient(command_runner=runner)

        execution = client.execute(
            request=self._request(payload=payload),
            input_payload=payload,
            output_validator=self._validate_canary,
        )

        self.assertEqual(execution.result["status"], "failed")
        self.assertEqual(
            execution.result["attempts"][0]["failure_code"],
            "actual_model_mismatch",
        )
        self.assertEqual(execution.result["output"], {"sha256": None, "bytes": None})
        self.assertIsNone(execution.output)

    def test_schema_failure_discards_raw_output(self):
        payload = b'{"return":{"canary":"ok"}}'
        runner = _QueuedRunner(
            [
                _completed(_auth_status("openai")),
                _completed(
                    _model_response(
                        "openai",
                        "gpt-5.6-sol",
                        '{"canary":"wrong"}',
                    )
                ),
            ]
        )
        client = OpenClawModelClient(command_runner=runner)

        execution = client.execute(
            request=self._request(payload=payload),
            input_payload=payload,
            output_validator=self._validate_canary,
        )

        self.assertEqual(execution.result["status"], "failed")
        self.assertEqual(
            execution.result["attempts"][0]["failure_code"],
            "schema_validation_failed",
        )
        self.assertEqual(execution.result["schema_validation"]["status"], "failed")
        self.assertIsNone(execution.output)

    def test_tool_use_fails_closed_without_output_binding(self):
        payload = b'{"return":{"canary":"ok"}}'
        response = _model_response("openai", "gpt-5.6-sol", '{"canary":"ok"}')
        response["toolSummary"] = {"calls": 1, "tools": ["read"]}
        runner = _QueuedRunner(
            [_completed(_auth_status("openai")), _completed(response)]
        )
        client = OpenClawModelClient(command_runner=runner)

        execution = client.execute(
            request=self._request(payload=payload),
            input_payload=payload,
            output_validator=self._validate_canary,
        )

        self.assertEqual(execution.result["status"], "failed")
        self.assertEqual(
            execution.result["attempts"][0]["failure_code"], "invalid_response"
        )
        self.assertIsNone(execution.output)

    def test_missing_tool_evidence_fails_closed(self):
        payload = b'{"return":{"canary":"ok"}}'
        response = _model_response("openai", "gpt-5.6-sol", '{"canary":"ok"}')
        del response["toolSummary"]
        runner = _QueuedRunner(
            [_completed(_auth_status("openai")), _completed(response)]
        )
        execution = OpenClawModelClient(command_runner=runner).execute(
            request=self._request(payload=payload),
            input_payload=payload,
            output_validator=self._validate_canary,
        )
        self.assertEqual(execution.result["status"], "failed")
        self.assertEqual(
            execution.result["attempts"][0]["failure_code"], "invalid_response"
        )
        self.assertIsNone(execution.output)

    def test_missing_agent_safety_evidence_fails_closed(self):
        payload = b'{"return":{"canary":"ok"}}'
        for missing in ("bridgeCalls", "codeModeEngaged", "assistantTurns"):
            with self.subTest(missing=missing):
                response = _model_response("openai", "gpt-5.6-sol", '{"canary":"ok"}')
                del response[missing]
                runner = _QueuedRunner(
                    [_completed(_auth_status("openai")), _completed(response)]
                )
                execution = OpenClawModelClient(command_runner=runner).execute(
                    request=self._request(payload=payload),
                    input_payload=payload,
                    output_validator=self._validate_canary,
                )
                self.assertEqual(execution.result["status"], "failed")
                self.assertEqual(
                    execution.result["attempts"][0]["failure_code"],
                    "invalid_response",
                )
                self.assertIsNone(execution.output)

    def test_jsonschema_validator_error_is_a_failed_execution(self):
        payload = b'{"return":{"canary":"ok"}}'
        runner = _QueuedRunner(
            [
                _completed(_auth_status("openai")),
                _completed(_model_response("openai", "gpt-5.6-sol", '{"canary":"ok"}')),
            ]
        )

        def reject(_value: object) -> None:
            raise ValidationError("test schema rejection")

        execution = OpenClawModelClient(command_runner=runner).execute(
            request=self._request(payload=payload),
            input_payload=payload,
            output_validator=reject,
        )
        self.assertEqual(execution.result["status"], "failed")
        self.assertEqual(
            execution.result["attempts"][0]["failure_code"],
            "schema_validation_failed",
        )
        self.assertIsNone(execution.output)

    def test_auth_lookup_uses_request_budget_and_timeout_fails_closed(self):
        payload = b'{"return":{"canary":"ok"}}'

        class TimeoutRunner(_QueuedRunner):
            def __call__(self, command: list[str], **kwargs):
                self.commands.append(command)
                self.kwargs.append(kwargs)
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        runner = TimeoutRunner([])
        execution = OpenClawModelClient(command_runner=runner).execute(
            request=self._request(payload=payload, timeout_seconds=10),
            input_payload=payload,
            output_validator=self._validate_canary,
        )
        self.assertEqual(len(runner.commands), 1)
        self.assertLessEqual(runner.kwargs[0]["timeout"], 10)
        self.assertEqual(execution.result["status"], "failed")
        self.assertEqual(execution.result["attempts"][0]["failure_code"], "timeout")

    def test_default_runner_bounds_stdout_and_stderr_during_capture(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), self.assertRaises(_OutputLimitExceeded):
                _bounded_run(
                    [
                        sys.executable,
                        "-c",
                        f"import sys; sys.{stream}.write('x' * 8192)",
                    ],
                    timeout=5,
                    max_stdout_bytes=1024,
                    max_stderr_bytes=1024,
                )

    def test_invalid_utf8_from_either_child_stream_fails_closed(self):
        payload = b'{"return":{"canary":"ok"}}'
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):

                class InvalidOutputRunner(_QueuedRunner):
                    def __call__(
                        self,
                        command: list[str],
                        *,
                        selected_stream: str = stream,
                        **kwargs,
                    ):
                        if self.responses:
                            return super().__call__(command, **kwargs)
                        return _bounded_run(
                            [
                                sys.executable,
                                "-c",
                                (
                                    "import sys; "
                                    f"sys.{selected_stream}.buffer.write(bytes([255]))"
                                ),
                            ],
                            input=kwargs.get("input"),
                            timeout=kwargs["timeout"],
                            env=kwargs["env"],
                        )

                runner = InvalidOutputRunner([_completed(_auth_status("openai"))])
                execution = OpenClawModelClient(command_runner=runner).execute(
                    request=self._request(payload=payload),
                    input_payload=payload,
                    output_validator=self._validate_canary,
                )
                self.assertEqual(execution.result["status"], "failed")
                self.assertEqual(
                    execution.result["attempts"][0]["failure_code"],
                    "invalid_response",
                )
                self.assertIsNone(execution.output)

    def test_short_timeout_rejected_before_any_process(self):
        payload = b'{"return":{"canary":"ok"}}'
        runner = _QueuedRunner([])
        with self.assertRaisesRegex(MillefeuilleContractError, "timeout_seconds"):
            OpenClawModelClient(command_runner=runner).execute(
                request=self._request(payload=payload, timeout_seconds=1),
                input_payload=payload,
                output_validator=self._validate_canary,
            )
        self.assertEqual(runner.commands, [])

    def test_explicit_fallback_is_rejected_before_auth_lookup(self):
        payload = b'{"return":{"canary":"ok"}}'
        runner = _QueuedRunner([])
        client = OpenClawModelClient(command_runner=runner)
        request = self._request(
            payload=payload,
            fallback_models=["xai/grok-4.6"],
        )

        with self.assertRaisesRegex(MillefeuilleContractError, "fallback.policy"):
            client.execute(
                request=request,
                input_payload=payload,
                output_validator=self._validate_canary,
            )
        self.assertEqual(runner.commands, [])

    def test_request_and_payload_binding_are_checked_before_any_process(self):
        payload = b'{"return":{"canary":"ok"}}'
        runner = _QueuedRunner([])
        client = OpenClawModelClient(command_runner=runner)
        request = self._request(payload=payload)
        drifted = deepcopy(payload)
        drifted += b" "

        with self.assertRaisesRegex(MillefeuilleContractError, "input drift"):
            client.execute(
                request=request,
                input_payload=drifted,
                output_validator=self._validate_canary,
            )
        self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main()
