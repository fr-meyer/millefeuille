# Approved-Live Receipt Contract

This contract defines the control plane for a bounded `approved-live`
Millefeuille action. A receipt records approval; it is not a credential, a
transferable capability, a provider adapter, or permission to widen the requested
work. The current stage CLI validates receipts only to prove that its manual
gate fails closed. It still performs no provider call, Zotero/OpenKB/index
mutation, PDF recovery, or live source-pack write.

## Normative Artifacts

- `approved-live-receipt.schema.json` defines the wire shape.
- `millefeuille.domain.live_receipts.ApprovedLiveReceipt` performs the
  mandatory semantic validation that JSON Schema cannot express.
- `approved-live-audit.schema.json` defines sanitized validation and
  consumption evidence.

Generic schema validation is insufficient. Every consumer must also use the
runtime checks or implement all assertions in
`x-millefeuille-semantic-validation`.

## Exact Scope

One receipt binds all of the following:

- a unique receipt ID and one exact run ID;
- a sorted, unique list of exact operation IDs;
- a sorted, unique list of target kind/ID pairs;
- one exact Zotero tag/query, paper/item identity, source pack, DOI, title,
  slug, or batch-manifest selector;
- the execution item cap and, at authorization time, a selected count no
  greater than that cap;
- normalized absolute output and source-pack roots;
- exact provider and model identity when provider calls are allowed;
- exact provider-call and micro-dollar cost caps;
- explicit PDF, provider-response, and temporary-file disposal policies;
- a sorted, unique set of stop-condition codes;
- the approving identity, approval time, and expiry time.

The receipt and independently constructed execution request must match exactly.
This includes the item cap, both roots, provider/model, budgets, disposal
policy, and stop conditions. Subset authorization is intentionally not
supported: an operator should issue a new receipt for a smaller or otherwise
changed operation set. Wildcard and `all`/`any` scopes are invalid.

Provider identity is `null` only when both provider limits are zero and the
provider-response disposal policy is `not-applicable`. A bound provider requires
at least one permitted call and a real disposal policy. Monetary limits are
integer millionths of one US dollar so accounting never depends on floating
point. `model.*` and `ocr.*` operations always require an exact provider/model
binding; they cannot be authorized with a null provider.

## Time and Single Use

Approval and expiry are canonical UTC timestamps with whole seconds. Expiry
must be later than approval and no more than 24 hours later. Authorization
requires:

```text
approved_at <= evaluation_time < expires_at
```

Live executors must build `ReceiptReplayState` from a durable audit ledger and
reject a previously reserved receipt ID or content digest before any external
effect. The live authorization and audit-builder APIs fail closed when callers
omit replay state; an explicit state loaded from the durable ledger is
mandatory. Only the separately named no-effect validation helper may use an
empty snapshot, and it must never guard a live adapter. They must reserve or
record consumption atomically with their
execution boundary; a crash after reservation requires explicit operator
resolution, never automatic replay.

Every structurally and cryptographically verified durable audit record enters
replay state, including a `validated` no-effect record. The
`validated|consumed` status is informational and never controls replay
reservation. This conservative rule means changing that mutable status cannot
make a single-use receipt reusable.

The current CLI never records consumption because it cannot execute live work.
After successful receipt validation it still exits through the unsupported-live
gate.

## Content Identity and Approval Authenticity

`integrity.content_digest` is the lowercase SHA-256 digest of canonical UTF-8
JSON containing every top-level field except `integrity`. Canonicalization uses
lexicographically sorted object keys, unescaped Unicode, no insignificant
whitespace, no non-finite numbers, and the JSON separators `,` and `:`. Arrays
remain ordered; the runtime separately requires operations, targets, and stop
conditions to be sorted and unique.

The receipt model recomputes this canonical digest and compares it in constant
time during every construction path, including direct programmatic construction.

The digest provides exact content identity and tamper evidence. It is not a
digital signature and does not prove who approved the receipt. A future live
adapter must obtain the receipt from a trusted approval channel and verify that
the asserted approver is authorized for every target. No signing secret belongs
inside the receipt.

## Secret and Audit Policy

