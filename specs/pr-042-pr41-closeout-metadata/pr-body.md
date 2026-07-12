## Summary

Close out repo-local Speculoos metadata after PR #41 merged to `dev`.

This PR records the PR #41 merge commit, marks the live golden-run evidence
task as merged/Done, and refreshes the completed PR history now that the
Millefeuille golden-run fixture is on `dev`.

## Scope

- Mark `.speculoos/tasks/pr-041-live-golden-route-evidence.yaml` as merged.
- Record PR #41 head `6b6a42ef52823e171bda58a111607858008dbc82`.
- Record PR #41 merge commit `e8f205b88948c2ee88276db2b6e4aea3a0273da1`.
- Record Mergeguez review and check-run evidence for PR #41.
- Add PR #41 to completed PR history in `.speculoos/manifest.yaml` and `.speculoos/surfaces/github.yaml`.
- Refresh the recorded full-test count from `142` to `146`.

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/tasks/pr-041-live-golden-route-evidence.yaml`
- `specs/pr-042-pr41-closeout-metadata/commit-message.txt`
- `specs/pr-042-pr41-closeout-metadata/pr-body.md`

## Publication Boundary

This is a metadata-only closeout PR. It performs no live Zotero reads/writes,
PDF download or recovery, OCR/Mistral/PageIndex call, OpenKB write,
source-pack write, credential change, repo/package rename, `main` promotion,
release tag, or package publication.

Feature PRs target `dev`; release/promotion PRs target `main`.

GitHub publication, PR creation, Mergeguez review request, and
`mergeguez_dev_merge` auto-merge are approved for this `dev`-target metadata PR
only after clean exact-head Mergeguez review and checks.

`dev` to `main` promotion, release tags, package publication, Zotero writeback,
and live provider/data actions all remain separate approval gates.

## Validation

- Metadata parse
- `git diff --check`
- `speculoos validate`
