# Supported and refused source observations

Offline qualification, 2026-11-11. This is not whole-paper acceptance.

The existing upstream planner consumes caller-supplied Markdown, not PDFs.
There was no existing embedded-text decoder in the inspected product tree.
The opt-in `domain/source_qualification.py` seam now decodes verified local
PDF bytes using optional PyPDF2. It is wired into packaged `inspect-native`, not the upstream planner.
The optional `native` extra declares PyPDF2>=3.0.1,<4; absence produces
`native-decoder-unavailable`. The CLI requires `--trusted-original-fixture`: no
CPU/memory-isolated parser is implemented, so untrusted/production PDFs are
explicitly unsupported. This closes the packaged adapter/dependency gap,
not end-to-end extraction support.

| ID | Observed contract | Remaining boundary |
| --- | --- | --- |
| S01 | Packaged adapter executed on two-page original PDF; exact text, numbers and stream order pass | Source review remains required; visual order is not guaranteed |
| S02 | OPEN_G2, no live OCR executed | Actual Mistral scanned-page success required |
| S03 | OPEN_G2, no live mixed extraction executed | Native and raster coverage/provenance required |
| S04 columns | Positioned text facts preserved, needs-review | Visual column order unqualified |
| S04 table | Exact 17.25 and 42.50 text retained, needs-review | Cell geometry and spanning cells unqualified |
| S04 figure | Caption text retained, needs-review | Figure graphics not tested or preserved by text extraction |
| S04 rotation | Rotated page text retained, needs-review | Visual placement unqualified |
| S04 equation | ASCII equation retained, needs-review | Mathematical glyph/layout fidelity unqualified |
| S05 | Existing upstream synthetic overlapping page/order-permutation test reused | Real main/supplement extraction and semantic acceptance remain separate |
| S06 | Exact bytes and same-text/different-bytes deterministic identity matches | Both remain needs-review; no corpus deduplication or source-count mutation |
| S07 | Changed content and stale expected hash detected, needs-review/refusal | No authoritative version selection; metadata/version enforcement belongs to acceptance |
| S08 | Missing file typed refusal, no output | Secure-I/O capability refusal may also occur on unsupported platforms |
| S09 | Matching hash reaches actual PyPDF2 decoder failure, typed refusal | No mistaken hash-only corruption claim |
| S10 | Encrypted valid PDF refused password-required | No password recovery, credential use or empty success |
| S11 | HTML with .pdf suffix refused unsupported-format | Suffix never establishes PDF support |

All successful decodes return needs-review, never semantic-complete. Typed
refusals have stable reason codes. No file outputs, provider calls, automatic routing effects,
acceptance records or canonical source-pack writes are made. Source hashes
cover exact bytes; text hashes include page lengths and boundaries and do not
establish semantic identity. The comparison helper does not mutate matches.

Input cap is 1 MiB and 32 pages, output text cap 1 MiB. These are not decoder
CPU/memory isolation. Untrusted production PDFs require a separately
resource-limited execution process, not this in-process qualification seam.
No installation, clean dependency claim or Windows behavior execution occurred.
Existing secure reads fail closed where required platform primitives are absent.

Executable coverage: RealSourceQualificationTests in
`tests/test_millefeuille_real_source_qualification.py` (nine tests, five layout
subcases); existing MultiSourceUpstreamTests supplies the S05 regression.
Fixtures are original PDFs with real xref tables generated at test time.
A blank native page is tested only as needs-review, not scanned/OCR support.
No private source prose or external fixtures were used.
