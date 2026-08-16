# Read-only Status and Observability Contract

`millefeuille status` is a local, read-only join. It never initializes a
Zotero, provider, OpenKB, PageIndex, ConDB, or ChatIndex client; loads a
credential; reads private paper text; or changes a run package. This command
does not satisfy or replace any manual approval gate.

## Canonical and Progressive Inputs

The canonical form resolves one paper and run beneath a supplied source-pack
root:

```text
millefeuille status --source-pack-root <root> --paper-id <id> --run-id <id>
millefeuille status --source-pack-root <root> --item-key <key> --run-id <id>
```

It joins the source-pack manifest identity, stage manifest, and artifact index
before reading any public status artifact. Their paper, run, source hash, stage
map, artifact root, source-pack root, and manifest refs must agree exactly.
`--index` and `--artifact-root` remain supported as progressive inspection:
missing source-pack or stage-manifest components are reported `not-observed`
and block `--strict`, rather than being inferred from the index.

Operator-supplied roots and evidence paths reject parent traversal. Portable
run-local refs reject empty, dot, parent, absolute, drive-qualified, backslash,
noncanonical, oversized, and unsupported path segments before normalization.
The existing source-pack-relative manifest, selected-fulltext, and
index-status refs may contain parent segments only when they equal the single
relative literal derived independently from the pinned canonical roots; they
never pass through the general ref resolver.

## Exact Joins

Every status-consumed artifact record must be a strict kind/format/stage/privacy
record and its ref must occur literally in the unique owning
`StageRecord.outputs`. Inputs and outputs are unique.

- Retrieval lanes are unique and have exact lane-name/status equality and
  cardinality between the artifact index and retrieval index record. Selected
  full text, hierarchical summary, and paper-card refs must point to the exact
  indexed canonical artifacts. Written lane result refs must be stable local
  regular files and stage outputs.
- Acceptance identity, source-pack ref, result status, and stage status must
  agree.
- Classification is intentionally single-paper on this surface. Its one plan
  entry binds the observed paper and indexed decision ref; plan and decision
  mode, taxonomy version, and status agree exactly.
- The writeback index binds the exact indexed plan ref. Preview mode may be
  complete from either an exact classification-owned preview whose potentially
  private proposal payload is not opened, or a passed, validated `previewed`
  writeback plan; the joined state distinguishes those cases.
  An approved-live passed stage claiming `written` must additionally bind an
  exact, content-addressed indexed writeback result and matching local
  observations. Its source item version must equal the one exact version in
  the source-pack identity, and the live-state observation must equal the
  exact post-write item version in the result. Missing or differing versions
  across a multi-source pack are ambiguous and fail closed. Permanent or live
  execution remains outside this command.

Any missing, extra, ambiguous, drifted, linked/reparse, oversized, duplicate,
or unstable consumed record fails closed. The JSON parser also caps bytes,
depth, nodes, and integer size; rejects duplicate fields, cycles, non-finite
numbers, credentials, URLs, provider payload text, and private-payload markers;
and converts parser recursion failures into sanitized contract errors.

## Local Observation Envelopes

Repeat `--evidence <json>` to join at most one strict
`millefeuille-status-observation/v0.1` envelope per kind. Every envelope is
content-addressed and binds the exact paper, run, and source hash. Its authority
is always:

```json
{"authoritative": false, "source": "operator-supplied-local-observation"}
```

Summaries are kind-specific and contain no raw Zotero tags, notes, item
metadata, provider request/response text, ledger filenames or IDs, quality
prose, private excerpts, credentials, URLs, or paper bytes.

| Kind | Canonical binding | Sanitized summary |
|---|---|---|
| `provider-usage` | Indexed/stage-output model-provenance record validated by the first-party semantic validator | Record digest and exact call/input/output/total token counts |
| `index-ledger` | Exact canonical written lanes and retrieval-index digest | Sorted written lane/status pairs |
| `quality` | Strict indexed/stage-output completion-gate result | Record digest and exact status/check counts |
| `writeback-result` | Strict indexed/stage-output writeback result, artifact-index `result_ref`, and exact source/post-write item versions | Record, receipt, and audit digests plus exact status/operation count |
| `zotero-live-state` | Exact source-pack version, or exact post-write result version when present; never promoted to canonical authority | Bound item version and coarse consistent/drifted/not-observed states |

Mere artifact-name presence never satisfies an observation. Provider, ledger,
quality, and writeback envelopes are rejected when no corresponding canonical
claim exists or any claimed value drifts. Absence is shown as `not-observed`
and is nonblocking unless a canonical passed stage or approved-live mode
requires that kind.

A supplied Zotero live-state observation is ready only when its aggregate
status, tag state, and note state are all `consistent`. A `drifted` or
`not-observed` substate remains an explicit blocker even if the aggregate
status is `consistent`.

## Output and Exit Semantics

JSON output is deterministic, content-addressed, and excludes absolute local
paths and source identity metadata. Human output is a concise projection of
the same object. A preview run can be `preview-complete`. An approved-live run
can only be `observational-live-ready`; it is never reported as an unqualified
or authoritative live completion.

Recognized incomplete workflow states remain valid status observations rather
than contract failures. Index lanes may report `not-started`; writeback may
report `not-started`, `manual-gate`, `needs-review`, or `failed` for any mode.
Each is emitted unchanged and added to `blocking_items`, so non-strict status
returns the report and `--strict` returns `2`. Structurally incompatible
terminal mode/status pairs still fail with exit code `3`.

Exit codes are `0` for a valid observation, `2` with `--strict` when joined
blockers remain, and `3` for a contract or safety failure. Repeated inspection
has no filesystem effect.
