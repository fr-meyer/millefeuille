## Summary

Implements Speculoos task `pr-073-batch-classification`: add deterministic offline batch classification routing on top of the accepted single-run preview path.

- adds a versioned batch manifest accepted by `millefeuille classify --batch-manifest`
- locks one taxonomy version and requires explicit batch-mode evidence for every run
- preflights every paper/run package, passing acceptance summary, evidence ref, taxonomy, and resolved identity before any output is written
- rejects unsafe ids or evidence refs, unknown fields, invalid types or locators, taxonomy drift, and duplicate resolved run identities
- preserves sorted per-run classification artifacts and emits aggregate JSON/Markdown routing reports with portable refs
- aggregates classified, needs-review, and adjudication-required outcomes without suppressing valid runs
- documents and schemas the offline manifest, output, storage, exit, and manual-gate contract

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-073-batch-classification.yaml`
- `README.md`
- `millefeuille/cli/stages.py`
- `millefeuille/domain/classification.py`
- `millefeuille/domain/millefeuille.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/classification-batch-manifest.schema.json`
- `specs/millefeuille-pipeline/classification-batch-summary.schema.json`
- `specs/millefeuille-pipeline/classification-orchestration.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/pr-073-batch-classification/commit-message.txt`
- `specs/pr-073-batch-classification/pr-body.md`
- `tests/test_millefeuille_classification_batch.py`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_contract_models.py`

## Validation

- focused classification/CLI/contract suites — 37 passed
- full unittest discovery — 243 passed
- Ruff — passed
- hard-rename identity guard — passed
- YAML/JSON metadata parse — passed
- `git diff --check` — passed
- Speculoos validation, private-data scan, documentation sync, and publication checks — passed

## Documentation Impact

User-facing documentation updated: `README.md`, `specs/millefeuille-pipeline/README.md`, `specs/millefeuille-pipeline/cli-contract.md`, `specs/millefeuille-pipeline/artifact-storage.md`, `specs/millefeuille-pipeline/classification-orchestration.md`, and `specs/millefeuille-pipeline/remaining-work.md` now explain deterministic offline batch classification, its schemas, output paths, routing/status semantics, and manual boundaries.

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main` and remain separately gated.

## Publication Boundary

Offline fixture-only batch classification over existing temporary source-pack runs and explicit local evidence. No live Zotero read or write, PDF recovery, source-pack mutation against real documents, OCR or model/provider call, worker-agent execution, OpenKB or index write, credential or permission change, release, package publication, production deployment, or approval bypass.