Receipt loading is a stable, no-follow regular-file read with duplicate-key,
non-UTF-8, non-finite-number, and 64 KiB limits. The loader checks the opened
file's size before allocating payload storage, keeps the bound while reading,
and rejects size drift or a one-byte-over-limit growth probe. Unknown fields
are rejected.
Secret-bearing field names and common credential, authenticated-URL, private
PDF, and provider-response markers are rejected recursively without reflecting
the supplied value in an error.

`build_approved_live_audit_record()` emits an allowlist only:

- receipt ID and content digest;
- independently computed request digest whose root fields use the same
  platform-normalized root identity as authorization, so equivalent Windows
  root case spellings serialize and replay identically;
- run, approval, expiry, exact sanitized scope, and selected count;
- evaluation time and `validated|consumed` status;
- `secret_material_persisted: false`.

The builder reruns liveness, replay, and exact-scope authorization at the
recorded evaluation time before it emits either status. If a caller durably
records either result, that record reserves the receipt; callers that need a
non-reserving diagnostic must not append it to the replay ledger. It cannot manufacture a
`validated` or `consumed` record for a mismatched, expired, or replayed request.

It never serializes credentials, request headers, private bytes, prompts,
provider responses, authenticated URLs, or arbitrary caller fields.

## Current CLI Gate

All current stage commands accept `--approval-receipt <json>` alongside `--mode`.
They use the explicitly no-effect receipt validator, never the live-authorization
primitive. The rules are:

1. `--approval-receipt` with `preview` or `read-only-live` exits at the gate.
   A receipt never changes the selected mode.
2. Live writeback requires both explicit `--mode approved-live` and explicit
   `--writeback approved-live`, plus a receipt. Neither flag implies the other.
3. The present single-run gate derives one operation, one target/selector,
   item cap `1`, run ID, canonical source-pack root, and effective output root
   from CLI arguments. The output root is the source-pack root only when
   `--artifact-root` is omitted or explicitly set to `source-pack`; otherwise
   it is the normalized path supplied by `--artifact-root`.
4. The request independently requires all three explicit
   `--approved-live-*-disposal` controls and one or more repeated
   `--approved-live-stop-condition` values. Missing controls refuse
   validation; receipt values are never copied into the request.
5. The present CLI has no provider execution controls, so its request binds a
   null provider and zero provider limits.
6. A missing, expired, replayed, tampered, secret-bearing, over-broad, or
   drifted receipt is rejected. A valid receipt is reported by content digest,
   then execution still exits as unsupported.

Batch and commands without an exact current run/target/root binding remain
fail-closed. Future live adapters must independently derive every request
field; they must not copy policy, budget, or target values from a receipt merely
to make validation pass.

## Operator Preflight Integration

`millefeuille operator-preflight` validates the stricter MF-102 operator packet
before any future adapter call. The packet is not approval and does not consume
a receipt. It binds adapter, resolved count, destination roles, provider
profile, rollback actions, acceptance status, and allowlisted credential
reference names in addition to the controls modeled here.

For approved-live, those additional controls are represented in this receipt's
normal exact target set by one reserved `preflight-scope` target. Its ID is the
canonical authorization-context digest defined in `operator-preflight.md`.
The operator-packet model recomputes that digest, while this contract's existing
exact target comparison independently requires the receipt to contain it. The
reserved target is not a write destination. Changing any covered control
requires a new packet identity and a new manual approval receipt.

The preflight checks only presence of allowlisted credential references and
emits no credential values. Successful approved-live validation still exits
through the unsupported-live boundary with no provider call or external write.
The eventual executor must load durable replay state and reserve consumption at
its own effect boundary.

## Adapter Checklist

Before the first live effect, an adapter must:

1. select `approved-live` explicitly;
2. load the receipt through the secure loader;
3. construct `ApprovedLiveRequest` from the execution plan, CLI/config, and
   resolved selected item set independently of the receipt;
4. load durable consumed evidence and validate time, replay, hash, and exact
   scope;
5. verify the approval source and approver authority outside this content-hash
   contract;
6. reserve consumption and emit sanitized evidence at the execution boundary;
7. enforce item, call, and cost caps continuously;
8. stop on any declared condition or identity/scope drift;
9. apply the disposal policy on success, failure, timeout, and cancellation.

Any unavailable control is a refusal condition, not a reason to infer a
default.
