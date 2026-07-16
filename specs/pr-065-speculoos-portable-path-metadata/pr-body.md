## Summary

- clear a transient worktree location from the committed Speculoos manifest
- use portable checkout discovery instead of pinning workflow metadata to one worktree
- align repo-local task, GitHub, and Plancha surfaces for the correction

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-065-speculoos-portable-path-metadata.yaml`
- `specs/pr-065-speculoos-portable-path-metadata/commit-message.txt`
- `specs/pr-065-speculoos-portable-path-metadata/pr-body.md`

## Validation

- `.venv/bin/python -m unittest discover -v` (230 tests)
- `.venv/bin/ruff check .`
- metadata YAML and JSON parse check
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-065-speculoos-portable-path-metadata --validation "git diff --check" --json`
- `speculoos publish-check --task pr-065-speculoos-portable-path-metadata --pr-body specs/pr-065-speculoos-portable-path-metadata/pr-body.md --commit-message specs/pr-065-speculoos-portable-path-metadata/commit-message.txt --json`

## Release Flow

This is a metadata-only correction for the development lane: feature PRs target `dev`;
release/promotion PRs target `main`.

## Documentation Impact

No user-facing documentation changes required: the correction changes only
repo-local workflow metadata and does not alter user-facing behavior.

## Publication Boundary

Metadata-only portability correction. No live Zotero read or write, PDF
recovery, OCR/Mistral/PageIndex provider call, model call, worker-agent
execution, OpenKB write, index write, source-pack write, credential or
permission change, main-branch merge, release tag, package publication, or
approval bypass.
