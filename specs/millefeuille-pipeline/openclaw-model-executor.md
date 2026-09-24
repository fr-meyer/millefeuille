# Bounded OpenClaw model adapter

The `OpenClawModelClient` executes one provider-neutral model-executor request
through the installed OpenClaw `agent exec` command. It is a low-level adapter;
no pipeline stage calls it automatically. The current executable lane is
`openai/gpt-5.6-sol` through subscription OAuth; xAI/Grok execution is deferred
until a subscription is active and a separate reviewed change enables it.
A live GPT call still requires the run's exact approved-live scope and
output-contract validation.

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
  agent directory only as the stored-auth source. The execution child receives
  private temporary home, state, XDG, and temp locations; only the narrow OAuth
  secret reference location is preserved when configured.
- The child environment passes only a small operational allowlist. Provider API
  keys and Codex API keys are not passed. Auth lookup and model execution share one request deadline. Child stdout
  and stderr are capped during capture, including the auth lookup.

## Result boundary

Success requires the stable `agent exec --json` envelope to report `ok`, exact
provider/model attribution, one non-media text payload matching `final`, zero
outer and bridge tool calls, no Code Mode, and exactly one assistant turn. The
caller then validates the parsed JSON against its output contract. Only the
output bytes and a sanitized executor result envelope return to the caller.
Duplicate object keys and non-standard JSON constants are rejected in auth,
outer execution, and model-output envelopes before validation.
Malformed or ambiguous evidence fails without an output binding. Explicit
fallback requests are rejected by this one-shot adapter; each later attempt
requires its own request and provenance.

The adapter has offline mock coverage for OAuth refusal, request/input drift,
model mismatch, schema failure, missing or positive tool-use evidence,
auth timeouts, bounded capture, and prompt transport. GCP validation used OpenClaw 2026.9.4 config validation and
read-only auth status. No live model call or paper text was used for this slice.

## GPT-only acceptance canary

`millefeuille.domain.gpt_oauth_canary.run_gpt_oauth_canary` is a supervised,
single-call acceptance boundary. It sends one fixed synthetic JSON prompt to
`openai/gpt-5.6-sol` with `thinking=xhigh`, no fallback, no tools, and no paper
content. It accepts only an exact approved-live operator packet and MF-100
receipt for the local `gpt-oauth-canary` fixture, one item, one `model.execute`
operation, one provider call, a zero incremental API-spend cap, private local
audit root, and explicit disposal/stop controls. The zero cap relies on the
adapter's subscription-OAuth-only route; any reported nonzero provider cost
fails the proof. This is not a cost estimator for paper execution.

After Franck approves the exact packet and receipt through the authenticated
manual channel, the administrator publishes their identities into the fixed
root-owned `/etc/millefeuille/gpt-oauth-canary-approval.json` store inside the
GCP container. The model-running user cannot write that store or the replay
ledger. A self-computed receipt digest or caller-supplied file cannot establish
approval. The runner checks the packet and OAuth readiness, then asks a
one-shot administrator broker over a root-owned Unix socket for a durable
reservation. The broker independently validates the trusted store and exact
receipt scope. Its SQLite transaction in the root-owned control directory
reserves the sanitized receipt audit before it grants the first possible model
call. A missing broker or approval store fails closed.
A failed or interrupted call leaves that receipt consumed and requires a new
approval. Successful results return only the validated executor envelope and
approval identities; provider output is never returned or persisted. The
root-owned control directory must not be writable by the model user, and its
SQLite database has mode `0600`. No source pack, Zotero,
OpenKB, or index write is performed.

This canary does not execute prepared page/section/full-paper work units.
That integration still requires an approved batch executor, output contracts,
acceptance, provenance materialization, and separate durable-write receipts.
