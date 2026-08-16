# Operator Preflight and Approval-Packet Validation

`millefeuille operator-preflight` is the offline, fail-closed entry point for
reviewing one bounded future operation. It validates an exact operator packet,
checks only whether allowlisted credential references are present, and emits a
sanitized result. It never discovers Zotero items, reads a source adapter,
recovers a PDF, calls OCR or a model, writes OpenKB or an index, creates a
source pack, mutates Zotero, or consumes an approval receipt.

The command is not an executor and the packet is not approval. `approved-live`
requires a separate MF-100 receipt from the trusted manual approval channel.
Even a valid packet and receipt end at exit code `3` because external execution
is still unsupported.

## Normative Artifacts

- `operator-preflight-packet.schema.json` defines the input wire shape.
- `operator-preflight-result.schema.json` defines the allowlisted output.
- `millefeuille.domain.operator_preflight` enforces semantic rules that JSON
  Schema cannot express, including direct-construction and content-identity
  checks.
- `approved-live-receipts.md` remains authoritative for receipt liveness,
  exact scope, replay, audit, and eventual consumption.

Generic schema validation is insufficient. Consumers must use the runtime
models or implement every published semantic assertion.

## Exact Packet Scope

One `millefeuille-operator-preflight-packet/v0.1` packet binds:

- an exact packet ID, explicit mode, and run ID;
- one source adapter and compatible exact tag, query, item, DOI, title, slug,
  source-pack, or batch selector;
- an item/attachment cap and exact resolved count;
- normalized absolute artifact and source-pack roots;
- sorted exact operations, targets, and destination-role targets;
- provider, model, profile, provider-call cap, and micro-dollar cost cap;
- PDF, provider-response, and temporary-file disposal policies;
- sorted stop conditions and rollback or compensating-action codes;
- acceptance status, including mandatory `pass` before every frozen
  classification operation and Zotero writeback;
- the exact inferred credential types and one allowlisted environment reference
  name for each type;
- for approved-live only, the exact receipt ID and receipt content digest;
- canonical packet and approved-live authorization-context identities.

Missing fields are never inferred. Counts cannot be floating point, boolean,
negative, above the cross-runtime safe-integer maximum, or above the packet
cap. Lists must be sorted and unique. Destinations must be exact members of the
target set. Wildcards and `all`, `any`, or `everything` scope segments are
invalid. Roots must be explicit, normalized absolute paths without dot or
parent-traversal components.

Every string must be NFC Unicode and contain no interior control or format
character. This prevents JSON, terminal, log, and Markdown injection while
still allowing exact normalized Unicode selectors and paths.

## Frozen v0.1 Operation Matrix

Operation names are a closed vocabulary. Safe-looking unknown names, aliases,
suffixes, and prefixes are rejected until the responsible adapter card extends
the runtime matrix and JSON Schema together. The v0.1 mappings are:

- `status.inspect` -> `artifact-root`, `index`, `paper-id`, `source-pack`,
  `source-pack-root`, or `zotero-item-key` inspection target; no credential;
- `source-pack.read` -> `paper-id`, `source-pack`, or `source-pack-root`; no
  credential;
- `index.read` -> `index` or `paper-id`; no credential;
- `zotero.discover` and `zotero.handoff-preview` -> exact Zotero selector
  target; Zotero library and read references;
- `zotero.handoff-export` and `zotero.recover-pdf` -> `artifact-root`; Zotero
  library and read references;
- `source-pack.write` -> `source-pack-root`; no credential;
- `stage.extract-native`, `stage.route`, `stage.structure`, and
  `stage.acceptance` -> `artifact-root`; no credential;
- `ocr.execute` -> `artifact-root`; exact provider/profile plus OCR reference;
- `model.execute`, `model.summarize`, and `model.card` -> `artifact-root`;
  exact provider/profile plus model OAuth readiness reference;
- `model.classify` -> `artifact-root`; exact provider/profile, model OAuth
  readiness, and acceptance `pass`;
- `classification.review` and `classification.adjudicate` -> `artifact-root`;
  no provider credential, but acceptance must be `pass`;
- `openkb.write` -> `openkb-collection`; OpenKB write reference;
- `index.write` -> `index`; index write reference;
- `zotero.writeback` -> `zotero-item`; Zotero library/read/write references
  and acceptance `pass`.

Each requested operation must find at least one exact destination of an allowed
role, and every destination must be covered by at least one requested
operation. This prevents an otherwise harmless operation from carrying an
unapproved write destination. `artifact-root` and `source-pack-root`
destination IDs are bounded SHA-256 identities of the corresponding validated
platform root identity. This preserves exact long-path and Windows-equivalent
root semantics without forcing a path into MF-100's bounded target-ID field.
Provider identity is present exactly when at least one frozen provider
operation is present.

Multiple operations may share a destination. A combined model-to-OpenKB plan,
for example, must explicitly list both `model.summarize` and `openkb.write`,
with both `artifact-root` and `openkb-collection` destinations and both
credential types. A destination name alone never infers or authorizes an
operation.

## Binding Packet-Only Controls to MF-100

MF-100 receipts directly bind operation, target, cap, selector, roots,
provider/model, budgets, disposal, stop conditions, run, approval, and expiry.
The operator packet additionally binds the source adapter, resolved count,
destination roles, provider profile, rollback actions, acceptance status, and
credential references.

