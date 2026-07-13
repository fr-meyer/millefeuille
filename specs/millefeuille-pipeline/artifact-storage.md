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
    source.md
    pages/
    extractions/
      native/
      mistral-ocr/
    selected/
    analyses/
      millefeuille/
        <run-id>/
          stage-manifest.json
          artifact-index.json
          model-provenance.jsonl
          structure/
          summaries/
          cards/
          classification/
          index/
          reports/
          zotero-writeback-plan.json
```

## Required Run Artifacts

Every non-trivial run should emit:

- `stage-manifest.json`
- `artifact-index.json`
- `model-provenance.jsonl` when any model or OCR provider is used
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
