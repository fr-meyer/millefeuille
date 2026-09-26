# Trusted GPT paper-card publication

The internal root publication broker decodes a bounded transient outcome,
replans the card from the immutable published summaries and fresh source
evidence, verifies the exact write approval, and durably reserves its receipt
before invoking the writer. It revalidates every prospective byte again after
reservation. It makes zero provider calls.

## Transport

The handoff is strict canonical JSON, bounded to 4 MiB, with duplicate fields,
non-standard constants, excessive depth/nodes, noncanonical base64 and
changed source/result bytes rejected. It contains the returned card only in
transit; no pickle or serialized Python object is accepted. The root decoder
rebuilds the request and validates actual model/authentication, usage, cost,
output hash/size and strict cited card content.

The fixed one-shot socket is `/etc/millefeuille/gpt-card-publication.sock`.
The root server checks the node peer UID; the node client checks root UID and
root-owned safe socket/directory modes. Metadata and handoff frames are
bounded. An unauthorized first connection cannot consume the listener.
Evidence paths are contained under the source root and read without following
symlinks. The socket is removed after the authorized exchange or timeout,
provided its inode still matches the socket the server created. Replies
contain only operational fingerprints and counts, never private card text.

## Commit and audit

The root filesystem primitive verifies the typed five-file bundle, its exact
approval-bound manifest, hashes, bytes and safe run-relative refs. It stages
the files in a private root-owned directory, uses descriptor-relative no-follow
access, fsyncs files/directories and publishes one complete new run using
Linux `renameat2(RENAME_NOREPLACE)`. Files are root-owned 0644; published
directories are 0755. Occupied or symlink destinations cannot be replaced.

The audit distinguishes `published`, `failed-before-commit` and `uncertain`.
A receipt consumed before an interrupted or failed commit cannot be retried.
Rename/post-rename or audit uncertainty requires inspection of the root ledger
and exact file census. The transport conservatively reports an unacknowledged
backend result as uncertain, including denied replay; it does not initiate
automatic recovery or retry.

Source roots retain the existing GCP cooperative-ancestor assumption: held
descriptors prevent symlink traversal and path/descriptor identity is checked
before/after rename, but a same-UID actor able to rename an ancestor can move
the resulting tree. The runtime operator must preserve the audited source root.

## Verification

Focused offline tests cover strict handoff, exact atomic output, no replacement,
private staging cleanup, uncertain commit/audit states, receipt rollback/replay,
bounded frames, one-shot cleanup and an unauthorized first peer. A separate
Linux root-to-node GCP rehearsal uses disposable synthetic source/control
paths, real peer UIDs and a root-owned node-group socket. It verifies all five
saved bytes, ownership/modes, terminal audit, one consumed receipt, denied
replay and socket/stage cleanup. Another real-root case verifies the complete
broker fixture directly. Neither rehearsal calls a provider or touches an
existing paper/control package.

This boundary publishes canonical card artifacts only. Source-pack run
reconciliation, whole-paper acceptance, indexing, duplicate checks,
classification and Zotero writeback remain separate later stages.
