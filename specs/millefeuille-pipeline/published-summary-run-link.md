# Reusing an immutable published GPT summary

`plan_published_gpt_summary_run_link` verifies a committed summary publication,
its original input and provenance files, current source evidence, and the
consuming card's hash-bound publication manifest. It returns one canonical
metadata file in memory at
`analyses/millefeuille/<card-run>/summaries/hierarchical-summary.json`.
It performs no writes or provider calls. Saving this metadata requires its own
applicable operator write gate; the file supplies no execution or write approval.

## Identity and provenance

The persisted schema is `millefeuille-published-summary-run-link/v0.1`, distinct
from a generated hierarchical summary. It binds the full original publication
identity, card write manifest digest, source hash, original record digest, and
normalized source-root-relative evidence references. The card run must differ
from the original generation run. Links have a 32 KiB read limit and may only
occupy the canonical global run path. Hierarchical-summary dispatch bounds its
initial JSON metadata read at 4 MiB; the link reader then applies the 32 KiB cap.

`load_hierarchical_summary` revalidates that link and returns a derived
`millefeuille-published-summary-run-view/v0.1`. The view's `run_id` identifies the
consumer and `origin_run_id` identifies the unchanged GPT generation. Unit IDs,
dependencies and locators remain unchanged. Text and optional per-unit provenance
references point to original verified files. Publication-level provenance is
verified against the original manifest even when units have no per-unit field.
The original record, Markdown, model provenance and generation run are never
rewritten or copied. The view cannot be materialized as a newly generated summary.

## Downstream use

Index dependency checks, offline resume, paper-card dependency checks, artifact
reference assembly, and run-scoped acceptance summary checks use this derived
view. Acceptance reports the original generation run and link digest. Passing
that summary check does not imply whole-paper acceptance: source, indexing,
duplicate evidence, taxonomy and later writeback gates remain independent.

The current card JSON must still match the original hash after restoring its
planned index state in memory. A separately governed index refresh may change
only `index_state`; that reader does not verify the index receipt. Current card
Markdown must exist as a regular readable file, but its rendering is not a
content authority here and is not compared to the original Markdown hash.
The card provenance and request metadata must remain byte-identical.

The link is operator-controlled data with a cooperative filesystem contract.
It does not establish a live Zotero identity or authorize model execution,
source-pack publication, OpenKB/PageIndex writes, classification, or Zotero edits.
