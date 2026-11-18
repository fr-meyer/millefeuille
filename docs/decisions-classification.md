# Optional Decisions category proposal

Standalone optional supplement. No OpenClaw dependency, OAuth access, Zotero mutation,
taxonomy mutation, or replacement of the complete grounded Astra classification.

## Protocol and CLI

Parent registers `run_decisions_classification_cli(argv)` from
`millefeuille.cli.decisions_classification`.
Arguments: --run-dir PATH [--taxonomy PATH] [--live --max-requests 1].
Taxonomy override must equal the run's already locked released snapshot.
Default is a read-only JSON payload plan with hashes, no writes, key reads or calls.
Existing completed reservations replay offline, including with --live; incomplete,
malformed, or mismatched reservations block retry. Return 0 for plan/recorded proposal,
2 for blocked or needs_review. Parent owns main.py registration.

Domain: prepare(run_dir, taxonomy=None) -> context; await execute(context,
live=False, max_requests=None) -> record; parse_proposal(raw_response_bytes, context)
-> versioned supplemental proposal. Use prepare immediately before execute. These are
internal Python functions, not an authorization boundary against hostile callers.

Live is a new, separately approved scope. Only OPENAI_API_KEY is read, after the
read-only preflight and replay check. Credential discovery and spending approval are
operator responsibilities. No other auth source or fallback is implemented.

## Official protocol checked 2026-11-18

https://developers.openai.com/api/docs/guides/decisions
https://developers.openai.com/api/reference/resources/decisions/methods/create

POST https://api.openai.com/v1/decisions, model gpt-6-luna. Request has model,
input (string), questions with type=choice, name, instructions, choices whose objects
have value and description. Official reference allows 2..255 choices. Thus the current
137 active level-2 IDs plus none and needs_review fit without a shortlist. Future
registries over 253 active level-2 entries fail closed, never silently hide candidates.
Full entry definitions and parent definitions are supplied. No invented wire fields.
Whole response body is parsed through answers, preserving raw body and provider metadata.
Choice answer: type, name, choice, confidence, probabilities [{value, probability}].
Refusal is journaled as needs_review. Additional outer metadata is supported, not rejected.
No score is invented and no confidence threshold confers classification authority.

## Evidence and bounded execution

Requires existing standalone analysis summary, classification, evidence, taxonomy lock,
registry, and OCR stage/fulltext artifacts. Checks paper identity, canonical summary and
classification hashes, exact fulltext hash, taxonomy lock and run scope. Uses every
summary source locator to select actual line spans; invalid/out-of-bounds locators fail.
No truncation or fabricated spans. The original full grounded files remain untouched.

One POST maximum per established paper directory, one choice question. Explicit --max-requests 1 is required.
500,000 request bytes, 1,000,000 response bytes, 60-second total call deadline.
No monetary cap is asserted: byte/request limits bound exposure but are not a billing
quote or spend authorization. No retries, proxies, redirects, SDK defaults or fallback.

Live creates only PAPER/decisions/supplemental-category-v1, exclusively. request.json,
intent.json, response.json, http.json, proposal.json preserve exact request/response,
hashes, model, configuration, candidate IDs, source bindings, uncertainty and proposal.
Files are private 0600, new directories 0700. Credentials/headers are not saved.
The reservation is retained on every failure, including ambiguous network outcomes.
Never delete a reservation to retry without independent operator reconciliation.

## Limits of this source delivery

No provider call or credential read was made during implementation. No functional,
synthetic, fixture or mock test was run. AST/Ruff are static evidence only, not API
acceptance. Parent must qualify using real artifacts and separately approve any live
canary. Local files must be in a trusted, nonconcurrently modified private run directory;
checks reject symlinks but do not claim hostile-filesystem race resistance. Reservation
is shared across runs in the established paper directory. Cloning the whole paper
to another output root must not evade the limit; this is not a global billing quota.
Raw output may contain sensitive paper text; keep the run private. Transport failures
or oversized bodies can only preserve an uncertain journal, not a complete response.

## Compact taxonomy prompt v1

New unreserved plans pin `prompt_format=taxonomy-compact/v1` in the binding.
The deterministic projection preserves entry order, every active level-2 ID,
label, complete definition, parent link, all parent semantics, and distinct rules.
It omits bookkeeping, empty rule lists and inclusion strings exactly equal to
that entry’s definition. Exclusions/boundaries retain their separate roles even
when text matches another field. No normalization, truncation, shortlist or registry
mutation occurs. Deprecated entries are never choice candidates. Registry validation
is unchanged; normalized successors must satisfy the existing validator first.

The reservation directory remains `supplemental-category-v1` across prompt versions.
An existing binding without a prompt-format field reproduces the historical request
and binding bytes. Present unknown/null formats fail closed; partial, malformed or
mismatched reservations never grant a new call. Completion still requires exact
request, binding, receipt hash and parsed proposal equality.

For in-process offline measurement only, `prepare(run_dir, compact_plan=True)`
builds the compact payload even for historical reserved runs. This context is marked
planning-only and `execute` rejects it, including with live=False. Inspect its
payload/binding directly; it is not replay or authority to execute. The CLI does not
expose this override. Ordinary legacy replay retains the historical format.

This is prompt efficiency only. The supplemental protocol, none/needs_review choices,
confidence handling and lack of writeback authority remain unchanged. Single-label
classification/create_new/GPT generation are separate, unimplemented work.
