# Internal parent-only writeback core — NOT live-ready

No public CLI, credential discovery or root authority issuance is implemented. The internal actual HTTP adapter is documented in writeback-http.md; it requires explicitly injected credentials and is not a trusted public route. Underscore APIs in writeback_core.py/writeback_core_journal.py are internal engineering interfaces, NOT a security boundary. A caller constructing _Authority has not authenticated itself. The next reviewed root route must independently validate real approval receipts, identity/expiry/budget/root allowlists and rederive the exact plan before projecting authority into this core. Never expose these APIs directly to user JSON or a user-writable root runtime.

## Completed bounded slice

Pure _plan accepts concrete checked materialized artifact-index/stage-manifest/classification-decision/taxonomy-lock/acceptance/source documents plus authoritative parent and existing-collection mapping snapshots. It binds their canonical materialization hash, accepted identities/source hash, actual classify+acceptance stage records and artifact reference, single-run locked active L2 mapping, original source_item_version and separately fresh expected parent version. A proposal or preview WRITEBACK passed bit is insufficient. The future trusted reader must establish these documents actually exist and match current source artifacts, not accept arbitrary caller claims. The core cross-checks the values; it does not authenticate their acquisition.

Explicit user/group library type, library ID, eight-character item and existing collection keys, complete parent semantic data/tag objects/collections, exact approved additions/removals are required. Snapshot64KiB, materialization1MiB,100tags/collections and10tag deltas maximum. Plan stores canonical immutable bytes. One existing collection is added without removing any others; unrelated tags/type/fields survive. No note scope is supported: any non-None note_html is rejected, including empty string. No collection creation, rename, note replacement or group-to-user coercion.

_Journal requires an existing private0700 owned directory and regular0600 single-link file, forbids symlink ancestors, uses SQLite FULL synchronization and explicit file/directory fsync. Reopening a missing/replaced journal fails rather than silently creating a new replay ledger. BEGIN IMMEDIATE plus unique receipt/digest and plan+operation constraints consume authority atomically before effects. Full plan/expiry/rollback audit linkage is retained with consume; each mutation intent is committed/fsynced before dispatch. Before/after observations and strict audit records retain recovery proof. Journal fsync failure cannot produce a successful result. Do not reset/delete the ledger to retry.

_run accepts DISTINCT writer and fresh independent reader interfaces, never a mutation response as readback. read(identity,deadline) returns exact snapshot; patch(identity,expected_version,delta,deadline) sends only tags+collections with conditional item version. Maximum1mutation attempt and2reads,10second shared budget. The transport MUST enforce passed absolute monotonic deadlines and zero mutating retries: this synchronous core checks deadlines between calls but cannot preempt an adapter blocking forever.413/412/429/timeout cannot trigger rebase, repeated mutation or automatic rollback. Ambiguous applied replies are needs-review EVEN when readback matches. Written requires an explicit204 acknowledgement and exact acknowledged-version independent readback. Otherwise needs-review, with partial effects retained.

Only a separately scoped rollback authority referencing the exact verified execution audit can compensate. It requires the exact observed post-execution version and complete semantic state; any concurrent change denies effects. It restores previous tag objects and collection membership via one conditional PATCH, which removes/restores only this plan delta because unrelated state must still match. Only verified written executions are currently rollback-eligible; ambiguous partial states require separate reviewed recovery, not guessed rollback. Rollback is journaled/replay-protected independently.

Successful execution emits the existing CLOSED zotero-writeback-result/v0.1 schema and canonical integrity hash. Unknown item version in needs-review is0, not an assertion of current remote state. Extra plan/delta/recovery details stay in journal and a separate strict millefeuille-writeback-core-audit/v2 contract validated by _validate_audit. Rollback returns its own audit/digest, not a forged writeback written result. In-process results from synthetic transports are not evidence of real live writes.

## Journal provisioning/opening contract (W1/W2 repair)

Ordinary opening is now ONLY _Journal(path, commitment): no O_CREAT, CREATE
TABLE or recovery/upgrade is permitted. The future trusted root configuration
must persist and supply the ORIGINAL commitment independently OUTSIDE the
SQLite ledger. Never reconstruct it from the current ledger/path on restart.
Its exact fields are version=1, random durable journal_id, canonical absolute
path, uid, directory_identity=[device,inode] and file_identity=[device,inode].
The database must have the exact versioned schema, matching sole metadata row,
and DELETE journal mode. Missing/empty/replaced/incompatible databases fail.
Valid ordinary restarts preserve receipt and plan-operation uniqueness.

Explicit one-time _provision_journal(path) is a separate LOCAL provisioning
API, not an execution fallback or authority to provision a root namespace. It
requires an existing private owned directory, uses O_EXCL for a NEW file, commits
schema+ID, synchronizes held file/directory descriptors, validates initialization
and ONLY THEN returns a commitment for separately trusted durable configuration.
An existing path always fails. Any failure retains the file as quarantine and
returns no commitment. Never auto-delete/recreate, auto-adopt a replacement ID,
or overwrite a prior trusted commitment to escape replay. Loss of the outside
commitment or namespace migration requires explicit reviewed recovery; none is
implemented here. This slice provisions only synthetic test directories.

Every database open and commit/sync validates ownership, exact0700 directory /
0600 regular single-link file, no-follow ancestor traversal and committed
namespace/inode identity. Validation occurs before and after SQLite commit and
fsync transitions, and immediately before transport dispatch. Fsync targets the
held validated descriptors, not newly resolved replacement paths. Drift is
rejected, never silently chmodded or unlinked. A post-commit drift failure may
leave authority CONSUMED; that proof is retained and retry remains denied.

These checks do NOT claim race-free SQLite pathname opening against an active
same-UID/root namespace attacker: SQLite still opens its pathname separately
from the inspection descriptors. Authentic independently protected root
namespace/configuration remains mandatory. Nor does this constitute an
anti-rollback seal against a privileged actor rewriting a previous database
snapshot in place while retaining committed metadata; that is outside this
core's trusted-namespace contract. No auto-recovery is provided.

## Next slice — required, not already implemented

1. Independently review/qualify the actual no-retry HTTP adapter and raw-proof projection in writeback-http.md. Root preflight still must verify collection existence/mapping. No live runtime qualification is implied.
2. Root-only receipt/allowlist adapter, source/materialization filesystem verification and source-version/content-drift policy, durable trusted journal provisioning and artifact result publication. Recompute plans from checked evidence and verify exact authority; local dataclasses are not root grants.
3. Scoped operator no-auth planning versus separately authorized remote preflight/execution/readback/rollback, then independent review and installed qualification. No host/OAuth/live permission is implied.
4. Optional managed-note creation/reconciliation/de-duplication/pagination and versioned owned-note rollback is an explicitly separate extension; current code fails closed for all notes.

Existing accepted96 source/runtime/package and generic preview writeback remain unchanged. No build/deploy/live calls in this slice.
