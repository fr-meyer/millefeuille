"""Synthetic installed-API route; expectations never enter the producer."""

import copy
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.evidence_answer import answer_evidence, canonical, seal
from millefeuille.domain.evidence_source_adapter import (
    ReadBudget,
    ScopedReadOnlyReader,
    saved_identity,
)
from millefeuille.domain.semantic_acceptance import digest
from millefeuille.domain.semantic_scenario_evaluator import evaluate_frozen_scenarios
from tests.platform_capabilities import requires_pinned_root_reads


def fixture(root, value="12"):
    texts = [
        ("main", "method", "prose", "Method uses calibrated optics."),
        ("main", "results", "table", value + " ms under dry conditions"),
        ("supp", "protocol", "procedure", "Optics procedure: rinse then dry."),
        ("supp", "conditions", "prose", "Calibrated optics require dry samples."),
    ]
    spans, attachments, files = [], [], {}
    for i, (key, section, kind, text) in enumerate(texts):
        ref = str(i) + ".txt"
        (root / ref).write_text(text)
        raw = text.encode()
        h = digest(raw)
        files[ref] = {"identity": saved_identity(root / ref), "sha256": h}
        spans.append(
            {
                "span_id": str(i),
                "attachment_key": key,
                "page": 1 if i != 1 else 2,
                "section_id": section,
                "kind": kind,
                "source_ref": ref,
                "source_sha256": digest(key.encode()),
                "byte_start": 0,
                "byte_end": len(raw),
                "text_sha256": h,
                "table_coordinates": {
                    "table_id": "T1",
                    "row_label": "dry",
                    "column_label": "latency",
                    "unit_literal": "ms",
                }
                if kind == "table"
                else None,
            }
        )
        attachments.append(
            {
                "key": key,
                "source_sha256": digest(key.encode()),
                "extraction_sha256": h,
                "pages": [1, 2],
            }
        )
    # Each attachment extraction is a single file with deterministic span boundaries.
    for key in ("main", "supp"):
        entries = [s for s in spans if s["attachment_key"] == key]
        raw = b"\n".join((root / s["source_ref"]).read_bytes() for s in entries)
        ref = key + ".txt"
        (root / ref).write_bytes(raw)
        files[ref] = {"identity": saved_identity(root / ref), "sha256": digest(raw)}
        offset = 0
        for s in entries:
            length = s["byte_end"]
            s.update(source_ref=ref, byte_start=offset, byte_end=offset + length)
            offset += length + 1
    attachments = [
        {
            "key": k,
            "source_sha256": digest(k.encode()),
            "extraction_sha256": files[k + ".txt"]["sha256"],
            "pages": [1, 2],
        }
        for k in ("main", "supp")
    ]
    corpus = {
        "schema_version": "millefeuille-evidence-corpus/v1",
        "paper_id": "paper",
        "run_id": "run",
        "attachments": attachments,
        "spans": spans,
        "completeness": {
            "authorized_scope_sha256": digest(b"scope"),
            "expected_spans": 4,
            "expected_source_bytes": sum(
                s["byte_end"] - s["byte_start"] for s in spans
            ),
            "all_selected_attachments_inventory_complete": True,
        },
    }
    package_root = root / "reviewed"
    package_root.mkdir()
    package_files = {}

    def put(ref, payload):
        if not isinstance(payload, bytes):
            payload = canonical(payload)
        (package_root / ref).write_bytes(payload)
        package_files[ref] = digest(payload)

    sections, units, package_attachments = [], [], []
    for key in ("main", "supp"):
        put(key + ".txt", key.encode())
        package_attachments.append(
            {"attachment_key": key, "source_ref": key + ".txt", "local_page_count": 2}
        )
    for span in spans:
        sections.append(
            {
                "section_id": span["section_id"],
                "attachment_key": span["attachment_key"],
                "heading": span["section_id"],
                "local_pages": [span["page"]],
            }
        )
        ref = span["span_id"] + "-summary.txt"
        put(ref, b"Reviewed synthetic summary")
        units.append(
            {
                "section_id": span["section_id"],
                "attachment_key": span["attachment_key"],
                "summary_ref": ref,
            }
        )
    put("structure.json", {"sections": sections})
    put(
        "provenance.json",
        {
            "kind": "current-assistant-review",
            "reviewer": "synthetic",
            "review_id": "review1",
            "original_generated_sha256": digest(b"generated"),
        },
    )
    manifest = {
        "schema": "millefeuille-reviewed-sections/v1",
        "paper_id": corpus["paper_id"],
        "run_id": corpus["run_id"],
        "files": package_files,
        "attachments": package_attachments,
        "units": units,
    }
    raw = canonical(manifest)
    (package_root / "manifest.json").write_bytes(raw)
    package = {"package_root": package_root, "manifest_sha256": digest(raw)}
    corpus["manifest_sha256"] = seal(corpus, "manifest_sha256")
    ancestors = {str(p): saved_identity(p) for f in files for p in (root / f).parents}
    return corpus, ScopedReadOnlyReader(
        root=root, files=files, ancestors=ancestors, reviewed_package=package
    )


