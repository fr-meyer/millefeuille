# OCR Backend Contract

OCR backends should emit evidence records, not just extracted Markdown.

## Adapter Interface

Every backend adapter should expose:

- `provider`
  - Example: `mistral`, `pageindex`, `native`.

- `provider_version`
  - Provider model/API version when known.

- `input_source_pack`
  - Source-pack identity, not raw bytes.

- `input_attachment_identity`
  - Zotero item key, attachment key, canonical filename, SHA-256, and Zotero
    version when present.

- `output_markdown_ref`
  - Local/source-pack/OpenKB reference to selected Markdown.

- `page_count`
  - Page count observed by the adapter.

- `coverage`
  - Extracted pages, blank pages, image-only pages, failures.

- `warnings`
  - Adapter warnings that affect confidence.

- `provider_payload_disposition`
  - Must state whether raw provider payload was discarded, quarantined, or
    retained outside committed files.

## Mistral OCR 4 Evidence Adapter

The Mistral OCR 4 adapter should be added after an approved provider-call gate.
Offline work can define the expected evidence shape and fixture payloads, but
must not call Mistral or commit real provider responses.

Required evidence:

- model/API identifier;
- request timestamp and local run id;
- source-pack identity;
- attachment SHA-256;
- page count;
- Markdown output reference;
- provider payload disposition;
- error/retry metadata if applicable.

## Route Selection

Route selection consumes native and OCR evidence:

- `native`: native extraction is complete and sufficient.
- `ocr`: OCR extraction is complete and native extraction is unavailable or
  insufficient.
- `merged-dual`: native and OCR evidence both exist and merged output is
  selected.

Route selection must be deterministic from evidence records and must leave a
human-readable reason.
