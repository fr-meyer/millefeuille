"""Command implementations for the Millefeuille CLI.

This module contains the command functions that implement the dry-run and
processing workflows. These commands are called from the main entry point
after configuration validation and client initialization.
"""

import logging
from typing import Any

from tabulate import tabulate

from millefeuille.clients.ocr_client import OCRClient
from millefeuille.clients.zotero_client import ZoteroClient
from millefeuille.domain.artifact_writer import write_dry_run_artifacts
from millefeuille.domain.config import AppConfig
from millefeuille.domain.extraction_fixtures import (
    write_native_extractions_from_evidence,
    write_ocr_extractions_from_evidence,
)
from millefeuille.domain.models import OpenKBHandoffRow, TagAddingResult
from millefeuille.domain.source_packs import write_source_packs_from_handoff_evidence
from millefeuille.domain.tree_processor import TreeStructureProcessor
from millefeuille.orchestration.pipeline import (
    Pipeline,
    _plan_outcome_tags,
)
from millefeuille.orchestration.processor import ItemProcessor
from millefeuille.utils.export import (
    build_export_records,
    build_openkb_handoff_rows,
    log_export_records,
    log_openkb_weak_verification_warnings,
    validate_openkb_handoff_rows,
    write_openkb_preview_jsonl,
)
from millefeuille.utils.logging import (
    _format_with_emoji,
    _supports_unicode,
    log_config_summary,
    log_discovery_stats,
    log_error_summary,
    log_selection_tagging_summary,
    log_summary_table,
    log_timing_summary,
)
from millefeuille.utils.redaction import redact_url


def attachment_is_pdf(content_type: str | None, filename: str | None) -> bool:
    """Return True if an attachment should be treated as a PDF."""
    normalized_content_type = (content_type or "").lower()
    normalized_filename = (filename or "").lower()
    return (
        normalized_content_type == "application/pdf"
        or normalized_filename.endswith(".pdf")
    )


def count_pdf_attachments(item_attachments: list[Any]) -> int:
    """Count PDF attachments on an item."""
    return sum(
        1
        for attachment in item_attachments
        if attachment_is_pdf(attachment.content_type, attachment.filename)
    )


