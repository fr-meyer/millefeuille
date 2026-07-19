# Live Run Plan

This is a plan artifact only. It does not grant permission to run live Zotero,
recover PDFs, call OCR providers, write OpenKB, or write source packs.

## Bounded Dogfood Sequence

1. Confirm staging tag and target item count.
2. Confirm allowed credentials and write boundaries.
3. Run `preview` discovery with read-only Zotero access.
4. Inspect skip evidence and handoff preview rows.
5. If approved, run authoritative handoff export.
6. If approved, recover PDF bytes and compute SHA-256.
7. Verify recovered bytes against handoff rows.
8. If approved, create source packs.
9. If approved, run native extraction.
10. If approved, run OCR extraction.
11. Select route and write OpenKB.
12. Run duplicate scan and acceptance summary.
13. Only then begin classification.

## Required Approval Packet

- exact staging tag;
- maximum Zotero item count;
- allowed live operations;
- output directories/source-pack root;
- OCR backend and budget if OCR is allowed;
- OpenKB target;
- disposal policy for PDFs and provider payloads;
- stop condition and rollback notes.

## Default Refusal Conditions

- no exact staging tag;
- ambiguous item count;
- missing disposal policy;
- request to classify before acceptance evidence;
- request to commit raw PDFs, authenticated URLs, provider payloads, or secrets;
- request to write Zotero/OpenKB/source packs without explicit approval.

## Post-PR79 Summary Model Profile

This model-profile change is a separate post-PR79 slice. It must not be folded
into PR79 or treated as permission for a live provider call.

- Page, section, and full-paper summaries select
  `openai/gpt-5.6-sol` with `reasoning_effort: xhigh`.
- The intended authentication lane is OpenClaw-native Codex OAuth. LiteLLM
  credentials are separate and must not be silently substituted.
- The profile records `fast_mode: off`, so standard processing is explicit.
  A live adapter must not inherit an agent-level fast default. Fast/priority
  processing requires an explicit run-level choice and must be recorded in
  provenance.
- There is no silent cross-provider or cross-model fallback. Any approved
  fallback must record the actual selected model and reasoning level.
- The `offline-preview` profile remains fixture-only and makes no provider
  calls.
- Historical golden-run artifacts keep the model identity that actually
  produced them; they are not rewritten to match the new profile.
- `millefeuille models --plan --profile research-default --stage <summary-stage>`
  may be used offline to inspect the requested model, authentication lane,
  fallback policy, execution blockers, and future provenance requirements. It
  performs no credential lookup or provider call and does not authorize one.
