## Summary

Implements Speculoos task `pr-075-classification-review-adjudication`: add deterministic offline classification review and adjudication actions over accepted runs and prior decisions.

- adds versioned action evidence accepted by `millefeuille classify --action-evidence`
- verifies prior-decision identity, source hash, locked taxonomy, local evidence refs, action ids, and mode/outcome combinations before writing
- materializes review no-change, correction, and escalation records plus adjudication confirmation, correction, and taxonomy-change-request records
- keeps prior and final decisions linked through portable immutable action artifacts
- updates the current classification plan, preview, stage manifest, and artifact index without mutating the taxonomy or any live system
- returns success for resolved actions and review status for unresolved escalation/taxonomy-gap actions
- keeps exact-input reruns deterministic and byte-stable
- documents and schemas the offline action input, output, storage, exit, and manual-gate contracts

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-075-classification-review-adjudication.yaml`
- `README.md`
- `millefeuille/cli/stages.py`
- `millefeuille/domain/classification.py`
- `millefeuille/domain/millefeuille.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/classification-action-evidence.schema.json`
- `specs/millefeuille-pipeline/classification-action-record.schema.json`
- `specs/millefeuille-pipeline/classification-orchestration.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/pr-075-classification-review-adjudication/commit-message.txt`
- `specs/pr-075-classification-review-adjudication/pr-body.md`
- `tests/test_millefeuille_classification_actions.py`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_contract_models.py`

## Validation

- focused classification-action, stage-CLI, and contract suites — 43 passed
- full unittest discovery — 254 passed
- Ruff — passed
- hard-rename identity guard — passed
- YAML/JSON metadata parse — passed
- `git diff --check` — passed
- Speculoos validation, private-data scan, documentation sync, and publication checks — passed

## Documentation Impact

User-facing documentation updated: `README.md`, `specs/millefeuille-pipeline/README.md`, `specs/millefeuille-pipeline/cli-contract.md`, `specs/millefeuille-pipeline/artifact-storage.md`, `specs/millefeuille-pipeline/classification-orchestration.md`, and `specs/millefeuille-pipeline/remaining-work.md` explain deterministic offline review/adjudication actions, their schemas, output paths, lineage/status semantics, and manual boundaries.

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main` and remain separately gated.

## Publication Boundary

Offline fixture-only classification review and adjudication action records over existing temporary accepted runs and explicit local evidence. No live Zotero read or write, PDF recovery, source-pack mutation against real documents, OCR or model/provider call, worker-agent execution, automatic taxonomy mutation, OpenKB or index write, credential or permission change, release, package publication, production deployment, or approval bypass.
