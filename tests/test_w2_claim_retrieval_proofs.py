"""W2 literal public/synthetic span proofs, not scientific or source authority."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.evidence_answer import answer_evidence, canonical, seal
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import digest
from millefeuille.domain.semantic_scenario_evaluator import evaluate_frozen_scenarios
from tests.fixtures.offline_core_source_fixture import budget, prepare
from tests.platform_capabilities import requires_posix_batch_publication

# Original persisted IDs are 0..7, not a newly invented eight-case taxonomy.
EXPECTED = (
    ("method",),
    ("supplement-procedure",),
    ("table",),
    ("method", "interpretation"),
    ("child", "parent", "table"),
    ("context", "interpretation", "supplement-procedure"),
    (),
    (),
)


@requires_posix_batch_publication
class W2ClaimRetrievalProofs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.case = prepare(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exact_eight_literal_claims_and_qualified_source_spans(self):
        c = self.case
        review = c["source_review"]
        for i, raw in enumerate(c["candidate_responses"]):
            with self.subTest(original_scenario_id=str(i)):
                response = json.loads(raw)
                excerpts = response["excerpts"]
                self.assertEqual(tuple(e["span_id"] for e in excerpts), EXPECTED[i])
                self.assertEqual(
                    response["outcome"],
                    "supported" if i < 6 else "ambiguous" if i == 6 else "no_evidence",
                )
                self.assertIn(
                    "no scientific entailment",
                    " ".join(response["explanation"]["limitations"]),
                )
                self.assertEqual(len(response["qualified_claims"]), len(excerpts))
                for claim, excerpt in zip(
                    response["qualified_claims"], excerpts, strict=True
                ):
                    source = review["source_spans"][excerpt["span_id"]]
                    self.assertEqual(excerpt, source)
                    span = next(
                        s
                        for s in review["corpus"]["spans"]
                        if s["span_id"] == excerpt["span_id"]
                    )
                    source_bytes = (
                        c["resolved"].source_pack_dir / span["source_ref"]
                    ).read_bytes()
                    literal = source_bytes[span["byte_start"] : span["byte_end"]]
                    self.assertEqual(literal.decode(), claim["text_utf8"])
                    self.assertEqual(digest(literal), excerpt["text_sha256"])
                    self.assertEqual(claim["excerpt_ids"], [span["span_id"]])
                    self.assertEqual(
                        claim["qualifiers_utf8"], span["table_coordinates"] or {}
                    )
                if i == 2:
                    self.assertEqual(
                        excerpts[0]["text_utf8"], "12 ms under dry conditions"
                    )
                    self.assertEqual(
                        response["qualified_claims"][0]["qualifiers_utf8"],
                        {
                            "table_id": "T1",
                            "row_label": "dry",
                            "column_label": "latency",
                            "unit_literal": "ms",
                        },
                    )
                if i >= 6:
                    self.assertEqual(response["qualified_claims"], [])
                    self.assertEqual(response["explanation"]["text_utf8"], "")

    def test_same_local_page_and_inventory_permutation_cannot_crosswire(self):
        c = self.case
        corpus = deepcopy(c["source_review"]["corpus"])
        corpus["attachments"].reverse()
        corpus["spans"].reverse()
        corpus["manifest_sha256"] = seal(corpus, "manifest_sha256")
        for i in (0, 1, 3, 4, 5, 6):
            with self.subTest(original_scenario_id=str(i)):
                original = json.loads(c["candidate_responses"][i])
                q = deepcopy(c["questions"][i])
                q["corpus_sha256"] = corpus["manifest_sha256"]
                actual = answer_evidence(
                    request=q, corpus=corpus, reader=c["reader"], budget=budget()
                )
                self.assertEqual(actual["outcome"], original["outcome"])
                self.assertEqual(actual["excerpts"], original["excerpts"])
                self.assertEqual(
                    actual["qualified_claims"], original["qualified_claims"]
                )
                self.assertEqual(actual["ambiguity"], original["ambiguity"])

    def test_resealed_material_claim_without_literal_support_is_not_accepted(self):
        c = self.case
        responses = list(c["candidate_responses"])
        forged = json.loads(responses[2])
        forged["qualified_claims"][0]["text_utf8"] = "12 ms under all conditions"
        forged["candidate_response_sha256"] = seal(forged, "candidate_response_sha256")
        responses[2] = canonical(forged)
        result = evaluate_frozen_scenarios(
            independent_gold=c["independent_gold"],
            candidate_responses=responses,
            candidate_response_sha256s=[digest(r) for r in responses],
            source_review=c["source_review"],
        )
        self.assertFalse(result["scenarios"][2]["passed"])

    def test_known_gap_resealed_false_numeric_qualifier_must_not_pass(self):
        """Known production gap: literal text survives but dry becomes wet metadata."""
        c = self.case
        responses = list(c["candidate_responses"])
        forged = json.loads(responses[2])
        forged["qualified_claims"][0]["qualifiers_utf8"]["row_label"] = "wet"
        forged["candidate_response_sha256"] = seal(forged, "candidate_response_sha256")
        responses[2] = canonical(forged)
        result = evaluate_frozen_scenarios(
            independent_gold=c["independent_gold"],
            candidate_responses=responses,
            candidate_response_sha256s=[digest(r) for r in responses],
            source_review=c["source_review"],
        )
        self.assertFalse(result["scenarios"][2]["passed"])

    def _evaluate(self, responses=None, review=None):
        c = self.case
        responses = c["candidate_responses"] if responses is None else responses
        return evaluate_frozen_scenarios(
            independent_gold=c["independent_gold"],
            candidate_responses=responses,
            candidate_response_sha256s=[digest(r) for r in responses],
            source_review=c["source_review"] if review is None else review,
        )

    def test_resealed_qualifier_kind_text_identity_and_count_mutations_refuse(self):
        mutations = [
            (
                "row",
                2,
                lambda a: a["qualified_claims"][0]["qualifiers_utf8"].update(
                    row_label="wet"
                ),
            ),
            (
                "column",
                2,
                lambda a: a["qualified_claims"][0]["qualifiers_utf8"].update(
                    column_label="dose"
                ),
            ),
            (
                "table",
                2,
                lambda a: a["qualified_claims"][0]["qualifiers_utf8"].update(
                    table_id="T2"
                ),
            ),
            (
                "unit",
                2,
                lambda a: a["qualified_claims"][0]["qualifiers_utf8"].update(
                    unit_literal="s"
                ),
            ),
            (
                "missing",
                2,
                lambda a: a["qualified_claims"][0]["qualifiers_utf8"].pop("row_label"),
            ),
            (
                "extra",
                2,
                lambda a: a["qualified_claims"][0]["qualifiers_utf8"].update(
                    extra="false"
                ),
            ),
            (
                "boolean",
                2,
                lambda a: a["qualified_claims"][0]["qualifiers_utf8"].update(
                    row_label=True
                ),
            ),
            (
                "null",
                2,
                lambda a: a["qualified_claims"][0].update(qualifiers_utf8=None),
            ),
            (
                "wrong-kind",
                2,
                lambda a: a["qualified_claims"][0].update(kind="verbatim_prose"),
            ),
            (
                "wrong-text",
                2,
                lambda a: a["qualified_claims"][0].update(
                    text_utf8="12 ms under all conditions"
                ),
            ),
            ("unlinked", 2, lambda a: a["qualified_claims"][0].update(excerpt_ids=[])),
            (
                "duplicate-id",
                2,
                lambda a: a["qualified_claims"][0].update(
                    excerpt_ids=["table", "table"]
                ),
            ),
            (
                "foreign-id",
                2,
                lambda a: a["qualified_claims"][0].update(excerpt_ids=["method"]),
            ),
            (
                "multiple-id",
                2,
                lambda a: a["qualified_claims"][0].update(
                    excerpt_ids=["table", "method"]
                ),
            ),
            ("missing-claim", 2, lambda a: a.update(qualified_claims=[])),
            (
                "duplicate-claim",
                2,
                lambda a: a["qualified_claims"].append(
                    deepcopy(a["qualified_claims"][0])
                ),
            ),
            (
                "extra-claim-field",
                2,
                lambda a: a["qualified_claims"][0].update(authority=True),
            ),
            (
                "prose-qualifier",
                0,
                lambda a: a["qualified_claims"][0].update(
                    qualifiers_utf8={"row_label": "dry"}
                ),
            ),
            (
                "procedure-qualifier",
                1,
                lambda a: a["qualified_claims"][0].update(
                    qualifiers_utf8={"unit_literal": "ms"}
                ),
            ),
            (
                "prose-kind",
                0,
                lambda a: a["qualified_claims"][0].update(kind="verbatim_procedure"),
            ),
            (
                "ambiguous-claim",
                6,
                lambda a: a["qualified_claims"].append({"text_utf8": "unsupported"}),
            ),
            (
                "absent-claim",
                7,
                lambda a: a["qualified_claims"].append({"text_utf8": "unsupported"}),
            ),
            (
                "forged-excerpt-page-bool",
                0,
                lambda a: a["excerpts"][0].update(page=True),
            ),
        ]
        for name, index, mutate in mutations:
            with self.subTest(mutation=name):
                responses = list(self.case["candidate_responses"])
                answer = json.loads(responses[index])
                mutate(answer)
                answer["candidate_response_sha256"] = seal(
                    answer, "candidate_response_sha256"
                )
                responses[index] = canonical(answer)
                self.assertFalse(
                    self._evaluate(responses)["scenarios"][index]["passed"]
                )

    def test_independent_corpus_stale_hash_and_resealed_inconsistent_metadata_refuse(
        self,
    ):
        mutations = [
            (
                "coordinates",
                lambda r: next(s for s in r["corpus"]["spans"] if s["kind"] == "table")[
                    "table_coordinates"
                ].update(row_label="wet"),
                False,
            ),
            ("missing-corpus", lambda r: r.pop("corpus"), False),
            ("binding", lambda r: r.update(corpus_sha256="0" * 64), False),
            (
                "duplicate-span",
                lambda r: r["corpus"]["spans"].append(
                    deepcopy(r["corpus"]["spans"][0])
                ),
                True,
            ),
            (
                "duplicate-attachment",
                lambda r: r["corpus"]["attachments"].append(
                    deepcopy(r["corpus"]["attachments"][0])
                ),
                True,
            ),
            (
                "foreign-span",
                lambda r: r["corpus"]["spans"][0].update(span_id="foreign"),
                True,
            ),
            (
                "locator",
                lambda r: r["corpus"]["spans"][0].update(attachment_key="SUPP5678"),
                True,
            ),
            ("bounds", lambda r: r["corpus"]["spans"][0].update(byte_start=-1), True),
            ("page-type", lambda r: r["corpus"]["spans"][0].update(page=True), True),
            (
                "unknown-kind",
                lambda r: r["corpus"]["spans"][0].update(kind="conclusion"),
                True,
            ),
            (
                "prose-qualifiers",
                lambda r: r["corpus"]["spans"][0].update(table_coordinates={}),
                True,
            ),
            (
                "table-type",
                lambda r: next(s for s in r["corpus"]["spans"] if s["kind"] == "table")[
                    "table_coordinates"
                ].update(row_label=True),
                True,
            ),
        ]
        for name, mutate, reseal in mutations:
            with self.subTest(mutation=name):
                review = deepcopy(self.case["source_review"])
                mutate(review)
                if reseal:
                    review["corpus"]["manifest_sha256"] = seal(
                        review["corpus"], "manifest_sha256"
                    )
                    review["corpus_sha256"] = review["corpus"]["manifest_sha256"]
                with self.assertRaises(MillefeuilleContractError):
                    self._evaluate(review=review)

    def test_original_eight_positive_without_expected_failure(self):
        result = self._evaluate()
        self.assertEqual(
            [s["id"] for s in result["scenarios"]], [str(i) for i in range(8)]
        )
        self.assertTrue(all(s["passed"] for s in result["scenarios"]))
