# Isolated staged Windows replacement preparation

## Job admission operand diagnostics after run 38394894696

The once-retrieved exact-head `4567268` evidence records a `Refusal` at
`bounded-job-admission`: complete child membership/accounting/PID binding
mismatch. Parent PID 1280 was assigned and read back while suspended with
active/assigned 1, PID list [1280], 512 MiB and one logical CPU before its single
resume. The worker did not retain any of its comparison operands. Terminal
cleanup was complete. Historical total processes 2 does not identify its PID,
a currently active second process, or which comparison failed.

This offline change preserves every original admission predicate and every
resource, inheritance and production gate. It reports expected and observed
membership, active accounting, PID-list assigned count, complete bounded PID
list, Python PID and Win32 PID, with explicit comparison results and unavailable
fields. Accounting immediately before the PID-list read and a second accounting
snapshot are retained. A later good snapshot cannot excuse an initial refusal.
A missing second read, changing active accounting, or inconsistent Python/Win32
PID refuses before dispatch. Changes to historical totals alone do not refuse.
No settling wait, admission retry, process-limit increase or Job handle inheritance
is introduced. The worker still queries its default associated job with a NULL
handle; the parent still queries its explicit owned Job handle.

All composite operands appear in one compact numeric-only stderr line capped at
1,792 ASCII bytes, before traceback output. It fits the existing 2,048-byte text
capture. The full separate error receipt retains supplementary resource/query
metadata. Invalid non-numeric fields become unavailable, never raw strings.
Successful admission also emits a numeric line. Reporting failures retain the
readback in the owned error receipt where possible. CRLF is normalized before
text sanitization. Controller markers record creation, assignment validation,
resume begin/return, previous suspend count and primary signaling. Creation and
handle PID values are retained, and a second resume attempt cannot issue another
ResumeThread call.

These are diagnostics and stricter identity/snapshot consistency checks. They do
not establish an OS root-cause repair. Default-job selection in a nested hierarchy,
interpreter handoff and a snapshot race remain hypotheses until actual values are
available. The source's after-case worker/parent PID binding remains mandatory.
No individual native outcomes are inferred from the missing case receipt.

Run `python -I -B test_job_admission.py` for the targeted pure-memory regressions.
They exercise each operand, combined failures, historical counters, bracketed
snapshot changes, API failures, PID differences, incomplete bounded PID queries,
maximum output, malformed values and exactly-once resume. They execute no Windows
API, external process or network call. This continuation performs no push, workflow,
download, private read or remote call. Another actual run requires a separate
reviewed exact-head proposal and approval.

## Worker startup evidence repair after run 38386606540

At exact head `f5ba780d6018633b0c442d87bb519e8d4932fedf`, the actual CI
PowerShell/CMD/Python argv fixtures and installed SDK compilation passed. The
worker then exited 1 without `worker-result.json`, `stdout.log` or `stderr.log`.
The once-retrieved ZIP contains only the owner marker and controller receipt;
process cleanup was terminal. No individual native case outcomes were retained.
The historical process total of two does not identify the cause. Existing logs
cannot recover the missing interpreter, import, argv or admission error.

This offline repair captures stdout/stderr before creating the suspended worker.
`STARTUPINFOEX` whitelists only three inherited handles: NUL stdin and the two
pipe writers. Parent readers are non-inheritable. Assignment, complete membership
and limit readback precede the single resume; no breakaway or extra process is
permitted. Each stream retains at most 32 KiB in memory, with one overflow byte
probe. Overflow stops reads and fails qualification through the existing cleanup
path. Retained text is ASCII-sanitized, token-redacted and capped at 2,048 bytes
per stream. The fixed public launcher argv, interpreter identity, cwd and five
allowlisted environment fields are recorded; the full host environment is never
recorded.

Worker entry errors report a phase, exception type, bounded sanitized message and
six bounded traceback metadata frames through original inherited stderr. A separate
`worker-entry-error.json` is written only after owner binding and a fresh root/
marker identity check. It contains no case rows or qualification claim. Import or
interpreter failures before the guard rely on parent pipe capture. Abrupt OS exit
or a process that emits no text can still leave the precise cause unknown. Existing
pending-buffer forced-exit custody remains unconditional, including reporting
failures. The controller always records its own observation and confirmed cleanup
where possible, without substituting that observation for a worker result. Unknown
process or handle custody blocks terminal collection.

