# Assistant-reviewed section handoff (local, read-only)

Use `millefeuille.domain.reviewed_summary_handoff.retrieve_reviewed_summary_refs` explicitly. The historical `retrieve_artifact_refs` and GPT publication verification paths are unchanged. Reviewed text must never be substituted into an immutable GPT publication or described as a newly generated GPT unit.

Required keyword inputs: `package_root`, `expected_paper_id`, `expected_run_id`, `expected_manifest_sha256` (`sha256:` plus 64 lowercase hex digits). The caller obtains these independently; computing a digest does not grant approval, authenticate a model/reviewer, or establish scientific correctness. The package is a separate reviewed run identity, not a historical publication identity. Refs are relative to `package_root`; returned hashes describe verified bytes at call time, not future reads.

## Exact flat package schema

All objects reject extra/missing keys; JSON duplicate keys are refused. IDs use 1–96 ASCII alphanumeric/underscore/hyphen characters, starting alphanumeric. File names use 1–128 ASCII alphanumeric/underscore/hyphen/dot characters, starting alphanumeric; directories and traversal are unsupported.

- `manifest.json`: exactly `schema` (`millefeuille-reviewed-sections/v1`), `paper_id`, `run_id`, `files`, `attachments`, `units`.
- `files`: map every other file name to its exact `sha256:` digest. No unused files allowed.
- `attachments`: 1–64 objects with `attachment_key`, `source_ref`, `local_page_count` (1–100000). Each has a distinct source snapshot file. Source bytes are opaque: no PDF/page parser or semantic validation is claimed.
- `structure.json`: exactly `sections`, 1–256 objects each with `section_id`, `attachment_key`, `heading` (nonblank trimmed text, max 512 characters), `local_pages` (1–1000 distinct positive integers within the attachment count). Section identity is the attachment/section pair.
- `units`: 1–256 objects with `section_id`, `attachment_key`, `summary_ref`. Exactly one real, nonempty UTF-8 summary file per declared scientific section, no shared file refs. The manifest binds source snapshots, structure, provenance and each individual summary; a JSON container is not a substitute for separate unit files.
- `provenance.json`: exactly `kind` (`current-assistant-review`), `reviewer`, `review_id` (IDs), `original_generated_sha256` (historical artifact fingerprint assertion only). This does not verify the historical artifact or carry GPT receipts. The original generation and current review are explicitly distinct; authenticity is not cryptographically established.

Every file is max 1 MiB, manifest max 256 KiB, total max 16 MiB, max 512 bound files. Secure descriptor-relative no-follow reads and final snapshot revalidation reject symlink roots/components/files, nonregular files, drift and unknown/missing entries. Platforms lacking required filesystem primitives fail closed. Inventory enumeration uses existing RootArtifactReader behavior; pathological directories may still cost enumeration resources before inventory rejection.

## Filters and limitations

Optional `attachment_key`, exact section ID `section`, and `local_page` intersect. Local page numbers belong to attachment namespaces; an unqualified page valid in multiple attachments refuses ambiguity even if another filter would narrow matches. Unknown valid section/attachment returns `status: no-match` with empty `matches`, not semantic refusal. Results include real summary refs, fingerprints, headings, attachment IDs and local pages; no summary text. `semantic_answer: unsupported` is separate from matching status. No query argument, generative answer, CLI, writer, canonical acceptance/classification/indexing/publication integration, or authority system is provided. Unrepresented scientific sections cannot be inferred; completeness means completeness against the pinned structure only. Private corpus acceptance is unperformed.

Synthetic executable package construction and usage: `tests/test_millefeuille_reviewed_summary_handoff.py`.
