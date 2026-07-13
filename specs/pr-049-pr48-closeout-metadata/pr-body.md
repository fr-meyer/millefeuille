## Summary

- mark PR #48 as merged/Done in repo-local Speculoos metadata
- record PR #48 head, merge commit, review URL, and Mergeguez check-run URL
- clear active task surfaces so Millefeuille is idle after the artifact/status slice

## Validation

- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-049-pr48-closeout-metadata --validation "git diff --check" --json`

## Boundaries

Metadata-only closeout. No live Zotero read or write, PDF recovery,
OCR/Mistral/PageIndex provider call, model call, worker-agent execution, OpenKB
write, index write, source-pack write, credential change, main-branch merge,
release tag, package publication, or approval bypass.
