# Taxonomy Registry And Change-Control Contract

Millefeuille treats a classification taxonomy as a versioned, immutable input,
not as a mutable list of labels. The registry contract records the exact
two-level tree, stable node IDs, definitions, include/exclude rules, boundary
notes, lifecycle state, and a canonical content identity. It does not invent a
taxonomy from paper text or promote a draft merely because it parses.
Released versions and scope locks are never overwritten.

## Artifacts

- `millefeuille-taxonomy-registry/v0.1` is one immutable registry version.
- `millefeuille-taxonomy-lock/v0.1` embeds a complete released-registry
  snapshot for one batch, pilot, or single run.
- `millefeuille-taxonomy-change-proposal/v0.1` binds one exact base version to
  one exact released candidate, its complete entry diff, evidence, impact, and
  migration plan.
- `millefeuille-taxonomy-change-review/v0.1` is one immutable approve/reject
  decision bound to both the proposal hash and candidate hash.
- `millefeuille-taxonomy-application/v0.1` records a governed apply or rollback
  after all required reviews pass.

Every artifact uses canonical JSON SHA-256 identity. Canonical hashing removes
only the top-level `content_identity`, serializes UTF-8 JSON with sorted object
keys and compact separators, and records `sha256:<64 lowercase hex>`. Registry
entries and set-like string arrays must also be sorted and duplicate-free.
Loading fails closed on malformed JSON, unknown or missing fields, padded or
non-NFC/control-bearing strings, malformed hashes, duplicate IDs or sibling labels,
invalid parent/replacement links, non-canonical order, and any hash drift.

Stable IDs are never recycled. A later version may revise a label, definition,
or boundary rule, but it cannot move an existing ID to another level or parent
and cannot delete an ID. Retirement preserves the node with `status:
deprecated` and an optional same-level active `replacement_id`. Split and
merge changes therefore add new IDs and deprecate old IDs with explicit
migration guidance.

The checked-in `taxonomy-registry.example.json` is deliberately `draft` and
non-authoritative. It illustrates the shape only; it cannot be locked for a
production batch.

## Lock And Snapshot Semantics

Only a `released` registry can be locked. A lock embeds the complete registry
and repeats its registry ID, taxonomy version, and content identity. Validation
checks all three values against the embedded snapshot and, when supplied,
against the external registry file.

Once a batch starts, its lock remains unchanged for the life of that batch.
Publishing a newer registry does not update, replace, or invalidate an existing
lock. Results from multiple taxonomy versions must not be merged without a
separate normalization/reclassification decision.

The existing v0.1 classification decision, action, and batch artifacts and the
v0.1/v0.2 source-pack family remain readable and valid. They continue to carry
their historical opaque `taxonomy_version`. New live classification must bind
that version to a validated registry lock; this contract does not silently
retrofit a hash into older artifacts or rewrite them.

## Manual Proposal, Review, And Apply

All commands are offline and print JSON to stdout. They do not replace a file,
move an active pointer, call a model, execute an agent, or write Zotero,
OpenKB, an index, or a source pack.

1. An owner writes the complete draft registry explicitly. `taxonomy seal`
   canonicalizes ordering and calculates its content identity. Human review is
   still required before its status is changed to `released` and sealed again.
2. `taxonomy validate` verifies a sealed registry and, optionally, an exact
   lock binding.
3. `taxonomy lock` derives a self-contained snapshot for a named scope.
4. `taxonomy propose` binds a released candidate to the exact released base.
   The declared affected IDs must equal the complete entry-level diff. The
   proposal must include evidence, impact, historical-reclassification choice,
   and migration instructions. Its active-batch policy is always
   `preserve-locked-version`.
5. `taxonomy review` creates separate immutable reviews. Apply requires three
   distinct approving actor identities in the `taxonomy-owner`, `qa-lead`, and
   `operations-lead` roles, and none may equal the requester identity. Any
   rejection, missing role, reused role, reused reviewer, requester acting as a
   required reviewer, stale proposal hash, or stale candidate hash blocks
   application.
