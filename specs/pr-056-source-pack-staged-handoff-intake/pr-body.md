## Summary

- add staged dry-run source-pack intake from explicit recovered-PDF fixture evidence
- require evidence to match validated handoff rows by item key, attachment key, canonical filename, file size, and SHA-256
- preflight all selected rows before any source-pack or artifact write
- add `--source-pack-intake-evidence` as the CLI alias for the staged path
- document the new command shape in the artifact/source-pack README section

## Changed Files

- `millefeuille/domain/source_packs.py`
- `millefeuille/domain/config.py`
- `millefeuille/cli/commands.py`
- `millefeuille/cli/main.py`
- `tests/test_millefeuille_source_pack_writer.py`
- `tests/test_millefeuille_artifact_writer.py`
- `README.md`
- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-055-pr54-closeout-metadata.yaml`
- `.speculoos/tasks/pr-056-source-pack-staged-handoff-intake.yaml`
- `specs/pr-056-source-pack-staged-handoff-intake/commit-message.txt`
- `specs/pr-056-source-pack-staged-handoff-intake/pr-body.md`

## Validation

- `.venv/bin/python -m unittest tests.test_millefeuille_source_pack_writer tests.test_millefeuille_artifact_writer`
- `.venv/bin/python -m unittest discover -v`
- `.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-056-source-pack-staged-handoff-intake --validation "git diff --check" --json`

## Release Flow

This is a dev-lane feature PR: feature PRs target `dev`; release/promotion PRs target `main`.

## Publication Boundary

Fixture-first staged source-pack intake only. Local source-pack writes are
allowed only under an explicit `--source-pack-root` after handoff/evidence
preflight. No live Zotero write, lifecycle tag mutation, live PDF recovery,
OCR/Mistral/PageIndex provider call, model call, worker-agent execution, OpenKB
write, index write, credential change, main-branch merge, release tag, package
publication, or approval bypass.
