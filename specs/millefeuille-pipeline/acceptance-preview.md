# Saved acceptance inspection

Use the public `preview_acceptance_summary` API in `millefeuille.domain.acceptance`,
or the operator command:

```sh
millefeuille acceptance-preview --source-pack-root /operator/approved/root \
  --paper-id zotero-ITEM1 --run-id saved-run --handoff /operator/handoff.jsonl --json
```

Optional: --item-key instead of --paper-id, --duplicate-scans,
--artifact-root and --stage-manifest (existing resolver contracts). Flat saved
runs require an explicit --artifact-root. No output, approval, live mode,
batch, provider or publication switches exist on this command.

The exact writer resolver and acceptance builder run without persistence or
stage/artifact upserts. Output is planned/preview, recorded=false, writes=0,
provider_calls=0. Candidate status and check statuses describe saved metadata
only. Exit 0 means inspection succeeded, including a needs-review candidate;
exit 2 means invalid/unavailable saved evidence. Errors are intentionally generic.

No arbitrary notes, paper text, handoff rows, credentials, absolute paths or
external references are exported. Review codes are fixed check-name codes,
not the writer's free-form review reasons. No refs are exported at all.

Important limitations:
- This is not semantic whole-paper acceptance or proof of negative duplicates.
  The legacy duplicate builder can report passed for absent/empty evidence.
  The preview conservatively marks missing or explicitly unrun duplicate evidence
  needs-review and emits a fixed review code. An explicit boolean match result
  only establishes saved evidence presence, not an independently verified current
  scan or its freshness. Writer behavior is unchanged. Preview candidate status
  can therefore be stricter than the legacy writer; this is intentional.
- Single-source resolution validates saved manifest identity, not necessarily
  a fresh hash of every original PDF. v0.2 upstream checks revalidate native
  text/page attribution. Do not infer checks that the builder does not perform.
- Missing handoff is needs-review. Malformed/drifted required inputs may fail
  inspection instead of producing a candidate. There is no fallback to writes.
- Scope is `saved-structural-evidence-only`; `semantic_acceptance_performed`,
  `write_authorized`, and `input_snapshot_bound` are all false. A preview is not
  an exact mutation plan, approval receipt, stable snapshot binding, or permission
  to record acceptance. Files may change after inspection.
- Existing acceptance command remains the separate four-output writer. Private
  writes, providers, Zotero, GitHub, releases and deployment retain separate
  exact-scope approval gates. No new live write or approval path is implemented.

Synthetic tests exercise real single-source writer compatibility and read-only
census, plus real two-source upstream/handoff synthesis with mocked resolver and
verified downstream loaders. They do not prove an end-to-end private two-source
published package or live provider behavior.