An approved-live packet therefore includes exactly one reserved receipt target:

```json
{"kind":"preflight-scope","id":"sha256:<canonical-context-identity>"}
```

The context identity is computed from every packet field except
`approval_receipt` and `integrity`, with the reserved target itself removed
from `targets`. This construction is non-circular: an operator first computes
the context identity, then issues the MF-100 receipt over the ordinary targets
plus that reserved target, and finally records the receipt identity in the
packet.

The packet model recomputes the context identity on every construction path.
The MF-100 validator independently exact-matches the complete target set. A
change to adapter, count, destination role, profile, rollback, acceptance, or
credential reference therefore requires both a newly addressed packet and a
new approval receipt. The reserved target is never an execution destination.

Equivalent normalized Windows root case spellings use MF-100's platform root
identity for receipt authorization. The packet still retains its exact caller
spelling in its own content identity; the two rules do not weaken each other.

## Credential Readiness

Credential requirements are derived from mode and scope, not chosen freely:

- Zotero sources require separate library-ID and read references;
- approved Zotero mutation requires a separate write reference;
- OCR operations require an OCR-provider reference;
- model operations require the intended OpenClaw/Codex OAuth readiness
  reference;
- approved OpenKB and index destinations require their write references.

The allowlisted references are:

- Zotero library ID: `ZOTERO_LIBRARY_ID` or
  `MILLEFEUILLE_CREDENTIAL_ZOTERO_LIBRARY_ID`;
- Zotero read: `ZOTERO_READ_KEY` or
  `MILLEFEUILLE_CREDENTIAL_ZOTERO_READ`;
- Zotero write: `ZOTERO_WRITE_KEY` or
  `MILLEFEUILLE_CREDENTIAL_ZOTERO_WRITE`;
- OCR: `MISTRAL_API_KEY`, `PAGEINDEX_API_KEY`, or
  `MILLEFEUILLE_CREDENTIAL_OCR_PROVIDER`;
- model OAuth readiness: `OPENCLAW_CODEX_OAUTH_READY` or
  `MILLEFEUILLE_CREDENTIAL_MODEL_OAUTH`;
- OpenKB write: `OPENKB_API_KEY` or
  `MILLEFEUILLE_CREDENTIAL_OPENKB_WRITE`;
- index write: `PAGEINDEX_API_KEY` or
  `MILLEFEUILLE_CREDENTIAL_INDEX_WRITE`.

A packet
must contain exactly the inferred types, one reference per type. The evaluator
asks only whether each referenced environment value exists and is non-empty.
It never serializes, logs, returns, persists, or hashes that value. JSON and
Markdown expose only the allowlisted type, reference name, and `present` or
`missing` state. Tests use isolated synthetic mappings and verify that changing
non-empty values does not change the result or its digest.

Credential readiness is configuration evidence, not authentication and not
authority. A present state does not validate the credential, its permissions,
its target, or its expiry. Those checks remain adapter responsibilities before
the first separately approved external effect.

## Content Identity and Loading

Packet and result `integrity.content_digest` values are lowercase SHA-256 over
canonical UTF-8 JSON containing every top-level field except `integrity`.
Canonicalization uses sorted object keys, unescaped Unicode, no insignificant
whitespace, no non-finite values, and JSON separators `,` and `:`. Array order
remains significant and is separately constrained.

Both models recompute their digest with constant-time comparison, including
direct programmatic construction. Result validation also reconstructs the
source packet and exact-matches packet ID, packet digest, and mode. A result
also exact-matches every credential type/reference row and the validated
approval receipt identity to that packet. A result cannot be trusted
independently of its packet, and a recorded `present` state remains readiness
evidence rather than proof that a credential is valid.

Packet and result loaders use stable, no-follow regular-file reads with a
64-KiB pre-allocation bound, bounded growth probe, file-stability checks,
strict UTF-8, duplicate-key rejection, non-finite-number rejection, and exact
field sets. Common credential material, authenticated URLs, private PDF bytes,
and provider-response markers are rejected without reflecting supplied data.

## CLI Contract

```text
millefeuille operator-preflight \
  --packet <operator-preflight.json> \
  --mode preview|read-only-live|approved-live \
  [--approval-receipt <approved-live-receipt.json>] \
  [--format json|markdown]
```

`--mode` is mandatory and must equal the packet mode. A packet never changes
the invocation mode. Passing a receipt with preview or read-only-live is a
manual-gate failure. Approved-live requires a separate receipt whose ID and
content digest equal the packet references and whose independently derived
MF-100 request matches exactly. Expiry and supplied replay state are checked by
the MF-100 validator. This no-effect CLI does not mark a receipt consumed.

Exit codes are:

- `0`: preview or read-only-live packet is valid and all required references
  are present;
- `2`: packet/result validation failed or a non-approved mode is blocked by
  missing credential readiness;
- `3`: a manual approved-live boundary was reached, including missing,
  invalid, drifted, expired, replayed, or valid-but-unsupported approval.

JSON and Markdown are deterministic views of the same result. Both always say
`external_effects_performed: false`; JSON also says
`secret_material_persisted: false`. No output path is supported, so the command
does not create an audit file. A future executor must separately emit MF-100
audit/consumption evidence and enforce every adapter stop, disposal, rollback,
and compensating-action obligation before and after effects.
