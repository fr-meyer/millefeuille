# Existing multi-source run package

`plan_multi_source_run_package` joins an existing v0.2 Zotero source pack with a
saved flat run at `source-packs/analyses/millefeuille/<run-id>`. It verifies every
PDF and upstream page attribution, the published summary view and text files,
the paper card, and the native index. A plan makes no provider call and writes
nothing. It proposes exactly `stage-manifest.json` and `artifact-index.json`,
with byte counts, SHA-256 hashes and a combined preview fingerprint. The
preview also records the size and SHA-256 of every regular file in the source
pack and the saved run's summaries, cards and index trees. Symlinks and
unexpected file types fail closed. Published-summary links also bind the
original generation's complete summaries tree and the exact route, structure
and preparation evidence refs. All these inputs share the same file, node,
depth and byte limits; repeated file refs are inventoried once. These input
hashes bind the approved preview to the saved evidence, not just to the two
proposed output files. The source
root is anchored to an absolute path before the preview is computed, so changing
the caller's working directory cannot redirect an approved publication.

The run package records the native extraction source directory, selected full
text, structure, published summaries, card and index by their verified refs.
It marks the handoff, acceptance and later classification/writeback stages as
not started until their own evidence is supplied. The OCR stage is skipped
because the selected upstream route is native text. Both the upstream builder
and the run-package planner reject OCR or mixed routes; they never label those
routes as native or propose native-only stage metadata for them. A written OpenKB lane is
recorded only when the saved index status proves it.

`publish_multi_source_run_package` accepts the exact preview fingerprint after
its separate operator approval. It recomputes the caller's fingerprint, replans
against all current input bytes, requires the complete canonical plan to match,
and writes only the refreshed paths and texts with no-follow, exclusive writes.
Publication holds an advisory POSIX lock on the pinned run directory. After
each write it rechecks the saved inputs, reads back every completed output with
the exact approved bytes, and rebinds the current directory to its approved
device/inode identity. Recovery also reads back the existing first output after
writing the second. A replaced directory, missing output or changed output
fails closed. Failed publication and recovery preserve all created or substituted
entries, including outputs in a displaced directory. They never automatically
unlink outputs: a name-based identity check cannot prevent replacement before
deletion. Nonregular outputs are rejected without a blocking read. Writers that
share these artifacts must coordinate with the advisory lock; the checks do
not make an entire mutable tree an atomic filesystem snapshot.
Existing targets are never replaced. If publication stops between the two
writes, `recover_partial_multi_source_run_package` requires a separate explicit
recovery approval for the same fingerprint, rechecks every input and the exact
first-file bytes, and then creates only the missing second file. A changed first
file or an existing second file fails closed. The standard `resolve_run_artifacts`
reader then checks the package identity. An input drift, altered or missing
output, or failed publication with both outputs present requires separate
operator resolution; this recovery function does not remove, overwrite or
declare those states successful. Restoring inputs does not grant a new approval.

The CollaGAN main paper and supplement have a read-only plan and in-memory
resolver check. Permanent publication, a verified two-row handoff report,
whole-paper acceptance and duplicate review remain separately gated steps.
