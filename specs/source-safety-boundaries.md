# Source safety boundaries

## Offline default, not provider acceptance

The CLI-selected legacy MistralClient refuses in its constructor before reading
configuration/credentials or initializing the SDK. BoundedMistralAdapter also
refuses before constructing any transport or ledger. No trusted independent
provider authority/account/current-price/PAYG controller or hard transport
termination capability ships. Caller booleans, references and hashes cannot
activate either route. No threads, children or orphan work are created by refusal.

Synthetic tests must explicitly choose BoundedMistralAdapter.isolated_mock with
an exact httpx.MockTransport. Its handler is trusted offline test code, not a
sandbox: Python handlers can themselves perform arbitrary effects. This seam
never constructs a real HTTPTransport and is not approved for provider use.

## Synthetic wire and durable accounting

The mock seam requests Accept-Encoding: identity, rejects compressed responses
before iterating their bodies, and uses raw bounded chunks rather than decoded
iter_bytes. MockTransport may deliver an already allocated body; that trusted
fixture allocation is not a hostile preallocation guarantee. Monotonic aggregate
checks reject late responses, but cannot terminate a blocking arbitrary mock
handler. Production refuses before send precisely because hard deadline/header
and cancellation control is unavailable. No fake capability boolean is proof.

Graph operation kinds bind exact synthetic endpoint/method pairs. Upload must
be observed before signed URL, both before OCR. Cleanup reserves must be delete;
reconciliation reserves must be status/poll and disjoint. All calls and maximum
costs are reserved up front. Unknown liability is never refunded or retried.
Cleanup after unknown upload and after deadline is not claimed safe/supported;
independent finalization authority/controller remains OPEN, live route disabled.

Ledger v2 is bounded append-only JSONL, nonblocking locked, fsynced before send,
and directory-fsynced at creation. Independent reopen replays transitions;
damaged/truncated tails refuse without resetting. Monotonic starts are bound to
Linux boot identity; cross-boot ledgers refuse. Old v1 ledgers refuse, never auto
migrate. Fork/death tests cover before reservation fsync, after fsync, synthetic
send and terminal transition; these demonstrate process-death safety, not power
loss/filesystem durability certification or hostile same-UID integrity.

Resolved model must be a pinned mistral-ocr-* family member, not latest or an
arbitrary nonempty string. Requested rolling model and resolved pin remain
separate. Raw cap, JSON depth 16, node count 4096, scalar UTF-8 64KiB, page count
1..32 and per-page markdown 64KiB are checked with typed refusal. No fallback.
Parser allocations are bounded by raw cap, not a universal hard memory sandbox.

## Native and mandatory acceptance matrix

PD06 qualify_untrusted_native_source still refuses BEFORE input access, decoder
import or child creation, with no trust override. No full filesystem/network/
cumulative-I/O native isolation adapter ships. RLIMIT alone is insufficient.
Trusted synthetic PyPDF2 fixture qualification is not hostile-PDF isolation.

S01 synthetic observation: needs-review only. S01 real qualification, S02/S03
private/provider success, S05 semantic/persistence integration and full native
termination/isolation remain OPEN. Offline refusal is a safety repair, not
mandatory product success, W4 completion, release or provider approval.
