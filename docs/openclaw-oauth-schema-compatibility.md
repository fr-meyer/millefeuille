# Selected OAuth auth-cell compatibility

The product admits agent database versions 19 through 25 only, and independently
checks both auth tables against the canonical ordered STRICT three-column layout
(including NOT NULL, primary-key, defaults, hidden columns and rowid properties).
Unknown versions/layouts fail closed. Schema admission and the selected read share
one read-only transaction. This is not a full agent-store validator or migration.

Installed OpenClaw source proof (2026-11-16):

- `openclaw-agent-db-schema-helpers-DcqwgdE8.mjs:638-644`,
  `getOpenClawAgentMigrationSchema`, derives versions 19-25 from the current
  schema. Changes concern canonical session validation (21/25), transcript and
  memory storage (23), and session snapshots (24), not either auth table.
- `openclaw-agent-db-schema-gUrejYT9.mjs:795-919` executes those migrations;
  no credential conversion or auth-cell relocation occurs in this range.
- `sqlite-json-jx6D1mku.mjs:85-158` reads the same primary auth store/state JSON
  cells. OAuth selection/provider/expiry semantics remain product checks.
- `sqlite-read-pool-CoXOy4fw.mjs:70-115` opens auth read-only and admits
  user_version <=25, without requiring the complete session schema. Therefore
  the minimal isolated access-only snapshot intentionally remains version 19;
  it is an auth-reader input, NOT a canonical writable agent/session database.
  Do not bump this marker to claim a full v25 database.

The reviewed source hashes/excerpts are in the oauth-schema-compatibility report.
The product policy is deliberately narrower than the installed reader. Future
versions require a fresh source audit and regression coverage, not a dynamically
read ceiling or unbounded version acceptance. No public selected-profile API
was established that provides the same no-refresh, no-broad-profile contract.

Only the already-selected agent-local profile is extracted. Ordinary model status
must still prove exactly one OpenAI OAuth profile, no keys/tokens, and no shell
fallback. Shared-only auth remains insufficient. Snapshot generation omits refresh
and unrelated profiles; tool-disabled/no-fallback execution is unchanged.
