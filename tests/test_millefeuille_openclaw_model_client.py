"""Tests for the tool-free OpenClaw subscription-OAuth model boundary."""

from __future__ import annotations

from copy import deepcopy
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from millefeuille.clients.openclaw_model_client import OpenClawModelClient
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
        "agentDir": "/home/node/.openclaw/agents/franck/agent",
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
        self.assertEqual(
            command[command.index("--model") + 1], "openai/gpt-5.6-sol"
        )
        self.assertEqual(command[command.index("--thinking") + 1], "xhigh")
        self.assertNotIn(payload.decode(), command)
        self.assertEqual(runner.kwargs[1]["input"], payload.decode())
        self.assertEqual(
            runner.runtime_configs[0]["tools"],
            {"deny": ["*"], "codeMode": {"enabled": False}},
        )
        self.assertEqual(runner.runtime_configs[0]["plugins"], {"enabled": False})
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

    def test_xai_success_is_attributed_to_exact_requested_model(self):
        payload = b'{"return":{"canary":"ok"}}'
        runner = _QueuedRunner(
            [
                _completed(_auth_status("xai")),
                _completed(
                    _model_response("xai", "grok-4.6", '{"canary":"ok"}')
                ),
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

    def test_actual_model_mismatch_fails_closed_without_output_binding(self):
        payload = b'{"return":{"canary":"ok"}}'
        runner = _QueuedRunner(
            [
                _completed(_auth_status("openai")),
                _completed(
                    _model_response("openai", "gpt-5.5", '{"canary":"ok"}')
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
