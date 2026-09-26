# One-call GPT paper-card execution scope preview

`validate_gpt_card_approval_preview` verifies an MF-100 packet and receipt
against a freshly planned card request from the immutable published summary
bundle. This is a no-effect review check, with zero provider calls, credential
reads, receipt reservations or writes. A self-hashed receipt is not trusted
administrator approval.

The exact supported operation is `model.card`, with one paper, one explicit
card run, one GPT request and zero incremental API cost. Targets must include
the source-pack artifact root, paper ID, card run ID, card request-plan hash,
published summary bundle hash and preflight authorization context. The model
is `gpt-5.6-sol` through the OpenClaw subscription OAuth profile. API-key use,
hidden fallback, wider calls, extra operations, other roots and changed source
or run scope are rejected. Packet and receipt integrity, scope matching and
expiry are checked using the existing MF-100 contract.

The stop conditions include authentication failure, model mismatch, provider
error, schema failure, source drift and missing usage. A summary receipt
cannot be reused for the card stage. Rollback is `stop-and-review`; provider
payloads are never persisted by this boundary and temporary files are disposed
of after the run.

The later live adapter must also match a fixed administrator-owned approval,
durably reserve the receipt once before the first possible call, verify saved
OAuth readiness and the exact source immediately before dispatch, and validate
returned card content with actual model, usage and output hash evidence.
Canonical card/source-run assembly and exact output write approval remain
separate. This preview grants no acceptance, indexing, classification or Zotero
writeback authority.
