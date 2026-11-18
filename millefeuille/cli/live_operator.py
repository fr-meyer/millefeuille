"""Explicit unprivileged live entrypoint; existing domain gates own authority."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import json
import os
import select
import signal
import sys
import time

from millefeuille.domain.live_receipts import load_approved_live_receipt
from millefeuille.domain.operator_preflight import (
    compute_operator_root_target_id,
    load_operator_preflight_packet,
)
from millefeuille.domain.paper_card_execution_scope import (
    validate_gpt_card_approval_preview,
)
from millefeuille.domain.paper_card_inputs import plan_published_gpt_paper_card_request
from millefeuille.domain.paper_card_live_execution import run_trusted_gpt_paper_card
from millefeuille.domain.paper_card_output_plan import plan_gpt_paper_card_outputs
from millefeuille.domain.paper_card_publication_socket import (
    request_gpt_card_publication,
)
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.stage_runtime import require_safe_package_id
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_execution_scope import (
    validate_gpt_summary_approval_preview,
)
from millefeuille.domain.summary_live_execution import run_trusted_gpt_summary_batch
from millefeuille.domain.summary_model_provenance_plan import (
    plan_gpt_summary_model_provenance,
)
from millefeuille.domain.summary_observed_usage_plan import (
    plan_gpt_summary_observed_usage,
)
from millefeuille.domain.summary_output_plan import plan_gpt_summary_outputs
from millefeuille.domain.summary_publication_bundle import (
    plan_gpt_summary_publication_bundle,
)
from millefeuille.domain.summary_publication_socket import (
    request_gpt_summary_publication,
)
from millefeuille.domain.summary_published_handoff import GptSummaryPublicationIdentity


def _emit(value):
    print(json.dumps(value, sort_keys=True, allow_nan=False), flush=True)


def _approval(packet, receipt):
    return {
        "packet": load_operator_preflight_packet(packet),
        "receipt": load_approved_live_receipt(receipt),
    }


def _publication_approval(timeout):
    """One bounded stdin line of two file paths, never paper/model payloads."""
    deadline = time.monotonic() + timeout
    data = bytearray()
    fd = sys.stdin.fileno()
    while len(data) < 8192:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise ValueError("publication approval deadline")
        part = os.read(fd, 1)
        if not part:
            raise ValueError("publication approval EOF")
        if part == b"\n":
            value = json.loads(data)
            if not isinstance(value, dict) or set(value) != {"packet", "receipt"}:
                raise ValueError("publication approval fields")
            if any(not isinstance(v, str) or not v for v in value.values()):
                raise ValueError("publication approval paths")
            return _approval(**value)
        data.extend(part)
    raise ValueError("publication approval size")


def _parser():
    parser = argparse.ArgumentParser(prog="millefeuille live-operator")
    parser.add_argument("stage", choices=("summary", "card"))
    parser.add_argument("action", choices=("plan", "preflight", "execute-publish"))
    for name in (
        "route-evidence",
        "structure-evidence",
        "preparation",
        "source-pack-root",
        "artifact-root",
        "run-id",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--publication-identity",
        help="Verified published summary identity JSON, required for card",
    )
    parser.add_argument("--packet")
    parser.add_argument("--receipt")
    parser.add_argument("--mode", choices=("approved-live",))
    parser.add_argument("--publication-wait-seconds", type=int, default=120)
    return parser


def _operate(args):
    # No root execution of caller-selected Python/code or broker provisioning.
    if os.name != "posix" or os.geteuid() == 0:
        raise ValueError("unprivileged POSIX operator required")
    if not 1 <= args.publication_wait_seconds <= 120:
        raise ValueError("bounded publication wait required")
    if args.action == "execute-publish" and args.mode != "approved-live":
        raise ValueError("explicit live mode required")
    if args.action != "execute-publish" and args.mode is not None:
        raise ValueError("offline actions cannot select live mode")
    if args.action == "plan" and (args.packet or args.receipt):
        raise ValueError("plan does not consume approval files")
    require_safe_package_id(args.run_id, "run_id")
    if len(args.run_id) > 128:
        raise ValueError("run id too long")
    evidence = {
        "route_evidence_path": args.route_evidence,
        "structure_evidence_path": args.structure_evidence,
        "preparation_path": args.preparation,
    }
    inputs = dict(evidence, source_pack_root=args.source_pack_root)
    publication = None
    if args.stage == "card":
        if not args.publication_identity:
            raise ValueError("summary publication identity required")
        publication = GptSummaryPublicationIdentity(
            **json.loads(
                read_bytes_no_follow(
                    args.publication_identity,
                    "summary publication identity",
                    max_bytes=16384,
                )
            )
        )
        inputs.update(publication=publication, run_id=args.run_id)
        plan = plan_published_gpt_paper_card_request(**inputs)
        metadata = {"manifest_sha256": plan.manifest_sha256, "request_count": 1}
    else:
        if args.publication_identity:
            raise ValueError("summary cannot use publication identity")
        plan = plan_grounded_gpt_summary_batch(**evidence)
        metadata = {
            "manifest_sha256": plan.manifest.sha256,
            "request_count": len(plan.batch.units),
        }
    del plan
    if args.action == "plan":
        _emit(dict(metadata, status="planned", provider_calls=0, writes=0))
        return
    approval = _approval(args.packet, args.receipt)
    scope = dict(inputs, artifact_root=args.artifact_root, **approval)
    validate = (
        validate_gpt_card_approval_preview
        if args.stage == "card"
        else validate_gpt_summary_approval_preview
    )
    preview = validate(**scope)
    if args.action == "preflight":
        _emit(
            dict(
                metadata,
                status="scope-validated-not-auth-or-root-readiness",
                provider_calls=0,
                writes=0,
            )
        )
        return
    # Domain executors retain exact OAuth/root reservation/source/replay gates.
    execute = (
        run_trusted_gpt_paper_card
        if args.stage == "card"
        else run_trusted_gpt_summary_batch
    )
    outcome = None
    try:
        outcome = execute(**scope)
        output_inputs = dict(evidence, outcome=outcome)
        if args.stage == "card":
            output = plan_gpt_paper_card_outputs(
                **output_inputs,
                source_pack_root=args.source_pack_root,
                publication=publication,
            )
            pending = {
                "write_manifest_sha256": output.write_manifest_sha256,
                "request_plan_sha256": output.request_plan_sha256,
                "summary_publication_sha256": output.summary_publication_sha256,
                "provenance_sha256": output.provenance_sha256,
                "total_bytes": output.total_bytes,
                "file_count": len(output.files),
                "paper_id": output.paper_id,
            }
            del output
        else:
            output_inputs["run_id"] = args.run_id
            output = plan_gpt_summary_outputs(**output_inputs)
            observed = plan_gpt_summary_observed_usage(**output_inputs)
            provenance = plan_gpt_summary_model_provenance(**output_inputs)
            pending = {
                "write_manifest_sha256": output.write_manifest_sha256,
                "source_manifest_sha256": output.source_manifest_sha256,
                "observed_usage_sha256": observed.observed_usage_sha256,
                "provenance_manifest_sha256": provenance.provenance_manifest_sha256,
                "paper_id": output.paper_id,
            }
            del output, observed, provenance
        _emit(
            dict(
                pending,
                status="awaiting-separate-publication-approval",
                run_id=args.run_id,
                execution_receipt_digest=preview.receipt_digest,
                wait_seconds=args.publication_wait_seconds,
            )
        )
        write_approval = _publication_approval(args.publication_wait_seconds)
        if (
            write_approval["receipt"].content_digest
            == approval["receipt"].content_digest
        ):
            raise ValueError("execution receipt is not publication authority")
        publish_inputs = dict(
            evidence,
            outcome=outcome,
            source_pack_root=args.source_pack_root,
            **write_approval,
        )
        if args.stage == "card":
            ack = request_gpt_card_publication(
                **publish_inputs, publication=publication
            )
        else:
            bundle = plan_gpt_summary_publication_bundle(
                **publish_inputs, run_id=args.run_id
            )
            identity = GptSummaryPublicationIdentity(
                paper_id=bundle.preview.paper_id,
                run_id=args.run_id,
                root_target_id=compute_operator_root_target_id(args.source_pack_root),
                packet_digest=bundle.preview.packet_digest,
                receipt_digest=bundle.preview.receipt_digest,
                source_manifest_sha256=bundle.preview.source_manifest_sha256,
                write_manifest_sha256=bundle.preview.write_manifest_sha256,
                observed_usage_sha256=bundle.preview.observed_usage_sha256,
                provenance_manifest_sha256=bundle.preview.provenance_manifest_sha256,
                bundle_manifest_sha256=bundle.bundle_manifest_sha256,
                file_count=len(bundle.files),
                total_bytes=bundle.total_bytes,
            )
            del bundle
            ack = request_gpt_summary_publication(**publish_inputs, run_id=args.run_id)
            if (
                ack.bundle_manifest_sha256 != identity.bundle_manifest_sha256
                or ack.file_count != identity.file_count
            ):
                raise ValueError("publication acknowledgement drift")
            _emit(
                {"status": "summary-publication-identity", "identity": asdict(identity)}
            )
        _emit(dict(asdict(ack), status="published-not-accepted"))
    finally:
        # No file persistence or resumable payload. Python immutable bytes cannot
        # be securely zeroized; dropping references/process exit is not erasure.
        outcome = None


class _OperatorCancelled(BaseException):
    """Process control, never a provider failure or retryable model result."""


@contextmanager
def _controlled_termination():
    """Unwind once; repeated ordinary termination cannot interrupt cleanup."""
    previous = {}
    cancelling = False

    def cancel(_signum, _frame):
        nonlocal cancelling
        if not cancelling:
            cancelling = True
            raise _OperatorCancelled()

    try:
        for name in ("SIGTERM", "SIGHUP", "SIGINT"):
            signum = getattr(signal, name, None)
            if signum is not None:
                previous[signum] = signal.signal(signum, cancel)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run_live_operator_cli(argv):
    args = _parser().parse_args(argv)
    try:
        with _controlled_termination():
            _operate(args)
    except (Exception, KeyboardInterrupt, _OperatorCancelled):
        # Never render provider exceptions, paths, private output or tracebacks.
        _emit(
            {
                "status": "stopped",
                "action": args.action,
                "retry": "forbidden-without-fresh-review",
                "publication": "not-confirmed",
            }
        )
        return 3
    return 0
