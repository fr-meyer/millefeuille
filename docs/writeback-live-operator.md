# Writeback root/operator source checkpoint, NOT live-ready

CLI registration is real:
- millefeuille writeback-operator plan --inputs /PRIVATE/inputs.json
- millefeuille writeback-operator preflight --inputs /PRIVATE/inputs.json --receipt /PRIVATE/receipt.json
- execute and rollback require --mode approved-live and an independently approved exact receipt.
- readback requires --mode approved-live --execution-audit sha256:... and returns journal evidence, not a new remote observation.

Inputs contain exactly materialized, before, collection, add_tags, remove_tags, matching writeback-core.md. Planning is no-auth/no-network; preflight is scope validation only. Rollback preflight uses --operation rollback --execution-audit. No secret argument/environment route exists.

The new one-shot Linux root broker uses a main-thread accepted socket, cumulative framing deadline and caller UID. It reconstructs the plan from fixed-root files authenticated by the administrator configuration; checks original-source bytes and original-parent identity/version/content separately from the fresh before snapshot; matches exact library/item/collection allowlist and authenticated receipt. Atomic core consumption precedes HTTP effects. Secret comes only from fixed protected credential file. No startup journal creation. Original commitment lives outside ledger, with its original digest pinned by separate root config. provision_attended is explicit administrator-only source, not invoked or available from operator CLI. A failed ceremony retains quarantine.

## Deliberate hard stop and remaining engineering

Service startup currently ALWAYS FAILS CLOSED after validating controls: the frozen item-only HTTP interface has no independently acquired existing-collection proof. Do not remove this stop on the basis of an administrator mapping assertion. Complete and independently review collection GET/mapping verification first, without weakening frozen HTTP semantics. This is a partial source integration, not usable production writeback.

Also required before lifting startup stop: independently reviewed source-custody import/attestation from actual accepted scientific output (not proposal-only), raw original/fresh proof custody and freshness policy, actual mock-HTTP successful Unix root-route integration test, broader source race/permission/replacement and scope negative tests, provisioning failure tests, immutable installed runtime verification and source acceptance of repaired T1/T2 HTTP transport. Current successful dispatch tests mock file custody/root gate and HTTP boundaries, while exercising real receipt checks, SQLite journal and core. They are NOT root or live qualification.

Fixed control /etc/millefeuille/writeback-v1: config.json, journal-commitment.json, execute-receipt.json, rollback-receipt.json, credential, operator.sock. Evidence /var/lib/millefeuille/writeback-evidence-v1. Journal /var/lib/millefeuille/writeback-v1/operations.sqlite3. All evidence and controls are single-link0600 with root-owned non-writable no-symlink ancestor traversal. Same-root privileged mutation/rollback remains outside this trust model. No files/services/credentials were provisioned.

Source acceptance, publication, writeback, rollback, target identification and deployment authority remain separate. No notes, attachments, collection creation or batches. No accepted source/runtime/package report changed.
