## Summary

- mark PR #54 as merged/Done in repo-local Speculoos metadata
- record PR #54 head, merge commit, review URL, Mergeguez check-run URL, and branch cleanup
- add PR #54 to completed task and GitHub PR ledgers
- move active task surfaces to this PR #55 closeout branch

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-054-source-pack-intake-fixtures.yaml`
- `.speculoos/tasks/pr-055-pr54-closeout-metadata.yaml`
- `specs/pr-055-pr54-closeout-metadata/commit-message.txt`
- `specs/pr-055-pr54-closeout-metadata/pr-body.md`

## Validation

- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-055-pr54-closeout-metadata --validation "git diff --check" --json`

## Release Flow

This is a metadata-only closeout PR for the dev lane: feature PRs target `dev`;
release/promotion PRs target `main`.

## Publication Boundary

Metadata-only closeout. No live Zotero read or write, PDF recovery,
OCR/Mistral/PageIndex provider call, model call, worker-agent execution, OpenKB
write, index write, source-pack write, credential change, main-branch merge,
release tag, package publication, or approval bypass.
