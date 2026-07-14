## Summary

- add offline retrieval/index fixture writers under existing verified source packs
- require index writes to match the source-pack manifest plus existing route, summary, and paper-card artifacts before writing any run-scoped index status
- materialize run-scoped retrieval/index status under `analyses/millefeuille/<run-id>/index`
- surface retrieval/index lane summaries through source-pack artifact-root stage manifests and artifact indexes
- add CLI aliases, config wiring, and contract schemas for `--index-evidence`

## Changed Files

- `millefeuille/domain/index_fixtures.py`
- `millefeuille/domain/millefeuille.py`
- `millefeuille/domain/artifact_writer.py`
- `millefeuille/domain/config.py`
- `millefeuille/cli/commands.py`
- `millefeuille/cli/main.py`
- `millefeuille/conf/export/default.yaml`
- `tests/test_millefeuille_source_pack_writer.py`
- `tests/test_millefeuille_artifact_writer.py`
- `tests/test_millefeuille_contract_models.py`
- `tests/test_millefeuille_contract_artifacts.py`
- `README.md`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/retrieval-index-contract.md`
- `specs/millefeuille-pipeline/retrieval-index-status.schema.json`
- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-062-index-fixtures.yaml`
- `specs/pr-062-index-fixtures/commit-message.txt`
- `specs/pr-062-index-fixtures/pr-body.md`

## Documentation Impact

- updates `README.md` with the new fixture-first `--index-evidence` lane
- extends `specs/millefeuille-pipeline/README.md` and `specs/millefeuille-pipeline/retrieval-index-contract.md`
- adds `specs/millefeuille-pipeline/retrieval-index-status.schema.json`

## Validation

- `/home/node/.openclaw/repos/millefeuille/.venv/bin/python -m unittest tests.test_millefeuille_source_pack_writer tests.test_millefeuille_artifact_writer tests.test_millefeuille_contract_models`
- `/home/node/.openclaw/repos/millefeuille/.venv/bin/python -m unittest discover -v`
- `/home/node/.openclaw/repos/millefeuille/.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-062-index-fixtures --validation "git diff --check" --json`

## Release Flow

This is a feature PR for the dev lane: feature PRs target `dev`;
release/promotion PRs target `main`.

This branch starts from exact `dev` after PR `#61` merged.

## Publication Boundary

Fixture-first retrieval/index status only. No live Zotero read or write,
lifecycle tag mutation, live PDF recovery, OCR/Mistral/PageIndex provider
call, model call, worker-agent execution, OpenKB write, live index write,
credential change, main-branch merge, release tag, package publication, or
approval bypass.
