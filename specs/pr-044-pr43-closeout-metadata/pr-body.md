## Summary

Close repo-local Speculoos metadata after PR #43 merged to `dev`.

This PR records the PR #43 merge commit, marks the hard-rename task as
merged/Done, and refreshes the completed PR history now that the Millefeuille
identity migration is on `dev`.

## Scope

- Mark `.speculoos/tasks/pr-043-millefeuille-hard-rename.yaml` as merged.
- Record PR #43 head `8adeba3dd53cf7daf4f162c69da404095be178a6`.
- Record PR #43 merge commit `e35a7acd279fb85fe4ab26294893a0c837425835`.
- Record Mergeguez review and check-run evidence ids for PR #43.
- Add PR #43 to completed PR history in `.speculoos/manifest.yaml` and `.speculoos/surfaces/github.yaml`.
- Move active repo-local surfaces to this metadata closeout branch.

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-043-millefeuille-hard-rename.yaml`
- `.speculoos/tasks/pr-044-pr43-closeout-metadata.yaml`
- `specs/pr-044-pr43-closeout-metadata/commit-message.txt`
- `specs/pr-044-pr43-closeout-metadata/pr-body.md`

## Publication Boundary

This is a metadata-only closeout PR. It performs no live Zotero reads/writes,
PDF download or recovery, OCR/Mistral/PageIndex call, OpenKB write,
source-pack write, credential change, compatibility shim, legacy alias,
`main` promotion, release tag, or package publication.

Feature PRs target `dev`; release/promotion PRs target `main`.

GitHub publication, PR creation, Mergeguez review request, and
`mergeguez_dev_merge` auto-merge are approved for this `dev`-target metadata PR
only after clean exact-head Mergeguez review and checks.

## Validation

- Metadata parse
- `git diff --check`
- `speculoos validate`
- hard-rename identity scan
