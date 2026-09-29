# Existing multi-source run package

`plan_multi_source_run_package` joins an existing v0.2 Zotero source pack with a
saved flat run at `source-packs/analyses/millefeuille/<run-id>`. It verifies every
PDF and upstream page attribution, the published summary view and text files,
the paper card, and the native index. A plan makes no provider call and writes
nothing. It proposes exactly `stage-manifest.json` and `artifact-index.json`,
with byte counts, SHA-256 hashes and a combined preview fingerprint.

The run package records the native extraction source directory, selected full
text, structure, published summaries, card and index by their verified refs.
It marks the handoff, acceptance and later classification/writeback stages as
not started until their own evidence is supplied. The OCR stage is skipped
because the selected upstream route is native text. A written OpenKB lane is
recorded only when the saved index status proves it.

`publish_multi_source_run_package` accepts the exact preview fingerprint after
its separate operator approval. It replans against current inputs and creates
the two files with no-follow, exclusive writes. Existing targets are never
replaced. If publication stops between the two writes, the partial package
requires explicit recovery; neither file should be replayed blindly. The
standard `resolve_run_artifacts` reader then checks the package identity.

The CollaGAN main paper and supplement have a read-only plan and in-memory
resolver check. Permanent publication, a verified two-row handoff report,
whole-paper acceptance and duplicate review remain separately gated steps.
