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

The offline fixture-first status shape is
`millefeuille-retrieval-index-status/v0.1`.

## Optional Lanes

ConDB may be used for hierarchy or tree experiments. A ConDB run must emit a
verdict artifact explaining whether it improved repeated retrieval,
classification readiness, or inspection compared with OpenKB/PageIndex and
direct file search.

ChatIndex may be used for bounded workflow recall, classification discussion
history, or decision-history recall. It is not the default paper index unless a
later Speculoos task promotes it.

## Retrieval Command

The read-only `retrieve` command queries a verified paper package by:

- paper id, source-pack slug, Zotero key, normalized DOI, or normalized exact
  title;
- page or exact section locator;
- summary scope;
- classification evidence need;
- index lane.

It requires a run id, validates the source-pack manifest, stage manifest,
artifact index, paper card, summary bundle, and index status before returning
anything, and fails closed when a DOI/title lookup is missing or ambiguous.
Section/page filters operate on structured summary source locators. The command
returns refs into the artifact package rather than pasting full private paper
content.

Explicit multi-run or batch retrieval manifests remain future work.

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
