"""Explicit parent-only writeback. Offline planning never grants authority."""

import argparse
import json

from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.writeback_live import preflight, prepare, request_scope
from millefeuille.domain.writeback_live_files import decode


def run_writeback_operator_cli(argv):
    parser = argparse.ArgumentParser(prog="millefeuille writeback-operator")
    parser.add_argument(
        "action", choices=("plan", "preflight", "execute", "readback", "rollback")
    )
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--receipt")
    parser.add_argument("--execution-audit")
    parser.add_argument(
        "--operation", choices=("execute", "rollback"), default="execute"
    )
    parser.add_argument("--mode", choices=("approved-live",))
    args = parser.parse_args(argv)
    try:
        live = args.action in ("execute", "rollback", "readback")
        if live != (args.mode == "approved-live"):
            raise ValueError("explicit mode required only for root route")
        plan = prepare(
            decode(read_bytes_no_follow(args.inputs, "inputs", max_bytes=1048576))
        )
        receipt = None
        if args.receipt:
            receipt = decode(
                read_bytes_no_follow(args.receipt, "receipt", max_bytes=65536)
            )
        if args.action == "plan":
            if receipt is not None:
                raise ValueError("plan accepts no receipt")
            result = {
                "status": "planned-not-authorized",
                "plan_digest": plan.digest,
                "request": request_scope(
                    plan, args.operation, args.execution_audit
                ).to_dict(),
            }
        elif args.action == "preflight":
            preflight(plan, args.operation, receipt, args.execution_audit)
            result = {
                "status": "scope-validated-not-root-or-remote-readiness",
                "plan_digest": plan.digest,
                "network_calls": 0,
                "writes": 0,
            }
        else:
            if args.action != "readback":
                preflight(plan, args.action, receipt, args.execution_audit)
            elif receipt is not None or args.execution_audit is None:
                raise ValueError("readback requires audit, no receipt")
            from millefeuille.domain.writeback_live_broker import request

            result = request(
                {
                    "operation": args.action,
                    "plan_digest": plan.digest,
                    "receipt": receipt,
                    "execution_audit": args.execution_audit,
                }
            )
        print(json.dumps(result, sort_keys=True))
        return 3 if result.get("status") == "denied-or-unconfirmed" else 0
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"status": "stopped-or-unconfirmed", "retry": False}))
        return 3
