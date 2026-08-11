"""Packaged CLI entry point for Millefeuille.

This module mirrors the repo-root main.py but is designed for installed-package
invocation via ``millefeuille`` (console_scripts) or
``python -m millefeuille``.  Key differences from the repo-root copy:

1. ``register_configs()`` is called at **module level** so that Hydra's
   config store is populated before the ``@hydra.main`` decorator fires.
2. When all operations are disabled the CLI prints a help/usage message and
   exits cleanly (exit 0) instead of raising ``ConfigError``.
3. Path-consuming modes (download, save-to-disk) reject the packaged
   placeholder defaults and require an explicit override.
"""

from collections.abc import Mapping
import json
import logging
import os
import sys

import hydra
from omegaconf import DictConfig, OmegaConf

from millefeuille.cli.artifacts import run_artifact_cli
from millefeuille.cli.commands import dry_run_command, process_command
from millefeuille.cli.lifecycle_tags import run_lifecycle_tags_cli
from millefeuille.cli.source_pack import run_source_pack_cli
from millefeuille.cli.stages import run_stage_cli
from millefeuille.cli.taxonomy import run_taxonomy_cli
from millefeuille.clients.exceptions import (
    OCRClientError,
    OpenKBHandoffValidationError,
    ZoteroClientError,
)
from millefeuille.clients.mistral_client import MistralClient
from millefeuille.clients.ocr_client import OCRClient
from millefeuille.clients.pageindex_client import PageIndexClient
from millefeuille.clients.pageindex_tree_client import PageIndexTreeClient
from millefeuille.clients.zotero_client import ZoteroClient
from millefeuille.domain.config import (
    PACKAGED_PLACEHOLDER_DOWNLOAD_FOLDER,
    PACKAGED_PLACEHOLDER_LIBRARY_ID,
    PACKAGED_PLACEHOLDER_READ_KEY,
    PACKAGED_PLACEHOLDER_STORAGE_BASE_DIR,
    AppConfig,
    ArtifactExportConfig,
    AttachmentUrlExportConfig,
    AuthQueryConfig,
    AuthQueryHelperConfig,
    ConfigError,
    DownloadConfig,
    ExportConfig,
    MistralOCRConfig,
    OpenKBHandoffExportConfig,
    PageIndexOCRConfig,
    ProcessingConfig,
    RetryConfig,
    SelectionTaggingConfig,
    StorageConfig,
    TagAddingConfig,
    TaggingConfig,
    TagRuleConfig,
    TagSelectionConfig,
    TagTargetConfig,
    TreeStructureConfig,
    ZoteroConfig,
    register_configs,
)
from millefeuille.domain.tree_processor import TreeStructureProcessor
from millefeuille.utils.logging import setup_logging
from millefeuille.utils.redaction import redact_message

# ---------------------------------------------------------------------------
# Populate Hydra's ConfigStore before the @hydra.main decorator is evaluated.
# ---------------------------------------------------------------------------
register_configs()


# ---------------------------------------------------------------------------
# Helper functions (ported unchanged from repo-root main.py)
# ---------------------------------------------------------------------------


def initialize_clients(
    cfg: AppConfig, logger: logging.Logger
) -> tuple[ZoteroClient, OCRClient | None]:
    """Initialize Zotero client and, when OCR is enabled, the OCR client.

    OCR clients read API credentials in ``__init__``. They are not constructed
    when ``cfg.ocr.enabled`` is False so download-only and tag-adding-only runs
    do not require Mistral/PageIndex keys.

    Args:
        cfg: Application configuration object
        logger: Logger instance

    Returns:
        Tuple of ``(zotero_client, ocr_client)``. ``ocr_client`` is None when
        OCR is disabled.
    """
    logger.info("Initializing Zotero client...")
    zotero_client = ZoteroClient(cfg.credentials)
    logger.info("Zotero client initialized successfully")

    if not cfg.ocr.enabled:
        logger.info("OCR disabled — skipping OCR client initialization")
        return zotero_client, None

    logger.info("Initializing OCR client...")
    provider = cfg.ocr.provider
    ocr_client: OCRClient
    if provider == "mistral":
        if not isinstance(cfg.ocr, MistralOCRConfig):
            raise ConfigError(
                "OCR config is not MistralOCRConfig for provider 'mistral'"
            )
        ocr_client = MistralClient(cfg.ocr)
    elif provider == "pageindex":
        if not isinstance(cfg.ocr, PageIndexOCRConfig):
            raise ConfigError(
                "OCR config is not PageIndexOCRConfig for provider 'pageindex'"
            )
        ocr_client = PageIndexClient(cfg.ocr)
    else:
        raise ConfigError(f"Unknown OCR provider: {provider}")

    logger.info("OCR client initialized successfully")

    return zotero_client, ocr_client


