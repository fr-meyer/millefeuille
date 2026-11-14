"""Finite operator-graph interpretation, not rendering or scientific acceptance.

No oracle inputs. Standard 14 Helvetica ASCII and Adobe Symbol Encoding's
0x61 alpha only (Adobe Symbol encoding vector). Unknown encodings fail closed.
Geometry is untransformed user space; unsupported state invalidates the page.
"""

import math
import re


class UnsupportedError(ValueError):
    """The bounded subset cannot establish meaning."""


def number(value):
    if type(value) not in (int, float) or abs(value) > 1e6 or not math.isfinite(value):
        raise UnsupportedError("numeric-type-or-budget")
    return value


def observe_page(page):
    """Return typed witnesses or one explicit unknown, never partial success."""
    try:
        return _page(page)
    except (UnsupportedError, KeyError, TypeError, IndexError, AttributeError) as error:
        return {"status": "unknown", "reason": str(error), "witnesses": []}


def _page(page):
    if type(page) is not dict:
        raise UnsupportedError("page-type")
    ops, fonts = page["operations"], page["fonts"]
    if (
        type(ops) is not list
        or len(ops) > 256
        or type(fonts) is not dict
        or len(fonts) > 16
    ):
        raise UnsupportedError("graph-budget")
    rotation = number(page["rotation"])
    if rotation not in (0, 90, 180, 270):
        raise UnsupportedError("rotation")
    box = page["media_box"]
    if type(box) is not list or len(box) != 4:
        raise UnsupportedError("media-box")
    for value in box:
        number(value)
    text, paths, path, stack = [], [], [], []
    inside, font, size, matrix, fresh, closed = False, None, None, None, False, False
    width = 1
    arities = {
        "BT": 0,
        "ET": 0,
        "Tf": 2,
        "Tm": 6,
        "Tj": 1,
        "q": 0,
        "Q": 0,
        "w": 1,
        "m": 2,
        "l": 2,
        "h": 0,
        "re": 4,
        "S": 0,
    }
    for index, operation in enumerate(ops):
        if (
            type(operation) is not dict
            or type(operation.get("index")) is not int
            or operation.get("index") != index
        ):
            raise UnsupportedError("operation-order")
        operator, args = operation["operator"], operation["operands"]
        if type(operator) is not str or operator not in arities:
            raise UnsupportedError("unsupported-operator")
        if type(args) is not list or len(args) != arities[operator]:
            raise UnsupportedError("operand-arity")
        if operator not in ("Tf", "Tj"):
            for value in args:
                number(value)
        if operator == "BT":
            if inside or path:
                raise UnsupportedError("nested-text")
            inside, matrix, fresh = True, None, False
        elif operator == "ET":
            if not inside:
                raise UnsupportedError("text-state")
            inside = False
        elif operator == "Tf":
            if not inside or type(args[0]) is not str:
                raise UnsupportedError("font-state")
            font, size = args[0], number(args[1])
            if not 0 < size <= 100:
                raise UnsupportedError("font-size")
        elif operator == "Tm":
            if not inside or args[:4] not in (
                [1, 0, 0, 1],
                [0, 1, -1, 0],
                [-1, 0, 0, -1],
                [0, -1, 1, 0],
            ):
                raise UnsupportedError("text-transform")
            matrix, fresh = args, True
        elif operator == "Tj":
            if not inside or not fresh or font not in fonts or size is None:
                raise UnsupportedError("text-position")
            raw, provenance = args[0], fonts[font]
            if type(provenance) is not dict or set(provenance) != {
                "base_font",
                "encoding",
                "has_to_unicode",
                "subtype",
                "has_descriptor",
                "has_font_file",
            }:
                raise UnsupportedError("font-provenance")
            if type(raw) is not str or not 0 < len(raw) <= 128:
                raise UnsupportedError("string-budget")
            if (
                provenance.get("subtype") != "/Type1"
                or provenance.get("has_descriptor") is not False
                or provenance.get("has_font_file") is not False
                or provenance.get("encoding") != ""
                or provenance.get("has_to_unicode") is not False
            ):
                raise UnsupportedError("unknown-encoding")
            records = operation.get("operand_bytes")
            if type(records) is not list or len(records) != 1:
                raise UnsupportedError("missing-original-bytes")
            record = records[0]
            if (
                type(record) is not dict
                or record.get("source")
                not in ("pdfdocencoding-autodetected", "byte-string")
                or type(record.get("hex")) is not str
                or not re.fullmatch(r"(?:[0-9a-f]{2}){1,128}", record["hex"])
            ):
                raise UnsupportedError("unknown-original-bytes")
            original = bytes.fromhex(record["hex"])
            if (
                not all(32 <= b <= 126 for b in original)
                or original.decode("ascii") != raw
            ):
                raise UnsupportedError("unsupported-original-bytes")
            base = provenance.get("base_font")
            if base == "/Helvetica" and all(32 <= ord(c) <= 126 for c in raw):
                decoded = raw
            elif base == "/Symbol" and raw == "a":
                decoded = "α"
            else:
                raise UnsupportedError("unknown-glyph-or-font")
            if any(t["matrix"] == matrix for t in text):
                raise UnsupportedError("overlapping-text")
            text.append(
                {
                    "kind": "glyph-run",
                    "text": decoded,
                    "raw": raw,
                    "font": font,
                    "provenance": provenance.copy(),
                    "size": size,
                    "matrix": matrix.copy(),
                    "operation": index,
                    "text_rotation": _angle(matrix),
                    "composed_rotation": (_angle(matrix) - rotation) % 360,
                }
            )
            fresh = False
        elif operator == "q":
            if inside or path or len(stack) >= 8:
                raise UnsupportedError("graphics-stack")
            stack.append((width, font, size))
        elif operator == "Q":
            if inside or path or not stack:
                raise UnsupportedError("graphics-stack")
            width, font, size = stack.pop()
        elif operator == "w":
            width = number(args[0])
            if width <= 0:
                raise UnsupportedError("stroke-width")
        elif operator in ("m", "re"):
            if inside or path:
                raise UnsupportedError("compound-path")
            if operator == "m":
                path = [args.copy()]
            else:
                x, y, w, h = args
                if w <= 0 or h <= 0:
                    raise UnsupportedError("rectangle")
                path = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
                closed = True
        elif operator == "l":
            if not path or closed or len(path) >= 4:
                raise UnsupportedError("path-state")
            path.append(args.copy())
        elif operator == "h":
            if not path or closed:
                raise UnsupportedError("path-state")
            closed = True
        elif operator == "S":
            if len(path) < 2:
                raise UnsupportedError("path-state")
            paths.append(
                {
                    "kind": "stroked-path",
                    "vertices": path,
                    "closed": closed,
                    "fill": False,
                    "width": width,
                }
            )
            path, closed = [], False
    if inside or path or stack:
        raise UnsupportedError("unbalanced-state")
    if not box[0] < box[2] or not box[1] < box[3]:
        raise UnsupportedError("media-box-order")
    witnesses = [{"kind": "page-rotation", "degrees": rotation}, *text, *paths]
    upright = all(t["text_rotation"] == 0 for t in text)
    if upright and text:
        columns = {}
        for t in text:
            columns.setdefault(t["matrix"][4], []).append(t)
        for x, runs in sorted(columns.items()):
            ordered = sorted(runs, key=lambda t: -t["matrix"][5])
            if runs != ordered:
                raise UnsupportedError("reordered-column")
            if len(runs) > 1 and len({r["size"] for r in runs}) == 1:
                witnesses.append(
                    {"kind": "column", "x": x, "runs": [r["text"] for r in ordered]}
                )
        witnesses.extend(_equation(text))
        witnesses.extend(_table(text, paths))
    if len(witnesses) > 512:
        raise UnsupportedError("output-budget")
    return {"status": "observed-needs-review", "witnesses": witnesses}


