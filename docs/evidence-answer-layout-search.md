# Evidence answer layout search

`match: "layout_all_terms"` is an opt-in v1 request extension. Existing
`all_terms` and `exact_phrase` retain their NFC/casefold, word-boundary search
semantics and response shape. Neither existing mode means exact byte equality.

The new mode searches an NFC/casefold source projection. It removes U+00AD
soft hyphens between Unicode letters, and ASCII hyphen or soft hyphen followed
by exactly one LF, CRLF or CR, with optional spaces/tabs, between letters.
Combining marks after the preceding letter are allowed. Terms still use only
NFC/casefold, not layout rewriting. All terms must match within one verified
span with existing word boundaries. No joining across spans.

Blank lines, page breaks and Unicode paragraph/line separators are not joined.
Inline lexical hyphens, Unicode dash/minus signs and numeric hyphenation stay
unchanged. A lexical hyphen at a line end is inherently ambiguous: re- followed
by a line break then sign may match resign. This is why the mode is explicit,
not a default correction. Every response for this mode includes this limitation
in the sealed explanation. No dictionary, query-specific rule, ranking or
scientific entailment is provided. Supported means matching text retrieval only.

Excerpts, claim text, source/extraction/text hashes, byte offsets and citations
remain the original verified bytes. The projection is never emitted as evidence.
Original-PDF binding remains separate.

Limits remain 8 hits, 24576 excerpt bytes, 65536 response bytes, and existing
source/read budgets. Too many hits or bytes returns incomplete without partial
excerpts. Refine attachment/page/section selectors in a new request if needed;
never reinterpret or overwrite earlier receipts.
