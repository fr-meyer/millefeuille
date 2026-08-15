# Migration Packet: {{migration_name}}

## Packet Metadata

- packet id: `{{packet_id}}`
- migration: `{{migration_name}}`
- owner: `{{owner}}`
- created at: `{{created_at}}`
- status: `{{status}}`
- source release or head: `{{source_release_or_head}}`
- target release or head: `{{target_release_or_head}}`

## Objective

{{migration_objective}}

## Current And Target States

- source contract: `{{source_contract}}`
- current operator or consumer path: {{current_state}}
- target contract: `{{target_contract}}`
- target operator or consumer path: {{target_state}}
- compatibility window: {{compatibility_window}}

## Compatibility Contract

- backward-compatible behavior: {{backward_compatibility}}
- intentionally changed behavior: {{intentional_change}}
- unsupported source state: {{unsupported_state}}
- mixed-version behavior: {{mixed_version_behavior}}
- downgrade behavior: {{downgrade_behavior}}

## Inventory And Mapping

| Old surface | New surface | Transformation | Owner |
| --- | --- | --- | --- |
| `{{old_surface}}` | `{{new_surface}}` | {{mapping_rule}} | {{mapping_owner}} |

Inventory CLI commands, schemas, stored artifacts, tags, configuration,
documentation, automation, adapters, and downstream consumers as applicable.

## Migration Phases

1. {{migration_phase}}

Each phase must define entry criteria, idempotency, completion evidence, and a
rollback checkpoint before destructive cleanup.

## Validation

- command or check: `{{validation_command}}`
- source-state fixture: {{source_fixture_ref}}
- target-state fixture: {{target_fixture_ref}}
- mixed-state result: {{mixed_state_result}}
- platform or consumer matrix: {{validation_matrix}}

## Evidence

- inventory evidence: {{inventory_evidence_ref}}
- migrated-state evidence: {{migration_evidence_ref}}
- compatibility evidence: {{compatibility_evidence_ref}}
- unresolved exception: {{exception_or_none}}

## Rollback And Recovery

- rollback checkpoint: {{rollback_checkpoint}}
- rollback action: {{rollback_action}}
- data or artifact recovery: {{recovery_method}}
- rollback validation: {{rollback_validation}}
- irreversible action and approval: {{irreversible_action_or_none}}

## Deprecation And Removal

- deprecation notice: {{deprecation_notice}}
- compatibility end date or version: {{compatibility_end}}
- removal gate: {{removal_gate}}
- stale-state detection: {{stale_state_check}}
- final cleanup evidence: {{cleanup_evidence_ref}}

Do not remove the old path until the removal gate is satisfied and rollback is
no longer required by the approved migration policy.

## Communication

- affected operators or consumers: {{audience}}
- documentation update: {{documentation_ref}}
- announcement or handoff: {{communication_ref}}
- support owner: {{support_owner}}

## Decision

- decision: `{{decision}}`
- decided by: {{decision_owner}}
- decided at: {{decision_time}}
- next checkpoint: {{next_checkpoint}}

## Publication Boundary

This packet does not authorize live data migration, credential use, destructive
cleanup, provider calls, stable promotion, release tagging, or package
publication. Capture those decisions separately and commit only public-safe
aggregate evidence.
