## Summary

- add one strict read-only status surface joining canonical source-pack,
  stage-manifest, and artifact-index state
- progressively join retrieval/index, acceptance, classification, writeback,
  provider usage, quality, ledger, and Zotero live-state observations
- require exact identities, refs, stage outputs, lane sets, content digests,
  and canonical Zotero source and post-write versions
- emit deterministic sanitized status with explicit blockers and explicitly
  non-authoritative observational readiness
- preserve compatible index-only status while adding the canonical
  source-pack-root form
- add strict schemas, operator documentation, CLI compatibility coverage,
  hostile-input regressions, and committed Speculoos metadata

## Changed Files

- `.speculoos/tasks/pr-107-status-observability.yaml`
- `README.md`
- `millefeuille/cli/artifacts.py`
- `millefeuille/domain/status_observability.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/completion-gate-result.schema.json`
- `specs/millefeuille-pipeline/status-observability.md`
- `specs/millefeuille-pipeline/status-observation.schema.json`
- `specs/millefeuille-pipeline/status.schema.json`
- `specs/millefeuille-pipeline/vision.md`
- `specs/millefeuille-pipeline/zotero-writeback-result.schema.json`
- `specs/pr-107-status-observability/commit-message.txt`
- `specs/pr-107-status-observability/pr-body.md`
- `tests/test_millefeuille_artifact_commands.py`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_status_observability.py`

## Validation

- 31 focused status, artifact-command, and contract-artifact tests passed
- all 534 repository tests passed with 8 expected platform skips
- Ruff lint and format checks on the five changed Python files passed
- diff, private-data, task metadata, delivery-policy, and all commit-identity
  checks passed
- Speculoos exact-task validation and publication checks passed

## Release Flow

Feature PRs target `dev`; release/promotion PRs target `main`.

This routine repository-local observability PR requires exact-head Mergeguez
approval and successful GitHub checks before merge. It does not authorize
promotion to `main`, release creation, tag creation, package publication, or
deployment.

## Documentation Impact

`README.md`, `specs/millefeuille-pipeline/README.md`,
`specs/millefeuille-pipeline/artifact-storage.md`,
`specs/millefeuille-pipeline/cli-contract.md`,
`specs/millefeuille-pipeline/status-observability.md`, and
`specs/millefeuille-pipeline/vision.md` are updated in the same change.

## Publication Boundary

Repository-local read-only status joins, sanitized observation envelopes, CLI
reporting, schemas, documentation, synthetic tests, and Speculoos metadata
only. No credential access, provider/model/OCR call, private-paper read,
source-pack mutation, live Zotero/OpenKB/PageIndex write, stable-branch
promotion, release, tag, package publication, deployment, or approval bypass.
Supplied observations remain local, operator-provided, content-addressed, and
non-authoritative. Approved-live output can become
`observational-live-ready`; it cannot claim authoritative live completion.

## Task

Speculoos task: `pr-107-status-observability`

Roadmap card: `MF-141`

The active task head field remains null by design: a commit cannot embed its
own SHA without changing it. Immutable Mergeguez review and check evidence
bind validation to GitHub's live exact head; closeout records the final head
and merge commit after merge.

Current `dev` was integrated before validation. The obsolete stacked-head
evidence in the original pull request was replaced by exact current-head GCP
validation evidence.
