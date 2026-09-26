# Receipt-bound GPT paper-card execution

`run_trusted_gpt_paper_card` performs one `model.card` request from verified
published summary inputs. It checks the exact no-effect scope, operator
readiness and saved OAuth, then requests a trusted root reservation. Source
bytes are freshly verified before dispatch and again during result validation.
There is one attempt and no fallback or retry. Output and card content remain
in memory; this runner publishes no paper artifact.

## Trusted approval and one-use reservation

The fixed administrator record is
`/etc/millefeuille/gpt-card-approval.json`, schema
`millefeuille-gpt-card-approval/v0.1`. It binds the exact receipt/packet,
card request-plan fingerprint, paper and card run, published summary bundle,
one request, approver and approval time. Held-descriptor no-follow reads,
administrator ownership, safe modes, strict canonical JSON, exact fields and
digest matching are required. Caller-issued receipt hashes are insufficient.

The Linux root broker consumes the matching receipt in
`/etc/millefeuille/gpt-card.sqlite3` before the first possible provider call.
The private ledger uses an immediate transaction, full synchronous commits,
unique receipt ID/digest and validated canonical audit rows. Untrusted
approval, expiry, source/packet drift, non-root execution, unsafe controls or
replay fails closed. An interrupted or failed provider call remains consumed.

The one-shot socket is `/etc/millefeuille/gpt-card.sock`. Both sides validate
peer credentials; only the node model user may request a reservation from the
root broker. Frames are bounded and contain metadata only. Evidence paths
must stay under the declared source root and no path component may be followed
through a symlink. The broker repeats source/approval checks. An unauthorized
first peer cannot consume the listener. The socket is removed after the
authorized exchange or timeout.

## Result and verification

The runner validates exact actual model/authentication, output hash/size,
strict card content and citations, complete actual usage, and explicit zero
incremental API cost. It produces only a transient outcome for later canonical
assembly and separately approved output publication. A missing readiness
marker, saved OAuth failure, broker denial, source drift, invalid acknowledgement,
invalid result or provider failure stops the run without retry.

The focused tests cover exact scope, trusted-record identity and permissions,
durable consumption, socket transport and replay, malformed requests,
unauthorized peers, reservation-before-call ordering, source rechecks and
result rejection. A real Linux root-to-node GCP rehearsal passed using
synthetic fixture data: separate UIDs, root-owned node-group socket, one
durable reservation, replay denial and socket removal. It made zero provider
calls and touched no existing paper package or production card control.

Canonical card/source-run assembly, exact write approval/publication,
whole-paper acceptance, index/duplicate checks, classification and Zotero
writeback remain later work. The immutable summary publication is unchanged.
Grok execution remains deferred.
