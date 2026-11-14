# Difficult-source qualification (offline, Phase 3)

Base: `025cd2161941ad14a907b444cf7cbaf16f60e634`, 2026-11-11.
This is contract qualification, NOT real PDF extraction, live provider acceptance,
or evidence that Phase 3 is complete. No source downloads or provider calls occurred.

## Evidence legend and exact test mapping

Test identifiers below are `tests.<module>.<class>.<method>`. All existing tests
listed here were run in the bounded selection; new dependency tests were run separately.
`S` = synthetic/fixture demonstrated; `U` = actual extraction/provider behavior unproven.
The intake fixtures are arbitrary bytes with a `.pdf` filename, not valid PDFs.

| Source row | Exact test mapping | Demonstrated / limits |
| --- | --- | --- |
| Native text | `test_millefeuille_source_pack_writer.TestSourcePackIntakeWriter.test_native_extraction_fixture_writes_sidecars_after_source_pack_verification` | S: supplied Markdown and identity sidecar persistence. U: embedded-text extraction, reading order and quality. `PyPDF2-fixture` is a label, not an invoked extractor. |
| Fully scanned | `test_millefeuille_source_pack_writer.TestSourcePackIntakeWriter.test_ocr_extraction_fixture_writes_sidecars_after_source_pack_verification`; `test_millefeuille_difficult_source_matrix.DifficultSourceRouteMatrixTests.test_native_ocr_and_dual_report_only_present_evidence` | S: supplied OCR Markdown, requested `mistral-ocr-latest`, fixture returned version, OCR-only dependency branch. U: image-only detection, provider execution and accuracy. |
| Mixed native/scanned | `test_millefeuille_source_pack_writer.TestSourcePackIntakeWriter.test_route_selection_fixture_writes_selected_fulltext`; `test_route_selection_fixture_requires_both_extractions_for_merged_dual` in same module/class | S: preselected merged-dual requires both sidecars. U: page-level route decision, actual merge quality and scanned-page completeness. |
| Difficult tables/layout | `test_millefeuille_local_structure.TestLocalMarkdownStructure.test_builds_pages_sections_tables_figures_and_references`; `test_millefeuille_table_row_guard.TableRowClosingGuardTests.test_uneven_rows_keep_exact_values_and_padding`; `test_unterminated_last_row_is_preserved` in same module/class | S: Markdown structure and token-row preservation. U: multi-column PDF reading order, spanning cells, rotated pages, equations, numeric OCR fidelity. |
| Paper + supplement | `test_millefeuille_multi_source_upstream.MultiSourceUpstreamTests.test_full_member_join_and_order_independent_no_write_preview`; `test_millefeuille_multi_source_acceptance.MultiSourceAcceptanceTests.test_valid_two_pdf_evidence_passes_without_writes` | S: complete 2-member scope, 3 supplied page markers, attachment locators and order independence; fixture acceptance only. U: real two-PDF extraction and supplement interpretation. |
| Duplicate | `test_millefeuille_multi_source_upstream.MultiSourceUpstreamTests.test_missing_duplicate_and_wrong_member_refused`; `test_millefeuille_duplicate_review.TestDuplicateProfileReview.test_unreviewed_match_remains_needs_review`; `test_full_acceptance_recognizes_review_and_keeps_original_match` in same module/class | S: duplicate member refusal and reviewed duplicate-profile contracts. U: real corpus duplicate detection quality; duplicate profile is not proof of duplicate PDFs. |
| Competing versions | `test_openkb_source_version_drift.TestOpenkbSourceVersionDriftFixture.test_matching_recovered_bytes_pass_hash_verification`; `test_drifted_recovered_bytes_fail_hash_verification`; `test_missing_handoff_sha_is_not_verifiable` in same module/class; `test_millefeuille_multi_source_acceptance.MultiSourceAcceptanceTests.test_native_source_drift_fails_closed` | S: expected-byte identity and drift rejection. U: semantic version ranking, authoritative-version choice; hashes do not choose a winner. |
| Missing source | `test_millefeuille_source_pack_writer.TestSourcePackIntakeWriter.test_handoff_intake_missing_evidence_fails_before_any_write`; `test_millefeuille_multi_source_upstream.MultiSourceUpstreamTests.test_missing_duplicate_and_wrong_member_refused` | S: missing intake evidence/member refusal before output. U: missing-file behavior of a real PDF extractor. |
| Corrupt source | `test_millefeuille_source_pack_writer.TestSourcePackIntakeWriter.test_hash_mismatch_fails_before_creating_source_pack`; `test_millefeuille_multi_source_upstream.MultiSourceUpstreamTests.test_pack_byte_drift_refused_before_output` | S: byte corruption relative to expected hash fails. U: malformed PDF with matching hash. Intake verifies identity, not PDF syntax. |
| Encrypted source | No direct PDF test in this bounded inspected surface; related `test_millefeuille_difficult_source_matrix.DifficultSourceRouteMatrixTests.test_each_route_requires_its_extraction_evidence` | S only for absent required extraction evidence. U: encrypted-PDF detection, password-required disposition, no accidental success or secret leakage. |
| Unsupported source | No direct format-decoder test in this bounded inspected surface; related `test_millefeuille_difficult_source_matrix.DifficultSourceRouteMatrixTests.test_each_route_requires_its_extraction_evidence` | S only for route dependency rejection. U: content-type/format rejection and actionable unsupported disposition. Arbitrary fixture bytes accepted by intake are not proof of supported format. |

New route tests cover seven missing-evidence combinations and three positive
combinations directly at `_validate_route_dependencies`. They deliberately use
minimal dictionaries and do not claim sidecar validation, extraction or end-to-end acceptance.

## Code boundaries and remaining live gate

`extraction_fixtures.py` consumes existing Markdown; `route_fixtures.py` consumes
an explicit `selected_route`, rather than measuring PDF quality.
`multi_source_upstream.py` joins supplied native Markdown and page markers.
`local_structure.py` parses selected Markdown, not PDF layout.
The OCR contract in `ocr-backend-contract.md` distinguishes fixture evidence
from a gated adapter. Existing legacy pipeline upload/poll methods are not
provider evidence and were not executed. No production defect was established.

Parent may generate local synthetic PDFs under the existing offline authority;
this child did not do so. External fixture acquisition and any upload/provider
call remain separately scoped. For a bounded real-source canary, pin the
input hashes and expected page/attachment inventory first; run one valid native
PDF locally and one image-only PDF through the approved Mistral route, recording
requested and returned model IDs, page coverage, selected-route reason, output
hashes and manually checked text/table fidelity. This canary cannot close all rows.
Then add mixed, complex-layout, paper+supplement, version/duplicate pairs and
matching-hash malformed/encrypted/unsupported cases with explicit expected
success/review/error dispositions. No silent provider fallback. Current child
authority does not permit this acquisition or live execution.

## Validation and policy classification

Focused existing selection: 50 tests, 49 passed, 1 platform-capability skip.
New tests: 2 passed, no skips (seven negative and three positive subcases).
Ruff and whitespace checks passed; metadata parsed with PyYAML and JSON
(Ruby is unavailable). The skipped test is
`test_millefeuille_local_structure.TestUnsupportedLocalStructurePreparation.test_cli_fails_closed_without_creating_output_parent`,
which requires a platform without descriptor-relative no-follow writes.
Logs and validation details remain under ignored `.local-validation/`.
No full suite, package-security rerun, private artifacts or external writes.
Manifest PR98 and retired Mergeguez-review declarations are repository policy
drift, not verified GitHub enforcement; no review lane was invoked or waived.
Parent owns integration, publication and metadata/access reconciliation.
