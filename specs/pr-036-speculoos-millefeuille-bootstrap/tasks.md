# Tasks - Speculoos Millefeuille Bootstrap

- [x] Confirm clean `dev` base and absent `.speculoos` metadata.
- [x] Run `speculoos preflight` with task, branch, acceptance, validation, and
  publication-boundary inputs.
- [x] Create feature branch
  `chore/pr-036-speculoos-millefeuille-bootstrap`.
- [x] Add repo-local `.speculoos` baseline.
- [x] Add Millefeuille spec/audit packet.
- [x] Run Ruff.
- [x] Run unit tests.
- [x] Parse YAML/JSON metadata.
- [x] Run `git diff --check`.
- [x] Run `speculoos status`, `plancha-status`, `validate`, and
  `publish-check`.
- [x] Scan changed files for credentials, signed URLs, bearer tokens, cookies,
  raw PDF payload markers, and unredacted Zotero secrets.
- [ ] Publish branch through the approved Mergeguez/broker path.
- [ ] Open PR to `dev`.
- [ ] Request exact-head Mergeguez review.
- [ ] Merge to `dev` only if validation, GitHub checks, and review evidence pass.

## Next-Slice Candidates

- [x] Source-version-drift fixture for changed Zotero attachment bytes.
- [x] Acceptance summary that joins handoff rows, skip rows, OpenKB outcomes,
  and duplicate-scan results.
- [x] Multi-attachment and non-PDF fixture coverage.
- [x] Live-run plan artifact with an explicit separate approval gate.
- [x] Remaining-work pipeline, CLI contract, stage-manifest schema, tag-state
  machine, OCR backend contract, and release/version policy.
