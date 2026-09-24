"""Tool-free OpenClaw subscription-OAuth model execution boundary.

This adapter uses OpenClaw's isolated ``agent exec`` capability with a
pinned tool-disabled config and stdin-only prompt transport. It resolves saved
agent authentication without sharing the live session database.
Prompts and raw model output remain transient; only their SHA-256 bindings enter
the provider-neutral executor result envelope.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_executor import (
    materialize_model_executor_result,
    validate_model_executor_request,
    verify_model_executor_input,
)

_MAX_OPENCLAW_STDOUT_BYTES = 4 * 1024 * 1024
_MAX_OPENCLAW_STDERR_BYTES = 256 * 1024
_SUPPORTED_RUNTIME_MODEL = "openai/gpt-5.6-sol"
_SUPPORTED_AUTH_DB_SCHEMA = 19
_AGENT_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "ALL_PROXY",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "NODE_EXTRA_CA_CERTS",
        "NO_PROXY",
        "OPENCLAW_AUTH_PROFILE_SECRET_DIR",
        "OPENCLAW_HOME",
        "OPENCLAW_STATE_DIR",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "USER",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


@dataclass(frozen=True)
class OpenClawModelExecution:
    """One validated result envelope plus transient successful output."""

    result: dict[str, Any]
    output: bytes | None


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
OutputValidator = Callable[[Any], None]


class _OutputLimitExceeded(subprocess.SubprocessError):
    """A child exceeded its bounded in-memory output allowance."""


class _AuthSnapshotUnavailableError(ValueError):
    """The selected access credential cannot safely cover this run."""


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop the isolated child group, including runtime/provider descendants."""

    if os.name == "nt":
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        with suppress(ProcessLookupError):
            process.kill()
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def _bounded_run(
    command: list[str],
    *,
    input: str | None = None,
    capture_output: bool = True,
    text: bool = True,
    encoding: str = "utf-8",
    timeout: float,
    check: bool = False,
    env: Mapping[str, str] | None = None,
    max_stdout_bytes: int = _MAX_OPENCLAW_STDOUT_BYTES,
    max_stderr_bytes: int = _MAX_OPENCLAW_STDERR_BYTES,
) -> subprocess.CompletedProcess[str]:
    """Run a child with live stdout/stderr caps and no unbounded capture."""

    if not capture_output or not text or encoding != "utf-8":
        raise ValueError("bounded OpenClaw runner requires UTF-8 text capture")
    input_bytes = input.encode("utf-8") if input is not None else None
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None,
        start_new_session=os.name != "nt",
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
    ) as process:
        assert process.stdout is not None and process.stderr is not None
        stdout = bytearray()
        stderr = bytearray()
        overflow = threading.Event()
        read_errors: list[OSError] = []

        def collect(stream: Any, buffer: bytearray, limit: int) -> None:
            try:
                while chunk := os.read(stream.fileno(), 64 * 1024):
                    if len(buffer) + len(chunk) > limit:
                        overflow.set()
                        _kill_process_tree(process)
                        return
                    buffer.extend(chunk)
            except OSError as exc:
                read_errors.append(exc)

        def send_input() -> None:
            assert input_bytes is not None and process.stdin is not None
            try:
                with process.stdin:
                    for offset in range(0, len(input_bytes), 64 * 1024):
                        view = memoryview(input_bytes)[offset : offset + 64 * 1024]
                        while view:
                            view = view[os.write(process.stdin.fileno(), view) :]
            except (BrokenPipeError, OSError):
                # The child may reject stdin; its exit status remains authoritative.
                pass

        readers = [
            threading.Thread(
                target=collect,
                args=(process.stdout, stdout, max_stdout_bytes),
                daemon=True,
            ),
            threading.Thread(
                target=collect,
                args=(process.stderr, stderr, max_stderr_bytes),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        writer = (
            threading.Thread(target=send_input, daemon=True)
            if input_bytes is not None
            else None
        )
        if writer is not None:
            writer.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
            raise
        finally:
            for thread in [*readers, *([writer] if writer is not None else [])]:
                thread.join(timeout=1)
            if any(reader.is_alive() for reader in readers):
                _kill_process_tree(process)
                for reader in readers:
                    reader.join(timeout=1)
        if overflow.is_set():
            raise _OutputLimitExceeded("OpenClaw child output limit exceeded")
        if any(reader.is_alive() for reader in readers) or read_errors:
            raise subprocess.SubprocessError("OpenClaw child output was incomplete")
        completed = subprocess.CompletedProcess(
            args=command,
            returncode=returncode,
            stdout=stdout.decode(encoding),
            stderr=stderr.decode(encoding),
        )
        if check:
            completed.check_returncode()
        return completed


def _read_agent_local_oauth_access(
    agent_dir: str, profile_ref: str, *, min_valid_seconds: float
) -> dict[str, Any] | None:
    """Check the store that isolated ``agent exec`` can actually read.

    OpenClaw's ordinary model status includes shared OAuth profiles, while
    ``agent exec`` only admits portable static credentials from that shared
    store. Its selected OAuth profile therefore needs an agent-local entry.
    Read only the selected credential. Never load other profiles into Python.
    """

    database = Path(agent_dir) / "openclaw-agent.sqlite"
    if not database.is_file() or database.is_symlink():
        return None
    profile_path = f"$.profiles.{json.dumps(profile_ref)}"
    try:
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
        ) as connection:
            connection.execute("PRAGMA query_only = ON")
            if connection.execute("PRAGMA user_version").fetchone()[0] != (
                _SUPPORTED_AUTH_DB_SCHEMA
            ):
                return None
            row = connection.execute(
                "SELECT json_extract(store_json, ?) FROM auth_profile_store "
                "WHERE store_key = 'primary'",
                (profile_path,),
            ).fetchone()
        if row is None or not isinstance(row[0], str):
            return None
        credential = _strict_json_loads(row[0])
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(credential, dict):
        return None
    if credential.get("type") != "oauth" or credential.get("provider") != "openai":
        return None
    access = credential.get("access")
    expires = credential.get("expires")
    if not isinstance(access, str) or not access.strip():
        return None
    if type(expires) not in (int, float) or not (
        expires > (time.time() + min_valid_seconds + 90) * 1000
    ):
        return None
    # A short-lived access credential is enough for this single run. Omitting
    # refresh makes rotation from the isolated store impossible.
    allowed = (
        "type",
        "provider",
        "access",
        "expires",
        "accountId",
        "chatgptPlanType",
        "email",
    )
    return {key: credential[key] for key in allowed if key in credential}


def _has_agent_local_oauth_profile(agent_dir: str, profile_ref: str) -> bool:
    """Check that an agent-local OAuth access credential can cover a short run."""

    return (
        _read_agent_local_oauth_access(agent_dir, profile_ref, min_valid_seconds=30)
        is not None
    )


def _write_oauth_access_snapshot(
    agent_dir: str,
    profile_ref: str,
    snapshot_agent_dir: Path,
    *,
    min_valid_seconds: float,
) -> None:
    """Give agent exec only the checked OAuth access token, never live auth."""

    credential = _read_agent_local_oauth_access(
        agent_dir, profile_ref, min_valid_seconds=min_valid_seconds
    )
    if credential is None:
        raise _AuthSnapshotUnavailableError(
            "agent-local OAuth access is unavailable or expiring"
        )
    snapshot_agent_dir.mkdir(mode=0o700)
    database = snapshot_agent_dir / "openclaw-agent.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(f"PRAGMA user_version = {_SUPPORTED_AUTH_DB_SCHEMA}")
        connection.execute(
            "CREATE TABLE auth_profile_store (store_key TEXT NOT NULL PRIMARY KEY, "
            "store_json TEXT NOT NULL, updated_at INTEGER NOT NULL) STRICT"
        )
        connection.execute(
            "CREATE TABLE auth_profile_state (state_key TEXT NOT NULL PRIMARY KEY, "
            "state_json TEXT NOT NULL, updated_at INTEGER NOT NULL) STRICT"
        )
        connection.execute(
            "INSERT INTO auth_profile_store VALUES ('primary', ?, ?)",
            (
                json.dumps({"version": 1, "profiles": {profile_ref: credential}}),
                int(time.time() * 1000),
            ),
        )
        connection.execute(
            "INSERT INTO auth_profile_state VALUES ('primary', ?, ?)",
            (
                json.dumps({"version": 1, "order": {"openai": [profile_ref]}}),
                int(time.time() * 1000),
            ),
        )
        connection.commit()
    database.chmod(0o600)


class OpenClawModelClient:
    """Execute GPT-only, no-fallback requests through saved subscription OAuth."""

    def __init__(
        self,
        *,
        agent_id: str = "franck",
        executable: str = "openclaw",
        command_runner: CommandRunner = _bounded_run,
    ) -> None:
        if not isinstance(agent_id, str) or _AGENT_ID.fullmatch(agent_id) is None:
            raise MillefeuilleContractError("OpenClaw agent_id is invalid")
        if not executable or not executable.strip():
            raise MillefeuilleContractError("OpenClaw executable must be non-empty")
        self.agent_id = agent_id
        self.executable = executable
        self._run = command_runner

    def preflight_auth(self, *, timeout_seconds: float = 30) -> None:
        """Refuse an unavailable isolated OAuth route before canary reservation."""

        try:
            self._require_single_oauth_profile(
                _SUPPORTED_RUNTIME_MODEL, timeout_seconds=timeout_seconds
            )
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise MillefeuilleContractError(
                "OpenClaw agent-local GPT OAuth is unavailable"
            ) from exc

    def execute(
        self,
        *,
        request: dict[str, Any],
        input_payload: bytes,
        output_validator: OutputValidator,
    ) -> OpenClawModelExecution:
        """Run one exact model request and return payload-free evidence.

        Explicit executor fallbacks are rejected for this one-shot adapter. Callers
        must make any later model attempt as a separately evidenced request.
        """

        normalized = validate_model_executor_request(request)
        verify_model_executor_input(normalized, input_payload)
        if normalized["requested_model"] != _SUPPORTED_RUNTIME_MODEL:
            raise MillefeuilleContractError(
                "OpenClaw live adapter currently supports only openai/gpt-5.6-sol"
            )
        if normalized["fallback"] != {"policy": "none", "models": []}:
            raise MillefeuilleContractError(
                "OpenClaw model execution requires fallback.policy none"
            )
        if not callable(output_validator):
            raise MillefeuilleContractError("output_validator must be callable")
        try:
            prompt = input_payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise MillefeuilleContractError(
                "OpenClaw model input must be valid UTF-8"
            ) from exc
        if not prompt.strip():
            raise MillefeuilleContractError(
                "OpenClaw model input must contain non-whitespace text"
            )

        if normalized["timeout_seconds"] < 10:
            raise MillefeuilleContractError(
                "OpenClaw execution requires timeout_seconds >= 10"
            )
        started_at = _utc_now()
        deadline = time.monotonic() + normalized["timeout_seconds"] - 1
        try:
            (
                auth_profile_ref,
                agent_dir,
            ) = self._require_single_oauth_profile(
                normalized["requested_model"],
                timeout_seconds=max(0.001, deadline - time.monotonic()),
            )
        except subprocess.TimeoutExpired:
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="timeout",
                auth_profile_ref=None,
                actual_model=None,
                schema_validation_status="not_run",
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="auth_unavailable",
                auth_profile_ref=None,
                actual_model=None,
                schema_validation_status="not_run",
            )

        remaining = deadline - time.monotonic()
        if remaining <= 3:
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="timeout",
                auth_profile_ref=auth_profile_ref,
                actual_model=None,
                schema_validation_status="not_run",
            )
        try:
            with tempfile.TemporaryDirectory(
                prefix="millefeuille-openclaw-"
            ) as temporary_dir:
                snapshot_agent_dir = Path(temporary_dir) / "auth-agent"
                _write_oauth_access_snapshot(
                    agent_dir,
                    auth_profile_ref,
                    snapshot_agent_dir,
                    min_valid_seconds=remaining,
                )
                # The bundled OpenAI provider is a plugin. Keep it loaded
                # while the separate tool policy denies every tool.
                config_path = Path(temporary_dir) / "openclaw.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "auth": {
                                "profiles": {
                                    auth_profile_ref: {
                                        "provider": normalized["requested_model"].split(
                                            "/", 1
                                        )[0],
                                        "mode": "oauth",
                                    }
                                },
                                "order": {
                                    normalized["requested_model"].split("/", 1)[0]: [
                                        auth_profile_ref
                                    ]
                                },
                            },
                            "agents": {
                                "entries": {
                                    self.agent_id: {"agentDir": str(snapshot_agent_dir)}
                                },
                                "defaults": {
                                    "systemAgent": {"agentId": self.agent_id},
                                    "model": {
                                        "primary": normalized["requested_model"],
                                        "fallbacks": [],
                                    },
                                    "models": {
                                        normalized["requested_model"]: {
                                            "agentRuntime": {"id": "openclaw"}
                                        }
                                    },
                                },
                            },
                            "tools": {
                                "deny": ["*"],
                                "codeMode": {"enabled": False},
                            },
                            "plugins": {
                                "enabled": True,
                                "allow": ["openai"],
                                "entries": {
                                    "openai": {"enabled": True},
                                    "memory-core": {"enabled": False},
                                },
                            },
                        },
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                config_path.chmod(0o600)
                command = [
                    self.executable,
                    "agent",
                    "exec",
                    "--config",
                    str(config_path),
                    "--cwd",
                    temporary_dir,
                    "--message-file",
                    "-",
                    "--model",
                    normalized["requested_model"],
                    "--thinking",
                    normalized["thinking"],
                    "--timeout",
                    str(max(1, int(remaining - 2))),
                    "--no-auth-env-only",
                    "--json",
                ]
                completed = self._run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=max(0.001, deadline - time.monotonic() - 1),
                    check=False,
                    env=_execution_environment(temporary_dir),
                )
        except (_AuthSnapshotUnavailableError, sqlite3.Error):
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="auth_unavailable",
                auth_profile_ref=auth_profile_ref,
                actual_model=None,
                schema_validation_status="not_run",
            )
        except subprocess.TimeoutExpired:
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="timeout",
                auth_profile_ref=auth_profile_ref,
                actual_model=None,
                schema_validation_status="not_run",
            )
        except _OutputLimitExceeded:
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="invalid_response",
                auth_profile_ref=auth_profile_ref,
                actual_model=None,
                schema_validation_status="not_run",
            )
        except UnicodeError:
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="invalid_response",
                auth_profile_ref=auth_profile_ref,
                actual_model=None,
                schema_validation_status="not_run",
            )
        except (OSError, subprocess.SubprocessError):
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="provider_error",
                auth_profile_ref=auth_profile_ref,
                actual_model=None,
                schema_validation_status="not_run",
            )

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if (
            len(stdout.encode("utf-8")) > _MAX_OPENCLAW_STDOUT_BYTES
            or len(stderr.encode("utf-8")) > _MAX_OPENCLAW_STDERR_BYTES
        ):
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="invalid_response",
                auth_profile_ref=auth_profile_ref,
                actual_model=None,
                schema_validation_status="not_run",
            )
        if completed.returncode != 0:
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="provider_error",
                auth_profile_ref=auth_profile_ref,
                actual_model=None,
                schema_validation_status="not_run",
            )

        try:
            response = _strict_json_loads(stdout)
            actual_model, output = _validate_openclaw_response(
                response,
                requested_model=normalized["requested_model"],
            )
        except (
            json.JSONDecodeError,
            MillefeuilleContractError,
            UnicodeError,
            ValueError,
        ):
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="invalid_response",
                auth_profile_ref=auth_profile_ref,
                actual_model=None,
                schema_validation_status="not_run",
            )
        if actual_model != normalized["requested_model"]:
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="actual_model_mismatch",
                auth_profile_ref=auth_profile_ref,
                actual_model=actual_model,
                schema_validation_status="not_run",
            )

        try:
            parsed_output = _strict_json_loads(output)
            output_validator(parsed_output)
        except Exception:
            # Validator errors fail closed; process-control exceptions propagate.
            return self._failed_execution(
                request=normalized,
                started_at=started_at,
                failure_code="schema_validation_failed",
                auth_profile_ref=auth_profile_ref,
                actual_model=actual_model,
                schema_validation_status="failed",
            )

        completed_at = _utc_now()
        output_bytes = output.encode("utf-8")
        attempt = _attempt(
            model=normalized["requested_model"],
            actual_model=actual_model,
            status="succeeded",
            started_at=started_at,
            completed_at=completed_at,
            auth_profile_ref=auth_profile_ref,
            failure_code=None,
        )
        result = materialize_model_executor_result(
            request=normalized,
            status="succeeded",
            actual_model=actual_model,
            auth_profile_ref=auth_profile_ref,
            started_at=started_at,
            completed_at=completed_at,
            attempts=[attempt],
            fallback_reason=None,
            schema_validation_status="passed",
            output_sha256=_sha256(output_bytes),
            output_bytes=len(output_bytes),
            usage=None,
            cost_micro_usd=None,
        )
        return OpenClawModelExecution(result=result, output=output_bytes)

    def _require_single_oauth_profile(
        self, requested_model: str, *, timeout_seconds: float
    ) -> tuple[str, str]:
        provider = requested_model.split("/", 1)[0]
        command = [
            self.executable,
            "models",
            "--agent",
            self.agent_id,
            "status",
            "--json",
        ]
        completed = self._run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=min(120, timeout_seconds),
            check=False,
            env=_oauth_only_environment(),
        )
        if completed.returncode != 0:
            raise ValueError("OpenClaw auth status failed")
        if (
            len((completed.stdout or "").encode("utf-8")) > _MAX_OPENCLAW_STDOUT_BYTES
            or len((completed.stderr or "").encode("utf-8"))
            > _MAX_OPENCLAW_STDERR_BYTES
        ):
            raise ValueError("OpenClaw auth status output exceeded limit")
        payload = _required_mapping(
            _strict_json_loads(completed.stdout or ""), "OpenClaw auth status"
        )
        if payload.get("agentId") != self.agent_id:
            raise ValueError("OpenClaw auth status agent drift")
        agent_dir = payload.get("agentDir")
        if (
            not isinstance(agent_dir, str)
            or not agent_dir.strip()
            or not Path(agent_dir).is_absolute()
        ):
            raise ValueError("OpenClaw agent directory is unavailable")
        config_path = payload.get("configPath")
        if (
            not isinstance(config_path, str)
            or not config_path.strip()
            or not Path(config_path).is_absolute()
            or len(Path(agent_dir).parents) < 3
            or Path(config_path).parent != Path(agent_dir).parents[2]
        ):
            raise ValueError("OpenClaw stored OAuth state directory is unavailable")
        auth = _required_mapping(payload.get("auth"), "OpenClaw auth status")
        providers = auth.get("providers")
        if not isinstance(providers, list):
            raise ValueError("OpenClaw auth providers are unavailable")
        matches = [
            row
            for row in providers
            if isinstance(row, Mapping) and row.get("provider") == provider
        ]
        if len(matches) != 1:
            raise ValueError("OpenClaw provider auth is unavailable")
        row = matches[0]
        effective = _required_mapping(row.get("effective"), "effective auth")
        profiles = _required_mapping(row.get("profiles"), "auth profiles")
        if effective.get("kind") != "profiles":
            raise ValueError("OpenClaw provider is not using an auth profile")
        if not (
            profiles.get("count") == 1
            and profiles.get("oauth") == 1
            and profiles.get("token") == 0
            and profiles.get("apiKey") == 0
        ):
            raise ValueError("OpenClaw provider is not OAuth-only")
        labels = profiles.get("labels")
        if not isinstance(labels, list) or len(labels) != 1:
            raise ValueError("OpenClaw OAuth profile identity is ambiguous")
        label = labels[0]
        if not isinstance(label, str) or "=" not in label:
            raise ValueError("OpenClaw OAuth profile identity is invalid")
        profile_ref = label.split("=", 1)[0].strip()
        if not profile_ref.startswith(f"{provider}:"):
            raise ValueError("OpenClaw OAuth profile provider drift")
        oauth = _required_mapping(auth.get("oauth"), "OAuth status")
        oauth_profiles = oauth.get("profiles")
        if not isinstance(oauth_profiles, list):
            raise ValueError("OpenClaw OAuth profile metadata is unavailable")
        oauth_matches = [
            candidate
            for candidate in oauth_profiles
            if isinstance(candidate, Mapping)
            and candidate.get("profileId") == profile_ref
            and candidate.get("provider") == provider
            and candidate.get("type") == "oauth"
            and candidate.get("source") == "store"
            and candidate.get("status") in {"ok", "valid", "expiring"}
        ]
        if len(oauth_matches) != 1:
            raise ValueError("OpenClaw stored OAuth profile is unavailable")
        if not _has_agent_local_oauth_profile(agent_dir, profile_ref):
            raise ValueError("OpenClaw agent-local OAuth profile is unavailable")
        shell_fallback = _required_mapping(
            auth.get("shellEnvFallback"), "shell environment fallback"
        )
        if shell_fallback.get("enabled") is not False or shell_fallback.get(
            "appliedKeys"
        ) not in (None, []):
            raise ValueError("OpenClaw environment auth fallback is active")
        return profile_ref, agent_dir

    def _failed_execution(
        self,
        *,
        request: dict[str, Any],
        started_at: str,
        failure_code: str,
        auth_profile_ref: str | None,
        actual_model: str | None,
        schema_validation_status: str,
    ) -> OpenClawModelExecution:
        completed_at = _utc_now()
        attempt = _attempt(
            model=request["requested_model"],
            actual_model=actual_model,
            status="failed",
            started_at=started_at,
            completed_at=completed_at,
            auth_profile_ref=auth_profile_ref,
            failure_code=failure_code,
        )
        result = materialize_model_executor_result(
            request=request,
            status="failed",
            actual_model=None,
            auth_profile_ref=auth_profile_ref,
            started_at=started_at,
            completed_at=completed_at,
            attempts=[attempt],
            fallback_reason=None,
            schema_validation_status=schema_validation_status,
            output_sha256=None,
            output_bytes=None,
            usage=None,
            cost_micro_usd=None,
        )
        return OpenClawModelExecution(result=result, output=None)


