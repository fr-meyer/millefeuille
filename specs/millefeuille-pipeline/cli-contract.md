# CLI Contract

The CLI now exposes preview/read-only stage commands for `extract-native`,
`extract-ocr`, `route`, `structure`, `summarize`, `card`, `index`,
`acceptance`, `classify`, `writeback`, `retrieve`, `models`, and `run`,
alongside the older `artifacts`, `status`, and `source-pack` helpers. The
remaining command-surface goal is to make discovery, handoff, recovery,
source-pack intake, OpenKB addition, and approved-live execution equally
explicit without weakening their manual gates.

## Global Options

Implemented stage-command controls are:

- `--mode preview|read-only-live|approved-live`
- `--source-pack-root <path>` plus `--paper-id|--item-key` and `--run-id`
- `--model-profile <profile-id-or-file>` for model-using stages
- `--writeback none|preview|approved-live` for commands that can affect Zotero
- `run --resume`, which skips only passed stages after their expected outputs
  and paper/run/source identity revalidate

Arbitrary `--artifact-root <path>` selection and an explicit
`--stage-manifest <path>` override remain contract work. The current stage
surface deliberately resolves the canonical run directory from the verified
source-pack root and rejects cross-wired manifest/index identity before any
derived artifact write.

## Current Preview Surface

- `extract-native`, `extract-ocr`, `route`, `structure`, `summarize`, `card`,
  and `index`
  - Implemented as fixture-only, single-paper stage adapters over existing
    verified source-pack writers.
  - Update the run-scoped stage manifest and artifact index after each write.
  - Preflight identity and refuse drift; `extract-ocr`, `summarize`, `card`,
    and `index` do not call providers or live stores in this mode.

- `acceptance`
  - Implemented for verified source-pack runs.
  - Requires explicit handoff evidence plus an existing run-scoped artifact
    package.
  - Writes `reports/acceptance-summary.json` and updates the stage manifest and
    artifact index.

- `classify`
  - Implemented for accepted runs from explicit local evidence.
  - Writes classification plan/decision artifacts plus a Zotero writeback
    preview without model/provider calls.

- `writeback`
  - Implemented in preview mode only.
  - Materializes a governed Zotero writeback plan and optional preview note.

- `retrieve`
  - Implemented as a read-only ref resolver over an existing artifact package.
  - Returns summary, card, index, acceptance, classification, and writeback
    refs instead of private paper content.

- `models`
  - Implemented as a bundled profile lister for preview and planning use.

- `run`
  - Implements the canonical preview chain from `extract-native` and
    `extract-ocr` through `acceptance`, `classify`, and `writeback`, with
    optional local release preflight.
  - Preflights stage order and required evidence before writing, rejects
    duplicate/out-of-order stages, and supports output-revalidating resume.
  - Does not orchestrate discovery, handoff, recovery, source-pack intake, or
    approved-live stages.

## Command Groups

- `discover`
  - Select items by tag rules.
  - Emit item, attachment, PDF/non-PDF, and no-PDF skip evidence.
  - Default mode: preview/read-only.

- `handoff`
  - Build `openkb-millefeuille-handoff/v0.1` rows.
  - Build `openkb-millefeuille-handoff-preview/v0.1` rows in preview mode.
  - Optionally compute SHA-256 only after the PDF recovery gate is approved.

- `recover`
  - Recover attachment bytes for verified handoff rows.
  - Verify SHA-256 with `verify_openkb_handoff_recovered_bytes()`.
  - Never serialize PDF payloads or authenticated URLs.

- `source-pack`
  - Create or update source packs after identity verification.
  - Initial fixture command:
    `millefeuille source-pack intake --evidence <json> --source-pack-root <dir>`.
  - Verify all recovered local bytes against expected SHA-256 before writing
    `manifest.json`, v0.1 `source.pdf`, or v0.2 `sources/*.pdf` entries.
  - Group same-item PDF handoff rows into one v0.2 source pack with per-source
    identity and a deterministic aggregate source hash.
  - Refuse to overwrite source packs whose source hash or manifest identity has
    drifted from the supplied evidence.
  - Emit disposal/import manifest rows.

- `extract-native`
  - Extract native text and structural metadata.
  - Emit native extraction evidence.
  - Preview fixture slice may write `extractions/native/evidence.json` plus
    `fulltext.md` from explicit local Markdown evidence after source-pack
    verification.

- `extract-ocr`
  - Run a configured OCR backend adapter.
  - Emit OCR evidence without committed provider payloads.
  - Record requested OCR model and returned provider model/version.
  - Preview fixture slice may write `extractions/mistral-ocr/evidence.json`
    plus `fulltext.md` from explicit local Markdown evidence after source-pack
    verification.

- `route`
  - Select `native`, `ocr`, or `merged-dual`.
  - Emit route decision evidence.
  - Preview fixture slice may write `selected/route.json` plus
    `selected/fulltext.md` only after the required native/OCR extraction
    sidecars are already verified.