The source manifest now includes the diagnostic helper. The SDK launcher, real
shell fixtures, workflow, native request implementation and ten-case assessment
are unchanged. Run the new portable faults with `python -I -B test_worker_startup.py`.
These simulations and affected controller regressions establish source behavior;
Windows handle inheritance and native rename qualification still require a
separately approved actual run. No publication, additional artifact download or
workflow execution is authorized or performed by this offline preparation.

## SDK launcher repair after run 38378966024

The failed head `fa6b6f7ba27fb6a4e64f4d56469e0cb0529363b6` checked out correctly
and set up Python 3.13.15, but the SDK bootstrap stopped with
`'""C:\Program' is not recognized`. It did not execute the ten native cases.
The emitted PowerShell script and decoded failed-run log are retained outside
this source patch; the full native command line was not captured in that run.

`sdk_bootstrap.py` passes one raw command-line string and an explicit executable
to `CreateProcessW` through Python's `subprocess`, with `shell=False`. CMD `/S`
removes one deliberate outer quote pair. `CALL` returns from the SDK environment
batch in that same CMD process, and `/V:OFF` disables delayed bang expansion.
Local paths with percent expansion, carets, quotes or control characters refuse.
PowerShell no longer serializes a quoted CMD payload. It passes ordinary Python
arguments. The unchanged controller continues using `list2cmdline` for its Python
worker, whose CRT argument parser has different rules from CMD.

Run the portable launcher contract tests with `python -I -B test_sdk_bootstrap.py`.
These tests check construction, refusal and dispatch parameters; mocks do not
establish Windows parsing, SDK compilation or native success. The actual CI
`pwsh` step also runs owned fixtures before compilation: a script path containing
spaces, batch environment propagation, CMD-to-Python argument capture, and Python
child CRT forwarding. Spaces, ampersands, parentheses, bangs, trailing backslashes
and a quoted logical argument are checked. The fixtures compile nothing and
dispatch no native rename controls. A failing batch must preserve its exit code
and prevent the chained child from starting. They add at most 64 KiB inside the existing
32-MiB post-build SDK scratch bound and the existing three-minute build step.
Neither the actual CI-shell fixtures nor the SDK can run on this Mac.

The failed run's one-push/one-run permission was consumed. The SDK follow-up was
separately approved and completed at `f5ba780`; its permission is now consumed too.
The worker evidence repair above is a separately proposed next step. Case selection,
limits, artifact cap and all production write gates remain unchanged.

## Original diagnostic preparation

This unpublished prototype supplies a staged transaction protocol, a deterministic
object/namespace adapter, fault tests, concrete native request preparation, and
an executable diagnostic worker/controller for a future owned Windows runner.
It is separate from PR175 and PR176. Their source, receipts and failed native
controls are unchanged. Production Windows metadata writes remain disabled.

Run the bounded offline suite with the existing runtime:

```sh
/opt/homebrew/bin/python3.13 -I -B run_executor_offline.py
```

The combined runner blocks network, process-launch and native-library audit
events. It limits CPU time to 20 seconds and checks a 15-second wall bound and
128 MiB peak RSS. It writes only executor-offline-evidence.json and its test log.
The protocol and simulated Windows fixtures live in memory; no application
metadata, physical Windows test fixtures or private inputs are accessed. These
tests are implementation validation, not an independent source review or
native Windows qualification. The original 31-test receipts and source are
preserved under baseline-31-tests; the mixed-case reserved-path correction adds
two core regressions.

## File and ownership contract

The accepted contract is `trusted-cooperating-owner/v1`. Cooperating readers and
writers use the same lock, retain the admitted run identity, and refuse pending
or unfinished/foreign history. The prototype does not exclude hostile same-owner
mutations. Its raw mutation methods are adversarial test seams, not confinement.

An update installs a new file object. Already-open handles and aliases to the old
object retain the old generation and its unchanged bytes. Every meaningful group
update replaces its members; an entirely byte-identical group preserves all file
identities and creates no journal. Readers must reopen under the cooperative lock
and barrier. Raw readers can see mixed generations or a missing member between
the two moves; no raw group atomicity is claimed.

