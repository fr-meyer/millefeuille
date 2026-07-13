## Summary

- add artifact-index load, validation, write, path-resolution, and completion helpers
- add read-only `millefeuille artifacts` and `millefeuille status` commands
- keep those commands outside the Hydra/Zotero/OCR provider path, so they work without Zotero or provider env vars
- add fixture-only complete and blocked artifact-index cases plus CLI coverage
- record PR #48 Speculoos task metadata and PR #47 merge closeout facts

## Validation

- `.venv/bin/python -m unittest tests.test_millefeuille_artifact_commands`
- `.venv/bin/python -m unittest discover -v`
- `.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-048-millefeuille-artifact-store-status --validation "git diff --check" --json`

## Boundaries

Fixture-only artifact/status implementation. No live Zotero read or write, PDF
recovery, OCR/Mistral/PageIndex provider call, model call, worker-agent
execution, OpenKB write, index write, source-pack write, credential change,
main-branch merge, release tag, package publication, or approval bypass.
