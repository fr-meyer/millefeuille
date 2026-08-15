# Feature Packet: {{title}}

## Packet Metadata

- packet id: `{{packet_id}}`
- Speculoos task: `{{task_id}}`
- owner: `{{owner}}`
- created at: `{{created_at}}`
- status: `{{status}}`
- base branch: `{{base_branch}}`
- head branch: `{{head_branch}}`
- head commit: `{{head_commit}}`

## Objective

{{objective}}

## Dependencies

- predecessor or contract: {{dependency}}
- input evidence: {{input_evidence_ref}}
- blocker: {{blocker_or_none}}

## Scope

- {{in_scope_change}}

## Non-Goals

- {{non_goal}}

## Acceptance Criteria

- [ ] {{acceptance_criterion}}

## Changed Files

- `{{changed_file}}` — {{change_reason}}

## Validation

- command: `{{validation_command}}`
- result: {{validation_result}}
- environment: {{validation_environment}}

## Evidence

- artifact or fixture: {{evidence_ref}}
- privacy scan: {{privacy_scan_result}}
- unresolved finding: {{finding_or_none}}

## Review And Delivery State

- pull request: {{pull_request_ref}}
- exact reviewed head: `{{reviewed_head}}`
- required review: {{review_evidence_ref}}
- required checks: {{check_evidence_ref}}
- merge commit: `{{merge_commit_or_pending}}`
- branch cleanup: {{branch_cleanup_state}}

Do not claim review or merge readiness when the recorded head differs from the
reviewed head.

## Release Flow

Feature PRs target `dev`; release/promotion PRs target `main`. Promotion, tag,
and package publication remain separate decisions even after this feature is
merged.

## Publication Boundary

This packet authorizes only the repository change described above. It does not
authorize credentials, private-data access, PDF recovery, Zotero writes,
OCR/model/provider calls, source-pack or OpenKB/index writes, stable promotion,
release tagging, package publication, or any other live operation unless a
separate approval packet says so.