def validate_tree_config(cfg: AppConfig) -> None:
    """Validate tree structure configuration requirements.

    When ``cfg.tree_structure.enabled`` is True, checks whether PageIndex API
    credentials are present when ``cfg.ocr`` is a ``PageIndexOCRConfig`` (via
    ``cfg.ocr.api_key``). If the key is missing, this function logs a debug
    message only; it does not raise. ``initialize_tree_processor`` performs
    real credential resolution and returns ``None`` when credentials are
    unavailable, so tree processing is skipped later in the pipeline.

    This validation is called after config conversion but before client
    initialization. It is skipped when ``processing.dry_run`` is True because
    dry-run mode does not initialize tree components.

    Args:
        cfg: Application configuration object
    """
    logger = logging.getLogger(__name__)
    logger.debug("Validating tree structure configuration")

    if not cfg.tree_structure.enabled:
        return

    has_pageindex_api_key = False

    if isinstance(cfg.ocr, PageIndexOCRConfig):
        has_pageindex_api_key = bool(cfg.ocr.api_key and cfg.ocr.api_key.strip())

    if not has_pageindex_api_key:
        logger.debug(
            "Tree structure extraction enabled but PageIndex API "
            "credentials not found. Tree processing will be skipped "
            "during initialization."
        )

    logger.debug("Tree structure configuration validated successfully")


