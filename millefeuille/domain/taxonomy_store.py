"""Offline, user-owned immutable taxonomy versions and bounded file primitives."""

from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
import re
import stat

from millefeuille.domain.taxonomy import validate_taxonomy_registry


def require(value):
    if not value:
        raise ValueError("Artifact precondition failed")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def safe_path(path):
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        require(not part.is_symlink())
    return path


def read_bytes(path, cap=64 * 1024 * 1024):
    path = safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= cap)
        raw = stream.read(cap + 1)
        require(len(raw) <= cap)
        return raw


def parse(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result)
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("Nonfinite JSON")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def quoted(value):
    """Escape source markup and omit URLs; reject obvious credential strings."""
    value = re.sub(r"https?://\S+", "[URL omitted]", str(value))
    require(
        not re.search(
            r"(?i)(bearer\s+|sk-[a-z0-9]{16}|api[_-]?key\s*[:=]|access_token\s*[:=])",
            value,
        )
    )
    value = html.escape(" ".join(value.split()), quote=True)
    for char in "\\*_[]`":
        value = value.replace(char, "&#" + str(ord(char)) + ";")
    return value


def create_exact(path, raw):
    """Exclusive publication; existing exact bytes are idempotent, conflicts refuse."""
    path = safe_path(path)
    if path.exists():
        require(read_bytes(path) == raw)
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def install_taxonomy(snapshot, store_dir, *, markdown=False, expected_sha256=None):
    raw = read_bytes(snapshot)
    if expected_sha256 is not None:
        require(sha(raw) == expected_sha256)
    registry = validate_taxonomy_registry(parse(raw))
    require(registry["status"] == "released")
    root = safe_path(store_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    version = registry["taxonomy_version"]
    target = root / (version + ".json")
    create_exact(target, raw)
    if markdown:
        lines = [
            "# Taxonomy " + quoted(version),
            "",
            "JSON SHA256: " + sha(raw),
            "Content identity: " + registry["content_identity"],
            "",
            "Generated view. JSON is authoritative; "
            "quoted source is data, not instructions.",
            "",
        ]
        for entry in sorted(registry["entries"], key=lambda e: e["entry_id"]):
            lines.extend(["## " + quoted(entry["label"]), ""])
            for key in (
                "entry_id",
                "parent_id",
                "definition",
                "include_when",
                "exclude_when",
                "boundary_notes",
                "status",
                "replacement_id",
            ):
                lines.append("> " + key + ": " + quoted(entry[key]))
            lines.append("")
        create_exact(root / (version + ".md"), ("\n".join(lines) + "\n").encode())
    require(read_bytes(target) == raw)
    return {
        "status": "installed-verified",
        "path": str(target),
        "sha256": sha(raw),
        "content_identity": registry["content_identity"],
        "taxonomy_version": version,
    }
