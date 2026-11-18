# Standalone clean taxonomy lifecycle

v23/v24 are provisional prior work, not optimized truth. Recommended bounded start: explicitly seed from the reviewed v24. Empty initialization remains optional. Neither is silently chosen when a store is absent. Normal process requires an explicitly initialized clean store, not --taxonomy. Legacy processing is explicit --legacy-analysis-only. Protected canonical validators and receipts are unchanged.

## Commands after independent review

Set PY to the approved runtime Python. STORE is an absent directory whose parent exists. RUN is an actual completed paper run. Commands below were not executed live during implementation.

```sh
PY=/path/to/approved/runtime/bin/python
# Recommended provisional seed, exact raw SHA256:
# Compare with the independently reviewed seed SHA256 before initialization.
SEED=/path/to/reviewed/provisional-taxonomy.json
sha256sum "$SEED"
"$PY" -m millefeuille.cli.clean_start init --store "$STORE" --seed "$SEED"
# Alternative only, different absent STORE: init --store "$STORE" (no --seed)
# Save returned registry_id, taxonomy_version and content_identity for this new root.
# Initial version is $STORE/<returned taxonomy_version>.json, not the old v24 ancestry.
"$PY" -m millefeuille.cli.clean_start project --run-dir "$RUN"
"$PY" -m millefeuille.cli.clean_start plan --store "$STORE" --run-dir "$RUN"
"$PY" -m millefeuille.cli.clean_start live --store "$STORE" --run-dir "$RUN"
"$PY" -m millefeuille.cli.clean_start replay --store "$STORE" --run-dir "$RUN"
"$PY" -m millefeuille.cli.clean_start export --store "$STORE" --run-dir "$RUN" --output-dir "$CARDS"
# Later paper, identical command, Decisions now sees all active leaves + create_new:
"$PY" -m millefeuille.cli.clean_start live --store "$STORE" --run-dir "$NEXT_RUN"
# New paper, taxonomy-free scientific analysis, followed by final selection:
"$PY" -m millefeuille.cli.standalone --output-dir "$OUTPUT" --taxonomy-store "$STORE" --item-key "$ITEM" --max-items 1 --live
```

Bootstrap needs ChatGPT token/account only, no OPENAI_API_KEY. Later final selection needs OPENAI_API_KEY and ChatGPT credentials preflighted because create_new remains possible. New process additionally requires Mistral and Zotero read configuration before OCR. Plan/project/replay require no credentials. All generation uses packaged subscription adapter, openai/gpt-6-astra xhigh, no tools/retries/API fallback. Its live protocol remains UNQUALIFIED until parent canary.

A consumed shared reservation blocks that paper, including bootstrap, rather than silently superseding old tags. Select only an explicitly authorized, unconsumed paper. Old locks, summaries, classification and tags remain immutable. Legacy evidence projection verifies old receipts but sends only summary content and original source spans. Scientific summary prose is retained verbatim: generic words such as Methods never trigger deletion. Only structured classification/parent-candidate fields are omitted, with explicit provenance. Any historical assignment claim inside prose is explicitly non-authoritative. Original spans remain unchanged.

Empty initialization creates zero parents/leaves. Seeded initialization validates the actual legacy registry and preserves every active ID, parent relation, label, definition, include/exclude and boundary array exactly (including empty historical boundaries). New proposals still require meaningful nonempty boundaries. Exact raw and semantic seed digests plus full original registry are sealed in seed_provenance; inactive entries are provenance/managed inventory, never candidate choices or provider registry input. Each initial root is new and independent, with null predecessor. Current-pointer ancestry terminates at that exact initial registry. No fake v23/v24 ancestry, rename or production initialization is performed by this review.


Each accepted proposal defines reusable primary contribution and meaningful include/exclude/boundaries. It can reuse a parent or create a new one. Contrast coverage is every existing active leaf, including 0/1/2 cases. Structural/lexical validation is NOT scientific proof. Human review should check synonyms, title compounds and over-specific categories. Exactly one final leaf is bound before CAS adoption. A completed saved response may reconcile without a provider call. Missing/uncertain provider outcomes cannot be retried automatically. Concurrent pointer changes fail closed; trusted POSIX filesystem/cooperating writers remain assumptions.

## Historical-label inventory and conditional writeback

Parent must locate every known sealed historical registry for the explicit scope, not merely assume v23/v24 are complete. SCOPE JSON has exactly library_type, library_id (string), item_keys (explicit eight-character keys, no wildcard). Inventory binds exact raw and semantic digests, versions and registry identities. It never claims those registries are new-root ancestors.

```sh
"$PY" -m millefeuille.cli.clean_start inventory --store "$STORE" --scope "$SCOPE" --historical "$V23" --historical "$V24" > "$INVENTORY"
"$PY" -m millefeuille.cli.clean_start writeback-plan --store "$STORE" --run-dir "$RUN" --inventory "$INVENTORY" --historical "$V23" --historical "$V24" --lineage "$INITIAL_VERSION" --snapshot "$ACTUAL_SNAPSHOT"
"$PY" -m millefeuille.cli.clean_start writeback-live --store "$STORE" --run-dir "$RUN" --inventory "$INVENTORY" --historical "$V23" --historical "$V24" --lineage "$INITIAL_VERSION" --authority "$AUTHORITY"
```

Repeat --historical for ALL inventory sources and --lineage for ALL clean-root ancestors through the initial empty or seeded version. Snapshot is actual parent/children JSON. Plan emits exact recognized tags, new label and binding. Authority has scope equal to that binding, approved:true, owner nonempty, complete_historical_inventory:true. Parent owns completeness attestation and actual authority. Live uses ZOTERO_WRITE_KEY, one conditional tag-only PATCH max and one readback snapshot, no retries/rollback. Unrecognized tags, notes, metadata, children and attachment metadata are preserved. No PDF byte verification claimed. Existing partial journal fails closed. No full-library loop exists.

## Consolidation and order bias

clean_consolidation.plan(before, after, mapping, rationale) validates a complete old-leaf mapping to existing surviving IDs and explicit linked successor, producing a review-only plan. Rename retains concept ID; merges retain a survivor and explicitly map removed IDs. It does not adopt or claim semantic correctness. Review representative papers across arrival orders, synonyms and one-paper fragmentation before a manually approved journaled transition. Preserve old versions and bindings, then plan a separately authorized reclassification campaign against mature taxonomy using saved content evidence, never redo OCR/analysis. Current per-paper paid reservation deliberately cannot be reset: later campaign reservation policy and manual transition adoption tooling remain separately scoped work.

Clean process exports only after verified final adoption and before writing 06-completed.json. Export failure is fatal, never silently complete. The offline export command can reconcile export without provider calls. Cards use <CARDS>/<clean-root>/<final-version>/<final-receipt-hash>.md; default is the clean journal cards directory. Exact unchanged replay performs no file writes. Legacy cards and old analysis are never overwritten. Cards contain full saved scientific summary, source hash/locators, saved-run and final-receipt paths, selected category/root/version/identity and explicit non-acceptance. A later current version does not relabel a historical receipt: its adopted version remains explicitly named.

PDFs/artifacts remain preserved pending actual cloud-hash qualification. No deletion, automatic full-library reprocess or per-paper guard reset exists. No end-to-end, semantic-quality or provider acceptance is claimed.
