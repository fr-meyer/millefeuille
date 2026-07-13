## Summary

- allow dry-run artifact writing with `--artifact-root source-pack`
- add `export.artifacts.source_pack_root` and the `--source-pack-root` CLI alias
- write source-pack-mode run artifacts under `<source-pack-root>/zotero/<paper-id>/analyses/millefeuille/<run-id>/`
- require an existing source-pack `manifest.json` and fail fast when it is missing
- mark the source-pack stage passed for verified existing manifests while keeping live artifact writes blocked
- carry PR #51 closeout merge facts forward in repo-local Speculoos ledgers

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-051-pr50-closeout-metadata.yaml`
- `.speculoos/tasks/pr-052-source-pack-artifact-root.yaml`
- `README.md`
- `millefeuille/cli/main.py`
- `millefeuille/conf/export/default.yaml`
- `millefeuille/domain/artifact_writer.py`
- `millefeuille/domain/config.py`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/pr-052-source-pack-artifact-root/commit-message.txt`
- `specs/pr-052-source-pack-artifact-root/pr-body.md`
- `tests/test_millefeuille_artifact_writer.py`

## Validation

- `.venv/bin/python -m unittest tests.test_millefeuille_artifact_writer tests.test_millefeuille_artifact_commands`
- `.venv/bin/python -m unittest discover -v`
- `.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-052-source-pack-artifact-root --validation "git diff --check" --json`

## Release Flow

This is a feature PR for the dev lane: feature PRs target `dev`;
release/promotion PRs target `main`.

## Publication Boundary

Dry-run source-pack artifact-root writer only. No live Zotero write, lifecycle
tag mutation, PDF recovery, source-pack source-evidence creation or mutation,
OCR/Mistral/PageIndex provider call, model call, worker-agent execution, OpenKB
write, index write, credential change, main-branch merge, release tag, package
publication, or approval bypass.
