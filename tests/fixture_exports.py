"""Exact exports of existing test pipelines; never production evidence.

Only portable no-auth planning and imported-page retrieval may consume these
bytes. Do not pass exports to root custody, publication or journal execution.
"""
import hashlib
import json
from pathlib import Path


def load_export(name):
    root = Path(__file__).parent / "fixtures" / "existing-pipeline-exports"
    manifest = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    if name not in manifest["files"]:
        raise AssertionError("unregistered existing fixture export")
    raw = (root / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["files"][name]:
        raise AssertionError("existing fixture export digest mismatch")
    return raw
