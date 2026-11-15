"""Layout matching is opt-in; all evidence remains source bytes."""

from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.evidence_answer import (
    _layout_text,
    _norm,
    answer_evidence,
    seal,
)
from millefeuille.domain.evidence_source_adapter import ReadBudget
from millefeuille.domain.semantic_acceptance import digest
from tests.platform_capabilities import requires_pinned_root_reads
from tests.test_evidence_answer import fixture, request


class LayoutProjectionTests(unittest.TestCase):
    def test_layout_and_unicode(self):
        for raw, expected in [
            ("vol-\numes", "volumes"),
            ("vol- \r\n\tumes", "volumes"),
            ("vol-\rumes", "volumes"),
            ("vo\u00adlumes", "volumes"),
            ("vol\u00ad\numes", "volumes"),
            ("RÉ-\nSUMÉ", "résumé"),
            ("STRA-\nSSE", "strasse"),
            ("Straße", "strasse"),
            ("α-\nβ", "αβ"),
            ("कि-\nत", "कित"),
            ("a-\nb-\nc", "abc"),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(_layout_text(raw), expected)

    def test_preserves_lexical_numeric_and_paragraph_boundaries(self):
        for raw in [
            "well-known",
            "well‐known",
            "well‑known",
            "10-\n20",
            "x-\n2",
            "2-\nx",
            "−90",
            "-\n90",
            "a−\nb",
            "a-\n\nb",
            "a-\n \nb",
            "a-\fb",
            "a-\u2029b",
            "a-\u2028b",
            "_\u00ada",
            "1\u00ada",
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(_layout_text(raw), _norm(raw))

    def test_wrapped_lexical_hyphen_is_explicitly_heuristic(self):
        self.assertEqual(_layout_text("re-\nsign"), "resign")


@requires_pinned_root_reads
class LayoutAnswerTests(unittest.TestCase):
    def answer(self, root, terms, mode="layout_all_terms", **changes):
        corpus, reader = fixture(root, value="Préface vol-\numes and vo\u00adlumes")
        r = request(corpus, terms=terms, match=mode, **changes)
        return corpus, answer_evidence(
            request=r, corpus=corpus, reader=reader, budget=ReadBudget()
        )

    def test_opt_in_and_raw_citation_invariants(self):
        for mode, outcome in [
            ("all_terms", "no_evidence"),
            ("exact_phrase", "no_evidence"),
            ("layout_all_terms", "supported"),
        ]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                corpus, answer = self.answer(root, ["VOLUMES"], mode)
                self.assertEqual(answer["outcome"], outcome)
                self.assertEqual(
                    answer["candidate_response_sha256"],
                    seal(answer, "candidate_response_sha256"),
                )
                if mode != "layout_all_terms":
                    self.assertEqual(len(answer["explanation"]["limitations"]), 1)
                    continue
                self.assertIn(
                    "not exact source-byte", answer["explanation"]["limitations"][1]
                )
                self.assertEqual(len(answer["excerpts"]), 1)
                excerpt = answer["excerpts"][0]
                span = corpus["spans"][1]
                raw = (root / span["source_ref"]).read_bytes()
                selected = raw[span["byte_start"] : span["byte_end"]]
                self.assertGreater(span["byte_start"], 0)
                self.assertEqual(excerpt["text_utf8"].encode(), selected)
                self.assertEqual(excerpt["text_sha256"], digest(selected))
                self.assertEqual(excerpt["extraction_sha256"], digest(raw))
                for key in (
                    "source_sha256",
                    "byte_start",
                    "byte_end",
                    "page",
                    "attachment_key",
                ):
                    self.assertEqual(excerpt[key], span[key])
                self.assertEqual(
                    answer["qualified_claims"][0]["text_utf8"].encode(), selected
                )

    def test_all_terms_and_word_boundaries(self):
        for terms, outcome in [
            (["volumes", "PRÉFACE"], "supported"),
            (["volumes", "absent"], "no_evidence"),
            (["volume"], "no_evidence"),
        ]:
            with self.subTest(terms=terms), tempfile.TemporaryDirectory() as tmp:
                _, answer = self.answer(Path(tmp), terms)
                self.assertEqual(answer["outcome"], outcome)

    def test_limits_still_fail_closed(self):
        for limits in [
            {"max_hits": 1, "max_excerpt_bytes": 24576, "max_response_bytes": 65536},
            {"max_hits": 8, "max_excerpt_bytes": 1, "max_response_bytes": 65536},
        ]:
            with self.subTest(limits=limits), tempfile.TemporaryDirectory() as tmp:
                _, answer = self.answer(Path(tmp), ["optics"], limits=limits)
                self.assertEqual(answer["outcome"], "incomplete")
                self.assertEqual(answer["excerpts"], [])
                self.assertEqual(answer["qualified_claims"], [])

    def test_unknown_mode_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, answer = self.answer(Path(tmp), ["volumes"], "layout_exact_phrase")
            self.assertEqual(answer["outcome"], "unsupported_request")
