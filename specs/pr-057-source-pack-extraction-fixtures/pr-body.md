## Summary

- add offline native extraction fixture writers under existing verified source packs
- add offline OCR extraction fixture writers that record requested model plus returned provider model/version without provider payloads
- preflight every extraction record against source-pack manifest identity and source hash before any sidecar write
- surface extraction sidecars through source-pack artifact-root stage manifests and artifact indexes
- add CLI aliases and config wiring for `--native-extraction-evidence` and `--ocr-extraction-evidence`

## Changed Files

- `millefeuille/domain/extraction_fixtures.py`
- `millefeuille/domain/millefeuille.py`
- `millefeuille/domain/artifact_writer.py`
- `millefeuille/domain/config.py`
- `millefeuille/cli/commands.py`
- `millefeuille/cli/main.py`
- `millefeuille/conf/export/default.yaml`
- `tests/test_millefeuille_source_pack_writer.py`
- `tests/test_millefeuille_artifact_writer.py`
- `tests/test_millefeuille_contract_models.py`
- `README.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/ocr-backend-contract.md`
- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-057-source-pack-extraction-fixtures.yaml`
- `specs/pr-057-source-pack-extraction-fixtures/commit-message.txt`
- `specs/pr-057-source-pack-extraction-fixtures/pr-body.md`

## Validation

- `/home/node/.openclaw/repos/millefeuille/.venv/bin/python -m unittest tests.test_millefeuille_source_pack_writer tests.test_millefeuille_artifact_writer tests.test_millefeuille_contract_models`
- `/home/node/.openclaw/repos/millefeuille/.venv/bin/python -m unittest discover -v`
- `/home/node/.openclaw/repos/millefeuille/.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-057-source-pack-extraction-fixtures --validation "git diff --check" --json`

## Release Flow

This is a feature PR for the dev lane: feature PRs target `dev`;
release/promotion PRs target `main`.

This branch has already been rebased onto exact `dev` after PR `#56` merged.

## Publication Boundary

Fixture-first extraction sidecars only. No live Zotero read or write,
lifecycle tag mutation, live PDF recovery, OCR/Mistral/PageIndex provider
call, model call, worker-agent execution, OpenKB write, index write,
credential change, main-branch merge, release tag, package publication, or
approval bypass.
