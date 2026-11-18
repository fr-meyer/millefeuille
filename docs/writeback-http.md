# Internal actual Zotero HTTP binding — source review candidate

`writeback_http.py` implements actual HTTPS GET/PATCH through existing httpx. No API call is made by import/construction, no secret/environment discovery, root grant, public CLI or automatic fallback exists. The future trusted root route must instantiate independent _HTTPReader and _HTTPWriter with an explicitly injected secret, never accept caller-provided remote proof as authentication. Production transport is a fresh no-retry AsyncHTTPTransport; tests inject MockTransport only.

## Fixed remote surface and budgets

Only https://api.zotero.org/users/{numericID}/items/{8charKey} or explicit groups equivalent. No caller URL/header injection, redirects, environment/proxy credential trust, note endpoint or collection creation. Authentication is in the request header only, removed from retained request objects after exchange; boundary repr/errors are sanitized. Response secret echoes are denied, not journaled. No secure zeroization of immutable Python strings is claimed.

PATCH body has exactly tags and collections, with If-Unmodified-Since-Version. Expected204 with one positive Last-Modified-Version header and empty body; version must advance. Mutation budget1 per writer instance, read budget2 per reader instance. Fresh client for every GET, no cache/write-response reuse, no-cache/no-store headers. Header cap8KiB/32fields, raw body64KiB, strict JSON type/UTF-8/duplicate/nonfinite/identity/library/data-key/version validation. Nonidentity content encoding and truncated declared lengths deny. No mutating retry/rebase or backoff sleep;429 is explicit refusal.

## Default production process containment

The default transport now runs the ENTIRE HTTP exchange and asyncio.run shutdown
in a fixed disposable Python worker, not in the supervisor. Explicit in-process
MockTransport injection remains only a test seam. The worker receives secret,
method/identity/body and inherited absolute deadline only through bounded private
anonymous stdin pipes; bounded framed result/proof uses stdout. No secret is in
argv/environment/files/logs; stderr is discarded. Command/code path is fixed and
Python isolated mode binds the exact adjacent package, not operator configuration.
The future root route must authenticate the installed immutable code namespace.

The Linux/main-thread supervisor has no resolver/executor/network thread. It uses
nonblocking selectors for bounded IPC, a single original monotonic deadline and
reserves cleanup INSIDE that deadline. Worker deadline is earlier, leaving time
for forced group termination/reap. Whole input131072bytes/output200000bytes are
capped; malformed, oversized, incomplete, extra output/nonzero exit denies. No
result is accepted until successful direct exit AND verified process-group
emptiness/reaping, then proof rederivation before deadline. A valid refusal is
preserved even if later exit/cleanup fails. Timeout remains ambiguous/no retry.

Accepted F1b _kill_process_tree is reused for POSIX SIGKILL of the isolated owned
group. The supervisor temporarily becomes a Linux child subreaper, scoped under a
local lock, so orphaned SAME-GROUP descendants are adopted/reaped with group-scoped
waitpid rather than left as container zombies. Prior subreaper state and signal
handlers are restored. WNOWAIT observes success without releasing the leader PID
before group termination, avoiding a group-identity reuse window. SIGINT/SIGTERM/SIGHUP
request cancellation while all cleanup phases continue. Other parent groups are
not signalled or reaped. There is no unbounded Popen context-manager wait.

Actual spawned-process tests block default-executor work forever, including a
worker that returns from its coroutine but hangs in asyncio.run executor shutdown.
Both return bounded with leader/descendant group gone; no real DNS/API is called.
A fixed trusted child must not daemonize out of its owned group. This is Linux
userspace containment, not universal hard realtime against uninterruptible kernel
fork/exec/kill/reap, a hostile privileged child, or external competing child reapers.
Such cleanup uncertainty denies output; it is not moved to a future root service.
Other OS/thread contexts fail closed. Actual root namespace/HTTP authorization and
live DNS/TLS/provider qualification are still separate gates, not missing resolver
containment implementation.

