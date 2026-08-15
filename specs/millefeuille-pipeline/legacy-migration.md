# Legacy-to-Lifecycle Migration And Deprecation Contract

This document is the operator contract for moving from the original
Hydra-configured Zotero-to-OCR workflow to the source-pack lifecycle. It does
not remove or disable the legacy path. It records which surface owns each
operation, which outputs can be carried forward, and how to stop or roll back
without treating unverified legacy output as lifecycle evidence.

All live Zotero reads or writes, PDF recovery, OCR/model/provider calls,
source-pack writes, OpenKB or index writes, releases, and package publication
remain approval gates. Migration does not turn a preview into approval.

## Command Surface Detection

The installed `millefeuille` entry point currently selects a surface from the
first argument:

| First argument | Surface | Current behavior |
| --- | --- | --- |
| `artifacts`, `status` | Artifact helper | Read-only inspection of artifact indexes. |
| `source-pack` | Source-pack helper | Verified, local fixture intake. |
| A current stage command | Lifecycle stage surface | Explicit `argparse` command operating on source-pack evidence. |
| Anything else, including Hydra overrides | Legacy Hydra surface | Monolithic discovery, optional download/OCR, note creation, tagging, and exports. |

The current stage command set is `extract-native`, `extract-ocr`, `route`,
`structure`, `summarize`, `card`, `index`, `acceptance`, `classify`,
`writeback`, `retrieve`, `models`, and `run`. Planned commands such as
`discover`, `handoff`, `recover`, and `openkb-add` are not available until
their implementation cards land.

The lightweight flags `--artifact-root`, `--source-pack-root`,
`--source-pack-intake-evidence`, the stage evidence flags, and `--run-id` are a
compatibility bridge when no named subcommand is present. The entry point
translates them to `export.artifacts.*` Hydra overrides. They do not change a
root invocation into lifecycle orchestration.

Operators must therefore identify a run by both its command surface and its
mode. A bare `millefeuille ocr.enabled=true` is a legacy live run; a named
stage command defaults to preview and fails closed if `--mode approved-live`
is requested for a stage that has no approved-live implementation.

## Ownership And Compatibility Matrix

| Capability | Legacy Hydra surface | Lifecycle stage surface | Migration rule |
| --- | --- | --- | --- |
| Zotero discovery | Implemented through configured include/exclude tags. | Planned `discover`; not yet implemented. | Keep discovery on a bounded legacy dry-run until staged discovery emits equivalent skip evidence. |
| Credential-free handoff | Implemented through `export.openkb_handoff.*`. | Planned `handoff`; not yet implemented. | Existing validated `openkb-millefeuille-handoff/v0.1` rows may feed verified recovery. |
| PDF download/recovery | `download.enabled=true` writes to `download.upload_folder`. | Planned `recover`; not yet implemented. | A downloaded file is not a source pack. Verify identity, size, and SHA-256 before intake. |
| Source-pack intake | Compatibility artifact writer plus `source-pack intake` for one-PDF evidence. | Source packs are the authority for every current stage command. | Prefer the explicit helper today; use v0.1 or v0.2 manifests as described below. |
| OCR and tree creation | Live direct Mistral or PageIndex API/SDK path; may create Zotero notes. | `extract-ocr` is fixture/evidence-only today. | Do not present legacy provider output as a passed lifecycle stage without a validated adapter evidence record. |
| Native extraction | Not an independently evidenced stage. | `extract-native` is implemented for local evidence. | Start new lifecycle runs from a verified source pack. |
| Route through card | Coupled to legacy processing/note output. | Explicit fixture-first stages are implemented. | Use a new run ID and preserve legacy outputs as comparison material only. |
| OpenKB/index writes | No complete lifecycle reconciliation contract. | `index` records fixture status only; live writes are planned. | No live index write until the approved connector and reconciliation cards land. |
| Acceptance/classification | No complete evidence join. | Preview and batch flows are implemented. | Classification begins only after lifecycle acceptance passes. |
| Zotero mutation | Notes plus configured success/error tags can be written during processing. | `writeback` is preview-only today. | Never run both write lanes for the same item in one migration attempt. |

There is no automatic equivalence between a legacy success and a lifecycle
stage status. Lifecycle status comes from the source-pack manifest, stage
manifest, artifact index, and acceptance evidence, not from a Zotero tag or the
presence of a legacy Markdown/note output.

## Operator Migration Procedure

### 1. Freeze The Legacy Run Envelope

Before processing a migration cohort, record the installed version, complete
Hydra overrides, include/exclude tags, selected item count, OCR provider and
mode, output paths, and retention settings. Use a dedicated 2-5 item staging
tag and a read-only dry-run first:

```bash
millefeuille processing.dry_run=true ocr.enabled=true \
  "tagging.selection.include.values=[millefeuille-test]"
```

Do not broaden the cohort while any item identity, attachment count, filename,
or output root is ambiguous.

### 2. Export Durable Handoff Evidence

