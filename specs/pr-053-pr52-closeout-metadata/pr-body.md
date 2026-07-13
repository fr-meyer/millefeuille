## Summary

- mark PR #52 as merged/Done in repo-local Speculoos metadata
- record PR #52 head, merge commit, review URL, and Mergeguez check-run URL
- clear active task surfaces so Millefeuille is idle after the source-pack artifact-root slice

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-052-source-pack-artifact-root.yaml`
- `.speculoos/tasks/pr-053-pr52-closeout-metadata.yaml`
- `specs/pr-053-pr52-closeout-metadata/commit-message.txt`
- `specs/pr-053-pr52-closeout-metadata/pr-body.md`

## Validation

- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-053-pr52-closeout-metadata --validation "git diff --check" --json`

## Release Flow

This is a metadata-only closeout PR for the dev lane: feature PRs target `dev`;
release/promotion PRs target `main`.

## Publication Boundary

Metadata-only closeout. No live Zotero read or write, PDF recovery,
OCR/Mistral/PageIndex provider call, model call, worker-agent execution, OpenKB
write, index write, source-pack write, credential change, main-branch merge,
release tag, package publication, or approval bypass.
