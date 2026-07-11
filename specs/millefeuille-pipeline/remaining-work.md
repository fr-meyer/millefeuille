# Remaining Work Pipeline

## Current State

- Repo-local Speculoos is active.
- PR #36 bootstrapped the Millefeuille spec packet.
- PR #37 added actor and branch policy metadata.
- PR #38 added offline source-version drift verification.
- PR #39 adds offline acceptance summary and multi-attachment/non-PDF fixture
  coverage.
- Current integration branch is `dev`; stable branch is `main`.
- Current package version is `0.4.0`.

## Pipeline

1. **Local Offline Contract Work**
   - Define CLI contracts, stage manifest shape, tag-state machine, OCR backend
     contract, and release/version policy.
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

8. **Route Selection And OpenKB Write**
   - Choose the auditable route: `native`, `ocr`, or `merged-dual`.
   - Add selected Markdown to OpenKB.
   - Run duplicate scan and emit `openkb-docai-acceptance-summary/v0.1`.
   - Manual gate: OpenKB write and duplicate scan against live OpenKB state.

9. **Classification**
   - Start only after handoff, recovery, extraction, OpenKB add, and duplicate
     evidence are complete.
   - Use the taxonomy and evidence-first classification workflow.
   - Manual gate: Zotero collection/tag writes or any live library mutation.

10. **Release Candidate**
    - Stabilize `dev` with offline and approved live evidence.
    - Prepare version bump and changelog/release notes.
    - Open `dev` to `main` promotion PR.
    - Manual gate: stable-branch promotion.

11. **Tag And Package Publication**
    - Create signed or otherwise policy-approved release tag.
    - Publish package only after explicit package-index approval.
    - Manual gate: tag, release, and package publication.

## Default Stop Points

- Stop before live Zotero credentials.
- Stop before any PDF byte recovery.
- Stop before OCR/model/provider calls.
- Stop before OpenKB or source-pack writes.
- Stop before GitHub publication, PR creation, review request, merge, stable
  promotion, tag, or package publication.