Use the existing credential-free handoff export until the lifecycle `handoff`
command exists. The authoritative row must identify the Zotero item and
attachment, canonical filename, Zotero version, size, and SHA-256 where the
configured export can compute it. It must not persist credentials,
authenticated attachment URLs, or PDF bytes.

Preview and authoritative export paths are separate. Review preview rows
before approving the authoritative export. A handoff row authorizes neither
recovery nor source-pack writing.

### 3. Verify Bytes And Create A Source Pack

Recovered local bytes must match the handoff identity and expected SHA-256.
For one attachment, create a pack from explicit recovered-PDF evidence:

```bash
millefeuille source-pack intake \
  --evidence recovered-pdf-evidence.json \
  --source-pack-root /path/to/source-packs
```

The supported source-pack formats are:

- `millefeuille-source-pack-manifest/v0.1`: one Zotero attachment, stored as
  `source.pdf`, with a `sha256:<hex>` source hash;
- `millefeuille-source-pack-manifest/v0.2`: multiple PDFs belonging to the
  same Zotero item, stored below `sources/`, with per-attachment hashes and a
  deterministic `sha256-aggregate:<hex>` source hash.

Both formats remain readable. Do not combine attachments from different
Zotero items in one v0.2 pack, rewrite a v0.1 pack in place merely to upgrade
its schema, or overwrite a pack whose existing identity or source bytes drift.

### 4. Start A Separate Lifecycle Run

Choose a new immutable run ID and use the source-pack root explicitly:

```bash
millefeuille run \
  --source-pack-root /path/to/source-packs \
  --paper-id zotero-ITEM1 \
  --run-id migration-001 \
  --stages extract-native,extract-ocr,route,structure,summarize,card,index \
  --native-extraction-evidence native.json \
  --ocr-extraction-evidence ocr.json \
  --route-selection-evidence route.json \
  --structure-evidence structure.json \
  --summary-evidence summary.json \
  --card-evidence card.json \
  --index-evidence index.json
```

Only include stages for which reviewed local evidence exists. Current stage
commands do not make the corresponding provider calls. `--resume` may skip a
passed stage only after its identity and expected outputs revalidate.

### 5. Accept Before Classification Or Writeback

Run acceptance with the authoritative handoff and any duplicate-scan evidence.
Continue only when the result passes. Classification and writeback previews
must preserve the accepted source hash, taxonomy version, decision lineage,
and exact proposed mutations.

Lifecycle writeback remains preview-only until the approved-live executor is
implemented and approved. Do not use the legacy monolithic run as a shortcut
to apply a lifecycle preview.

## PageIndex Boundary

The current legacy OCR implementation contains a direct PageIndex HTTP API
mode and a PageIndex SDK mode selected by `ocr=pageindex` and
`ocr.use_sdk=false|true`. This compatibility path can upload, poll, retrieve,
and clean up provider documents as part of the legacy workflow. It remains in
place in this documentation slice and must retain its existing tests while the
migration window is open.

All newly implemented lifecycle PageIndex ingestion and indexing must follow
the later **PageIndex MCP-only** policy. Lifecycle code must not reuse the
legacy direct HTTP client, SDK client, or an ad-hoc multipart upload as its
production connector. The planned MCP bridge must:

1. resolve verified Zotero bytes only at runtime;
2. expose them briefly over approved HTTPS with the exact canonical filename
   in the response metadata;
3. require fail-closed access control through either an authenticated bridge
   request or a narrowly scoped, unguessable, short-lived, single-use
   capability URL with minimal expiry and use limits;
4. bind authorization to the intended document and request where feasible, and
   reject expired, replayed, or mismatched access attempts;
5. ingest only through the PageIndex MCP capability;
6. target the approved folder and reject exact duplicates globally and in that
   folder rather than creating suffixed names such as `_1.pdf`;
7. verify the final document ID, name, status, and folder;
8. commit the Zotero-to-PageIndex ledger record transactionally; and
9. revoke the serving capability, shut down temporary serving, and dispose of
   bytes under the approved policy immediately after ingestion or failure.

Capability URLs, authorization material, provider credentials, and private
payloads must not enter handoff rows, source-pack manifests, committed
evidence, logs, or the bridge ledger. Access failures must be logged only with
redacted request/document identifiers and without the capability URL or private
payload. Existing direct PageIndex results may be retained as legacy evidence,
but they cannot satisfy the MCP bridge or live index stage by themselves.

## Output And Artifact Root Mapping

| Purpose | Legacy/configured root | Lifecycle root | Carry-forward policy |
| --- | --- | --- | --- |
| Temporary downloaded PDFs | `download.upload_folder` | Runtime recovery staging, then verified source pack | Never adopt by path alone; verify bytes and identity. |
| Legacy OCR/processing files | `storage.base_dir/<item-key>/` | Not authoritative | Keep read-only for comparison or dispose under the approved retention policy. |
| Handoff export | `export.openkb_handoff.jsonl_path` or preview path | Future handoff stage evidence | Reuse only validated credential-free v0.1 rows. |
| Compatibility artifacts at an external root | `export.artifacts.artifact_root=<path>` | Explicit project/batch artifact root | Preserve refs and source hash; do not mix runs. |
| Source-pack artifacts | `export.artifacts.artifact_root=source-pack` plus `source_pack_root` | `<root>/zotero/<paper-id>/analyses/millefeuille/<run-id>/` | Preferred durable lifecycle layout. |
| Stage CLI artifacts | `--source-pack-root` plus paper/run locators | Same source-pack analysis directory | Treat manifest/index identity as authoritative. |