def dry_run_command(
    cfg: AppConfig, logger: logging.Logger, zotero_client: ZoteroClient
) -> int:
    """Execute dry-run mode to preview items without processing.

    Fetches items matching the configured tags and displays a preview table
    showing item titles and PDF counts without performing any actual processing.

    Args:
        cfg: Application configuration object
        logger: Logger instance for logging messages
        zotero_client: Initialized Zotero client instance

    Returns:
        Exit code: 0 for success
    """
    logger.info("Dry-run mode enabled - previewing items without processing")
    items, discovery_stats = zotero_client.get_items_by_selection(
        cfg.tagging.selection, cfg.tagging.include_abstract
    )

    total_pdfs = sum(count_pdf_attachments(item.attachments) for item in items)

    log_config_summary(logger, len(items), cfg.ocr)
    logger.info(f"Total PDF attachments: {total_pdfs}")

    if items:
        logger.info("Preview of items to be processed:")
        unicode = _supports_unicode()
        sep_char = "\u2500" if unicode else "-"
        for idx, item in enumerate(items, start=1):
            pdf_count = count_pdf_attachments(item.attachments)
            header = f" Item {idx}/{len(items)} "
            logger.info(f"{sep_char * 2}{header}{sep_char * (50 - len(header))}")
            logger.info(f"  Title       : {item.title[:60]}")
            logger.info(f"  Citation key: {item.citation_key or '[none]'}")
            logger.info(f"  DOI         : {item.paper_metadata.doi or '[none]'}")
            logger.info(
                f"  Authors     : {item.paper_metadata.author_string or '[no authors]'}"
            )
            logger.info(f"  PDFs        : {pdf_count}")
            current_tags = ", ".join(item.tags) if item.tags else "[none]"
            logger.info(f"  Current tags: {current_tags}")
            if cfg.ocr.enabled or cfg.download.enabled:
                success_plan = _plan_outcome_tags(cfg.tagging, cfg.zotero, "success")
                failure_plan = _plan_outcome_tags(cfg.tagging, cfg.zotero, "failure")
                success_add = (
                    ", ".join(success_plan.tags_to_add)
                    if success_plan.tags_to_add
                    else "[none]"
                )
                success_remove = (
                    ", ".join(success_plan.tags_to_remove)
                    if success_plan.tags_to_remove
                    else "[none]"
                )
                failure_add = (
                    ", ".join(failure_plan.tags_to_add)
                    if failure_plan.tags_to_add
                    else "[none]"
                )
                failure_remove = (
                    ", ".join(failure_plan.tags_to_remove)
                    if failure_plan.tags_to_remove
                    else "[none]"
                )
                logger.info("  On success:")
                logger.info(f"    Would add   : {success_add}")
                logger.info(f"    Would remove: {success_remove}")
                logger.info("  On failure:")
                logger.info(f"    Would add   : {failure_add}")
                logger.info(f"    Would remove: {failure_remove}")

        log_discovery_stats(logger, discovery_stats, total_pdfs)
    else:
        logger.info("No items found to process")
        log_discovery_stats(logger, discovery_stats, total_pdfs)

    if cfg.tag_adding.enabled and cfg.tag_adding.assignments:
        assignments = cfg.tag_adding.assignments
        configured_keys = set(assignments.keys())

        matching_items = [
            item
            for item in items
            if (item.citation_key or "").strip() in configured_keys
        ]

        logger.info("")
        formatted_header = _format_with_emoji(
            "Tag Adding Preview:", "\U0001f3f7\ufe0f", "[TAG ADDING]"
        )
        logger.info(formatted_header)

        if matching_items:
            if cfg.tag_adding.replace_all_existing_tags:
                logger.info(
                    "\u26a0\ufe0f  Replace mode: all existing tags on these items "
                    "will be removed."
                )
                for item in matching_items:
                    title = item.title[:60]
                    ckey = (item.citation_key or "").strip()
                    assigned_tags = assignments.get(ckey, [])
                    logger.info(
                        f'  - "{title}" (citation key: {ckey})  \u2192  '
                        f"tags REPLACED by: {assigned_tags}"
                    )
                logger.info(
                    f"  {len(matching_items)} item(s) would have ALL existing tags "
                    "replaced with their assigned tags"
                )
            else:
                for item in matching_items:
                    title = item.title[:60]
                    ckey = (item.citation_key or "").strip()
                    assigned_tags = assignments.get(ckey, [])
                    logger.info(
                        f'  - "{title}" (citation key: {ckey})  \u2192  '
                        f"tags: {assigned_tags}"
                    )
                logger.info(
                    f"  {len(matching_items)} item(s) would be tagged with "
                    "their assigned tags"
                )
        else:
            logger.info("  No items match the configured citation key list")

        discovered_keys = {
            (item.citation_key or "").strip()
            for item in items
            if (item.citation_key or "").strip()
        }
        unmatched_keys = [k for k in assignments if k not in discovered_keys]
        logger.info(f"  Unmatched assignment keys: {len(unmatched_keys)}")
        if unmatched_keys:
            example_count = min(5, len(unmatched_keys))
            logger.info(
                f"  First {example_count} example(s): {unmatched_keys[:5]}"
            )

    if cfg.selection_tagging.enabled:
        logger.info("")
        formatted_header = _format_with_emoji(
            "Selection Tagging Preview:", "\U0001f3f7\ufe0f", "[SELECTION TAGGING]"
        )
        logger.info(formatted_header)

        for item in items:
            title = item.title[:60]
            logger.info(f'  - "{title}"')
            current_tags = ", ".join(item.tags) if item.tags else "[none]"
            logger.info(f"  Current tags: {current_tags}")
            would_add = (
                ", ".join(cfg.selection_tagging.add.values)
                if cfg.selection_tagging.add.values
                else "[none]"
            )
            logger.info(f"  Would add: {would_add}")
            would_remove = (
                ", ".join(cfg.selection_tagging.remove.values)
                if cfg.selection_tagging.remove.values
                else "[none]"
            )
            logger.info(f"  Would remove: {would_remove}")

        logger.info(f"  Selected items: {len(items)}")
        logger.info(
            f"  Planned add ops: {len(items) * len(cfg.selection_tagging.add.values)}"
        )
        logger.info(
            f"  Planned remove ops: "
            f"{len(items) * len(cfg.selection_tagging.remove.values)}"
        )

    if cfg.export.attachment_urls.enabled:
        records = build_export_records(items, zotero_client)
        if cfg.export.attachment_urls.auth_query.enabled:
            for rec in records:
                auth_url = zotero_client.build_authenticated_attachment_url(
                    rec.attachment_key
                )
                logger.info(
                    "[AUTH QUERY URL] item_key=%s attachment_key=%s url=%s",
                    rec.item_key,
                    rec.attachment_key,
                    redact_url(auth_url),
                )
        if cfg.export.attachment_urls.log:
            log_export_records(records, logger)
        if cfg.export.attachment_urls.write_manifest:
            logger.info(
                "  [dry-run] Manifest write suppressed "
                "(no writes in dry-run mode)"
            )

    openkb_handoff_rows: list[OpenKBHandoffRow] = []
    if cfg.export.openkb_handoff.enabled:
        openkb_handoff_rows = build_openkb_handoff_rows(
            items, zotero_client, cfg.export.openkb_handoff
        )
        report = validate_openkb_handoff_rows(
            openkb_handoff_rows, mode="dry_run", source_items=items
        )
        log_openkb_weak_verification_warnings(
            logger, report.weak_verification_rows
        )
        if report.failures:
            for failure in report.failures:
                logger.error(str(failure))
            return 2
        if cfg.export.openkb_handoff.preview_jsonl_path:
            write_openkb_preview_jsonl(
                openkb_handoff_rows, cfg.export.openkb_handoff.preview_jsonl_path
            )
            logger.info(
                "Non-authoritative OpenKB handoff preview written to "
                f"{cfg.export.openkb_handoff.preview_jsonl_path}"
            )
        else:
            logger.info(
                f"[DRY-RUN] OpenKB handoff JSONL write suppressed "
                f"({len(openkb_handoff_rows)} rows validated)"
            )

    if (
        cfg.export.artifacts.enabled
        and cfg.export.artifacts.source_pack_intake_evidence_path
    ):
        assert cfg.export.artifacts.source_pack_root is not None
        intake_results = write_source_packs_from_handoff_evidence(
            handoff_rows=openkb_handoff_rows,
            evidence_path=cfg.export.artifacts.source_pack_intake_evidence_path,
            source_pack_root=cfg.export.artifacts.source_pack_root,
        )
        logger.info(
            "[DRY-RUN] Source-pack intake fixtures written: "
            f"{len(intake_results)}"
        )
        for result in intake_results:
            logger.info(
                "  - paper_id=%s status=%s manifest=%s",
                result.paper_id,
                result.status,
                result.manifest_path,
            )

    if (
        cfg.export.artifacts.enabled
        and cfg.export.artifacts.native_extraction_evidence_path
    ):
        assert cfg.export.artifacts.source_pack_root is not None
        native_results = write_native_extractions_from_evidence(
            evidence_path=cfg.export.artifacts.native_extraction_evidence_path,
            source_pack_root=cfg.export.artifacts.source_pack_root,
        )
        logger.info(
            "[DRY-RUN] Native extraction fixtures written: %s",
            len(native_results),
        )
        for result in native_results:
            logger.info(
                "  - paper_id=%s status=%s evidence=%s",
                result.paper_id,
                result.status,
                result.evidence_path,
            )

    if (
        cfg.export.artifacts.enabled
        and cfg.export.artifacts.ocr_extraction_evidence_path
    ):
        assert cfg.export.artifacts.source_pack_root is not None
        ocr_results = write_ocr_extractions_from_evidence(
            evidence_path=cfg.export.artifacts.ocr_extraction_evidence_path,
            source_pack_root=cfg.export.artifacts.source_pack_root,
        )
        logger.info(
            "[DRY-RUN] OCR extraction fixtures written: %s",
            len(ocr_results),
        )
        for result in ocr_results:
            logger.info(
                "  - paper_id=%s status=%s evidence=%s",
                result.paper_id,
                result.status,
                result.evidence_path,
            )

    if cfg.export.artifacts.enabled:
        artifact_results = write_dry_run_artifacts(
            items=items,
            handoff_rows=openkb_handoff_rows,
            config=cfg.export.artifacts,
            handoff_enabled=cfg.export.openkb_handoff.enabled,
        )
        logger.info(
            "[DRY-RUN] Millefeuille artifact indexes written: "
            f"{len(artifact_results)}"
        )
        for result in artifact_results:
            logger.info(
                "  - paper_id=%s run_id=%s artifact_index=%s "
                "stage_manifest=%s",
                result.paper_id,
                result.run_id,
                result.artifact_index_path,
                result.stage_manifest_path,
            )

    return 0


