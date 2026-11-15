# Ordinary F05 recovery receipts

The two original process-death methods emit kind F05 through

defined short frames in tests/fixtures/evidence_transport.py. Each frame starts
MILLEFEUILLE_EVIDENCE_FRAME_V1: and is <=8192 UTF-8 bytes (excluding log timestamp
and newline). Canonical JSON is base64-chunked in 5400-byte pieces with zero-based
index/count, total byte_length, SHA-256 and identical content-addressed id.
Each canonical receipt is <=3 MiB. Frames must be ordered with bounded interframe log noise (see transport contract); missing,
reordered, conflicting, oversized, noncanonical or hash-mismatching data fails.

## Independent acceptance

Use the stdlib-only archive processor, never product imports:

    python scripts/validate_evidence_archive.py --zip LOGZIP --manifest TRUSTEDJSON --output OUTPUTDIR

OUTPUTDIR must not exist. Only evidence.json is generated, never ZIP extraction.
The parent independently verifies the archive's run, attempt, head, job names,
exact tree hashes and worker literal hash and prepares the trusted manifest.
Do not derive trusted expected values from the receipts being accepted.
See docs/evidence-log-transport.md for the machine-readable manifest contract.

Only canonical job/[number]_Run unit tests.txt members are consumed, never
job-level log copies. Byte-identical complete receipts deduplicate by enclosing
cell/kind/method (or S04 case); conflicting duplicates fail. Exactly two F05
methods per each of six named cells are mandatory. ZIP bounds: 128 MiB compressed
and aggregate uncompressed, 1024 entries, 24 MiB/member, 64 KiB input log line,
60-second archive processing deadline; per-step framing has 30 seconds and 64
complete receipts maximum. Timestamp-prefixed frame lines are supported.

accept(bytes, external bindings...) is the independent acceptance entry point.
validate(dict) is only a low-level synthetic shape/integrity checker; fixtures
with blank source/protected bindings must never be advertised as acceptance.
Acceptance requires exact named nonempty source hashes, external runtime identity,
method/seam/worker/position binding, complete protected initial inventory outside
the run, exact captured transaction file set, unchanged custody and initial
retained material, journal inventory, actual file/directory/byte counts, readback,
retry and recomputed net effects. It is not a signature or an anti-fabrication
proof. The parent must still inspect actual before/death/retry material.

Snapshot bounds remain 160 combined entries, 128 KiB/file and 512 KiB total.
Only caller-owned quiescent nonprivate synthetic fixtures are captured. Source
hashing covers the original six paths plus transport, transport tests and CLI,
each bounded to 512 KiB. Runtime identity contains only exact GITHUB_SHA,
GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, RUNNER_OS and Python major.minor. Missing values
are permitted for synthetic helper work only, never archive acceptance.

The original assertions, Windows displacement handling and real os._exit(71)
remain unchanged. Net snapshots are not syscall counts, fsync proof, universal
power-loss durability, arbitrary crash-window or R6/shared-guard qualification.
No runtime evidence is produced by source changes or static checking.

The only additional non-suffix snapshot entry is a regular file at
source-packs/zotero/<paper-id>/analyses/millefeuille/run-fixture/.card-index.lock.
Its actual empty or nonempty bytes and identity are captured under the same
no-follow, entry, per-file and aggregate bounds. Other .lock files, misplaced
locks and symlinks remain rejected. The entire transaction subtree is still
captured and exact inventory, source and protected bindings are unchanged.


Producer-path correction: source_packs.write_source_pack_from_recovered_pdf
sets source_pack_dir = source_pack_root / "zotero" / paper_id. The real fixture
then appends analyses/millefeuille/run-fixture. The former six-component allowlist
and its toy unit fixture omitted the zotero namespace and rejected the actual
seven-component generated lock. The replacement is specific to that producer
layout, not a general recursive-lock allowance. A new regression test calls the
same _prepare_card_index_fixture as the original crash tests and snapshots its
exact returned run directory, rather than guessing a second fixture layout.
Unknown-data diagnostics now include only a bounded repr of the synthetic path
relative to the supplied fixture root, never an absolute root or file contents.
No original crash worker/assertion, production rule, count, source/protected
binding, byte bound or archive acceptance condition is weakened. This change is
source-only until freshly authorized runtime validation has actually passed.


## Checkout bytes and retained validation failure

The thirteen exact F05/S04 source-binding paths now declare text eol=lf in
.gitattributes, so future checkouts retain the Git-blob bytes even on Windows.
This is an artifact-side checkout rule, not receipt normalization: raw-byte
source equality remains mandatory. It cannot retroactively accept a historical
CRLF receipt, change its frozen manifest or upgrade a refused archive.

Run 43575513843 passed all six CI jobs but the strict archive was refused.
The index-publication crash boundary legitimately precedes card exchange; the
original recovery retry later retains the original card inside that same
transaction. The original test checks all earlier retained entries unchanged,
while accept() additionally requires identical whole transaction file sets
after death and retry. These differ: retry adds card-exchange.json on Linux
and displaced-card.json on Windows. The checkout repair alone did not authorize changing that cross-stage equality
requirement. The separately approved prospective amendment below changes it;
no production change, snapshot omission or historical acceptance follows.

## Owner-approved prospective custody amendment, 2026-11-14 18:32 KST

The owner explicitly approved changing cross-stage transaction-set equality,
not a transparent or nonweakening repair. Local source/static review only is
authorized. The historical run 43575513843 archive remains REJECTED, including
its Windows raw CRLF source mismatch (six jobs passed, 7224 tests, 5847 passed,
1377 skipped, zero failures/errors). No qualification or programme acceptance.

Only externally bound Linux/Windows and the two original method/seam pairs are
accepted. Before has zero transaction files/directories. Death/retry have exactly
one transaction directory. Relative to it, index publication death contains only
index-status.json; retry contains that unchanged entry plus exactly the original
card as card-exchange.json (Linux) or displaced-card.json (Windows). Card-exchange
death and retry both contain precisely index-status.json and that OS-specific
original-card filename. custody.transaction_files remains the exact death set.
No schema allowance, arbitrary additions, removals, sibling transactions, nested
files or extra directories are admitted. Complete stage file sets equal initial
files plus the canonical index, exact run/.card-index.lock and exact stage
transaction set; directory sets preserve initial directories and add only missing
canonical-index parent and transaction ancestors. The initial run has neither
the lock nor canonical index directory. The lock is an existing producer-run
artifact, not a transaction entry. Its canonical one-NUL byte (AA==), size 1,
SHA256 and valid identity are captured, with the full death FileImage unchanged
at retry and identity distinct from canonical card/index and every retained
transaction image. No other lock path or extra directory is admitted.

Every old FileImage remains unchanged. The retained original equals the before
canonical card's full bytes_b64/hash/size/device-inode FileImage. At the index
seam the death canonical card also equals that full original. Every retained
transaction image is identity-distinct from current canonical card and index at
its stage, and retained index bytes equal canonical index bytes. All existing
raw nine-source, runtime, method, worker, exit71/position, protected initial
inventory, readback, journal, count, net-effect, entry/file/aggregate/receipt/frame
and archive bounds remain mandatory. Original production/crash semantics and
assertions are untouched. New synthetic test source is not executed CI evidence.
No runtime/helper tests, imports, CI, publication, provider/network operation or
retroactive acceptance is authorized here. R6/S07/scientific/release holds remain.
