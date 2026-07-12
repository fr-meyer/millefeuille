## Summary

- Hard renames the Python distribution, import package, module entry point, and installed console script to `millefeuille`.
- Removes the old package and CLI identity instead of keeping fallback aliases or compatibility shims.
- Migrates the default Zotero lifecycle tags and OpenKB handoff/acceptance schema labels to the Millefeuille namespace.
- Updates Speculoos metadata, docs, fixtures, and tests to the target `fr-meyer/millefeuille` identity.
- Adds a regression guard that fails if retired package/CLI/import identity strings return in tracked source.

## Publication Boundary

This is a code and metadata migration PR for the dev lane. It performs no live Zotero writeback, PDF recovery, OCR/Mistral/PageIndex provider call, OpenKB write, source-pack write, credential change, remote GitHub repository rename, dev-to-main promotion, release tag, or package publication.

Feature PRs target `dev`; release/promotion PRs target `main`.

## Validation

- `.venv/bin/python -m unittest discover -v` — 147 tests passed
- `.venv/bin/ruff check .` — passed
- `.venv/bin/python -m millefeuille --help` — passed, wrote 126 help lines
- `ruby -ryaml -rjson ...` metadata parse — passed
- `git diff --check` — passed
- `speculoos status --json` — passed
- `speculoos actors --json` — passed
- `speculoos validate --task pr-043-millefeuille-hard-rename --validation "git diff --check" --json` — passed
- Old package/CLI/import identity scan — passed

## Changed Files

- `.speculoos/README.md`
- `.speculoos/goal.md`
- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-036-speculoos-millefeuille-bootstrap.yaml`
- `.speculoos/tasks/pr-037-pr36-closeout-metadata.yaml`
- `.speculoos/tasks/pr-038-openkb-source-version-drift.yaml`
- `.speculoos/tasks/pr-039-offline-acceptance-summary.yaml`
- `.speculoos/tasks/pr-041-live-golden-route-evidence.yaml`
- `.speculoos/tasks/pr-043-millefeuille-hard-rename.yaml`
- `MANIFEST.in`
- `README.md`
- `environment.yml`
- `millefeuille/__init__.py`
- `millefeuille/__main__.py`
- `millefeuille/cli/__init__.py`
- `millefeuille/cli/commands.py`
- `millefeuille/cli/main.py`
- `millefeuille/clients/__init__.py`
- `millefeuille/clients/exceptions.py`
- `millefeuille/clients/mistral_client.py`
- `millefeuille/clients/ocr_client.py`
- `millefeuille/clients/pageindex_client.py`
- `millefeuille/clients/pageindex_tree_client.py`
- `millefeuille/clients/temp_file_utils.py`
- `millefeuille/clients/tree_client.py`
- `millefeuille/clients/zotero_client.py`
- `millefeuille/conf/__init__.py`
- `millefeuille/conf/config.yaml`
- `millefeuille/conf/credentials/__init__.py`
- `millefeuille/conf/credentials/default.yaml`
- `millefeuille/conf/download/__init__.py`
- `millefeuille/conf/download/default.yaml`
- `millefeuille/conf/export/__init__.py`
- `millefeuille/conf/export/default.yaml`
- `millefeuille/conf/ocr/__init__.py`
- `millefeuille/conf/ocr/default.yaml`
- `millefeuille/conf/ocr/mistral.yaml`
- `millefeuille/conf/ocr/pageindex.yaml`
- `millefeuille/conf/processing/__init__.py`
- `millefeuille/conf/processing/default.yaml`
- `millefeuille/conf/selection_tagging/__init__.py`
- `millefeuille/conf/selection_tagging/default.yaml`
- `millefeuille/conf/storage/__init__.py`
- `millefeuille/conf/storage/default.yaml`
- `millefeuille/conf/tag_adding/__init__.py`
- `millefeuille/conf/tag_adding/default.yaml`
- `millefeuille/conf/tagging/__init__.py`
- `millefeuille/conf/tagging/default.yaml`
- `millefeuille/conf/tree_structure/__init__.py`
- `millefeuille/conf/tree_structure/default.yaml`
- `millefeuille/conf/zotero/__init__.py`
- `millefeuille/conf/zotero/default.yaml`
- `millefeuille/domain/__init__.py`
- `millefeuille/domain/config.py`
- `millefeuille/domain/markdown_converter.py`
- `millefeuille/domain/millefeuille.py`
- `millefeuille/domain/models.py`
- `millefeuille/domain/note_formatter.py`
- `millefeuille/domain/note_validator.py`
- `millefeuille/domain/table_converter.py`
- `millefeuille/domain/tree_processor.py`
- `millefeuille/orchestration/__init__.py`
- `millefeuille/orchestration/pipeline.py`
- `millefeuille/orchestration/processor.py`
- `millefeuille/utils/__init__.py`
- `millefeuille/utils/export.py`
- `millefeuille/utils/logging.py`
- `millefeuille/utils/progress.py`
- `millefeuille/utils/redaction.py`
- `millefeuille/utils/retry.py`
- `pyproject.toml`
- `pyrightconfig.json`
- `requirements.txt`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/millefeuille-pipeline/stage-manifest.schema.json`
- `specs/millefeuille-pipeline/tag-state-machine.md`
- `specs/pr-036-speculoos-millefeuille-bootstrap/spec.md`
- `specs/pr-039-offline-acceptance-summary/pr-body.md`
- `specs/pr-043-millefeuille-hard-rename/commit-message.txt`
- `specs/pr-043-millefeuille-hard-rename/pr-body.md`
- `tests/fixtures/millefeuille_live_golden_run/golden-run.json`
- `tests/fixtures/openkb_handoff_millefeuille_test/README.md`
- `tests/fixtures/openkb_handoff_millefeuille_test/duplicate_scans.jsonl`
- `tests/fixtures/openkb_handoff_millefeuille_test/handoff.live.jsonl`
- `tests/fixtures/openkb_handoff_millefeuille_test/handoff.preview.jsonl`
- `tests/fixtures/openkb_handoff_millefeuille_test/openkb_outcomes.jsonl`
- `tests/fixtures/openkb_handoff_multi_attachment/handoff.live.jsonl`
- `tests/fixtures/openkb_handoff_multi_attachment/openkb_outcomes.jsonl`
- `tests/fixtures/openkb_source_version_drift/source-version-drift.json`
- `tests/test_millefeuille_contract_models.py`
- `tests/test_millefeuille_hard_rename.py`
- `tests/test_millefeuille_live_golden_run_fixture.py`
- `tests/test_openkb_handoff_identity.py`
- `tests/test_openkb_handoff_multi_attachment_fixture.py`
- `tests/test_openkb_handoff_pipeline.py`
- `tests/test_openkb_handoff_security.py`
- `tests/test_openkb_millefeuille_test_fixture.py`
- `tests/test_openkb_source_version_drift.py`
- `tests/test_outcome_tag_removal.py`
- `tests/test_selection_tagging.py`
- `tests/test_zotero_client_pagination.py`