def _failure_severity(failed: int, succeeded: int, *, total: int = 0) -> int:
    """Map per-feature failed/succeeded counts to exit severity (0, 1, or 2)."""
    if failed <= 0:
        return 0
    if succeeded > 0:
        return 1
    if total > 0 or failed > 0:
        return 2
    return 0


def _determine_exit_code(
    summary: dict[str, Any],
) -> int:
    """Determine the appropriate exit code based on processing summary.

    Each active feature contributes its own severity from feature-specific
    counters; the final exit code is the worst severity across contributors.

    Args:
        summary: Dictionary containing processing summary with keys:
            - failed_items: Number of items that failed processing
            - successful_items: Number of items that succeeded
            - total_items: Total number of items processed
            - total_pdfs_failed: Number of PDFs that failed to download

    Returns:
        Exit code: 0 for success, 1 for partial failure, 2 for complete failure
    """
    failed_items = summary.get("failed_items", 0)
    successful_items = summary.get("successful_items", 0)
    total_items = summary.get("total_items", 0)
    total_pdfs_failed = summary.get("total_pdfs_failed", 0)

    # Type narrow to int
    if not isinstance(failed_items, int):
        failed_items = 0
    if not isinstance(successful_items, int):
        successful_items = 0
    if not isinstance(total_items, int):
        total_items = 0
    if not isinstance(total_pdfs_failed, int):
        total_pdfs_failed = 0

    severities: list[int] = []

    if total_pdfs_failed > 0:
        total_pdfs_downloaded = summary.get("total_pdfs_downloaded", 0)
        if not isinstance(total_pdfs_downloaded, int):
            total_pdfs_downloaded = 0
        severities.append(
            _failure_severity(total_pdfs_failed, total_pdfs_downloaded)
        )

    tag_adding_failed = summary.get("tag_adding_failed", 0)
    if not isinstance(tag_adding_failed, int):
        tag_adding_failed = 0
    if tag_adding_failed > 0:
        tag_adding_succeeded = summary.get("tag_adding_succeeded", 0)
        if not isinstance(tag_adding_succeeded, int):
            tag_adding_succeeded = 0
        severities.append(
            _failure_severity(tag_adding_failed, tag_adding_succeeded)
        )

    st_item_failed = summary.get("selection_tagging_item_failed", 0)
    if not isinstance(st_item_failed, int):
        st_item_failed = 0
    if st_item_failed > 0:
        st_item_succeeded = summary.get("selection_tagging_item_succeeded", 0)
        if not isinstance(st_item_succeeded, int):
            st_item_succeeded = 0
        severities.append(_failure_severity(st_item_failed, st_item_succeeded))

    if failed_items > 0 and (total_items > 0 or successful_items > 0):
        severities.append(_failure_severity(failed_items, successful_items))

    if severities:
        return max(severities)
    return 0


