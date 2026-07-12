"""
Millefeuille.

Run the source-pack, extraction, OpenKB handoff, and Zotero lifecycle for
research-paper attachments.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("millefeuille")
except PackageNotFoundError:
    __version__ = "unknown"