def _validate_openclaw_response(
    payload: object,
    *,
    requested_model: str,
) -> tuple[str, str]:
    response = _required_mapping(payload, "OpenClaw agent exec response")
    if response.get("ok") is not True or response.get("status") != "ok":
        raise MillefeuilleContractError("OpenClaw agent exec did not succeed")
    provider = response.get("provider")
    model = response.get("model")
    if not isinstance(provider, str) or not isinstance(model, str):
        raise MillefeuilleContractError("OpenClaw model attribution is missing")
    actual_model = f"{provider}/{model}"

    tools = _required_mapping(response.get("toolSummary"), "OpenClaw tool summary")
    if type(tools.get("calls")) is not int or tools["calls"] != 0:
        raise MillefeuilleContractError("OpenClaw agent exec used a tool")
    if tools.get("tools") not in (None, []):
        raise MillefeuilleContractError("OpenClaw tool list is inconsistent")
    bridge = _required_mapping(response.get("bridgeCalls"), "OpenClaw bridge calls")
    if any(
        type(bridge.get(key)) is not int or bridge[key] != 0
        for key in ("search", "describe", "call")
    ):
        raise MillefeuilleContractError("OpenClaw agent exec used a tool bridge")
    if response.get("codeModeEngaged") is not False:
        raise MillefeuilleContractError("OpenClaw agent exec engaged code mode")
    turns = response.get("assistantTurns")
    if type(turns) is not int or turns != 1:
        raise MillefeuilleContractError("OpenClaw agent exec used multiple turns")

    payloads = response.get("payloads")
    if not isinstance(payloads, list) or len(payloads) != 1:
        raise MillefeuilleContractError("OpenClaw response must contain one payload")
    output = _required_mapping(payloads[0], "OpenClaw payload")
    if output.get("mediaUrl") is not None or output.get("mediaUrls") not in (None, []):
        raise MillefeuilleContractError("OpenClaw response contains media")
    text = output.get("text")
    if not isinstance(text, str) or not text.strip():
        raise MillefeuilleContractError("OpenClaw response text is empty")
    if response.get("final") != text:
        raise MillefeuilleContractError("OpenClaw final text mismatch")
    if actual_model != requested_model:
        return actual_model, text
    return actual_model, text


