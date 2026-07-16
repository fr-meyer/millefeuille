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
- `hierarchical-summary.schema.json` - page/section/paper summary shape.
- `paper-card.schema.json` - compact human/agent card shape.
- `retrieval-index-status.schema.json` - retrieval/index lane status shape.
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
manual gates, tag-state transitions, acceptance summaries, classification
records, writeback plans, and release-preflight records without requiring live
provider or Zotero mutation.

Preview/read-only stage commands now live under `millefeuille/cli/stages.py`:
`extract-native`, `extract-ocr`, `route`, `structure`, `summarize`, `card`,
`index`, `acceptance`, `classify`, `writeback`, `retrieve`, `models`, and
`run`. The fixture-only stages can run as one canonical, resumable chain;
resume skips only passed stages whose paper/run/source identity and expected
outputs revalidate.

Manual gates remain explicit: live Zotero reads/writes, PDF recovery/download,
OCR/Mistral/PageIndex calls, model calls, worker-agent execution, OpenKB
writes, index writes, source-pack writes, GitHub publication/merge, `dev` to
`main` promotion, release tags, and package publication all require separate
approval.
