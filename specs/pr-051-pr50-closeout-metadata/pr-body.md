## Summary

- mark PR #50 as merged/Done in repo-local Speculoos metadata
- record PR #50 head, merge commit, review URL, and Mergeguez check-run URL
- clear active task surfaces so Millefeuille is idle after the dry-run artifact writer slice

## Validation

- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-051-pr50-closeout-metadata --validation "git diff --check" --json`

## Boundaries

Metadata-only closeout. No live Zotero read or write, PDF recovery,
OCR/Mistral/PageIndex provider call, model call, worker-agent execution, OpenKB
write, index write, source-pack write, credential change, main-branch merge,
release tag, package publication, or approval bypass.
