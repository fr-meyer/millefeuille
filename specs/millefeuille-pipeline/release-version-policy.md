# Release And Version Policy

Speculoos should govern releases, tags, and version changes, but release actions
remain manual gates.

## Branches

- Feature branches target `dev`.
- Feature PRs to `dev` may be auto-merged by `mergeguez_dev_merge` after
  clean exact-head Mergeguez review and checks.
- Stable promotion uses a separate PR from `dev` to `main`.
- No direct feature branch should target `main`.

## Versioning

Use semantic versioning after the Millefeuille contract stabilizes:

- patch: fixture/test/doc hardening with no CLI contract change;
- minor: new offline stage contracts, CLI commands, adapter interfaces, or
  compatible manifest fields;
- major: incompatible schema, CLI, tag-state, or source-pack contract changes.

Current version: `0.4.0`.

The legacy Hydra surface remains available through the compatibility window
defined in `legacy-migration.md`. Removing or disabling that surface is an
incompatible CLI change and must not occur earlier than a separately approved
`1.0.0` proposal whose migration exit criteria pass.

## Release Candidate Checklist

- `dev` contains the intended feature PRs.
- Offline validation passes.
- Any approved live dogfood evidence is summarized in a non-secret artifact.
- Changelog/release notes mention manual gates and unsupported live paths.
- Stable promotion PR targets `main`.
- Mergeguez or configured review evidence is clean.

## Tag Checklist

- Version bump committed on the promotion branch or release PR.
- Release notes reviewed.
- `main` matches the approved promotion commit.
- Tag creation is explicitly approved.
- Package publication is explicitly approved separately from tag creation.

## Manual Gates

- GitHub publication and PR creation.
- Merge to `dev` unless the dev-lane auto-merge policy has passed cleanly.
- `dev` to `main` promotion.
- Release tag.
- Package publication.
- Any provider, credential, source-pack, OpenKB, Zotero, or PDF live action.