6. `taxonomy apply` returns the already-reviewed candidate and an application
   record. The applying actor must differ from the requester and every reviewer.
   Apply never edits the base registry or an existing lock. Publication or
   selection of that new file remains a separate manual repository operation.

`requested_by`, `reviewer_id`, and `applied_by` identify accountable actor identities.
They are not role labels, display names, or shared accounts. They must be stable
canonical identifiers from the governing identity system and are compared as
exact strings. The offline runtime does not contact that identity system; the
operator is responsible for supplying its canonical identifiers.

Representative commands:

```text
millefeuille taxonomy seal --registry-draft taxonomy-v20.draft.json
millefeuille taxonomy validate --registry taxonomy-v20.json
millefeuille taxonomy lock --registry taxonomy-v20.json --lock-id LOCK-2026-001 --scope-type batch --scope-id BATCH-2026-001 --locked-by taxonomy-owner --locked-at 2026-08-11T12:00:00Z
millefeuille taxonomy propose --base-registry taxonomy-v19.json --candidate-registry taxonomy-v20.json --change-id TCR-2026-001 --operation clarify --affected-entry-id L2-BIOSIGNALS-ECG --reason "Clarify a repeated boundary conflict." --evidence-ref TCR-2026-001/evidence.md --impact-risk medium --impact-summary "Existing ECG decisions need review." --estimated-affected-records 12 --historical-reclassification review --migration-instructions "Queue affected records; do not alter active batches." --requested-by requester-a --requested-at 2026-08-11T12:05:00Z
millefeuille taxonomy review --base-registry taxonomy-v19.json --proposal TCR-2026-001.json --review-id REVIEW-2026-001 --role taxonomy-owner --decision approve --reviewer-id owner-a --reviewed-at 2026-08-11T13:00:00Z --notes "Definitions and migration are acceptable."
millefeuille taxonomy apply --base-registry taxonomy-v19.json --proposal TCR-2026-001.json --review owner-review.json --review qa-review.json --review ops-review.json --application-id APPLY-2026-001 --applied-by release-owner --applied-at 2026-08-11T14:00:00Z
```

## Rollback

Rollback never reactivates or edits an old file in place. The historical
source must be compatible with the current base lineage: every source entry ID
must still exist in the base with the same level and parent. A source-only ID
therefore rejects the source as non-ancestral. The new released candidate must
restore every source entry exactly. It must also retain every later base-only
ID byte-for-value from the current base, with one exception: a later entry that
is `active` must change only its status to `deprecated`. An already-deprecated
later entry remains completely unchanged. Labels, definitions, inclusion and
exclusion rules, boundary notes, parents, levels, and replacement IDs cannot
change during rollback. During rollback, the candidate entry-ID set must equal
the base/source union. This rejects unrelated new IDs.

The candidate's governing basis and owner match the historical source, while
its new `taxonomy_version` advances from the current base. A rollback proposal
binds the current base, candidate, and exact historical source identity, then
follows the same independent three-role review process. `taxonomy rollback`
rejects a stale or non-ancestral source, source-entry drift, a retained later
ID that differs beyond the required active-to-deprecated status change, an ID
outside the allowed union, or an attempt to reuse the historical version
number.

This forward-only rollback keeps prior classifications, registry versions,
application records, and active batch locks traceable. Reclassification of
historical papers is a separately governed action described by the proposal;
application itself never performs it.

## Security And Manual Gates

Registry, lock, proposal, and review inputs use no-follow, mutation-detecting
local reads. The runtime returns derived JSON in memory and the CLI prints it;
there is no automatic taxonomy generation or filesystem replacement. Provider
calls, worker execution, credentials, live classification, Zotero/OpenKB/index
writes, source-pack mutation, GitHub publication, releases, and package
publication retain their existing independent gates.
