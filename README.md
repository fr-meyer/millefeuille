# Millefeuille

![Version](https://img.shields.io/badge/version-0.4.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/fr-meyer/millefeuille?utm_source=oss&utm_medium=github&utm_campaign=fr-meyer%2Fmillefeuille&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)

Millefeuille runs the source-pack, extraction, OpenKB handoff, acceptance, and
classification lifecycle for research-paper attachments discovered from Zotero.

## Table of Contents

- [Quick Start](#quick-start)
- [Installation](#installation)
- [Runtime Prerequisites](#runtime-prerequisites)
- [Configuration](#configuration)
- [Item Selection & Tagging](#item-selection--tagging)
- [Tag Adding](#tag-adding)
- [PDF Download](#pdf-download)
- [OpenKB/Millefeuille Handoff Export](#openkbmillefeuille-handoff-export)
- [Artifact and Status Inspection](#artifact-and-status-inspection)
- [Extraction Modes](#extraction-modes)
- [Troubleshooting](#troubleshooting)
- [License](#license)
- [Contributing](#contributing)

## Quick Start

Complete the [Installation](#installation) and [Runtime Prerequisites](#runtime-prerequisites) steps first, then follow this workflow:

1. **Tag items in Zotero:**
   - Tag items you want to process with the configured include tag (default: `millefeuille`)

2. **Run the pipeline:**
   ```bash
   millefeuille ocr.enabled=true
   # or equivalently:
   python -m millefeuille ocr.enabled=true
   ```

3. **Verify results:**
   - Check your Zotero library for newly created notes
   - Successfully processed items are tagged with the configured success tag(s) (default: `millefeuille-processed`)

### Dry-Run Mode

Test your configuration without creating notes:
```bash
millefeuille processing.dry_run=true ocr.enabled=true
# or equivalently:
python -m millefeuille processing.dry_run=true ocr.enabled=true
```

Dry-run mode performs the full item-selection logic but **does not write any tags or notes** to Zotero. For every matched item the following details are logged:

- **Title**
- **Citation key** — displayed as `[none]` when absent
- **DOI** — displayed as `[none]` when absent
- **Author summary** — e.g. `"Smith, Doe, and Lee"` or `[no authors]`
- **PDF attachment count**
- **Current tags** on the item
- **Would-apply tags** — the success tags that would be written on a real run

A summary line is printed at the end showing the total matched items, total PDFs, and total excluded items.

**Export-only dry-run (no OCR key required):** To validate discovery and log attachment URLs without OCR, use:

```bash
millefeuille processing.dry_run=true export.attachment_urls.enabled=true
# or equivalently:
python -m millefeuille processing.dry_run=true export.attachment_urls.enabled=true
```

This mode does **not** require `PAGEINDEX_API_KEY` or `MISTRAL_API_KEY`. It runs discovery, logs one `[DISCOVERY URL]` record per PDF attachment, and exits — no bytes are downloaded, no notes are written, and no OCR credentials are needed.

### Safe Subset Workflow

Use this five-step staging-tag pattern to validate configuration on a small subset before running on your full library:

1. Tag 2–5 items in Zotero with a staging tag (e.g. `millefeuille-test`).
2. Override `tagging.selection.include.values=[millefeuille-test]` on the CLI.
3. Run with `processing.dry_run=true` to preview.
4. Confirm output, then run live on the subset.
5. Only then widen to the full `millefeuille` tag set.

This pattern applies to selection-retagging, OCR, download, and the OpenKB handoff export.

## Installation

### Install the package

```bash
pip install .        # standard install
pip install -e .     # editable / development install
```

After installation, `millefeuille` is available on `PATH` and can be verified from any directory:
```bash
millefeuille --help
```

### Optional: conda environment

If you prefer using conda for dependency isolation:
```bash
conda env create -f environment.yml
conda activate millefeuille
pip install .
```

The `environment.yml` file includes all required dependencies: Python 3.11+, Hydra Core, PyZotero, Mistral AI SDK, PageIndex SDK, and markdown processing libraries.

## Runtime Prerequisites

The following environment variables must be set **before running** the pipeline.

### Zotero credentials (required)

```bash
export ZOTERO_LIBRARY_ID="your-numeric-library-id"
export ZOTERO_READ_KEY="your-zotero-read-api-key"
```

`ZOTERO_LIBRARY_ID` is your numeric user library ID, visible in your Zotero web
library URL (`https://www.zotero.org/users/{id}`). `ZOTERO_READ_KEY` is
required for discovery, export, dry-run, and other read operations.

Use one of these credential patterns:

```bash
# Read-only
export ZOTERO_LIBRARY_ID="your-numeric-library-id"
export ZOTERO_READ_KEY="your-zotero-read-api-key"

# Writeback
export ZOTERO_LIBRARY_ID="your-numeric-library-id"
export ZOTERO_READ_KEY="your-zotero-read-api-key"
export ZOTERO_WRITE_KEY="your-zotero-write-api-key"

# Single-key writeback
export ZOTERO_LIBRARY_ID="your-numeric-library-id"
export ZOTERO_READ_KEY="your-zotero-write-api-key"
export ZOTERO_WRITE_KEY="your-zotero-write-api-key"
```

`ZOTERO_WRITE_KEY` is required only when write-capable features are active:
tag adding (`tag_adding.enabled=true`), note creation (`ocr.enabled=true`),
selection tagging, and success/error tagging. You may omit `ZOTERO_WRITE_KEY`
for read-only or export-only runs. Obtain API keys from
[Zotero key settings](https://www.zotero.org/settings/keys).

### OCR provider key (required only when OCR is enabled)

```bash
export PAGEINDEX_API_KEY="your-pageindex-api-key"  # For PageIndex OCR
# or
export MISTRAL_API_KEY="your-mistral-api-key"      # For Mistral OCR
```

**Provider Setup:**
- **PageIndex:** Obtain API key from [PageIndex dashboard](https://docs.pageindex.ai). Optional SDK mode: install with `pip install pageindex` and set `use_sdk: true` in `millefeuille/conf/ocr/pageindex.yaml`.
- **Mistral:** Obtain API key from [Mistral platform](https://docs.mistral.ai).

> **Note:** `PAGEINDEX_API_KEY` and `MISTRAL_API_KEY` are required **only** when `ocr.enabled=true`. The following run modes work **without** any OCR provider key: export-only dry-run (`processing.dry_run=true export.attachment_urls.enabled=true`), tag-adding-only (`tag_adding.enabled=true`), and download-only (`download.enabled=true`).

> **Note:** Missing or invalid environment variables may cause the CLI to error before help text is shown. If you see unexpected errors on startup, verify your environment variables are set correctly.

## Configuration

The pipeline uses Hydra for configuration management. Most settings can be overridden via command-line arguments. This section covers essential options and command-line configuration.

### Essential Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `ocr.provider` | string | `pageindex` | OCR provider selection (`mistral` or `pageindex`) |
| `ocr.enabled` | boolean | `false` | Enable OCR processing (required to create notes) |
| `download.enabled` | boolean | `false` | Enable PDF download from Zotero |
| `tag_adding.enabled` | boolean | `false` | Enable citationKey-based Zotero tag assignment |
| `tree_structure.enabled` | boolean | `true` | Enable hierarchical tree structure extraction |
| `processing.extraction_mode` | string | `all_at_once` | Note organization mode (`all_at_once` or `page_by_page`) |
| `processing.batch_size` | integer | varies | Batch processing size (provider-dependent) |
| `tagging.selection.include.values` | list | `[millefeuille]` | Tags items must match to be selected for processing |
| `tagging.selection.include.operator` | string | `or` | How include tags are combined (`and` / `or`) |
| `tagging.selection.exclude.values` | list | `[millefeuille-processed]` | Tags that disqualify items from selection |
| `tagging.selection.exclude.operator` | string | `or` | How exclude tags are combined (`and` / `or`) |
| `tagging.apply_on_success.values` | list | `[millefeuille-processed]` | Tags added to items on successful processing |
| `tagging.apply_on_error.values` | list | `[millefeuille-error]` | Tags added to items on failed processing |
| `tagging.include_abstract` | boolean | `false` | Include abstract in `paper_metadata` passed to OCR |
| `zotero.error_tagging_enabled` | boolean | `true` | Whether error tags are applied on failure |
| `download.upload_folder` | string | — | Example: `./downloads`. Must be set to an explicit path when `download.enabled=true` (packaged placeholders are rejected). |
| `download.max_concurrent_downloads` | integer | `5` | Maximum number of concurrent downloads |
| `storage.base_dir` | string | — | Example: `./data/ocr_output`. Must be set to an explicit path when `processing.save_to_disk=true` (packaged placeholders are rejected). |
| `processing.cleanup_uploaded_files` | boolean | `false` | Controls file deletion after processing (default: false = keep files) |

> **Explicit output paths required in path-consuming modes:**
> - `download.enabled=true` requires an explicit `download.upload_folder` override.
> - `processing.save_to_disk=true` requires an explicit `storage.base_dir` override.
>
> The packaged placeholder defaults are rejected; the CLI will exit with an actionable error if they are not overridden.

### Choosing an OCR Provider

The pipeline supports two OCR providers:

| Feature | PageIndex | Mistral |
|---------|-----------|---------|
| Processing Type | Asynchronous | Synchronous |
| Batch Processing | Optimized (FIFO queue) | Single document |
| Tree Structure | ✅ Full support | ⚠️ Requires PageIndex credentials |
| Best For | Batch processing, hierarchical organization | Single documents, fast processing |

**Recommendation:** For new installations, **PageIndex OCR** is recommended for batch processing and hierarchical document organization. Use **Mistral OCR** for single-document processing when tree structure is not needed.

## Artifact and Status Inspection

The read-only artifact commands inspect Millefeuille artifact indexes without
initializing Zotero, OCR providers, OpenKB, PageIndex, ConDB, ChatIndex, or
model clients.

### Preview Stage Commands

For offline artifact-package validation, Millefeuille also exposes preview and
read-only stage commands that operate on a verified source-pack run directory:

```bash
millefeuille extract-native --source-pack-root ./source-packs --paper-id zotero-ITEM1 --run-id run-001 --evidence native-evidence.json
millefeuille acceptance --source-pack-root ./source-packs --paper-id zotero-ITEM1 --run-id run-001 --handoff handoff.jsonl
millefeuille classify --source-pack-root ./source-packs --paper-id zotero-ITEM1 --run-id run-001 --evidence classification-evidence.json
millefeuille writeback --source-pack-root ./source-packs --paper-id zotero-ITEM1 --run-id run-001 --writeback preview
millefeuille retrieve --source-pack-root ./source-packs --paper-id zotero-ITEM1 --run-id run-001
millefeuille retrieve --source-pack-root ./source-packs --doi https://doi.org/10.1234/example --run-id run-001 --section methods --evidence-need classification --json
millefeuille retrieve --source-pack-root ./source-packs --batch-manifest retrieval-batch.json --summary-scope classification --json
millefeuille models
millefeuille run --source-pack-root ./source-packs --paper-id zotero-ITEM1 --run-id run-001 --stages acceptance,classify,writeback --handoff handoff.jsonl --classification-evidence classification-evidence.json --release-preflight
```

These commands stay offline and preview-only in the current contract slice:

- `acceptance` synthesizes a final verdict from handoff, source-pack,
  extraction, route, structure, summary, card, and index evidence.
- `classify` materializes classification plans, decision records, review
  queues, and writeback previews from explicit local evidence.
- `writeback` turns the preview classification output into a governed Zotero
  writeback plan without mutating Zotero.
- `retrieve` resolves verified packages by paper id, Zotero item key, source-pack
  slug, normalized DOI, or normalized exact title, then lists stored artifact
  references for downstream inspection. Optional scope, grain, index-lane,
  section, page, and classification-evidence filters never paste private paper
  content. Corpus ambiguity, identity drift, traversal, and invalid page queries
  fail closed. Batch mode applies the same filters uniformly after preflighting
  every manifest locator and emits only sorted root-relative refs and status
  metadata.
- `models` prints the bundled offline model-profile catalog.
- `extract-native`, `extract-ocr`, `route`, `structure`, `summarize`, `card`,
  and `index` expose the existing fixture writers as explicit single-paper
  stages and update the run manifest and artifact index.
- `run` chains those fixture stages through `acceptance`, `classify`, and
  `writeback` in canonical order, supports `--resume` with output
  revalidation, and can emit optional release-candidate preflight reporting.
- Derived writes and resume fail closed when paper, run, source hash, source
  identity, stage set, or stage status differs across the source-pack manifest,
  stage manifest, and artifact index.
- `--mode approved-live` and approved-live writeback stop at exit code `3`;
  these commands never turn a preview invocation into a live provider or
  Zotero mutation.

### Offline Batch Retrieval

To inspect several existing source-pack runs in one deterministic operation,
provide a strict versioned retrieval manifest instead of a single locator:

```bash
millefeuille retrieve \
  --source-pack-root /path/to/source-packs \
  --batch-manifest /path/to/retrieval-batch.json \
  --evidence-need classification \
  --json
```

The manifest uses `millefeuille-retrieval-batch-manifest/v0.1`, supplies one
traversal-safe `batch_id`, and contains a non-empty `runs` array. Every entry
has `run_id` and exactly one of `paper_id`, `item_key`, `slug`, `doi`, or
`title`; unknown fields and non-string locator values are rejected. The usual
scope, grain, index-lane, section, page, and classification-evidence filters
apply coherently to every resolved run. `--batch-manifest` is preview-only:
every non-preview mode is rejected independently of the generic live-mode gate.
It must not be empty and cannot be combined with direct locators or `--run-id`.

Millefeuille resolves and validates the complete manifest before writing. A
missing or ambiguous identity, duplicate resolved paper/run pair, drifted
package, unsafe artifact ref, or malformed later entry prevents all aggregate
writes. Successful preflight emits byte-stable, sorted artifacts at:

- `batches/millefeuille/<batch-id>/retrieval/batch-retrieval-result.json`
- `batches/millefeuille/<batch-id>/retrieval/batch-retrieval-report.md`

The JSON follows `millefeuille-retrieval-batch-result/v0.1`. Both views contain
only portable refs, locator/source/acceptance status, optional
classification/writeback refs, and aggregate counts. Summary matches are
allowlisted to `grain`, `scope`, and portable `text_ref`; artifact-controlled
`summary_id` values are deliberately excluded. Index lanes are allowlisted to
`lane` and `status`. On supported POSIX local filesystems, batch preflight pins
one no-follow source-root descriptor and opens every source-pack manifest, card,
summary, index, optional artifact, and referenced full-text/summary path relative
to it, without path-based reads or symlink following. Stable file identities,
missing optional inputs, and enumerated corpus entries are snapshotted. Only an
actual absent path is optional; an optional path that exists as a directory,
FIFO, or other non-regular entry fails closed. The external batch manifest is
read no-follow and retained byte-for-byte. Publication
then pins the batch-directory inode, locks the batch directory, creates staging files
exclusively by descriptor, and keeps independently opened read-only verification
and private read/write cleanup descriptors for each owned inode. While the
randomized mode-`0700` staging generation is still unpublished,
Millefeuille validates its exact entry set, rereads both expected byte streams,
rechecks names, inode identities, file metadata, and single-link counts, then
changes the files to mode `0444` and the generation directory to mode `0555`.
A second held-descriptor verification follows those permission changes. The
atomic no-replace rename of that complete generation is the publication commit
point; the parent directory is fsynced afterward for durability, with no
post-publication validation window before commit. If any pre-commit step fails,
Millefeuille truncates and fsyncs only the owned staged inodes through their
retained cleanup descriptors, even if a staged name was displaced or replaced,
so a raced external hard link cannot retain aborted aggregate bytes. Failure
cleanup deliberately does not unlink, rename, or remove namespace
entries because an uncooperative same-UID replacement cannot be conditionally
mutated atomically; unverified entries remain untouched. The failed temporary
generation therefore remains in its reached mode (`0700` or `0555`) with owned
output files reduced to zero bytes for explicit operator cleanup. Exact
byte-identical reruns verify opened read-only regular-file descriptors and no-op,
while incomplete, writable, drifted, symlinked, hard-linked, displaced, or
substituted output fails closed instead of being overwritten. Immediately before
the rerun no-op or atomic rename, while the cooperative batch lock is held,
Millefeuille reopens and revalidates every snapshotted input and corpus entry and
rereads the external batch manifest. Namespace, entry-set, hard-link, or
same-inode content changes observed before that final check fail closed.

The lock serializes cooperating Millefeuille writers; POSIX `flock`, mode bits,
and descriptor checks cannot stop an uncooperative same-UID owner from changing
source inputs or staging after the last validation and before the rename, or
from mutating files after publication. The contract therefore requires trusted
ownership, cooperative same-UID writers, or stronger immutable/content-addressed
storage and does not claim protection against that adversary. The atomic rename
is the completed-operation boundary inside this stated trust model. Platforms or
filesystems without the
required POSIX directory-lock, no-replace rename, and descriptor-relative
primitives fail closed before aggregate publication; package import and legacy
single-run retrieval use their portable legacy read fallback when POSIX
`O_NOFOLLOW` is unavailable. That fallback rejects parent traversal, lstat-checks
every parent and target, rejects symbolic links and non-regular entries, and
binds the opened descriptor to the checked identity before reading. This
preview-only path never includes summary or
paper-card prose, PDFs, or provider payloads, and does not read live Zotero,
recover PDFs, call OCR/models/providers, write OpenKB or an index, or grant
approval for publication or release operations.

### Offline Batch Acceptance

To validate several existing source-pack runs in one deterministic offline
operation, provide a versioned batch manifest instead of a single paper and run
identifier:

```bash
millefeuille acceptance \
  --source-pack-root /path/to/source-packs \
  --batch-manifest /path/to/acceptance-batch.json \
  --handoff /path/to/handoff.jsonl \
  --json
```

The manifest uses `millefeuille-acceptance-batch-manifest/v0.1`, has one safe
`batch_id`, and lists unique `paper_id` or `item_key` plus `run_id` locators.
Millefeuille verifies every referenced package and the shared handoff before it
writes any acceptance output. Successful preflight writes the existing
per-run summaries plus a sorted aggregate report at
`batches/millefeuille/<batch-id>/reports/acceptance-batch-summary.{json,md}`.
The aggregate passes only when every run passes; otherwise it records
`needs-review` while retaining the valid per-run results.

This path remains local and fixture-only. It does not read or mutate live
Zotero data, recover PDFs, call OCR/model/OpenKB/index providers, execute
worker agents, or approve a release.

### Offline Batch Classification

To route several accepted runs under one locked taxonomy without model or
worker execution, provide a classification batch manifest:

```bash
millefeuille classify \
  --source-pack-root /path/to/source-packs \
  --batch-manifest /path/to/classification-batch.json \
  --json
```

The manifest uses `millefeuille-classification-batch-manifest/v0.1`, supplies
one traversal-safe `batch_id` and `taxonomy_version`, and lists unique
`paper_id` or `item_key` plus `run_id` locators. Each entry also supplies a
traversal-safe `evidence_ref` relative to the manifest. Every run must already
have passing acceptance evidence, and every referenced classification evidence
file must declare `mode: batch` and the locked taxonomy version.

Millefeuille preflights the complete batch before writing. It preserves the
existing per-run classification artifacts and emits deterministic aggregate
routing output at
`batches/millefeuille/<batch-id>/classification/batch-classification-summary.json`
and `batch-classification-report.md`. The aggregate groups decisions by primary
taxonomy path and counts `classified`, `needs-review`, and
`adjudication-required` results. Review and adjudication outcomes return exit
code `2` without suppressing valid per-run outputs.

This path consumes explicit local evidence only. It does not call a model,
execute worker agents, mutate a taxonomy, write Zotero/OpenKB/index state, or
grant approval for any live or release operation.

### Offline Classification Review And Adjudication

To record a deterministic review or adjudication action for one accepted run,
provide a versioned action-evidence file alongside the run locator:

```bash
millefeuille classify \
  --source-pack-root /path/to/source-packs \
  --paper-id zotero-ITEM1 \
  --run-id run-fixture \
  --action-evidence /path/to/classification-action.json \
  --json
```

Action evidence uses
`millefeuille-classification-action-evidence/v0.1`, names a traversal-safe
`action_id`, and identifies an existing run-relative decision record under the
same accepted paper package. Millefeuille validates the prior paper, run,
source hash, taxonomy version, and evidence refs before writing. Review accepts
`no-change`, `corrected`, or `escalated`; adjudication accepts `confirmed`,
`corrected`, or `taxonomy-change-requested`. Adjudication starts only from a
`needs-review` or `adjudication-required` decision.

Each immutable action package is written under
`classification/actions/<action-id>/` with an action record, final decision,
Markdown views, writeback preview, and deterministic queue or taxonomy-request
JSONL when applicable. The current classification plan, artifact-index refs,
stage status, and canonical writeback preview then point to the final action
state while preserving the prior decision. Exact reruns are byte-stable; reuse
of an action ID with drift is rejected before overwrite.

Resolved actions return exit code `0`. `escalated` and
`taxonomy-change-requested` actions materialize their audit artifacts and return
exit code `2`. This path consumes explicit local evidence only: it does not call
a model, execute worker agents, mutate the locked taxonomy, or write live
Zotero/OpenKB/index/source-pack state.

```bash
millefeuille artifacts --index /path/to/artifact-index.json
millefeuille status --index /path/to/artifact-index.json
millefeuille status --artifact-root /path/to/run --strict
```

These commands are intentionally outside the Hydra pipeline path. They do not
require `ZOTERO_LIBRARY_ID`, `ZOTERO_READ_KEY`, `ZOTERO_WRITE_KEY`,
`MISTRAL_API_KEY`, or `PAGEINDEX_API_KEY`.

Use `--json` when another tool or agent needs machine-readable output:

```bash
millefeuille status --index /path/to/artifact-index.json --json
```

`millefeuille status --strict` exits with code `2` when the artifact index has
blocking stage, index, or approved-live writeback states such as `failed`,
`needs-review`, `manual-gate`, or `not-started`.

Dry-run artifact writing can seed the artifact/status surface from the existing
Zotero discovery and OpenKB handoff preview path:

```bash
millefeuille --artifact-root /path/to/millefeuille-artifacts --run-id dry-run-demo \
  processing.dry_run=true export.openkb_handoff.enabled=true
```

This writes `stage-manifest.json` and `artifact-index.json` under
`<artifact-root>/<paper-id>/<run-id>/` for each discovered paper. It does not
download PDFs, recover source packs, call OCR/model/index providers, write
OpenKB/PageIndex/ConDB/ChatIndex data, or mutate Zotero.

When a verified source pack already exists, `--artifact-root source-pack` writes
the same run artifacts into the source pack analysis area:

```bash
millefeuille --artifact-root source-pack \
  --source-pack-root /path/to/source-packs \
  --run-id dry-run-demo \
  processing.dry_run=true export.openkb_handoff.enabled=true
```

This requires `<source-pack-root>/zotero/<paper-id>/manifest.json` and writes
only under
`<source-pack-root>/zotero/<paper-id>/analyses/millefeuille/<run-id>/`. It does
not create source packs or alter source evidence; a missing manifest is a hard
configuration/evidence error.

To create a source pack from explicit local recovered-PDF fixture evidence, use
the offline intake command:

```bash
millefeuille source-pack intake \
  --evidence /path/to/recovered-pdf-evidence.json \
  --source-pack-root /path/to/source-packs
```

The evidence JSON must include the Zotero item key, attachment key, canonical
filename, local recovered PDF path, and expected SHA-256. The command verifies
the recovered bytes before writing `manifest.json` and either `source.pdf` for
a one-PDF item or deterministic `sources/*.pdf` entries for a same-item
multi-PDF group. It refuses mismatched hashes or existing pack drift. It does
not read Zotero, download PDFs, call OCR/model providers, write OpenKB/index
data, or mutate Zotero.

The staged dry-run handoff path can also create source packs from explicit
fixture evidence before writing the run artifacts:

```bash
millefeuille --artifact-root source-pack \
  --source-pack-root /path/to/source-packs \
  --source-pack-intake-evidence /path/to/recovered-pdf-evidence.jsonl \
  --run-id dry-run-demo \
  processing.dry_run=true export.openkb_handoff.enabled=true
```

The evidence file may be one JSON object, a JSON array, an object containing
`evidence` or `records`, or JSONL. Each selected PDF handoff row must have a
matching evidence record with the same Zotero item key, attachment key,
canonical filename, file size, and SHA-256. The command preflights every row
before writing any source pack. It then writes only local source-pack evidence
and Millefeuille artifact manifests under the explicit `--source-pack-root`;
it does not write Zotero, OCR/model providers, OpenKB, PageIndex, ConDB, or
ChatIndex.

One-PDF source packs retain the v0.1 `source.pdf` manifest contract. Same-item
multi-PDF handoff groups use the v0.2 manifest contract: each attachment has a
deterministic traversal-safe `sources/*.pdf` ref, per-source identity and hash
evidence, and the pack has a deterministic `sha256-aggregate:<hex>` hash. The
intake result is one source pack per Zotero item and is idempotent regardless of
handoff-row order.

Once a source pack already exists, the dry-run artifact lane can also stage
fixture extraction sidecars before writing the run artifacts:

```bash
millefeuille --artifact-root source-pack \
  --source-pack-root /path/to/source-packs \
  --native-extraction-evidence /path/to/native-extraction-evidence.jsonl \
  --ocr-extraction-evidence /path/to/ocr-extraction-evidence.jsonl \
  --run-id dry-run-demo \
  processing.dry_run=true
```

Each extraction evidence record points to a local Markdown fixture file and the
same Zotero item key, attachment key, canonical filename, and SHA-256 already
verified by the source-pack manifest. The dry-run path preflights every native
or OCR record before writing any `extractions/native/*`,
`extractions/mistral-ocr/*`, or artifact-index output. It records requested OCR
model plus returned provider model/version in the OCR sidecar, but still does
not call OCR/model providers, write OpenKB/PageIndex/ConDB/ChatIndex, or mutate
Zotero.

Once both extraction sidecars exist, the same dry-run path can stage a route
selection fixture:

```bash
millefeuille --artifact-root source-pack \
  --source-pack-root /path/to/source-packs \
  --native-extraction-evidence /path/to/native-extraction-evidence.jsonl \
  --ocr-extraction-evidence /path/to/ocr-extraction-evidence.jsonl \
  --route-selection-evidence /path/to/route-selection-evidence.jsonl \
  --run-id dry-run-demo \
  processing.dry_run=true
```

Route fixture evidence writes `selected/route.json` plus `selected/fulltext.md`
only after the source-pack manifest and required extraction sidecars are
verified. `selected_route=native` requires native extraction evidence,
`selected_route=ocr` requires OCR extraction evidence, and
`selected_route=merged-dual` requires both. The dry-run artifact index then
marks `route` passed and exposes the selected fulltext ref without calling
providers or writing OpenKB/index lanes.

Once the route sidecar exists, the dry-run path can stage structure fixture
evidence:

```bash
millefeuille --artifact-root source-pack \
  --source-pack-root /path/to/source-packs \
  --structure-evidence /path/to/structure-evidence.jsonl \
  --run-id dry-run-demo \
  processing.dry_run=true
```

Structure fixture evidence writes `structure/structure.json` plus optional
`structure/outline.md` only after the source-pack manifest, selected route, and
selected fulltext are verified. The dry-run artifact index then marks
`structure` passed and exposes the structure refs without calling model
providers or writing OpenKB/index lanes.

Once the structure sidecar exists, the same dry-run path can stage
hierarchical-summary fixture evidence:

```bash
millefeuille --artifact-root source-pack \
  --source-pack-root /path/to/source-packs \
  --structure-evidence /path/to/structure-evidence.jsonl \
  --summary-evidence /path/to/summary-evidence.jsonl \
  --run-id dry-run-demo \
  processing.dry_run=true
```

Summary fixture evidence points to a local
`millefeuille-hierarchical-summary/v0.1` JSON file whose `text_ref` files
resolve beside the fixture. The dry-run path preflights the source-pack
manifest plus existing structure sidecar, then writes
`analyses/millefeuille/<run-id>/summaries/hierarchical-summary.json` and
`summaries/texts/*.md` under the run directory. The artifact index marks
`summarize` passed and exposes the summary bundle without calling model
providers or writing OpenKB/index lanes.

Once the summary bundle exists, the same dry-run path can stage paper-card
fixture evidence:

```bash
millefeuille --artifact-root source-pack \
  --source-pack-root /path/to/source-packs \
  --summary-evidence /path/to/summary-evidence.jsonl \
  --card-evidence /path/to/card-evidence.jsonl \
  --run-id dry-run-demo \
  processing.dry_run=true
```

Paper-card fixture evidence points to a local
`millefeuille-paper-card/v0.1` JSON file plus Markdown card content. The
dry-run path preflights the source-pack manifest and existing hierarchical
summary bundle, then writes `cards/paper-card.json` and `cards/paper-card.md`
under `analyses/millefeuille/<run-id>/`. The artifact index marks `card`
passed and exposes both refs without calling model providers or writing
OpenKB/index lanes.

Once the paper-card bundle exists, the same dry-run path can stage retrieval
and index fixture evidence:

```bash
millefeuille --artifact-root source-pack \
  --source-pack-root /path/to/source-packs \
  --card-evidence /path/to/card-evidence.jsonl \
  --index-evidence /path/to/index-evidence.jsonl \
  --run-id dry-run-demo \
  processing.dry_run=true
```

Index fixture evidence points to a local
`millefeuille-retrieval-index-status/v0.1` JSON file. The dry-run path
preflights the source-pack manifest, selected fulltext, hierarchical summary,
and paper card, then writes `index/index-status.json` under
`analyses/millefeuille/<run-id>/`. The artifact index marks `index` passed and
surfaces the lane statuses without writing OpenKB/PageIndex/ConDB/ChatIndex.

### Command-Line Configuration

Override configuration from the command line:
```bash
# Change OCR provider
millefeuille ocr=pageindex ocr.enabled=true
# or: python -m millefeuille ocr=pageindex ocr.enabled=true

# Enable/disable tree extraction
millefeuille tree_structure.enabled=true ocr.enabled=true
# or: python -m millefeuille tree_structure.enabled=true ocr.enabled=true

# Change extraction mode
millefeuille processing.extraction_mode=page_by_page ocr.enabled=true
# or: python -m millefeuille processing.extraction_mode=page_by_page ocr.enabled=true

# Multiple overrides
millefeuille ocr=pageindex tree_structure.enabled=true processing.dry_run=true ocr.enabled=true
# or: python -m millefeuille ocr=pageindex tree_structure.enabled=true processing.dry_run=true ocr.enabled=true
```

## Item Selection & Tagging

The pipeline uses a flexible, config-driven tagging system to control which items are processed and how they are tagged afterward. All tagging behaviour is defined under the `tagging` config group.

### Item Selection (`tagging.selection`)

Items are selected for processing based on **include** and **exclude** tag rules:

- **`selection.include.values`** — a list of tags an item must carry to be eligible. Multiple tags are combined with the `include.operator` (`and` requires all tags; `or` requires at least one).
- **`selection.exclude.values`** — a list of tags that disqualify an item. Combined with the `exclude.operator` using the same logic.
- **`conflict_resolution`** controls what happens when an item matches both include and exclude rules:
  - **`exclude_wins`** *(default)* — the item is excluded and not processed.
  - **`include_wins`** — the item is included and processed normally.

### Success and Error Tagging

After processing, the pipeline applies outcome-based tags:

- **`apply_on_success.values`** — tags added to items that were processed successfully (default: `[millefeuille-processed]`).
- **`apply_on_error.values`** — tags added to items that failed during processing (default: `[millefeuille-error]`). Error tagging can be disabled globally by setting `zotero.error_tagging_enabled: false`.

### Rich Metadata

Setting `tagging.include_abstract: true` includes the item's abstract in `paper_metadata` passed to the OCR provider, which can improve extraction quality for academic papers.

### Processing Summary Output

Each processed item's entry in `processing_summary.json` contains a nested **`paper_metadata`** block with the following fields:

- `citation_key`, `title`, `doi`, `item_type`, `date`, `year`, `publication_title`
- `authors`, `editors`, `author_count`, `author_string`
- `tags`, `collections`, `zotero_uri`
- `attachments` (PDF-only)

**Omit-missing-keys rule:** optional fields that are absent from the Zotero item are omitted from the JSON entirely — they are never emitted as `null`.

**`tagging.include_abstract` flag:** when `true`, `abstract_note` is included in `paper_metadata`; when `false` (the default), it is omitted.

### Default Configuration

The full default tagging configuration (`millefeuille/conf/tagging/default.yaml`):

```yaml
# Tagging workflow configuration
selection:
  include:
    values:
      - millefeuille
    operator: "or"
  exclude:
    values:
      - millefeuille-processed
    operator: "or"
  conflict_resolution: "exclude_wins"

apply_on_success:
  values:
    - millefeuille-processed

apply_on_error:
  values:
    - millefeuille-error

include_abstract: false
```

### Overriding via CLI

Override tagging settings from the command line:

```bash
millefeuille "tagging.selection.include.values=[my-tag]"
# or: python -m millefeuille "tagging.selection.include.values=[my-tag]"
```

### Selection-Based Retagging

Use `selection_tagging` to add and remove tags on items matched by `tagging.selection` without running OCR or download:

- **`selection_tagging.enabled`** — enable selection-based retagging.
- **`selection_tagging.add.values`** — tags to add to each matched item.
- **`selection_tagging.remove.values`** — tags to remove from each matched item.

Dry-run preview:

```bash
python -m millefeuille \
  processing.dry_run=true \
  selection_tagging.enabled=true \
  "selection_tagging.add.values=[millefeuille-v2]" \
  "selection_tagging.remove.values=[millefeuille]"
```

Live run (same command without `processing.dry_run=true`):

```bash
python -m millefeuille \
  selection_tagging.enabled=true \
  "selection_tagging.add.values=[millefeuille-v2]" \
  "selection_tagging.remove.values=[millefeuille]"
```

`selection_tagging` and `tag_adding` are **mutually exclusive** — the CLI exits with a config error if both are enabled. A live run with `selection_tagging` requires `ZOTERO_WRITE_KEY`.

## Tag Adding

Optional step that applies Zotero tags to items by matching their **citation keys** (no OCR/notes are created in tag-adding-only mode).

### Key configuration
- `tag_adding.enabled`: set to `true` to enable tag adding.
- `tag_adding.assignments`: a dictionary mapping `citation_key -> list[str]` (tags to add).
- `tag_adding.replace_all_existing_tags` (opt-in, destructive): when `true`, all existing tags are replaced with the assigned tags.

### Citation key matching
- Matching is **exact**, **case-sensitive**, and **whitespace-trimmed**.
- The citation key is resolved from Zotero data using this precedence:
  1. `item["data"]["citationKey"]` (preferred when present and non-empty)
  2. a `Citation Key: <key>` line in `item["data"]["extra"]`

### Environment override (recommended for large mappings)
- If you set `TAG_ADDING_ASSIGNMENTS_JSON` to a JSON object of the form `{ "citation_key": ["tag1", "tag2"] }`, the app will enable tag-adding automatically.

Example:
```bash
export TAG_ADDING_ASSIGNMENTS_JSON='{"Smith2020":["millefeuille-tag1","millefeuille-tag2"]}'
millefeuille tag_adding.enabled=true
# or: python -m millefeuille tag_adding.enabled=true
```

## PDF Download

Optional step that downloads PDFs from Zotero items to local disk (used as an input for OCR, and also supported in download-only mode).

### Key configuration
- `download.enabled`: set to `true` to enable PDF downloads.
- `download.upload_folder`: local directory for downloaded PDFs (must be overridden with an explicit path when `download.enabled=true`).
- `download.preserve_filenames`: whether to preserve the original PDF filenames (default: `true`).
- `download.create_subfolders`: whether to create subfolders under `upload_folder` (default: `false`).
- `download.skip_existing`: whether to skip PDFs that already exist locally (default: `true`).
- `download.max_concurrent_downloads`: maximum number of concurrent downloads (default: `5`).
- Retry is configurable via `download.retry.*` (see `millefeuille/conf/download/default.yaml`).

### Important constraint
- `processing.dry_run=true` does not download PDFs. It can be combined with
  `download.enabled=true` only for selection-tagging preview workflows where
  `selection_tagging.enabled=true`.

Examples:
```bash
# Download-only (explicit output path required)
millefeuille download.enabled=true download.upload_folder=/path/to/downloads
# or: python -m millefeuille download.enabled=true download.upload_folder=/path/to/downloads

# Download + OCR
millefeuille download.enabled=true download.upload_folder=/path/to/downloads ocr.enabled=true
# or: python -m millefeuille download.enabled=true download.upload_folder=/path/to/downloads ocr.enabled=true
```

## OpenKB/Millefeuille Handoff Export

Produces a durable, credential-free JSONL file (`openkb-millefeuille-handoff/v0.1`) that an external OpenKB/Millefeuille helper can consume to recover and verify Zotero PDF attachments. No authenticated URLs, API keys, or file payloads are stored.

**Key facts:**

- `ZOTERO_READ_KEY` is sufficient — no write key needed.
- No OCR provider key required.
- Recovery uses Zotero API keys only; no pre-built URLs are stored.
- Optional `export.openkb_handoff.compute_sha256=true` transiently fetches
  attachment bytes in memory to compute SHA-256 and upgrade row verification
  to `full`; no PDF payload is written or stored.
- Live export runs the same validators as dry-run and fails closed on unsafe rows.
- Dry-run first is **recommended** but **not required** by the CLI.

**Validate-only dry-run (no output file):**

```bash
python -m millefeuille \
  processing.dry_run=true \
  export.openkb_handoff.enabled=true \
  "tagging.selection.include.values=[millefeuille-test]"
```

**Explicit preview mode (writes a non-authoritative preview file):**

```bash
python -m millefeuille \
  processing.dry_run=true \
  export.openkb_handoff.enabled=true \
  export.openkb_handoff.preview_jsonl_path=./handoff-dry-run.preview.jsonl \
  "tagging.selection.include.values=[millefeuille-test]"
```

Note: the preview file is non-authoritative and written only to `preview_jsonl_path`. The live `jsonl_path` is never written in dry-run.

**Strong verification preview/readiness run:**

Use this for merge readiness, dogfood, or production verification when Zotero-hosted PDFs should produce `verification_strength=full` and no weak-verification warning. It transiently fetches each PDF in memory to compute SHA-256, but does not write or store PDF payloads.

```bash
python -m millefeuille \
  processing.dry_run=true \
  ocr.enabled=false \
  download.enabled=false \
  tag_adding.enabled=false \
  selection_tagging.enabled=false \
  export.openkb_handoff.enabled=true \
  export.openkb_handoff.compute_sha256=true \
  export.openkb_handoff.preview_jsonl_path=./handoff-dry-run.preview.jsonl \
  "tagging.selection.include.values=[millefeuille-test]"
```

**Live export:**

```bash
python -m millefeuille \
  export.openkb_handoff.enabled=true \
  export.openkb_handoff.jsonl_path=./handoff.jsonl \
  "tagging.selection.include.values=[millefeuille]"
```

For production-grade handoff verification, include `export.openkb_handoff.compute_sha256=true` in the live command as well.

**Configuration reference:**

| Key | Type | Default | Description |
|---|---|---|---|
| `export.openkb_handoff.enabled` | bool | `false` | Enable handoff export |
| `export.openkb_handoff.jsonl_path` | string | `null` | Live output path (required for live export) |
| `export.openkb_handoff.preview_jsonl_path` | string | `null` | Preview output path (dry-run only) |
| `export.openkb_handoff.include_item_type` | bool | `true` | Include parent item type |
| `export.openkb_handoff.include_zotero_version` | bool | `true` | Include Zotero item version |
| `export.openkb_handoff.compute_sha256` | bool | `false` | Transiently fetch PDF bytes and compute SHA-256 for `full` verification |

## Extraction Modes

Choose how pages are organized into notes. Both modes work with all OCR providers. Use `all_at_once` (default) to consolidate all document content in a single note, or `page_by_page` for separate notes per page.

| Feature | All-at-Once (Default) | Page-by-Page |
|---------|----------------------|--------------|
| Notes per PDF | Single note | One note per page |
| Page separators | `--- Page N ---` markers | N/A (each page is separate) |
| Note title format | `"{filename} -\nOCR (All Pages)"` | `"Page N -\n{filename} -\nOCR"` |
| Best for | Single consolidated view, easier searching | Page-by-page organization, large documents |
| Size limits | Auto-splits if content exceeds threshold | Auto-splits per page if needed |

## Troubleshooting

Common issues and solutions when running the pipeline:

### API Key and Tree Extraction Issues

**API keys**: Ensure the correct environment variable is set (see [Runtime Prerequisites](#runtime-prerequisites)):
- `PAGEINDEX_API_KEY` for PageIndex OCR
- `MISTRAL_API_KEY` for Mistral OCR

These keys are only required when `ocr.enabled=true`; non-OCR runs (export-only dry-run, tag-adding, download-only) do not need them.

**Tree extraction**: Requires PageIndex provider (`ocr.provider: pageindex`), valid `PAGEINDEX_API_KEY`, and `tree_structure.enabled: true` (see [Configuration](#configuration)).

### No Items Found

Verify:
- Items are tagged with the configured include tag(s) (see `tagging.selection.include.values`, default: `millefeuille`)
- Items are not already tagged with the configured exclude tag(s) (see `tagging.selection.exclude.values`, default: `millefeuille-processed`)
- `ZOTERO_LIBRARY_ID` env var is set to your correct numeric library ID
- `ZOTERO_READ_KEY` has read access to the specified library

### Note Size Exceeded

If notes exceed Zotero's size limits:
- Adjust `processing.note_size_threshold` to reduce note size
- Enable `processing.auto_split_oversized_notes` to automatically split large notes
- Consider using `extraction_mode: page_by_page` for very large documents (see [Extraction Modes](#extraction-modes))

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request or open an Issue on GitHub.

For major changes, please open an issue first to discuss what you would like to change.
