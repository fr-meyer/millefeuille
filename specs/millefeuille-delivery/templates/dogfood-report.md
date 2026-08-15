# Dogfood Report: {{dogfood_name}}

## Packet Metadata

- packet id: `{{packet_id}}`
- repository or surface: `{{repository}}`
- owner: `{{owner}}`
- run at: `{{run_at}}`
- run mode: `{{run_mode}}`
- source head or version: `{{source_head_or_version}}`
- approval packet: {{approval_packet_ref}}

## Goal

{{goal}}

## Authority And Mode

The default Carte Blanche mode is no-write evidence for planning surfaces only.
It is not release, promotion, runtime-route, credential, GitHub ruleset, or
write authorization evidence.

- selected mode: `{{run_mode}}` (`no-write` or `approved-live`)
- writes performed: {{writes_performed}}
- commands permitted: {{command_authority}}
- live authority: {{approval_packet_ref}}

When the mode is `approved-live`, the referenced approval packet must predate
the run and bound every credential, private input, provider call, and write.
This report cannot create or widen authority retroactively.

## Bounded Scenario

- input selector: {{bounded_input}}
- maximum scope: {{maximum_scope}}
- environment: {{environment}}
- starting state: {{starting_state}}
- expected safe outcome: {{expected_outcome}}

## Commands Or Actions Exercised

- {{action}}

Record sanitized command shapes or named actions. Do not record credential
values, authenticated URLs, or private payloads.

## Readback

- {{readback}}

Distinguish observed state from inference. State explicitly when an action was
planned, previewed, blocked, skipped, or executed.

## Safety Invariants

- selector remained within the bounded input: {{selector_invariant}}
- repository or provider writes matched authority: {{write_invariant}}
- private payloads stayed outside committed artifacts: {{privacy_invariant}}
- stop conditions remained clear: {{stop_condition_invariant}}

## Evidence

- public-safe evidence: {{evidence_ref}}
- private evidence location: {{private_evidence_ref_or_not_applicable}}
- exact counts and verdicts: {{aggregate_result}}
- validation evidence: {{validation_ref}}

## Friction

- {{friction}}

## Product Gaps

- {{product_gap_or_none}}

## Reconciliation

{{reconciliation}}

Record superseded branches, stale plans, changed state, or later completion so
future work does not restart from obsolete dogfood evidence.

## Carry-Forward Constraints

- {{carry_forward_constraint}}
- retain every unexecuted manual gate
- do not treat this report as approval for a later live operation

## Outcome

- verdict: `{{outcome}}`
- next bounded step: {{next_step}}
- owner: {{next_owner}}
- blocker or approval needed: {{next_gate_or_none}}

## Publication Boundary

Publish only sanitized planning readback or approved public-safe aggregates.
No credentials, authenticated URLs, private item identifiers, raw PDF bytes,
private paper text, provider payloads, private paths, or unapproved write
evidence belong in this report.
