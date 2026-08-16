# Evidence-Safe Staging Cleanup

MF-106 adds an offline operator surface for inspecting, planning, and
reversibly quarantining narrowly proven abandoned staging. Inspection and plan
generation are always read-only. Apply never deletes data: permanent disposal
is outside this contract and belongs to MF-197.

The maintenance operation is exactly
`maintenance.quarantine-abandoned-retrieval-staging-and-bridge-assets`.
Nothing with a different ownership proof or publication state is inferred to
belong to this operation.

## Candidate Recognition

| Kind | Exact namespace and ownership proof | Required state |
| --- | --- | --- |
| Retrieval staging | `batches/millefeuille/<safe-batch-id>/.retrieval.tmp-<16-lowercase-hex>` | One plain directory containing exactly `batch-retrieval-result.json` and `batch-retrieval-report.md`; both must be no-follow, singly linked, read-only, zero-byte regular files. On POSIX the generation is mode `0700` or `0555` and the files are `0444`. |
| Temporary bridge | A direct source-root child named `.millefeuille-bridge.tmp-<16-lowercase-hex>` | One plain read-only directory with exact `ownership.json` plus its declared flat inventory. The manifest must say `publication_state: unpublished` and `cleanup_state: abandoned`; bind safe producer, run, and bridge IDs; and bind every private regular file by exact relative name, type, size, and SHA-256. Every declared file and the manifest itself must be singly linked and read-only. |

The bridge namespace is a forward-compatible ownership convention. Existing
source-pack bridges do not use it and remain outside cleanup. A reserved bridge
name with a missing, invalid, secret-bearing, URL-bearing, drifted, incomplete,
or extra inventory is reported only by an opaque unverified identity and is
preserved exactly. The same preservation rule applies to malformed retrieval
temporary names, nonzero retrieval bytes, hard links, symbolic links, Windows
reparse points, unexpected entries, and writable or changing generations.

Canonical `retrieval/` generations, source packs, prior evidence, ordinary
source-root content, and unrelated quarantine entries are never candidates.
The scanner does not expose private bridge inventory, private bytes, unknown
names, credentials, URLs, or absolute roots in inspection, plan, audit, or
disposition evidence.

## Read-Only Inspection and Planning

Both roots must be existing, disjoint absolute directories. Inspection is the
default action:

```bash
millefeuille maintenance staging inspect \
  --source-root /exact/source-pack-root \
  --quarantine-root /exact/same-filesystem-quarantine \
  --json
```

The result separates exact eligible candidates from opaque unverified entries
and reports bounded platform capabilities. `same_device_stable_roots` means
only that both observed root identities are nonzero and on one device; it is
not a claim that a filesystem is local. `apply_supported` is true only for the
complete Linux descriptor-relative executor boundary. Windows and macOS v0.1
remain portable inspection and planning platforms.

An operator selects every candidate explicitly and repeats the complete sorted
stop set:

```bash
millefeuille maintenance staging plan \
  --source-root /exact/source-pack-root \
  --quarantine-root /exact/same-filesystem-quarantine \
  --run-id run-maintenance-001 \
  --candidate batches/millefeuille/batch-1/.retrieval.tmp-0123456789abcdef \
  --candidate-count 1 \
  --disposal quarantine-until-mf-197 \
  --stop-condition candidate-drift \
  --stop-condition concurrent-namespace-change \
  --stop-condition first-error \
  --stop-condition root-identity-drift \
  --stop-condition stale-or-active-lock \
  --stop-condition unsupported-atomic-primitive \
  --json > cleanup-plan.json
```

The strict plan binds SHA-256 identities for the canonical root spellings and
filesystem objects, the managed source and quarantine namespaces, every exact
relative candidate and snapshot, deterministic no-collision quarantine names,
the exact count, disposal, and stop conditions. Its content digest covers every
field except `integrity`. It contains neither absolute root spelling nor bridge
inventory or bytes. Any plan/apply drift is a refusal.

## Approved-Live Scope

Mutation requires both `--mode approved-live` and a valid MF-100 receipt from a
trusted approval channel. The independently derived request must match exactly:

- operation
  `maintenance.quarantine-abandoned-retrieval-staging-and-bridge-assets`;
- one target per plan candidate, with its exact candidate kind and relative
  path;
