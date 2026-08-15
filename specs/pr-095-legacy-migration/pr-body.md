## Summary

- define the supported legacy Hydra and source-pack lifecycle command surfaces
- document operator migration for command/config, v0.1/v0.2 source packs, artifact roots, historical tags, safe stop, and rollback
- pin direct PageIndex HTTP/SDK to legacy compatibility and require MCP-only connectors for new lifecycle work
- add release-based deprecation and removal exit criteria
- add operator navigation, executable documentation-contract checks, and canonical Speculoos delivery gates for roadmap card MF-005
- preserve the repository hard-rename invariant while describing predecessor tags by operational role

## Changed Files

- `.speculoos/tasks/pr-095-legacy-migration.yaml`
- `README.md`
- `millefeuille/conf/ocr/pageindex.yaml`
- `millefeuille/domain/config.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/legacy-migration.md`
- `specs/millefeuille-pipeline/release-version-policy.md`
- `specs/pr-095-legacy-migration/commit-message.txt`
- `specs/pr-095-legacy-migration/pr-body.md`
- `tests/test_millefeuille_contract_artifacts.py`

## Validation

- focused contract-artifact and hard-rename tests passed
- full unittest discovery passed
- repository-wide Ruff passed
- YAML and JSON metadata parsing passed
- `git diff --check`, changed-file private-data scan, Speculoos validation, and publication checks passed

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main`.

This routine feature PR requires exact-head Mergeguez approval and successful GitHub checks before merge. It does not authorize promotion to `main`, release creation, package publication, or deployment.

## Documentation Impact

User-facing documentation updated. `README.md` and `specs/millefeuille-pipeline/legacy-migration.md` now document the migration, compatibility, safe-stop, rollback, and release boundaries for operators.

## Publication Boundary

Offline configuration comments, operator contracts, navigation, synthetic documentation tests, and repository-local metadata only. No credential lookup, provider/model/OCR call, private paper read, live Zotero/OpenKB/PageIndex/source-pack mutation, stable-branch promotion, release, package publication, deployment, or approval bypass.

## Task

Speculoos task: `pr-095-legacy-migration`

Roadmap card: `MF-005`

Supersedes closed PR #91; this branch name satisfies the repository actor policy.
