# Legacy standalone final classification, repair v2

For normal new processing and provisional seeded/optional empty roots use [clean-start.md](clean-start.md). Commands here are the preserved legacy taxonomy protocol, not the clean-root workflow.

Historical analysis/locks remain unchanged. Final selection uses all active leaves plus create_new_category; confidence is metadata, never an abstention threshold. Existing consumed legacy reservations still block new spend.

## Subscription adapter

Default executor=None now selects the packaged standalone adapter:

    python -m millefeuille.clients.subscription_adapter
    python -m millefeuille.clients.subscription_adapter --capabilities

Generation reads one direct-generation/v1 JSON request from stdin and emits one response. Operator supplies MILLEFEUILLE_CHATGPT_ACCESS_TOKEN and MILLEFEUILLE_CHATGPT_ACCOUNT_ID through inherited environment only, never argv. No automatic credential discovery/refresh, OpenClaw imports, native Codex executable, installer, paid API fallback, tools, redirects, environment proxy or retry. Existing httpx is the only transport dependency. Final selection admits only this packaged adapter (explicit equivalent argv is allowed), checked before Decisions reservation. Other legacy adapters remain usable by direct_analysis but not by the final-category stage.

Fixed POST endpoint https://chatgpt.com/backend-api/codex/responses, requested gpt-6-astra with reasoning effort xhigh. Actual completed SSE response must report completed status, exact model, nonempty response ID, usage and JSON output; tool/refusal content fails. transport_evidence preserves actual completed response, SSE hash and request hash. The top-level reasoning field is CLIENT REQUESTED, not observed proof; provider_observed_reasoning is null if absent. These are local transport records, not signed provider receipts. Parent must qualify actual subscription credentials and server compatibility before live use. Unknown: acceptance of this minimal independent client's headers and exact output shape on the live account. No speculative telemetry/originator is added.

## Commands

    python -m millefeuille.cli.final_classification --run-dir RUN --taxonomy V24 --compact-plan
    python -m millefeuille.cli.final_classification --run-dir RUN --taxonomy V24 --store STORE --live --max-requests 1

Normal new process uses the clean-root lifecycle; --legacy-analysis-only retains historical analysis behavior. The explicit legacy final-classification command can use a different final taxonomy. STORE/current.json must be initialized by the operator after boundary approval with taxonomy_version/content_identity matching the selected snapshot. Existing version bytes, including noncanonical whitespace, are preserved if their parsed registry matches.

## Crash and concurrency contract

Private fsynced temporary files are exclusively linked into public immutable filenames, never overwritten. Parent-directory fsync makes paper/provider reservations durable before calls. Request-only or partial provider outcomes fail closed: no retry. A genuine saved Decisions response and, for create, saved complete category response permit explicit reconcile(context, store), with no provider calls. CLI --live on an existing incomplete final directory invokes only this reconciler; ordinary replay remains read-only.

Final lock, final paper binding and transition journal are durable BEFORE pointer CAS. Transition binds predecessor/successor, request/category request/proposal/final/lock digests. Cooperating POSIX writers share .current.lock. Store adoption marker is run-bound; same successor without that marker is not proof of this run's adoption. Historical installation receipt is required by verification/writeback independently of later pointer advances. No-change finals also carry a receipt.

A crash after pointer change but before receipt can reconcile only while pointer is still that exact successor and the adoption marker matches. If another transition already advanced it, fail closed for operator investigation; no rollback or overwrite. A pointer race leaves a bound but uninstalled final, never writeback-ready. Missing provider artifacts cannot be reconstructed or regenerated.

## Proposal quality

Exactly one proposal call only when create wins, reusing genuine saved analysis context. Exact normalized labels reuse active existing IDs. New proposals require a reusable research question, parent-insufficiency rationale and distinct concrete nearest-category contrasts covering all active siblings plus closest alternative, at least three. Title-specific method/application compounds are prohibited in instructions and compound=true fails. Invalid/ambiguous structures fail processing, not final none/needs_review.

These checks enforce evidence structure, NOT scientific truth. Semantic synonyms, superficial prose or omitted cross-parent plausible alternatives cannot be excluded deterministically. Actual semantic/boundary review remains necessary; automatic scientific acceptance is NOT claimed.

## Tag-only writeback

    python -m millefeuille.cli.final_classification --run-dir RUN --taxonomy V24 --writeback-authority AUTHORITY --lineage PREVIOUS_REGISTRY [--live]

Supply complete validated ancestry to the root (including v23 with both previous fields null), never just a connected suffix. Authority scope includes operation, library_type/library_id, item_key, run_dir, final_sha256, final_lock_identity, sorted lineage_identities, lineage_sha256 map and max_mutations=1. All historical full leaf labels, including renamed/deprecated, are recognized. Exactly one recognized label remains; unrelated tags preserved. Full actual parent tags/data and all children metadata are snapshotted, one conditional PATCH at most, independent readback. No PDF download claim.

Exclusive writeback directory creation, preimage, intent and mutation-start marker are fsynced with the parent before PATCH. Any incomplete journal, including uncertain remote outcomes, blocks automatic retry and rollback. Credentials are read only after final/ancestry/authority checks.

## Qualification

Only static AST/Ruff and the three actual saved plans/legacy replay were used. No synthetic papers/responses/fixtures/tests, credentials, provider/Zotero/network calls or publication. Live protocol, crash fault injection and semantic acceptance remain unqualified. Parent independent frozen-source review precedes any live qualification.
