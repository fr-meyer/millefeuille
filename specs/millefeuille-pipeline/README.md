# Millefeuille Pipeline Contract Packet

This packet records the remaining `millefeuille` work as an
offline-first contract. It is safe to review and test without Zotero
credentials, PDF bytes, OCR providers, OpenKB writes, source-pack writes,
GitHub publication, releases, or package-index changes.

Files:

- `vision.md` - complete paper-processing product vision.
- `remaining-work.md` - end-to-end work pipeline and manual gates.
- `cli-contract.md` - intended command groups and run modes.
- `stage-manifest.schema.json` - machine-readable stage manifest shape.
- `artifact-storage.md` - artifact-root and source-pack storage contract.
- `source-pack-manifest.schema.json` - source-pack source identity and hash
  contract.
- `artifact-index.schema.json` - machine-readable artifact index shape.
- `model-profile.schema.yaml` - per-stage model/profile selection shape.
- `model-execution-evidence.schema.json` - approved execution-evidence shape
  accepted by provenance materialization.
- `model-execution-plan.schema.json` - no-call model execution plan shape.
- `model-provenance-record.schema.json` - provider-payload-free model
  provenance record shape.
- `hierarchical-summary.schema.json` - page/section/paper summary shape.
- `paper-card.schema.json` - compact human/agent card shape.
- `retrieval-index-status.schema.json` - retrieval/index lane status shape.
- `retrieval-batch-manifest.schema.json` - strict multi-run retrieval locators.
- `retrieval-batch-result.schema.json` - deterministic aggregate ref/status
  results.
- `acceptance-batch-manifest.schema.json` - deterministic offline batch input
  locators.
- `acceptance-batch-summary.schema.json` - aggregate pass/review result shape.
- `classification-batch-manifest.schema.json` - locked-taxonomy offline batch
  routing inputs and evidence refs.
- `classification-batch-summary.schema.json` - aggregate classification routes,
  counts, and review/adjudication status.
- `classification-action-evidence.schema.json` - strict offline review and
  adjudication action inputs.
- `classification-action-record.schema.json` - immutable action lineage and
  final-decision refs.
- `retrieval-index-contract.md` - OpenKB/PageIndex and optional index lanes.
- `classification-orchestration.md` - CLI-owned classification modes and
  multi-agent governance.
- `tag-state-machine.md` - Zotero tag lifecycle design.
- `ocr-backend-contract.md` - native/OCR evidence adapter contract.
- `release-version-policy.md` - Speculoos-governed release and version path.
- `release-candidate-preflight.md` - local RC preflight artifacts and stop
  points before promotion/tagging.
- `live-run-plan.md` - future live dogfood plan, still requiring approval.

Executable offline contract models live in
`millefeuille/domain/millefeuille.py`. They cover stage manifests,
manual gates, tag-state transitions, single-run and batch acceptance summaries,
single-run and batch classification records, offline classification review and
adjudication actions, writeback plans, and release-preflight records without
requiring live provider or Zotero mutation. Batch retrieval orchestration lives
in `millefeuille/domain/retrieve.py`; it validates strict versioned locators,
preflights every package through one pinned source-root descriptor, and emits
only portable allowlisted refs/status metadata without artifact-controlled
summary IDs. Descendant reads and corpus enumeration use descriptor-relative
no-follow operations. Safe aggregate publication uses pinned descriptors, a
batch-directory inode lock, and an atomic no-replace generation rename on
supported POSIX local filesystems. While staging remains unpublished, validation
rereads expected bytes, rechecks held descriptors, metadata, entry names, and
single-link counts, then makes files and the generation directory read-only; the
atomic rename is the commit point. Immediately before that point, the cooperative
batch lock remains held while all snapshotted inputs and corpus entries are
reopened and revalidated and the external manifest is reread byte-for-byte. On
pre-commit failure, descriptor-only cleanup scrubs owned output bytes but
intentionally leaves the temporary generation in its reached mode and any
unverified namespace entries in place for explicit operator cleanup; unavailable
primitives fail closed before publication. This boundary assumes trusted
ownership or cooperative same-UID writers: POSIX locks and mode bits do not
prevent an uncooperative owner from mutating staging after the last check.

Preview/read-only stage commands now live under `millefeuille/cli/stages.py`:
`extract-native`, `extract-ocr`, `route`, `structure`, `summarize`, `card`,
`index`, `acceptance`, `classify`, `writeback`, `retrieve`, `models`, and
`run`. The fixture-only stages can run as one canonical, resumable chain;
resume skips only passed stages whose paper/run/source identity and expected
outputs revalidate.

`models --plan` resolves one page, section, or full-paper summary profile into
a strict `millefeuille-model-execution-plan/v0.1` object. It is deliberately
no-call: credential lookup, provider execution, resolved-model claims, and
actual usage values remain false/unset until a separately approved live lane
exists.

`models --provenance --plan-file <json> --execution-evidence <json>` validates
strict `millefeuille-model-execution-evidence/v0.1` against the no-call plan and
materializes a `millefeuille-model-provenance/v0.1` record to stdout or an
explicit `--output` JSON path. The materializer rejects unknown fields,
identity/control drift, invalid fallback resolution, invalid token totals,
unsafe or duplicate refs, malformed warnings, prompts, provider payloads,
private paper text, PDF payload markers, and credential/secret markers. It does
not append to a run package or perform credential lookup/provider execution.
The complete plan shape, canonical bundled-profile values, and no-call controls
are revalidated before evidence is trusted. An explicit output uses exclusive
descriptor-relative no-follow creation, rejects existing files, symlinked
parents, and run-package marker ancestors, and detects name or parent
replacement races.

Manual gates remain explicit: live Zotero reads/writes, PDF recovery/download,
OCR/Mistral/PageIndex calls, model calls, worker-agent execution, OpenKB
writes, index writes, source-pack writes, GitHub publication/merge, `dev` to
`main` promotion, release tags, and package publication all require separate
approval.
