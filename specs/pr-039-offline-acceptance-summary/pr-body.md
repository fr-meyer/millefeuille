# Summary

Adds the next offline Millefeuille slice for `zotero-docai-pipeline`.

This branch:

- adds `openkb-docai-acceptance-summary/v0.1` support;
- adds redacted duplicate-scan fixture evidence;
- adds multi-attachment and non-PDF fixture coverage;
- records the remaining Zotero -> OpenKB -> classification pipeline;
- documents the CLI contract, live-run plan, tag-state machine, OCR backend
  contract, stage-manifest schema, and release/version policy;
- adds executable offline contract models for stage manifests, manual gates,
  tag transitions, and OCR evidence records.

# Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/actors.json`
- `.speculoos/review-evidence.json`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/tasks/pr-038-openkb-source-version-drift.yaml`
- `.speculoos/tasks/pr-039-offline-acceptance-summary.yaml`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/live-run-plan.md`
- `specs/millefeuille-pipeline/ocr-backend-contract.md`
- `specs/millefeuille-pipeline/release-version-policy.md`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/millefeuille-pipeline/stage-manifest.schema.json`
- `specs/millefeuille-pipeline/tag-state-machine.md`
- `specs/pr-036-speculoos-millefeuille-bootstrap/spec.md`
- `specs/pr-036-speculoos-millefeuille-bootstrap/tasks.md`
- `specs/pr-039-offline-acceptance-summary/commit-message.txt`
- `specs/pr-039-offline-acceptance-summary/pr-body.md`
- `tests/fixtures/openkb_handoff_docai_test/README.md`
- `tests/fixtures/openkb_handoff_docai_test/duplicate_scans.jsonl`
- `tests/fixtures/openkb_handoff_multi_attachment/README.md`
- `tests/fixtures/openkb_handoff_multi_attachment/duplicate_scans.jsonl`
- `tests/fixtures/openkb_handoff_multi_attachment/handoff.live.jsonl`
- `tests/fixtures/openkb_handoff_multi_attachment/openkb_outcomes.jsonl`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_contract_models.py`
- `tests/test_openkb_docai_test_fixture.py`
- `tests/test_openkb_handoff_multi_attachment_fixture.py`
- `zotero_docai_pipeline/domain/millefeuille.py`
- `zotero_docai_pipeline/utils/export.py`

# Validation

- `.venv/bin/python -m unittest -v tests.test_openkb_docai_test_fixture tests.test_openkb_handoff_multi_attachment_fixture`
- `.venv/bin/python -m unittest -v tests.test_millefeuille_contract_artifacts tests.test_millefeuille_contract_models`
- `.venv/bin/python -m unittest discover -v`
- `.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml"].sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json"].sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos actors --json`
- `speculoos validate --task pr-039-offline-acceptance-summary --validation "git diff --check" --json`

# Publication Boundary

This is an offline implementation/test/docs slice.

No live Zotero read/write, PDF download/recovery, OCR call, Mistral call,
PageIndex call, OpenKB write, source-pack write, credential change, model-route
change, ruleset change, GitHub App permission change, release, tag, package
publication, or stable-branch promotion is included.

Feature PRs target `dev`; release/promotion PRs target `main`.

GitHub publication, PR creation, Mergeguez review request, and
`mergeguez_dev_merge` auto-merge are approved for this `dev`-target feature PR
only after clean exact-head Mergeguez review and checks.

`dev` to `main` promotion, release tags, package publication, and live
provider/data actions all remain separate approval gates.