def validate_flags(cfg: AppConfig) -> None:
    """Validate flag configuration compatibility.

    Ensures that configuration flags are compatible and at least one operation
    is enabled. This validation is called after AppConfig construction but before
    client initialization to catch configuration errors early.

    Validation rules:
    1. Mutually exclusive flags: processing.dry_run and download.enabled cannot
       both be True. Dry-run mode is for testing configuration without actual
       operations, while download is an actual operation.
    2. At least one operation enabled: At least one of download.enabled,
       ocr.enabled, tag_adding.enabled, or selection_tagging.enabled must be
       True, except for standalone export modes (export.openkb_handoff.enabled
       for dry-run or live export; export.attachment_urls.enabled with
       processing.dry_run; or export.artifacts.enabled).

    Args:
        cfg: Application configuration object

    Raises:
        ConfigError: If flag combinations are invalid. Error messages include
            actionable guidance for fixing the configuration.
    """
    logger = logging.getLogger(__name__)
    logger.debug("Validating flag configuration")

    if (
        cfg.processing.dry_run
        and cfg.download.enabled
        and not cfg.selection_tagging.enabled
    ):
        raise ConfigError(
            "Invalid configuration: dry_run mode cannot be used with download feature. "
            "Set processing.dry_run=false or download.enabled=false."
        )

    if cfg.selection_tagging.enabled and cfg.tag_adding.enabled:
        raise ConfigError(
            "Invalid configuration: selection_tagging and tag_adding cannot both "
            "be enabled in the same run. Disable one of them."
        )

    standalone_export = (
        cfg.export.openkb_handoff.enabled
        or (cfg.processing.dry_run and cfg.export.attachment_urls.enabled)
        or cfg.export.artifacts.enabled
    )
    if (
        not cfg.download.enabled
        and not cfg.ocr.enabled
        and not cfg.tag_adding.enabled
        and not cfg.selection_tagging.enabled
        and not standalone_export
    ):
        raise ConfigError(
            "Invalid configuration: at least one operation must be enabled. "
            "Set download.enabled=true, ocr.enabled=true, tag_adding.enabled=true, "
            "or selection_tagging.enabled=true."
        )

    read_key = cfg.credentials.read_key
    if (
        not read_key
        or not str(read_key).strip()
        or read_key == PACKAGED_PLACEHOLDER_READ_KEY
    ):
        raise ConfigError(
            "ZOTERO_READ_KEY is required for Zotero read operations. "
            "Set it to a Zotero API key with read access."
        )

    write_reasons: list[str] = []
    if cfg.ocr.enabled and not cfg.processing.dry_run:
        write_reasons.append("OCR (live run creates Zotero notes)")
    if cfg.tag_adding.enabled and not cfg.processing.dry_run:
        write_reasons.append("tag_adding.enabled (live run writes tags to Zotero)")
    if (
        cfg.selection_tagging.enabled
        and (cfg.selection_tagging.add.values or cfg.selection_tagging.remove.values)
        and not cfg.processing.dry_run
    ):
        write_reasons.append(
            "selection_tagging.enabled with non-empty add/remove "
            "(live run writes tags to Zotero)"
        )
    can_reach_outcome_tag_mutations = not cfg.processing.dry_run and (
        cfg.ocr.enabled or cfg.download.enabled or cfg.tag_adding.enabled
    )
    if can_reach_outcome_tag_mutations:
        if cfg.tagging.apply_on_success.values:
            write_reasons.append(
                "tagging.apply_on_success is non-empty "
                "(live run writes post-processing success tags)"
            )
        if cfg.tagging.apply_on_error.values and cfg.zotero.error_tagging_enabled:
            write_reasons.append(
                "tagging.apply_on_error is non-empty and "
                "zotero.error_tagging_enabled "
                "(live run writes post-processing error tags)"
            )
        if cfg.tagging.remove_on_success.values:
            write_reasons.append(
                "tagging.remove_on_success is non-empty "
                "(live run removes post-processing success tags)"
            )
        if cfg.tagging.remove_on_error.values:
            write_reasons.append(
                "tagging.remove_on_error is non-empty "
                "(live run removes post-processing error tags)"
            )
    if write_reasons:
        wk = cfg.credentials.write_key
        if wk is None or (isinstance(wk, str) and not wk.strip()):
            details = ", ".join(write_reasons)
            raise ConfigError(
                "ZOTERO_WRITE_KEY is required for Zotero write operations: "
                f"{details}. Set it to a Zotero API key with write access."
            )

    if (
        not cfg.credentials.redact_logs
        and cfg.export.attachment_urls.auth_query.enabled
    ):
        raise ConfigError(
            "credentials.redact_logs must be true when "
            "export.attachment_urls.auth_query.enabled=true"
        )

    handoff = cfg.export.openkb_handoff
    if handoff.enabled:
        if not cfg.processing.dry_run and (
            handoff.jsonl_path is None or not str(handoff.jsonl_path).strip()
        ):
            raise ConfigError(
                "export.openkb_handoff.jsonl_path is required for live export "
                "when export.openkb_handoff.enabled=true"
            )
        if handoff.preview_jsonl_path is not None:
            if not str(handoff.preview_jsonl_path).strip():
                raise ConfigError(
                    "export.openkb_handoff.preview_jsonl_path cannot be empty"
                )
            if handoff.preview_jsonl_path == handoff.jsonl_path:
                raise ConfigError(
                    "export.openkb_handoff.preview_jsonl_path must differ from "
                    "export.openkb_handoff.jsonl_path"
                )

    artifacts = cfg.export.artifacts
    source_pack_fixture_paths = [
        artifacts.native_extraction_evidence_path,
        artifacts.ocr_extraction_evidence_path,
        artifacts.route_selection_evidence_path,
        artifacts.structure_evidence_path,
        artifacts.summary_evidence_path,
        artifacts.card_evidence_path,
        artifacts.index_evidence_path,
    ]
    if artifacts.source_pack_intake_evidence_path is not None:
        if not artifacts.enabled:
            raise ConfigError(
                "export.artifacts.source_pack_intake_evidence_path requires "
                "export.artifacts.enabled=true"
            )
        if artifacts.artifact_root != "source-pack":
            raise ConfigError(
                "export.artifacts.source_pack_intake_evidence_path requires "
                "export.artifacts.artifact_root=source-pack"
            )
        if not artifacts.source_pack_root:
            raise ConfigError(
                "export.artifacts.source_pack_intake_evidence_path requires "
                "an explicit export.artifacts.source_pack_root"
            )
        if not cfg.export.openkb_handoff.enabled:
            raise ConfigError(
                "export.artifacts.source_pack_intake_evidence_path requires "
                "export.openkb_handoff.enabled=true"
            )
    if any(path is not None for path in source_pack_fixture_paths):
        if not artifacts.enabled:
            raise ConfigError(
                "source-pack stage fixture evidence requires "
                "export.artifacts.enabled=true"
            )
        if artifacts.artifact_root != "source-pack":
            raise ConfigError(
                "source-pack stage fixture evidence requires "
                "export.artifacts.artifact_root=source-pack"
            )
        if not artifacts.source_pack_root:
            raise ConfigError(
                "source-pack stage fixture evidence requires an explicit "
                "export.artifacts.source_pack_root"
            )
    if artifacts.enabled:
        if not cfg.processing.dry_run:
            raise ConfigError(
                "export.artifacts.enabled=true is supported only with "
                "processing.dry_run=true in this artifact-writer slice."
            )
        if artifacts.artifact_root is None or not str(artifacts.artifact_root).strip():
            raise ConfigError(
                "export.artifacts.artifact_root must be set when "
                "export.artifacts.enabled=true. Use --artifact-root /path or "
                "export.artifacts.artifact_root=/path|source-pack."
            )

    logger.debug("Flag configuration validated successfully")


def _enforce_explicit_download_upload_folder(app_cfg: AppConfig) -> None:
    """Reject the packaged download-folder placeholder for live download runs.

    Dry-run preview does not write files, so placeholder paths are allowed when
    ``processing.dry_run`` is True (e.g. combined selection-tagging + download
    preview).
    """
    if (
        app_cfg.download.enabled
        and not app_cfg.processing.dry_run
        and app_cfg.download.upload_folder.strip()
        == PACKAGED_PLACEHOLDER_DOWNLOAD_FOLDER
    ):
        raise ConfigError(
            "download.upload_folder must be set to an explicit path when "
            f"download.enabled=true. The packaged default "
            f"{PACKAGED_PLACEHOLDER_DOWNLOAD_FOLDER!r} is not accepted. "
            "Override with: download.upload_folder=/your/path"
        )


