# Tag State Machine

Tags should represent stage state, not final classification truth. They are
secondary to handoff/source-pack/OpenKB evidence.

## Suggested Tags

- `docai`
  - Operator-selected candidate.

- `docai-previewed`
  - Discovery or handoff preview was generated.

- `docai-handoff-exported`
  - Authoritative handoff row exists.

- `docai-source-verified`
  - Recovered attachment bytes matched the handoff SHA-256.

- `docai-source-packed`
  - Source pack was created or updated.

- `docai-extracted-native`
  - Native extraction evidence exists.

- `docai-extracted-ocr`
  - OCR extraction evidence exists.

- `docai-openkb-added`
  - Selected Markdown was added to OpenKB.

- `docai-acceptance-passed`
  - Acceptance summary passed with no unmatched rows or duplicate review rows.

- `docai-ready-for-classification`
  - Classification may begin.

- `docai-classified`
  - Evidence-backed classification decision is complete.

- `docai-needs-review`
  - The item needs manual review.

- `docai-error`
  - The item failed a stage.

## Allowed Transitions

1. `docai` -> `docai-previewed`
2. `docai-previewed` -> `docai-handoff-exported`
3. `docai-handoff-exported` -> `docai-source-verified`
4. `docai-source-verified` -> `docai-source-packed`
5. `docai-source-packed` -> `docai-extracted-native`
6. `docai-source-packed` -> `docai-extracted-ocr`
7. any extraction-complete state -> `docai-openkb-added`
8. `docai-openkb-added` -> `docai-acceptance-passed`
9. `docai-acceptance-passed` -> `docai-ready-for-classification`
10. `docai-ready-for-classification` -> `docai-classified`
11. any state -> `docai-needs-review`
12. any state -> `docai-error`

## Rules

- Live tag writes require explicit approval.
- Classification tags must not be applied before acceptance evidence passes.
- Error and review tags must preserve the prior evidence trail.
- Removing a staging tag is a separate write action and requires approval.
