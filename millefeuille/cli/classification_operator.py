"""Explicit classification lifecycle; never chained to summary/card authority."""

import argparse
import json
import os

from millefeuille.cli.live_operator import (
    _approval,
    _controlled_termination,
    _emit,
    _OperatorCancelled,
    _publication_approval,
)
from millefeuille.domain.classification_live import (
    execute_classification,
    output_identity,
    validated_files,
)
from millefeuille.domain.classification_live_broker import approval_record, request
from millefeuille.domain.classification_live_scope import (
    OUTPUT_ROOT,
    identity,
    prepare,
    validate_scope,
)
from millefeuille.domain.secure_io import read_bytes_no_follow


def _operate(args):
    if os.name != "posix" or os.geteuid() == 0:
        raise ValueError("unprivileged POSIX operator required")
    if not 1 <= args.publication_wait_seconds <= 120:
        raise ValueError("publication wait bound")
    if (args.action == "execute-publish") != (args.mode == "approved-live"):
        raise ValueError("explicit live mode required only for execution")
    inputs = json.loads(
        read_bytes_no_follow(
            args.inputs, "classification input metadata", max_bytes=16384
        )
    )
    plan = prepare(inputs)
    meta = {
        "plan_sha256": identity(plan),
        "request_sha256": plan.request_sha256,
        "paper_id": plan.paper_id,
        "run_id": plan.run_id,
        "output_root": str(OUTPUT_ROOT),
        "request_count": 1,
    }
    del plan
    if args.action == "plan":
        if args.packet or args.receipt:
            raise ValueError("plan consumes no approvals")
        _emit(dict(meta, status="planned", provider_calls=0, writes=0))
        return
    approval = _approval(args.packet, args.receipt)
    validate_scope(inputs=inputs, **approval)
    if args.action == "preflight":
        _emit(
            dict(
                meta,
                status="scope-validated-not-auth-or-root-readiness",
                provider_calls=0,
                writes=0,
                root_approval_contract=approval_record(inputs=inputs, **approval),
            )
        )
        return
    outcome = None
    try:
        outcome = execute_classification(inputs=inputs, **approval)
        files = validated_files(inputs=inputs, outcome=outcome)
        output_sha256 = output_identity(files)
        del files
        _emit(
            dict(
                meta,
                status="awaiting-separate-publication-approval",
                output_sha256=output_sha256,
                execution_receipt_digest=outcome.execution_receipt_digest,
                wait_seconds=args.publication_wait_seconds,
            )
        )
        publication = _publication_approval(args.publication_wait_seconds)
        validate_scope(inputs=inputs, output_sha256=output_sha256, **publication)
        if publication["receipt"].content_digest == approval["receipt"].content_digest:
            raise ValueError("separate classification publication approval required")
        ack = request(inputs=inputs, outcome=outcome, **publication)
        _emit(ack)
    finally:
        outcome = None


def run_classification_operator_cli(argv):
    parser = argparse.ArgumentParser(prog="millefeuille classification-operator")
    parser.add_argument("action", choices=("plan", "preflight", "execute-publish"))
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--packet")
    parser.add_argument("--receipt")
    parser.add_argument("--mode", choices=("approved-live",))
    parser.add_argument("--publication-wait-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        with _controlled_termination():
            _operate(args)
    except (Exception, KeyboardInterrupt, _OperatorCancelled):
        _emit(
            {
                "status": "stopped",
                "publication": "not-confirmed",
                "retry": "forbidden-without-fresh-review",
            }
        )
        return 3
    return 0
