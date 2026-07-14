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
the recovered bytes before writing
`<source-pack-root>/zotero/<paper-id>/source.pdf` and `manifest.json`; it
refuses mismatched hashes or existing pack drift. It does not read Zotero,
download PDFs, call OCR/model providers, write OpenKB/index data, or mutate
Zotero.

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

The current source-pack schema is still single-source: one `source.pdf` and one
`manifest.json` per Zotero item-derived source pack. If a discovered Zotero item
has multiple PDF handoff rows, this staged intake lane now aborts before any
write rather than partially creating a source pack and drifting on a later
attachment. Multi-PDF item support needs a dedicated follow-up slice.

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
