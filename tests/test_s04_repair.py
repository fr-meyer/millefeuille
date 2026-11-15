"""Pure finite adversarial graphs, no expected-fact input to extraction."""

import copy
import unittest

from millefeuille.domain.pdf_semantics import _table, observe_page
from tests.platform_capabilities import requires_linux_native_isolation


def page():
    return {
        "rotation": 0,
        "media_box": [0, 0, 612, 792],
        "fonts": {
            "/F1": {
                "base_font": "/Helvetica",
                "encoding": "",
                "has_to_unicode": False,
                "subtype": "/Type1",
                "has_descriptor": False,
                "has_font_file": False,
            },
            "/F2": {
                "base_font": "/Symbol",
                "encoding": "",
                "has_to_unicode": False,
                "subtype": "/Type1",
                "has_descriptor": False,
                "has_font_file": False,
            },
        },
        "operations": [],
    }


def add(p, op, args):
    p["operations"].append(
        {
            "index": len(p["operations"]),
            "operator": op,
            "operands": args,
            "operand_bytes": [
                {
                    "source": "pdfdocencoding-autodetected",
                    "hex": args[0].encode("ascii").hex(),
                }
            ]
            if op == "Tj"
            else [None] * len(args),
        }
    )


class RepairTests(unittest.TestCase):
    def text(self, p, font, size, x, raw):
        for op, args in [
            ("BT", []),
            ("Tf", [font, size]),
            ("Tm", [1, 0, 0, 1, x, 600]),
            ("Tj", [raw]),
            ("ET", []),
        ]:
            add(p, op, args)

    def test_nested_graphics_font_and_size(self):
        p = page()
        self.text(p, "/F1", 12, 10, "before")
        add(p, "q", [])
        self.text(p, "/F2", 18, 20, "a")
        add(p, "q", [])
        self.text(p, "/F1", 9, 30, "inner")
        add(p, "Q", [])
        for op, args in [
            ("BT", []),
            ("Tm", [1, 0, 0, 1, 40, 600]),
            ("Tj", ["a"]),
            ("ET", []),
            ("Q", []),
            ("BT", []),
            ("Tm", [1, 0, 0, 1, 50, 600]),
            ("Tj", ["a"]),
            ("ET", []),
        ]:
            add(p, op, args)
        result = observe_page(p)
        runs = [w for w in result["witnesses"] if w["kind"] == "glyph-run"]
        self.assertEqual(
            [(w["text"], w["size"]) for w in runs[-2:]], [("α", 18), ("a", 12)]
        )
        for op in ("q", "Q"):
            bad = copy.deepcopy(p)
            add(bad, op, [])
            self.assertEqual(observe_page(bad)["status"], "unknown")

    def test_provenance_refuses(self):
        p = page()
        self.text(p, "/F2", 12, 10, "a")
        for key, value in [
            ("subtype", "/Type3"),
            ("has_descriptor", True),
            ("has_font_file", True),
            ("encoding", "/WinAnsiEncoding"),
            ("has_to_unicode", True),
        ]:
            bad = copy.deepcopy(p)
            bad["fonts"]["/F2"][key] = value
            self.assertEqual(observe_page(bad)["status"], "unknown")
        for record in (
            None,
            {"source": "unknown", "hex": "61"},
            {"source": "utf16-autodetected", "hex": "feff0061"},
            {"source": "byte-string", "hex": "ff"},
        ):
            bad = copy.deepcopy(p)
            bad["operations"][3]["operand_bytes"] = [record]
            self.assertEqual(observe_page(bad)["status"], "unknown")

    def test_numeric_fail_closed(self):
        for value in (10**400, -(10**400), True, float("nan"), float("inf")):
            for target in ("rotation", "box", "operand", "size"):
                p = page()
                self.text(p, "/F1", 12, 10, "a")
                if target == "rotation":
                    p["rotation"] = value
                elif target == "box":
                    p["media_box"][0] = value
                elif target == "size":
                    p["operations"][1]["operands"][1] = value
                else:
                    p["operations"][2]["operands"][4] = value
                self.assertEqual(observe_page(p)["status"], "unknown")

    def test_nonrectangles_do_not_associate(self):
        for vertices in (
            [[40, 600], [100, 620], [360, 720], [300, 710]],
            [[40, 600], [360, 720], [360, 600], [40, 720]],
            [[40, 600], [40, 720], [360, 720], [360, 600]],
            [[40, 600], [40, 600], [360, 720], [40, 720]],
            [[40, 600], [40, 600], [40, 720], [40, 720]],
        ):
            self.assertEqual(_table([], [{"closed": True, "vertices": vertices}]), [])

    def test_table_association_novel_rectangles_and_skew(self):
        for left, bottom, right, top in ((10, 50, 210, 170), (40, 600, 360, 720)):
            with self.subTest(rectangle=(left, bottom, right, top)):
                middle = (left + right) / 2
                h = (top - bottom) / 3
                paths = [
                    {
                        "closed": True,
                        "vertices": [
                            [left, bottom],
                            [right, bottom],
                            [right, top],
                            [left, top],
                        ],
                    },
                    {"closed": False, "vertices": [[middle, bottom], [middle, top]]},
                    {
                        "closed": False,
                        "vertices": [[left, bottom + h], [right, bottom + h]],
                    },
                    {
                        "closed": False,
                        "vertices": [[left, bottom + 2 * h], [right, bottom + 2 * h]],
                    },
                ]
                runs = []
                for row, pair in enumerate(
                    (("state", "mass kg"), ("low", "3.50"), ("high", "6.25"))
                ):
                    for x, value in zip((left + 10, middle + 10), pair, strict=True):
                        runs.append(
                            {
                                "text": value,
                                "size": 12,
                                "matrix": [1, 0, 0, 1, x, top - (row + 0.5) * h],
                            }
                        )
                found = _table(runs, paths)
                self.assertEqual(
                    [(w["row"], w["quantity"], w["unit"], w["value"]) for w in found],
                    [("low", "mass", "kg", "3.50"), ("high", "mass", "kg", "6.25")],
                )
                for vertices in (
                    [
                        [left, bottom],
                        [left + 10, bottom + 20],
                        [right, top],
                        [right - 10, top - 10],
                    ],
                    [[left, bottom], [right, top], [right, bottom], [left, top]],
                    [[left, bottom], [left, top], [right, top], [right, bottom]],
                    [[left, bottom], [left, bottom], [right, top], [left, top]],
                ):
                    bad = copy.deepcopy(paths)
                    bad[0]["vertices"] = vertices
                    self.assertEqual(_table(runs, bad), [])

    def test_page_rotation_convention(self):
        matrices = {
            0: [1, 0, 0, 1],
            90: [0, 1, -1, 0],
            180: [-1, 0, 0, -1],
            270: [0, -1, 1, 0],
        }
        for text_angle, matrix in matrices.items():
            for page_angle in (0, 90, 180, 270):
                with self.subTest(text=text_angle, page=page_angle):
                    p = page()
                    self.text(p, "/F1", 12, 10, "a")
                    p["rotation"] = page_angle
                    p["operations"][2]["operands"][:4] = matrix
                    run = next(
                        w
                        for w in observe_page(p)["witnesses"]
                        if w["kind"] == "glyph-run"
                    )
                    self.assertEqual(run["text_rotation"], text_angle)
                    self.assertEqual(
                        run["composed_rotation"], (text_angle - page_angle) % 360
                    )
        p["operations"][2]["operands"][:4] = [1, 1, 0, 1]
        self.assertEqual(observe_page(p)["status"], "unknown")


