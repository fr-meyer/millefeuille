# Standalone note-only writeback

Explicit new standalone operation, never a fallback from protected-mode refusal.
No canonical scientific acceptance, parent tags, collection membership, OCR or model calls.
POSIX only, Python 3.11+, httpx, existing pure taxonomy validators. No OpenClaw dependency.

Entrypoint: run_standalone_writeback_cli(argv). Parent integration registers
standalone-writeback in main.py. Module invocation is also supported:

    python -m millefeuille.cli.standalone_writeback --run-dir PATH --item-key KEY --library-id ID --approval-manifest PATH [--live]

Without --live and without an existing journal this validates local inputs only.
Existing completed journal reconciliation uses read-only remote requests, even with --live.
Supply ZOTERO_READ_KEY (library + notes) and ZOTERO_WRITE_KEY (library + write)
through operator environment. Both must identify the explicit user library. Writer need
not have notes access. No key discovery, response metadata persistence or key output.
Library privacy is an operator preflight, not inferred from permissions.

## Approval manifest v1

This is ordinary scoped operator intent, NOT a protected authority receipt.
Top-level schema = millefeuille-standalone-note-approval/v1, library_type = user,
library_id = decimal string, items = exactly three unique entries. Each entry has:

- item_key, run_dir (absolute original established run), run_id (run basename).
- attachment_key, parent_version, attachment_version (integers).
- parent_data_sha256, attachment_data_sha256: SHA256 of canonical selected item's data object.
- manifest_sha256: raw paperdir/manifest.json SHA256.
- source_sha256: original PDF SHA256.
- taxonomy_lock_identity: existing sha256:-prefixed validated lock identity.
- payload_path: absolute private frozen JSON file containing an array of one note.
- payload_sha256: raw payload file SHA256; note_html_sha256: UTF-8 HTML SHA256.
- identity: existing sha256:-prefixed stable managed-note identity from reviewed note plan.
- run_files: map of relative run path to raw file SHA256, including 00-selected.json,
  03-ocr-completed.json, 05-analysis-completed.json, 06-completed.json, taxonomy.json,
  analysis/{summary,classification,evidence,taxonomy-lock}.json, AND every ref in both
  03-ocr-completed.json.files and 05-analysis-completed.json.files (fulltext, OCR evidence,
  raw OCR response, analysis outputs). No absolute or parent-relative refs.

Canonical JSON = UTF-8, ensure_ascii=False, sort_keys=True, separators comma/colon,
allow_nan=False. Unprefixed hashes above are lowercase hex. Analysis evidence summary
and classification identities are separately recomputed from canonical JSON, not assumed
identical to file bytes. Preserve original files and hashes, do not fabricate receipts.
All input files must be owner-owned regular non-symlink files with no group/other bits;
paper directory must be private. Local artifacts bounded 64MiB; PDF 100MiB; regular JSON
2MiB. Operator must retain original established directory, not copy it to evade a journal.

Payload fields exactly itemType=note, parentItem=KEY, note=approved HTML,
tags=[{tag:millefeuille-summary}], collections=[], relations={}.
Only passive formatting tags with no attributes are allowed; no links, scripts or images.
HTML must contain exactly one approved 'Millefeuille writeback identity: sha256:...' marker.
Operator reviews frozen rendered content and its identity; hashes do not constitute scientific review.

## One effect, durable uncertainty

Fixed https://api.zotero.org; user libraries only; redirects/proxies/retries disabled.
30-second hard request deadline and 120-second aggregate remote budget, 2MiB response cap.
Fresh complete children list capped at 100, fail closed if total differs. Fresh parent and
PDF attachment must match selected original data/version and manifest, including local PDF MD5.
Any matching marker without an owned completed journal refuses creation, including exact
unowned duplicates. Ambiguous duplicates always refuse.

Exclusive fsynced paperdir/standalone-writeback-NOTE.json contains before-images, approved
payload and mutation intent BEFORE one array-of-one POST. Deterministic Zotero-Write-Token
is first 32 hex characters of entry canonical SHA256. This follows Zotero v3 write request
documentation; it is defense-in-depth, NEVER retry authority. No library version condition
is claimed: concurrent external modification can cause a retained note plus needs-review.

Require explicit successful[0], no unchanged/failed, version matching Last-Modified-Version;
validate all available ACK identity fields. Separate read client verifies exact saved note,
parent semantics (ignores only version/dateModified), unchanged attachment and children union.
ACK and terminal result stay private alongside journal. No rollback or deletion is automatic.
Interrupted/uncertain journal is terminal needs-review, never retried or removed. Completed
rerun only reads and verifies owned note/version and original metadata/children preservation.
Result JSON status is planned-read-only, written-verified, already-written-verified,
needs-review or refused, plus note_key (null unless known); refusal exposes exception class only.
Exit 0 for planned/verified, 2 for needs-review/refused. A later destructive recovery needs
separate approval and fresh versions; retained before-images and note/version support review.

## Qualification boundary

Source-only delivery. AST/lint are not functional evidence. Execute one authorized REAL item,
independently inspect its private proof/content, then the remaining two serially. Do not use
synthetics or call models again. No automatic retry after any post-intent failure.
