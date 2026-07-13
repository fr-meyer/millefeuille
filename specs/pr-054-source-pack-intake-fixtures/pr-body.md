## Summary

- add fixture-first source-pack intake from explicit recovered-PDF evidence JSON
- verify recovered bytes against expected SHA-256 before writing `source.pdf`
  and `manifest.json`
- refuse hash/size mismatch and existing source-pack drift without overwrite
- add `millefeuille source-pack intake` outside the Hydra/provider path
- require source-pack manifests to expose a `sha256:<hex>` source hash before
  `--artifact-root source-pack` treats them as verified
- carry PR #53 closeout merge facts forward in repo-local Speculoos ledgers

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-053-pr52-closeout-metadata.yaml`
- `.speculoos/tasks/pr-054-source-pack-intake-fixtures.yaml`
- `README.md`
- `millefeuille/cli/main.py`
- `millefeuille/cli/source_pack.py`
- `millefeuille/domain/artifact_writer.py`
- `millefeuille/domain/source_packs.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/source-pack-manifest.schema.json`
- `specs/pr-054-source-pack-intake-fixtures/commit-message.txt`
- `specs/pr-054-source-pack-intake-fixtures/pr-body.md`
- `tests/test_millefeuille_artifact_writer.py`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_source_pack_writer.py`

## Validation

- `.venv/bin/python -m unittest tests.test_millefeuille_source_pack_writer tests.test_millefeuille_artifact_writer tests.test_millefeuille_artifact_commands tests.test_millefeuille_contract_artifacts`
- `.venv/bin/python -m unittest discover -v`
- `.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-054-source-pack-intake-fixtures --validation "git diff --check" --json`

## Release Flow

This is a feature PR for the dev lane: feature PRs target `dev`;
release/promotion PRs target `main`.

## Publication Boundary

Fixture-first source-pack intake only. No live Zotero read or write, lifecycle
tag mutation, live PDF recovery, OCR/Mistral/PageIndex provider call, model
call, worker-agent execution, OpenKB write, index write, credential change,
main-branch merge, release tag, package publication, or approval bypass.
