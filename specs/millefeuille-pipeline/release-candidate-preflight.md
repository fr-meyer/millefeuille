# Release Candidate Preflight

Local RC preflight is now an explicit offline artifact step.

## What Exists Now

- `millefeuille run --release-preflight` writes
  `reports/release-candidate-preflight.json` plus
  `reports/release-candidate-preflight.md` under a verified run directory.
- The preflight records current package version, completed offline stages,
  remaining manual gates from the stage manifest, and a local readiness verdict.
- The verdict is intentionally conservative: it is for review and planning, not
  for automatic promotion or tagging.

## What It Does Not Do

- It does not bump the version.
- It does not update changelog or release notes automatically.
- It does not open a `dev` to `main` PR.
- It does not create a tag.
- It does not publish a package.

## Required Manual Gates

- GitHub publication and merge confirmation for any pending feature PRs.
- Approved live evidence if release readiness depends on live dogfood lanes.
- `dev` to `main` promotion approval.
- Release tag approval.
- Package publication approval.

## Expected Operator Flow

1. Finish offline validation on `dev`-target feature branches.
2. Merge the intended feature PRs through the approved review lane.
3. Re-run local RC preflight against the intended artifact package or latest
   validated run.
4. Review version, release notes, and remaining manual gates explicitly.
5. Open the promotion PR only after those review steps are complete.
