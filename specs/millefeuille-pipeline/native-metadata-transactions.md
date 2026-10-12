# Native metadata transactions

Metadata text writers use descriptor-anchored no-follow transactions. Existing
entries are checked by exact bytes and device/inode, then exchanged with staged
single-link files. New entries use atomic no-replace rename. Exact original-byte
copies and displaced original entries remain in the recovery journal.

The stage/index pair and a run's two acceptance reports commit as one guarded
group. Native pair readers hold a shared run-inode lock; writers hold the same
exclusive lock. A pending journal blocks native reads before and after load.
Acceptance returns success only after all final bytes, input bindings, parent
identities, journal transition and required fsync operations verify.

This is a cooperating-reader transaction, not raw multi-file filesystem
atomicity or an immutable input-tree snapshot. Raw readers can observe intermediate
entries. Ordinary failure restores exact owned old entries and withdraws created
reports. Concurrent replacement, uncertain commit or failed rollback preserves
all evidence and a pending barrier for separately approved recovery; it never
blindly overwrites foreign entries. No automatic recovery occurs on restart.

The existing batch API still persists individually committed runs in sequence;
it does not promise rollback of previously committed runs when another run fails.

Private execution must separately authorize every exact journal, staged, backup,
commit and recovery effect, as well as replacement/report outputs and read caps.
Earlier source approval or a package-only receipt does not cover these effects.
Unsupported descriptor-relative atomic rename/write platforms fail closed.
