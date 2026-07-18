# Retrieval And Index Contract

Millefeuille should make processed papers retrievable by agents without making
private source material harder to govern.

## Default Lane

OpenKB/PageIndex is the default local retrieval lane for selected paper content.
It receives only artifacts that passed the extraction, route, structure, and
quality checks required by the run profile.

The index stage must record:

- index target and local path or service id;
- source-pack ref and source hash;
- selected full-text ref;
- summary and paper-card refs;
- chunking or page-indexing profile;
- duplicate-scan or collision results when available;
- skip reasons for disabled lanes.

The offline fixture-first status shape is
`millefeuille-retrieval-index-status/v0.1`.

## Optional Lanes

ConDB may be used for hierarchy or tree experiments. A ConDB run must emit a
verdict artifact explaining whether it improved repeated retrieval,
classification readiness, or inspection compared with OpenKB/PageIndex and
direct file search.

ChatIndex may be used for bounded workflow recall, classification discussion
history, or decision-history recall. It is not the default paper index unless a
later Speculoos task promotes it.

## Retrieval Command

The read-only `retrieve` command queries a verified paper package by:

- paper id, source-pack slug, Zotero key, normalized DOI, or normalized exact
  title;
- page or exact section locator;
- summary scope;
- classification evidence need;
- index lane.

It requires a run id, validates the source-pack manifest, stage manifest,
artifact index, paper card, summary bundle, and index status before returning
anything, and fails closed when a DOI/title lookup is missing or ambiguous.
Section/page filters operate on structured summary source locators. The command
returns refs into the artifact package rather than pasting full private paper
content.

## Batch Retrieval

`retrieve --batch-manifest <path>` accepts
`millefeuille-retrieval-batch-manifest/v0.1`. The strict JSON object has one
traversal-safe `batch_id` and a non-empty `runs` array. Each run has a safe
`run_id` plus exactly one of `paper_id`, `item_key`, `slug`, `doi`, or `title`;
all other fields and non-string locator values are rejected. DOI and title use
the same normalized exact-match behavior as single-run retrieval.
`--batch-manifest` must not be empty and cannot be combined with direct
locators or `--run-id`.

The command applies any supplied summary-scope, grain, index-lane, section,
page, or classification-evidence filter uniformly. Before writing, it resolves
and verifies every locator and package, including canonical index refs and
selected summary text refs. A malformed later entry, missing or ambiguous
identity, duplicate resolved `(paper_id, run_id)` pair, package drift, symlink,
absolute path, backslash, or traversal segment aborts the entire batch without
an aggregate artifact.

Successful preview writes:

- `batches/millefeuille/<batch-id>/retrieval/batch-retrieval-result.json`
- `batches/millefeuille/<batch-id>/retrieval/batch-retrieval-report.md`

The JSON follows `millefeuille-retrieval-batch-result/v0.1`. Runs are sorted by
paper and run identity; matched summaries and index lanes are also sorted.
Every file or directory ref is slash-normalized and relative to the supplied
source-pack root. Summary entries are allowlisted to `grain`, `scope`, and
portable `text_ref`; artifact-controlled `summary_id` values are excluded.
Index lanes are allowlisted to `lane` and `status`. The result contains source
hashes, locator types, per-run status,
acceptance status when present, optional classification/writeback refs, and
aggregate counts. It does not load or paste summary text, paper-card prose,
PDFs, or provider payloads. On supported POSIX local filesystems, batch
preflight opens one pinned source-root descriptor, traverses every descendant
component through descriptor-relative no-follow operations, and parses JSON
from held regular-file descriptors. Corpus enumeration and referenced artifact
verification use that same pinned root. It snapshots stable input identities,
missing optional inputs, and corpus entries; the external batch manifest is read
no-follow and retained byte-for-byte. Publication creates the batch hierarchy
relative to a pinned descriptor and locks the
pinned batch-directory inode. Both files are created exclusively inside a
descriptor-relative mode-`0700` temporary generation and kept open through owned
read/write descriptors. Before publication, Millefeuille validates the exact
entry set, rereads both expected byte streams, rechecks names, inode identities,
metadata, and single-link counts, changes files to mode `0444` and the directory
to mode `0555`, then repeats the held-descriptor verification. The atomic
no-replace rename into the stable `retrieval/` path is the publication commit
point; parent-directory fsync follows for durability. Pre-commit failure cleanup
truncates and fsyncs only the owned staged inodes through those held descriptors,
so a raced external hard link cannot retain aborted aggregate bytes. It does not
unlink, rename, or remove namespace entries because an uncooperative same-UID
replacement cannot be conditionally mutated atomically; unverified entries stay
untouched. The failed temporary generation remains in its reached mode (`0700`
or `0555`) with owned outputs at zero bytes for explicit operator cleanup.
Identical verified inputs and filters reopen, verify, and reread existing
read-only no-follow single-link regular files before a no-op; incomplete,
writable, drifted, symlinked, hard-linked, displaced, or substituted output
fails closed. Immediately before an exact-rerun no-op or atomic rename, while
the cooperative batch lock is held, Millefeuille reopens and revalidates every
snapshotted input and corpus entry and rereads the external batch manifest.
Namespace, entry-set, hard-link, or same-inode changes observed before that
final check fail closed.

The lock serializes cooperating Millefeuille writers only. POSIX `flock`, mode
bits, and descriptor checks cannot stop an uncooperative same-UID owner from
mutating inputs or staging after the last validation and before rename, or from
mutating published files. This contract therefore requires trusted ownership,
cooperative same-UID writers, or stronger immutable/content-addressed storage;
it does not claim protection against that adversary. The atomic rename is the
completed-operation boundary inside this stated trust model. Platforms or
filesystems without the required POSIX no-follow, descriptor-relative,
directory-lock, and no-replace-rename primitives fail closed before aggregate
publication; package import and legacy single-run retrieval use their portable
legacy read fallback when POSIX `O_NOFOLLOW` is unavailable.

## Acceptance

The acceptance stage should be able to verify that required index updates exist
or were intentionally skipped. A paper can be classification-ready only when the
configured retrieval/index requirements are satisfied or explicitly waived in
the stage manifest.

## Manual Gates

OpenKB writes, PageIndex/ConDB/ChatIndex index writes, provider/model calls,
Zotero writeback, source-pack writes, GitHub publication, release tags, and
package publication require explicit approval. Read-only local inspection of
already-created artifacts is allowed in preview mode.
