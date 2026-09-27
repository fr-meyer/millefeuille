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

Published-link batch publication remains unsupported because its complete
publication-verification footprint is not enlisted in the batch's pinned-root
input snapshot. It fails before publication; existing inline batch guarantees are
preserved. Complete batch snapshot integration and generated query answers remain
separate acceptance requirements. This repair performs no provider calls, native
reindex, Zotero mutation, OCR operation or source-pack write.
