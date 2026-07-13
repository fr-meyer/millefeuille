# Retrieval And Index Contract

Millefeuille should make processed papers retrievable by agents without making
private source material harder to govern.

## Default Lane

OpenKB/PageIndex is the default local retrieval lane for selected paper content.
It receives only artifacts that passed the extraction, route, structure, and
quality checks required by the run profile.

The index stage must record:

- index target and local path or service id;
- source-pack ref and source hash;
- selected full-text ref;
- summary and paper-card refs;
- chunking or page-indexing profile;
- duplicate-scan or collision results when available;
- skip reasons for disabled lanes.

## Optional Lanes

ConDB may be used for hierarchy or tree experiments. A ConDB run must emit a
verdict artifact explaining whether it improved repeated retrieval,
classification readiness, or inspection compared with OpenKB/PageIndex and
direct file search.

ChatIndex may be used for bounded workflow recall, classification discussion
history, or decision-history recall. It is not the default paper index unless a
later Speculoos task promotes it.

## Retrieval Command

The future `retrieve` command should query a paper package or batch by:

- paper id, slug, Zotero key, DOI, or title;
- page or section range;
- summary scope;
- classification evidence need;
- index lane.

It should return refs into the artifact package rather than pasting full private
paper content by default.

## Acceptance

The acceptance stage should be able to verify that required index updates exist
or were intentionally skipped. A paper can be classification-ready only when the
configured retrieval/index requirements are satisfied or explicitly waived in
the stage manifest.

## Manual Gates

OpenKB writes, PageIndex/ConDB/ChatIndex index writes, provider/model calls,
Zotero writeback, source-pack writes, GitHub publication, release tags, and
package publication require explicit approval. Read-only local inspection of
already-created artifacts is allowed in preview mode.
