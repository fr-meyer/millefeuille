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

Package metadata at the qualification baseline is `0.5.0`. Former 0.4.0
documentation is stale. A cached 0.5.0 tag is historical/local evidence, not
fresh remote evidence or an available next version. Fresh tag/commit history
and the entire release diff must determine a separately approved SemVer plan.
This change does not bump a version, select a tag or replace a release.

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

## Actor-safe release boundary

The automatic semantic-release mutation job is removed. Push, pull_request and
workflow_dispatch run only six Linux/Windows x Python 3.11/3.12/3.13 read-only
test cells. Retained pyproject semantic-release configuration is historical
tooling configuration, not authority to run version/tag/release mutations.
A manually dispatched bot workflow is not a replacement release path.

The declared release owner in .speculoos/actors.json is human_web. After the
dependent final-main approval, the owner signs into GitHub personally, opens
fr-meyer/millefeuille -> Releases -> Draft a new release, selects the approved
new tag and exact actual-main commit, attaches only sealed assets and publishes
only the approved destination/visibility. If the UI cannot bind the exact SHA,
stop, do not select moving main by assumption. Verify tag target, downloaded
hashes and source provenance afterward. A bot token is not human_web. No new
broker release command is asserted; an alternative needs a supported reviewed
preview and explicit actor-policy resolution. Package registry publication and
activation remain separately approved effects.

## Review-policy ambiguity

Actors and publish-policy retain historical Mergeguez review declarations.
Current review actor availability has not been retrieved in this local-only
scope. These are repository_declared requirements, not proven github_enforced
or broker_enforced controls. No replacement actor or waiver is selected. Before
publication retrieve supported current review authority and live controls,
reconcile declarations with owner authority and obtain exact-head review.
Unavailable retrieval blocks publication, not local work. Historical PR98 is
retained as history, not a current PR readback or current task.
