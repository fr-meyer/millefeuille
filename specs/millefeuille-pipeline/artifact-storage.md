# Artifact Storage Contract

Millefeuille artifacts must be easy to locate, move, validate, and rebuild.
No important output should disappear into a hidden default directory.

## Artifact Roots

The lifecycle/stage CLI accepts an explicit `--artifact-root` for single-run
commands that create or read derived run artifacts. Existing source-level
extraction, route, and structure evidence remains anchored in the verified
source pack; the selected artifact root owns the run manifest, artifact index,
summaries, cards, index status, acceptance, classification, writeback preview,
and release-preflight outputs.

Supported root modes:

- `source-pack`
  - Store derived artifacts under the paper source pack.
  - Preferred for production Zotero/OpenKB runs.
  - Requires a verified existing source-pack `manifest.json` with a
    `source_hash` in `sha256:<hex>` or `sha256-aggregate:<hex>` form; derived
    Millefeuille run artifacts are written under
    `analyses/millefeuille/<run-id>/` without changing source evidence.

- absolute or relative path
  - Store a project or batch bundle outside the source pack.
  - The artifact index must point back to the source pack and source hash.
  - The discovery/dry-run writer creates `<root>/<paper-id>/<run-id>/`.
  - Stage commands also accept an exact run directory or the declared legacy
    container layouts documented in `cli-contract.md`, but exactly one complete
    package may match.

- `memory/...`
  - Store only lightweight, public-safe reports and summaries.
  - Full text, raw OCR, private provider JSON, PDFs, and large assets are not
    allowed in tracked memory.

## Recommended Source-Pack Layout

```text
source-packs/
  batches/
    millefeuille/<batch-id>/
      reports/
        acceptance-batch-summary.json
        acceptance-batch-summary.md
      classification/
        batch-classification-summary.json
        batch-classification-report.md
  zotero/<paper-slug>/
    manifest.json
    source.pdf                  # v0.1 one-PDF pack
    sources/                    # v0.2 same-item multi-PDF pack
      <attachment-ref>.pdf
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
            actions/
              <action-id>/
                action-record.json
                action-record.md
                final-decision.json
                final-decision.md
                zotero-writeback-preview.json
                adjudication-queue.jsonl
                taxonomy-change-requests.jsonl
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
mismatch, and refuses to overwrite an existing pack whose source files or
manifest drift from the evidence.

One-PDF `manifest.json` files follow
`millefeuille-source-pack-manifest/v0.1` and record:

- `paper_id`, `source_type`, and top-level `source_hash`;
- `source.ref` (`source.pdf`), byte size, format, and source SHA-256;
- Zotero item/attachment identity and canonical filename;
- verification status/method plus expected and actual SHA-256;
- fixture intake provenance and sanitized recovery/policy hints.

Same-item multi-PDF groups follow
`millefeuille-source-pack-manifest/v0.2`. They record an item-level identity,
one `sources[]` entry per attachment with its own deterministic ref, identity,
verification, and provenance, plus a top-level `sha256-aggregate:<hex>` hash
computed from the sorted source SHA-256 values. Existing v0.1 packs and
single-PDF intake output remain unchanged.

The fixture-first intake command does not read Zotero, download PDFs, call OCR
or model providers, write OpenKB/PageIndex/ConDB/ChatIndex data, or mutate
Zotero tags/notes. Live recovery and live source-pack writes remain separate
approved-live stages.

## Required Run Artifacts

Every non-trivial run should emit:

- `stage-manifest.json`
- `artifact-index.json`
- `model-provenance.jsonl` when any model or OCR provider is used
- `model-execution-plan.json` may be emitted before a separately approved live
  summary call; the v0.1 no-call plan records requested controls and provenance
  requirements but never claims actual provider execution or usage
- `reports/acceptance-summary.json` once the acceptance stage is synthesized
- `classification/classification-plan.json` when classification preview or live
  classification runs
- `classification/zotero-writeback-preview.json` when classification proposes
  future Zotero state
- `quality-report.md`
- `completion-gate-result.json`
- `zotero-writeback-plan.json` when Zotero state would change

The read-only status surface consumes `completion-gate-result.json` only as a
strict `millefeuille-completion-gate-result/v0.1` public JSON artifact owned by
the release stage. A future approved-live writeback result is a strict
`millefeuille-zotero-writeback-result/v0.1` public JSON artifact owned by the
writeback stage. Both are content-addressed, contain sanitized identities and
counts only, and require the applicable manual approval upstream. A writeback
result also binds the exact source-pack item version and exact observed
post-write item version; status never creates either record.

An offline acceptance batch additionally emits one run-scoped acceptance
summary per validated locator plus
`batches/millefeuille/<batch-id>/reports/acceptance-batch-summary.{json,md}`.
Batch reports contain portable source-root-relative refs and aggregate status;
they do not copy paper text, PDFs, credentials, or provider payloads.

An offline classification batch preserves each run-scoped classification
package and additionally emits
`batches/millefeuille/<batch-id>/classification/batch-classification-summary.json`
plus `batch-classification-report.md`. These aggregate artifacts lock the
taxonomy version, group portable decision refs by primary path, and retain
review/adjudication status without copying classification evidence content.

An offline classification review or adjudication action emits an immutable
package at `classification/actions/<action-id>/`. Its
`millefeuille-classification-action/v0.1` record links the prior decision to a
final decision and action-scoped writeback preview. The run's classification
plan, canonical preview, stage manifest, and artifact index are advanced to that
final decision without overwriting the prior record. Unresolved actions also
emit a one-row `adjudication-queue.jsonl`; taxonomy-gap actions emit a one-row
`taxonomy-change-requests.jsonl`. Empty queue/request files remain present for
deterministic package shape and exact-rerun verification.

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
- A selected run package contains one regular `artifact-index.json` and one
  regular stage manifest. A custom stage-manifest filename is valid only when
  every indexed stage and the stage-manifest artifact record reference it.
- The artifact index's `artifact_root`, source-pack ref, paper id, run id,
  source hash, source identity, stage set, and stage statuses must revalidate
  against the selected package before reads, resume, or writes.
- Explicit locator paths must not contain parent traversal or pass through a
  symbolic link or Windows reparse point. A container matching more than one
  declared run layout is ambiguous and rejected.
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
