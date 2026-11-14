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

## Mandatory final-main approval fence

1. Obtain exact promotion approval (head/base, actor, merge strategy). Verify
   the actual merged CI workflow cannot mutate a release before promotion.
2. Read back actual final main SHA, tree and package version. Compare the entire
   promotion delta with the approved candidate/version plan. Unexpected merge,
   conflict resolution, version or workflow changes stop the release.
3. Rebuild wheel and sdist from an archive of that exact final main SHA. Never
   relabel dev-built assets. Clean-install in a disposable environment, smoke
   and rerun all invalidated checks. Record resolved dependency provenance,
   separately from reused-dependency tests.
4. Seal asset SHA256 hashes, source/member provenance, release notes, destination
   and visibility. Obtain a dependent tag/assets approval snapshot bound to
   those actual bytes and SHA. Promotion approval cannot approve unknown future
   assets. Tag and package publication each need explicit scope.
5. The human_web owner follows the release-policy UI path. Verify tag target,
   downloaded hashes and that asset members derive from the approved SHA.
6. Activation, if selected, requires exact target approval, preserved previous
   runtime, ordinary-UID smoke and reversible selector rollback.

Unexpected deltas require bounded review, requalification and rebound approval.
No tag replacement, release/deploy automation, credentials or provider calls are
authorized by preflight. Hosted six-cell results, fresh remote tags, current
review authority and clean dependency resolution remain independent gates.
