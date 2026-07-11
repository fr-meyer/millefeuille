# Millefeuille Pipeline Contract Packet

This packet records the remaining `zotero-docai-pipeline` work as an
offline-first contract. It is safe to review and test without Zotero
credentials, PDF bytes, OCR providers, OpenKB writes, source-pack writes,
GitHub publication, releases, or package-index changes.

Files:

- `remaining-work.md` - end-to-end work pipeline and manual gates.
- `cli-contract.md` - intended command groups and run modes.
- `stage-manifest.schema.json` - machine-readable stage manifest shape.
- `tag-state-machine.md` - Zotero tag lifecycle design.
- `ocr-backend-contract.md` - native/OCR evidence adapter contract.
- `release-version-policy.md` - Speculoos-governed release and version path.
- `live-run-plan.md` - future live dogfood plan, still requiring approval.

Manual gates remain explicit: live Zotero reads/writes, PDF recovery/download,
OCR/Mistral/PageIndex calls, OpenKB writes, source-pack writes, GitHub
publication/merge, `dev` to `main` promotion, release tags, and package
publication all require separate approval.
