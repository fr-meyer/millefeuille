## Summary

- add offline paper-card fixture writers under existing verified source packs
- require card writes to match the source-pack manifest and existing hierarchical summary bundles before writing any run-scoped card artifacts
- materialize run-scoped paper-card json and markdown under `analyses/millefeuille/<run-id>/cards`
- surface paper-card refs through source-pack artifact-root stage manifests and artifact indexes
- add CLI aliases and config wiring for `--card-evidence`

## Changed Files

- `millefeuille/domain/card_fixtures.py`
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
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-061-card-fixtures.yaml`
- `specs/pr-061-card-fixtures/commit-message.txt`
- `specs/pr-061-card-fixtures/pr-body.md`

## Validation

- `/home/node/.openclaw/repos/millefeuille/.venv/bin/python -m unittest tests.test_millefeuille_source_pack_writer tests.test_millefeuille_artifact_writer tests.test_millefeuille_contract_models`
- `/home/node/.openclaw/repos/millefeuille/.venv/bin/python -m unittest discover -v`
- `/home/node/.openclaw/repos/millefeuille/.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-061-card-fixtures --validation "git diff --check" --json`

## Release Flow

This is a feature PR for the dev lane: feature PRs target `dev`;
release/promotion PRs target `main`.

This branch starts from exact `dev` after PR `#60` merged.

## Publication Boundary

Fixture-first paper cards only. No live Zotero read or write, lifecycle tag
mutation, live PDF recovery, OCR/Mistral/PageIndex provider call, model call,
worker-agent execution, OpenKB write, index write, credential change,
main-branch merge, release tag, package publication, or approval bypass.
