# Adapter Evidence Packet: {{adapter_name}}

## Packet Metadata

- packet id: `{{packet_id}}`
- adapter: `{{adapter_name}}`
- adapter version: `{{adapter_version}}`
- owner: `{{owner}}`
- created at: `{{created_at}}`
- implementation head: `{{implementation_head}}`
- evidence state: `{{evidence_state}}`

## Objective

{{adapter_objective}}

## Adapter Boundary

- source system: {{source_system}}
- target system: {{target_system}}
- invocation mode: {{invocation_mode}}
- allowed reads: {{allowed_reads}}
- allowed writes: {{allowed_writes}}
- explicitly unsupported behavior: {{unsupported_behavior}}

## Source Contract

- contract and version: `{{source_contract}}`
- required fields: {{source_required_fields}}
- source trust assumptions: {{source_trust_assumptions}}
- source drift behavior: {{source_drift_behavior}}

## Target Contract

- contract and version: `{{target_contract}}`
- emitted fields or artifacts: {{target_artifacts}}
- idempotency rule: {{idempotency_rule}}
- target write boundary: {{target_write_boundary}}

## Capability Matrix

| Capability | Supported | Mode | Evidence |
| --- | --- | --- | --- |
| {{capability}} | {{supported}} | {{capability_mode}} | {{capability_evidence_ref}} |

## Identity And Integrity Mapping

| Source | Target | Rule | Failure behavior |
| --- | --- | --- | --- |
| `{{identity_field}}` | `{{mapped_identity_field}}` | {{identity_rule}} | fail closed |
| `{{integrity_field}}` | `{{mapped_integrity_field}}` | {{integrity_rule}} | fail closed |

Describe aggregation, normalization, collision handling, and source-version
drift explicitly. Never infer successful integrity verification from presence
of an identifier alone.

## Error And Retry Semantics

- permanent input error: {{permanent_error_behavior}}
- transient provider error: {{transient_error_behavior}}
- retry policy: {{retry_policy}}
- partial-write behavior: {{partial_write_behavior}}
- cancellation behavior: {{cancellation_behavior}}
- redacted error evidence: {{error_evidence_shape}}

## Fixtures

- fixture: `{{fixture_ref}}`
- scenario: {{fixture_scenario}}
- expected result: {{fixture_expected_result}}
- forbidden data assertion: {{fixture_privacy_assertion}}

## Validation

- command: `{{validation_command}}`
- result: {{validation_result}}
- platform or backend: {{validation_platform}}

## Evidence

- offline contract evidence: {{offline_evidence_ref}}
- approved live evidence: {{live_evidence_ref_or_not_applicable}}
- identity verdict: {{identity_verdict}}
- integrity verdict: {{integrity_verdict}}
- failure-path verdict: {{failure_verdict}}

## Compatibility

- compatible source versions: {{compatible_source_versions}}
- compatible target versions: {{compatible_target_versions}}
- migration requirement: {{migration_requirement}}
- deprecation trigger: {{deprecation_trigger}}

## Verdict

- verdict: `{{verdict}}`
- limitations: {{limitation}}
- required follow-up: {{follow_up_or_none}}

## Publication Boundary

Commit only contract shapes, sanitized status, counts, hashes, opaque evidence
refs, and public-safe fixtures. Credentials, authenticated URLs, private paper
content, raw PDF bytes, and provider payloads remain outside the repository.
This evidence does not authorize a provider call or target-system write.
