## Summary

- accept strict v0.1 `sha256:<hex>` and v0.2 `sha256-aggregate:<hex>` identities in retrieval-index runtime validation
- align the retrieval-index JSON Schema and normative contract
- reject malformed, uppercase, whitespace-padded, and newline-tainted identities
- add schema, runtime, fixture-loader, and multi-PDF propagation regression coverage
- record the canonical Speculoos delivery gates for roadmap card MF-006

## Changed Files

- `.speculoos/tasks/pr-094-aggregate-source-hash.yaml`
- `millefeuille/domain/millefeuille.py`
- `specs/millefeuille-pipeline/retrieval-index-contract.md`
- `specs/millefeuille-pipeline/retrieval-index-status.schema.json`
- `specs/pr-094-aggregate-source-hash/commit-message.txt`
- `specs/pr-094-aggregate-source-hash/pr-body.md`
- `tests/test_millefeuille_source_hash_compatibility.py`

## Validation

- focused source-hash compatibility tests passed
- full unittest discovery passed: 338 tests
- repository-wide Ruff passed
- YAML and JSON metadata parsing passed
- `git diff --check`, changed-file private-data scan, Speculoos validation, and publication checks passed

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main`.

This routine feature PR requires exact-head Mergeguez approval and successful GitHub checks before merge. It does not authorize promotion to `main`, release creation, package publication, or deployment.

## Documentation Impact

No user-facing documentation changes required. The normative contract file is a schema companion that clarifies runtime validation; this PR changes no user-facing product behavior or operator workflow requiring README or docs updates.

## Publication Boundary

Offline runtime, schema, contract, synthetic tests, and repository-local metadata only. No credential lookup, provider/model/OCR call, private paper read, live Zotero/OpenKB/PageIndex/source-pack mutation, stable-branch promotion, release, package publication, deployment, or approval bypass.

## Task

Speculoos task: `pr-094-aggregate-source-hash`

Roadmap card: `MF-006`

Supersedes closed PR #90; this branch name satisfies the repository actor policy.
