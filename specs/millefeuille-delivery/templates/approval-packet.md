# Approval Packet: {{requested_operation_name}}

## Packet Metadata

- packet id: `{{packet_id}}`
- requester: `{{requester}}`
- approver: `{{approver}}`
- created at: `{{created_at}}`
- expires at: `{{expires_at}}`
- related task or run: `{{task_or_run_id}}`
- decision state: `{{decision}}`

## Requested Operation

{{requested_operation}}

This packet is a request until the Approval Decision is complete. Planning,
preview, and prior approval are not approval for this operation.

## Exact Boundaries

- exact staging tag: `{{staging_tag}}`
- maximum Zotero item count: `{{maximum_item_count}}`
- item or corpus selector: {{bounded_selector}}
- time window: {{time_window}}
- output root: `{{output_root}}`
- source-pack root: `{{source_pack_root}}`
- OpenKB target: `{{openkb_target}}`
- environment: {{environment}}

## Preconditions

- [ ] bounded preview reviewed: {{preview_evidence_ref}}
- [ ] current source identity verified: {{source_identity_evidence_ref}}
- [ ] required offline validation passed: {{offline_validation_ref}}
- [ ] rollback owner is available: {{rollback_owner}}
- [ ] private evidence location is approved: {{private_evidence_location}}

## Allowed Operations

- {{allowed_operation}}

Anything not listed here is denied. Approval for a read does not imply approval
for recovery, a provider call, a write, or writeback.

## Denied Operations

- {{denied_operation}}
- widening the selector or item limit without a new decision
- storing credentials, authenticated URLs, private paper text, raw PDF bytes,
  or provider payloads in committed artifacts
- stable promotion, tag creation, or package publication

## Credential And Secret Handling

- credential class: {{credential_class}}
- credential source: {{credential_source}}
- permitted process or adapter: {{credential_consumer}}
- logs and error redaction: {{redaction_policy}}
- credential disposal or revocation: {{credential_disposal}}

Record credential names or opaque references only. Never paste credential
values into this packet.

## Output And Disposal Policy

- durable public-safe outputs: {{public_output}}
- private transient outputs: {{private_output}}
- disposal policy: {{disposal_policy}}
- disposal verification: {{disposal_evidence}}
- retention owner and deadline: {{retention_policy}}

## Budget And Limits

- OCR backend: `{{ocr_backend}}`
- model or provider route: `{{provider_route}}`
- provider budget: `{{provider_budget}}`
- retry limit: `{{retry_limit}}`
- wall-clock or request limit: `{{execution_limit}}`

## Stop Conditions

- {{stop_condition}}
- identity or hash drift
- selector expansion beyond the approved maximum
- unexpected write, payload retention, or redaction failure
- budget or retry limit reached

## Rollback Plan

- rollback action: {{rollback_action}}
- rollback checkpoint: {{rollback_checkpoint}}
- partial-write reconciliation: {{partial_write_reconciliation}}
- rollback evidence: {{rollback_evidence_ref}}

## Evidence To Capture

- start and end timestamps
- exact bounded selector and counts, without private item content
- executed operation names and sanitized result counts
- source identity and integrity verdicts
- output and disposal evidence refs
- stop, retry, partial-write, and rollback outcomes
- {{additional_evidence}}

## Approval Decision

- decision: `{{decision}}`
- decided by: `{{approver}}`
- decided at: `{{decided_at}}`
- exact approved operations: {{approved_operations}}
- amendments: {{approval_amendments_or_none}}
- signature or decision evidence: {{decision_evidence_ref}}

Each later live lane, Zotero writeback, stable promotion, release tag, and
package publication requires a separate decision.

## Publication Boundary

Only public-safe aggregate evidence may be committed. Credentials,
authenticated URLs, private Zotero identifiers, raw PDF bytes, private paper
text, provider payloads, and private paths stay in the approved private
evidence location.
