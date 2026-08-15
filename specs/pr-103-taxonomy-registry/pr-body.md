## Summary

- add deterministic content-addressed two-level taxonomy registries and
  immutable scope locks
- add offline proposal, independent three-role review, application, and
  forward-only rollback artifacts plus CLI commands
- preserve stable IDs during rollback, restore historical entries exactly, and
  deprecate later-added IDs without mutating their other values
- bind every non-root registry to its exact predecessor content identity and
  reject fabricated or merely structurally compatible rollback sources
- restore the historical source's exact governing basis and owner during
  rollback, even when the current registry changed that metadata
- enforce the declared add, clarify, rename, deprecate, split, or merge
  operation against the candidate's exact registry diff
- enforce requester, reviewer, and applier separation
- bound registry entries, entry rules, affected IDs, evidence references, and
  review collections consistently in runtime validation and JSON Schemas before
  expensive canonical sorting, hashing, or repeated validation
- add five strict JSON Schemas, a deliberately non-authoritative draft example,
  governance documentation, and regression coverage

## Changed Files

- `.speculoos/tasks/pr-103-taxonomy-registry.yaml`
- `README.md`
- `millefeuille/cli/main.py`
- `millefeuille/cli/taxonomy.py`
- `millefeuille/domain/taxonomy.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/classification-orchestration.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/millefeuille-pipeline/taxonomy-application.schema.json`
- `specs/millefeuille-pipeline/taxonomy-change-proposal.schema.json`
- `specs/millefeuille-pipeline/taxonomy-change-review.schema.json`
- `specs/millefeuille-pipeline/taxonomy-lock.schema.json`
- `specs/millefeuille-pipeline/taxonomy-registry.example.json`
- `specs/millefeuille-pipeline/taxonomy-registry.md`
- `specs/millefeuille-pipeline/taxonomy-registry.schema.json`
- `specs/pr-103-taxonomy-registry/commit-message.txt`
- `specs/pr-103-taxonomy-registry/pr-body.md`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_taxonomy_registry.py`

## Validation

- 26 focused taxonomy and contract-artifact tests passed
- all 444 repository tests passed with 7 capability skips
- repository-wide Ruff, changed-Python-file format, and diff checks passed
- taxonomy JSON Schemas, tracked JSON/YAML metadata, Speculoos validation, and
  publication checks passed

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main`.

This routine offline governance-contract PR requires exact-head Mergeguez
approval and successful GitHub checks before merge. It does not authorize
promotion to `main`, release creation, tag creation, package publication, or
deployment.

## Documentation Impact

`README.md`, `specs/millefeuille-pipeline/README.md`,
`specs/millefeuille-pipeline/classification-orchestration.md`,
`specs/millefeuille-pipeline/cli-contract.md`,
`specs/millefeuille-pipeline/remaining-work.md`, and
`specs/millefeuille-pipeline/taxonomy-registry.md` are updated in the same
change.

## Publication Boundary

Repository-local taxonomy registry, lock, proposal, review, application,
rollback contracts, offline CLI operations, schemas, documentation, synthetic
example data, tests, and Speculoos metadata only. No taxonomy generation,
production approval, credential access, provider/model/OCR call, private-paper
read, source-pack mutation, classification, live Zotero/OpenKB/PageIndex write,
stable-branch promotion, release, tag, package publication, deployment, or
approval bypass.

## Task

Speculoos task: `pr-103-taxonomy-registry`

Roadmap card: `MF-154`

The active task head field remains null by design: a commit cannot embed its own
SHA without changing it. Immutable Mergeguez review and check evidence bind
validation to GitHub's live exact head; closeout records the final head and
merge commit after merge.

The obsolete cross-platform CI stack wording is removed. PR #103 now targets
`dev` directly after integrating all completed dependency work.
