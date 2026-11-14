# S04 prospective bounded semantic witnesses

The operator receipt remains kernel-isolated. pdf_semantics is a pure finite
interpreter of that receipt, with no PDF access, oracle input or acceptance
power. Existing structural comparisons and eight original assertions remain;
semantic_observation adds independently reviewable witnesses, not a changed
historical acceptance status. acceptance_decision remains null.

Supported: at most 256 operators per page, 16 font resources, stack depth 8,
128 characters per explicit-position string, finite numeric magnitudes <=1e6,
512 output witnesses. No recursive input traversal. Quadratic geometry work is
bounded by the operator cap. Only explicit Tm then Tj positioning, untransformed
stroke-only simple paths, upright or quarter-turn text, and quarter-turn page
rotation are interpreted. Unknown operators invalidate the whole page.

Glyph decoding supports printable ASCII in default Helvetica and only byte 0x61
in default Symbol, mapped to U+03B1 by Adobe Symbol Encoding (PDF standard-14
Symbol encoding vector). It is not an ASCII substitution. Explicit encodings,
ToUnicode, other fonts and unsupported Symbol glyphs remain unknown. The
boundary now retains Type1 subtype, descriptor/font-file absence, encoding,
ToUnicode and bounded original string bytes. PyPDF2 original_bytes is used only
with its parsed PDFDocEncoding autodetection flag or ByteStringObject; missing
provenance and UTF16 BOM are unknown. No str-to-byte guess is made. These are
standard14 mapping premises, not rendered-font qualification.

Columns use identical x origins and decreasing baseline order. Ruled tables
require one rectangle, one full-height divider, full-width horizontal rules,
one same-size run per cell and equal row baselines. The header's two tokens
supply quantity and unit, not a scientific units ontology. Paths preserve
vertices, closure, width and explicit absence of fill. Rotation witnesses retain
page clockwise and text counterclockwise y-up angles; display orientation is text minus page modulo360 (Rotate90/TmCCW90 equals0).

The mathematical grammar is five explicitly positioned runs: single variable,
raised integer, plus, single variable, equals integer. A smaller raised run must
be 40–70% of base size, raised 70–120% of base size and within one base-size
horizontal displacement; base runs share exact baseline and size. This is a
finite typographic interpretation, not algebra solving, general mathematics,
rendering or general PDF qualification. Subscripts and changed baselines/sizes
outside the relation yield no AST. Values are observed, never filled from an
oracle. Unsupported or ambiguous graphs return unknown or omit the unsupported
higher-level relation, never a scientific pass.

No historical archive, F05 seam, transport framing, native kernel policy,
source fixture, GOLDENS, original source PDFs or kernel policy or lifecycle is modified.
Fresh local tests are prospective observations only. Independent code and
scientific review and other-platform qualification remain outstanding.

Scientific-mode receipt collection now adds bounded Type1/descriptor/font-file and original-string-byte provenance after the existing kernel filter. This changes the native collector source, not the sandbox policy, syscall limits or lifecycle. Unverified font or string provenance suppresses glyph and dependent equation witnesses.