def request(corpus, **changes):
    r = {
        "schema_version": "millefeuille-evidence-question/v1",
        "paper_id": "paper",
        "run_id": "run",
        "corpus_sha256": corpus["manifest_sha256"],
        "selection": {"attachment_keys": None, "local_page": None, "section_ids": None},
        "intent": "explain",
        "terms": ["optics"],
        "match": "all_terms",
        "evidence_kind": "any",
        "table_selector": None,
        "relation": "none",
        "limits": {
            "max_hits": 8,
            "max_excerpt_bytes": 24576,
            "max_response_bytes": 65536,
        },
    }
    r.update(changes)
    return r


def eight(corpus):
    def sel(keys=None, page=None, sections=None):
        return {"attachment_keys": keys, "local_page": page, "section_ids": sections}

    return [
        request(corpus, selection=sel(["main"], sections=["method"])),
        request(
            corpus, intent="procedure", selection=sel(["supp"], sections=["protocol"])
        ),
        request(
            corpus,
            intent="numeric",
            terms=["ms"],
            evidence_kind="table",
            table_selector=corpus["spans"][1]["table_coordinates"],
        ),
        request(
            corpus,
            intent="compare",
            relation="side_by_side_only",
            selection=sel(["main", "supp"]),
        ),
        request(corpus, intent="page", terms=[], selection=sel(["main"], 1)),
        request(corpus, intent="page", terms=[], selection=sel(["supp"], 1)),
        request(corpus, intent="page", terms=[], selection=sel(page=1)),
        request(corpus, terms=["magnetism"]),
    ]


def generated_scenarios(tmp):
    corpus, reader = fixture(Path(tmp))
    requests = eight(corpus)
    # Independently source-reviewed synthetic expected locators frozen first.
    indices = [[0], [2], [1], [0, 2, 3], [0], [2, 3], [], []]
    gold = []
    source_spans = {}
    for s in corpus["spans"]:
        text = (
            (Path(tmp) / s["source_ref"])
            .read_bytes()[s["byte_start"] : s["byte_end"]]
            .decode()
        )
        source_spans[s["span_id"]] = {
            k: s[k]
            for k in (
                "span_id",
                "attachment_key",
                "page",
                "section_id",
                "source_sha256",
                "byte_start",
                "byte_end",
                "text_sha256",
            )
        }
        source_spans[s["span_id"]].update(
            text_utf8=text,
            extraction_sha256=next(
                a["extraction_sha256"]
                for a in corpus["attachments"]
                if a["key"] == s["attachment_key"]
            ),
        )
    for i, r in enumerate(requests):
        locators = [
            [
                corpus["spans"][j][k]
                for k in ("attachment_key", "page", "section_id", "text_sha256")
            ]
            for j in indices[i]
        ]
        g = {
            "id": str(i),
            "query_sha256": digest(canonical(r)),
            "expected_locators": locators,
            "expected_verdict": "supported"
            if i < 6
            else ("ambiguous" if i == 6 else "no_evidence"),
        }
        if i == 6:
            g["ambiguity"] = {
                "page": 1,
                "attachment_keys": ["main", "supp"],
                "required_selector": "attachment_keys",
            }
        if i == 7:
            g["independent_scientific_absence_reviewed"] = True
        gold.append(g)
    review = {
        "frozen_gold_sha256": digest(canonical(gold)),
        "corpus_sha256": corpus["manifest_sha256"],
        "source_spans": source_spans,
    }
    responses = [
        canonical(
            answer_evidence(
                request=r, corpus=corpus, reader=reader, budget=ReadBudget()
            )
        )
        for r in requests
    ]
    result = evaluate_frozen_scenarios(
        independent_gold=gold,
        candidate_responses=responses,
        candidate_response_sha256s=[digest(r) for r in responses],
        source_review=review,
    )
    return gold, responses, review, result


