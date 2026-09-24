# Remaining Work Pipeline

## Current State

- Repo-local Speculoos is active.
- PR #36 bootstrapped the Millefeuille spec packet.
- PR #37 added actor and branch policy metadata.
- PR #38 added offline source-version drift verification.
- PR #39 adds offline acceptance summary and multi-attachment/non-PDF fixture
  coverage.
- PR #41 adds offline live golden-route fixture evidence.
- PR #43 completes the repository hard rename to Millefeuille.
- PR #45 tightens Zotero credential guidance.
- PR #47 expands the product spec toward a complete paper-processing CLI:
  artifact-root control, model profiles, hierarchical summaries, paper cards,
  retrieval/index lanes, CLI-owned classification orchestration, and Zotero
  writeback separation.
- PR #62 adds offline retrieval/index fixture writers plus source-pack
  artifact-index exposure.
- PR #63 adds offline acceptance synthesis, classification and writeback
  previews, fixture-stage commands from extraction through index, resumable
  canonical `run` orchestration, identity/drift guards, read-only retrieval,
  model-profile listing, and local release-candidate preflight artifacts on
  top of merged PR #62.
- PR #69 adds deterministic same-item multi-PDF source-pack intake while
  preserving the v0.1 single-PDF contract.
- PR #71 adds deterministic offline batch acceptance with preflight-all
  validation and aggregate pass/review reporting.
- PR #73 adds locked-taxonomy offline batch classification with deterministic
  aggregate routing and review/adjudication reporting.
- PR #75 adds lineage-checked offline classification review and adjudication
  actions with immutable final records, escalation queues, and taxonomy-change
  request artifacts.
- PR #77 adds corpus-aware read-only artifact retrieval by paper id, Zotero key,
  source-pack slug, normalized DOI, or normalized exact title, with strict
  identity, ambiguity, section, page, and classification-evidence handling.
- The current offline slice adds strict multi-run retrieval manifests, complete
  preflight, and deterministic JSON/Markdown aggregates containing portable
  refs and status metadata only.
- The current model-planning slice adds deterministic no-call execution plans
  for page, section, and full-paper summary profiles, including explicit auth,
  fallback, live-blocker, and provenance requirements.
- The current taxonomy-governance slice adds content-addressed two-level
  registries, immutable scope locks, exact-diff proposals, independent
  three-role reviews, forward apply records, and forward-only rollback without
  generating labels or mutating active batches.
- Current integration branch is `dev`; stable branch is `main`.
- Current package version is `0.4.0`.

## Pipeline

1. **Local Offline Contract Work**
   - Define CLI contracts, stage manifest shape, tag-state machine, OCR backend
     contract, artifact storage, model profile, paper-card, summary,
     retrieval/index, classification, and release/version policy.
   - Add fixture-only tests and parse checks.
   - Current PR #63 coverage includes full-chain preview, revalidated resume,
     idempotent rerun, metadata drift, failure isolation, and manual-gate exit
     behavior.
   - Manual gate: none while changes stay local/offline.

2. **Feature PR To `dev`**
   - Publish feature branch through the approved Mergeguez broker path.
   - Open a user-authored PR to `dev`.
   - Request exact-head Mergeguez review and wait for checks.
   - Manual gate: GitHub publication, PR creation, review request, and merge.

3. **Read-Only Zotero Discovery Dogfood**
   - Run a bounded staging-tag discovery with read-only Zotero credentials.
   - Emit preview handoff rows and skip evidence.
   - Do not recover PDF bytes or write Zotero state.
   - Manual gate: live Zotero read and credential use.

4. **Verified Attachment Recovery**
   - Recover attachment bytes for the bounded staging set.
   - Compute SHA-256, verify against handoff rows, and discard bytes after
     source-pack intake.
   - Manual gate: PDF recovery/download.

5. **Source-Pack Intake**
   - Create or update source packs under `source_type=zotero`.
   - Record source identity, recovery evidence, disposal status, and import
     manifest rows.
   - Manual gate: source-pack writes.

6. **Native Extraction**
   - Extract native text/metadata where possible.
   - Record extraction coverage, page counts, and exact source-pack inputs.
   - Manual gate: source-pack read/write policy if run against real documents.

7. **OCR Extraction**
   - Run OCR backend abstraction, initially with Mistral OCR 4 evidence adapter
     plus any existing native evidence.
   - Keep provider payloads out of committed files.
   - Manual gate: Mistral/PageIndex/OCR/provider call.

8. **Route Selection**
   - Choose the auditable route: `native`, `ocr`, or `merged-dual`.
   - Record per-page or per-block conflicts that affect summary or
     classification readiness.
   - Manual gate: none for offline route selection; source-pack write approval
     is required if route artifacts are written to durable packs.

9. **Structure Layer**
   - Build page, section, table, figure, and reference structure from the
     selected reconstruction.
   - Emit locators, coverage, and low-confidence warnings.
   - Provider-free local preparation now exists through `structure-prepare`:
     it deterministically derives conservative structure fixtures from selected
     Markdown, preflights batch outputs, rejects replay drift, and performs no
     source-pack or Zotero write. Remaining work is approved durable-pack
     materialization and any model-assisted semantic enrichment.
   - Manual gate: model/provider approval only if structure uses a model call;
     source-pack write approval if durable pack artifacts are written.

