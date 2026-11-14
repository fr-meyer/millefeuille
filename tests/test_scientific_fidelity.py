"""Future CI evidence from the actual production boundary, not local execution."""

import ast
import copy
from dataclasses import replace
import hashlib
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain.scientific_fidelity import (
    FactExpectation,
    compare_scientific_fact,
    observe_scientific_source,
    scientific_log_receipt,
)
from tests.fixtures.evidence_transport import frame_receipt, runtime_identity
from tests.fixtures.remaining_source_fixture import GOLDENS, STREAMS, layout_pdf
from tests.platform_capabilities import requires_linux_native_isolation

S04_SOURCE_PATHS = (
    "millefeuille/domain/native_decode_boundary.py",
    "millefeuille/domain/scientific_fidelity.py",
    "tests/fixtures/remaining_source_fixture.py",
    "tests/test_scientific_fidelity.py",
    "tests/fixtures/evidence_transport.py",
    "scripts/validate_evidence_archive.py",
)


def scientific_source_binding():
    root = Path(__file__).resolve().parents[1]
    hashes = {}
    for name in S04_SOURCE_PATHS:
        with (root / name).open("rb") as stream:
            raw = stream.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("scientific-source-binding-budget")
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return hashes


def op(index, operator, operands):
    return (
        (("operations", index, "operator"), operator),
        (("operations", index, "operands"), operands),
    )


# Independent hand-authored locators/values. Never used by the extractor.
CHECKS = {
    "multi-column": (
        op(4, "Tm", [1, 0, 0, 1, 40, 720])
        + op(5, "Tj", ["Left 11"])
        + op(8, "Tm", [1, 0, 0, 1, 40, 650])
        + op(9, "Tj", ["Left 33"]),
        op(2, "Tm", [1, 0, 0, 1, 340, 720])
        + op(3, "Tj", ["Right 22"])
        + op(6, "Tm", [1, 0, 0, 1, 340, 650])
        + op(7, "Tj", ["Right 44"]),
    ),
    "table": (
        op(17, "Tm", [1, 0, 0, 1, 50, 660])
        + op(18, "Tj", ["dry"])
        + op(19, "Tm", [1, 0, 0, 1, 220, 660])
        + op(20, "Tj", ["17.25"])
        + op(15, "Tm", [1, 0, 0, 1, 220, 700])
        + op(16, "Tj", ["latency ms"]),
        op(21, "Tm", [1, 0, 0, 1, 50, 620])
        + op(22, "Tj", ["wet"])
        + op(23, "Tm", [1, 0, 0, 1, 220, 620])
        + op(24, "Tj", ["42.50"])
        + op(15, "Tm", [1, 0, 0, 1, 220, 700])
        + op(16, "Tj", ["latency ms"]),
    ),
    "figure": (
        op(8, "m", [80, 520])
        + op(9, "l", [160, 680])
        + op(10, "l", [280, 520])
        + op(11, "h", [])
        + op(12, "S", []),
    ),
    "rotation": (
        ((("rotation",), 90),),
        op(2, "Tm", [0, 1, -1, 0, 100, 120]),
    ),
    "equation": (
        op(2, "Tm", [1, 0, 0, 1, 80, 660])
        + op(3, "Tj", ["x"])
        + op(4, "Tf", ["/F1", 10])
        + op(5, "Tm", [1, 0, 0, 1, 94, 678])
        + op(6, "Tj", ["2"])
        + op(10, "Tf", ["/F2", 18])
        + op(11, "Tm", [1, 0, 0, 1, 140, 660])
        + op(12, "Tj", ["a"])
        + ((("fonts", "/F2", "base_font"), "/Symbol"),),
    ),
}


