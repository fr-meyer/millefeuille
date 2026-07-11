# OpenKB Source-Version Drift Fixture

Offline, redacted fixture for checking that recovered attachment bytes still
match the SHA-256 recorded in an OpenKB handoff row.

The fixture contains no PDFs, authenticated URLs, provider payloads, OCR text,
source-pack content, API keys, real paper titles, or local machine paths. The
byte samples are short UTF-8 placeholders used only to produce deterministic
hashes for the source-version-drift guard.
