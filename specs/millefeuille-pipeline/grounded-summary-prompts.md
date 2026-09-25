# Grounded Summary Prompts v1

`build_v1_summary_prompt` is the provider-free prompt builder for the bundled
`summary-page-v1`, `summary-section-v1`, and `summary-full-paper-v1` profile
versions. `plan_verified_summary_dispatch` adds the authoritative source-binding
header and exact input hash before execution planning. This builder makes no
model call and writes no paper text.

The builder accepts only the local Markdown headings structure backend. It
checks contiguous page line coverage, section anchors and heading levels, exact
work-unit IDs, and source-locator order. A page prompt contains that page's
lines. A section prompt contains its heading through the next peer or parent
heading on the same page, including nested subsections. A full-paper prompt
contains the selected Markdown in page order. Source text is encoded as JSON
data alongside its locators, and the instructions explicitly tell the model to
ignore commands found inside the source.

The builder never truncates input. It rejects a page over 64 KiB, a section
over 96 KiB, or a full paper over 512 KiB; larger sources need a reviewed
chunking plan. Each response must be the exact `v1` summary JSON object checked
by the result validator, with citations drawn only from the prepared locators.

This prompt template supplies source-scoped input for future exact batch
manifests. It does not authorize provider calls or durable summary writes.
