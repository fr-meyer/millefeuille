## Summary

Enable [python-semantic-release](https://github.com/python-semantic-release/python-semantic-release) on `fr-meyer/millefeuille` so that **push to `main` after a green test matrix** can create the next SemVer Git tag and GitHub Release.

This PR targets `dev`. It does **not** cut a release and does **not** promote `dev` to `main`.

## Behavior

- Conventional Commits: `feat:` → minor, `fix:`/`perf:` → patch, `BREAKING CHANGE:` → major (`major_on_zero = false` while 0.x).
- Tag format `{version}` to match existing tags `0.1.0`–`0.4.0` (no `v` prefix; no partial tags).
- Release job uses `secrets.GITHUB_TOKEN` with `contents: write` only on that job. Workflow default remains `contents: read`.
- No PyPI/npm upload. `build_command` is a no-op.
- Rollback: remove or `if: false` the `release` job, revert `pyproject.toml` PSR tables, leave existing tags in place.

## Publication Boundary

Repository CI metadata only. No Zotero, OCR, or OpenKB runtime change. No new long-lived credential. GitHub Releases after tests on `main` still require a separate later approval to promote `dev`.

## Changed Files

- `.github/workflows/ci.yml`
- `pyproject.toml`
- `.speculoos/tasks/pr-162-semantic-release-on-main.yaml`
- `specs/pr-162-semantic-release-on-main/pr-body.md`