def _display_download_summary(logger: logging.Logger, summary: dict) -> None:
    """Display summary for download-only mode.

    Displays a formatted table with download statistics including downloaded,
    skipped, and failed counts, along with timing information and skipped items.

    Args:
        logger: Logger instance for logging messages
        summary: Dictionary containing download processing summary with keys:
            - total_pdfs_downloaded: Number of PDFs successfully downloaded
            - total_pdfs_skipped: Number of PDFs skipped
            - total_pdfs_failed: Number of PDFs that failed to download
            - total_time: Total execution time in seconds
            - skipped_items: Optional number of items skipped during discovery
    """
    # Summary header
    logger.info("")
    formatted_header = _format_with_emoji("Download Summary:", "📥", "[DOWNLOAD]")
    logger.info(formatted_header)

    # Extract metrics from summary dictionary
    downloaded = summary.get("total_pdfs_downloaded", 0)
    skipped = summary.get("total_pdfs_skipped", 0)
    failed = summary.get("total_pdfs_failed", 0)
    total = downloaded + skipped + failed

    # Build table data
    table_data = [
        ["Downloaded", downloaded],
        ["Skipped", skipped],
        ["Failed", failed],
        ["Total", total],
    ]

    # Format and log table
    tablefmt = "grid" if _supports_unicode() else "simple"
    table_str = tabulate(table_data, headers=["Status", "Count"], tablefmt=tablefmt)
    logger.info(table_str)

    # Timing summary
    log_timing_summary(logger, summary.get("total_time", 0))

    # Skipped items
    skipped_items = summary.get("skipped_items", 0)
    if skipped_items > 0:
        message = f"Skipped: {skipped_items} items (already processed)"
        formatted_message = _format_with_emoji(message, "⏭️", "[SKIP]")
        logger.info("")
        logger.info(formatted_message)


