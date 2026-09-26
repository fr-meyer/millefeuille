# Canonical GPT paper-card output plan

`plan_gpt_paper_card_outputs` revalidates a transient trusted card outcome
against the immutable published summaries, fresh source preparation, exact
request and validated result. It assembles five files in memory and performs
zero provider calls and zero writes:

- `cards/paper-card.json`: canonical `millefeuille-paper-card/v0.2` record;
- `cards/paper-card.md`: readable card;
- `cards/model-provenance.json`: actual model/authentication, usage and cost;
- `cards/request-plan.json`: the exact no-call request metadata;
- `cards/write-manifest.json`: payload hashes, bytes and source/run lineage.

All refs are under a new `analyses/millefeuille/<card-run>/` directory. The
card run must differ from the published summary run, and an existing or
symlink destination is rejected. The write manifest binds the source-pack
manifest bytes as well as the summary publication, card request, provenance
and execution receipt. The plan is deterministic for the same accepted input.

The source pack's verified canonical filename supplies the title and, when
present in its structured filename, the year. Missing authors, DOI and URLs
are omitted. A quality note explicitly records pending bibliographic metadata
reconciliation. No bibliographic information is inferred from model output.

The canonical record keeps OpenKB and PageIndex pending. It includes validated
classification clues but makes no taxonomy decision, acceptance claim or
Zotero lifecycle assertion. Evidence refs preserve lineage to the immutable
summary snapshots and hierarchical summary. The result contains private bytes
excluded from diagnostic representations.

This API is an artifact assembly plan. Exact write approval, trusted root
reservation and atomic publication must follow separately. Source-pack run
reconciliation, whole-paper acceptance, indexing, duplicate checks,
classification and Zotero writeback remain later stages.

Focused verification covers deterministic canonical schema-valid output,
the exact five-file census/hashes/bytes, unchanged source files, private
diagnostic representations, truthful downstream state, source identity drift,
unbound/tampered execution evidence and occupied destinations.
