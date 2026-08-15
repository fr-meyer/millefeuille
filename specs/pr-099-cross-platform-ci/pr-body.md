## Summary

- add a read-only GitHub Actions matrix for Ubuntu and Windows across CPython 3.11, 3.12, and 3.13
- pin the official checkout and Python-setup actions to reviewed immutable commits
- install declared test tooling, run repository-wide Ruff, and run full unittest discovery in every matrix job
- centralize filesystem capability markers so supported behavior runs everywhere and unsupported Windows write paths prove their fail-closed contracts
- validate retrieval filters before platform capability failure and document the exact support boundary
- add canonical Speculoos delivery gates for roadmap card MF-004

## Changed Files

- `.github/workflows/ci.yml`
- `.speculoos/tasks/pr-099-cross-platform-ci.yaml`
- `README.md`
- `millefeuille/domain/retrieve.py`
- `pyproject.toml`
- `specs/pr-099-cross-platform-ci/commit-message.txt`
- `specs/pr-099-cross-platform-ci/pr-body.md`
- `tests/platform_capabilities.py`
- `tests/test_cross_platform_ci.py`
- `tests/test_millefeuille_artifact_writer.py`
- `tests/test_millefeuille_model_execution.py`
- `tests/test_millefeuille_retrieve.py`
- `tests/test_millefeuille_retrieval_batch.py`
- `tests/test_millefeuille_secure_io.py`

## Validation

- 119 focused CI, writer, model-execution, retrieval, retrieval-batch, and secure-I/O tests passed with 3 capability skips
- all 365 repository tests passed with 3 capability skips
- repository-wide Ruff passed
- `git diff --check`, privacy/manual-gate contract checks, Speculoos validation, and publication checks passed

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main`.

This routine CI and portability PR requires exact-head Mergeguez approval and
successful GitHub checks before merge. It does not authorize promotion to
`main`, release creation, tag creation, package publication, or deployment.

## Documentation Impact

`README.md` adds the supported Ubuntu/Windows CPython matrix and an explicit
table distinguishing portable behavior from fail-closed write and aggregate
publication paths.

## Publication Boundary

Repository CI configuration, offline runtime validation ordering, README support
guidance, synthetic tests, and repository-local Speculoos metadata only. No
credential lookup, provider/model/OCR call, private paper read, live
Zotero/OpenKB/PageIndex/source-pack mutation, stable-branch promotion, release,
tag, package publication, deployment, or approval bypass.

## Task

Speculoos task: `pr-099-cross-platform-ci`

Roadmap card: `MF-004`

The active task head field remains null by design: a commit cannot embed its own
SHA without changing it. The immutable Mergeguez review and check bind
validation to GitHub's live exact head; closeout records the final head SHA and
merge commit after merge.

PR #100, PR #102, and PR #103 currently depend on this branch. Retain it after
PR #99 merges until all three are safely retargeted.
