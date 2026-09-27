# Component model boundaries

## GCP inventory observed 2026-09-27 UTC

These are distinct routes. An installed package default is not evidence that a
Millefeuille job used that model. Configuration inspection performs no model call.

| Component and operation | Configured or default model | Evidence and qualification |
| --- | --- | --- |
| Millefeuille local structure preparation | No model | `local_structure.py` uses `local-markdown-headings-v0.1`; the research profile now matches this builder. |
| Millefeuille page, section, whole-paper summaries, cards and classification | `openai/gpt-5.6-sol`, `xhigh` | Typed requests require subscription OAuth, reject API keys and have no fallback. GPT-only dispatch rejects foreign profile defaults before transport. |
| Millefeuille native pilot index materialization | No new model call | The five completed pilots reuse verified saved summaries and structure. They do not invoke vendor compilation, `build_index`, concept/entity generation or an embedding model. This does not establish live generated-query coverage. |
| OpenKB installed package | `0.4.5` | Active `openkb-mvp` environment resolves to the dated `openkb-0.4.5-pageindex-0.3.0.dev3` runtime. |
| OpenKB native compile and native query | `mistral/mistral-medium-3-5` | The separate `.openkb/config.yaml` names this model. `openkb/indexer.py:183-203` passes the selected model to PageIndex; package fallback is `gpt-5.4`. This is not the Millefeuille OAuth route. |
| OpenKB separate OAuth query | `openai/gpt-5.6-sol`, `xhigh` | `.openkb/openclaw-oauth-query.json`; a separate adapter uses its selected OAuth executor. Native configuration does not describe this adapter. |
| PageIndex installed package | `0.3.0.dev3` | Active package metadata. |
| Standalone PageIndex local indexing default | `gpt-4o-2024-11-20` | `pageindex/config.py:12-37`; node summaries and document description are enabled by default. `index/pipeline.py:72-119` can therefore generate even for a Markdown level tree. Disabling those flags is insufficient for PDF content-based structure, which can still call a model. |
| PageIndex local retrieval | Explicit retrieval model, otherwise selected index model | `backend/local.py:26-39`; the model flows through the owning client. A hosted PageIndex service controls its own server-side generation; its API key is not OpenClaw subscription OAuth. |
| ChatIndex raw CTree build and load defaults | `gpt-4o-mini` | `ctree/ctree.py:162` and `:1697-1708`. This raw default does not describe the current wrapper. |
| Current ChatIndex LiteLLM sandbox build and query wrapper | Alias `gpt-5.6-sol-xhigh` | `scripts/chatindex-litellm-sandbox` explicitly passes build and retrieval models. `chatindex_session_sandbox.py:515-523`, `:558-574`, `:735-785` propagate them. The current LiteLLM alias maps to `openai/gpt-5.6-sol` with `reasoning_effort=xhigh`. This is an API/LiteLLM sandbox route, not proof of subscription OAuth. |
| Historical ChatIndex sandbox | `openkb-qwen` | Older project memory describes earlier Qwen jobs. It is superseded as a current wrapper default; no `openkb-qwen` alias was present in the current inspected model list. Saved historical jobs remain historical evidence. |
| OCR | `mistral-ocr-latest` | Current research profile and user preference use the rolling alias. Resolved dated IDs belong in observed provenance. Older pilot approval metadata may record a dated model; it is not a current default. OCR remains separate from non-OCR generation. |
| Embeddings, reranking, concept/entity enrichment | No such call in the verified pilot materialization | Do not infer a selected model from package presence. These future operations require explicit operation-specific planning and observed evidence. |

Configuration paths in the inventory refer to the active research KB. The dated
private audit manifest retains the exact host paths and source hashes.

No private configuration values, keys, paper text, prompts or model outputs are
included here. Mutable configuration must be rechecked before an approved live job.

## Enforced paths and regression evidence

`models --json` describes local structure with `model=none`, `provider=none`,
`auth_lane=none`, `fallback_policy=none` and no recorded model usage. The backend
comes from the same identifier used by the actual deterministic builder.

`test_millefeuille_component_boundaries.py` runs the real offline CLI pipeline
through indexing in a fresh interpreter that rejects imports of OpenKB,
PageIndex, ChatIndex CTree/retrieval and vendor SDKs. HTTP, socket and child-process
transports are denied. It then builds actual grounded summary, card and accepted-run
classification requests and checks their model, OAuth and fallback fields. Injecting
the observed foreign component defaults into each generation profile fails before
transport. Existing executor tests separately validate actual runtime model, OAuth,
usage, input identity and result identity.

The legacy PageIndex HTTP tree client remains an explicitly configured compatibility
client. The lifecycle local structure and fixture index paths do not select it.
An eventual semantic structure or generated index enrichment adapter must bind an
explicit typed GPT OAuth request to its input and record actual model evidence.
Calling the vendor defaults is not an authorized substitute for that adapter.

## Limits

This audit establishes source/configuration boundaries and offline regressions.
It does not rerun the completed papers, call a provider, generate retrieval answers,
reindex a production KB, change credentials or publish a stable release. Actual
retrieval/query acceptance and broader source/edge-case coverage remain planned.
