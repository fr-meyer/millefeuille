# docai-test OpenKB Handoff Fixture

Offline, redacted fixture derived from the June 2, 2026 `docai-test` Zotero
dogfood run.

The real run covered four tagged Zotero items:

- three recoverable PDF attachments, represented in `handoff.live.jsonl`;
- one tagged item without a PDF attachment, represented in
  `openkb_outcomes.jsonl` as a skip.

The fixture intentionally contains no PDFs, authenticated URLs, API keys,
provider payloads, OCR text, source-pack content, real paper titles, or local
machine paths. It keeps only the minimum handoff identity, verification,
preview-redaction, and downstream OpenKB outcome shape needed for repeatable
acceptance tests.
