## Summary

Implements Speculoos task `pr-080-ci-retire-broken-coderabbit`.

- remove the obsolete automatic `pull_request` CodeRabbit remediation workflow whose private shared-toolkit checkout fails before remediation starts
- preserve the manual and comment-triggered CodeRabbit workflows unchanged
- keep exact-head Mergeguez review and Speculoos policy merge as the required review/publication lane

## Why

PR79 is implementation-complete and exact-head Mergeguez-approved, but policy merge correctly remains blocked by a red legacy CodeRabbit check. The failing run checked out the consumer repository successfully, then failed while checking out the private shared toolkit; every remediation step was skipped. The automatic caller is not configured with the documented cross-repository credential and is no longer the required review path.

This repair removes only that automatic trigger. It does not add, inspect, or change credentials, permissions, branch protection, rulesets, or review policy. Operators can still invoke the preserved manual and comment-triggered CodeRabbit workflows when their private configuration is available.

## Branch Policy Evidence

Live read-only GitHub policy checks on 2026-07-18 established that this check is not required by repository protection:

- `GET /repos/fr-meyer/millefeuille/rules/branches/dev` returned `[]` (no effective rules on `dev`)
- `GET /repos/fr-meyer/millefeuille/rulesets` returned `[]` (no repository rulesets)
- the approved Mergeguez broker exposes no branch-protection target/profile for `millefeuille`

The sanitized live-policy snapshot and replacement-gate configuration are committed in `specs/pr-080-ci-retire-broken-coderabbit/policy-evidence.json`. That evidence is pinned to the SHA-256 of `.speculoos/actors.json` and records the `feature_to_dev` requirements: exact PR/base/head/SHA identity, native Mergeguez review, ready live checks, zero blocking findings, and verified `mergeguez[bot]` merge attribution.

Speculoos re-reads those gates from the live PR before merge; removing the broken CodeRabbit caller cannot bypass them.

## Scope

- delete `.github/workflows/coderabbit-pr-automation-pr.yml`
- add bounded Speculoos task and publication metadata
- do not modify application code or the remaining CodeRabbit workflows

## Changed Files

- `.github/workflows/coderabbit-pr-automation-pr.yml`
- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/tasks/pr-080-ci-retire-broken-coderabbit.yaml`
- `specs/pr-080-ci-retire-broken-coderabbit/commit-message.txt`
- `specs/pr-080-ci-retire-broken-coderabbit/policy-evidence.json`
- `specs/pr-080-ci-retire-broken-coderabbit/pr-body.md`

## Validation

- workflow and Speculoos YAML/JSON parse
- `git diff --check`
- byte-identical diff check for the manual and comment-triggered CodeRabbit workflows
- committed policy-evidence JSON parse and actor-policy digest check
- Speculoos status, validation, private-data scan, documentation sync, and publication checks

## Documentation Impact

No user-facing documentation changes required because this repair changes repository CI wiring only; the operator rationale is recorded in this PR and its Speculoos task.

## Release Flow

Feature PRs target `dev`; release/promotion PRs target `main` and remain separately gated. This CI repair requires exact-head Mergeguez review and policy merge. No stable-branch change, release tag, package publication, or production deployment is included.

## Publication Boundary

Repository CI metadata only: remove one broken automatic `pull_request` workflow while preserving manual and comment-triggered tooling. No credential or permission change, application code change, private data access, Zotero access or write, PDF recovery, OCR/model/provider call, OpenKB/index write, production deployment, main-branch change, release, package publication, or approval bypass.
