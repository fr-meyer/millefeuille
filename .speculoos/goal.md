# Goal

Make `zotero-docai-pipeline` a trustworthy adapter in the Millefeuille paper
analysis lifecycle.

The repo should reliably export Zotero attachment identity and verification
evidence into an OpenKB/DocAI handoff without storing credentials,
authenticated URLs, raw PDF bytes, or provider payloads in committed files.

The first Speculoos task is a bootstrap/spec-audit slice. It records the
current offline evidence, defines the next implementation gates, and avoids any
live Zotero, OCR, OpenKB, PageIndex, Mistral, or source-pack write.
