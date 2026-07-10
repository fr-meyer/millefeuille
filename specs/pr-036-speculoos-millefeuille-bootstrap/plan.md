# Plan - Speculoos Millefeuille Bootstrap

## Phase 1 - Metadata Bootstrap

Add repo-local Speculoos files:

- `.speculoos/README.md`
- `.speculoos/goal.md`
- `.speculoos/manifest.yaml`
- `.speculoos/publish-policy.yaml`
- `.speculoos/review-evidence.json`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-036-speculoos-millefeuille-bootstrap.yaml`

The metadata records `dev` as the integration branch and
`chore/pr-036-speculoos-millefeuille-bootstrap` as the current working branch.

## Phase 2 - Spec/Audit Packet

Add `specs/pr-036-speculoos-millefeuille-bootstrap/` with:

- `spec.md` for the Millefeuille lifecycle definition and acceptance base;
- `plan.md` for this PR's execution plan;
- `tasks.md` for the next concrete checklist.

The packet is intentionally small enough to be the handoff for the next
implementation slice.

## Phase 3 - Local Validation

Run only local/offline validation:

```bash
.venv/bin/ruff check .
.venv/bin/python -m unittest discover -v
ruby -ryaml -rjson -e 'Dir[".speculoos/**/*.yaml"].sort.each { |p| Psych.parse_file(p) }; JSON.parse(File.read(".speculoos/review-evidence.json")); puts "metadata ok"'
git diff --check
speculoos status --json
speculoos plancha-status --json
speculoos validate --task pr-036-speculoos-millefeuille-bootstrap --validation "git diff --check" --json
speculoos publish-check
```

Do not run live Zotero, OCR, OpenKB, PageIndex, Mistral, or source-pack
commands in this PR.

## Phase 4 - Publish And Review

Publish the branch through the approved Mergeguez/broker path. The PR body must
say:

- feature PRs target `dev`;
- release/promotion PRs target `main`;
- Planning/docs/metadata only;
- Publication Boundary includes Zotero, OCR, OpenKB, credentials, releases, and
  approvals.

Request exact-head Mergeguez review. Merge to `dev` only if local validation,
GitHub checks, and review evidence pass on the exact PR head.

## Follow-Up Slice

After bootstrap lands, choose one implementation slice:

1. Add a source-version-drift fixture where recovered bytes do not match an
   earlier handoff SHA-256.
2. Add a machine-readable acceptance summary that joins handoff rows, skips,
   OpenKB outcomes, and duplicate-scan results.
3. Add multi-attachment and non-PDF fixture coverage.
4. Add an explicit live-run plan artifact, still requiring separate operator
   approval before any live credentials or provider calls.