10. **Hierarchical Summaries**
    - Produce page, section, figure/table, full-paper, and scope-specific
      summaries.
    - Preserve source refs and model provenance. The offline provenance
      materializer now validates exact execution evidence against the no-call
      model plan and emits strict `millefeuille-model-provenance/v0.1` records
      without accepting prompts, provider payloads, private paper text,
      credentials, unsafe refs, malformed warnings, or invalid token totals.
      Provider-free `summarize-prepare` now joins and verifies route/structure
      identities, hashes the selected inputs, and emits input-bound page,
      section, and full-paper plans plus locator-only work units without paper
      text, section titles, credentials, provider calls, summary outputs, or
      durable-pack writes. Exact per-paper and aggregate work-unit counts now
      bound future execution planning without treating work units as provider
      calls. Remaining work is an
      approved summary executor and attaching validated provenance to verified
      run packages after execution.
    - Manual gate: model/provider call approval.

11. **Paper Card**
    - Generate Markdown and JSON paper card artifacts.
    - Include paper identity, one-line thesis, contribution, method, results,
      limitations, classification clues, index state, and quality warnings.
    - The v0.2 contract materializes planned/pending lanes before indexing and
      permits only a controlled post-index refresh to observed canonical lane
      outcomes. Existing v0.1 cards remain readable but immutable.
    - v0.2 accepts aggregate source identities, but multi-source pack
      propagation through card/index fixture materialization remains MF-114;
      this lifecycle repair does not claim end-to-end multi-PDF support.
    - Manual gate: model/provider call approval when the card is model-made.

12. **Retrieval And Index Layer**
    - Add selected Markdown and approved refs to OpenKB/PageIndex.
    - Optionally run ConDB or ChatIndex support lanes with explicit verdicts.
    - Run duplicate/collision checks and emit index status.
    - Offline read-only retrieval now resolves verified run packages through
      direct or corpus identity locators and returns filtered artifact refs
      without returning private content.
    - Offline batch retrieval now accepts a strict versioned manifest covering
      every single-run locator, applies coherent filters to all runs, rejects
      duplicate resolved identities and unsafe refs before writes, and emits
      byte-stable aggregate JSON/Markdown after pinned-root descriptor-relative
      preflight. Publication verifies the complete unpublished staging
      generation through held descriptors, makes it read-only, revalidates the
      snapshotted inputs plus external manifest under the cooperative batch lock,
      then uses an atomic no-replace rename as the commit point. This boundary
      requires trusted ownership or cooperative same-UID writers; it does not
      claim protection from an uncooperative owner racing the final check.
      Artifact-controlled summary IDs are excluded. Unsupported
      publication primitives fail closed before aggregate output. Pre-commit
      failure cleanup scrubs only owned staged inodes through held descriptors
      and leaves the temporary generation in its reached mode plus unverified
      namespace entries in place for explicit operator cleanup.
    - Remaining work: separately approved live index reconciliation.
    - Manual gate: OpenKB write, PageIndex/ConDB/ChatIndex index writes, and
      duplicate scan against live local state.

13. **Acceptance**
    - Offline preview implementation now exists via `millefeuille acceptance`.
    - It joins handoff, source-pack, extraction, route, structure, summary,
      paper card, index, and duplicate-scan evidence into
      `openkb-millefeuille-acceptance-summary/v0.1`, then updates the
      run-scoped stage manifest and artifact index.
    - Offline batch acceptance accepts an explicit versioned batch manifest,
      preflights all unique paper/run packages, writes deterministic per-run
      results, and emits an aggregate pass/needs-review report.
    - Remaining work: live OpenKB/source-pack/index reconciliation and any
      approval-aware waivers.
    - Manual gate: none for offline fixture acceptance; live index/source-pack
      state checks require their applicable approvals.

14. **Classification**
    - Offline preview implementation now exists via `millefeuille classify`.
    - It starts only after acceptance passes and emits classification plans,
      decision records, rejected alternatives, adjudication queues, and Zotero
      writeback previews from explicit local evidence.
    - Offline batch routing now accepts a versioned manifest, locks one taxonomy
      version, preflights every accepted run and evidence ref, preserves
      deterministic per-run decisions, and emits aggregate route/review reports.
    - Offline review and adjudication now accept strict action evidence,
      validate prior-decision lineage and taxonomy identity, preserve immutable
      action/final-decision packages, and advance canonical run refs. Escalation
      and taxonomy-gap outcomes retain deterministic queue/request artifacts.
    - Offline taxonomy governance now validates stable-ID registry versions,
      snapshots released versions per batch/pilot/run, and derives manual
      proposal, review, apply, and rollback artifacts without replacing files.
    - Remaining work: live model-backed classification, worker-agent execution,
      binding live runs to registry locks, and later taxonomy-governance
      automation beyond the manual reviewed lifecycle.
    - Manual gate: model/provider calls and worker-agent execution when used.

15. **Zotero Writeback**
    - Offline preview implementation now exists via `millefeuille writeback`.
    - It materializes governed tag/note/collection plans without mutating
      Zotero and records preview state in the artifact index.
    - Remaining work: approved-live execution, failure handling against real
      Zotero state, and policy-specific note/tag mutation safeguards.
    - Manual gate: Zotero write credentials and explicit approval.

16. **Release Candidate**
    - Local preview artifacts now exist via `millefeuille run --release-preflight`
      and `release-candidate-preflight.md`.
    - Remaining work: stabilize `dev` with approved live evidence, commit any
      version bump/changelog, and open the `dev` to `main` promotion PR.
    - Manual gate: stable-branch promotion.

17. **Tag And Package Publication**
    - Create signed or otherwise policy-approved release tag.
    - Publish package only after explicit package-index approval.
    - Manual gate: tag, release, and package publication.

## Default Stop Points

- Stop before live Zotero credentials.
- Stop before any PDF byte recovery.
- Stop before OCR/model/provider calls.
- Stop before OpenKB, PageIndex, ConDB, ChatIndex, or source-pack writes.
- Stop before GitHub publication, PR creation, review request, merge, stable
  promotion, tag, or package publication.
