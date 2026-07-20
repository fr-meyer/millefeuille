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
- `retrieve --batch-manifest <path>` as the locator source for deterministic
  multi-run preview output
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
  - `--batch-manifest` preflights a non-empty set of paper/run locators before
    any acceptance write, emits each run summary, and writes a deterministic
    aggregate report under `batches/millefeuille/<batch-id>/reports/`.

- `classify`
  - Implemented for accepted runs from explicit local evidence.
  - Writes classification plan/decision artifacts plus a Zotero writeback
    preview without model/provider calls.

- `writeback`
  - Implemented in preview mode only.
  - Materializes a governed Zotero writeback plan and optional preview note.

- `retrieve`
  - Implemented as a read-only ref resolver over verified artifact packages.
  - Resolves a known package by paper id, Zotero item key, or source-pack slug,
    and scans the local source-pack corpus for one normalized exact DOI or title
    match at the requested run id.
  - Filters summary refs by scope, grain, exact section locator, page locator,
    or the classification-evidence shortcut and filters index refs by lane.
  - Returns summary, card, index, acceptance, classification, and writeback
    refs instead of private paper content; missing, ambiguous, cross-wired,
    symlinked, or traversal-unsafe corpus matches fail closed.
  - `--batch-manifest` accepts strict v0.1 paper-id, Zotero-key, slug, DOI, or
    exact-title locators, preflights every run before any aggregate write, and
    emits sorted JSON/Markdown under
    `batches/millefeuille/<batch-id>/retrieval/`.

- `models`
  - Lists the bundled profile catalog for preview and planning use.
  - `--plan --profile <id> --stage summarize_page|summarize_section|summarize_full_paper`
    resolves a deterministic `millefeuille-model-execution-plan/v0.1` object.
  - The no-call plan records requested model and parameters, authentication
    lane, fallback policy, live blockers, and the required provenance shape.
    It never reads credentials, calls a provider, or claims actual resolved
    model, usage, input, or output evidence.
  - `--provenance --plan-file <json> --execution-evidence <json>` validates
    one strict `millefeuille-model-execution-evidence/v0.1` object against the
    selected plan and materializes a `millefeuille-model-provenance/v0.1`
    record to stdout or an explicit `--output` JSON path. It rejects unknown
    fields, identity/control drift, leading or trailing string whitespace,
    invalid fallback resolution, invalid token totals, unsafe or duplicate refs,
    malformed warning records, prompts, provider payloads, private paper text,
    PDF payload markers, and credential or secret markers. This is JSON
    materialization only; appending provenance to a verified run package remains
    later work. The full plan/no-call
    contract and canonical bundled-profile values are revalidated before
    evidence is trusted. `--output` exclusively creates a new file through
    descriptor-relative no-follow traversal and fails closed on existing paths,
    symlinked parents, run-package marker ancestors, or replacement races.

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
    id, Zotero item key, source-pack slug, normalized DOI, or normalized exact
    title and returns artifact refs.
  - Section and page filters match structured summary source locators;
    `--evidence-need classification` selects classification-scoped summary refs
    while retaining the card, index, acceptance, decision, and writeback refs.
  - Corpus lookup rejects missing or ambiguous identity matches and validates
    every candidate package before returning refs.
  - Offline batch mode consumes
    `millefeuille-retrieval-batch-manifest/v0.1`, applies the same optional
    filters to every run, rejects malformed entries and duplicate resolved
    paper/run identities, and writes
    `millefeuille-retrieval-batch-result/v0.1` plus a Markdown view only after
    full preflight.
  - Aggregate refs are source-pack-root-relative, slash-normalized, sorted,
    content-free, and byte-stable on exact reruns. Future expansion is limited
    to separately approved live index reconciliation.

- `acceptance`
  - Current preview implementation joins handoff, source-pack, extraction,
    route, structure, summary, card, index, and duplicate-scan evidence.
  - Offline batch mode sorts unique paper/run identities, aggregates pass and
    needs-review results without skipping valid runs, and rejects invalid or
    duplicate manifests before writes.
  - Future expansion should add broader skip/OpenKB/live-state joins and
    approval-aware waivers.

