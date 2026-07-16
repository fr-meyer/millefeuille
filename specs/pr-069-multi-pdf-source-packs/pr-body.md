## Summary

- preserve v0.1 `source.pdf` packs for one-PDF Zotero items
- add v0.2 same-item multi-PDF manifests with deterministic per-attachment refs
- compute one order-independent aggregate pack hash from verified source hashes
- reject source-byte or manifest drift and keep exact-evidence reruns idempotent
- let the artifact-root lane consume verified v0.2 aggregate hashes

## Changed Files

- `millefeuille/domain/source_packs.py`
- `millefeuille/domain/artifact_writer.py`
- `tests/test_millefeuille_source_pack_writer.py`
- `tests/test_millefeuille_artifact_writer.py`
- `specs/millefeuille-pipeline/source-pack-manifest.schema.json`
- `README.md`
- `specs/millefeuille-pipeline/artifact-storage.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-069-multi-pdf-source-packs.yaml`
- `specs/pr-069-multi-pdf-source-packs/pr-body.md`
- `specs/pr-069-multi-pdf-source-packs/commit-message.txt`

## Validation

- `.venv/bin/python -m unittest tests.test_millefeuille_source_pack_writer tests.test_millefeuille_artifact_writer tests.test_millefeuille_contract_artifacts -v`
- `.venv/bin/python -m unittest discover -v`
- `.venv/bin/ruff check .`
- metadata YAML and JSON parse check
- `git diff --check`
- `speculoos status --json`
- `speculoos validate --task pr-069-multi-pdf-source-packs --validation "git diff --check" --json`
- `speculoos publish-check --task pr-069-multi-pdf-source-packs --pr-body specs/pr-069-multi-pdf-source-packs/pr-body.md --commit-message specs/pr-069-multi-pdf-source-packs/commit-message.txt --json`

## Release Flow

Feature PRs target `dev`; release/promotion PRs target `main`. Stable promotion
remains a separate explicitly approved operation.

## Documentation Impact

`README.md`, `specs/millefeuille-pipeline/artifact-storage.md`, and
`specs/millefeuille-pipeline/cli-contract.md` now describe v0.1 one-PDF
compatibility and the v0.2 same-item multi-PDF layout and aggregate hash.

## Publication Boundary

Offline fixture-only multi-PDF source-pack schema and intake support under
explicit temporary roots. No live Zotero read or write, PDF recovery,
real-document source-pack write, OCR/model/provider call, worker-agent
execution, OpenKB/index write, credential or permission change, main-branch
merge, release, package publication, production deployment, or approval bypass.
