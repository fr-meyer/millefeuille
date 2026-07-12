# Tag State Machine

Tags should represent stage state, not final classification truth. They are
secondary to handoff/source-pack/OpenKB evidence.

## Suggested Tags

- `millefeuille`
  - Operator-selected candidate.

- `millefeuille-previewed`
  - Discovery or handoff preview was generated.

- `millefeuille-handoff-exported`
  - Authoritative handoff row exists.

- `millefeuille-source-verified`
  - Recovered attachment bytes matched the handoff SHA-256.

- `millefeuille-source-packed`
  - Source pack was created or updated.

- `millefeuille-extracted-native`
  - Native extraction evidence exists.

- `millefeuille-extracted-ocr`
  - OCR extraction evidence exists.

- `millefeuille-openkb-added`
  - Selected Markdown was added to OpenKB.

- `millefeuille-acceptance-passed`
  - Acceptance summary passed with no unmatched rows or duplicate review rows.

- `millefeuille-ready-for-classification`
  - Classification may begin.

- `millefeuille-classified`
  - Evidence-backed classification decision is complete.

- `millefeuille-needs-review`
  - The item needs manual review.

- `millefeuille-error`
  - The item failed a stage.

## Allowed Transitions

1. `millefeuille` -> `millefeuille-previewed`
2. `millefeuille-previewed` -> `millefeuille-handoff-exported`
3. `millefeuille-handoff-exported` -> `millefeuille-source-verified`
4. `millefeuille-source-verified` -> `millefeuille-source-packed`
5. `millefeuille-source-packed` -> `millefeuille-extracted-native`
6. `millefeuille-source-packed` -> `millefeuille-extracted-ocr`
7. any extraction-complete state -> `millefeuille-openkb-added`
8. `millefeuille-openkb-added` -> `millefeuille-acceptance-passed`
9. `millefeuille-acceptance-passed` -> `millefeuille-ready-for-classification`
10. `millefeuille-ready-for-classification` -> `millefeuille-classified`
11. any state -> `millefeuille-needs-review`
12. any state -> `millefeuille-error`

## Rules

- Live tag writes require explicit approval.
- Classification tags must not be applied before acceptance evidence passes.
- Error and review tags must preserve the prior evidence trail.
- Removing a staging tag is a separate write action and requires approval.
