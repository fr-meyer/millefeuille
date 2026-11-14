"""Duplicate identity and competing-version regressions on existing APIs."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.evidence_answer import answer_evidence, canonical, seal
from millefeuille.domain.evidence_source_adapter import ReadBudget, iter_verified_spans
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import digest
from millefeuille.domain.source_qualification import (
    NativeSourceObservation,
    compare_native_sources,
)
from tests.platform_capabilities import requires_pinned_root_reads
from tests.test_evidence_answer import fixture, request


class _CountingReader:
    def __init__(self, reader):
        self.reader = reader
        self.reviewed_package = reader.reviewed_package
        self.calls = []

    def read(self, source_ref, budget):
        self.calls.append(source_ref)
        return self.reader.read(source_ref, budget)


class RemainingEvidenceSourceTests(unittest.TestCase):
    @requires_pinned_root_reads
    def test_s06_alias_source_hash_refuses_before_evidence_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            corpus, reader = fixture(Path(temp))
            # Both package source objects really become identical; keep package
            # manifest and corpus bindings valid, not merely the comparison label.
            package = reader.reviewed_package
            root = package["package_root"]
            (root / "supp.txt").write_bytes((root / "main.txt").read_bytes())
            manifest = json.loads((root / "manifest.json").read_bytes())
            same = digest((root / "main.txt").read_bytes())
            manifest["files"]["supp.txt"] = same
            raw = canonical(manifest)
            (root / "manifest.json").write_bytes(raw)
            package["manifest_sha256"] = digest(raw)
            for attachment in corpus["attachments"]:
                attachment["source_sha256"] = same
            for span in corpus["spans"]:
                span["source_sha256"] = same
            corpus["manifest_sha256"] = seal(corpus, "manifest_sha256")
            for reverse in (False, True):
                c = copy.deepcopy(corpus)
                if reverse:
                    c["attachments"].reverse()
                    c["spans"].reverse()
                c["manifest_sha256"] = seal(c, "manifest_sha256")
                budget = ReadBudget()
                counted = _CountingReader(reader)
                result = answer_evidence(
                    request=request(c), corpus=c, reader=counted, budget=budget
                )
                self.assertEqual(result["outcome"], "integrity_refused")
                self.assertEqual(result["excerpts"], [])
                self.assertEqual(result["qualified_claims"], [])
                self.assertEqual(result["search_proof"]["visited_spans"], 0)
                self.assertEqual(counted.calls, [])
                # Inventory validation remains charged; distinguish the intended
                # duplicate refusal from an unrelated package validation error.
                self.assertGreater(budget.scans, 0)
                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "duplicate source evidence requires review",
                ):
                    list(
                        iter_verified_spans(
                            corpus=c,
                            selection=request(c)["selection"],
                            budget=ReadBudget(),
                            reader=counted,
                        )
                    )
                self.assertEqual(counted.calls, [])

    @requires_pinned_root_reads
    def test_s06_distinct_sources_still_produce_literal_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            corpus, reader = fixture(Path(temp))
            counted = _CountingReader(reader)
            result = answer_evidence(
                request=request(corpus),
                corpus=corpus,
                reader=counted,
                budget=ReadBudget(),
            )
            self.assertEqual(result["outcome"], "supported")
            self.assertTrue(result["excerpts"])
            self.assertTrue(counted.calls)

    def test_s07_conflicting_versions_never_choose_authority(self):
        # No timestamp or filename is authority in this observation interface.
        older = NativeSourceObservation("1" * 64, ("Value 17",), "3" * 64, "fixture")
        newer = NativeSourceObservation("2" * 64, ("Value 18",), "4" * 64, "fixture")
        for left, right in ((older, newer), (newer, older)):
            self.assertEqual(
                compare_native_sources(left, right), "competing-content-needs-review"
            )
            self.assertEqual(left.disposition, "needs-review")
            self.assertEqual(right.disposition, "needs-review")
            self.assertEqual({left.pages[0], right.pages[0]}, {"Value 17", "Value 18"})


class ExtractionEquivalenceTests(unittest.TestCase):
    def make_texts(self, root, texts):
        from millefeuille.domain.evidence_source_adapter import saved_identity

        corpus, reader = fixture(root)
        for attachment, raw in zip(corpus["attachments"], texts, strict=True):
            ref = attachment["key"] + ".txt"
            (root / ref).write_bytes(raw)
            reader.files[ref] = {
                "identity": saved_identity(root / ref),
                "sha256": digest(raw),
            }
            attachment["extraction_sha256"] = digest(raw)
            for span in corpus["spans"]:
                if span["attachment_key"] == attachment["key"]:
                    span.update(
                        source_ref=ref,
                        byte_start=0,
                        byte_end=len(b"optics"),
                        text_sha256=digest(b"optics"),
                    )
        corpus["completeness"]["expected_source_bytes"] = 4 * len(b"optics")
        corpus["manifest_sha256"] = seal(corpus, "manifest_sha256")
        return corpus, reader

    @requires_pinned_root_reads
    def test_whole_equivalence_all_orders_and_narrowed_scopes(self):
        with tempfile.TemporaryDirectory() as temp:
            corpus, reader = self.make_texts(
                Path(temp), [b"optics same", b"optics same"]
            )
            for attachment_reverse in (False, True):
                for span_reverse in (False, True):
                    for keys, page, sections in (
                        (None, None, None),
                        (["main"], None, None),
                        (["supp"], 1, ["protocol"]),
                        (None, 2, None),
                        (None, None, ["method"]),
                    ):
                        with self.subTest(
                            attachments=attachment_reverse,
                            spans=span_reverse,
                            keys=keys,
                            page=page,
                            sections=sections,
                        ):
                            c = copy.deepcopy(corpus)
                            if attachment_reverse:
                                c["attachments"].reverse()
                            if span_reverse:
                                c["spans"].reverse()
                            c["manifest_sha256"] = seal(c, "manifest_sha256")
                            before = copy.deepcopy(c)
                            r = request(
                                c,
                                selection={
                                    "attachment_keys": keys,
                                    "local_page": page,
                                    "section_ids": sections,
                                },
                            )
                            budget, counted = ReadBudget(), _CountingReader(reader)
                            result = answer_evidence(
                                request=r, corpus=c, reader=counted, budget=budget
                            )
                            self.assertEqual(result["outcome"], "integrity_refused")
                            self.assertEqual(result["excerpts"], [])
                            self.assertEqual(result["qualified_claims"], [])
                            proof = result["search_proof"]
                            self.assertEqual(proof["visited_spans"], 0)
                            self.assertFalse(
                                proof["all_authorized_selected_spans_visited"]
                            )
                            self.assertEqual(
                                len(proof["unread_refs"]), proof["expected_spans"]
                            )
                            self.assertEqual(counted.calls, [])
                            self.assertGreater(budget.scans, 0)
                            self.assertEqual(c, before)
                            with self.assertRaisesRegex(
                                MillefeuilleContractError,
                                "same extraction different sources requires review",
                            ):
                                list(
                                    iter_verified_spans(
                                        corpus=c,
                                        selection=r["selection"],
                                        reader=counted,
                                        budget=ReadBudget(),
                                    )
                                )

    @requires_pinned_root_reads
    def test_shared_span_distinct_whole_extractions_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            corpus, reader = self.make_texts(
                Path(temp), [b"optics alpha", b"optics beta"]
            )
            result = answer_evidence(
                request=request(corpus),
                corpus=corpus,
                reader=reader,
                budget=ReadBudget(),
            )
            self.assertEqual(result["outcome"], "supported")
            self.assertEqual(len(result["excerpts"]), 4)
            self.assertEqual(
                {e["attachment_key"] for e in result["excerpts"]}, {"main", "supp"}
            )

    @requires_pinned_root_reads
    def test_missing_empty_malformed_hashes_refuse_without_evidence_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            corpus, reader = fixture(Path(temp))
            for invalid in (None, "", "0" * 63, "z" * 64, [], 42):
                with self.subTest(invalid=invalid):
                    c = copy.deepcopy(corpus)
                    if invalid is None:
                        del c["attachments"][0]["extraction_sha256"]
                    else:
                        c["attachments"][0]["extraction_sha256"] = invalid
                    c["manifest_sha256"] = seal(c, "manifest_sha256")
                    counted = _CountingReader(reader)
                    result = answer_evidence(
                        request=request(c),
                        corpus=c,
                        reader=counted,
                        budget=ReadBudget(),
                    )
                    self.assertEqual(result["outcome"], "integrity_refused")
                    self.assertEqual(result["excerpts"], [])
                    self.assertEqual(counted.calls, [])

    @requires_pinned_root_reads
    def test_forged_distinct_hash_cannot_escape_actual_read_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            c, reader = self.make_texts(Path(temp), [b"optics same", b"optics same"])
            c["attachments"][1]["extraction_sha256"] = digest(b"forged")
            c["manifest_sha256"] = seal(c, "manifest_sha256")
            counted, budget = _CountingReader(reader), ReadBudget()
            result = answer_evidence(
                request=request(c), corpus=c, reader=counted, budget=budget
            )
            self.assertEqual(result["outcome"], "integrity_refused")
            self.assertEqual(result["excerpts"], [])
            self.assertEqual(result["qualified_claims"], [])
            self.assertFalse(
                result["search_proof"]["all_authorized_selected_spans_visited"]
            )
            self.assertTrue(counted.calls)
            self.assertGreater(budget.read_bytes, 0)

    @requires_pinned_root_reads
    def test_duplicate_gate_preserves_inventory_budget_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            c, reader = self.make_texts(Path(temp), [b"optics same", b"optics same"])
            budget, counted = ReadBudget(max_read_bytes=0), _CountingReader(reader)
            result = answer_evidence(
                request=request(c), corpus=c, reader=counted, budget=budget
            )
            self.assertEqual(result["outcome"], "incomplete")
            self.assertTrue(result["search_proof"]["budget_exhausted"])
            self.assertEqual(result["excerpts"], [])
            self.assertEqual(counted.calls, [])
            self.assertEqual(budget.read_bytes, 0)