@requires_pinned_root_reads
class EvidenceTests(unittest.TestCase):
    def test_eight_actual_producer_adapter_evaluator(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, result = generated_scenarios(tmp)
            self.assertTrue(all(s["passed"] for s in result["scenarios"]), result)

    def test_budget_ambiguity_grammar_stale_and_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, reader = fixture(Path(tmp))

            def answer(r, budget=None):
                return answer_evidence(
                    request=r,
                    corpus=corpus,
                    reader=reader,
                    budget=budget or ReadBudget(),
                )

            r = eight(corpus)[6]
            b = ReadBudget()
            self.assertEqual(answer(r, b)["outcome"], "ambiguous")
            self.assertEqual(b.opens, 0)
            self.assertEqual(
                answer(request(corpus, case_id="gold"))["outcome"],
                "unsupported_request",
            )
            self.assertEqual(
                answer(
                    request(corpus, terms=["missing"]), ReadBudget(max_read_bytes=1)
                )["outcome"],
                "incomplete",
            )
            partial = copy.deepcopy(corpus)
            partial["spans"].pop()
            partial["manifest_sha256"] = seal(partial, "manifest_sha256")
            self.assertEqual(
                answer_evidence(
                    request=request(partial, terms=["missing"]),
                    corpus=partial,
                    reader=reader,
                    budget=ReadBudget(),
                )["outcome"],
                "incomplete",
            )
            (Path(tmp) / "main.txt").write_text("changed")
            self.assertEqual(answer(eight(corpus)[0])["outcome"], "integrity_refused")

    def test_source_change_changes_actual_answer(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            c1, r1 = fixture(Path(a), "12")
            c2, r2 = fixture(Path(b), "25")
            x = answer_evidence(
                request=eight(c1)[2], corpus=c1, reader=r1, budget=ReadBudget()
            )
            y = answer_evidence(
                request=eight(c2)[2], corpus=c2, reader=r2, budget=ReadBudget()
            )
            self.assertEqual(x["outcome"], "supported")
            self.assertNotEqual(
                x["candidate_response_sha256"], y["candidate_response_sha256"]
            )
            self.assertIn("25 ms", y["qualified_claims"][0]["text_utf8"])


class RepairRegressionTests(unittest.TestCase):
    def test_all_outcome_caps_and_strict_package_migration(self):
        from millefeuille.domain.evidence_answer import ResponseLimitError

        with tempfile.TemporaryDirectory() as tmp:
            corpus, reader = fixture(Path(tmp))
            queries = eight(corpus) + [request(corpus, case_id="bad")]
            for query in queries:
                query["limits"]["max_response_bytes"] = 256
                with self.assertRaises(ResponseLimitError):
                    answer_evidence(
                        request=query, corpus=corpus, reader=reader, budget=ReadBudget()
                    )
            package_manifest = (
                Path(reader.reviewed_package["package_root"]) / "manifest.json"
            )
            original = package_manifest.read_bytes()
            package_manifest.write_bytes(original + b" ")
            result = answer_evidence(
                request=eight(corpus)[0],
                corpus=corpus,
                reader=reader,
                budget=ReadBudget(),
            )
            self.assertEqual(result["outcome"], "integrity_refused")
            package_manifest.write_bytes(original)
            reader.reviewed_package = None
            result = answer_evidence(
                request=eight(corpus)[0],
                corpus=corpus,
                reader=reader,
                budget=ReadBudget(),
            )
            self.assertEqual(result["outcome"], "integrity_refused")

    def test_repeated_secure_reads_predebit_and_no_io_after_exhaustion(self):
        from unittest.mock import patch

        from millefeuille.domain.evidence_source_adapter import BudgetExhaustedError
        from millefeuille.domain.semantic_acceptance import (
            budgeted_read,
            semantic_read_scope,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample"
            path.write_bytes(b"abcd")
            budget = ReadBudget(max_read_bytes=12)
            with semantic_read_scope(budget):
                self.assertEqual(budgeted_read(path, "sample", max_bytes=4), b"abcd")
                self.assertEqual(budgeted_read(path, "sample", max_bytes=4), b"abcd")
                with patch(
                    "millefeuille.domain.semantic_acceptance._secure_read"
                ) as read:
                    with self.assertRaises(BudgetExhaustedError):
                        budgeted_read(path, "sample", max_bytes=4)
                    read.assert_not_called()
            self.assertEqual(budget.read_bytes, 12)


class SerialBudgetRegressions(unittest.TestCase):
    def test_cross_thread_budget_refuses(self):
        from concurrent.futures import ThreadPoolExecutor

        from millefeuille.domain.evidence_source_adapter import (
            BudgetExhaustedError,
            ReadBudget,
        )

        budget = ReadBudget()
        with ThreadPoolExecutor(max_workers=1) as pool:  # noqa: SIM117
            with self.assertRaises(BudgetExhaustedError):  # noqa: SIM117
                pool.submit(budget.debit, read_bytes=1).result()
        self.assertEqual(budget.read_bytes, 0)

    def test_exhausted_budget_refuses_before_metadata(self):
        from unittest.mock import patch

        from millefeuille.domain.evidence_source_adapter import (
            BudgetExhaustedError,
            ReadBudget,
        )
        from millefeuille.domain.semantic_acceptance import (
            budgeted_read,
            semantic_read_scope,
        )

        budget = ReadBudget(max_read_bytes=0)
        with semantic_read_scope(budget), patch("pathlib.Path.lstat") as metadata:  # noqa: SIM117
            with self.assertRaises(BudgetExhaustedError):  # noqa: SIM117
                budgeted_read("/not-read", "fixture", max_bytes=1)
        metadata.assert_not_called()

    @requires_pinned_root_reads
    def test_directory_enumeration_and_revalidation_are_incrementally_bounded(self):
        import os
        import tempfile
        from unittest.mock import patch

        from millefeuille.domain.millefeuille import MillefeuilleContractError
        from millefeuille.domain.secure_io import RootArtifactReader

        with tempfile.TemporaryDirectory() as directory:  # noqa: SIM117
            for index in range(513):
                Path(directory, str(index)).touch()
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with patch("os.listdir", side_effect=AssertionError("unbounded list")):  # noqa: SIM117
                    with self.assertRaisesRegex(
                        MillefeuilleContractError, "512 entries"
                    ):  # noqa: SIM117
                        RootArtifactReader._directory_entries(
                            fd, path=Path(directory), label="fixture"
                        )
            finally:
                os.close(fd)


class CompleteBudgetBoundaryTests(unittest.TestCase):
    def test_exact_axes_and_sticky_failed_large_then_small(self):
        from millefeuille.domain.evidence_source_adapter import BudgetExhaustedError

        for axis in ["read_bytes", "opens", "scans"]:
            with self.subTest(axis=axis):
                b = ReadBudget(**{"max_" + axis: 2})
                b.debit(**{axis: 2})
                self.assertEqual(getattr(b, axis), 2)
                with self.assertRaises(BudgetExhaustedError):
                    b.debit(**{axis: 3})
                with self.assertRaises(BudgetExhaustedError):
                    b.debit()
                self.assertEqual(getattr(b, axis), 2)

    def test_invalid_bounds_and_deadline_do_not_touch_files(self):
        from unittest.mock import patch

        from millefeuille.domain.evidence_source_adapter import BudgetExhaustedError
        from millefeuille.domain.millefeuille import MillefeuilleContractError
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        with patch("os.open") as opened, patch("pathlib.Path.lstat") as metadata:
            for field in ["max_read_bytes", "max_opens", "max_scans"]:
                for v in [True, -1, 1.5, float("inf"), float("nan")]:
                    with self.subTest(field=field, v=v):  # noqa: SIM117 (explicit budget lifetime)
                        with self.assertRaises(MillefeuilleContractError):  # noqa: SIM117 (explicit budget lifetime)
                            with semantic_read_scope(ReadBudget(**{field: v})):
                                self.fail("invalid budget admitted")
            for v in [True, False, 0, -1, "90", float("inf"), float("nan")]:
                with self.assertRaises(MillefeuilleContractError):
                    ReadBudget(max_seconds=v).debit()
            b = ReadBudget(max_seconds=1)
            with patch(
                "millefeuille.domain.evidence_source_adapter.time.monotonic",
                return_value=b.started + 1,
            ):
                b.debit()
            with (
                patch(
                    "millefeuille.domain.evidence_source_adapter.time.monotonic",
                    return_value=b.started + 1.001,
                ),
                self.assertRaises(BudgetExhaustedError),
            ):
                b.debit()
            with self.assertRaises(BudgetExhaustedError):
                b.debit()
            opened.assert_not_called()
            metadata.assert_not_called()

    def test_nested_identity_and_exhaustion_cannot_replace_active_ledger(self):
        from millefeuille.domain.evidence_source_adapter import BudgetExhaustedError
        from millefeuille.domain.millefeuille import MillefeuilleContractError
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        b = ReadBudget(max_read_bytes=2)
        with semantic_read_scope(b):  # noqa: SIM117 (explicit budget lifetime)
            with semantic_read_scope() as nested:
                self.assertIs(nested, b)
                nested.debit(read_bytes=1)
            with self.assertRaisesRegex(MillefeuilleContractError, "replace"):  # noqa: SIM117 (explicit budget lifetime)
                with semantic_read_scope(ReadBudget()):
                    self.fail("replaced")
            with self.assertRaises(BudgetExhaustedError):
                b.debit(read_bytes=3)
            with self.assertRaises(BudgetExhaustedError):  # noqa: SIM117 (explicit budget lifetime)
                with semantic_read_scope():
                    self.fail("reset")
        self.assertEqual(b.read_bytes, 1)

    def test_real_decorated_apis_refuse_fresh_copied_and_explicit_worker_scopes(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextvars import copy_context
        from unittest.mock import patch

        from millefeuille.domain.millefeuille import MillefeuilleContractError
        from millefeuille.domain.reviewed_summary_handoff import (
            retrieve_reviewed_summary_refs,
        )
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        b = ReadBudget()
        args = {
            "package_root": "/never-read",
            "expected_paper_id": "paper",
            "expected_run_id": "run",
            "expected_manifest_sha256": "sha256:" + "a" * 64,
        }
        with semantic_read_scope(b):
            context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:

            def call(variant):
                if variant == "copied":
                    return context.run(retrieve_reviewed_summary_refs, **args)
                if variant == "foreign":
                    return retrieve_reviewed_summary_refs(**args, budget=b)
                if variant == "own":
                    return retrieve_reviewed_summary_refs(**args, budget=ReadBudget())
                return retrieve_reviewed_summary_refs(**args)

            with (
                patch("pathlib.Path.lstat") as metadata,
                patch("os.open") as opened,
                patch("os.scandir") as scan,
            ):
                for variant in ["fresh", "copied", "foreign", "own"]:
                    with self.subTest(variant=variant):  # noqa: SIM117 (explicit budget lifetime)
                        with self.assertRaisesRegex(
                            MillefeuilleContractError, "main-thread"
                        ):
                            pool.submit(call, variant).result()
                for spy in [metadata, opened, scan]:
                    spy.assert_not_called()
        self.assertEqual((b.read_bytes, b.opens, b.scans), (0, 0, 0))
