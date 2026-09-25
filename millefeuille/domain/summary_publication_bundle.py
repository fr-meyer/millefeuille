"""No-write, byte-exact bundle for a separately approved GPT summary write.

The trusted writer can consume this plan only after revalidating the source,
administrator approval, and one-use receipt in its own privileged boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from millefeuille.domain.live_receipts import ApprovedLiveReceipt
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import OperatorPreflightPacket
from millefeuille.domain.summary_live_execution import TrustedGptSummaryOutcome
from millefeuille.domain.summary_model_provenance_plan import (
    plan_gpt_summary_model_provenance,
)
from millefeuille.domain.summary_observed_usage_plan import (
    plan_gpt_summary_observed_usage,
)
from millefeuille.domain.summary_output_plan import plan_gpt_summary_outputs
from millefeuille.domain.summary_output_write_scope import (
    SummaryOutputWritePreview,
    validate_gpt_summary_output_write_preview,
)


@dataclass(frozen=True)
class PlannedPublicationFile:
    ref: str
    sha256: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class SummaryPublicationBundle:
    preview: SummaryOutputWritePreview
    bundle_manifest_sha256: str
    total_bytes: int
    files: tuple[PlannedPublicationFile, ...] = field(repr=False)
    bundle_manifest_json: bytes = field(repr=False)


def plan_gpt_summary_publication_bundle(
    *,
    outcome: TrustedGptSummaryOutcome,
    run_id: str,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    source_pack_root: str | Path,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
) -> SummaryPublicationBundle:
    """Enumerate exact prospective bytes after the no-effect write preview."""

    evidence = {
        "route_evidence_path": route_evidence_path,
        "structure_evidence_path": structure_evidence_path,
        "preparation_path": preparation_path,
    }
    preview = validate_gpt_summary_output_write_preview(
        outcome=outcome,
        run_id=run_id,
        source_pack_root=source_pack_root,
        packet=packet,
        receipt=receipt,
        **evidence,
    )
    output = plan_gpt_summary_outputs(outcome=outcome, run_id=run_id, **evidence)
    observed = plan_gpt_summary_observed_usage(
        outcome=outcome, run_id=run_id, **evidence
    )
    provenance = plan_gpt_summary_model_provenance(
        outcome=outcome, run_id=run_id, **evidence
    )
    if (
        output.write_manifest_sha256 != preview.write_manifest_sha256
        or observed.observed_usage_sha256 != preview.observed_usage_sha256
        or provenance.provenance_manifest_sha256 != preview.provenance_manifest_sha256
        or output.paper_id != preview.paper_id
        or output.run_id != preview.run_id
    ):
        raise MillefeuilleContractError("GPT publication bundle approval drift")

    write_manifest = json.loads(output.write_manifest_json)
    expected: list[tuple[str, str, bytes]] = [
        (
            output.write_manifest_ref,
            output.write_manifest_sha256,
            output.write_manifest_json,
        ),
        (
            observed.observed_usage_ref,
            observed.observed_usage_sha256,
            observed.observed_usage_json,
        ),
        (
            provenance.provenance_manifest_ref,
            provenance.provenance_manifest_sha256,
            provenance.provenance_manifest_json,
        ),
        (
            output.summary_record_ref,
            write_manifest["summary_record_sha256"],
            output.summary_record_json,
        ),
        *((item.ref, item.sha256, item.data) for item in output.texts),
        *((item.ref, item.sha256, item.data) for item in output.source_inputs),
        *((item.ref, item.sha256, item.data) for item in provenance.records),
    ]
    refs = [ref for ref, _, _ in expected]
    if len(refs) != len(set(refs)) or tuple(sorted(refs)) != preview.file_refs:
        raise MillefeuilleContractError("GPT publication bundle file scope drift")
    files: list[PlannedPublicationFile] = []
    for ref, declared_digest, data in sorted(expected):
        actual_digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if actual_digest != declared_digest:
            raise MillefeuilleContractError("GPT publication bundle byte drift")
        files.append(PlannedPublicationFile(ref, actual_digest, data))
    manifest = {
        "schema_version": "millefeuille-gpt-summary-publication-bundle/v0.1",
        "paper_id": output.paper_id,
        "run_id": run_id,
        "packet_digest": preview.packet_digest,
        "receipt_digest": preview.receipt_digest,
        "source_manifest_sha256": preview.source_manifest_sha256,
        "write_manifest_sha256": preview.write_manifest_sha256,
        "observed_usage_sha256": preview.observed_usage_sha256,
        "provenance_manifest_sha256": preview.provenance_manifest_sha256,
        "entries": [
            {"ref": item.ref, "sha256": item.sha256, "bytes": len(item.data)}
            for item in files
        ],
    }
    encoded = _canonical_json(manifest)
    return SummaryPublicationBundle(
        preview=preview,
        bundle_manifest_sha256="sha256:" + hashlib.sha256(encoded).hexdigest(),
        total_bytes=sum(len(item.data) for item in files),
        files=tuple(files),
        bundle_manifest_json=encoded,
    )


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