- the exact run ID, candidate count, and item cap;
- selector `{kind: maintenance-plan, value: <plan-content-digest>}`;
- canonical absolute output/quarantine and source-pack roots;
- no provider, zero provider calls, and zero cost;
- PDF and provider-payload dispositions `not-applicable`, temporary-file
  disposition `quarantine-until-mf-197`;
- the exact sorted stop set shown above and a current approval window.

The selector and temporary-file disposition are backward-compatible MF-100
enum extensions for this maintenance child. They do not widen older receipts.
Subset authorization, wildcard targets, copied receipt scope, expired approval,
or any mismatch fails before the first move.

```bash
millefeuille maintenance staging apply \
  --source-root /exact/source-pack-root \
  --quarantine-root /exact/same-filesystem-quarantine \
  --plan cleanup-plan.json \
  --approval-receipt exact-approved-live-receipt.json \
  --mode approved-live \
  --json
```

Omitting `--mode approved-live` is a no-effect preview refusal.

## Linux Transaction and Recovery

Apply is enabled only when Linux exposes `O_DIRECTORY`, `O_NOFOLLOW`, directory
file-descriptor operations, `flock`, and `renameat2(RENAME_NOREPLACE)`, and both
stable roots are on the same device. Unsupported filesystems or primitives and
cross-device moves fail closed. Windows does not claim a safe no-replace move;
macOS `renamex_np` is not used because v0.1 cannot bind it to the pinned parent
descriptors.

The transaction:

1. pins source and quarantine root directories, then pins every candidate
   parent and generation without following links;
2. acquires the persistent reserved quarantine lock
   `.mf106-quarantine.lock` and the affected retrieval batch advisory locks;
3. rescans and revalidates root, namespace, plan, candidate, and destination
   identities through the final precommit boundary;
4. atomically reserves a sanitized consumed audit before the first candidate
   move;
5. moves each exact candidate with descriptor-relative
   `renameat2(RENAME_NOREPLACE)`, then verifies the held generation identity at
   the pinned destination;
6. verifies that the source namespace lost only the planned candidates and the
   quarantine gained only the audit and planned generations.

The maintenance lock is an exact one-byte regular file containing `0`, protected
by a nonblocking advisory lock. It is intentionally persistent and is never
unlinked. Every held-lock check resolves its exact reserved name through the
pinned quarantine-root descriptor and requires that name and the held descriptor
to remain the same singly linked inode with unchanged marker bytes. An unlocked
valid marker is reusable; a locked, renamed, replaced, linked, reparse,
malformed, or otherwise unverifiable marker is a refusal. This avoids the
check-then-unlink race that could delete an unverified replacement.

If a later move or verification fails, MF-106 attempts reverse atomic
no-replace moves through the same pinned descriptors. It never overwrites a
newly appeared source entry. After the reverse move, it re-snapshots the
restored candidate and requires the exact planned generation identity; in-place
content drift can never be reported as a successful rollback. A complete
identity-verified rollback is reported as `failed-rolled-back`; a blocked,
drifted, or mixed source/quarantine state is `recovery-required`. In every case,
an audit reservation means the attempted receipt remains consumed. It is never
removed or silently reused.

An exact completed rerun with the same plan, receipt, generation identities,
and matching consumed audit is a no-effect `already-quarantined` result even if
the receipt later expires. Every unresolved or rolled-back attempt requires a
new plan over the new namespace state and a separately approved receipt.

The advisory locks serialize cooperating Millefeuille writers. As with
retrieval publication, trusted ownership or cooperative same-UID writers are
required; portable advisory locks cannot prevent a malicious owner from
changing a candidate name in the final interval of a name-based filesystem
operation. Pinned roots and parents ensure such a swap cannot redirect the
operation into a replacement root or parent namespace, and every observed race
or post-move identity mismatch stops and uses the pinned rollback path.

## Evidence and Disposal Boundary

The durable audit records only opaque receipt, request, plan, and audit
identities, operation, count, evaluation time, and `secret_material_persisted:
false`. Dispositions expose only opaque generation identities and bounded
status/failure codes. They explicitly report `permanent_deletion_performed:
false`.

Quarantine is reversible custody, not disposal. MF-106 provides no delete,
purge, overwrite, recursive cleanup, provider call, live Zotero/OpenKB/PageIndex
call, source-pack write, or network path. MF-197 must define any later permanent
disposal with its own approval, retention, and evidence contract.
