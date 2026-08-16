# Release Packet: {{version}}

## Packet Metadata

- packet id: `{{packet_id}}`
- version: `{{version}}`
- owner: `{{owner}}`
- created at: `{{created_at}}`
- status: `{{status}}`
- `dev` head: `{{dev_head}}`
- `main` base: `{{main_base}}`

## Release Scope

{{release_objective}}

- release type: {{release_type}}
- intended audience: {{release_audience}}
- excluded work: {{excluded_work}}

## Included Changes

- {{included_change}}

Record the merged PR, merge commit, user-visible impact, and evidence packet for
every included change.

## Compatibility And Migration

- schema and CLI compatibility: {{compatibility_summary}}
- migration packet: {{migration_packet_ref_or_not_applicable}}
- deprecations: {{deprecation_summary}}
- downgrade or rollback constraint: {{downgrade_constraint}}

## Validation Matrix

| Lane | Environment | Result | Evidence |
| --- | --- | --- | --- |
| {{validation_lane}} | {{validation_environment}} | {{validation_result}} | {{validation_evidence_ref}} |

## Approved Live Evidence

- approval packet: {{live_approval_ref_or_not_applicable}}
- public-safe live evidence: {{live_evidence_ref}}
- remaining live gate: {{remaining_live_gate_or_none}}

Absence of live evidence must be explicit. A preview, fixture, or no-write
dogfood report must not be represented as an approved live run.

## Known Limitations

- {{known_limitation}}
- unresolved blocker: {{release_blocker_or_none}}
- operator workaround: {{workaround_or_none}}

## Promotion

- `dev` to `main` promotion PR: {{promotion_pr}}
- exact promotion head: `{{release_commit}}`
- exact review evidence: {{promotion_review_ref}}
- checks: {{promotion_checks_ref}}
- merge decision: {{promotion_decision}}

Promotion requires its own approval. A clean feature PR or release-candidate
preflight is not permission to merge `dev` to `main`.

## Version And Changelog

- package version: `{{version}}`
- version source: {{version_file_ref}}
- changelog or release notes: {{release_notes_ref}}
- version-policy verdict: {{version_policy_verdict}}

## Tag

- proposed tag: `{{tag_name}}`
- tag target: `{{release_commit}}`
- signing or policy method: {{tag_method}}
- tag decision and evidence: {{tag_decision_ref}}

Tag creation requires separate approval after `main` matches the approved
promotion commit.

## Package Publication

- package target: `{{package_target}}`
- artifact digest or provenance: {{package_artifact_evidence}}
- publication smoke: {{package_smoke_plan}}
- publication decision and evidence: {{package_decision_ref}}

Package publication requires separate approval from promotion and tag creation.

## Rollback

- rollback trigger: {{rollback_trigger}}
- code rollback: {{code_rollback_plan}}
- package or tag response: {{package_rollback_plan}}
- owner and communication: {{rollback_owner_and_notice}}

## Approval Decision

- overall readiness: `{{decision}}`
- promotion decision: {{promotion_decision}}
- tag decision: {{tag_decision}}
- package publication decision: {{package_decision}}
- decided by and at: {{decision_actor_and_time}}

No decision in this section may silently stand in for another gate.

## Publication Boundary

The packet may contain public-safe release metadata, exact commits, sanitized
validation results, and opaque evidence refs. It must not contain credentials,
private paper content, raw PDF bytes, provider payloads, authenticated URLs, or
private paths. Runtime/provider execution and production operations remain
bounded by their own approval packets.
