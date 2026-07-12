# CLI Contract

The future CLI should make each Millefeuille stage explicit. Existing Hydra
flags can stay supported, but operator-facing commands should make live
boundaries harder to cross accidentally.

## Command Groups

- `discover`
  - Select items by tag rules.
  - Emit item, attachment, PDF/non-PDF, and no-PDF skip evidence.
  - Default mode: dry-run/read-only.

- `handoff`
  - Build `openkb-millefeuille-handoff/v0.1` rows.
  - Build `openkb-millefeuille-handoff-preview/v0.1` rows in dry-run mode.
  - Optionally compute SHA-256 only after the PDF recovery gate is approved.

- `recover`
  - Recover attachment bytes for verified handoff rows.
  - Verify SHA-256 with `verify_openkb_handoff_recovered_bytes()`.
  - Never serialize PDF payloads or authenticated URLs.

- `source-pack`
  - Create/update source packs after identity verification.
  - Emit disposal/import manifest rows.

- `extract-native`
  - Extract native text and structural metadata.
  - Emit native extraction evidence.

- `extract-ocr`
  - Run a configured OCR backend adapter.
  - Emit OCR evidence without committed provider payloads.

- `route`
  - Select `native`, `ocr`, or `merged-dual`.
  - Emit route decision evidence.

- `openkb-add`
  - Add selected Markdown to OpenKB.
  - Emit OpenKB outcome rows.

- `acceptance`
  - Join handoff, skip, OpenKB outcome, and duplicate-scan evidence.
  - Emit `openkb-millefeuille-acceptance-summary/v0.1`.

- `classify`
  - Start only after acceptance evidence is complete.
  - Emit evidence-backed classification report before any Zotero mutation.

## Run Modes

- `preview`
  - No live writes.
  - No PDF recovery unless an explicit approved fixture is used.

- `read-only-live`
  - Uses Zotero read credentials.
  - No Zotero writes, OCR calls, OpenKB writes, source-pack writes, or package
    publication.

- `approved-live`
  - Requires a named approval token or operator confirmation outside committed
    files.
  - Used for PDF recovery, OCR calls, source-pack writes, OpenKB writes, Zotero
    writes, and release actions.

## Exit Rules

- `0`: stage passed and emitted expected evidence.
- `1`: operator/config error.
- `2`: validation failed or acceptance needs review.
- `3`: manual approval required for the requested stage.
