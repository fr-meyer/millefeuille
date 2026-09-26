# Receipt-bound GPT summary execution

`run_trusted_gpt_summary_batch` is the first live paper-summary entry point. It
accepts one exact MF-100 packet and receipt for a verified preparation package.
It rechecks the GPT-only manifest and operator preflight, checks saved OAuth
before consuming the receipt, and asks the administrator-owned one-use broker
to reserve it. A missing broker or trusted approval prevents every model call.
The no-effect approval preview first requires normalized absolute evidence
paths below the declared source-pack root, matching the broker's path rules.
Evidence prepared beside that root is rejected before source replanning or
approval. The broker repeats containment validation and its no-follow reads
before receiving the request.

For each approved page, section, and full-paper unit, the runner replans the
source and compares the entire manifest before dispatch. The OpenClaw adapter
checks the OAuth profile and pinned model for each call. Each result is checked
against its request, output hash, strict JSON contract, source locators, and
zero incremental API cost before the next call. Any failure stops the batch;
the already reserved receipt remains consumed and cannot be replayed.

After all calls, the runner verifies the full source and result batch again.
Accepted summary text and exact executor evidence are returned only in memory.
`plan_gpt_summary_outputs` revalidates that complete evidence and maps it to a
deterministic hierarchical summary record in immutable canonical bytes,
private text refs, and a text-free prospective write fingerprint. The refs are
scoped under `analyses/millefeuille/<run_id>/` so separate runs have disjoint
destinations. The write manifest's own path is fixed in its canonical bytes. It also
binds exact selected Markdown and structure bytes to run-scoped
`structure/inputs/` snapshots for future provenance input refs. These
snapshots are planned in memory and included in the write preview; they
are not published by the planner.
`validate_gpt_summary_output_write_preview` rereads the source and validates
an exact `source-pack.write` packet and MF-100 receipt against that manifest,
the observed-usage and model-provenance digests, one paper, and one run.
Missing actual usage now
blocks even the write preview. It reports the prospective file refs without reserving
the receipt or writing files. `validate_trusted_gpt_summary_output_write_approval`
also requires a matching administrator-owned record at
`/etc/millefeuille/gpt-summary-write-approval.json`. The privileged
`reserve_trusted_gpt_summary_output_write_receipt` primitive consumes that
exact receipt in a separate root-owned replay ledger at
`/etc/millefeuille/gpt-summary-write.sqlite3`. These individual checks write
no paper output. The root-only `publish_trusted_gpt_summary_handoff` broker
combines receipt reservation with a pending audit row in one transaction,
replans the source and exact output bytes, and calls the filesystem writer.
The writer stages root-owned files, syncs them, and publishes the complete run
with a Linux atomic no-replace rename. The audit records `published`,
`failed-before-commit`, or `uncertain` when a rename or final audit outcome
cannot be proved. A consumed receipt is never replayed. The caller sends its
transient accepted handoff over a one-shot root-owned Unix socket; the reply
contains only commit identity, never summary text. This does not index,
classify, or write to Zotero. Source-pack ancestors on GCP are owned by the
model user's UID, so an uncooperative same-UID process can still rename an
ancestor; descriptor-relative checks and the cooperative writer contract do
not protect against that peer.

`plan_gpt_summary_observed_usage` separately revalidates the complete accepted
batch and requires actual reconciled input, output, and total token counts for
every unit. It emits canonical, text-free in-memory evidence bound to the
output manifest, each executor result, model identity, input hash, and output
hash. The output manifest fixes the prospective observed-usage path. Missing
or inconsistent usage fails closed. The separate no-write `plan_gpt_summary_model_provenance` materializer
now derives strict per-unit `millefeuille-model-provenance/v0.1` records
from these observed counts, the exact model plans, validated executor
results, selected Markdown and structure snapshots, and summary text refs.
A canonical manifest binds each record hash to its result and output.
`plan_gpt_summary_publication_bundle` now rechecks the exact write preview
and enumerates all source snapshots, summary texts, records, usage, and
provenance as immutable in-memory bytes. It verifies every declared
digest and the complete approved file-ref set. The bundle is still a
no-write plan; a trusted broker must reserve the receipt and publish
the files without replacement.

The offline tests use a synthetic paper and a fake model client. They cover
the preflight and broker gates, complete accepted batches, source drift after
one call, a malformed first result, and a mismatched reservation. A Linux
root-only integration fixture runs the real publication broker, ledger, audit,
and filesystem writer against a temporary synthetic source pack, then checks
every published byte and rejects replay. Normal unprivileged CI skips only
that root-only case. No live paper text or provider call is used in these
tests.
