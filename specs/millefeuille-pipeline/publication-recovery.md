# Exact classification plans and conservative recovery

## Supported local contract

The pure classification planner calls the existing evidence preparation path.
It does not invent taxonomy or review private sources. It renders eight actual
classification artifacts plus the stage manifest and artifact index. The legacy
writer consumes the same bytes (including JSONL Unicode/newline conventions).
Each PlannedOutput binds payload/hash and existing bytes/hash/mode/UID/GID.
New-file metadata is not an implicit production approval.

The reservation API requires a caller-provided existing absolute private 0700
workspace, finite approved refs, count/byte limits and an explicitly bounded
journal slot. Output parents must exist. It rejects symlinks, escape,
overlap, nonregular files, stale images and unsupported ownership preservation.
No chmod/chown, privilege escalation, CLI activation or canonical discovery occurs.
The create-only 0600 journal embeds before-images and hashes. Existing or partial
journals block blind retry. fsync failure preserves the entry for reconciliation.
This is a snapshot interface, not a lock; concurrent namespace mutation is unsupported.

## Explicit unsupported boundary

**No output publication executor or rollback mutation is implemented.** Calling
execute_publication always fails closed. No sequential per-file atomic write,
multi-file atomicity, crash-safe recovery completion, consumed approval ledger,
or production authority is claimed. The legacy writer remains nontransactional
and must not be used as the G3 publisher. Root-owned private publication remains
blocked pending a separately reviewed exact route. Do not chmod/chown to bypass it.

The read-only rollback postimage guard rejects missing/changed bytes and unsafe
files, but does not record post-write inode identity and therefore cannot authorize
rollback. Same-byte inode replacement is not proven safe by this guard.

## Operator recovery decision tree

1. No journal and no executor ran: preflight may be repeated only after checking
   the exact current snapshot and authority. Do not infer another publisher had no effect.
2. Any journal entry, including malformed/truncated/fsync-uncertain: preserve it,
   original before-images and approvals. Do not replay, delete or overwrite it.
3. Reconcile each exact target with pre/post hashes, metadata and externally
   recorded inode identity. Partial or unrelated changes stop recovery.
4. Obtain explicit recovery authority and a reviewed executor before restoring
   any bytes. Never delete the last recovery path or mark completion from intent.

## Fault coverage and limitations

Synthetic tests cover F01 stale bytes/metadata, F06 journal durability uncertainty,
F07 reservation no-replay, F10 symlink/escape/nonregular/private-mode refusal and
F12 finite output/journal bounds. F04 partial postimages are rejected by a read-only
guard, not recovered. Before/between-output-write injections and inode-bound
rollback are **unimplemented because there is no executor**, not passing cases.
Provider uncertainty, retries, billing, rate limits and cancellation are not covered.
No live Zotero, OCR, OpenKB, credential, release, deploy or GitHub effect is enabled;
each remains behind separate exact approval and qualification.

## Explicit bounded-executor assessment (local synthetic scope)

The separate execute_reserved_publication and rollback_reserved_publication
entry points now fail closed with specific reasons, without modifying outputs,
consuming a new reservation, or claiming completion. Existing reservations remain
consumed and preserved. This is a deliberate tested refusal, NOT an implemented
transaction and NOT an external approval blocker.

A safe implementation remains engineering work: descriptor-anchored access alone
does not supply atomic compare-inode-and-replace. POSIX rename can overwrite a
leaf changed after the final identity check; private mode0700 does not exclude
another same-UID writer. A qualified interface must define enforceable namespace
exclusion or explicitly constrain its threat model, then implement and fault-test
finite durable intent/progress slots, create-only one-attempt consumption,
exclusive same-parent temporaries, postimage inode receipts, uncertain crash
stops, and separately authorized identity-bound rollback. A hash-only reservation
is never sufficient rollback evidence. No claimed publication or rollback occurs
here. The root-owned group-readable canonical run remains unsupported by this
nonprivileged interface and requires a separately approved privileged runner.

## Explicit private single-cooperating-writer subset

`exclusive_publication.publish_snapshot` and `rollback_snapshot` are separate,
non-CLI-wired APIs for caller-owned existing mode-0700 namespaces, mode-0600
single-link files, explicit finite output/auxiliary budgets and a named namespace
contract. They take a nonblocking flock on the root directory, anchor parent
operations with no-follow descriptors, reserve a create-only transaction directory,
and fsync bounded before-images, staged data, intents and post-inode receipts.
Publication is atomic per file, not as a group. It is not an authorization token.

This subset assumes one cooperating writer. It does not protect against root,
malicious same-UID or uncooperative writers. It is unsupported for the existing
root-owned/group-readable private canonical run; that needs a separately reviewed
exact G3 privileged runner. The original publication_journal executor APIs still
refuse, and neither API is automatically selected by classification/acceptance.

Every transaction name stays consumed. Exceptions, interruption or uncertain
effects preserve journal/staging evidence and never retry, resume or auto-rollback.
Explicit rollback validates the whole finite journal, all before-images and all
current postimage hashes/inodes/modes/owners before its first effect. Only fully
recorded postimages may be restored. Missing post-receipts, partial journal writes,
unrelated changes or an interrupted rollback require operator reconciliation.
Restoration preserves bytes/mode/ownership, not the old inode identity. No last
recovery path or retained artifact is automatically deleted.

Synthetic qualification includes a real two-replacement/one-create transaction,
explicit restoration, one-shot replay guards, injected boundary failures, three
actual child-process exits, identical-bytes/different-inode refusal, malformed
late before-image and auxiliary-path rejection, staging/terminal drift, locks,
size limits and no-follow refusals. This is not power-loss/filesystem qualification
or completion of provider-uncertainty, all twelve fault categories or six platforms.

Terminal publication now reopens every canonical parent and revalidates the
transaction directory/journal identity, not only held descriptors. Explicit
rollback repeats all root/parent/target/staging checks after its pre-effect hook
and verifies every canonical restored receipt or absence before recording
rollback-complete. Deterministic parent substitution, detached-parent writes,
previously restored target drift and transaction-directory replacement refuse
without a false terminal success. These checks do not expand the threat model.
