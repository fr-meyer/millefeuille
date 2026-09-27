# Reviewed Duplicate Profiles

A historical copy of the same PDF remains a duplicate match. Acceptance can
recognize an explicitly reviewed separate text profile without erasing that match.

The optional canonical record is `reports/duplicate-review.json`, accompanied by
`duplicate-review-receipt.json` and `duplicate-review-audit.json` in the same directory.
Missing evidence preserves the existing review requirement. Malformed, symlinked,
incomplete or changed evidence fails before acceptance writes.

The strict record binds paper, run, source hash, the current index bytes, the
original duplicate scan and both selected text profiles. Both text refs are traversal-free paths relative to the source-pack root and
must be regular files within the source corpus; the current ref must resolve to the
canonical selected full text. The retained text must have a different byte hash.
A changed model name alone does not resolve a byte-identical duplicate.

The only supported decision is `retain-separate-profile`. A named reviewer and
reason are required. The receipt must match the exact provider-free
`duplicate.review-retain-profile` request, with one item and zero provider calls
or cost. The companion consumed audit is recomputed and compared at its original
evaluation time, so an expired historical receipt remains readable evidence.
Reading this record never reserves or replays a receipt.

Receipt self-hashes verify binding, not approver identity. Authenticity remains
the trusted publication boundary used by other pipeline artifacts. Materializing
real review evidence requires independently verified authorization and durable
one-use consumption; supplying local JSON never grants a later provider,
classification, index or Zotero write.

Acceptance retains `matched_existing=true`, adds the verified review identity
and refs, and passes this check only when every binding agrees. Other acceptance
checks and classification gates remain in force.
