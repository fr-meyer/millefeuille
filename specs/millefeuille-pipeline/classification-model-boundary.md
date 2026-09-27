# Classification Model Request Boundary

The read-only planner prepares a genuine `classify` executor task for one
accepted paper. It pins openai/gpt-5.6-sol, xhigh, subscription OAuth, one
attempt and no fallback. It does not execute the request or publish a decision.

## Inputs and joins

A released taxonomy lock must have scope_type single-run and the exact run ID.
The planner validates the saved passing acceptance identity, rebuilds actual
source/card/summary/index/duplicate joins, and rejects unresolved review.
The caller must provide the immutable selected-text SHA-256 from verified
summary/index generation evidence, never a new hash of potentially changed text.
The planner compares actual bytes to that binding. It reads selected full text,
structure, the validated card and the verified
full-paper summary. Corpus evidence must resolve inside the selected source-pack
root; no-follow bounded reads refuse symlinked input files.
The prompt includes the entire locked registry, all selected text and page
locators. Hash-bound metadata excludes private prompt content.

The transient output uses classification-model-output.schema.json. Runtime
checks additionally bind exact paper/run/source/taxonomy identities, active
level-2 membership, a distinct active alternative when provided, valid page
locators, nonempty claims and rationale, and explicit review reasons for low
confidence or a taxonomy gap. Validation also rechecks the input binding and
taxonomy snapshot, rejecting mutated plan state. A fixed canonical SHA-256 binds
every request field, including model, thinking, timeout, retry/fallback policy,
template/output contract and task/source locators. Generically valid requests
with recomputed idempotency keys still fail when their original commitment drifts.
Exported metadata uses independent nested copies. Live callers obtain the checked
request snapshot through `plan.executor_request()` immediately before verifying
the exact receipt; output validation repeats the same complete plan check.

The result is a model proposal. Membership checks cannot establish scientific
correctness. Source-based QA must assess the rationale, evidence claims,
strongest alternative and confidence before a final classification is published.

## Publication boundary

Planning/validation grants no live authority. Production model execution must
independently verify a fresh exact approved-live receipt and trusted authorization,
reserve it durably once before the call, and retain observed OAuth/model/usage
provenance. Reading a plan is not consent and does not reserve a receipt.

This feature does not promote a draft taxonomy, synthesize production labels,
change a lock, invoke a provider, write source packs or OpenKB, mutate Zotero,
create credentials, publish a release, or bypass approval. The isolated
OpenClaw connector's existing tool-disabled, stdin-only and transient-output
controls remain in force. Decision/output publication and external writeback
retain their separate exact gates.
