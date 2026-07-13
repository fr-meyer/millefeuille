## Summary

- add explicit artifact export config plus `--artifact-root` and `--run-id` CLI aliases
- write per-paper `stage-manifest.json` and `artifact-index.json` from dry-run discovery plus OpenKB handoff preview evidence
- place outputs under `<artifact-root>/<paper-id>/<run-id>/`
- record manual gates for PDF recovery and source-pack writing while keeping those stages unperformed
- reject live artifact writes and the reserved `source-pack` artifact root for this slice
- carry PR #49 closeout merge facts forward in repo-local Speculoos ledgers

## Validation

- `.venv/bin/python -m unittest tests.test_millefeuille_artifact_writer tests.test_millefeuille_artifact_commands`
- `.venv/bin/python -m unittest discover -v`
- `.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-050-artifact-writer-dry-run-source-pack --validation "git diff --check" --json`

## Boundaries

Dry-run artifact writer only. No live Zotero write, lifecycle tag mutation, PDF
recovery, source-pack creation, OCR/Mistral/PageIndex provider call, model call,
worker-agent execution, OpenKB write, index write, credential change,
main-branch merge, release tag, package publication, or approval bypass.