def initialize_tree_processor(
    cfg: AppConfig,
    logger: logging.Logger,
) -> TreeStructureProcessor | None:
    """Initialize TreeStructureProcessor based on configuration.

    Initializes tree processor when tree structure extraction is enabled and
    PageIndex API credentials are available. Tree processing can work with any
    OCR provider (e.g., Mistral) as long as PageIndex credentials are provided
    for tree extraction.

    Credential lookup strategy:
    - Priority 1: If OCR provider is PageIndex, extract credentials
      from OCR config
    - Priority 2: If OCR provider is not PageIndex, read
      PAGEINDEX_API_KEY from environment
    - When credentials come from environment, use default base URL
      https://api.pageindex.ai

    Args:
        cfg: Application configuration object
        logger: Logger instance

    Returns:
        Optional TreeStructureProcessor instance. Returns None when tree
        structure extraction is disabled or tree credentials are missing.
    """
    if not cfg.tree_structure.enabled:
        logger.debug("Tree structure processing disabled")
        return None

    if cfg.tree_structure.provider != "pageindex":
        logger.debug(
            f"Tree structure provider '{cfg.tree_structure.provider}' not supported"
        )
        return None

    api_key = None
    base_url = None
    credential_source = None

    if isinstance(cfg.ocr, PageIndexOCRConfig):
        api_key = cfg.ocr.api_key
        base_url = cfg.ocr.base_url
        credential_source = "OCR config"
        logger.debug("Using PageIndex credentials from OCR config")
    else:
        api_key = os.getenv("PAGEINDEX_API_KEY")
        if api_key:
            base_url = "https://api.pageindex.ai"
            credential_source = "environment"
            logger.debug(
                "Using PageIndex credentials from PAGEINDEX_API_KEY "
                "environment variable"
            )

    if not api_key or not api_key.strip():
        if isinstance(cfg.ocr, MistralOCRConfig):
            logger.warning(
                "Tree structure extraction enabled with provider "
                "'pageindex' but PAGEINDEX_API_KEY environment variable "
                "not set. Set PAGEINDEX_API_KEY for tree processing "
                "with Mistral OCR."
            )
        else:
            logger.warning(
                "Tree structure extraction is enabled but PageIndex API "
                "key is not available. Tree extraction will be skipped. "
                "Provide PageIndex API credentials for tree processing."
            )
        return None

    if not base_url or not base_url.strip():
        base_url = "https://api.pageindex.ai"
        logger.debug(f"Using default PageIndex base URL: {base_url}")

    logger.info(
        f"Initializing tree structure processor "
        f"(credentials from {credential_source})..."
    )

    tree_client = PageIndexTreeClient(
        cfg.tree_structure,
        base_url,
        api_key,
    )
    tree_processor = TreeStructureProcessor(tree_client)

    logger.info("Tree structure processor initialized successfully")
    return tree_processor


# ---------------------------------------------------------------------------
# DictConfig → AppConfig conversion
# ---------------------------------------------------------------------------


def _defaults_entry_is_old_mistral_default(d: object) -> bool:
    """Detect legacy Hydra defaults entries that selected ``mistral: default``."""
    if "mistral: default" in str(d):
        return True
    if isinstance(d, Mapping) and "mistral" in d:
        v = d["mistral"]
        return v == "default" or str(v).strip() == "default"
    return False


