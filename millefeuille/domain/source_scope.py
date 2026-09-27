"""Immutable whole-pack evidence for downstream multi-PDF materialization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.source_packs import (
    SOURCE_PACK_MULTI_MANIFEST_SCHEMA_VERSION,
    aggregate_source_hash,
    load_source_pack_manifest,
    paper_id_for_zotero_item_key,
)

SOURCE_SCOPE_SCHEMA_VERSION = "millefeuille-source-scope/v0.1"


def _canonical_string(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise MillefeuilleContractError(f"{label} must be a canonical non-empty string")
    return value


@dataclass(frozen=True)
class SourceScopeMember:
    attachment_key: str
    canonical_filename: str
    sha256: str
    source_ref: str
    zotero_version: int | None = None

    def __post_init__(self) -> None:
        _canonical_string(self.attachment_key, "attachment_key")
        filename = _canonical_string(self.canonical_filename, "canonical_filename")
        if filename in (".", "..") or "/" in filename or "\\" in filename:
            raise MillefeuilleContractError("canonical_filename must be a basename")
        if not isinstance(self.sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.sha256
        ):
            raise MillefeuilleContractError(
                "source member sha256 must be 64 lowercase hex"
            )
        if not isinstance(self.source_ref, str) or not re.fullmatch(
            r"sources/[A-Za-z0-9._-]+\.pdf", self.source_ref
        ):
            raise MillefeuilleContractError(
                "source_ref must be a safe sources/*.pdf ref"
            )
        if self.zotero_version is not None and (
            type(self.zotero_version) is not int or self.zotero_version < 0
        ):
            raise MillefeuilleContractError(
                "zotero_version must be a non-negative integer"
            )

    @classmethod
    def from_dict(cls, payload: Any) -> SourceScopeMember:
        required = {"attachment_key", "canonical_filename", "sha256", "source_ref"}
        if (
            not isinstance(payload, dict)
            or not required <= payload.keys()
            or (payload.keys() - required - {"zotero_version"})
        ):
            raise MillefeuilleContractError(
                "source member fields do not match the contract"
            )
        return cls(**payload)


@dataclass(frozen=True)
class MultiSourceScope:
    source_hash: str
    sources: tuple[SourceScopeMember, ...]
    schema_version: str = SOURCE_SCOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SOURCE_SCOPE_SCHEMA_VERSION:
            raise MillefeuilleContractError("unsupported source_scope schema_version")
        if (
            not isinstance(self.sources, tuple)
            or len(self.sources) < 2
            or not all(isinstance(member, SourceScopeMember) for member in self.sources)
        ):
            raise MillefeuilleContractError(
                "source_scope requires at least two typed members"
            )
        for field in ("attachment_key", "source_ref"):
            if len({getattr(member, field) for member in self.sources}) != len(
                self.sources
            ):
                raise MillefeuilleContractError(f"duplicate source_scope {field}")
        if self.source_hash != aggregate_source_hash([s.sha256 for s in self.sources]):
            raise MillefeuilleContractError("source_scope aggregate source_hash drift")
        object.__setattr__(
            self, "sources", tuple(sorted(self.sources, key=lambda s: s.attachment_key))
        )

    @classmethod
    def from_dict(cls, payload: Any) -> MultiSourceScope:
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "source_hash", "sources"}
            or not isinstance(payload.get("sources"), list)
        ):
            raise MillefeuilleContractError(
                "source_scope fields do not match the contract"
            )
        return cls(
            schema_version=payload["schema_version"],
            source_hash=payload["source_hash"],
            sources=tuple(SourceScopeMember.from_dict(s) for s in payload["sources"]),
        )


def resolve_multi_source_pack(
    *,
    source_pack_root: Path,
    item_key: str,
    paper_id: str | None,
    scope: MultiSourceScope,
) -> tuple[Path, str]:
    _canonical_string(item_key, "item_key")
    resolved_id = paper_id or paper_id_for_zotero_item_key(item_key)
    if (
        not isinstance(resolved_id, str)
        or resolved_id in (".", "..")
        or not re.fullmatch(r"[A-Za-z0-9._-]+", resolved_id)
    ):
        raise MillefeuilleContractError("paper_id must be a safe path component")
    pack = Path(source_pack_root) / "zotero" / resolved_id
    verify_multi_source_pack(pack, item_key=item_key, paper_id=resolved_id, scope=scope)
    return pack, scope.source_hash


def verify_multi_source_pack(
    pack: Path, *, item_key: str, paper_id: str, scope: MultiSourceScope
) -> None:
    manifest = load_source_pack_manifest(pack / "manifest.json")
    if manifest.get("schema_version") != SOURCE_PACK_MULTI_MANIFEST_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "whole-pack evidence requires a v0.2 source pack"
        )
    if (
        manifest.get("paper_id") != paper_id
        or manifest["identity"].get("zotero_item_key") != item_key
    ):
        raise MillefeuilleContractError(
            "multi-source source-pack paper/item identity drift"
        )
    if type(manifest["identity"].get("pdf_count")) is not int:
        raise MillefeuilleContractError("multi-source pdf_count must be an integer")
    if manifest["source_hash"] != scope.source_hash:
        raise MillefeuilleContractError(
            "multi-source source-pack aggregate source_hash drift"
        )
    members = tuple(
        sorted(
            (
                SourceScopeMember(
                    attachment_key=s["identity"]["zotero_attachment_key"],
                    canonical_filename=s["identity"]["canonical_filename"],
                    sha256=s["sha256"],
                    source_ref=s["ref"],
                    zotero_version=s["identity"].get("zotero_version"),
                )
                for s in manifest["sources"]
            ),
            key=lambda s: s.attachment_key,
        )
    )
    if members != scope.sources:
        raise MillefeuilleContractError("multi-source source-pack member set drift")
    for source in manifest["sources"]:
        if (
            source.get("kind") != "recovered-pdf"
            or source.get("format") != "pdf"
            or source["verification"].get("status") != "verified"
            or source["verification"].get("method") != "sha256"
        ):
            raise MillefeuilleContractError(
                "multi-source member must be a verified recovered PDF"
            )
        size = source.get("byte_size")
        if type(size) is not int or size < 0:
            raise MillefeuilleContractError(
                "multi-source PDF byte_size must be non-negative"
            )
        payload = read_bytes_no_follow(
            pack / source["ref"], "multi-source PDF", max_bytes=size
        )
        if len(payload) != size:
            raise MillefeuilleContractError("multi-source PDF byte_size drift")
        if hashlib.sha256(payload).hexdigest() != source["sha256"]:
            raise MillefeuilleContractError("multi-source PDF content hash drift")


def validate_whole_pack_evidence_fields(
    payload: dict[str, Any], paths: set[str]
) -> None:
    required = {"schema_version", "item_key", "source_scope"} | paths
    allowed = required | {"source_type", "paper_id"}
    if not required <= payload.keys() or payload.keys() - allowed:
        raise MillefeuilleContractError(
            "v0.2 whole-pack evidence fields do not match the contract"
        )
