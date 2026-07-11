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
