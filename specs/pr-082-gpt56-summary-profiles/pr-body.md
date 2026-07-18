## Summary

Implements Speculoos task `pr-082-gpt56-summary-profiles`: reconstruct the
transcript-backed post-PR79 model-profile slice on current `dev` and select the
exact GPT-5.6 Sol route for hierarchical summaries.

- page, section, and full-paper summaries select `openai/gpt-5.6-sol`
- all three record `reasoning_effort: xhigh` and `fast_mode: off`
- the profile schema accepts explicit reasoning and processing-tier metadata
- the live-run contract records the OAuth, no-silent-fallback,
  standard-processing, and historical-provenance boundaries
- paper-card, classification, and fixture-only offline profiles remain unchanged

## Recovery Provenance

The earlier uncommitted worktree was damaged and is not used as a source. This
PR replays the exact intended source/schema/test/documentation edits preserved
in the indexed OpenClaw session transcript onto a fresh workspace-safe worktree
from synchronized `origin/dev`. It does not apply the damaged worktree patch or
modify the damaged worktree/backups.

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-082-gpt56-summary-profiles.yaml`
- `README.md`
- `millefeuille/domain/model_profiles.py`
- `specs/millefeuille-pipeline/live-run-plan.md`
- `specs/millefeuille-pipeline/model-profile.schema.yaml`
- `specs/pr-082-gpt56-summary-profiles/commit-message.txt`
- `specs/pr-082-gpt56-summary-profiles/pr-body.md`
- `tests/test_millefeuille_stage_cli.py`

## Validation

- focused stage-CLI suite
- full unittest discovery
- Ruff
- YAML/JSON metadata parse
- `git diff --check`
- changed-file private-data scan
- Speculoos task validation and publication checks
- independent local code review

## Documentation Impact

User-facing `README.md` and
`specs/millefeuille-pipeline/live-run-plan.md` now record the selected summary
model, xhigh reasoning, explicit standard processing, OAuth lane,
no-silent-fallback contract, fixture-only boundary, and preservation of
historical provenance. `specs/millefeuille-pipeline/model-profile.schema.yaml`
adds the matching machine-readable fields.

## Release Flow

Feature PRs target `dev`; release/promotion PRs target `main` and remain
separately gated. This PR stops before merge. No stable-branch change, release
tag, package publication, or production deployment is part of this slice.

## Publication Boundary

Offline model-profile metadata, schema, tests, and documentation only. GitHub
publication is limited to a user-authored feature PR targeting `dev` plus
exact-head Mergeguez review. No private paper content, live Zotero access, PDF
recovery, OCR/model/provider call, paid completion smoke, worker-agent
execution, source-pack mutation, OpenKB/index write, classification/writeback,
credential or permission change, OpenClaw routing/configuration change,
main-branch change, release, package publication, production deployment,
cleanup, or approval bypass.
