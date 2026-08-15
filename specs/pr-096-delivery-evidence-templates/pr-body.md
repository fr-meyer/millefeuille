## Summary

- add a machine-readable catalog and navigation for reusable delivery evidence
- provide copy-ready feature, approval, adapter-evidence, dogfood, maintenance-handoff, migration, and release packets
- keep approvals exact and non-transitive, dogfood no-write by default, handoffs non-authorizing, and promotion, tag, and package gates independent
- enforce safe local paths, required headings and placeholders, policy markers, and detectable credential, authenticated-URL, private-root, and raw-PDF signatures across every added public documentation and metadata artifact
- add canonical Speculoos delivery gates for roadmap card MF-002

## Changed Files

- `.speculoos/README.md`
- `.speculoos/tasks/pr-096-delivery-evidence-templates.yaml`
- `specs/millefeuille-delivery/README.md`
- `specs/millefeuille-delivery/catalog.json`
- `specs/millefeuille-delivery/templates/adapter-evidence-packet.md`
- `specs/millefeuille-delivery/templates/approval-packet.md`
- `specs/millefeuille-delivery/templates/dogfood-report.md`
- `specs/millefeuille-delivery/templates/feature-packet.md`
- `specs/millefeuille-delivery/templates/maintenance-handoff.md`
- `specs/millefeuille-delivery/templates/migration-packet.md`
- `specs/millefeuille-delivery/templates/release-packet.md`
- `specs/millefeuille-pipeline/README.md`
- `specs/pr-096-delivery-evidence-templates/commit-message.txt`
- `specs/pr-096-delivery-evidence-templates/pr-body.md`
- `tests/test_millefeuille_delivery_templates.py`

## Validation

- 17 focused delivery-template, contract, and hard-rename tests passed
- all 348 repository tests passed
- repository-wide Ruff passed
- changed catalog JSON parsing passed
- `git diff --check`, privacy/manual-gate contract checks, Speculoos validation, and publication checks passed

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main`.

This routine feature PR requires exact-head Mergeguez approval and successful GitHub checks before merge. It does not authorize promotion to `main`, release creation, tag creation, package publication, or deployment.

## Documentation Impact

No user-facing documentation changes required. The repository-native
`specs/millefeuille-delivery/` packet set documents its own selection guidance,
copy instructions, evidence conventions, and explicit privacy and authority
boundaries.

## Publication Boundary

Repository-local documentation, reusable public-safe templates, machine-readable catalog metadata, synthetic contract tests, and Speculoos publication metadata only. No credential lookup, provider/model/OCR call, private paper read, live Zotero/OpenKB/PageIndex/source-pack mutation, stable-branch promotion, release, tag, package publication, deployment, or approval bypass.

## Task

Speculoos task: `pr-096-delivery-evidence-templates`

Roadmap card: `MF-002`

The active task head field remains null by design: a commit cannot
embed its own SHA without changing it. The immutable Mergeguez review and check
bind validation to GitHub's live exact head; closeout records the final head SHA
and merge commit after merge.

Supersedes closed PR #92; this branch name satisfies the repository actor policy.