def _display_combined_summary(logger: logging.Logger, summary: dict) -> None:
    """Display summary for download+OCR combined mode.

    Displays both download statistics and OCR results in a unified view,
    including per-item OCR results, timing information, and error details.

    Args:
        logger: Logger instance for logging messages
        summary: Dictionary containing combined download and OCR processing summary
            with keys:
            - total_pdfs_downloaded: Number of PDFs successfully downloaded
            - total_pdfs_skipped: Number of PDFs skipped
            - total_pdfs_failed: Number of PDFs that failed to download
            - results: List of ProcessingResult objects from OCR processing
            - skipped_items: Optional number of items skipped during discovery
            - total_time: Total execution time in seconds
    """
    # Download section
    logger.info("")
    formatted_header = _format_with_emoji("Download Summary:", "📥", "[DOWNLOAD]")
    logger.info(formatted_header)

    # Extract download metrics
    downloaded = summary.get("total_pdfs_downloaded", 0)
    skipped = summary.get("total_pdfs_skipped", 0)
    failed = summary.get("total_pdfs_failed", 0)

    # Build download table data
    download_table_data = [
        ["Downloaded", downloaded],
        ["Skipped", skipped],
        ["Failed", failed],
    ]

    # Format and log download table
    tablefmt = "grid" if _supports_unicode() else "simple"
    download_table_str = tabulate(
        download_table_data, headers=["Status", "Count"], tablefmt=tablefmt
    )
    logger.info(download_table_str)

    # OCR section
    logger.info("")
    ocr_header = _format_with_emoji("OCR Summary:", "🔍", "[OCR]")
    logger.info(ocr_header)
    log_summary_table(
        logger, summary.get("results", []), summary.get("skipped_items", 0)
    )

    # Timing and errors
    log_timing_summary(logger, summary.get("total_time", 0))
    log_error_summary(logger, summary.get("results", []))


def _display_tag_adding_summary(
    logger: logging.Logger, tag_adding_results: list[TagAddingResult]
) -> None:
    """Display summary for tag-adding operations.

    Displays a formatted table with per-item tag-adding outcomes including
    which tags were added and which failed for each matched item.

    Args:
        logger: Logger instance for logging messages
        tag_adding_results: List of TagAddingResult objects from tag-adding
    """
    logger.info("")
    formatted_header = _format_with_emoji(
        "Tag Adding Summary:", "\U0001f3f7\ufe0f", "[TAG ADDING]"
    )
    logger.info(formatted_header)

    if not tag_adding_results:
        logger.info("No items matched the configured citation key list")
        return

    table_data = [
        [
            result.item_title[:40],
            ", ".join(result.tags_added),
            ", ".join(result.tags_failed),
        ]
        for result in tag_adding_results
    ]

    tablefmt = "grid" if _supports_unicode() else "simple"
    table_str = tabulate(
        table_data,
        headers=["Item Title", "Tags Applied", "Tags Failed"],
        tablefmt=tablefmt,
    )
    logger.info(table_str)

    matched = len(tag_adding_results)
    succeeded = sum(1 for r in tag_adding_results if not r.tags_failed)
    failed = sum(1 for r in tag_adding_results if r.tags_failed)
    logger.info(f"Matched: {matched} | Succeeded: {succeeded} | Failed: {failed}")


def _display_selection_tagging_summary(
    logger: logging.Logger, summary: dict[str, Any]
) -> None:
    """Display summary for selection-tagging operations."""
    log_selection_tagging_summary(logger, summary)