def build_app_config(cfg: DictConfig) -> AppConfig:
    """Convert a Hydra DictConfig into a validated AppConfig.

    Handles OCR provider dispatch, nested config construction, and the
    ``TAG_ADDING_ASSIGNMENTS_JSON`` environment-variable override.

    Args:
        cfg: Raw Hydra DictConfig loaded from YAML + overrides.

    Returns:
        Fully constructed and validated ``AppConfig``.

    Raises:
        ConfigError: On migration issues or invalid configuration values.
    """
    logger = logging.getLogger(__name__)

    defaults = cfg.get("defaults", [])
    if defaults and any(_defaults_entry_is_old_mistral_default(d) for d in defaults):
        raise ConfigError(
            "Migration needed: Old 'mistral: default' config structure "
            "detected. Please update your config defaults to use "
            "'ocr: default' or 'ocr: mistral' instead. "
            "See SPECS_BACKWARD_COMPATIBILITY.md for migration details."
        )

    # OCR provider dispatch
    ocr_provider = cfg.ocr.provider
    if ocr_provider == "mistral":
        ocr_config = MistralOCRConfig(**cfg.ocr)
    elif ocr_provider == "pageindex":
        ocr_config = PageIndexOCRConfig(**cfg.ocr)
    else:
        allowed_providers = {"mistral", "pageindex"}
        raise ConfigError(
            f"Unsupported OCR provider {ocr_provider!r}. "
            f"Allowed providers: {sorted(allowed_providers)}. "
            "Dispatch only supports MistralOCRConfig and PageIndexOCRConfig; "
            "OCRProviderConfig is not a valid fallback for unknown providers."
        )

    tree_structure_config = TreeStructureConfig(**cfg.tree_structure)

    # Construct RetryConfig before DownloadConfig
    retry_config = RetryConfig(**cfg.download.retry)
    download_kw = {k: v for k, v in cfg.download.items() if k != "retry"}

    # TAG_ADDING_ASSIGNMENTS_JSON env-var override
    env_assignments = os.getenv("TAG_ADDING_ASSIGNMENTS_JSON")
    if env_assignments:
        redacted_assignments = "<REDACTED_PAYLOAD>"
        try:
            parsed_assignments = json.loads(env_assignments)
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse TAG_ADDING_ASSIGNMENTS_JSON=%r: %s; "
                "falling back to config tag_adding.",
                redacted_assignments,
                e,
            )
            tag_adding_config = TagAddingConfig(**cfg.tag_adding)
        else:
            if isinstance(parsed_assignments, dict):
                extra_fields = {
                    k: v
                    for k, v in cfg.tag_adding.items()
                    if k not in ("enabled", "assignments")
                }
                try:
                    tag_adding_config = TagAddingConfig(
                        enabled=True,
                        assignments=parsed_assignments,
                        **extra_fields,
                    )
                except (ConfigError, TypeError, ValueError) as e:
                    logger.error(
                        "Failed to build TagAddingConfig from "
                        "TAG_ADDING_ASSIGNMENTS_JSON=%r: %s; falling back to config "
                        "tag_adding.",
                        redacted_assignments,
                        e.__class__.__name__,
                    )
                    tag_adding_config = TagAddingConfig(**cfg.tag_adding)
            else:
                logger.error(
                    "TAG_ADDING_ASSIGNMENTS_JSON must decode to a dict, "
                    "got %s; falling back to config tag_adding.",
                    type(parsed_assignments).__name__,
                )
                tag_adding_config = TagAddingConfig(**cfg.tag_adding)
    else:
        tag_adding_config = TagAddingConfig(**cfg.tag_adding)

    # Construct TaggingConfig from nested DictConfig
    include_rule = TagRuleConfig(
        values=list(cfg.tagging.selection.include["values"]),
        operator=cfg.tagging.selection.include.operator,
    )
    exclude_rule = TagRuleConfig(
        values=list(cfg.tagging.selection.exclude["values"]),
        operator=cfg.tagging.selection.exclude.operator,
    )
    selection_cfg = TagSelectionConfig(
        include=include_rule,
        exclude=exclude_rule,
        conflict_resolution=cfg.tagging.selection.conflict_resolution,
    )
    success_target = TagTargetConfig(
        values=list(cfg.tagging.apply_on_success["values"]),
    )
    error_target = TagTargetConfig(
        values=list(cfg.tagging.apply_on_error["values"]),
    )
    remove_success_target = TagTargetConfig(
        values=list(cfg.tagging.remove_on_success["values"]),
    )
    remove_error_target = TagTargetConfig(
        values=list(cfg.tagging.remove_on_error["values"]),
    )
    tagging_config = TaggingConfig(
        selection=selection_cfg,
        apply_on_success=success_target,
        apply_on_error=error_target,
        remove_on_success=remove_success_target,
        remove_on_error=remove_error_target,
        include_abstract=cfg.tagging.include_abstract,
    )

    selection_tagging_config = SelectionTaggingConfig(
        enabled=cfg.selection_tagging.enabled,
        add=TagTargetConfig(values=list(cfg.selection_tagging.add["values"])),
        remove=TagTargetConfig(values=list(cfg.selection_tagging.remove["values"])),
    )

    att_urls = OmegaConf.to_container(cfg.export.attachment_urls, resolve=True)
    if not isinstance(att_urls, dict):
        raise ConfigError("export.attachment_urls must resolve to a mapping")
    auth_query_raw = att_urls.pop("auth_query", {})
    if not isinstance(auth_query_raw, dict):
        raise ConfigError("export.attachment_urls.auth_query must be a mapping")
    attachment_urls_export = AttachmentUrlExportConfig(
        **att_urls, auth_query=AuthQueryHelperConfig(**auth_query_raw)
    )

    openkb_handoff_raw = OmegaConf.to_container(cfg.export.openkb_handoff, resolve=True)
    if not isinstance(openkb_handoff_raw, dict):
        raise ConfigError("export.openkb_handoff must resolve to a mapping")
    openkb_handoff_export = OpenKBHandoffExportConfig(**openkb_handoff_raw)

    artifacts_raw = OmegaConf.to_container(cfg.export.artifacts, resolve=True)
    if not isinstance(artifacts_raw, dict):
        raise ConfigError("export.artifacts must resolve to a mapping")
    artifact_export = ArtifactExportConfig(**artifacts_raw)

    export_config = ExportConfig(
        attachment_urls=attachment_urls_export,
        openkb_handoff=openkb_handoff_export,
        artifacts=artifact_export,
    )

    if not OmegaConf.is_missing(cfg, "credentials"):
        credentials_cfg = cfg.credentials
        if not OmegaConf.is_missing(credentials_cfg, "library_id"):
            raw_library_id = credentials_cfg.library_id
            if (
                raw_library_id != PACKAGED_PLACEHOLDER_LIBRARY_ID
                and raw_library_id is not None
            ):
                raise ConfigError(
                    "credentials.library_id must not be set via Hydra override. "
                    "Set ZOTERO_LIBRARY_ID as an environment variable instead."
                )
        if not OmegaConf.is_missing(credentials_cfg, "read_key"):
            raw_read_key = credentials_cfg.read_key
            if (
                raw_read_key != PACKAGED_PLACEHOLDER_READ_KEY
                and raw_read_key is not None
            ):
                raise ConfigError(
                    "credentials.read_key must not be set via Hydra override. "
                    "Set ZOTERO_READ_KEY as an environment variable instead."
                )
        if not OmegaConf.is_missing(credentials_cfg, "write_key"):
            raw_write_key = credentials_cfg.write_key
            if raw_write_key is not None:
                raise ConfigError(
                    "credentials.write_key must not be set via Hydra override. "
                    "Set ZOTERO_WRITE_KEY as an environment variable instead."
                )

    library_id = os.getenv("ZOTERO_LIBRARY_ID") or PACKAGED_PLACEHOLDER_LIBRARY_ID
    read_key_env = os.getenv("ZOTERO_READ_KEY") or PACKAGED_PLACEHOLDER_READ_KEY
    write_key_env = os.getenv("ZOTERO_WRITE_KEY")
    if OmegaConf.is_missing(cfg, "credentials"):
        redact_logs = True
    else:
        redact_logs = bool(cfg.credentials.redact_logs)
    auth_query_config = AuthQueryConfig(
        library_id=library_id,
        read_key=read_key_env,
        write_key=write_key_env,
        redact_logs=redact_logs,
    )

    return AppConfig(
        zotero=ZoteroConfig(**cfg.zotero),
        ocr=ocr_config,
        processing=ProcessingConfig(**cfg.processing),
        storage=StorageConfig(**cfg.storage),
        credentials=auth_query_config,
        tree_structure=tree_structure_config,
        download=DownloadConfig(retry=retry_config, **download_kw),
        tag_adding=tag_adding_config,
        tagging=tagging_config,
        selection_tagging=selection_tagging_config,
        export=export_config,
    )


