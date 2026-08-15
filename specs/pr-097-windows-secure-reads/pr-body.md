## Summary

- preserve exact binary artifact reads on Windows with binary and non-inheritable descriptor flags
- separate stable object identity from mutation snapshots so Windows avoids inconsistent descriptor-side ctime while POSIX retains ctime checks
- reject symbolic links, reparse points, non-directories, unstable identities, path replacement, mutation, and partial reads in the portable fallback
- document the compatibility boundary and add focused cross-platform regression coverage
- add canonical Speculoos delivery gates for roadmap card MF-001

## Changed Files

- `.speculoos/tasks/pr-097-windows-secure-reads.yaml`
- `README.md`
- `millefeuille/domain/secure_io.py`
- `specs/pr-097-windows-secure-reads/commit-message.txt`
- `specs/pr-097-windows-secure-reads/pr-body.md`
- `tests/test_millefeuille_retrieve.py`
- `tests/test_millefeuille_secure_io.py`

## Validation

- 26 focused secure-I/O and retrieval tests passed
- all 359 repository tests passed
- repository-wide Ruff passed
- changed metadata parsing passed
- `git diff --check`, privacy/manual-gate contract checks, Speculoos validation, and publication checks passed

## Release Flow

feature PRs target `dev`; release/promotion PRs target `main`.

This routine compatibility fix requires exact-head Mergeguez approval and
successful GitHub checks before merge. It does not authorize promotion to
`main`, release creation, tag creation, package publication, or deployment.

## Documentation Impact

`README.md` now documents Windows binary-read flags, reparse-point rejection, stable
identity binding, and the platform-specific timestamp checks used by the
portable fallback.

## Publication Boundary

Offline secure-read runtime, README guidance, synthetic tests, and
repository-local Speculoos metadata only. No credential lookup,
provider/model/OCR call, private paper read, live
Zotero/OpenKB/PageIndex/source-pack mutation, stable-branch promotion, release,
tag, package publication, deployment, or approval bypass.

## Task

Speculoos task: `pr-097-windows-secure-reads`

Roadmap card: `MF-001`

The active task head field remains null by design: a commit cannot embed its own
SHA without changing it. The immutable Mergeguez review and check bind
validation to GitHub's live exact head; closeout records the final head SHA and
merge commit after merge.

PR #99 currently depends on this branch. Retain it after PR #97 merges until PR
#99 is safely retargeted to `dev`.
