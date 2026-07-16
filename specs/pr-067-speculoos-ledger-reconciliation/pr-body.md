## Summary

- mark stale task records for already-merged work as merged and Done
- add the missing completed-ledger entries for PR #39 and PRs #56 through #61
- record exact reviewed heads, merge commits, review evidence, and check runs from live readback
- document the disposition of historical informational findings

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-039-offline-acceptance-summary.yaml`
- `.speculoos/tasks/pr-044-pr43-closeout-metadata.yaml`
- `.speculoos/tasks/pr-045-zotero-credential-guidance.yaml`
- `.speculoos/tasks/pr-049-pr48-closeout-metadata.yaml`
- `.speculoos/tasks/pr-056-source-pack-staged-handoff-intake.yaml`
- `.speculoos/tasks/pr-057-source-pack-extraction-fixtures.yaml`
- `.speculoos/tasks/pr-058-route-selection-fixtures.yaml`
- `.speculoos/tasks/pr-059-structure-fixtures.yaml`
- `.speculoos/tasks/pr-060-summary-fixtures.yaml`
- `.speculoos/tasks/pr-061-card-fixtures.yaml`
- `.speculoos/tasks/pr-067-speculoos-ledger-reconciliation.yaml`
- `specs/pr-067-speculoos-ledger-reconciliation/commit-message.txt`
- `specs/pr-067-speculoos-ledger-reconciliation/pr-body.md`

## Validation

- `.venv/bin/python -m unittest discover -v`
- `.venv/bin/ruff check .`
- metadata YAML and JSON parse check
- merged-task and completed-ledger consistency audit
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-067-speculoos-ledger-reconciliation --validation "git diff --check" --json`
- `speculoos publish-check --task pr-067-speculoos-ledger-reconciliation --pr-body specs/pr-067-speculoos-ledger-reconciliation/pr-body.md --commit-message specs/pr-067-speculoos-ledger-reconciliation/commit-message.txt --json`

## Release Flow

This is a metadata-only correction for the development lane: feature PRs target `dev`;
release/promotion PRs target `main`.

## Documentation Impact

No user-facing documentation changes required: the reconciliation changes only
repo-local workflow metadata and does not alter user-facing behavior.

## Publication Boundary

Metadata-only ledger reconciliation. No application behavior change, live
Zotero read or write, PDF recovery, OCR/Mistral/PageIndex provider call, model
call, worker-agent execution, OpenKB write, index write, source-pack write,
credential or permission change, main-branch merge, release tag, package
publication, or approval bypass.
