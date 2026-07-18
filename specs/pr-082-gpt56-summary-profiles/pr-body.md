## Summary

Closes the repo-local Speculoos delivery metadata for merged PR #82, which
selected the exact GPT-5.6 Sol route for hierarchical summaries.

- records exact reviewed head, zero-finding Mergeguez evidence, and `dev` merge
- records approved remote feature-branch cleanup
- marks task `pr-082-gpt56-summary-profiles` merged and Project status Done
- returns canonical Speculoos and local Vibe mirror state to idle
- records that the delivered profile is merged on `dev`, while promotion to
  `main` and live provider execution remain separately gated

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
- `specs/pr-082-gpt56-summary-profiles/pr-body.md`

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
separately gated. PR #82 received exact-head Mergeguez approval and was
squash-merged into `dev`; its remote feature branch was deleted. No
stable-branch change, release tag, package publication, or production deployment
is part of this slice.

## Delivery Closeout

- exact reviewed head: `5f01b378d4c8c84b14bb3e41ada2cb6e9fd59d72`
- zero-finding Mergeguez review: https://github.com/fr-meyer/millefeuille/pull/82#pullrequestreview-4728585238
- successful Mergeguez check: https://github.com/fr-meyer/millefeuille/runs/88084819154
- `dev` squash-merge commit: `ba7ce07f48732a981be5de5751fa7e34380c84d6`
- remote feature branch deleted through the approved post-merge cleanup lane

## Publication Boundary

Offline model-profile metadata, schema, tests, and documentation only. GitHub
publication was limited to the completed PR #82 review, `dev` merge, remote
feature-branch cleanup, and repo-local Speculoos closeout. No private paper
content, live Zotero access, PDF recovery, OCR/model/provider call, paid
completion smoke, worker-agent execution, source-pack mutation, OpenKB/index
write, classification/writeback, credential or permission change, OpenClaw
routing/configuration change, main-branch change, release, package publication,
production deployment, legacy branch/worktree cleanup, or approval bypass.