Existing live files must be plain, bounded, single-link objects. Output preconditions
can pin the exact old identity, bytes, hash and link count; when supplied, their
keys must cover the whole output set. Separate input and run/parent identity
bindings remain stable. New outputs are exact UTF-8 bytes. The prototype supports
ASCII relative names with case-insensitive matching, including existing names,
parents and journals; Windows Unicode case behavior remains unqualified.

The protocol creates an owned empty pending barrier and a fixed, separately owned
history journal. It retains independent exact before-byte copies, staged file
identities, old displaced objects, and an exact plan binding. Existing outputs
move to reserved journal slots by no-replace moves; staged objects then move to
vacant live slots by no-replace moves. Each operation records completion before
an after-operation fault can report a late error. Source/destination ownership,
bytes, inputs and directory bindings are checked around the transitions.

Rollback withdraws only a still-bound owned installed object, restores only the
exact displaced old object into a vacant live slot, and never overwrites or deletes
a foreign collision. Observed aliasing, changed bindings, uncertain operations or
failed rollback retain recovery evidence and block subsequent cooperation. A lost
pending-marker restoration remains explicitly uncertain; an unfinished journal
also blocks reader/writer admission. No automatic restart recovery or blind retry
is implemented. A single transaction object is single-use.

Terminal journal state and flush acknowledgements are abstract model operations.
They are not real on-disk persistence or crash proof. A native backend must provide
bounded durable state/commit records and prove restart behavior before integrating
this additional history admission into production readers. If the named run is
replaced, recovery does not create markers in the foreign replacement run; the
owned original journal/barrier remains preserved and readers reject root drift.

## Native request preparation

`native_contract.py` prepares typed requests and unqualified AMD64 buffers for
`NtSetInformationFile` class 10 and SDK-built `SetFileInformationByHandle` class 3.
Both are candidates. No API is selected or dispatched. The adapter's `move` and
`flush` methods always refuse, as does the production entrypoint.

Requests bind source, run and both parents; require exact FileIdInfo/volume/link/
byte readback, zero replacement flags, one relative destination leaf, granted
DELETE-access readback, same-form positive baselines, and source/destination
reconciliation on every result. The actual owned pending leaf is permitted only
for explicit empty-barrier archive/restore operations. General reserved paths
and arbitrary operation kinds remain refused.

The sample JSON uses fictitious handle tokens and object IDs. Buffer packing
tests verify only the proposed offsets and byte lengths. The byte-identical
retained SDK/probe sources in `references/` were neither compiled nor executed
on this Mac.
Correct packing alone does not explain or fix the prior native error 87.

Native qualification must establish regular-file and empty-directory no-replace
baselines, compatible access/sharing, actual SDK layout, collision preservation,
identity/link/reparse drift, flush/recovery behavior, old-handle compatibility,
cooperative serialization, and resource-controller accounting. It must retain
the original failed alias evidence. Replacing old objects avoids writing through
old aliases; staging alone does not prevent an adversary from aliasing new objects.

## Ten-case native diagnostic preparation

The diagnostic executes exactly eight positive rows and two no-clobber controls.
Each of a regular file and empty directory uses Win32 class 3 with a null-root
absolute path, Win32 class 3 with a held-parent leaf, SDK-built Win32 class 3 with
a held-parent leaf, and NT class 10 with a held-parent leaf. Two regular-file
collisions use the held-parent ctypes Win32 and NT forms. The collision fixture
creates `TaRgEt` while requesting `target`; both object identities and bytes
must survive. Each collision requires its matching API/form/builder/access/
sharing positive baseline. The six retained hardlink reference probes are never
selected or dispatched.

Each row records source/destination parents, source and target FileIdInfo,
volume/link/reparse/read-byte bindings, requested and NtQueryObject-granted
access, sharing, proposed buffer layout/hash/length, installed SDK layout,
Win32 BOOL/failure-only LastError or NTSTATUS/IO_STATUS_BLOCK, and before/after
reconciliation. Files remain open through the move. A late error after an
observed completed move is recorded without retry and does not qualify the
baseline. Missing SDK/layout proof or admission errors produce failed mandatory
rows, never successful skips.

An unresolved STATUS_PENDING retains its IOSB, buffer and source handle in a
global custody list, stops native progression, records UNKNOWN and exits the
worker without Python destroying those buffers. A finally block makes this
exit unconditional even if serialization, receipt open/write/flush or stream
flush fails. Owned process termination settles the retained request; the
controller must prove terminal accounting. Directory enumeration and source
reads use the trusted-owner contract, not hostile same-owner confinement.
Windows lacks Python O_NOFOLLOW here; the reader uses a bounded cap+1 retained
descriptor read with pre/open/post identity/link/kind checks.

