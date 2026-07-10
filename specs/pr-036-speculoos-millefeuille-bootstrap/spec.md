# Spec - Speculoos Millefeuille Bootstrap

## Objective

Bootstrap repo-local Speculoos metadata for `zotero-docai-pipeline` and define a
bounded Millefeuille spec/audit plan for the Zotero to OpenKB handoff path.

The slice is planning/docs/metadata only. It must not run live Zotero
operations, fetch private PDFs, call OCR providers, mutate OpenKB source packs,
or change credentials, model routes, rulesets, releases, or package indexes.

## Context

`zotero-docai-pipeline` already supports an OpenKB/DocAI handoff export:

- `openkb-docai-handoff/v0.1` live rows;
- `openkb-docai-handoff-preview/v0.1` dry-run rows;
- strong SHA-256 verification with transient in-memory attachment reads;
- recovery metadata instead of authenticated URLs or stored PDF payloads;
- offline fixture coverage under
  `tests/fixtures/openkb_handoff_docai_test/`.

The June 2026 `docai-test` dogfood proved the first full lifecycle for three
research PDFs: Zotero identity, strong handoff rows, source-pack creation,
native plus Mistral OCR evidence, route `merged-dual`, OpenKB add, duplicate
scan, and one visible no-PDF skip.

## Millefeuille Definition For This Repo

Millefeuille is the evidence-preserving paper-analysis lifecycle:

1. Select a bounded Zotero subset by tag and exclusion rules.
2. Export credential-free handoff rows with Zotero identity, canonical filename,
   attachment identity, recovery method, and verification strength.
3. Recover attachment bytes only in an approved live run.
4. Verify recovered bytes against the handoff SHA-256 before source-pack intake.
5. Create or update source packs under `source_type=zotero`.
6. Run native extraction and OCR extraction, then choose an auditable route.
7. Add the selected Markdown to OpenKB.
8. Record import/disposal manifest rows, duplicate-scan evidence, and exact
   query or fixture checks.
9. Keep private PDFs, provider payloads, auth material, and signed URLs out of
   committed files.

## Current Acceptance Base

The default acceptance suite should remain offline and credential-free:

- `tests/test_openkb_handoff_identity.py`
- `tests/test_openkb_handoff_security.py`
- `tests/test_openkb_handoff_pipeline.py`
- `tests/test_openkb_docai_test_fixture.py`
- `tests/fixtures/openkb_handoff_docai_test/`

These tests are the initial non-live gate for future Millefeuille changes.

## Audit Questions For The Next Implementation Slice

- Can the current handoff schema represent source-version drift when a Zotero
  attachment changes after a prior source-pack import?
- Can the pipeline produce a machine-readable acceptance summary that combines
  handoff rows, skipped items, source-pack outcomes, and duplicate-scan results?
- Are multi-attachment items represented clearly enough for later source-pack
  recovery and de-duplication?
- Are non-PDF attachments skipped with enough evidence to avoid silent loss?
- Is there a clean handoff from this repo into the source-neutral OpenKB helper
  without reviving the old PageIndex MCP/cloud bridge?
- Which live checks must remain behind explicit operator approval because they
  require Zotero credentials, PDF bytes, OCR calls, or OpenKB writes?

## Non-Goals

- Running live Zotero discovery or download.
- Running PageIndex, Mistral, OCR, or model calls.
- Creating, editing, deleting, or importing source packs.
- Writing Zotero tags, notes, attachments, or library state.
- Changing provider credentials, model routes, branch rules, GitHub App
  permissions, releases, tags, or package publication.

## Success Criteria

- Repo-local `.speculoos` metadata exists and validates.
- This task is visible as the current branch/task in committed metadata.
- The spec, plan, and tasks identify the no-live-write boundary clearly.
- Validation passes using only local/offline checks.
- The next implementation slice can start from this packet without rereading the
  entire memory history.
