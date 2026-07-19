## Summary

- add deterministic no-call execution plans for page, section, and full-paper summary profiles
- expose requested model controls, authentication lane, fallback policy, live blockers, and future provenance requirements
- add a strict v0.1 plan schema, CLI contract, documentation, and regression coverage

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-084-model-execution-plan.yaml`
- `README.md`
- `millefeuille/cli/stages.py`
- `millefeuille/domain/model_execution.py`
- `millefeuille/domain/model_profiles.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/live-run-plan.md`
- `specs/millefeuille-pipeline/model-execution-plan.schema.json`
- `specs/millefeuille-pipeline/model-profile.schema.yaml`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/pr-084-model-execution-plan/commit-message.txt`
- `specs/pr-084-model-execution-plan/pr-body.md`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_model_execution.py`
- `tests/test_millefeuille_stage_cli.py`

## Validation

- focused model-plan, stage-CLI, and contract tests
- full unittest discovery
- Ruff
- YAML/JSON metadata parse
- changed-file private-data scan
- `git diff --check`
- Speculoos task validation and publication checks

## Documentation Impact

`README.md` and the pipeline contract now explain the no-call planning command,
authentication and fallback selection, live blockers, and future provenance
requirements. The model-profile schema and new execution-plan schema provide
the matching machine-readable boundaries.

## Release Flow

Feature PRs target `dev`; release/promotion PRs target `main` and remain
separately gated. This slice may proceed through exact-head review and merge to
`dev` under the configured branch policy. No stable-branch change, release tag,
package publication, or production deployment is included.

## Publication Boundary

Offline model execution planning, schema, tests, and documentation only. No
private paper content, live Zotero access, PDF recovery, OCR/model/provider
call, paid completion smoke, worker-agent execution, source-pack mutation,
OpenKB/index write, classification/writeback, credential lookup or permission
change, OpenClaw routing/configuration change, main-branch change, release,
package publication, production deployment, or approval bypass.

## Speculoos

Task: `pr-084-model-execution-plan`
