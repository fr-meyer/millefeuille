# CLI Contract

The future CLI should make each Millefeuille stage explicit. Existing Hydra
flags can stay supported, but operator-facing commands should make live
boundaries, artifact locations, model profiles, and Zotero writeback harder to
cross accidentally.

## Global Options

Every command that reads or writes derived artifacts should accept:

- `--mode preview|read-only-live|approved-live`
- `--artifact-root source-pack|<path>`
- `--source-pack-root <path>` when `--artifact-root source-pack` should resolve
  against a non-default source-pack base directory
- `--model-profile <profile-id-or-file>` for model-using stages
- `--run-id <id>` for resumable runs
- `--stage-manifest <path>` when resuming or inspecting a prior run
- `--writeback none|preview|approved-live` for commands that can affect Zotero

The CLI should record the resolved artifact-root, model profile, and approval
state in the stage manifest and artifact index.

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
  - Emit disposal/import manifest rows.

- `extract-native`
  - Extract native text and structural metadata.
  - Emit native extraction evidence.

- `extract-ocr`
  - Run a configured OCR backend adapter.
  - Emit OCR evidence without committed provider payloads.
  - Record requested OCR model and returned provider model/version.

- `route`
  - Select `native`, `ocr`, or `merged-dual`.
  - Emit route decision evidence.

- `structure`
  - Build page, section, table, figure, and reference structure from the
    selected reconstruction.
  - Emit source locators and quality warnings.

- `summarize`
  - Produce page, section, figure/table, full-paper, and scope-specific
    summaries.
  - Support configurable grains and scopes such as `classification`,
    `literature-review`, `technical`, `domain`, and `quick-read`.
  - Record model profile and source refs for every summary output.

- `card`
  - Produce Markdown and JSON paper card artifacts.
  - Include identity, source hash, one-line thesis, contribution, method,
    results, limitations, classification clues, index state, and quality
    warnings.

- `openkb-add`
  - Add selected Markdown or approved refs to OpenKB.
  - Emit OpenKB outcome rows.

- `index`
  - Update local OpenKB/PageIndex and optional ConDB/ChatIndex lanes.
  - Emit lane status, duplicate/collision evidence, skip reasons, and result
    refs.

- `retrieve`
  - Query a paper package or corpus by paper id, Zotero key, DOI, title,
    section, page, summary scope, or classification evidence need.
  - Return artifact refs by default instead of dumping full private text.

- `acceptance`
  - Join handoff, skip, source-pack, extraction, structure, summary, card,
    OpenKB, index, duplicate-scan, and writeback-preview evidence.
  - Emit `openkb-millefeuille-acceptance-summary/v0.1` plus the artifact-index
    completion verdict.

- `classify`
  - Start only after acceptance evidence is complete or explicitly waived.
  - Run `single`, `batch`, `review`, `adjudicate`, or optional `multi-agent`
    mode against a locked taxonomy version.
  - Emit evidence-backed decision records before any Zotero mutation.

- `writeback`
  - Apply verified Zotero lifecycle tags, compact notes, or collection updates.
  - Default to preview.
  - Require explicit approval and `ZOTERO_WRITE_KEY` for live mutation.

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
  - Execute a staged pipeline with explicit stage selection, artifact-root,
    model profile, writeback mode, and approval gates.

## Example

```bash
millefeuille run \
  --from-zotero-tag millefeuille-test \
  --stages discover,handoff,recover,source-pack,extract-native,extract-ocr,route,structure,summarize,card,index,acceptance \
  --artifact-root source-pack \
  --model-profile research-default \
  --mode approved-live \
  --writeback preview
```

## Run Modes

- `preview`
  - No live writes.
  - No PDF recovery unless an explicit approved fixture is used.
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

## Exit Rules

- `0`: stage passed and emitted expected evidence.
- `1`: operator/config error.
- `2`: validation failed or acceptance needs review.
- `3`: manual approval required for the requested stage.
