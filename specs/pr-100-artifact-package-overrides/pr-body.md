## Summary

- add explicit `--artifact-root` and exact `--stage-manifest` overrides across single-run stage, lifecycle, run, resume, preflight, and retrieve flows
- resolve declared external run packages deterministically while preserving selected manifest references and run-scoped output routing
- fail closed on traversal, ambiguity, symbolic-link or reparse-point indirection, and package/source/paper/run/stage identity drift
- reject single-run package overrides for batch manifests where mixed roots are intentionally unsupported
- align README and the artifact storage and CLI contracts with the governed override boundary

## Changed Files

- `.speculoos/tasks/pr-100-artifact-package-overrides.yaml`
- `README.md`
- `millefeuille/cli/stages.py`
- `millefeuille/domain/acceptance.py`
- `millefeuille/domain/artifact_writer.py`
- `millefeuille/domain/card_fixtures.py`
- `millefeuille/domain/classification.py`
- `millefeuille/domain/index_fixtures.py`
- `millefeuille/domain/offline_stages.py`
- `millefeuille/domain/release_preflight.py`
- `millefeuille/domain/retrieve.py`
- `millefeuille/domain/stage_runtime.py`
- `millefeuille/domain/summary_fixtures.py`
- `millefeuille/domain/writeback.py`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/pr-100-artifact-package-overrides/commit-message.txt`
- `specs/pr-100-artifact-package-overrides/pr-body.md`
- `tests/test_millefeuille_acceptance_batch.py`
- `tests/test_millefeuille_artifact_overrides.py`
- `tests/test_millefeuille_classification_batch.py`
- `tests/test_millefeuille_retrieval_batch.py`
- `tests/test_millefeuille_retrieve.py`

## Validation

- 75 focused override and affected-flow tests passed with 1 capability skip
- all 370 repository tests passed with 3 capability skips
- repository-wide Ruff passed
- `git diff --check`, privacy/manual-gate contract checks, Speculoos validation, and publication checks passed

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main`.

This routine offline runtime and contract PR requires exact-head Mergeguez
approval and successful GitHub checks before merge. It does not authorize
promotion to `main`, release creation, tag creation, package publication, or
deployment.

## Documentation Impact

`README.md`, `artifact-storage.md`, and `cli-contract.md` document exact
artifact-root and stage-manifest selection, containment, identity checks, and
batch rejection behavior.

## Publication Boundary

Repository-local artifact package selection, offline runtime validation, CLI
and documentation contracts, synthetic tests, and Speculoos metadata only. No
credential lookup, provider/model/OCR call, private paper read, live
Zotero/OpenKB/PageIndex/source-pack mutation, stable-branch promotion, release,
tag, package publication, deployment, or approval bypass.

## Task

Speculoos task: `pr-100-artifact-package-overrides`

Roadmap card: `MF-103`

The active task head field remains null by design: a commit cannot embed its own
SHA without changing it. Immutable Mergeguez review and check evidence bind
validation to GitHub's live exact head; closeout records the final head and
merge commit after merge.

The original stacked dependency wording is obsolete. PR #100 now targets
`dev` directly after integrating the completed PR #99 work.