class NativeProvenanceTests(unittest.TestCase):
    @requires_linux_native_isolation
    def test_isolated_font_and_byte_collisions(self):
        import re

        from tests.fixtures.remaining_source_fixture import layout_pdf
        from tests.test_scientific_fidelity import ScientificFidelityTests

        original = layout_pdf("equation")

        def rebuild(raw):
            objects = re.findall(rb"[1-6] 0 obj\n(.*?)\nendobj", raw, re.S)
            self.assertEqual(len(objects), 6)
            result = b"%PDF-1.4\n"
            offsets = []
            for i, obj in enumerate(objects, 1):
                if i == 5:
                    stream = obj.split(b"stream\n", 1)[1].rsplit(b"\nendstream", 1)[0]
                    obj = (
                        b"<< /Length "
                        + str(len(stream)).encode()
                        + b" >>\nstream\n"
                        + stream
                        + b"\nendstream"
                    )
                offsets.append(len(result))
                result += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
            start = len(result)
            result += b"xref\n0 7\n0000000000 65535 f \n"
            result += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
            return (
                result
                + (
                    f"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n"
                ).encode()
            )

        for name, raw in (
            (
                "subtype",
                original.replace(
                    b"/Type1 /BaseFont /Symbol", b"/TrueType /BaseFont /Symbol"
                ),
            ),
            (
                "descriptor",
                original.replace(
                    b"/BaseFont /Symbol",
                    b"/BaseFont /Symbol /FontDescriptor << /FontFile 3 0 R >>",
                ),
            ),
            ("unicode-bom", original.replace(b"(a) Tj", b"<feff0061> Tj")),
        ):
            with self.subTest(name=name):
                receipt = ScientificFidelityTests.observe(self, rebuild(raw))
                self.assertEqual(receipt["outcome"], "needs-review", receipt)
                self.assertEqual(receipt["semantics"]["pages"][0]["status"], "unknown")
                self.assertEqual(receipt["semantics"]["pages"][0]["witnesses"], [])