# ---------------------------------------------------------------------------
# Help text shown when all operations are disabled
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
Millefeuille
============================

Process Zotero library items with OCR, download PDFs, and manage tags.

Entry points:
  millefeuille          (installed console script)
  python -m millefeuille

Read-only artifact commands:
  millefeuille artifacts --index /path/to/artifact-index.json
  millefeuille status --index /path/to/artifact-index.json --strict

Offline source-pack commands:
  millefeuille source-pack intake --evidence recovered-pdf-evidence.json
    --source-pack-root /path/to/source-packs

Offline taxonomy governance commands:
  millefeuille taxonomy validate --registry /path/to/taxonomy-registry.json
  millefeuille taxonomy lock --registry /path/to/taxonomy-registry.json ...

Offline lifecycle-tag migration commands:
  millefeuille lifecycle-tags registry
  millefeuille lifecycle-tags validate --registry /path/to/lifecycle-registry.json
  millefeuille lifecycle-tags plan --registry /path/to/lifecycle-registry.json ...

Artifact-writer source-pack fixture flags:
  --native-extraction-evidence /path/to/native-extraction-evidence.jsonl
  --ocr-extraction-evidence /path/to/ocr-extraction-evidence.jsonl
  --route-selection-evidence /path/to/route-selection-evidence.jsonl
  --structure-evidence /path/to/structure-evidence.jsonl
  --summary-evidence /path/to/summary-evidence.jsonl
  --card-evidence /path/to/card-evidence.jsonl
  --index-evidence /path/to/index-evidence.jsonl

