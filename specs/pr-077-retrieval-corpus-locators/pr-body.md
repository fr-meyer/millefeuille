## Summary

Implements Speculoos task `pr-077-retrieval-corpus-locators`: add deterministic corpus-aware, read-only artifact retrieval over verified local source-pack runs.

- resolves packages by paper id, Zotero item key, source-pack slug, normalized DOI, or normalized exact title
- fails closed on missing or ambiguous corpus matches, unsafe identifiers, symlinked paper cards, and cross-wired identity
- verifies canonical index refs and optional acceptance/classification/writeback identity before exposing their refs
- filters summary refs by scope, grain, exact section locator, page locator, or classification-evidence need
- filters index refs by lane and retains acceptance, classification, and writeback refs when present
- returns refs and status metadata without returning private paper content
- documents the offline contract and keeps multi-run/batch retrieval explicitly out of scope

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-077-retrieval-corpus-locators.yaml`
- `README.md`
- `millefeuille/cli/stages.py`
- `millefeuille/domain/retrieve.py`
- `millefeuille/domain/stage_runtime.py`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/millefeuille-pipeline/retrieval-index-contract.md`
- `specs/pr-077-retrieval-corpus-locators/commit-message.txt`
- `specs/pr-077-retrieval-corpus-locators/pr-body.md`
- `tests/test_millefeuille_retrieve.py`

## Validation

- focused retrieval and stage-CLI suites — 16 passed
- full unittest discovery — 261 passed
- Ruff — passed
- hard-rename identity guard — passed
- YAML/JSON metadata parse — passed
- `git diff --check` — passed
- Speculoos validation, private-data scan, documentation sync, and publication checks — passed

## Documentation Impact

User-facing documentation in `README.md`,
`specs/millefeuille-pipeline/cli-contract.md`,
`specs/millefeuille-pipeline/retrieval-index-contract.md`, and
`specs/millefeuille-pipeline/remaining-work.md` explains the new local corpus
locators, section/page and classification-evidence filters, ref-only output,
fail-closed ambiguity/identity behavior, and the remaining batch/live
boundaries.

## Release Flow

Feature PRs target `dev`; release/promotion PRs target `main` and remain separately gated.

## Publication Boundary

Offline, read-only ref resolution over existing temporary fixture source-pack runs. No private paper content return, live Zotero read or write, PDF recovery, durable source-pack mutation, OCR/model/provider call, worker-agent execution, OpenKB/index write, credential or permission change, release, package publication, production deployment, or approval bypass.
