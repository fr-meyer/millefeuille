# Existing multi-source run package

`plan_multi_source_run_package` joins an existing v0.2 Zotero source pack with a
saved flat run at `source-packs/analyses/millefeuille/<run-id>`. It verifies every
PDF and upstream page attribution, the published summary view and text files,
the paper card, and the native index. A plan makes no provider call and writes
nothing. It proposes exactly `stage-manifest.json` and `artifact-index.json`,
with byte counts, SHA-256 hashes and a combined preview fingerprint. The
preview also records the size and SHA-256 of every regular file in the source
pack and the saved run's summaries, cards and index trees. Symlinks and
unexpected file types fail closed. These input hashes bind the approved preview
to the saved evidence, not just to the two proposed output files. The source
root is anchored to an absolute path before the preview is computed, so changing
the caller's working directory cannot redirect an approved publication.

The run package records the native extraction source directory, selected full
text, structure, published summaries, card and index by their verified refs.
It marks the handoff, acceptance and later classification/writeback stages as
not started until their own evidence is supplied. The OCR stage is skipped
because the selected upstream route is native text. A written OpenKB lane is
recorded only when the saved index status proves it.

`publish_multi_source_run_package` accepts the exact preview fingerprint after
its separate operator approval. It recomputes the caller's fingerprint, replans
against all current input bytes, requires the complete canonical plan to match,
and writes only the refreshed paths and texts with no-follow, exclusive writes.
Existing targets are never replaced. If publication stops between the two
writes, `recover_partial_multi_source_run_package` requires a separate explicit
recovery approval for the same fingerprint, rechecks every input and the exact
first-file bytes, and then creates only the missing second file. A changed first
file or an existing second file fails closed. The standard `resolve_run_artifacts`
reader then checks the package identity.

The CollaGAN main paper and supplement have a read-only plan and in-memory
resolver check. Permanent publication, a verified two-row handoff report,
whole-paper acceptance and duplicate review remain separately gated steps.
