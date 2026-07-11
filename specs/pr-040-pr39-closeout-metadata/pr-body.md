## Summary

Close out the repo-local Speculoos metadata after PR #39 merged to `dev`.

This PR records the PR #39 merge commit, marks the offline acceptance-summary
task as merged/Done, and clears the active task and pull-request surfaces so
the repository returns to an idle governed state.

## Scope

- Mark `.speculoos/tasks/pr-039-offline-acceptance-summary.yaml` as merged.
- Record PR #39 merge commit `c8e97cfbfca38542cf472e581c672980d82a33b7`.
- Clear `.speculoos/manifest.yaml` active task/branch state.
- Clear `.speculoos/surfaces/github.yaml` active PR state.
- Mark `.speculoos/surfaces/vibe-kanban.yaml` idle.

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-039-offline-acceptance-summary.yaml`
- `specs/pr-040-pr39-closeout-metadata/commit-message.txt`
- `specs/pr-040-pr39-closeout-metadata/pr-body.md`

## Publication Boundary

This is a metadata-only closeout PR. It performs no live Zotero reads/writes,
PDF download or recovery, OCR/Mistral/PageIndex call, OpenKB write,
source-pack write, credential change, `main` promotion, release tag, or package
publication.

Feature PRs target `dev`; release/promotion PRs target `main`.

GitHub publication, PR creation, Mergeguez review request, and
`mergeguez_dev_merge` auto-merge are approved for this `dev`-target metadata PR
only after clean exact-head Mergeguez review and checks.

`dev` to `main` promotion, release tags, package publication, and live
provider/data actions all remain separate approval gates.

## Validation

- Metadata parse
- `git diff --check`
- `speculoos validate`
