# Classification-only installed operator (v1)

This separate command never runs automatically after summary/card. It requires an already accepted exact run, its source/card/summary/handoff evidence, and a released single-run taxonomy lock. It cannot add/rename labels. Summary ten-call/card/historical Sol receipts do not authorize classification. Astra/xhigh/subscription OAuth, one call, no fallback/retry, zero priced-spend budget are fixed by existing model and new exact scope validation.

## Input metadata / offline commands

Save a private metadata JSON containing exactly these six STRING fields (no prompt bodies):

```json
{"source_pack_root":"/PRIVATE/source-packs","run_id":"FAKE_RUN","paper_id":"FAKE_PAPER","expected_selected_text_sha256":"sha256:FAKE_SHA256","taxonomy_lock_path":"/PRIVATE/source-packs/run/taxonomy-lock.json","handoff_path":"/PRIVATE/source-packs/run/handoff.jsonl"}
```

Use real verified identities/absolute paths under the corpus root; do not regenerate the existing reviewed paper artifacts.

```sh
millefeuille classification-operator plan --inputs /PRIVATE/classification-inputs.json
millefeuille classification-operator preflight --inputs /PRIVATE/classification-inputs.json --packet /PRIVATE/classification-execution-packet.json --receipt /PRIVATE/classification-execution-receipt.json
```

Both are metadata-only/no-auth/no-network/no-write. Preflight validates scope, not actual OAuth/root readiness. It emits a root-approval CONTRACT, not authority; writing that metadata as an ordinary user grants nothing. Existing generic offline stage hardstops are unchanged.

## Versioned administrator contract (not deployed)

New fixed one-shot root broker: `python -m millefeuille.domain.classification_live_broker`, from an independently reviewed administrator-installed runtime, NEVER from a user-writable checkout. No setup/elevation/systemd/config changes are performed by this feature. Root runtime/provisioning/live calls require separate explicit authorization.

Each client exchange and accepted server connection shares one monotonic30second
IO deadline across connect (client), partial header/body reads and partial sends.
No fragment or denial reply refreshes it. Server processing consumes the same
budget but is not asynchronously interrupted; if processing completes after
expiry, no response is sent and the outcome remains ambiguous/consumed. There
is no automatic operation retry or claim that an expired reply means no effect.
The listener accept timeout remains separately120seconds.

Fixed controls under root-owned `/etc/millefeuille`:
- `gpt-classification-v1.sock`: root-owned Unix socket, node peer checked via SO_PEERCRED.
- `gpt-classification-v1-execution.json`: separate exact model.classify approval.
- `gpt-classification-v1-publication.json`: separate exact classification.review publication approval.
- `gpt-classification-v1.sqlite3`: root-owned0600 durable, consumed-before-dispatch/effects receipt ledger. No ledger/receipt reset or automatic retry.

Declared OUTPUT_ROOT is `/var/lib/millefeuille/classification-output-v1`: administrator-owned0700 private single-cooperating-writer namespace, precreated separately. No arbitrary output-root argument or trust-root override exists. Three unique plan-hash-prefixed files are published: validated proposal, evidence consumable by the existing classifier, and payload-free provenance. Existing outputs are never overwritten. The existing exclusive publication primitive journals bounded per-file publication; it is NOT an all-files atomic transaction. Failures remain consumed and require explicit receipt/readback review, never auto rollback/retry.

Root approval records are canonical compact sorted UTF-8 JSON plus one newline. Exact fields: schema=`millefeuille-classification-root-approval/v1`, packet_digest, receipt_digest, inputs_sha256 (canonical exact metadata object), operation, output_root, approver_id, approved_at. `approval_record(inputs=...,packet=...,receipt=...)` renders this pure offline contract. Administrator files must satisfy the existing root-owned/no-follow0600 trusted reader; arbitrary user files are never accepted. Exact record verification happens BEFORE privileged source reads. It does not replace receipt expiry/scope validation.

Execution packet: mode approved-live; operation model.classify; run matches accepted plan; local-fixture adapter denotes local accepted evidence, not synthetic-only operation; selector=batch-manifest of `identity(plan)`; item_cap/resolved_item_count=1; roots bind source_pack_root and OUTPUT_ROOT; destination is exact OUTPUT_ROOT artifact-root target; targets additionally bind paper-id, analysis-run, classification-plan-v1, taxonomy-lock and computed preflight-scope. Provider=openai/gpt-6-astra/openclaw-subscription-oauth; limits1call/0microUSD; credential model-oauth/OPENCLAW_CODEX_OAUTH_READY; acceptance pass. The marker alone never grants authentication: actual adapter OAuth preflight is mandatory.

Publication packet changes operation to classification.review, adds classification-output-v1 target binding the emitted exact three-file digest, provider=None, credentials=[], calls=0. Both use exact stops auth-failure/model-mismatch/provider-error/schema-failure/source-drift/usage-missing, rollback stop-and-review, PDF not-applicable and temporary delete-after-run. Provider-payload disposal is never-persist for execution and not-applicable for publication. Neither authorizes persistence of raw prompts or arbitrary provider payloads. Receipt approver must be fr-meyer; all receipt/packet joins, source commitments and expiry are revalidated. Root publication also requires a genuinely consumed classification execution with the same plan, not a summary/card record.

## Explicit execution, then separately reviewed publication

```sh
millefeuille classification-operator execute-publish --mode approved-live --inputs /PRIVATE/classification-inputs.json --packet /PRIVATE/classification-execution-packet.json --receipt /PRIVATE/classification-execution-receipt.json
```

After one validated call, the process holds output only in memory and emits an output digest. Prepare an attended independent approval workflow before calling; the publication window is1..120seconds. Within it, provide exactly one stdin line:

```json
{"packet":"/PRIVATE/classification-publication-packet.json","receipt":"/PRIVATE/classification-publication-receipt.json"}
```

A fresh root broker instance with the separately reviewed publication contract must be available. EOF/timeout/ordinary signals/errors stop without retry; existing accepted SIGTERM/HUP/INT child-group/snapshot cleanup is reused. SIGKILL/crashes/unresponsive kernels cannot guarantee unwinding; immutable Python bytes are not securely zeroized. Publication failure/transport loss reports not-confirmed, even if effects might have occurred.

## Deliberate handoff boundary

Success says **published-proposal-not-classified**. Root-private evidence requires separately approved access/handoff, scientific review and the existing offline `millefeuille classify --evidence ...` step to materialize classification-stage outputs. The pure existing classification output planner accepts the published evidence (covered by regression); this command does not silently mutate the accepted source run or adjudicate low-confidence/taxonomy-gap cases. Existing writeback remains preview-only; no live Zotero writeback or new taxonomy labels are introduced. Source tests are synthetic, not production/live qualification; wheel build/install and trusted runtime deployment follow independent review.
