## Summary

- introduce `millefeuille-paper-card/v0.2` with explicit planned index state
  and exact ordered pending lanes while preserving v0.1 compatibility
- publish canonical index status before a guarded planned-to-observed card
  refresh with exact paper, run, source, reference, lane, and output joins
- enforce the same strict card/index contract in acceptance, INDEX resume,
  artifact exposure, retrieval, and exact reruns
- reject malformed, duplicate, stale, missing, or identity-drifted state,
  including aggregate source-hash drift
- make refresh per-run locked, no-follow/reparse guarded, bounded to 1 MiB,
  single-link only, atomically recoverable, and evidenced deterministically
- preserve concurrent/displaced bytes and fail closed on hostile races,
  interrupted writes, or rollback drift
- preserve PR #100 external artifact-package support by verifying against the
  resolved source pack instead of reconstructing it from the run directory

## Changed Files

- `.speculoos/tasks/pr-101-card-index-circularity.yaml`
- `README.md`
- `millefeuille/domain/acceptance.py`
- `millefeuille/domain/artifact_writer.py`
- `millefeuille/domain/card_fixtures.py`
- `millefeuille/domain/card_index_contract.py`
- `millefeuille/domain/index_fixtures.py`
- `millefeuille/domain/millefeuille.py`
- `millefeuille/domain/offline_stages.py`
- `millefeuille/domain/retrieve.py`
- `specs/millefeuille-pipeline/paper-card.schema.json`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/millefeuille-pipeline/retrieval-index-contract.md`
- `specs/pr-101-card-index-circularity/commit-message.txt`
- `specs/pr-101-card-index-circularity/pr-body.md`
- `tests/test_millefeuille_acceptance_batch.py`
- `tests/test_millefeuille_artifact_writer.py`
- `tests/test_millefeuille_card_index_state.py`
- `tests/test_millefeuille_classification_batch.py`
- `tests/test_millefeuille_retrieval_batch.py`
- `tests/test_millefeuille_source_pack_writer.py`
- `tests/test_millefeuille_stage_cli.py`

## Validation

- 161 focused card/index and affected-flow tests passed with 4 capability skips
- the focused external artifact-package regression passed
- all 399 repository tests passed with 6 capability skips
- repository-wide Ruff and diff checks passed
- schema parsing, privacy/manual-gate contract checks, Speculoos validation,
  and publication checks passed

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main`.

This routine offline contract PR requires exact-head Mergeguez approval and
successful GitHub checks before merge. It does not authorize promotion to
`main`, release creation, tag creation, package publication, or deployment.

## Documentation Impact

`README.md`, `remaining-work.md`, `paper-card.schema.json`, and
`retrieval-index-contract.md` document the v0.2 state transition, strict join,
and recovery boundary.

## Publication Boundary

Repository-local card/index contract code, offline transaction safety, schemas,
documentation, synthetic tests, and Speculoos metadata only. No credentials,
provider/model/OCR calls, private paper reads, live Zotero/OpenKB/PageIndex
writes, stable-branch promotion, release, tag, package publication, deployment,
or approval bypass.

## Task

Speculoos task: `pr-101-card-index-circularity`

Roadmap card: `MF-135`

The active task head field remains null by design: a commit cannot embed its own
SHA without changing it. Immutable Mergeguez review and check evidence bind
validation to GitHub's live exact head; closeout records the final head and
merge commit after merge.

The original PR #94 stack wording is obsolete. PR #101 now targets `dev`
directly after integrating the completed PR #100 work.
