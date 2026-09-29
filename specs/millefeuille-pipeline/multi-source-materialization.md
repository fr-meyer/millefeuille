# Multi-source card and index materialization (MF-114)

The card and index fixture writers accept explicit whole-pack evidence for a
verified multi-PDF Zotero source pack. The existing v0.1 single-attachment evidence
remains supported. This downstream contract does not claim live multi-PDF
extraction, OCR, structure or model generation.

## Whole-pack evidence

Use millefeuille-card-fixture-evidence/v0.2 with card_json_path and
card_markdown_path, or millefeuille-index-fixture-evidence/v0.2 with
index_status_path. Both require item_key and source_scope. source_type defaults
to zotero; paper_id is optional and otherwise derived from the item key.

source_scope has schema_version millefeuille-source-scope/v0.1, the exact
sha256-aggregate source_hash and at least two sources. Each member declares:

- attachment_key, canonical_filename, the lowercase 64-character sha256;
- source_ref under sources/, naming a flat PDF file;
- zotero_version when present in the manifest. Missing and null versions mean
  no recorded version and must match the manifest exactly.

See [the machine-readable evidence schema](whole-pack-fixture-evidence.schema.json).
Runtime validation additionally checks canonical strings, unique attachment keys
and refs, and the recomputed aggregate. Reordering members is harmless. Duplicate
PDF content is allowed when attachment identities and refs are distinct.
Top-level single-attachment fields and unknown v0.2 fields are rejected. A v0.1
record carrying source_scope is rejected instead of silently discarding it.

## Verified materialization

Before planning and immediately before applying a write, the writer loads the
v0.2 source manifest, compares the complete member set, item and paper identity,
aggregate hash, filename and version, then securely reads and hashes every PDF.
Missing files, symlinks, changed bytes, sizes or metadata fail before that write.
The selected route and structure sidecars must carry the same aggregate identity.
The existing summary bundle must match the resolved paper and analysis run.
A batch is fully preflighted before its first write; this does not make a batch of
separate papers one transaction or freeze files against later concurrent changes.

Cards require the canonical paper-card/v0.2 contract. Their identity stores the
aggregate source_hash and canonical_filename is null: all filenames and attachment
identities remain in the verified source manifest. No primary PDF is invented.
The canonical index stores the same aggregate, run and artifact refs. The existing
controlled card/index transaction changes planned lanes to observed outcomes,
preserves concurrent edits on rejection and retains recovery evidence. Exact
reruns preserve the original card, index and manifest bytes, including after the
card has reached observed state.

## Verification and remaining coverage

Synthetic tests use real two-PDF intake and explicit upstream fixtures containing
both attachment identities. They cover card creation, observed index refresh,
reversed-order reruns, malformed and incomplete scopes, full member metadata drift,
missing or changed PDFs, symlinks, batch preflight, mutation after planning and
concurrent card rollback. Existing single-source tests remain the compatibility
baseline. These operations make zero provider calls.

The [whole-pack native upstream planner](multi-source-upstream.md) has a live
CollaGAN publication. Its two verified PDFs, 19 native pages, 23 saved GPT
summaries, paper card and native index are preserved in the private source
packs. The [existing multi-source run-package plan](multi-source-run-package.md)
checks these outputs without a provider call; permanent package publication,
two-row handoff, acceptance and duplicate review remain separate gates. Raw
production paper text, provider prompts, credentials and live receipts do not
belong in repository fixtures.