def _required_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _strict_json_loads(value: str) -> Any:
    """Reject ambiguous or non-standard JSON before validating model evidence."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("JSON object has a duplicate field")
            result[key] = item
        return result

    def reject_constant(_value: str) -> Any:
        raise ValueError("JSON contains a non-standard constant")

    return json.loads(
        value,
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )


def _attempt(
    *,
    model: str,
    actual_model: str | None,
    status: str,
    started_at: str,
    completed_at: str,
    auth_profile_ref: str | None,
    failure_code: str | None,
) -> dict[str, Any]:
    return {
        "sequence": 1,
        "model": model,
        "actual_model": actual_model,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "auth_class": ("subscription_oauth" if auth_profile_ref is not None else None),
        "auth_profile_ref": auth_profile_ref,
        "failure_code": failure_code,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha256(payload: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _oauth_only_environment() -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items() if key in _CHILD_ENV_ALLOWLIST
    }
    return env


def _execution_environment(temporary_dir: str) -> dict[str, str]:
    """Keep the snapshot separate from every live OpenClaw auth store."""

    env = _oauth_only_environment()
    isolated = Path(temporary_dir)
    locations = {
        "HOME": "home",
        "OPENCLAW_HOME": "openclaw-home",
        "XDG_CONFIG_HOME": "xdg-config",
        "XDG_DATA_HOME": "xdg-data",
        "TMPDIR": "tmp",
        "TMP": "tmp",
        "TEMP": "tmp",
    }
    for key, suffix in locations.items():
        path = isolated / suffix
        path.mkdir(mode=0o700, exist_ok=True)
        env[key] = str(path)
    auth_state_dir = isolated / "auth-state"
    auth_state_dir.mkdir(mode=0o700)
    env["OPENCLAW_STATE_DIR"] = str(auth_state_dir)
    env.pop("OPENCLAW_AUTH_PROFILE_SECRET_DIR", None)
    env["OPENCLAW_AUTH_STORE_READONLY"] = "1"
    return env
