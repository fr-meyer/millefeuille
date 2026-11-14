"""Public-domain original PDF drawings; golden facts authored before decoding.

Coordinates are PDF user space, origin bottom left. These are review oracles,
not an assertion that a text-only decoder understands geometry or mathematics.
"""

GOLDENS = {
    "multi-column": {
        "page": 1,
        "bbox": (40, 640, 540, 730),
        "facts": (
            "left column: 11 then 33",
            "right column: 22 then 44",
        ),
        "locator": "content object 5; left x40, right x340",
        "required": b"340 720 Tm (Right 22)",
    },
    "table": {
        "page": 1,
        "bbox": (40, 600, 360, 720),
        "facts": (
            "dry latency = 17.25 ms",
            "wet latency = 42.50 ms",
        ),
        "locator": "object 5; row dry y660, wet y620; latency x220",
        "required": b"40 600 320 120 re S",
    },
    "figure": {
        "page": 1,
        "bbox": (60, 500, 300, 700),
        "facts": ("triangle vertices (80,520),(160,680),(280,520)",),
        "locator": "object 5; closed vector path, no textual vertex values",
        "required": b"80 520 m 160 680 l 280 520 l h S",
    },
    "rotation": {
        "page": 1,
        "bbox": (40, 100, 200, 300),
        "facts": (
            "page rotation 90 degrees",
            "text matrix rotates 90 degrees",
        ),
        "locator": "page object 4 /Rotate; content object 5 Tm",
        "required": b"0 1 -1 0 100 120 Tm",
    },
    "equation": {
        "page": 1,
        "bbox": (80, 610, 260, 690),
        "facts": ("x squared plus alpha equals 9",),
        "locator": "object 5; superscript at (94,678), Symbol alpha at (140,660)",
        "required": b"/F2 18 Tf 1 0 0 1 140 660 Tm (a) Tj",
    },
}

STREAMS = {
    "multi-column": (
        b"BT /F1 12 Tf 1 0 0 1 340 720 Tm (Right 22) Tj 1 0 0 1 40 720 Tm "
        b"(Left 11) Tj 1 0 0 1 340 650 Tm (Right 44) Tj 1 0 0 1 40 650 Tm "
        b"(Left 33) Tj ET"
    ),
    "table": (
        b"q 1 w 40 600 320 120 re S 40 680 m 360 680 l S 40 640 m 360 640 l "
        b"S 200 600 m 200 720 l S Q BT /F1 12 Tf 1 0 0 1 50 700 Tm "
        b"(condition) Tj 1 0 0 1 220 700 Tm (latency ms) Tj 1 0 0 1 50 660 "
        b"Tm (dry) Tj 1 0 0 1 220 660 Tm (17.25) Tj 1 0 0 1 50 620 Tm (wet) "
        b"Tj 1 0 0 1 220 620 Tm (42.50) Tj ET"
    ),
    "figure": (
        b"q 2 w 60 500 m 300 500 l S 60 500 m 60 700 l S 80 520 m 160 680 l "
        b"280 520 l h S Q BT /F1 12 Tf 1 0 0 1 60 470 Tm (Figure 1: "
        b"triangular response) Tj ET"
    ),
    "rotation": (b"BT /F1 16 Tf 0 1 -1 0 100 120 Tm (Orientation marker 90) Tj ET"),
    "equation": (
        b"BT /F1 18 Tf 1 0 0 1 80 660 Tm (x) Tj /F1 10 Tf 1 0 0 1 94 678 Tm "
        b"(2) Tj /F1 18 Tf 1 0 0 1 115 660 Tm (+) Tj /F2 18 Tf 1 0 0 1 140 "
        b"660 Tm (a) Tj /F1 18 Tf 1 0 0 1 170 660 Tm (= 9) Tj ET"
    ),
}


def layout_pdf(case):
    stream = STREAMS[case]
    rotation = b" /Rotate 90" if case == "rotation" else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            b" /Resources << /Font << /F1 3 0 R /F2 6 0 R >> >> /Contents 5 0 R"
            + rotation
            + b" >>"
        ),
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Symbol >>",
    ]
    raw = b"%PDF-1.4\n% Original Millefeuille synthetic drawing\n"
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(raw)
    raw += b"xref\n0 7\n0000000000 65535 f \n"
    raw += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    raw += f"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return raw
