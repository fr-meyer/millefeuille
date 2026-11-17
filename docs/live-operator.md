# Installed summary/card operator

`millefeuille live-operator` is a narrow unprivileged POSIX controller over the existing trusted OAuth executors and publication sockets. No repository checkout or script is needed. It never provisions brokers, elevates, selects broker paths, changes auth, or weakens the generic offline command hardstop. Root execution is refused. Administrator-installed trusted broker code and fixed root-owned controls remain a separate prerequisite; never elevate a user-writable runtime.

## Offline inspection

All paths below are illustrative. Supply already verified route, structure and canonical preparation, not raw PDFs or fabricated fixtures.

```sh
millefeuille live-operator summary plan \
  --route-evidence /PRIVATE/route.json --structure-evidence /PRIVATE/structure.json \
  --preparation /PRIVATE/preparation.json --source-pack-root /PRIVATE/source-packs \
  --artifact-root /PRIVATE/artifacts --run-id NEW_RUN
```

Replace `plan` with `preflight` and add `--packet /PRIVATE/execution-packet.json --receipt /PRIVATE/execution-receipt.json` to validate exact execution scope. These actions make no auth/provider/network calls and do not check actual OAuth or root readiness. Plans return only digest/count, never prompts. Preflight success is NOT execution authority; the existing trusted executor/broker still independently validates and consumes the exact one-use receipt.

Use the existing reviewed Astra preparation, not old Sol approvals. The current real trial already has manifest `sha256:071f3a21bbf05a49847f13d8520abe95fd4eae607a53685d0214a511b61cba8b`, preparation `sha256:f13e927d17c25f4ed24c3a852d7c5dd452be66ce3015047c3f30017a8bfd59e2`: 9 page + 1 paper requests, 46 sections retained but excluded, 94090 prompt bytes. These hashes are provenance, not permission. Do not repeat OCR or reprepare immutable reviewed artifacts.

## Explicit execution and separately approved publication

With human-initiated intended Franck-local OAuth setup, legitimate trusted broker availability, and a fresh exact execution receipt, replace `plan` with `execute-publish`, adding `--mode approved-live --packet ... --receipt ...`. No fallback/model override is exposed: current domain contracts bind Astra/xhigh/subscription OAuth. Do not set readiness markers to claim authentication; actual adapter preflight remains mandatory.

The process executes once, validates all outputs, keeps them in memory and emits `awaiting-separate-publication-approval` with only output/usage/provenance fingerprints. In a separate trusted approval flow the operator reviews that concrete scope and provisions a DIFFERENT exact publication receipt and corresponding trusted root authorization. Within `--publication-wait-seconds` (1..120, default120), send exactly one JSON line on the controller stdin:

```json
{"packet":"/PRIVATE/publication-packet.json","receipt":"/PRIVATE/publication-receipt.json"}
```

This line names ordinary evidence files, not a broker/code location. It never grants root trust. Publication uses the fixed authenticated broker and existing separate write-scope/replay checks. Prepare the attended approval workflow BEFORE execution: output digests cannot be preapproved before actual results. No receipt generation or trust provisioning is provided by this CLI. If the window cannot be met, do not start. EOF, timeout, interruption, any validation/dispatch error or ambiguous publication stops without retry. Consumed authority stays consumed. Failure output says publication not confirmed: inspect existing trusted receipts/readback before any separately reviewed recovery, never replay blindly.

Successful summary publication emits a metadata-only `summary-publication-identity` object. Save only its `identity` member as private JSON for card input. It includes bundle/receipt/source identities and byte counts, not model output. `published-not-accepted` is intentionally not run acceptance.

For a card use the same surface with `card`, a distinct card run ID, and `--publication-identity /PRIVATE/summary-identity.json`. Card planning verifies the real published summary package. Card execution and card publication each require their own exact approval; ten summary calls never authorize either.

No prompt/output bodies are persisted by the controller or printed. Original executor temp cleanup remains intact. All transient references are dropped on return/failure; ordinary immutable Python bytes cannot be securely zeroized, and process exit is not a cryptographic erasure guarantee. This CLI is a short-lived process, not a resumable daemon. Do not add debug/traceback or raw payload logging. POSIX-only is explicit, not a Windows activation claim.

## Ordinary termination and hard-crash limits

During the CLI operation, scoped SIGTERM/SIGHUP/SIGINT handlers initiate one
controlled cancellation. Further ordinary termination signals are ignored only
while that cancellation unwinds, so they cannot interrupt child cleanup or
temporary snapshot disposal. The adapter kills its isolated process group and
waits/reaps its direct child before leaving the temporary-auth context. Previous
handlers are restored on exit; unrelated process-control exceptions are not
converted to provider results. Cancellation returns sanitized stopped metadata,
with publication not confirmed and no automatic retry, including cancellation
after a publication request may have reached its broker. Review the trusted
receipt/readback before any separately authorized recovery.

SIGKILL, process/kernel crash, power loss and an unresponsive kernel cannot
guarantee Python unwinding, child reaping or temporary-file disposal. No cleanup
scanner, credential recovery or resumable daemon is introduced.

## Limits

No automatic accepted-run assembly, live classification or live Zotero writeback is added. Existing acceptance still requires genuine card/index/handoff/duplicate evidence; human-reviewed classification with a released run-specific taxonomy and writeback preview remain supported. No fake acceptance or taxonomy result is generated. Live qualification, production deployment and external approvals remain separate.
