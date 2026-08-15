# Maintenance Handoff: {{title}}

## Packet Metadata

- packet id: `{{packet_id}}`
- outgoing owner: `{{outgoing_owner}}`
- next owner: `{{next_owner}}`
- created at: `{{created_at}}`
- status: `{{status}}`
- current branch: `{{current_branch}}`
- current head: `{{current_head}}`
- related issue, task, or incident: {{tracking_ref}}

## Handoff Summary

{{handoff_summary}}

## Current State

{{current_state}}

- last known good state: {{last_known_good_state}}
- active blocker: {{blocker_or_none}}
- dirty or unpublished work: {{unpublished_work_or_none}}
- latest operator action: {{latest_action}}

## Trigger And Impact

- trigger: {{trigger}}
- affected users or workflows: {{impact}}
- severity or urgency: {{priority}}
- first observed: {{first_observed_at}}

## Owned Surfaces

- repository paths: {{repository_paths}}
- runtime or provider surface: {{runtime_surface_or_none}}
- control-plane records: {{control_plane_refs}}
- private evidence location: {{private_evidence_ref_or_not_applicable}}

## Safety Invariants

- {{safety_invariant}}
- do not widen live reads, writes, credentials, or provider spend without an
  approval packet
- keep private inputs and payloads out of committed evidence

## Operator Procedure

1. {{operator_step}}

For every mutating step, name the expected result, stop condition, and
idempotency or partial-failure behavior.

## Validation

- command or check: `{{validation_command}}`
- expected result: {{expected_validation_result}}
- last observed result: {{observed_validation_result}}
- environment: {{validation_environment}}

## Evidence

- committed evidence: {{evidence_ref}}
- exact reviewed or deployed head: `{{evidence_head}}`
- logs or private evidence: {{private_log_ref_or_not_applicable}}
- unresolved evidence gap: {{evidence_gap_or_none}}

## Rollback And Recovery

- rollback trigger: {{rollback_trigger}}
- rollback action: {{rollback_action}}
- recovery validation: {{recovery_validation}}
- irreversible residue: {{irreversible_residue_or_none}}

## Open Risks

- unresolved risk: {{unresolved_risk}}
- owner: {{risk_owner}}
- mitigation or deadline: {{risk_mitigation}}

## Restart Instructions

1. Verify the current branch and current head still match this packet.
2. Re-read the linked task, evidence, and latest validation result.
3. {{restart_step}}
4. Stop and reconcile this packet if external state or evidence has drifted.

## Next Owner

- owner: `{{next_owner}}`
- first action: {{next_owner_first_action}}
- success signal: {{handoff_success_signal}}
- escalation path: {{escalation_path}}

## Publication Boundary

This handoff transfers knowledge, not authority. Existing live-operation,
credential, write, promotion, tag, release, and package gates remain in force.
Commit only sanitized state and opaque refs to private evidence.
