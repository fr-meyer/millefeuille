## Summary

Add a strict offline model-provenance materializer for post-execution evidence.

- adds v0.1 execution-evidence and provenance-record schemas
- adds `millefeuille models --provenance --plan-file ... --execution-evidence ...`
- validates execution evidence against the no-call plan before materializing provenance
- validates the complete strict plan shape, including every no-call control
- requires the plan to equal the canonical bundled profile before trusting its values
- creates optional output through exclusive, descriptor-relative no-follow writes
- rejects output beneath verified run-package markers
- rejects unknown fields, identity/control drift, invalid fallback resolution, invalid usage totals, unsafe or duplicate refs, malformed warnings, and prompt/provider/private/secret payload markers
- documents that this command only writes explicit JSON and does not mutate run packages or call providers

## Changed Files

- `.speculoos/croquis/pr-086-model-provenance-record.yaml`
- `.speculoos/moulinettes/pr-086-model-provenance-record.yaml`
- `.speculoos/tasks/pr-086-model-provenance-record.yaml`
- `README.md`
- `millefeuille/cli/stages.py`
- `millefeuille/domain/model_execution.py`
- `millefeuille/domain/secure_io.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/model-execution-evidence.schema.json`
- `specs/millefeuille-pipeline/model-provenance-record.schema.json`
- `specs/millefeuille-pipeline/remaining-work.md`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_model_execution.py`
- `specs/pr-086-model-provenance-record/commit-message.txt`
- `specs/pr-086-model-provenance-record/pr-body.md`

## Validation

- focused model-plan, stage-CLI, and contract tests
- full unittest discovery
- Ruff
- YAML/JSON metadata parse
- `git diff --check`
- changed-file private-data scan
- Speculoos task validation and publication checks

## Documentation Impact

`README.md`, `specs/millefeuille-pipeline/README.md`,
`specs/millefeuille-pipeline/cli-contract.md`, and
`specs/millefeuille-pipeline/remaining-work.md` now describe the strict
provenance materializer, the v0.1 evidence/provenance schemas, and the boundary
that provenance append/run-package mutation remains later work.

## Release Flow

Feature PRs target `dev`; release/promotion PRs target `main`. Promotion,
release tags, package publication, and production deployment remain separate
explicit gates.

## Publication Boundary

Offline model provenance materialization, schema, documentation, and tests
only. No credential lookup, provider/model/OCR call, private paper read, live
Zotero/OpenKB/index/source-pack mutation, run-package provenance append,
main-branch merge, release, package publication, production deployment, or
approval bypass.
