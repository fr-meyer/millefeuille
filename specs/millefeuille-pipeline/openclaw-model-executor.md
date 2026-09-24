# Bounded OpenClaw model adapter

The `OpenClawModelClient` executes one provider-neutral model-executor request
through the installed OpenClaw `agent exec` command. It is a low-level adapter;
no pipeline stage calls it automatically. A live model call still requires the
run's approved-live scope and output-contract validation.

## Input and execution boundary

- The caller supplies the exact UTF-8 prompt separately from the payload-free
  executor request. Its bytes must match the request's SHA-256 and byte count.
- The prompt is sent through process stdin with `--message-file -`. It is never
  placed in command arguments, environment variables, the temporary config, or
  a committed file.
- The adapter checks the selected agent's OpenClaw auth status before dispatch.
  Exactly one stored OAuth profile for the requested provider must be effective;
  API-key, token, shell-environment fallback, and ambiguous profiles fail closed.
- A mode-0600 temporary config pins the requested model to OpenClaw's own
  runtime, disables fallback and all tools/plugins, and selects the checked
  agent directory only as the stored-auth source. `agent exec` strips inherited
  agent database locations from its run config and uses temporary run state.
- The child environment passes only a small operational allowlist. Provider API
  keys and Codex API keys are not passed. The subprocess and OpenClaw run each
  have a deadline inside the request's timeout.

## Result boundary

Success requires the stable `agent exec --json` envelope to report `ok`, exact
provider/model attribution, one non-media text payload matching `final`, zero
outer and bridge tool calls, no Code Mode, and at most one assistant turn. The
caller then validates the parsed JSON against its output contract. Only the
output bytes and a sanitized executor result envelope return to the caller.
Malformed or ambiguous evidence fails without an output binding. Explicit
fallback requests are rejected by this one-shot adapter; each later attempt
requires its own request and provenance.

The adapter has offline mock coverage for OAuth refusal, request/input drift,
model mismatch, schema failure, missing or positive tool-use evidence, and
prompt transport. GCP validation used OpenClaw 2026.9.4 config validation and
read-only auth status. No live model call or paper text was used for this slice.
