# Lifecycle Tag Registry And Migration Preview Contract

This document is the normative MF-161 contract for translating verified local
run evidence into a reviewable Zotero lifecycle-tag migration preview. The
command performs no Zotero read or write, no provider call, and no artifact
write. A generated plan is evidence of what the local files supported at plan
time; it is not approval and cannot execute itself.

The immutable registry is
`lifecycle-tag-registry.v0.1.json`. Its JSON Schema is
`lifecycle-tag-registry.schema.json`; migration previews use
`lifecycle-tag-migration-plan.schema.json`. Runtime validation remains
mandatory because JSON Schema alone cannot recompute content identities,
require NFC Unicode, reject every interior control character, or rederive the
semantic relationships described below.

## Authority And Compatibility

The registry has schema version
`millefeuille-lifecycle-tag-registry/v0.1`, registry ID
`millefeuille-lifecycle`, and registry version `0.1`. Its ordered tag list is
exactly the `TagState` enumeration. Its normal transition set is exactly
`ALLOWED_TAG_TRANSITIONS`; `millefeuille-needs-review` and
`millefeuille-error` retain the existing any-state exception behavior.

The registry content identity is SHA-256 over canonical UTF-8 JSON with sorted
object keys, no insignificant whitespace, and the `content_identity` member
excluded. A validator exact-matches the entire checked-in policy and recomputes
that identity with a constant-time comparison. Editing a tag, transition,
legacy policy, or write policy therefore requires a new registry version and
review; changing only the stored hash is invalid.

Zotero tags are observations, never proof that a lifecycle stage happened.
In particular:

- `millefeuille-processed` records only a legacy Millefeuille outcome;
- every exact tag whose string begins with `docai` belongs to the external
  predecessor family;
- all such tags have no canonical mapping and are preserved by default;
- `docai-pageindex` never implies `millefeuille-indexed`; and
- unrelated user tags, including valid NFC Unicode tags, are preserved.

These rules refine the migration guidance in `legacy-migration.md` and keep
the runtime compatible with `tag-state-machine.md`.

## Plan Inputs And Exact Bindings

`millefeuille lifecycle-tags plan` accepts only explicit local files and exact
operator observations. It binds:

- a safe plan ID;
- one Zotero item key, exact integer item version, and the complete unique
  current-tag set;
- one exact stage-manifest and artifact-index wire record;
- their matching paper ID, run ID, stage-status map, Zotero item identity, and
  SHA-256 or aggregate source hash;
- an optional exact acceptance summary;
- an optional exact classification plan and decision, supplied together with
  a released taxonomy lock; and
- every supplied evidence object's schema version, repository-relative ref,
  and canonical SHA-256 content identity.

The join is structural, not a filename convention inferred by the caller. The
artifact index must contain the canonical `stage_manifest` record and every
indexed stage must point to that exact manifest ref. Acceptance requires the
canonical `acceptance_summary` record and the same ref in the acceptance stage
outputs. Classification requires canonical `classification_plan` and
`classification_decision` artifact metadata, both refs in the classify stage
outputs, and the plan's sole `decision_ref` equal to the indexed decision ref.
Kind, JSON format, owning stage, and `private_content: false` are checked for
each of these control records. A schema-valid file selected from another run
directory therefore cannot authorize a tag merely because its internal IDs
look similar.

Input JSON is read as a regular file without following symlinks or reparse
points. The stable opened-file metadata is checked against the 1 MiB limit
before allocation, reads probe at most one byte past the remaining allowance,
and file identity, size, and timestamps are revalidated after reading. JSON
must be UTF-8, duplicate-free, finite, and object-rooted. Registry and plan
objects reject unknown members. Security-sensitive identities and refs reject
padding, non-NFC text, controls, parent traversal, backslashes, URL/drive
prefixes, and encoded-path spellings.

The current tag list is deliberately present in the content-addressed plan.
Changing a tag, item version, item/run/source identity, or any evidence content
changes the plan identity. MF-160 must still re-read the current Zotero item
version and tags immediately before an approved write; the plan-time binding
cannot replace that optimistic-concurrency check.

## Evidence Derivation

A lifecycle tag is proposed only when local evidence derives it. Existing
Zotero tags do not participate in derivation.

