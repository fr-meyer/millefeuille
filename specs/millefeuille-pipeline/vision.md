# Millefeuille Product Vision

Millefeuille is the CLI control surface for turning a Zotero paper attachment
into an auditable paper knowledge package.

The goal is not only OCR. The goal is a complete, restartable paper-processing
pipeline that preserves source-pack provenance, records model/profile choices,
creates hierarchical summaries and a compact paper card, indexes the result for
local agent retrieval, and then runs evidence-first taxonomy classification.

## North Star

A complete paper run should be able to:

1. Select a paper or batch from Zotero or another source adapter.
2. Verify attachment identity and recovered bytes.
3. Build or update a source pack with provenance and disposal records.
4. Extract native text and OCR evidence with page-aware metadata.
5. Route the selected reconstruction as native, OCR, or merged dual evidence.
6. Normalize page, section, table, figure, and reference structure.
7. Produce page, section, scope-specific, and full-paper summaries.
8. Produce a Markdown and JSON paper card.
9. Index selected artifacts into local OpenKB/PageIndex and optional support
   lanes such as ConDB or ChatIndex.
10. Make the paper easy for agents to retrieve, inspect, cite, summarize, and
    classify.
11. Run taxonomy classification from the prepared evidence package.
12. Write Zotero lifecycle tags or notes only after the relevant stage has been
    verified and explicitly approved.

## Product Principles

- Source packs are the durable provenance layer.
- Stage manifests are the durable progress layer.
- Artifact roots must be explicit, inspectable, and recoverable.
- Zotero is a source system and progress surface, not the only architecture.
- OpenKB/PageIndex is the default local retrieval lane; ConDB and ChatIndex are
  optional support lanes until evidence promotes them.
- Every model-using stage records requested model, resolved provider model,
  profile id, prompt/template version, input refs, output refs, warnings, and
  usage/cost data when available.
- Classification consumes the paper evidence package. It must not be the first
  deep processing step.
- A Zotero success tag is earned only after the corresponding artifact and
  validation evidence exists.

## Target Lifecycle

The long-term stage order is:

```text
discover
handoff
recover
source-pack
extract-native
extract-ocr
route
structure
summarize
card
openkb-add
index
acceptance
classify
writeback
release
```

Individual stages can be skipped when a run mode, fixture, or local-only
configuration makes that appropriate, but skipped stages must be explicit in the
stage manifest.

## Artifact Outcomes

Each processed paper should expose a predictable package:

- identity and source-provenance records;
- selected full text or source-pack pointer;
- page, section, table, figure, and reference structure;
- hierarchical summaries at the requested grains and scopes;
- Markdown and JSON paper card;
- OpenKB/PageIndex status plus optional ConDB/ChatIndex reports;
- classification decision records and rejected alternatives;
- Zotero writeback preview or verified writeback result;
- quality and completion-gate reports.

The operator status view progressively joins those canonical local records and
optional content-addressed observations without reading paper content or
contacting live systems. Its approved-live wording is explicitly
non-authoritative and does not replace a manual approval gate.

## Manual Gates

This vision does not grant any live permission by itself. Live Zotero reads,
Zotero writes, PDF recovery, OCR/provider calls, model calls, worker-agent
execution, OpenKB writes, index writes, source-pack writes, GitHub publication,
release tags, and package publication each require the applicable approval
gate.
