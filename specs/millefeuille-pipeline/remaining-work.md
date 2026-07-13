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
- Current integration branch is `dev`; stable branch is `main`.
- Current package version is `0.4.0`.

## Pipeline

1. **Local Offline Contract Work**
   - Define CLI contracts, stage manifest shape, tag-state machine, OCR backend
     contract, artifact storage, model profile, paper-card, summary,
     retrieval/index, classification, and release/version policy.
   - Add fixture-only tests and parse checks.
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
   - Manual gate: model/provider approval only if structure uses a model call;
     source-pack write approval if durable pack artifacts are written.

10. **Hierarchical Summaries**
    - Produce page, section, figure/table, full-paper, and scope-specific
      summaries.
    - Preserve source refs and model provenance.
    - Manual gate: model/provider call approval.

11. **Paper Card**
    - Generate Markdown and JSON paper card artifacts.
    - Include paper identity, one-line thesis, contribution, method, results,
      limitations, classification clues, index state, and quality warnings.
    - Manual gate: model/provider call approval when the card is model-made.

12. **Retrieval And Index Layer**
    - Add selected Markdown and approved refs to OpenKB/PageIndex.
    - Optionally run ConDB or ChatIndex support lanes with explicit verdicts.
    - Run duplicate/collision checks and emit index status.
    - Manual gate: OpenKB write, PageIndex/ConDB/ChatIndex index writes, and
      duplicate scan against live local state.

13. **Acceptance**
    - Join handoff, recovery, source-pack, extraction, route, structure,
      summary, paper card, index, duplicate-scan, and writeback-preview
      evidence.
    - Emit `openkb-millefeuille-acceptance-summary/v0.1` and artifact-index
      completion verdicts.
    - Manual gate: none for offline fixture acceptance; live index/source-pack
      state checks require their applicable approvals.

14. **Classification**
    - Start only after the paper evidence package is complete or a reduced
      pipeline waiver is recorded.
    - Use the taxonomy and evidence-first classification workflow through
      CLI-owned `single`, `batch`, `review`, `adjudicate`, or optional
      `multi-agent` modes.
    - Emit decision records, rejected alternatives, QA/adjudication queues, and
      Zotero writeback previews.
    - Manual gate: model/provider calls and worker-agent execution when used.

15. **Zotero Writeback**
    - Apply verified lifecycle tags, compact notes, or collection moves only
      after preview and approval.
    - Keep Zotero state secondary to manifests and artifact indexes.
    - Manual gate: Zotero write credentials and explicit approval.

16. **Release Candidate**
    - Stabilize `dev` with offline and approved live evidence.
    - Prepare version bump and changelog/release notes.
    - Open `dev` to `main` promotion PR.
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
