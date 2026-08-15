## Summary

- add a strict approved-live receipt model, schema, bounded secure loader,
  canonical SHA-256 identity, expiry window, and exact single-use replay state
- bind exact operations, targets, selectors, item caps, runs, roots,
  provider/model choices, call and cost limits, disposal, and stop conditions
- reject tampering, replay, wildcard scope, secret-like material, duplicate
  fields, malformed or non-finite JSON, digest forgery, and scope drift
- emit allowlisted self-verifying audit records with canonical root identities
  and no credential material
- expose an explicit CLI receipt gate without promoting preview mode; a valid
  receipt still exits 3 because approved-live effects remain unimplemented
- extend secure no-follow reads with a backwards-compatible byte limit and
  file-growth detection

## Changed Files

- `.speculoos/tasks/pr-102-approved-live-receipts.yaml`
- `millefeuille/cli/stages.py`
- `millefeuille/domain/live_receipts.py`
- `millefeuille/domain/secure_io.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/approved-live-audit.schema.json`
- `specs/millefeuille-pipeline/approved-live-receipt.schema.json`
- `specs/millefeuille-pipeline/approved-live-receipts.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/live-run-plan.md`
- `specs/pr-102-approved-live-receipts/commit-message.txt`
- `specs/pr-102-approved-live-receipts/pr-body.md`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_live_receipts.py`
- `tests/test_millefeuille_secure_io.py`

## Validation

- 56 focused receipt, secure-I/O, CLI, and contract tests passed with 2
  capability skips
- all 422 repository tests passed with 7 capability skips
- repository-wide Ruff and diff checks passed
- all JSON Schemas, tracked JSON/YAML metadata, Speculoos validation, and
  publication checks passed

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main`.

This routine offline authorization-contract PR requires exact-head Mergeguez
approval and successful GitHub checks before merge. It does not authorize
promotion to `main`, release creation, tag creation, package publication, or
deployment.

## Documentation Impact

No user-facing documentation changes required outside the normative pipeline
contract packet: approved-live effects remain unimplemented, so there is no
new live operator workflow to document in the top-level README or docs/. The
pipeline README, receipt contract, CLI contract, and live-run plan document
the exact authority, replay, audit, and no-effect boundaries.

## Publication Boundary

Repository-local approved-live authorization contracts, secure bounded reads,
no-effect CLI validation, schemas, documentation, synthetic tests, and
Speculoos metadata only. No credential value access, provider/model/OCR call,
private paper read, live Zotero/OpenKB/PageIndex write, source-pack mutation,
stable-branch promotion, release, tag, package publication, deployment, or
approval bypass.

## Task

Speculoos task: `pr-102-approved-live-receipts`

Roadmap card: `MF-100`

The active task head field remains null by design: a commit cannot embed its own
SHA without changing it. Immutable Mergeguez review and check evidence bind
validation to GitHub's live exact head; closeout records the final head and
merge commit after merge.

The original PR #99 stack wording is obsolete. PR #102 now targets `dev`
directly after integrating all completed dependency work.
