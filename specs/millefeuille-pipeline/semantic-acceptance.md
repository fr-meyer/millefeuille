# Snapshot semantic review and exact output handoff

The existing acceptance writer and preview retain their structural contracts.
A structural pass is not a whole-paper semantic-complete claim. Historical
records remain readable; no migration upgrades their meaning.

Multi-source classification and canonical status now require a companion
reports/semantic-decision.json and separately sealed
reports/semantic-review-inventory.json. Each material source, summary, card,
index, duplicate/version review and retrieval evidence file must be enumerated
by the review coordinator, relative to the run, with a SHA256. Every role is
required; multiple entries per role are supported. Exact inventory equality,
identity, reviewer/method, coverage, citation, material-claim, version and
retrieval outcomes are checked. Changed evidence invalidates the decision.
Limitations are mandatory. This is snapshot-specific evidence only.

The coordinator must authenticate the reviewer and independently establish
inventory completeness against observed sources/pages/claims. Neither a
self-issued JSON file nor a reviewer string proves external authority. No
signature, remote freshness, approval or live backend health is inferred.
Source refs must be safe run-relative regular files (no symlinks); staging an
external source reference requires separately approved bytes, never traversal.

Historical Zotero version comparison requires equal nonnegative integers;
missing, malformed, boolean or mismatched versions require review. Equal
versions do not establish current remote metadata or competing-version semantic
superiority. No download, provider or Zotero call is performed.

## Pure publication handoff

plan_semantic_acceptance renders six exact outputs: structural JSON/Markdown,
semantic companion, review inventory, stage manifest and artifact index.
plan_exact_outputs returns immutable payload bytes, output hashes, before-image
bytes/hashes and existing modes/UID/GID. It creates no files or directories.
New destination modes/owners, publisher identity, journal slots and approval
must be supplied by the parent publication phase. Existing ownership is not
changed by this plan. Never publish directly with the legacy writer to imply
semantic completion.

Remaining explicit seams: reviewer authentication and source inventory
completeness are coordinator gates, not solved by hashing. A separately scoped
journaled publisher must revalidate all targets and inputs, exact approval,
owner/mode and before-images immediately before effects; independently verify
ordinary-UID readback and rollback identity. No new privileged executor exists.
Classification's exact rendering/journal adapter remains a separate W3/W5 gate.
Do not reuse the old one-shot publication runner or receipt.

## Independently resolved material binding

The companion now also requires material_binding, derived from the actual resolved
source-pack manifest and artifact declarations, not from its own role inventory.
Every declared attachment is checked against its expected bytes, SHA256 and identity;
all declared extraction, route, structure, summary, card and index files are bound,
including every text_ref in the hierarchy. Only the explicit run/source-pack roots
are supported, with confined historical refs and no-follow reads. Missing material,
changed attachment identity or actual content invalidates classification/status.
This technical binding does not authenticate reviewers or prove semantic judgments.
Review prose refs remain run-relative; separately approved staging is still needed.

PlanningLimits defaults and hard ceilings: 64 outputs, 2 MiB per output/before-image,
16 MiB each aggregate and 16 MiB combined payload/before-image bytes. Callers may
narrow, never expand, these limits. Invalid/bool limits and file/child or role-path
collisions fail before output mutation; the planner never creates directories.