def process_command(
    cfg: AppConfig,
    logger: logging.Logger,
    zotero_client: ZoteroClient,
    ocr_client: OCRClient | None,
    tree_processor: TreeStructureProcessor | None = None,
) -> int:
    """Execute the full pipeline processing workflow.

    Initializes the processor and pipeline, executes the processing workflow,
    and displays comprehensive summary information including per-item results,
    timing, and error details. Supports download-only, download+OCR, and OCR-only
    modes with appropriate summary display for each mode.

    Args:
        cfg: Application configuration object
        logger: Logger instance for logging messages
        zotero_client: Initialized Zotero client instance
        ocr_client: OCR client when ``cfg.ocr.enabled``; otherwise None
        tree_processor: Optional tree structure processor instance

    Returns:
        Exit code: 0 for success, 1 for partial failure, 2 for complete failure
    """
    logger.info("Starting pipeline execution...")
    processor = ItemProcessor(zotero_client, ocr_client, cfg.processing)
    pipeline = Pipeline(
        zotero_client,
        ocr_client,
        processor,
        cfg.zotero,
        cfg.processing,
        cfg.storage,
        cfg.tree_structure,
        cfg.ocr,
        cfg.download,
        cfg.tag_adding,
        cfg.tagging,
        cfg.selection_tagging,
        tree_processor=tree_processor,
        export_config=cfg.export,
    )
    summary = pipeline.run()

    if cfg.selection_tagging.enabled and not summary.get(
        "selection_tagging_summary_displayed"
    ):
        _display_selection_tagging_summary(logger, summary)

    # Determine which mode is enabled for appropriate summary display
    tag_adding_only = (
        cfg.tag_adding.enabled and not cfg.ocr.enabled and not cfg.download.enabled
    )
    download_only = (
        cfg.download.enabled and not cfg.ocr.enabled and not cfg.tag_adding.enabled
    )
    download_and_tag = (
        cfg.download.enabled and not cfg.ocr.enabled and cfg.tag_adding.enabled
    )
    download_and_ocr = cfg.download.enabled and cfg.ocr.enabled

    # Route to appropriate summary display based on mode
    if tag_adding_only:
        _display_tag_adding_summary(
            logger, summary.get("tag_adding_results", [])
        )
        no_key = summary.get("tag_adding_no_key", 0)
        logger.info(
            _format_with_emoji(
                f"Items without citation key (skipped): {no_key}",
                "\U0001f3f7\ufe0f",
                "[TAG ADDING]",
            )
        )
        logger.info(
            _format_with_emoji(
                f"Items marked as processed: {summary.get('tag_adding_processed', 0)}",
                "\U0001f3f7\ufe0f",
                "[TAG ADDING]",
            )
        )
    elif download_only:
        _display_download_summary(logger, summary)
    elif download_and_tag:
        _display_download_summary(logger, summary)
        eligible = summary.get("tag_adding_eligible", 0)
        logger.info(
            _format_with_emoji(
                f"Eligible for Tag Adding (download succeeded): {eligible}",
                "\U0001f3f7\ufe0f",
                "[ELIGIBLE]",
            )
        )
        _display_tag_adding_summary(
            logger, summary.get("tag_adding_results", [])
        )
        no_key = summary.get("tag_adding_no_key", 0)
        logger.info(
            _format_with_emoji(
                f"Items without citation key (skipped): {no_key}",
                "\U0001f3f7\ufe0f",
                "[TAG ADDING]",
            )
        )
        logger.info(
            _format_with_emoji(
                f"Items marked as processed: {summary.get('tag_adding_processed', 0)}",
                "\U0001f3f7\ufe0f",
                "[TAG ADDING]",
            )
        )
    elif download_and_ocr:
        _display_combined_summary(logger, summary)
        if cfg.tag_adding.enabled:
            _display_tag_adding_summary(
                logger, summary.get("tag_adding_results", [])
            )
            no_key = summary.get("tag_adding_no_key", 0)
            logger.info(
                _format_with_emoji(
                    f"Items without citation key (skipped): {no_key}",
                    "\U0001f3f7\ufe0f",
                    "[TAG ADDING]",
                )
            )
    elif (
        cfg.selection_tagging.enabled
        and not cfg.ocr.enabled
        and not cfg.download.enabled
    ):
        log_timing_summary(logger, summary.get("total_time", 0.0))
    else:
        # OCR-only or OCR + tag-adding
        results = summary.get("results", [])
        skipped_items = summary.get("skipped_items", 0)
        total_time = summary.get("total_time", 0.0)

        # Type narrow to expected types
        if not isinstance(results, list):
            results = []
        if not isinstance(skipped_items, int):
            skipped_items = 0
        if not isinstance(total_time, (int, float)):
            total_time = 0.0

        log_summary_table(logger, results, skipped_items)
        log_timing_summary(logger, float(total_time))
        log_error_summary(logger, results)

        if cfg.tag_adding.enabled:
            _display_tag_adding_summary(
                logger, summary.get("tag_adding_results", [])
            )
            no_key = summary.get("tag_adding_no_key", 0)
            logger.info(
                _format_with_emoji(
                    f"Items without citation key (skipped): {no_key}",
                    "\U0001f3f7\ufe0f",
                    "[TAG ADDING]",
                )
            )

    # Determine exit code based on summary
    return _determine_exit_code(summary)
