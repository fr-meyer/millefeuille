## Summary

- add offline route-selection fixture writers under existing verified source packs
- require route writes to match the native and/or OCR extraction sidecars already present under each source pack
- preflight every route-selection record against source-pack manifest identity and source hash before any selected sidecar write
- surface selected fulltext and route evidence through source-pack artifact-root stage manifests and artifact indexes
- add CLI aliases and config wiring for `--route-selection-evidence`

## Changed Files

- `millefeuille/domain/route_fixtures.py`
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
- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-058-route-selection-fixtures.yaml`
- `specs/pr-058-route-selection-fixtures/commit-message.txt`
- `specs/pr-058-route-selection-fixtures/pr-body.md`

## Validation

- `/home/node/.openclaw/repos/millefeuille/.venv/bin/python -m unittest tests.test_millefeuille_source_pack_writer tests.test_millefeuille_artifact_writer tests.test_millefeuille_contract_models`
- `/home/node/.openclaw/repos/millefeuille/.venv/bin/python -m unittest discover -v`
- `/home/node/.openclaw/repos/millefeuille/.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-058-route-selection-fixtures --validation "git diff --check" --json`

## Release Flow

This is a feature PR for the dev lane: feature PRs target `dev`;
release/promotion PRs target `main`.

Because the branch currently stacks on the local PR `#57` head while PR `#56`
review is still pending, rebase to exact `dev` after PR `#56` and PR `#57`
merge before publishing PR `#58`.

## Publication Boundary

Fixture-first route selection only. No live Zotero read or write, lifecycle
tag mutation, live PDF recovery, OCR/Mistral/PageIndex provider call, model
call, worker-agent execution, OpenKB write, index write, credential change,
main-branch merge, release tag, package publication, or approval bypass.
