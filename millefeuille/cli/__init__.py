"""Command-line interface components for the Millefeuille.

This package provides command implementations that handle the dry-run and
processing workflows. Commands are called from the main entry point after
configuration validation and client initialization.
"""

from .commands import dry_run_command, process_command

__all__ = ["dry_run_command", "process_command"]
