# Read-only retrieval of published GPT summaries

The single-run retrieval API and CLI accept a canonical verified
millefeuille-published-summary-run-link/v0.1 at the consuming card run's
summaries/hierarchical-summary.json. Inline hierarchical summary records retain
their existing format and traversal checks.

## Verification and lineage

Retrieval checks the run's source, card and canonical index identity, then reuses
load_published_summary_run_view to verify the original publication, its source and
preparation evidence, card write manifest, summary record, text and provenance.
The view's paper/run/source identity must match the selected retrieval package.
A saved derived view cannot serve as a verification-bearing input artifact.

The response adds these metadata fields for verified linked summaries:

- summary_schema_version identifies the published-summary-run-view/v0.1;
- summary_origin_run_id preserves the original generation run;
- summary_link_sha256 and source_summary_sha256 preserve the verified bindings;
- source_summary_ref points to the unchanged original record, relative to the
  selected card run.

Summary text and optional provenance refs resolve to the original generation.
They may cross from the consuming run to that origin run, but the adapter confines
these derived refs to the verified origin publication and checks regular files
without following symlinks. Inline records keep their stricter local relative-ref
rules. No summary text is copied or regenerated, and retrieval returns metadata
and refs rather than paper or summary content.

## Published package layout

The five completed pilots store consuming card packages under the source root's
analyses/millefeuille/run directory. Select that run explicitly with artifact_root
or the CLI's --artifact-root option, using its canonical card paper_id and run_id.
The artifact index binds the selected root; a different host/container path spelling
is not an identity override. On GCP the published packages use the container path
namespace. Source item-key lookup uses the identity recorded in the immutable
source manifest; this change does not invent full-key aliases or rewrite sources.

Single-run paper, source item-key, slug and corpus title locators retain their
existing behavior. Page/grain, classification-scope and index-lane filters operate
on the original saved locators. CLI JSON and API metadata use the same reader.

## Acceptance evidence and remaining work

A GCP acceptance run passed 40 identity, filter, API/CLI and unknown-title checks
across five real pilots and all 166 saved summary refs, with network transports
denied, acceptance-pass metadata and unchanged hashes for all 704 source files.
Synthetic regressions cover real publication/card fixtures, canonical index
refresh, API/CLI parity, filters, original text/provenance/source/link tampering,
origin boundaries, rejection of saved views and inline compatibility.

Published linked summaries are supported by the explicit batch v0.2 contract
below. Batch v0.1 keeps its original inline format and rejects linked summaries.
Generated query answers remain a separate acceptance requirement. Single-run
retrieval performs no provider calls, native reindex, Zotero mutation, OCR
operation or source-pack write.

## Published summary batch v0.2

Use millefeuille-retrieval-batch-manifest/v0.2 with the usual batch_id and runs.
Every run requires exactly one paper_id, item_key, slug, DOI or title locator,
its run_id, and artifact_run_ref equal to analyses/millefeuille followed by that
run_id. The ref is relative to the selected source root and is a package locator;
it cannot override identity. Absolute paths, traversal, a missing ref and a ref
for another run are rejected. V0.2 requires verified published summary links;
v0.1 continues to handle inline packages unchanged.

The protected root reader is explicitly passed through preparation verification,
route/structure evidence loading, bound source reads, dispatch verification,
original publication/text/provenance verification and card verification. The
original publication's complete directory census is captured through held root
descriptors. Both every read input and the directory census are revalidated before
the existing atomic no-replace commit. Adding an original publication file or
changing any verified dependency after preflight prevents the result generation.
No ambient reader state, provider dispatch or model output regeneration is used.

The v0.2 result retains original summary_id and source_locators along with portable
text refs into the original run. Optional model_provenance_ref is retained when
present in the source record. Each consuming run also includes the single-run
lineage fields above, with source_summary_ref normalized relative to the source
root. Result sorting is deterministic, independently of manifest input order.
The JSON/Markdown output contains refs and metadata, never source or summary text.

Contracts: [manifest v0.2](retrieval-batch-manifest-v0.2.schema.json) and
[result v0.2](retrieval-batch-result-v0.2.schema.json). Their v0.1 counterparts remain
unchanged. The CLI uses retrieve --batch-manifest with the same contract as the API.
Output publication uses the established cooperative lock, protected staging,
complete input revalidation and atomic commit. Its documented trusted-owner or
cooperative-writer boundary remains unchanged.

Eight synthetic regressions exercise actual new batch publication and exact rerun,
lineage with empty filters, duplicates and late missing runs, invalid run refs,
explicit reader propagation, every linked dependency mutation, census additions,
wrong roots and symlinks. The focused retrieval/publication suite passes 74 tests
(1 skipped). Five actual pilot public batch API/CLI invocations pass complete
precommit snapshot checks for all 166 summaries and five acceptance-pass packages,
including reversed order, page and classification/index filters. Transports are
blocked and all 704 source hashes remain unchanged. That actual acceptance run
intercepts publication before commit: it performs zero durable source-pack writes.
Actual combined-result publication and generated GPT query answers remain separate
live acceptance work; synthetic transaction coverage is not claimed as a live write.
