## Summary

Implements Speculoos task `pr-088-retire-remaining-coderabbit-workflows`.

- removes the remaining manual and comment-triggered CodeRabbit workflows
- removes the obsolete repository-level CodeRabbit configuration
- preserves historical PR80 task/evidence records and the Mergeguez review/merge lane
- does not add a replacement GitHub Actions reviewer workflow

## Why

CodeRabbit is deprecated for this stack. These workflows are product-specific remediation entrypoints, not generic CI, and must not be renamed into Mergeguez workflows. Mergeguez remains the exact-head review provider; Speculoos remains the policy and evidence layer.

## Scope

- delete `.coderabbit.yaml`
- delete `.github/workflows/coderabbit-pr-automation-manual.yml`
- delete `.github/workflows/coderabbit-pr-comment-trigger.yml`
- add bounded Speculoos task and publication metadata

Historical PR80 records remain intact as audit evidence of the earlier partial retirement.

## Changed Files

- `.coderabbit.yaml`
- `.github/workflows/coderabbit-pr-automation-manual.yml`
- `.github/workflows/coderabbit-pr-comment-trigger.yml`
- `.speculoos/tasks/pr-088-retire-remaining-coderabbit-workflows.yaml`
- `specs/pr-088-retire-remaining-coderabbit-workflows/commit-message.txt`
- `specs/pr-088-retire-remaining-coderabbit-workflows/pr-body.md`

## Validation

- YAML/JSON metadata parse
- full unittest discovery
- Ruff
- `git diff --check`
- explicit absence check for active CodeRabbit workflow/configuration files
- Speculoos validation and publication checks

## Documentation Impact

No user-facing documentation changes required because this is repository CI wiring retirement only. The repository-local task metadata and this PR body record the retirement rationale and preserve the Mergeguez/Speculoos review boundary.

## Release Flow

Feature PRs target `dev`; release/promotion PRs target `main` and remain separately gated. This CI retirement requires exact-head Mergeguez review and policy merge. No stable-branch change, release tag, package publication, or production deployment is included.

## Publication Boundary

Repository CI metadata only. No credential or permission change, application code change, private data access, Zotero access or write, PDF recovery, OCR, model/provider call, OpenKB/index write, deployment, release, main-branch change, package publication, or approval bypass.
