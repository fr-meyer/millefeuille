# Trusted manifest v1 and shared framing API

Public stdlib-only API in tests.fixtures.evidence_transport:
frame_receipt(kind: str, receipt: dict) -> list[str], kind F05 or S04;
runtime_identity() -> dict; Reassembler.feed(bytes) -> None or
(kind, canonical_payload_bytes, receipt_dict); finish() rejects truncation.
A sequence cannot interleave another receipt. Between its frames, at most 32
nonframe lines and 16384 bytes (cumulative per receipt) are permitted. Generic
bounded progress noise is allowed because GitHub merges stdout/stderr. A line
containing MILLEFEUILLE_EVIDENCE, EVIDENCE_FRAME or FRAME_V1: but not a valid
frame/envelope fails, including clipped/malformed markers. Missing final frames
still fail at finish; index sequence, identity, canonical bytes, SHA, length,
frame/count/receipt bounds and deadline remain strict.

Frames may start at byte zero, after a GitHub ISO timestamp followed by a space,
or after that timestamp and/or a unittest test_name (qualified class) ... prefix.
Arbitrary pre-frame content is rejected, not searched past. The F05 emitter uses
one bounded stdout write per receipt (plus flush), reducing but not claiming to
eliminate OS/merged-stream interruption. frame_receipt's shared API is unchanged.

This replaces the unsupported contiguous-nonframe requirement after archived run
43564003732 (Ubuntu 3.11) showed frame 0, unittest progress, then frame 1.
It does not waive any failed check or authorize accepting the failed run.
Regression fixtures use synthetic payloads, not recovered acceptance evidence.

Manifest JSON object (externally verified, never extracted from log claims):

    {
      "schema": "millefeuille-evidence-manifest/v1",
      "GITHUB_SHA": "<exact externally verified head>",
      "GITHUB_RUN_ID": "<string>",
      "GITHUB_RUN_ATTEMPT": "<string>",
      "source_sha256": {"<each exact SOURCE_PATHS entry>": "<64 lowercase hex>"},
      "worker_sha256": "<sha256 of exact CRASH_WORKER UTF-8 literal>",
      "cells": [
        {"job": "<exact ZIP top-level job directory>",
         "runtime": {"GITHUB_SHA": "<same head>", "GITHUB_RUN_ID": "<same run>",
                     "GITHUB_RUN_ATTEMPT": "<same attempt>",
                     "RUNNER_OS": "Linux", "python": "3.12"},
         "methods": ["<full method id 1>", "<full method id 2>"],
         "s04_case_ids": ["<five exact expected IDs on intended Linux cells>"]}
      ],
      "s04_source_sha256": {"<exact declared nonempty S04 source set>": "<hash>"}
    }

cells has exactly six distinct job entries, each two methods from METHOD_SEAMS.
For cells without S04 omit s04_case_ids or use []. For intended S04 Linux cells
supply exactly five distinct IDs. s04_source_sha256 is required when S04 occurs;
it is compared for exact equality, not subset inclusion. F05 source_sha256 exact
set is SOURCE_PATHS in tests/fixtures/f05_receipts.py (nine paths). Parent must
populate S04's full expected set, not an arbitrary partial set.

S04 payload convention required by this CLI: receipt.runtime equals shared
runtime_identity(); receipt.case_id is the expected case ID;
receipt.source_sha256 equals trusted s04_source_sha256. Other payload fields are
opaque and preserved verbatim with canonical bytes, digest, member and job
context. This is transport/source/runtime/case coverage only; the parent remains
responsible for all S04 scientific semantics. No S04 production modules imported.

The trust boundary is the externally prepared manifest and authenticated archived
run association. Self-reported identity alone is never sufficient. Archive output
status explicitly distinguishes F05 custody acceptance from pending S04 semantics.


F05 prospective custody amendment (owner 2026-11-14 18:32 KST): acceptance now
requires exact complete per-stage OS/seam-bound transaction inventories, not
whole-set death/retry equality. Only index-publication retry adds the sole
original card (Linux card-exchange.json, Windows displaced-card.json), fully
bound to the original FileImage; all old images remain unchanged and retained
images cannot alias current canonical card/index. See f05-recovery-evidence.md
for exact sets, counts and anti-alias requirements. No transport/CLI/schema or
raw source normalization change. Historical 43575513843 remains REJECTED; local
static source validation grants no runtime, CI, scientific or release acceptance.

Complete F05 stage sets also include the exact persistent run/.card-index.lock
producer artifact (absent before, one NUL byte after death, full FileImage retained
at retry, anti-alias checked) and missing canonical index parent ancestors. The
lock is not a transaction entry. Initial producer directories are retained; no
arbitrary lock, directory or sibling allowance is introduced.