- `structure`
  - Build page, section, table, figure, and reference structure from the
    selected reconstruction.
  - Emit source locators and quality warnings.
  - Preview fixture slice may write `structure/structure.json` plus optional
    `structure/outline.md` only after the source-pack manifest, route sidecar,
    and selected full text are verified.

- `summarize`
  - Produce page, section, figure/table, full-paper, and scope-specific
    summaries.
  - Support configurable grains and scopes such as `classification`,
    `literature-review`, `technical`, `domain`, and `quick-read`.
  - Record model profile and source refs for every summary output.
  - Preview fixture slice may write
    `analyses/millefeuille/<run-id>/summaries/hierarchical-summary.json` plus
    `summaries/texts/*.md` only after the source-pack manifest and structure
    sidecar are already verified.

- `card`
  - Produce Markdown and JSON paper card artifacts.
  - Include identity, source hash, one-line thesis, contribution, method,
    results, limitations, classification clues, index state, and quality
    warnings.
  - Preview fixture slice may write `analyses/millefeuille/<run-id>/cards`
    with `paper-card.json` plus `paper-card.md` only after the source-pack
    manifest and hierarchical summary bundle are already verified.

- `openkb-add`
  - Add selected Markdown or approved refs to OpenKB.
  - Emit OpenKB outcome rows.

- `index`
  - Update local OpenKB/PageIndex and optional ConDB/ChatIndex lanes.
  - Emit `millefeuille-retrieval-index-status/v0.1` with lane status,
    duplicate/collision evidence, skip reasons, and result refs.

- `retrieve`
  - Current preview implementation queries a verified paper package by paper
    id or item key, plus summary/index filters, and returns artifact refs.
  - Future expansion should support corpus lookup by DOI, title, section, page,
    or broader classification evidence need.

- `acceptance`
  - Current preview implementation joins handoff, source-pack, extraction,
    route, structure, summary, card, index, and duplicate-scan evidence.
  - Future expansion should add broader skip/OpenKB/live-state joins and
    approval-aware waivers.

- `classify`
  - Current preview implementation starts only after acceptance evidence is
    complete and emits evidence-backed decision records before any Zotero
    mutation.
  - Future expansion should cover richer batch/review/adjudicate routing and
    optional `multi-agent` execution.

- `writeback`
  - Current implementation is preview-only and writes governed plans rather
    than mutating Zotero.
  - Future approved-live mutation still requires explicit approval and
    `ZOTERO_WRITE_KEY`.

- `models`
  - List, validate, and explain available model profiles for each model-using
    stage.

- `artifacts`
  - Show artifact-root, source-pack refs, manifest status, missing outputs, and
    privacy-sensitive refs for a paper or run.

- `status`
  - Read stage manifests, source packs, Zotero tags, artifact indexes, and
    index state.

- `run`
  - Current implementation sequences the fixture-only extraction, route,
    structure, summary, card, and index stages followed by preview
    `acceptance`, `classify`, and `writeback`.
  - `--resume` revalidates before skipping; idempotent reruns preserve the
    artifact package byte-for-byte for identical evidence.
  - Future expansion should add discovery/source-pack/OpenKB and approved-live
    execution with explicit artifact-root and approval-token gates.

## Example

```bash
millefeuille run \
  --source-pack-root /path/to/source-packs \
  --paper-id zotero-ITEM1 \
  --run-id run-fixture \
  --stages extract-native,extract-ocr,route,structure,summarize,card,index,acceptance,classify,writeback \
  --native-extraction-evidence /path/to/native-evidence.json \
  --ocr-extraction-evidence /path/to/ocr-evidence.json \
  --route-selection-evidence /path/to/route-evidence.json \
  --structure-evidence /path/to/structure-evidence.json \
  --summary-evidence /path/to/summary-evidence.json \
  --card-evidence /path/to/card-evidence.json \
  --index-evidence /path/to/index-evidence.json \
  --handoff /path/to/handoff.jsonl \
  --classification-evidence /path/to/classification-evidence.json \
  --writeback preview \
  --release-preflight
```

## Run Modes

- `preview`
  - No live writes.
  - No PDF recovery or source-pack intake unless an explicit approved local
    fixture is used.
  - Model/provider calls are skipped unless the stage is running against an
    approved fixture or approved local profile.

- `read-only-live`
  - Uses Zotero read credentials.
  - No Zotero writes, OCR calls, model calls, OpenKB writes, index writes,
    source-pack writes, or package publication.

- `approved-live`
  - Requires a named approval token or operator confirmation outside committed
    files.
  - Used for PDF recovery, OCR calls, model calls, source-pack writes, OpenKB
    writes, index writes, Zotero writes, and release actions.
  - The current stage-oriented preview CLI stops with exit code `3`; it does
    not implement approved-live execution.

## Exit Rules

- `0`: stage passed and emitted expected evidence.
- `1`: operator/config error.
- `2`: validation failed or acceptance needs review.
- `3`: manual approval required for the requested stage.
