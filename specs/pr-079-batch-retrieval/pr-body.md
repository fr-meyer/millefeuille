## Summary

Implements Speculoos task `pr-079-batch-retrieval`: deterministic offline multi-run retrieval over verified local source-pack packages.

- accepts a strict `millefeuille-retrieval-batch-manifest/v0.1` through `millefeuille retrieve --batch-manifest`
- supports paper-id, Zotero-key, source-pack-slug, normalized DOI, and normalized exact-title locators
- preflights every locator, package, identity, canonical ref, and output path before aggregate writes
- rejects unknown or non-string fields, unsafe locators/refs, missing or ambiguous identities, and duplicate resolved runs
- applies existing scope, grain, index-lane, section, page, and classification-evidence filters uniformly
- emits sorted source-pack-root-relative allowlisted refs/status metadata as strict v0.1 JSON plus Markdown, excluding artifact-controlled summary IDs
- pins one source-root descriptor across preflight, performs descendant and corpus reads through descriptor-relative no-follow operations, snapshots every inspected file, truly absent optional input, and enumerated corpus entry, and rejects optional paths that exist as directories, FIFOs, or other non-regular entries
- rereads the external batch manifest and reopens/revalidates every snapshotted source input under the cooperative batch lock immediately before an exact-rerun no-op or atomic commit
- pins and revalidates the batch-directory inode, locks its descriptor, verifies the complete unpublished staging generation through held read/write descriptors, makes it read-only, and uses the atomic no-replace rename as the commit point
- retains separate read-only verification and private read/write cleanup descriptors for each staged inode; on pre-commit failure it truncates/fsyncs the owned inode directly even if its staged name was displaced or replaced, so raced external hard links retain no aborted bytes while unverified namespace entries remain untouched
- treats exact reruns as verified read-only byte-stable no-ops and fails closed on incomplete, writable, drifted, linked, displaced, or substituted output; mutations observed before the final precommit check fail closed
- states the enforceable trust boundary explicitly: POSIX locks and mode bits serialize cooperative writers but cannot prevent an uncooperative same-UID owner from racing after the last check, so trusted ownership, cooperation, or stronger immutable storage is required
- fails closed before aggregate publication when required POSIX no-follow, descriptor-relative, directory-lock, or no-replace-rename primitives are unavailable, while preserving portable legacy single-run fallback reads that reject parent traversal, target/parent-path symlinks, non-regular entries, and pre-read lstat/open identity swaps
- never includes private paper, summary, card, PDF, or provider payload content

## Changed Files

- `.speculoos/manifest.yaml`
- `.speculoos/surfaces/github.yaml`
- `.speculoos/surfaces/vibe-kanban.yaml`
- `.speculoos/tasks/pr-079-batch-retrieval.yaml`
- `README.md`
- `millefeuille/cli/stages.py`
- `millefeuille/domain/artifacts.py`
- `millefeuille/domain/card_fixtures.py`
- `millefeuille/domain/index_fixtures.py`
- `millefeuille/domain/retrieve.py`
- `millefeuille/domain/secure_io.py`
- `millefeuille/domain/source_packs.py`
- `millefeuille/domain/stage_runtime.py`
- `millefeuille/domain/summary_fixtures.py`
- `specs/millefeuille-pipeline/README.md`
- `specs/millefeuille-pipeline/cli-contract.md`
- `specs/millefeuille-pipeline/remaining-work.md`
- `specs/millefeuille-pipeline/retrieval-batch-manifest.schema.json`
- `specs/millefeuille-pipeline/retrieval-batch-result.schema.json`
- `specs/millefeuille-pipeline/retrieval-index-contract.md`
- `specs/pr-079-batch-retrieval/commit-message.txt`
- `specs/pr-079-batch-retrieval/pr-body.md`
- `tests/test_millefeuille_contract_artifacts.py`
- `tests/test_millefeuille_retrieval_batch.py`
- `tests/test_millefeuille_retrieve.py`

## Validation

- focused batch/retrieval/stage-CLI/contract suite — 73 passed
- full unittest discovery — 313 passed
- Ruff — passed
- YAML/JSON metadata parse — passed
- `git diff --check` — passed
- Speculoos status, task validation, private-data scan, documentation sync, and publication checks — passed

## Documentation Impact

User-facing documentation in `README.md` and the pipeline contract packet now
defines the v0.1 manifest/result schemas, full-preflight and duplicate rules,
uniform filter behavior, pinned-root descriptor-relative preflight and
no-replace aggregate publication, POSIX fail-closed requirements, unpublished
held-descriptor byte/metadata/link-count revalidation, read-only generation
modes, final under-lock input snapshot revalidation, the atomic rename commit
boundary, the cooperative same-UID/trusted-ownership limitation, legacy
single-run fallback reads, descriptor-only failure scrubbing,
explicit cleanup of retained failed generations, root-relative portable refs,
portable single-run traversal/symlink/non-regular rejection plus pre-read
lstat/open swap detection, deterministic reruns, and the ref/status-only privacy
boundary.

## Release Flow

Feature PRs target `dev`; release/promotion PRs target `main` and remain separately
gated. PR #79 received exact-head Mergeguez approval and merged to `dev`; its
remote feature branch was deleted. No stable-branch change, release tag, package
publication, or production deployment is part of this slice.

## Delivery Closeout

- exact reviewed head: `c950bb07e6abadfd4d41426ecd0404e99eae49c3`
- zero-finding Mergeguez review: https://github.com/fr-meyer/millefeuille/pull/79#pullrequestreview-4728318466
- successful Mergeguez check: https://github.com/fr-meyer/millefeuille/runs/88071003328
- `dev` merge commit: `18c452bcd070d43ccd1e167164613e9262054f22`
- remote feature branch deleted by the approved merge lane

## Publication Boundary

Offline aggregate ref/status reporting over existing temporary fixture or
synthetic source-pack runs only. GitHub publication was limited to the completed
PR #79 review and `dev` merge lane. No private paper content return, live Zotero
access, PDF recovery, durable source-pack mutation, OCR/model/provider call,
worker-agent execution, OpenKB/index write, credential or permission change,
main-branch change, release, package publication, production deployment, or
approval bypass.
