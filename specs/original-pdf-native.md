# Opt-in original PDF native observation v1

Interface: millefeuille.domain.original_pdf.inspect_original_pdf with exact path
and lowercase full-file sha256 manifest. No discovery, provider, OCR, persistence,
Windows writes or automatic route selection. Caller must independently authorize
the exact private source. A hash is identity, not authorization.

The existing native boundary is reused, not replaced by a new decoder. Default
NativeResourceLimits and trusted fixture qualification retain their 1 MiB cap.
That cap actually bounds PDF bytes in both native_decode_boundary and
source_qualification, not just metadata/text. Separate metadata/status and OCR
transport caps remain unchanged. secure_io.read_bytes_no_follow has a caller cap,
not a universal PDF cap. EvidenceSourceAdapter reads extraction bytes and checks
extraction_sha256 separately from original source_sha256; those contracts are
unchanged. No conversion of native observation into an approved evidence corpus.

Opt-in ORIGINAL_PDF_LIMITS: full original 1..16,777,216 bytes; 1..32 raw pages;
logical parser reads 67,108,864 bytes; CPU 10 seconds; decoder wall deadline 20
seconds; address space 268,435,456 bytes; extracted UTF-8 total 131,072 bytes;
shared output 262,144 bytes (262,096 payload). Fixed existing kernel boundary,
no caller-selected arbitrary limits. The 16 MiB ceiling allows image-bearing
originals while keeping mapped input plus bounded decoder copies below the
unchanged address-space ceiling; it is not a promise every such PDF will parse.
The 64 MiB logical-read ceiling is finite headroom, not guaranteed capacity.
Pages, time, memory and output are deliberately not increased.

After existing capability setup, parent copies a stable no-follow regular file
in <=65,536-byte chunks into private staging, hashes ALL bytes, checks opened
and named descriptor identity/stability, compares expected digest, then seals.
Short/changed/oversize/hash-mismatched sources never reach parsing. Failed staging
is discarded by boundary cleanup. Reserve full 16 MiB read and staging charges
before access, retain charges on failure. Child maps sealed exact bytes and
rechecks digest under existing filter. Child raw/BytesIO copies remain finite
under RLIMIT_AS; parent does not aggregate original payload in RAM.

Parent regular-file I/O deadline checks are cooperative between operations.
This is NOT hard termination of a blocked host filesystem read; runtime admission
must qualify the actual source filesystem/containment. Do not describe RLIMIT or
these pure tests as whole-host I/O isolation. No new isolation probe is supplied.

Output retains original digest, complete contiguous raw one-based PDF pages and
per-page text hashes. extraction_sha256 hashes canonical ASCII JSON of the exact
page/text/text_sha256 list (sorted keys, compact separators, ensure_ascii).
It is never substituted for original source_sha256. Page text is an extraction,
not an original PDF byte range. Equal snapshots with different originals remain
different source identities. Empty pages, conflicting statements, Figure 2 first
row identity and scientific interpretation remain unresolved/needs-review.
No partial result, ambiguous identity or failed parser is promoted to acceptance.

Validation scope: synthetic pure file admission/chunking/hash/stability and page
binding tests. These justify finite checks, not parser performance or sandbox
qualification. Dynamic native tests (including malformed PDFs, 32/33 pages,
>1 MiB valid original, 16 MiB boundary, CPU/memory/output refusal) require separate
genuine runtime admission and exact-head independent review. No F04/R6 execution
or workaround; private qualification additionally requires fresh exact scope.
