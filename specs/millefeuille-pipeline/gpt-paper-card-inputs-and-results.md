# Verified GPT paper-card input and result handoff

## Input bytes

`load_published_gpt_summary_card_inputs` uses the same held byte buffers that
the publication verifier checks against the trusted root commit identity.
There is no unchecked second read of snapshots or summary text. The existing
metadata-only handoff API remains unchanged. All summary record references
must match the verified text file set exactly, without duplicate or missing
entries. Private input bytes stay out of object representations.

`plan_published_gpt_paper_card_request` feeds those verified buffers to the
pure card request builder. It binds the complete immutable summary bundle,
source hash, source pages, exact input bytes and explicit card run ID. It
performs no provider calls, receipt reservations, or writes. The supplied
expected publication identity must come from a trusted commit record.

The actual Picard read-only check on GCP passed for ten summary units, ten
summary provenance records, and two source snapshots. The one-card preview
uses nine pages and a 34,112-byte prompt for `openai/gpt-5.6-sol`, `xhigh`,
subscription OAuth, one attempt and no fallback. Its explicit run ID is
`run-picard-gpt-card-20260926-01`; the request-plan fingerprint is
`sha256:0421c01e0dff1fb92e7b7923e1f429c875f2c81c31442dadb8f5e585a6f06a86`.
This check generated no card text and changed no paper artifacts.

## Result evidence

`validate_published_gpt_paper_card_execution` replans the immutable source
publication and rejects any plan drift. It applies the generic executor
contract, verifies exact output bytes and hash, validates the card's bound
identity and citations, requires complete actual token usage, and requires
an explicit zero incremental provider cost. Model, authentication, schema,
request, output or usage drift fails closed.

The resulting `millefeuille-gpt-paper-card-provenance/v0.1` plan contains only
paper/run/source fingerprints and typed request/result metadata. Paper text
and card content are excluded. Its schema references the existing executor
schemas, while the original summary provenance v0.1 remains summary-only.
This is transient execution evidence, not an authenticated approval or
whole-paper acceptance verdict. Synthetic test evidence is never presented
as a live card result.

## Remaining live gates

The trusted MF-100 `model.card` execution reservation and runner, canonical
card/source-run assembly, exact output write authorization and publisher,
paper acceptance, index/duplicate checks, classification and Zotero writeback
remain separate work. The published Picard summary bundle stays immutable.
The current handoff is single-source; MF-114 multi-source propagation remains
open. Grok execution is deferred while its subscription is inactive.