class ScientificFidelityTests(unittest.TestCase):
    def observe(self, raw, digest=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.pdf"
            path.write_bytes(raw)
            return observe_scientific_source(
                {
                    "path": path,
                    "sha256": digest or hashlib.sha256(raw).hexdigest(),
                }
            )

    def test_actual_boundary_eight_fact_receipts(self):
        for case, golden in GOLDENS.items():
            with self.subTest(case=case):
                receipt = self.observe(layout_pdf(case))
                if sys.platform != "linux" or platform.machine() != "x86_64":
                    self.assertEqual(receipt["outcome"], "refused")
                    self.assertFalse(receipt["limits_enforced"])
                    continue
                self.assertEqual(receipt["outcome"], "needs-review", receipt)
                self.assertTrue(receipt["limits_enforced"])
                self.assertTrue(receipt["child_reaped"])
                comparisons = []
                for index, fact in enumerate(golden["facts"]):
                    expected = FactExpectation(
                        f"S04.{case}.{index + 1}",
                        fact,
                        "tests/fixtures/remaining_source_fixture.py GOLDENS; "
                        + golden["locator"],
                        golden["page"],
                        golden["bbox"],
                        CHECKS[case][index],
                        source_pdf_sha256=hashlib.sha256(layout_pdf(case)).hexdigest(),
                        requires_semantics=case == "equation",
                    )
                    comparison = compare_scientific_fact(receipt, expected)
                    self.assertEqual(
                        comparison["status"],
                        (
                            "unresolved-semantic-evidence"
                            if case == "equation"
                            else "structural-match-needs-review"
                        ),
                        comparison,
                    )
                    self.assertIsNone(comparison["acceptance_decision"])
                    comparisons.append(comparison)
                    drift = replace(expected, checks=((("rotation",), 777),))
                    self.assertEqual(
                        compare_scientific_fact(receipt, drift)["status"], "mismatch"
                    )
                payload = scientific_log_receipt(
                    receipt,
                    comparisons,
                    context=runtime_identity(),
                    case_id=case,
                    source_sha256=scientific_source_binding(),
                )
                for frame in frame_receipt("S04", payload):
                    print(frame, flush=True)

    def test_malformed_and_hash_drift_refuse_without_observations(self):
        for raw, digest in ((b"%PDF-broken", None), (layout_pdf("figure"), "0" * 64)):
            receipt = self.observe(raw, digest)
            self.assertEqual(receipt["outcome"], "refused")
            self.assertNotIn("scientific", receipt)

    def test_windows_failclosed_before_input(self):
        with patch("millefeuille.domain.native_decode_boundary.sys.platform", "win32"):
            receipt = observe_scientific_source({"path": "absent", "sha256": "0" * 64})
        self.assertEqual(receipt["outcome"], "refused")
        self.assertEqual(receipt["charged_read_bytes"], 0)
        self.assertNotIn("scientific", receipt)

    def test_missing_evidence_is_not_a_match(self):
        expected = FactExpectation(
            "missing",
            "fact",
            "independent",
            1,
            (0, 0, 1, 1),
            ((("rotation",), 0),),
            source_pdf_sha256="0" * 64,
        )
        self.assertEqual(
            compare_scientific_fact({}, expected)["status"], "insufficient-evidence"
        )
        self.assertEqual(
            compare_scientific_fact({}, replace(expected, checks=()))["status"],
            "insufficient-evidence",
        )
        with self.assertRaisesRegex(ValueError, "log-budget"):
            scientific_log_receipt(
                {"oversized": "x" * 65537},
                [],
                context={},
                case_id="synthetic",
                source_sha256={},
            )

    def test_source_and_coordinate_bindings_fail_closed(self):
        expected = FactExpectation(
            "bound",
            "fact",
            "independent",
            1,
            (0, 0, 1, 1),
            (),
            source_pdf_sha256="a" * 64,
        )
        with self.assertRaisesRegex(ValueError, "source-binding"):
            compare_scientific_fact({"source_sha256": "b" * 64}, expected)
        with self.assertRaisesRegex(ValueError, "coordinate-system"):
            compare_scientific_fact(
                {
                    "source_sha256": "a" * 64,
                    "scientific": {"coordinate_system": "top-left"},
                },
                expected,
            )

    def test_actual_embedded_numeric_helper_rejects_nonfinite_and_overflow(self):
        # Execute ONLY the pure scalar conversion function, never decoder/setup/guard.
        import math

        from PyPDF2.generic import FloatObject, NumberObject

        from millefeuille.domain.native_decode_boundary import _CHILD

        tree = ast.parse(_CHILD)
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_scientific_number"
        )
        module = ast.Module(body=[copy.deepcopy(node)], type_ignores=[])
        namespace = {"math": math}
        exec(
            compile(
                ast.fix_missing_locations(module), "<pure-scientific-number>", "exec"
            ),
            namespace,
        )
        convert = namespace["_scientific_number"]
        self.assertEqual(convert(FloatObject("80.125")), 80.125)
        self.assertEqual(convert(NumberObject(7)), 7.0)
        for value in (
            FloatObject("NaN"),
            FloatObject("Infinity"),
            FloatObject("1e999"),
            FloatObject("1000000000000.5"),
        ):
            with (
                self.subTest(value=str(value)),
                self.assertRaisesRegex(ValueError, "number-out-of-bounds"),
            ):
                convert(value)

    @requires_linux_native_isolation
    def test_fractional_pdf_geometry_is_observed_not_null(self):
        modified = b"0.125 w " + STREAMS["figure"].replace(
            b"80 520 m", b"80.125 520.5 m"
        ).replace(b"60 470 Tm", b"60.25 470.75 Tm")
        with patch.dict(STREAMS, {"figure": modified}):
            raw = layout_pdf("figure")
        receipt = self.observe(raw)
        self.assertEqual(receipt["outcome"], "needs-review", receipt)
        operations = receipt["scientific"]["pages"][0]["operations"]
        self.assertTrue(
            any(
                x["operator"] == "m" and x["operands"] == [80.125, 520.5]
                for x in operations
            )
        )
        self.assertTrue(
            any(
                x["operator"] == "Tm" and x["operands"][-2:] == [60.25, 470.75]
                for x in operations
            )
        )
        self.assertTrue(
            any(x["operator"] == "w" and x["operands"] == [0.125] for x in operations)
        )

    @requires_linux_native_isolation
    def test_scientific_operation_and_numeric_limits_refuse(self):
        for stream, reason in (
            (b"q Q " * 1025, "scientific-operation-budget-exhausted"),
            (b"1000000000000.5 520 m", "scientific-number-out-of-bounds"),
        ):
            with self.subTest(reason=reason):
                with patch.dict(STREAMS, {"figure": stream}):
                    raw = layout_pdf("figure")
                receipt = self.observe(raw)
                self.assertEqual(receipt["outcome"], "refused", receipt)
                self.assertEqual(receipt["reason"], reason)
                self.assertNotIn("scientific", receipt)

    @requires_linux_native_isolation
    def test_scientific_output_limit_refuses(self):
        stream = b"1 0 0 1 999999999999.5 999999999999.5 cm " * 2048
        with patch.dict(STREAMS, {"figure": stream}):
            raw = layout_pdf("figure")
        receipt = self.observe(raw)
        self.assertEqual(receipt["outcome"], "refused", receipt)
        self.assertEqual(receipt["reason"], "output-budget-exhausted")
        self.assertNotIn("scientific", receipt)

    @requires_linux_native_isolation
    def test_default_text_and_scientific_mode_agree_without_default_disclosure(self):
        from millefeuille.domain.native_decode_boundary import inspect_bounded_pdf

        raw = layout_pdf("table")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.pdf"
            path.write_bytes(raw)
            manifest = {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}
            default = inspect_bounded_pdf(manifest)
            scientific = observe_scientific_source(manifest)
        self.assertEqual(default["outcome"], "needs-review", default)
        self.assertEqual(scientific["outcome"], "needs-review", scientific)
        for field in ("pages", "source_sha256", "tool"):
            self.assertEqual(default[field], scientific[field])
        self.assertNotIn("scientific", default)
        self.assertIn("scientific", scientific)

    def test_scientific_input_limit_has_no_observations(self):
        receipt = self.observe(b"%PDF-" + b"x" * 1048576)
        self.assertEqual(receipt["outcome"], "refused", receipt)
        self.assertNotIn("scientific", receipt)
        self.assertLessEqual(receipt["charged_read_bytes"], 1048577)
