# Receipt-bound GPT summary execution

`run_trusted_gpt_summary_batch` is the first live paper-summary entry point. It
accepts one exact MF-100 packet and receipt for a verified preparation package.
It rechecks the GPT-only manifest and operator preflight, checks saved OAuth
before consuming the receipt, and asks the administrator-owned one-use broker
to reserve it. A missing broker or trusted approval prevents every model call.

For each approved page, section, and full-paper unit, the runner replans the
source and compares the entire manifest before dispatch. The OpenClaw adapter
checks the OAuth profile and pinned model for each call. Each result is checked
against its request, output hash, strict JSON contract, source locators, and
zero incremental API cost before the next call. Any failure stops the batch;
the already reserved receipt remains consumed and cannot be replayed.

After all calls, the runner verifies the full source and result batch again.
Accepted summary text and exact executor evidence are returned only in memory.
`plan_gpt_summary_outputs` revalidates that complete evidence and maps it to a
deterministic hierarchical summary record, private text refs, and a text-free
prospective write fingerprint. The plan contains no final model-provenance
record and cannot be published until that record and a separate durable-write
receipt are validated. This entry point writes no
summary, source pack, index, Zotero record, or paper card. Durable
materialization remains a separate approval boundary.

The offline tests use a synthetic paper and a fake model client. They cover
the preflight and broker gates, complete accepted batches, source drift after
one call, a malformed first result, and a mismatched reservation. No live
paper text or provider call is used in these tests.
