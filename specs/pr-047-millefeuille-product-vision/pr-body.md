## Summary

- define Millefeuille as the complete paper-processing CLI, not only an OCR or OpenKB handoff tool
- add artifact-root, artifact-index, model-profile, hierarchical-summary, and paper-card contracts
- make OpenKB/PageIndex indexing first-class while keeping ConDB/ChatIndex optional support lanes
- define CLI-owned classification modes, including optional multi-agent mode, with Zotero writeback separated
- extend stage/tag/manual-gate contracts and offline consistency tests

## Validation

- `.venv/bin/python -m unittest tests.test_millefeuille_contract_artifacts tests.test_millefeuille_contract_models`
- `.venv/bin/python -m unittest discover -v`
- `.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-047-millefeuille-product-vision --validation "git diff --check" --json`

## Boundaries

No live Zotero read or write, PDF recovery, OCR/Mistral/PageIndex provider call,
model call, worker-agent execution, OpenKB write, index write, source-pack
write, credential change, main-branch merge, release tag, package publication,
or approval bypass.
