# Artifact Storage Contract

Millefeuille artifacts must be easy to locate, move, validate, and rebuild.
No important output should disappear into a hidden default directory.

## Artifact Roots

The CLI should accept an explicit `--artifact-root` for commands that create or
read derived artifacts.

Supported root modes:

- `source-pack`
  - Store derived artifacts under the paper source pack.
  - Preferred for production Zotero/OpenKB runs.
  - Requires a verified existing source-pack `manifest.json` with a
    `source_hash` in `sha256:<hex>` form; derived Millefeuille run artifacts are
    written under
    `analyses/millefeuille/<run-id>/` without changing source evidence.

- absolute or relative path
  - Store a project or batch bundle outside the source pack.
  - The artifact index must point back to the source pack and source hash.

- `memory/...`
  - Store only lightweight, public-safe reports and summaries.
  - Full text, raw OCR, private provider JSON, PDFs, and large assets are not
    allowed in tracked memory.

## Recommended Source-Pack Layout

```text
source-packs/
  zotero/<paper-slug>/
    manifest.json
    source.pdf
    source.md
    pages/
    extractions/
      native/
      mistral-ocr/
    selected/
    structure/
      structure.json
      outline.md
    analyses/
      millefeuille/
        <run-id>/
          stage-manifest.json
          artifact-index.json
          model-provenance.jsonl
          summaries/
            hierarchical-summary.json
            texts/
          cards/
            paper-card.json
            paper-card.md
          classification/
            classification-plan.json
            decision-records/
            rejected-alternatives.jsonl
            adjudication-queue.jsonl
            zotero-writeback-preview.json
          index/
            index-status.json
          reports/
            acceptance-summary.json
            acceptance-summary.md
            release-candidate-preflight.json
            release-candidate-preflight.md
          zotero-writeback-plan.json
```

## Source-Pack Intake Contract

The fixture-first source-pack writer accepts an explicit local recovered-PDF
evidence file and a source-pack root:

```bash
millefeuille source-pack intake \
  --evidence recovered-pdf-evidence.json \
  --source-pack-root /path/to/source-packs
```

The evidence file must name the Zotero item key, attachment key, canonical
filename, local recovered PDF path, and expected SHA-256. Relative recovered PDF
paths resolve beside the evidence JSON. The writer computes the recovered bytes'
SHA-256 before creating the pack, fails before writing on hash or size
mismatch, and refuses to overwrite an existing pack whose `source.pdf` or
manifest drift from the evidence.

`manifest.json` must follow `millefeuille-source-pack-manifest/v0.1` and record:

- `paper_id`, `source_type`, and top-level `source_hash`;
- `source.ref` (`source.pdf`), byte size, format, and source SHA-256;
- Zotero item/attachment identity and canonical filename;
- verification status/method plus expected and actual SHA-256;
- fixture intake provenance and sanitized recovery/policy hints.

The fixture-first intake command does not read Zotero, download PDFs, call OCR
or model providers, write OpenKB/PageIndex/ConDB/ChatIndex data, or mutate
Zotero tags/notes. Live recovery and live source-pack writes remain separate
approved-live stages.

## Required Run Artifacts

Every non-trivial run should emit:

- `stage-manifest.json`
- `artifact-index.json`
- `model-provenance.jsonl` when any model or OCR provider is used
- `reports/acceptance-summary.json` once the acceptance stage is synthesized
- `classification/classification-plan.json` when classification preview or live
  classification runs
- `classification/zotero-writeback-preview.json` when classification proposes
  future Zotero state
- `quality-report.md`
- `completion-gate-result.json`
- `zotero-writeback-plan.json` when Zotero state would change

The artifact index should answer:

- where the artifact-root is;
- which paper/source identity the run used;
- which source hash and source-pack version the run belongs to;
- where selected full text and evidence live;
- where structure, summaries, paper card, classification, and reports live;
- which OpenKB/PageIndex/ConDB/ChatIndex indexes were updated or skipped;
- which Zotero tags or notes were written, skipped, or left as preview.

## Storage Rules

- Artifacts must use relative refs inside a portable bundle when possible.
- Artifacts may point to absolute source-pack paths when the source pack is the
  authority.
- Provider payloads and raw PDF bytes must not be committed to the repository.
- Model prompts and outputs should be stored by ref when they include private
  paper content.
- The paper card can be copied to lightweight reports when it does not expose
  private full text beyond approved bibliographic and summary content.

## Manual Gates

Source-pack writes, OpenKB writes, index writes, model/provider calls, Zotero
writeback, GitHub publication, release tags, and package publication require
the applicable approval. An artifact-root choice does not bypass those gates.
Writing Millefeuille analysis artifacts under an existing source pack is not the
same as creating or mutating the source pack's recovered source evidence.
