# GPT paper-card request planning

`plan_gpt_paper_card_request` builds one deterministic, provider-free request
from supplied verified Markdown, local structure, and published page, section,
and full-paper summary text. The caller must verify the publication and input
bytes before using this pure builder. It performs no file reads, OAuth lookup,
provider execution, receipt reservation, or writes.

The request binds the paper ID, run ID, source hash, published summary bundle,
all input hashes, exact prompt bytes, source pages, output contract, timeout,
and pinned model. `research-default.paper_card` selects
`openai/gpt-5.6-sol`, subscription OAuth, `xhigh`, fast mode off, one attempt,
no API key, and no fallback. Profile drift fails closed. The metadata manifest
and object representations exclude paper and summary text.

`paper-card-content.schema.json` describes the transient model output.
`validate_v1_paper_card_content` additionally checks exact paper/run/source
identity and ordered page citations. It rejects unknown fields, duplicate
JSON keys, nonstandard constants, oversized replies, malformed content,
invented page locators, and model-supplied index, classification, acceptance,
Zotero, or provenance fields. Source instructions are explicitly treated as
untrusted data in the prompt. Section summaries are allowed only when their
locators match the validated local structure; the card cites source pages.

Validated content is not provider execution acceptance and is not a canonical
`millefeuille-paper-card/v0.2` artifact. Existing canonical card and index
contracts remain authoritative. The summary execution and provenance v0.1
contracts remain summary-only.

Before a live card can be generated and saved, the downstream adapter still
needs verified published-input loading, an exact MF-100 `model.card` approval,
a trusted one-use execution reservation, actual model/usage/provenance
acceptance, canonical identity and evidence assembly, and a separately
approved exact write. The immutable published summary bundle must remain
unchanged. Acceptance, indexing, classification, and Zotero writeback follow
their own stages and scopes. Grok execution remains deferred.
