# Speculoos Metadata

This directory is the repo-local Speculoos control plane for
`fr-meyer/zotero-docai-pipeline`.

The committed metadata is canonical for task status and planned surfaces.
GitHub and local Plancha/Vibe are readback or cockpit surfaces only. They do
not replace the files in this directory.

Current boundary:

- Feature work targets `dev`.
- GitHub writes use the approved Mergeguez/broker path.
- Actor and branch policy lives in `.speculoos/actors.json`.
- Default validation must stay offline and credential-free.
- Live Zotero, OCR, OpenKB, PageIndex, Mistral, and source-pack writes require
  a separate explicit operator approval.
