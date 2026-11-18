# Standalone one-paper processing

Python 3.11+. The default clean-start lifecycle requires a trusted POSIX
filesystem and cooperating writers; Windows qualification is not claimed.
Install the package and its declared
Python dependencies in your own environment. No OpenClaw, GCP, broker, socket,
privileged service, or machine-specific directory is required.

## Current default: clean-start processing

Normal live processing requires an explicitly initialized clean taxonomy store
via --taxonomy-store, not --taxonomy. Follow [clean-start](clean-start.md) for
reviewed seed or empty initialization, credentials, final selection and replay.
It performs taxonomy-free scientific analysis, final classification and card
export before recording completion. PDFs and artifacts are preserved pending
actual cloud-hash qualification; --keep-pdf is not needed on this default path.

The taxonomy-based analysis and automatic retention sections below describe only
the explicitly selected --legacy-analysis-only path, not the current default.
Neither mode implies end-to-end or scientific-quality acceptance.

## Read-only discovery

Set ZOTERO_LIBRARY_ID and ZOTERO_READ_KEY in the process environment using your
platform's secret/environment mechanism. The key needs library read access,
not write access. Do not put secrets in command arguments or shell history.

```sh
millefeuille process --tag millefeuille --max-items 1 --output-dir ./papers
```

This makes bounded Zotero metadata GETs only. It writes no files, downloads no
PDF and calls no model. It selects the newest exact-tagged parent, never an
unbounded scan. Use --item-key KEY to bind a specific tagged parent after
reviewing discovery. Group libraries require --library-type group.
Exactly one Zotero-stored PDF and at most ten children are supported. Ambiguous,
linked-only, missing or oversized sources stop, not silently skip to another
paper. Existing classification tags are not evidence of processing success.

## Legacy analysis-only: explicit live local processing

Select --legacy-analysis-only explicitly. Also set MISTRAL_API_KEY. Supply a released, validated Millefeuille taxonomy
registry JSON via --taxonomy. By default install Codex CLI on PATH and run
`codex login` with your ChatGPT subscription. The CLI checks availability and
`codex login status` (15-second bound, output suppressed) before OCR spend,
then selects the native Codex analysis adapter with executor_argv=None.
The adapter remains responsible for strict subscription-auth and model checks;
a successful status command alone does not prove model access. No API-key or
alternate-model fallback occurs. For an alternative protocol-compatible executor,
supply --executor-command as a JSON argv array and authenticate it separately.
An arbitrary CLI is not necessarily compatible with the JSON executor protocol.

```sh
millefeuille process --tag millefeuille --max-items 1 --output-dir ./papers \
  --item-key YOURKEY8 --taxonomy ./released-taxonomy.json \
  --legacy-analysis-only --live
```

Optional override: --executor-command '["/path/to/protocol-compatible-executor"]'.
That path is a placeholder, not a bundled executable. On Windows JSON paths use escaped backslashes
(or forward slashes); quote the JSON according to your shell. The executor runs
without a shell through the analysis adapter. --live is explicit operator
approval for this bounded local processing path and provider disclosure/spend,
not a receipt for legacy protected live-operator commands.

The frozen adapters are direct_mistral.extract_pdf and
direct_analysis.analyze_paper. OCR requests mistral-ocr-latest (180 seconds,
250 pages, 50 MB); analysis requests openai/gpt-6-astra, xhigh (300 seconds).
No model substitution, placeholder classification or fixture fallback exists.
The adapter executor protocol and account authentication must be provisioned by
the operator; the CLI does not extract OAuth credentials or install executables.

## Evidence, replay and safety

Each library/item has an exclusively created directory under output-dir. It
temporarily contains source.pdf, a real-live source manifest and an immutable run
journal. Only legacy analysis-only processing defaults to verified removal of source.pdf
and the OCR original.pdf copy, while retaining the Zotero cloud original.
On that legacy path, use --keep-pdf to retain both local PDFs. The default
clean-start path preserves them regardless. See
[retention and reconciliation](standalone-retention.md). A retention failure
returns exit 2 and preserves its journals; 06-completed alone is processing
evidence, not no-PDF completion. The run retains exact discovery JSON, selected Zotero metadata/version,
PDF hash, taxonomy snapshot, original OCR response, fulltext, summary,
classification and adapter evidence. These files contain private research data:
use a private trusted local directory, restrict Windows ACLs as appropriate,
and do not commit them. Output directories must not contain symlinks/junctions.
Only cooperating writers in a trusted local filesystem are supported; this is
not a defense against another process running as the same user.

Metadata bodies are capped at 1 MB, PDF at 50 MB, each Zotero operation at
60 seconds with 20-second socket timeouts. No retry is automatic. Only fixed
Zotero API URLs are queried. A single PDF redirect to allowlisted Zotero storage
is permitted with the Zotero credential removed. Arbitrary attachment URLs,
proxy environment settings and additional redirects are refused.

The paper directory itself is the reservation. A repeated invocation refuses
before downloading or spending again. A started journal stage without its
completion record means an uncertain outcome, not permission to rerun. Retain
all evidence and reconcile provider/executor history manually. Do not delete
reservations or change output roots to bypass this protection. There is no
automatic resume or cross-root/global deduplication. A different output root
can incur duplicate spend, so use one durable root per library.

The manifest is explicitly millefeuille-standalone-source-pack/v1. Existing
canonical source-pack schemas hardcode fixture intake provenance; this command
does not mislabel live evidence as a fixture, nor claim canonical lifecycle
stage-manifest/artifact-index acceptance or retrieval integration. Its success
means the real adapters returned nonempty, hash-bound local outputs, not that
scientific quality, indexing or governed writeback passed. Review the summary,
classification, OCR coverage and model provenance. Full canonical lifecycle
integration is separate work.

No Zotero write key is read or required. No queue tag is removed, no success tag
is added, and no notes/collections are written. Original Hydra commands and
protected live-operator behavior remain unchanged.

Exit 0 reports discovery/no-match or local processing completion explicitly;
exit 2 reports refusal/failure. Provider exceptions are sanitized so signed URLs,
credentials and private text are not printed. Always inspect the last retained
journal stage after interruption. Real-paper acceptance must use actual Zotero,
Mistral and selected-model outputs; compilation/lint is not functional proof.

## Legacy completed-run discovery and retention

Successful legacy `process --legacy-analysis-only` completion calls `finalize_completed_run`: it creates a
source-backed portable paper card, optionally exports that card, then removes the
two known local PDF copies after verified Zotero recovery. `--keep-pdf` is an
explicit override, not the default.

Use `--memory-dir /YOUR/SEARCHABLE/CARDS` for a managed Markdown export into your
host memory system. `--memory-project-comment` is optional and only appropriate
for project-scoped records; ordinary scientific paper cards need no project scope.
The core CLI has no OpenClaw, embeddings, or PageIndex dependency. Card creation
is not proof that its host indexed it or that semantic retrieval is functioning.

Install a released taxonomy with `taxonomy-store --snapshot PATH --store-dir DIR
--markdown`, then pass the returned immutable version JSON to `process --taxonomy`.
Per-run snapshots and locks remain unchanged.

Existing completed artifacts can use `paper-card --run-dir PATH --memory-dir DIR`
and `standalone-retention --run-dir PATH --live` without repeating OCR or model
analysis. See the dedicated retention, paper-card, taxonomy-store and Decisions
classification documents for the exact safety boundaries.
