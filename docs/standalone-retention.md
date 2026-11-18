# Standalone PDF retention

Successful new process runs default to no retained local PDF bytes. Both exact
owned paths are removed: paper/source.pdf and
runs/RUN/ocr/direct-mistral/original.pdf. Zotero's original is never mutated.
--keep-pdf explicitly opts out. Failures retain PDFs for reconciliation; they
must not be described as successful no-PDF completion.

Processing writes the historical 06-completed.json before retention. That file
is immutable and proves processing only, not retention. Retention uses separate
07-retention-intent.json and 08-retention-completed.json. Existing old runs are
not silently migrated. OCR response, fulltext, summaries, classifications,
taxonomy, source identity/hash, reservations and journals remain unchanged.

## Existing completed runs

Local plan, no credential access, network or writes:

    millefeuille standalone-retention --run-dir PATH

After reviewing the exact run and its plan:

    millefeuille standalone-retention --run-dir PATH --live

Set ZOTERO_LIBRARY_ID and ZOTERO_READ_KEY through your private environment.
Library type comes from the validated source manifest (user or group).
The live path verifies completion, artifact hashes, source SHA256/size/MD5,
both regular single-link PDFs (50 MB maximum each), and original identities.
It then makes fresh bounded metadata GETs and downloads the cloud PDF into
memory, verifies SHA256 plus attachment MD5, and rechecks attachment metadata.
No old parent version is required: approved notes/tags may have advanced it.
Only existing credential-stripped allowlisted redirects are supported. No
proxy, retry, cloud write, OCR or analysis call occurs. Cloud reads have a
120-second aggregate timeout, with the existing tighter per-request bounds.
The conservative second download after a new process avoids treating an old
initial download or its hash as evidence that cloud recovery still works.

The exclusive intent is durable before unlink. Only the two named paths may
be unlinked, after a fresh hash and inode/size/time check. There is no glob or
recursive deletion. Missing files without a valid intent are errors. Symlinks,
changed files, changed artifacts, unknown states and cloud mismatches stop.

After interruption, inspect journals and the read-only plan. Explicitly resume
only this retention operation with fresh cloud verification:

    millefeuille standalone-retention --run-dir PATH --live --reconcile

Already completed calls validate the receipt and local absence without replay.
A local receipt is historical evidence, not proof of current cloud existence.
Explicit fresh re-verification after completed retention, without local writes:

    millefeuille standalone-retention --run-dir PATH --verify-cloud

## Future visual inspection

Python API: from millefeuille.domain.standalone_retention import verified_pdf.
Use with verified_pdf(run_dir) as path: and consume path inside that context.
It performs fresh identity/download/hash checks and exposes a private temporary
PDF; normal completion or exceptions remove it. It does not call OCR itself.
Abrupt process death can leave an OS temporary file; no crash-proof secure
wiping or forensic erasure is claimed. Do not copy the file or retain the path.

The source-only implementation needs real-paper qualification. POSIX O_NOFOLLOW,
O_DIRECTORY and owner/mode checks are currently required for retention. Windows
retention is not qualified; no silent weaker fallback is supplied. Trusted
private roots and cooperating writers are required, not adversarial same-user
race resistance. Receipts are local hash bindings, not cryptographic signatures
against a malicious owner. Keep backups of the non-PDF provenance artifacts.
Standalone note writeback accepts deliberately absent PDFs only after complete
retention-record validation plus its unchanged original approval/artifact pins.
Protected canonical modes are not altered.

CLI adapter: run_standalone_retention_cli(argv), exit 0 for a validated plan or
result, exit 2 for failure; parent main.py dispatch registration is separate.
Direct module invocation is python -m millefeuille.cli.standalone_retention.
