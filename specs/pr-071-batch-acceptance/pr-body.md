## Summary

Implements Speculoos task `pr-071-batch-acceptance`: add deterministic offline batch acceptance on top of the existing verified single-run acceptance path.

- adds a versioned batch manifest accepted by `millefeuille acceptance --batch-manifest`
- preflights every paper/run package and shared evidence before any acceptance output is written
- rejects unsafe ids, unknown fields, invalid locators, and duplicate resolved run identities
- writes sorted per-run acceptance results plus aggregate JSON/Markdown reports with portable refs
- preserves valid run results when another run needs review and makes aggregate exit/status semantics explicit
- fixes single-run acceptance so an existing duplicate match contributes a review reason
- documents and schemas the offline manifest, output, and storage contract

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-071-batch-acceptance.yaml`
- `README.md`
- `millefeuille/cli/stages.py`
- `millefeuille/domain/acceptance.py`
- `millefeuille/domain/millefeuille.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/acceptance-batch-manifest.schema.json`
- `specs/millefeuille-pipeline/acceptance-batch-summary.schema.json`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/pr-071-batch-acceptance/commit-message.txt`
- `specs/pr-071-batch-acceptance/pr-body.md`
- `tests/test_millefeuille_acceptance_batch.py`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_contract_models.py`

## Validation

- focused acceptance/CLI/contract suites — 37 passed
- full unittest discovery — 237 passed
- Ruff — passed
- hard-rename identity guard — passed
- YAML/JSON metadata parse — passed
- `git diff --check` — passed
- Speculoos validation, private-data scan, documentation sync, and publication checks — passed

## Documentation Impact

User-facing documentation updated: `README.md`, `specs/millefeuille-pipeline/README.md`, `specs/millefeuille-pipeline/cli-contract.md`, `specs/millefeuille-pipeline/artifact-storage.md`, and `specs/millefeuille-pipeline/remaining-work.md` now explain deterministic offline batch acceptance, its schemas, output paths, status semantics, and manual boundaries.

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main` and remain separately gated.

## Publication Boundary

Offline fixture-only processing under temporary source-pack roots. No live Zotero read or write, PDF recovery, source-pack mutation against real documents, OCR or model/provider call, worker-agent execution, OpenKB or index write, credential or permission change, release, package publication, production deployment, or approval bypass.