| Proposed lifecycle state | Required local condition |
| --- | --- |
| Stage progress through `millefeuille-openkb-added` | The corresponding stage is `passed` in both the exact stage manifest and artifact index. |
| `millefeuille-indexed` | The index stage passed and every explicit lane is terminal: `written` with a safe result ref or `skipped` with a reason. A legacy tag cannot satisfy this condition. |
| `millefeuille-acceptance-passed` | The acceptance stage passed; the exact summary binds the same paper, run, and source, reports `pass`, and contains no needs-review check. |
| `millefeuille-ready-for-classification` | The same passing acceptance evidence exists. |
| `millefeuille-classified` | Acceptance passed; the classify stage passed; one exact plan and classified decision bind the same paper/run/source, mode, and taxonomy version; and an exact released `single-run` taxonomy lock binds that run and version. |
| `millefeuille-needs-review` | At least one stage or supplied acceptance result is explicitly needs-review. |
| `millefeuille-error` | At least one stage is explicitly failed. |

A passed acceptance or classify stage without its required evidence fails
closed. Partial classification evidence fails closed. Batch, pilot, draft,
deprecated, wrong-run, wrong-version, wrong-mode, or tampered taxonomy evidence
cannot derive classification. An existing but unsupported lifecycle tag is
reported in `unsubstantiated_lifecycle_tags` and preserved; it is never treated
as retroactive proof and is not silently removed.

## Mutation Boundary

The plan always records:

- `writeback.mode: preview`;
- `writeback.status: not-executed`;
- `external_effects_performed: false`;
- `requires_mf160_executor: true`; and
- `requires_separate_approval: true`.

No historical legacy tag is automatically proposed for removal. The only
possible removal is the exact selection tag `millefeuille`, and it appears only
when the operator explicitly requests a removal preview, that tag is in the
bound current set, and terminal classified evidence passes every rule above.
Even then, the proposal carries mandatory preconditions to revalidate
acceptance, classification, the taxonomy lock, the current Zotero version, and
a separate approved-live writeback. MF-161 does not implement that write.

Adding a lifecycle tag and removing the selection tag are distinct proposed
mutations. MF-160 must compare the complete current remote state, receive an
exact approval for its write scope, apply optimistic version checks, and emit
its own write/audit result. A preview plan must never be accepted as an
approval receipt or implicit execution request.

## CLI

Print the canonical registry:

```bash
millefeuille lifecycle-tags registry
```

Validate the exact checked-in registry and optionally rederive one plan from its
complete local evidence bundle:

```bash
millefeuille lifecycle-tags validate \
  --registry lifecycle-tag-registry.v0.1.json \
  --plan migration-plan.json \
  --stage-manifest stage-manifest.json \
  --artifact-index artifact-index.json \
  --acceptance-summary reports/acceptance-summary.json \
  --classification-plan classification/classification-plan.json \
  --classification-decision classification/decisions/ITEM1.json \
  --taxonomy-lock taxonomy-lock.json
```

Derive one terminal-success preview from local evidence:

```bash
millefeuille lifecycle-tags plan \
  --registry lifecycle-tag-registry.v0.1.json \
  --plan-id PLAN-ITEM1-42 \
  --item-key ITEM1 \
  --zotero-version 42 \
  --current-tag millefeuille \
  --current-tag millefeuille-processed \
  --stage-manifest stage-manifest.json \
  --artifact-index artifact-index.json \
  --acceptance-summary reports/acceptance-summary.json \
  --classification-plan classification/classification-plan.json \
  --classification-decision classification/decisions/ITEM1.json \
  --taxonomy-lock taxonomy-lock.json \
  --remove-selection-after-terminal-success
```

All successful commands write sanitized JSON to standard output only. They do
not create the plan file named in the validation example; an operator may
capture reviewed output separately. Plan validation reloads the supplied local
evidence, repeats every stage/acceptance/classification/taxonomy derivation, and
requires the rebuilt canonical plan to match exactly. Errors identify the
failed contract and do not echo artifact content. Exit code `0` means the requested
offline operation succeeded and `2` means contract validation failed.

## Stop Conditions And Non-Goals

Stop and regenerate the preview if the Zotero item version or current tags
change, a local evidence file changes, any paper/run/source join drifts, the
acceptance result is not a pass, the decision is not classified, or the exact
taxonomy lock no longer applies. Preserve the old plan as historical review
material; do not edit its digest to make it appear current.

MF-161 does not query Zotero, verify credentials, recover PDF bytes, execute a
stage, call a provider or worker, write OpenKB/PageIndex/another index, mutate
the taxonomy, write tags, or perform rollback. Those operations retain their
separate manual gates and approval contracts.