- `classify`
  - Current preview implementation starts only after acceptance evidence is
    complete and emits evidence-backed decision records before any Zotero
    mutation.
  - Offline batch mode locks one taxonomy version, preflights all run/evidence
    pairs, preserves per-run decisions, and emits deterministic aggregate
    routes and review/adjudication counts.
  - Offline `review` and `adjudicate` actions consume strict local action
    evidence, validate prior-decision lineage and locked-taxonomy identity, and
    preserve immutable prior/final records under one traversal-safe action ID.
  - Review outcomes are `no-change`, `corrected`, and `escalated`;
    adjudication outcomes are `confirmed`, `corrected`, and
    `taxonomy-change-requested`.
  - Future expansion should cover model-backed classification, optional
    `multi-agent` execution, and automated taxonomy governance beyond emitting
    a reviewable change-request record.

- `writeback`
  - Current implementation is preview-only and writes governed plans rather
    than mutating Zotero.
  - Future approved-live mutation still requires explicit approval and
    `ZOTERO_WRITE_KEY`.

- `models`
  - List, validate, and explain available model profiles for each model-using
    stage.
  - Materialize provider-payload-free model provenance from exact execution
    evidence after a separately approved execution lane has produced that
    evidence.

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

Batch retrieval uses only already-created local artifact packages:

```bash
millefeuille retrieve \
  --source-pack-root /path/to/source-packs \
  --batch-manifest /path/to/retrieval-batch.json \
  --summary-scope classification \
  --index-lane pageindex \
  --json
```

The manifest has schema version
`millefeuille-retrieval-batch-manifest/v0.1`, a traversal-safe `batch_id`, and
a non-empty `runs` array. Each run has `run_id` and exactly one locator:
`paper_id`, `item_key`, `slug`, `doi`, or `title`. Unknown fields, non-string or
unsafe locators, missing/ambiguous corpus identities, drifted packages,
traversal-unsafe artifact refs, and duplicate resolved identities fail before
either aggregate file is written. `--batch-manifest` must not be empty and may
not be combined with direct locators or `--run-id`. The JSON result and
Markdown report are written beneath
`batches/millefeuille/<batch-id>/retrieval/`. On supported POSIX local
filesystems, batch preflight pins one source-root descriptor, traverses every
input component through descriptor-relative no-follow operations, parses JSON
from held regular-file descriptors, and snapshots stable input identities,
missing optional inputs, and enumerated corpus entries. Only an actually absent
optional path is recorded as missing; directories, FIFOs, and other non-regular
entries fail closed. The external batch manifest is retained byte-for-byte for a
no-follow precommit reread. Publication
pins the batch-directory inode, locks its descriptor, and stages
exclusively created files by descriptor while retaining independently opened
read-only verification and private read/write cleanup descriptors for each owned
inode. Before publication it validates the exact staging entry set,
rereads both expected byte streams, rechecks names, inode identities, metadata,
and single-link counts, changes files to mode `0444` and the generation
directory to mode `0555`, then repeats the held-descriptor verification. The
atomic no-replace rename of that complete read-only generation is the
publication commit point; parent-directory fsync follows for durability, not as
a post-publication validation gate. Pre-commit failure cleanup truncates and
fsyncs only the owned staged inodes through their retained cleanup descriptors,
even if a staged name was displaced or replaced, preventing a raced external
hard link from retaining aborted aggregate bytes. It deliberately performs no
namespace unlink, rename, or remove operation because an
uncooperative same-UID replacement cannot be conditionally mutated atomically;
unverified entries stay untouched. The failed temporary generation remains in
its reached mode (`0700` or `0555`) with owned output files at zero bytes for
explicit operator cleanup. Exact byte-identical reruns verify opened read-only
no-follow regular-file descriptors and no-op. Incomplete, writable, drifted,
symlinked, hard-linked, displaced, or substituted output fails closed.
Immediately before an exact-rerun no-op or atomic rename, while the cooperative
batch lock is held, Millefeuille reopens and revalidates every snapshotted input
and corpus entry and rereads the external batch manifest. Namespace, entry-set,
hard-link, or same-inode changes observed before that final check fail closed.