Only a future explicitly approved diagnostic invocation may create native
fixtures. The worker refuses non-Windows, non-AMD64, non-Python-3.13, a missing
explicit invocation flag, source/head/SDK byte drift, a foreign synthetic root
marker, or absence of a matching one-process JobObject. The controller creates
the root/marker and launches the worker suspended with a fixed argv and stripped
environment. It assigns and reads back the JobObject before the one resume.
CPU affinity is one logical processor; a CPU hard cap represents at most one
processor. Process and job memory limits are each 512 MiB; the active-process
limit is one. Breakaway is refused.

The controller budget is 300 seconds including admission, cleanup and ZIP;
the worker budget is 290 seconds and cleanup receives at most the remaining
10 seconds. Complete assigned-PID and active-process accounting, primary-process
signaling, and successful process/thread/job closes are required for terminal
success. A late CreateProcess error reconciles returned handles/PID without
retry. Unknown cleanup retains custody and prevents ZIP collection. The worker
blocks sockets and additional processes. The separate workflow must compile
the SDK helper with its already installed SDK/MSVC, under its separate 3-minute
build and 32-MiB post-build acceptance bounds.

Packaging uses deterministic uncompressed ZIP entries. Each evidence file is
at most 128 KiB, at most 128 files are accepted, and owned scratch file bytes
plus the ZIP are at most 1 MiB. The prebuilt DLL, installed runtime and pinned
source are outside that owned root. Later retention is seven days and collection
is once. controller-result.json inside the ZIP binds the pre-packaging terminal
receipt; final console JSON adds artifact digest/size and total elapsed time,
avoiding a self-referential checksum.

The standalone interface is:

```text
python -I -B build_manifest.py --source-root ABS_SOURCE --expected-head EXACT_GIT_HEAD --sdk-dll ABS_PREBUILT_DLL --output ABS_FRESH_MANIFEST
python -I -B native_controller.py --source-root ABS_SOURCE --scratch-parent ABS_RUNNER_TEMP --sdk-dll ABS_PREBUILT_DLL --source-manifest ABS_MANIFEST --source-manifest-sha256 SHA256 --expected-head EXACT_GIT_HEAD --route-id ROUTE --owner-clearance-id CLEARANCE_LABEL --artifact ABS_FRESH_ZIP
```

Entry points explicitly add only their script root for isolated-mode sibling
imports. A nonempty, pinned references/__init__.py prevents an ambient regular
package from taking over the retained reference module. The manifest binds ten
runtime sources and the prebuilt SDK DLL; the Git head is supplied by the
workflow rather than embedded in source. An `approved-publication:<head>` label
is custody metadata, not proof of human approval. The future publisher must
enforce actual approval before its sole push. No dispatch, push, workflow run or
approval is performed here.

## Deliverables

- `staged_replacement.py`: synthetic adapter and staged group transaction.
- `test_staged_replacement.py`: protocol, failure, alias, collision and packet tests.
- `native_contract.py`: typed fail-closed native request preparation.
- `diagnostic_core.py`: pure exact-row assessment, manifest and ZIP contracts.
- `native_worker.py`: gated native diagnostic APIs and reconciliation.
- `native_controller.py`: bounded JobObject custody and artifact controller.
- `build_manifest.py`: source/head and prebuilt SDK DLL binding.
- `test_diagnostic_executor.py` and `run_executor_offline.py`: bounded memory-only
  syscall/controller/packaging validation combined with the core tests.
- `native_request_contract.json`: sample packets for all forward/rollback moves.
- `offline-evidence.json` and `offline-tests.log`: bounded test evidence and hashes.
- `parent-offline-verification.json` and `parent-offline-test.log`: independent
  parent verification of the frozen protocol/test/request source.

No publication, CI, installation, model/broker, GCP, Windows desktop, private-data
or production operation is performed by this preparation. Production adapter
dispatch remains disabled. The retained `f5ba780` run establishes its shell
fixtures and SDK compilation. Native controls, directory flush/durability/crash
guarantees, restart recovery, hostile alias exclusion and
application integration remain unqualified. Ten successful native rows would
qualify only the observed primitive baselines, not production writes.
