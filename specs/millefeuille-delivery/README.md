# Millefeuille Delivery And Evidence Templates

This directory contains copy-ready, repository-native packet templates for the
Millefeuille lifecycle. The templates make task scope, approval authority,
evidence, privacy boundaries, and restart state explicit without treating chat
or agent memory as the system of record.

`catalog.json` is the machine-readable index. Contract tests verify that every
catalog entry resolves to a template and that required sections, placeholders,
and safety statements remain present.

## Choose A Packet

| Need | Template | Use it when |
| --- | --- | --- |
| Bounded repository change | [Feature packet](templates/feature-packet.md) | Planning, reviewing, or closing one feature PR |
| Live or sensitive operation | [Approval packet](templates/approval-packet.md) | Credentials, writes, paid calls, private inputs, or live state are involved |
| Provider or system boundary | [Adapter evidence packet](templates/adapter-evidence-packet.md) | Proving an adapter's mapping, integrity, and failure behavior |
| Bounded product exercise | [Dogfood report](templates/dogfood-report.md) | Recording no-write readback or a separately approved live run |
| Restartable operations | [Maintenance handoff](templates/maintenance-handoff.md) | Moving unfinished or recurring work to another operator or session |
| Contract transition | [Migration packet](templates/migration-packet.md) | Replacing a CLI, schema, storage, tag, or integration surface |
| Stable delivery | [Release packet](templates/release-packet.md) | Promoting `dev`, reviewing a version, tagging, or publishing a package |

## How To Use A Template

1. Copy the selected file into the task's `specs/` directory or an approved
   private evidence location. Do not edit the canonical template in place.
2. Replace every `{{lower_snake_case}}` placeholder. When a field is genuinely
   inapplicable, write `not-applicable` and a short reason; do not silently
   delete a required section.
3. Link to committed public-safe artifacts by repository-relative path. Keep
   private evidence in the approved private store and record only a safe opaque
   reference in a committed packet.
4. Record exact branch names, commit SHAs, validation commands, decisions, and
   timestamps. A plan, preview, or no-write readback must not be described as an
   executed live operation.
5. Update the applicable `.speculoos/tasks/` record and `.speculoos/manifest.yaml`
   independently. A packet supplements the canonical control plane; it does not
   replace task, review, or merge evidence.
6. Run `python -m unittest -v tests.test_millefeuille_delivery_templates` before
   publishing template-derived repository evidence.

## Common Safety Contract

- Never commit credentials, authenticated URLs, cookies, raw PDF bytes, private
  paper text, provider payloads, or private filesystem paths.
- Approval is exact and bounded. One approved operation does not authorize a
  later provider call, write, promotion, tag, or package publication.
- No-write Carte Blanche dogfood is evidence for planning surfaces only. It is
  not release, promotion, runtime-route, credential, ruleset, or write
  authorization evidence.
- Feature work and stable release work follow the branch and actor rules in
  `.speculoos/actors.json`, `.speculoos/publish-policy.yaml`, and
  `../millefeuille-pipeline/release-version-policy.md`.
- Live Millefeuille approval fields are defined by
  `../millefeuille-pipeline/live-run-plan.md`.

## Contract Sources

- [Repo-local Speculoos control plane](../../.speculoos/README.md)
- [Millefeuille pipeline packet](../millefeuille-pipeline/README.md)
- [Live-run approval plan](../millefeuille-pipeline/live-run-plan.md)
- [Release and version policy](../millefeuille-pipeline/release-version-policy.md)
- [Artifact storage contract](../millefeuille-pipeline/artifact-storage.md)
- [Speculoos Carte Blanche dogfood](https://github.com/fr-meyer/speculoos/blob/main/docs/carte-blanche-dogfood.md)

The original no-write onboarding requirement was recorded by Speculoos Carte
Blanche dogfood: use planning surfaces without mutating the downstream repo,
then preserve readback, friction, reconciliation, and carry-forward constraints.
