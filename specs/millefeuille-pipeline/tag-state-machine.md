# Tag State Machine

Tags should represent stage state, not final classification truth. They are
secondary to handoff/source-pack/OpenKB evidence.

The machine-readable v0.1 authority is
`lifecycle-tag-registry.v0.1.json`, validated by
`lifecycle-tag-registry.schema.json` and the runtime exact-policy validator.
Its ordered tags match `TagState`, and its normal transitions match
`ALLOWED_TAG_TRANSITIONS`. See `lifecycle-tag-migration.md` for the offline,
content-addressed migration preview and its separate approval boundary.

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

- `millefeuille-structure-ready`
  - Page, section, table, figure, and reference structure evidence exists.

- `millefeuille-summarized`
  - Required page/section/full-paper summaries exist for the configured scope.

- `millefeuille-card-ready`
  - Markdown and JSON paper card artifacts exist.

- `millefeuille-openkb-added`
  - Selected Markdown was added to OpenKB.

- `millefeuille-indexed`
  - Required OpenKB/PageIndex and optional ConDB/ChatIndex index lanes were
    written or explicitly skipped with evidence.

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
7. any extraction-complete state -> `millefeuille-structure-ready`
8. `millefeuille-structure-ready` -> `millefeuille-summarized`
9. `millefeuille-summarized` -> `millefeuille-card-ready`
10. `millefeuille-card-ready` -> `millefeuille-openkb-added`
11. `millefeuille-card-ready` -> `millefeuille-indexed`
12. `millefeuille-openkb-added` -> `millefeuille-indexed`
13. `millefeuille-indexed` -> `millefeuille-acceptance-passed`
14. `millefeuille-acceptance-passed` -> `millefeuille-ready-for-classification`
15. `millefeuille-ready-for-classification` -> `millefeuille-classified`
16. any state -> `millefeuille-needs-review`
17. any state -> `millefeuille-error`

Compatibility transitions:

- any extraction-complete state -> `millefeuille-openkb-added`
- `millefeuille-openkb-added` -> `millefeuille-acceptance-passed`

These shortcuts are allowed only when the stage manifest records the skipped
structure, summary, card, or index stages and explains why the reduced pipeline
is acceptable.

## Rules

- Live tag writes require explicit approval.
- Classification tags must not be applied before acceptance evidence passes.
- Error and review tags must preserve the prior evidence trail.
- Removing a staging tag is a separate write action and requires approval.
- Tag writeback is separate from stage execution. A stage can pass in the
  manifest while the live Zotero tag write remains preview-only until approved.
- Zotero tags are observations, never stage evidence. The exact
  `millefeuille-processed` tag and every `docai`-prefixed tag are legacy
  observations with no canonical mapping and must be preserved by MF-161.
- `docai-pageindex` does not imply `millefeuille-indexed`; indexing requires a
  passed index stage and explicit final lane evidence.
- `millefeuille-classified` requires a passing acceptance summary, a classified
  decision, and the released taxonomy version locked to the exact single run.
- An offline migration preview may propose removing only the exact
  `millefeuille` selection tag, only after terminal success. MF-160 must recheck
  the current Zotero item version and obtain separate approved-live authority
  before any mutation.