Required environment variables:
  ZOTERO_LIBRARY_ID   Your Zotero user-library numeric ID
  ZOTERO_READ_KEY     Zotero API key with read access — required for all runs
                      (https://www.zotero.org/settings/keys)
  ZOTERO_WRITE_KEY    Zotero API key with write access — required only when write
                      features are enabled (OCR note creation, tag_adding,
                      post-processing tag writes)
  OCR provider key    MISTRAL_API_KEY or PAGEINDEX_API_KEY — required only when
                      OCR is enabled (not needed for download-only, tag-only, or
                      export-only dry-run)

Key override examples:
  ocr.enabled=true
  download.enabled=true download.upload_folder=/path/to/downloads
  tag_adding.enabled=true
  processing.dry_run=true ocr.enabled=true

Note: path-consuming modes (download, save-to-disk) require explicit path
overrides; the packaged placeholder defaults are not accepted.
"""


# ---------------------------------------------------------------------------
# Hydra entry point
# ---------------------------------------------------------------------------


def entrypoint() -> None:
    """Dispatch read-only subcommands before falling back to Hydra."""
    argv = sys.argv[1:]
    if argv and argv[0] in {"artifacts", "status"}:
        sys.exit(run_artifact_cli(argv))
    if argv and argv[0] == "source-pack":
        sys.exit(run_source_pack_cli(argv))
    if argv and argv[0] == "taxonomy":
        sys.exit(run_taxonomy_cli(argv[1:]))
    if argv and argv[0] == "lifecycle-tags":
        sys.exit(run_lifecycle_tags_cli(argv[1:]))
    if argv and argv[0] in {
        "extract-native",
        "extract-ocr",
        "route",
        "structure",
        "summarize",
        "card",
        "index",
        "acceptance",
        "classify",
        "writeback",
        "retrieve",
        "models",
        "run",
    }:
        sys.exit(run_stage_cli(argv))
    translated_argv = _translate_artifact_writer_args(argv)
    if translated_argv != argv:
        sys.argv = [sys.argv[0], *translated_argv]
    main()


def _translate_artifact_writer_args(argv: list[str]) -> list[str]:
    """Translate lightweight artifact flags into Hydra overrides."""
    translated: list[str] = []
    artifact_enable_seen = False
    idx = 0
    while idx < len(argv):
        arg = argv[idx]
        if arg == "--artifact-root":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append(f"export.artifacts.artifact_root={argv[idx + 1]}")
            artifact_enable_seen = True
            idx += 2
            continue
        if arg.startswith("--artifact-root="):
            translated.append("export.artifacts.artifact_root=" + arg.split("=", 1)[1])
            artifact_enable_seen = True
            idx += 1
            continue
        if arg == "--source-pack-intake-evidence":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append(
                "export.artifacts.source_pack_intake_evidence_path=" + argv[idx + 1]
            )
            artifact_enable_seen = True
            idx += 2
            continue
        if arg.startswith("--source-pack-intake-evidence="):
            translated.append(
                "export.artifacts.source_pack_intake_evidence_path="
                + arg.split("=", 1)[1]
            )
            artifact_enable_seen = True
            idx += 1
            continue
        if arg == "--source-pack-root":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append(f"export.artifacts.source_pack_root={argv[idx + 1]}")
            idx += 2
            continue
        if arg.startswith("--source-pack-root="):
            translated.append(
                "export.artifacts.source_pack_root=" + arg.split("=", 1)[1]
            )
            idx += 1
            continue
        if arg == "--native-extraction-evidence":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append(
                "export.artifacts.native_extraction_evidence_path=" + argv[idx + 1]
            )
            artifact_enable_seen = True
            idx += 2
            continue
        if arg.startswith("--native-extraction-evidence="):
            translated.append(
                "export.artifacts.native_extraction_evidence_path="
                + arg.split("=", 1)[1]
            )
            artifact_enable_seen = True
            idx += 1
            continue
        if arg == "--ocr-extraction-evidence":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append(
                "export.artifacts.ocr_extraction_evidence_path=" + argv[idx + 1]
            )
            artifact_enable_seen = True
            idx += 2
            continue
        if arg.startswith("--ocr-extraction-evidence="):
            translated.append(
                "export.artifacts.ocr_extraction_evidence_path=" + arg.split("=", 1)[1]
            )
            artifact_enable_seen = True
            idx += 1
            continue
        if arg == "--route-selection-evidence":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append(
                "export.artifacts.route_selection_evidence_path=" + argv[idx + 1]
            )
            artifact_enable_seen = True
            idx += 2
            continue
        if arg.startswith("--route-selection-evidence="):
            translated.append(
                "export.artifacts.route_selection_evidence_path=" + arg.split("=", 1)[1]
            )
            artifact_enable_seen = True
            idx += 1
            continue
        if arg == "--structure-evidence":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append(
                "export.artifacts.structure_evidence_path=" + argv[idx + 1]
            )
            artifact_enable_seen = True
            idx += 2
            continue
        if arg.startswith("--structure-evidence="):
            translated.append(
                "export.artifacts.structure_evidence_path=" + arg.split("=", 1)[1]
            )
            artifact_enable_seen = True
            idx += 1
            continue
        if arg == "--summary-evidence":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append("export.artifacts.summary_evidence_path=" + argv[idx + 1])
            artifact_enable_seen = True
            idx += 2
            continue
        if arg.startswith("--summary-evidence="):
            translated.append(
                "export.artifacts.summary_evidence_path=" + arg.split("=", 1)[1]
            )
            artifact_enable_seen = True
            idx += 1
            continue
        if arg == "--card-evidence":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append("export.artifacts.card_evidence_path=" + argv[idx + 1])
            artifact_enable_seen = True
            idx += 2
            continue
        if arg.startswith("--card-evidence="):
            translated.append(
                "export.artifacts.card_evidence_path=" + arg.split("=", 1)[1]
            )
            artifact_enable_seen = True
            idx += 1
            continue
        if arg == "--index-evidence":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append("export.artifacts.index_evidence_path=" + argv[idx + 1])
            artifact_enable_seen = True
            idx += 2
            continue
        if arg.startswith("--index-evidence="):
            translated.append(
                "export.artifacts.index_evidence_path=" + arg.split("=", 1)[1]
            )
            artifact_enable_seen = True
            idx += 1
            continue
        if arg == "--run-id":
            if idx + 1 >= len(argv):
                translated.append(arg)
                idx += 1
                continue
            translated.append(f"export.artifacts.run_id={argv[idx + 1]}")
            idx += 2
            continue
        if arg.startswith("--run-id="):
            translated.append("export.artifacts.run_id=" + arg.split("=", 1)[1])
            idx += 1
            continue
        translated.append(arg)
        idx += 1
    if artifact_enable_seen and "export.artifacts.enabled=true" not in translated:
        translated.append("export.artifacts.enabled=true")
    return translated


@hydra.main(
    config_path="../conf",
    config_name="config",
    version_base=None,
)
def main(cfg: DictConfig) -> None:
    """Main entry point for the pipeline.

    Args:
        cfg: Hydra configuration object

    Note:
        Hydra's ``@hydra.main`` does not propagate return values to the process
        exit status, so this function finishes by calling :func:`sys.exit` with
        the intended code (0 for success; 1 or 2 from commands; 3 for
        configuration or fatal errors).
    """
    logger = logging.getLogger(__name__)

    exit_code = 3
    try:
        app_cfg = build_app_config(cfg)

        # --- No-op help branch (replaces the ConfigError for all-disabled) ---
        all_ops_disabled = (
            not app_cfg.download.enabled
            and not app_cfg.ocr.enabled
            and not app_cfg.tag_adding.enabled
            and not app_cfg.selection_tagging.enabled
        )
        standalone_export = (
            app_cfg.export.openkb_handoff.enabled
            or (app_cfg.processing.dry_run and app_cfg.export.attachment_urls.enabled)
            or app_cfg.export.artifacts.enabled
        )
        if all_ops_disabled and not standalone_export:
            print(_HELP_TEXT)
            exit_code = 0
        else:
            # --- Fail-fast path enforcement for download mode ---
            _enforce_explicit_download_upload_folder(app_cfg)

            # --- Fail-fast path enforcement for save-to-disk mode ---
            if (
                app_cfg.processing.save_to_disk
                and app_cfg.storage.base_dir.strip()
                == PACKAGED_PLACEHOLDER_STORAGE_BASE_DIR
            ):
                raise ConfigError(
                    "storage.base_dir must be set to an explicit path when "
                    "processing.save_to_disk=true. The packaged default "
                    f"{PACKAGED_PLACEHOLDER_STORAGE_BASE_DIR!r} is not accepted. "
                    "Override with: "
                    "storage.base_dir=/your/path"
                )

            # Validate flag compatibility (dry_run vs download, etc.)
            validate_flags(app_cfg)

            logger = setup_logging(redact_logs=app_cfg.credentials.redact_logs)

            # Route to appropriate command
            if app_cfg.processing.dry_run:
                logger.info("Initializing Zotero client...")
                zotero_client = ZoteroClient(app_cfg.credentials)
                logger.info("Zotero client initialized successfully")
                exit_code = dry_run_command(app_cfg, logger, zotero_client)
            else:
                zotero_client, ocr_client = initialize_clients(app_cfg, logger)

                tree_processor: TreeStructureProcessor | None = None
                if app_cfg.ocr.enabled:
                    validate_tree_config(app_cfg)
                    tree_processor = initialize_tree_processor(app_cfg, logger)

                if app_cfg.ocr.enabled:
                    if tree_processor is not None:
                        logger.info("Tree structure processing enabled")
                    elif app_cfg.tree_structure.enabled:
                        logger.warning(
                            "Tree structure processing requested but could not "
                            "be initialized"
                        )
                    else:
                        logger.debug("Tree structure processing disabled")
                else:
                    logger.debug(
                        "Tree structure processing skipped (OCR disabled; "
                        "matches download-only / tag-only pipeline paths)"
                    )

                exit_code = process_command(
                    app_cfg, logger, zotero_client, ocr_client, tree_processor
                )

    except OpenKBHandoffValidationError as e:
        logger.error(f"OpenKB handoff validation failed: {redact_message(str(e))}")
        exit_code = 2
    except ConfigError as e:
        logger.error(f"Configuration error: {redact_message(str(e))}")
        exit_code = 3
    except (ZoteroClientError, OCRClientError) as e:
        logger.error(f"Fatal error: {redact_message(str(e))}", exc_info=True)
        exit_code = 3
    except Exception as e:
        logger.error(f"Fatal error: {redact_message(str(e))}", exc_info=True)
        exit_code = 3

    sys.exit(exit_code)


if __name__ == "__main__":
    entrypoint()
