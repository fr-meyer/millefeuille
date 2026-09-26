"""No-write byte-exact GPT card bundle bound to its output approval."""

from dataclasses import dataclass, field
import hashlib
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.paper_card_output_plan import (
    PlannedGptCardFile,
    plan_gpt_paper_card_outputs,
)
from millefeuille.domain.paper_card_output_write_scope import (
    GptCardOutputWritePreview,
    validate_gpt_card_output_write_preview,
)
from millefeuille.domain.summary_publication_bundle import _canonical_json


@dataclass(frozen=True)
class GptCardPublicationBundle:
    preview: GptCardOutputWritePreview
    bundle_manifest_sha256: str
    total_bytes: int
    files: tuple[PlannedGptCardFile, ...] = field(repr=False)
    bundle_manifest_json: bytes = field(repr=False)


def plan_gpt_card_publication_bundle(**scope: Any) -> GptCardPublicationBundle:
    """Replan all five files and bind their exact census to packet and receipt."""
    preview = validate_gpt_card_output_write_preview(**scope)
    inputs = {
        key: value
        for key, value in scope.items()
        if key not in {"packet", "receipt", "now"}
    }
    output = plan_gpt_paper_card_outputs(**inputs)
    if (
        output.paper_id != preview.paper_id
        or output.run_id != preview.run_id
        or output.write_manifest_sha256 != preview.write_manifest_sha256
        or output.request_plan_sha256 != preview.request_plan_sha256
        or output.summary_publication_sha256 != preview.summary_publication_sha256
        or output.provenance_sha256 != preview.provenance_sha256
        or output.total_bytes != preview.total_bytes
        or tuple(item.ref for item in output.files) != preview.file_refs
    ):
        raise MillefeuilleContractError("GPT card publication bundle approval drift")
    for item in output.files:
        if item.sha256 != "sha256:" + hashlib.sha256(item.data).hexdigest():
            raise MillefeuilleContractError("GPT card publication bundle byte drift")
    manifest = {
        "schema_version": "millefeuille-gpt-card-publication-bundle/v0.1",
        "paper_id": preview.paper_id,
        "run_id": preview.run_id,
        "packet_digest": preview.packet_digest,
        "receipt_digest": preview.receipt_digest,
        "source_manifest_sha256": preview.source_manifest_sha256,
        "request_plan_sha256": preview.request_plan_sha256,
        "summary_publication_sha256": preview.summary_publication_sha256,
        "write_manifest_sha256": preview.write_manifest_sha256,
        "provenance_sha256": preview.provenance_sha256,
        "entries": [
            {"ref": item.ref, "sha256": item.sha256, "bytes": len(item.data)}
            for item in output.files
        ],
    }
    encoded = _canonical_json(manifest)
    return GptCardPublicationBundle(
        preview,
        "sha256:" + hashlib.sha256(encoded).hexdigest(),
        output.total_bytes,
        output.files,
        encoded,
    )
