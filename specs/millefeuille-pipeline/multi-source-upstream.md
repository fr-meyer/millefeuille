# Whole-pack native upstream planning

A verified v0.2 Zotero source pack can now be prepared for native route and
structure materialization without a provider call or a durable source-pack write.
The caller supplies an explicit `MultiSourceScope` for the complete pack and one
`MultiSourceNativeInput` per attachment. Each input names already extracted
UTF-8 Markdown, its SHA-256, page count, and an unambiguous source label.

`plan_multi_source_native_upstream` rechecks the manifest, complete attachment
set, and every PDF byte before reading the native Markdown. It requires
consecutive page markers for every source and verifies the Markdown hashes.
The result is an in-memory plan: one native text file per PDF, native evidence,
combined selected text, route evidence with global-to-attachment page mapping,
local structure evidence, and an outline. The selected text has a source heading
inside each attachment's first global page. The route and structure sidecars
carry the whole-pack aggregate source hash and typed source scope.

The public `preview()` exposes only output refs, sizes, hashes, source identity,
page count, and warnings. Publishing the planned bytes to a real source pack
requires a separate exact MF-100 output approval and a write path that rechecks
source and planned output bytes at application time. This planner does not
extract PDFs, run OCR, call GPT, or publish a summary.

## Live CollaGAN checkpoint, 2026-09-28

The private GCP source pack for Zotero item `RGESYYF3` contains the verified
main paper (`DQVY94K8`) and supplement (`JVPRI7I7`). Native extraction
produced 10 and 9 text pages respectively. The planner produced a 19-page,
seven-file, 127,031-byte private preview with zero warnings and zero provider
calls. The supplement heading and first page map to global page 11 and
`attachment:JVPRI7I7/p.1`. The canonical preview identity is
`sha256:926bfc3e07e56423f903983cb18e4dad2aa8ae270c889bae1a439fa042946470`.
No planned upstream files have been published to the durable source pack. Live
whole-pack GPT generation and output publication remain pending.