def _equation(text):
    # Generic five-run grammar: variable, raised integer, +, variable, = number.
    # No semantic claim is made for different baselines, sizes or ordering.
    if len(text) != 5:
        return []
    a, power, plus, b, equal = text
    if not (
        re.fullmatch("[a-zA-Zα]", a["text"])
        and re.fullmatch("[0-9]", power["text"])
        and plus["text"] == "+"
        and re.fullmatch("[a-zA-Zα]", b["text"])
        and re.fullmatch(r"= [0-9]{1,4}", equal["text"])
    ):
        return []
    base = a["matrix"][5]
    size = a["size"]
    if not (
        all(t["size"] == size and t["matrix"][5] == base for t in (plus, b, equal))
        and 0.4 * size <= power["size"] <= 0.7 * size
        and 0.7 * size <= power["matrix"][5] - base <= 1.2 * size
        and all(text[i]["matrix"][4] < text[i + 1]["matrix"][4] for i in range(4))
        and 0 < power["matrix"][4] - a["matrix"][4] <= size
    ):
        return []
    return [
        {
            "kind": "mathematical-ast",
            "ast": [
                "equal",
                [
                    "add",
                    ["power", ["symbol", a["text"]], ["integer", int(power["text"])]],
                    ["symbol", b["text"]],
                ],
                ["integer", int(equal["text"][2:])],
            ],
            "relation": "raised-smaller-run",
            "baseline": base,
        }
    ]