An output directory is not evidence of success. Never point a lifecycle run at
the legacy `storage.base_dir` as though it were a source-pack root. Relative
paths are resolved from the operator's working directory because Hydra is
configured with `hydra.job.chdir=false`; production automation should use
stable explicit paths.

## Tag Compatibility And Migration

The legacy defaults use `millefeuille` as the selection tag,
`millefeuille-processed` as both the success and future-exclusion tag, and
`millefeuille-error` on failures. `millefeuille-processed` means only that the
configured legacy processing path reported success; it is not proof that the
source was packed, indexed, accepted, or classified.

The lifecycle vocabulary is stage-specific, from
`millefeuille-previewed`, `millefeuille-handoff-exported`, and
`millefeuille-source-packed` through `millefeuille-indexed`,
`millefeuille-acceptance-passed`, and `millefeuille-classified`. The exact
transition rules live in `tag-state-machine.md`.

Historical selection, PageIndex-success, and error tags from the external
predecessor workflow are not configured Millefeuille lifecycle states. Their
former spellings are intentionally not repeated in tracked repository content;
operator-owned inventory snapshots remain the evidence source. A later
writeback-policy card must decide their evidence-backed mapping and removal
rules. Until then:

- preserve all historical and legacy tags during intake;
- never infer a lifecycle pass solely from one of those tags;
- preview every add and removal separately;
- re-read the Zotero item version immediately before any approved write; and
- remove a selection/staging tag only after the configured terminal success is
  verified.

## Compatibility Timetable

The timetable is release-based rather than date-based:

| Release | Contract |
| --- | --- |
| `0.4.x` (current) | Both surfaces remain available. The legacy Hydra path is supported; lifecycle live parity is incomplete. |
| `0.5.0` | Ship the stable offline lifecycle and this migration contract. Legacy behavior remains intact; documentation labels it compatibility-only for new automation. Runtime warning work may land separately. |
| `0.6.0` target | Make the approved-live lifecycle the recommended production path after bounded dogfood and acceptance. Freeze the legacy path to critical fixes; direct PageIndex API/SDK remains legacy-only. |
| `0.7.x` target | Operate and harden lifecycle reconciliation, backfill, retry, and recovery. Keep the legacy path available for rollback during at least one complete supported release cycle. |
| No earlier than `1.0.0` | Removal or default-disablement may be proposed only as a separately approved major change after the exit criteria below pass. |

Legacy removal is not automatic at `1.0.0`. It requires all of the following:

- staged discovery through writeback has bounded live evidence;
- v0.1 and v0.2 source packs remain readable or have a tested migration;
- PageIndex MCP bridge and ledger reconciliation are operational;
- legacy tag and output migrations have audit reports;
- a clean-install and upgrade test passes on supported platforms;
- release notes provide a rollback path; and
- the incompatible CLI change receives explicit release approval.

## Safe Stop And Rollback

Before each migration cohort, keep the legacy configuration snapshot, input
handoff evidence, source hashes, and last accepted lifecycle run ID. Do not
reuse a run ID for a changed source or configuration.

If migration fails before any approved external write:

1. stop the lifecycle run and retain its failed stage manifest;
2. leave verified source evidence and earlier run directories unchanged;
3. point readers to the last accepted run ID;
4. quarantine incomplete temporary outputs instead of merging them into an
   accepted package; and
5. resume only after the failed stage's inputs and outputs revalidate.

If an external write has occurred, rollback is system-specific and is not
implied by deleting local artifacts. Use the writeback preview and ledger to
identify exact Zotero or PageIndex state, re-read current versions, and apply a
separately approved compensating action. Never delete a PageIndex document or
remove Zotero tags/notes merely because a later local stage failed.

The legacy surface may be used as an operational fallback only with its saved
configuration, a fresh bounded dry-run, and its normal approval gates. It must
not write into a lifecycle run directory or claim the failed lifecycle run's
stage status.

## Follow-On Implementation Decisions

This contract deliberately leaves these code changes to dependency-tracked
cards:

- add a machine-readable command-surface/version marker and legacy warning;
- implement `discover`, `handoff`, `recover`, and `openkb-add` stage commands;
- define and implement provider-to-OCR evidence adapters;
- build the PageIndex MCP bridge, exact-duplicate checks, and durable ledger;
- define canonical mappings for `millefeuille-processed` and historical
  predecessor-workflow tags before approved-live writeback;
- resolve external artifact-root support for all stage commands;
- implement approved-live writeback and compensating-action records; and
- collect the live parity and rollback evidence required before any removal
  proposal.
