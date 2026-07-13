# Classification Orchestration Contract

Classification is a CLI-owned stage that consumes the prepared paper evidence
package. The reusable `classify-research-papers` skill remains the method
source for taxonomy rules, evidence order, ambiguity handling, and multi-agent
decision governance.

The separation is:

- skill: doctrine, decision rules, ambiguity handling, QA/adjudication logic;
- CLI: manifests, run state, artifact refs, worker assignments, model
  provenance, QA queues, reports, and Zotero writeback previews/results.

## Preconditions

Production classification requires:

- locked taxonomy version;
- paper card and source-pack refs;
- extraction, structure, summary, index, and acceptance evidence;
- model profile for the classification stage when a model is used;
- writeback mode set to `preview` unless Zotero writes were approved.

Classification must not run from title keywords alone when the evidence package
contains deeper paper evidence needed to resolve the decision.

## Modes

- `single`
  - One paper package, one decision record.
  - Default for pilots and straightforward papers.

- `batch`
  - Many paper packages under one taxonomy version.
  - Produces a batch manifest and aggregate report.

- `review`
  - Rechecks prior classifications or QA samples.
  - Produces correction or no-change records.

- `adjudicate`
  - Resolves low-confidence, conflict, or taxonomy-gap cases.
  - Produces final adjudication records and taxonomy-change requests when
    needed.

- `multi-agent`
  - Optional mode for large, ambiguous, audit-heavy, or QA/adjudication batches.
  - Requires worker support, complete evidence packages, and a locked taxonomy.
  - Produces worker assignments, worker records, coordinator merge records,
    conflict reports, and final batch disposition.

## Decision Rules

The classifier must:

- classify by the paper's primary intellectual contribution;
- prefer author-stated objectives over incidental application context;
- assign one primary Level 1 and one primary Level 2 when the taxonomy supports
  a clean fit;
- inspect full text or key body sections when metadata is insufficient;
- compare the selected path against the strongest rejected alternative;
- escalate ambiguity instead of forcing false certainty;
- record page/section evidence refs, taxonomy clauses, confidence, and review
  reasons.

## Multi-Agent Governance

Multi-agent mode is not the default. Use it only when it adds value.

Stay single-agent when the taxonomy is not locked, the batch is small, the cases
are straightforward, or the environment cannot collect and normalize worker
outputs.

Use multi-agent mode when:

- the batch has enough work or ambiguity to justify merge overhead;
- worker execution is available;
- a coordinator can assign papers, collect normalized outputs, route review
  cases, and produce the final report;
- the taxonomy version is locked for the batch.

The default decision model is bounded deliberation with designated
adjudication:

1. independent classifier proposal;
2. optional challenge or QA review for contested cases;
3. final adjudication by the responsible coordinator, QA lead, or taxonomy lead.

Voting can be a signal, but it is not the final authority.

## Required Artifacts

Classification should create:

- `classification-plan.json`
- `decision-records/<paper-id>.md`
- `decision-records/<paper-id>.json`
- `rejected-alternatives.jsonl`
- `batch-classification-report.md` for batches
- `worker-assignments.jsonl` in multi-agent mode
- `conflicts.jsonl` when worker or QA records disagree
- `adjudication-queue.jsonl` for unresolved cases
- `taxonomy-change-requests.jsonl` when the locked taxonomy appears inadequate
- `zotero-writeback-preview.json`

## Zotero Writeback

Classification can propose Zotero tags, notes, and collection moves, but it
does not mutate Zotero by default. The separate `writeback` stage applies
verified changes only with `ZOTERO_WRITE_KEY` and explicit approval.

## Manual Gates

Model calls, worker-agent execution, Zotero writes, OpenKB/index writes,
source-pack writes, GitHub publication, release tags, and package publication
remain gated. Classification specs and preview artifacts do not grant live
write permission.