def _table(text, paths):
    rectangles = [p for p in paths if p["closed"] and len(p["vertices"]) == 4]
    if len(rectangles) != 1:
        return []
    vertices = rectangles[0]["vertices"]
    left, bottom = vertices[0]
    right, top = vertices[2]
    if not (
        left < right
        and bottom < top
        and vertices == [[left, bottom], [right, bottom], [right, top], [left, top]]
    ):
        return []
    vertical = [
        p["vertices"][0][0]
        for p in paths
        if len(p["vertices"]) == 2
        and p["vertices"][0][0] == p["vertices"][1][0]
        and {p["vertices"][0][1], p["vertices"][1][1]} == {bottom, top}
    ]
    horizontal = sorted(
        [
            p["vertices"][0][1]
            for p in paths
            if len(p["vertices"]) == 2
            and p["vertices"][0][1] == p["vertices"][1][1]
            and {p["vertices"][0][0], p["vertices"][1][0]} == {left, right}
        ],
        reverse=True,
    )
    if len(vertical) != 1 or not left < vertical[0] < right or len(horizontal) < 1:
        return []
    bounds = [top, *horizontal, bottom]
    if (
        bounds != sorted(set(bounds), reverse=True)
        or len({t["size"] for t in text}) != 1
    ):
        return []
    cells = []
    for upper, lower in zip(bounds, bounds[1:], strict=False):
        row = []
        for x0, x1 in ((left, vertical[0]), (vertical[0], right)):
            runs = [
                t
                for t in text
                if x0 < t["matrix"][4] < x1 and lower < t["matrix"][5] < upper
            ]
            if len(runs) != 1:
                return []
            row.append(runs[0])
        if row[0]["matrix"][5] != row[1]["matrix"][5]:
            return []
        cells.append(row)
    if len(text) != 2 * len(cells):
        return []
    header = cells[0][1]["text"].split()
    if len(header) != 2:
        return []
    result = []
    for label, value in cells[1:]:
        if not re.fullmatch(r"[0-9]{1,6}([.][0-9]{1,6})?", value["text"]):
            return []
        result.append(
            {
                "kind": "table-association",
                "row": label["text"],
                "quantity": header[0],
                "unit": header[1],
                "value": value["text"],
                "baseline": label["matrix"][5],
            }
        )
    return result


def _angle(matrix):
    return {
        (1, 0, 0, 1): 0,
        (0, 1, -1, 0): 90,
        (-1, 0, 0, -1): 180,
        (0, -1, 1, 0): 270,
    }[tuple(matrix[:4])]
