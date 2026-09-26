# Exact GPT paper-card write controls

The no-effect write preview replans all five card files from the validated
transient outcome. Its MF-100 packet permits only `source-pack.write` for one
paper and the exact new card run, source root, card write/request manifests
and immutable summary publication. It permits zero provider calls, zero
incremental cost, no external mutation and stop/review on source drift or
write failure. A matching caller packet and receipt do not establish trusted
administrator approval.

The no-write publication bundle binds the exact five refs, hashes and byte
counts to the write packet and receipt. Its fingerprint includes approval
metadata; approval finalization can change that fingerprint without changing
the approved payload files. The bundle manifest remains metadata in memory
and the root publication audit; it is not a sixth paper file.

The fixed administrator record is
`/etc/millefeuille/gpt-card-write-approval.json`, schema
`millefeuille-gpt-card-write-approval/v0.1`. Strict canonical JSON and exact
fields bind receipt, packet, source-pack manifest, card request, summary
publication, output/provenance manifests, paper, run, five files, exact total
bytes, approver and approval time. Held no-follow descriptors, root ownership
and safe directory/file modes are required.

Only the Linux root broker may reserve the matching receipt in
`/etc/millefeuille/gpt-card-write.sqlite3`. The private ledger uses immediate
transactions, full synchronous commits, unique receipt ID/digest and canonical
audit rows. For publication it records `committing`, the exact bundle
fingerprint, file census and bytes in the same transaction that consumes the
receipt. Missing/untrusted approval, expiry, source/output drift, unsafe
controls, non-root execution and replay fail closed.

These APIs establish exact scope, trusted approval and durable reservation.
They publish no card artifact and provide no model-facing durable writer.
Atomic filesystem commit and root/node publication transport follow at a
separate boundary. Source-run reconciliation, acceptance, indexing,
classification and Zotero writeback remain later stages.
