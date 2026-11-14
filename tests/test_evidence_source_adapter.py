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
