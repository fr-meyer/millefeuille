# Summary

Adds the next offline Millefeuille slice for `millefeuille`.

This branch:

- adds acceptance synthesis across source-pack, extraction, route, structure,
  summary, card, and index artifacts;
- adds offline classification orchestration and governed writeback previews;
- exposes preview/read-only stage CLI commands for `acceptance`, `classify`,
  `writeback`, `retrieve`, `models`, and `run`;
- prepares local release-candidate preflight documentation;
- keeps the slice offline-only on top of merged PR `#62`.

Feature PRs target `dev`. Release/promotion PRs target `main`.

# Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/tasks/pr-062-index-fixtures.yaml`
- `.speculoos/tasks/pr-063-acceptance-classification-cli.yaml`
- `README.md`
- `millefeuille/cli/commands.py`
- `millefeuille/cli/main.py`
- `millefeuille/cli/stages.py`
- `millefeuille/clients/zotero_client.py`
- `millefeuille/domain/acceptance.py`
- `millefeuille/domain/artifact_writer.py`
- `millefeuille/domain/artifacts.py`
- `millefeuille/domain/card_fixtures.py`
- `millefeuille/domain/classification.py`
- `millefeuille/domain/config.py`
- `millefeuille/domain/extraction_fixtures.py`
- `millefeuille/domain/index_fixtures.py`
- `millefeuille/domain/millefeuille.py`
- `millefeuille/domain/model_profiles.py`
- `millefeuille/domain/release_preflight.py`
- `millefeuille/domain/retrieve.py`
- `millefeuille/domain/route_fixtures.py`
- `millefeuille/domain/source_packs.py`
- `millefeuille/domain/stage_runtime.py`
- `millefeuille/domain/structure_fixtures.py`
- `millefeuille/domain/summary_fixtures.py`
- `millefeuille/domain/writeback.py`
- `millefeuille/orchestration/pipeline.py`
- `millefeuille/utils/export.py`
- `millefeuille/utils/logging.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/release-candidate-preflight.md`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/pr-063-acceptance-classification-cli/commit-message.txt`
- `specs/pr-063-acceptance-classification-cli/pr-body.md`
- `tests/test_millefeuille_artifact_writer.py`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_contract_models.py`
- `tests/test_millefeuille_live_golden_run_fixture.py`
- `tests/test_millefeuille_source_pack_writer.py`
- `tests/test_millefeuille_stage_cli.py`
- `tests/test_openkb_handoff_identity.py`
- `tests/test_openkb_handoff_multi_attachment_fixture.py`
- `tests/test_openkb_handoff_pipeline.py`
- `tests/test_openkb_handoff_security.py`
- `tests/test_openkb_millefeuille_test_fixture.py`
- `tests/test_openkb_source_version_drift.py`
- `tests/test_outcome_tag_removal.py`
- `tests/test_selection_tagging.py`
- `tests/test_zotero_client_pagination.py`
- `tests/test_zotero_credentials_policy.py`

# Validation

- `.venv/bin/python -m unittest tests.test_millefeuille_source_pack_writer tests.test_millefeuille_artifact_writer tests.test_millefeuille_contract_models tests.test_millefeuille_stage_cli`
- `.venv/bin/python -m unittest discover -v`
- `.venv/bin/ruff check .`
- `ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml", "specs/**/*.yaml"].flatten.sort.each { |p| Psych.parse_file(p) }; Dir[".speculoos/**/*.json", "specs/**/*.json"].flatten.sort.each { |p| JSON.parse(File.read(p)) }; puts "metadata ok"'`
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-063-acceptance-classification-cli --validation "git diff --check" --json`

# Documentation Impact

- updates the root `README.md` with the preview-stage command surface;
- refreshes the pipeline contract docs for acceptance, classification,
  writeback preview, retrieval, and release-candidate preflight behavior.

# Publication Boundary

This is an offline implementation/test/docs slice that stays inside the
approved preview boundary.

No live Zotero read/write, PDF download/recovery, OCR call, Mistral call,
PageIndex call, OpenKB write, live index write, model call, worker-agent
execution, credential change, main-branch merge, release tag, package
publication, stable-branch promotion, or approval bypass is included.
