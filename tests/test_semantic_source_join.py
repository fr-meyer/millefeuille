"""Actual canonical joins and positive offline acceptance, not refusal stand-ins."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.evidence_answer import answer_evidence, canonical, seal
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import digest
from millefeuille.domain.semantic_review_orchestrator import plan_reviewed_acceptance
from millefeuille.domain.semantic_source_join import (
    build_canonical_source_join,
    validate_source_review,
)
from tests.fixtures.offline_core_source_fixture import budget, plan_inputs, prepare
from tests.platform_capabilities import requires_posix_batch_publication


@requires_posix_batch_publication
class CanonicalJoinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.case = prepare(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_real_eight_scenario_six_output_positive_and_unchanged_inputs(self):
        c = self.case
        before = {
            str(p): digest(p.read_bytes())
            for p in c["resolved"].source_pack_dir.rglob("*")
            if p.is_file()
        }
        ledger = c["pipeline_budget"]
        producer_bytes = ledger.read_bytes
        self.assertGreater(producer_bytes, 0)
        result = plan_reviewed_acceptance(**plan_inputs(c), budget=ledger)
        self.assertEqual(len(result["evaluation"]["scenarios"]), 8)
        self.assertTrue(all(s["passed"] for s in result["evaluation"]["scenarios"]))
        self.assertGreater(ledger.read_bytes, producer_bytes)
        self.assertEqual(len(result["exact_plan"]), 6)
        self.assertEqual(len({p.ref for p in result["exact_plan"]}), 6)
        self.assertEqual(result["decision"]["outcome"], "pass")
        after = {
            str(p): digest(p.read_bytes())
            for p in c["resolved"].source_pack_dir.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)
        print(
            "CANONICAL_POSITIVE_LEDGER", ledger.opens, ledger.scans, ledger.read_bytes
        )

    def test_hierarchical_crosspage_wrapper_and_raw_utf8_crlf(self):
        join = self.case["join"]
        main = next(a for a in join.attachments if a.attachment_key == "MAIN1234")
        method = next(s for s in main.sections if s.heading == "Method")
        results = next(s for s in main.sections if s.heading == "Results")
        self.assertEqual([p.local_page for p in method.page_ranges], [1, 2])
        self.assertEqual(method.original_parent_kind, "generated_source_wrapper")
        self.assertIsNotNone(method.wrapper_heading_sha256)
        self.assertIsNone(method.span_parent_id)
        self.assertEqual(method.attachment_key, "MAIN1234")
        self.assertEqual(method.wrapper_attachment_key, "MAIN1234")
        self.assertEqual(method.wrapper_global_page, 1)
        self.assertGreater(method.wrapper_selected_line, 0)
        self.assertIsNone(method.original_scientific_parent_id)

        self.assertIsNone(results.original_parent_id)
        self.assertEqual(results.span_parent_id, method.canonical_section_id)
        raw = (self.case["resolved"].source_pack_dir / main.extraction_ref).read_bytes()
        s = self.case["source_review"]["source_spans"]["method"]
        self.assertEqual(raw[s["byte_start"] : s["byte_end"]].decode(), s["text_utf8"])
        self.assertIn(b"\r\n", raw)
        supp = next(a for a in join.attachments if a.attachment_key == "SUPP5678")
        self.assertFalse(
            (self.case["resolved"].source_pack_dir / supp.extraction_ref)
            .read_bytes()
            .endswith(b"\n")
        )

    def test_every_source_join_drift_refuses(self):
        original = self.case["source_review"]
        for mutation in [
            lambda x: x.update(paper_id="foreign"),
            lambda x: x.update(run_id="foreign"),
            lambda x: x.update(source_hash="sha256:" + "0" * 64),
            lambda x: x["canonical_join"]["attachments"][0].update(
                source_ref="unrelated.pdf"
            ),
            lambda x: x["canonical_join"]["attachments"][0].update(
                extraction_ref="unrelated.md"
            ),
            lambda x: x["canonical_join"]["attachments"][0]["sections"][0].update(
                original_parent_id="foreign-wrapper"
            ),
            lambda x: x["canonical_join"]["attachments"][0]["sections"][0].update(
                span_parent_id="foreign-real-parent"
            ),
            lambda x: x["canonical_join"]["attachments"][0]["sections"][0].update(
                wrapper_heading_sha256="sha256:" + "0" * 64
            ),
            lambda x: x["source_spans"]["method"].update(page=2),
            lambda x: x["source_spans"]["method"].update(section_id="wrapper"),
            lambda x: x["source_spans"]["method"].update(byte_start=1),
        ]:
            r = deepcopy(original)
            mutation(r)
            with self.assertRaises(MillefeuilleContractError):
                validate_source_review(
                    resolved=self.case["resolved"], source_review=r, budget=budget()
                )

    def test_independent_gold_candidate_receipt_and_claim_drift_refuse(self):
        for field in [
            "candidate_responses",
            "candidate_response_sha256s",
            "independent_gold",
            "independent_review_record",
            "review_result",
        ]:
            args = deepcopy(plan_inputs(self.case))
            if field == "candidate_responses":
                args[field][0] = b"{}"
            elif field == "candidate_response_sha256s":
                args[field][0] = "sha256:" + "0" * 64
            elif field == "independent_gold":
                args[field][0]["expected_verdict"] = "no_evidence"
            elif field == "independent_review_record":
                args[field]["source_review_sha256"] = "sha256:" + "0" * 64
            else:
                args[field]["claims"][0]["qualification"] = (
                    "Authentic real science proven by a hash"
                )
            with (
                self.subTest(field=field),
                self.assertRaises(MillefeuilleContractError),
            ):
                plan_reviewed_acceptance(**args, budget=budget())

    def test_resealed_incomplete_or_foreign_corpus_cannot_authorize_absence(self):
        for mutate in [
            lambda c: c["spans"].pop(),
            lambda c: c["attachments"].pop(),
            lambda c: c["spans"][0].update(source_sha256="sha256:" + "0" * 64),
            lambda c: c["spans"][0].update(source_ref=c["spans"][-1]["source_ref"]),
            lambda c: c["spans"][0].update(byte_end=c["spans"][0]["byte_end"] + 2),
            lambda c: c["spans"].append(deepcopy(c["spans"][0])),
        ]:
            r = deepcopy(self.case["source_review"])
            mutate(r["corpus"])
            r["corpus"]["manifest_sha256"] = seal(r["corpus"], "manifest_sha256")
            r["corpus_sha256"] = r["corpus"]["manifest_sha256"]
            with self.assertRaises(MillefeuilleContractError):
                validate_source_review(
                    resolved=self.case["resolved"], source_review=r, budget=budget()
                )

    def test_claim_resealed_but_not_in_source_refuses(self):
        review = deepcopy(self.case["source_review"])
        review["reviewed_claims"][0]["claim_text"] = "An unsupported invented result."
        review["reviewed_claims"][0]["claim_text_sha256"] = digest(
            review["reviewed_claims"][0]["claim_text"].encode()
        )
        with self.assertRaises(MillefeuilleContractError):
            validate_source_review(
                resolved=self.case["resolved"], source_review=review, budget=budget()
            )

    def test_strict_package_still_reference_only_and_unreviewed_join_refuses(self):
        with self.assertRaises(MillefeuilleContractError):
            build_canonical_source_join(
                resolved=self.case["resolved"], reviewed_package=None, budget=budget()
            )

    def test_changed_actual_source_value_changes_answer_and_stale_gold_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            changed = prepare(Path(temp), value="19")
            response = answer_evidence(
                request=changed["questions"][2],
                corpus=changed["corpus"],
                reader=changed["reader"],
                budget=budget(),
            )
            self.assertEqual(response["outcome"], "supported")
            self.assertIn("19 ms", canonical(response).decode())
            self.assertNotEqual(
                changed["candidate_responses"][2], self.case["candidate_responses"][2]
            )
            args = plan_inputs(changed)
            args["independent_gold"] = self.case["independent_gold"]
            with self.assertRaises(MillefeuilleContractError):
                plan_reviewed_acceptance(**args, budget=budget())