The lock serializes cooperating Millefeuille writers only. POSIX `flock`, mode
bits, and descriptor checks cannot stop an uncooperative same-UID owner from
mutating inputs or staging after the last validation and before rename, or from
mutating published files. The command therefore requires trusted ownership,
cooperative same-UID writers, or stronger immutable/content-addressed storage
and does not claim protection against that adversary. The atomic rename is the
completed-operation boundary inside this stated trust model. Platforms or
filesystems lacking the required POSIX no-follow, descriptor-relative,
directory-lock, or no-replace-rename primitives fail closed before aggregate
publication, while package import and legacy single-run retrieval use their
portable legacy read fallback when POSIX `O_NOFOLLOW` is unavailable. That
fallback rejects parent traversal, lstat-checks every parent and target, rejects
symbolic links and non-regular entries, and binds the opened descriptor to the
checked identity before reading. The result contains only deterministic refs, allowlisted
summary/index status metadata, and counts. Artifact-controlled
`summary_id` values are excluded from batch output. This is not approval for live Zotero,
PDF recovery, OCR/model/provider calls, OpenKB/index writes, source-pack intake,
or publication.

Batch acceptance uses the same read-only evidence inputs without live provider
or Zotero access:

```bash
millefeuille acceptance \
  --source-pack-root /path/to/source-packs \
  --batch-manifest /path/to/acceptance-batch.json \
  --handoff /path/to/handoff.jsonl \
  --json
```

The batch manifest uses
`millefeuille-acceptance-batch-manifest/v0.1`, supplies a traversal-safe
`batch_id`, and lists unique `paper_id` or `item_key` plus `run_id` locators.
Offline batch acceptance is not approval for live Zotero, OCR, OpenKB, index,
source-pack, model, or worker-agent operations.

Batch classification consumes already-accepted runs and one local evidence ref
per run:

```bash
millefeuille classify \
  --source-pack-root /path/to/source-packs \
  --batch-manifest /path/to/classification-batch.json \
  --json
```

The manifest uses
`millefeuille-classification-batch-manifest/v0.1`, locks one taxonomy version,
and resolves traversal-safe evidence refs relative to the manifest. All
evidence must use `mode: batch`. The command preflights every package and
evidence file before writing per-run decisions or aggregate routes. Its JSON
summary follows `millefeuille-classification-batch-summary/v0.1`; review or
adjudication outcomes materialize but return exit code `2`. This offline path
does not authorize model/provider calls, worker execution, taxonomy mutation,
or live writes.

Offline classification actions use one accepted run and explicit local
evidence:

```bash
millefeuille classify \
  --source-pack-root /path/to/source-packs \
  --paper-id zotero-ITEM1 \
  --run-id run-fixture \
  --action-evidence /path/to/classification-action.json \
  --json
```

The evidence follows
`millefeuille-classification-action-evidence/v0.1`. It supplies a safe
`action_id`, `mode`, allowed outcome, locked taxonomy version, run-relative
`prior_decision_ref`, final path/confidence/summary, and existing local evidence
refs. The command rejects unknown fields, invalid types, traversal, missing
refs, paper/run/source drift, taxonomy drift, invalid mode/outcome pairs, and
adjudication of an already-classified prior decision before writing anything.

Successful preflight creates `classification/actions/<action-id>/` with an
immutable `millefeuille-classification-action/v0.1` record, final decision,
Markdown views, action-scoped writeback preview, and deterministic queue or
taxonomy-change-request JSONL. It then advances the canonical classification
plan, stage manifest, artifact index, and writeback preview to the final action
state. `no-change`, `corrected`, and `confirmed` return `0`; `escalated` and
`taxonomy-change-requested` preserve their audit artifacts and return `2`.
This offline command does not call models, run workers, mutate the taxonomy, or
write live Zotero/OpenKB/index/source-pack state.

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
- `2`: validation failed, acceptance needs review, or classification requires
  review/adjudication.
- `3`: manual approval required for the requested stage.