## Authoritative projection contract

Reader returns exactly {snapshot,proof}. Proof schema millefeuille-zotero-read-proof/v1 contains identity, status200, header_version, original raw_utf8 bytes represented as UTF-8 text, raw_sha256 and snapshot_digest. Core independently reparses/rederives this projection and journals both snapshot and bounded original raw proof privately. Raw response is not dropped in normalization. Only duplicated data.key/data.version and validated server dateModified are removed from semantic data. dateAdded, all unknown data fields and ordered creator/content arrays remain. Tags/collections alone are canonicalized as unordered sets with deterministic ordering, duplicate values rejected and every tag object/type preserved. Missing tag type is NOT equated to0 without protocol evidence.

The core pure planner uses the same tags/collections ordering; receipt plan digests therefore reflect the new canonical semantics. Proof hashes are content bindings, NOT cryptographic remote attestations: trusted HTTPS transport/root acquisition remains essential. Existing root/materialized-source/taxonomy/collection approvals are neither waived nor provided here.

## Ownership and compensation

Typed definitive refusals400/401/403/404/409/412/413/415/422/428/429 cannot become owned success even if another actor happens to make the target state. No verified acknowledgement means ambiguous: a matching subsequent GET is preserved as observed evidence but remains needs-review and rollback-ineligible. Written/rolled-back requires an explicit204 acknowledgement and readback whose version equals that exact acknowledgement, with exact semantic target and identity. Same-data concurrent version advancement denies ownership. No automatic compensation.

Core audit is now v2, adding exact acknowledgement status/version; older v1 state-match-only records are NOT silently upgraded into rollback ownership. Existing journal schema/externally persisted commitment/replay protection remain unchanged. New fake core tests deliberately return acknowledgements/raw proofs; the old ambiguous-success expectation was tightened, not retained by fabricated ownership.

## Protocol evidence

First-party https://www.zotero.org/support/dev/web_api/v3/write_requests, Partial-item updating and Both PUT and PATCH: omitted fields untouched; arrays complete lists; item version in JSON and single-item Last-Modified-Version; conditional header required;204 success and412 stale item. https://www.zotero.org/support/dev/web_api/v3/syncing describes library/object version assignment after successful writes. Retrieved public documentation is archived with repair evidence. No api.zotero.org request was made during implementation/testing.

Next: independent review, then root receipt/allowlist/externally persisted journal commitment, actual source-file/scientific acceptance and existing collection mapping verification plus operator binding. No installation/build/live writeback or notes qualification here.

## T1/T2 supervisor lifecycle repair

Resource ownership now starts inside a guarded scope before lock acquisition, CDLL/selector allocation, handler installation or prctl setup. Optional resources are initialized safely; setup faults sanitize the error, release the lock, restore acquired state and permit a subsequent actual worker call.

SIGINT/SIGTERM/SIGHUP are blocked during setup, then latched during exchange. Cleanup blocks these ordinary signals across group termination, reap, subreaper restoration and lock release; pending cancellation is drained while blocked, and the original handlers/mask restored. An interrupted kill is retried up to8times within the ORIGINAL deadline while the leader remains unreaped/reserved. This is cleanup retry ONLY, never HTTP or mutation retry. Handler/subreaper restoration has bounded continuation on transient faults; lock release precedes restoring inherited exception-raising handlers. Cleanup uncertainty invalidates ACK/success; definitive refusal remains primary.

The corrected reviewer harness captures the real kill helper before injection and calls it after sending SIGHUP; the original harness that never actually kills is preserved as historical evidence, not misrepresented as a passing lifecycle proof. Real spawned regression coverage includes mixed cancellation at kill/restoration, same-group descendants, unrelated sentinel survival, setup/prctl/partial-handler/selector-close faults and actual next-call usability. Linux main-thread and prior OS boundaries remain unchanged; no public interface/root/CLI expansion.
