"""Synthetic custody/UID mocks, never real root or provider qualification."""

import contextlib
import copy
from datetime import UTC, datetime, timedelta
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import unittest
from unittest.mock import patch

from millefeuille.cli.writeback_operator import run_writeback_operator_cli
from millefeuille.domain import writeback_live as live
from millefeuille.domain import writeback_live_broker as broker
from millefeuille.domain.live_receipts import compute_approved_live_receipt_digest
from millefeuille.domain.writeback_core import _wire
from tests import test_millefeuille_writeback_core as core_tests


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = core_tests.WritebackCoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan
        self.inputs = {
            "materialized": self.fixture.bundle,
            "before": self.fixture.before,
            "collection": self.fixture.mapping,
            "add_tags": ["new"],
            "remove_tags": ["remove"],
        }

    def receipt(self, operation="execute", audit=None):
        request = live.request_scope(self.plan, operation, audit).to_dict()
        scope = {
            k: v
            for k, v in request.items()
            if k
            not in (
                "run_id",
                "item_cap",
                "selected_item_count",
                "provider_call_limit",
                "cost_limit_usd_micros",
            )
        }
        scope["max_items"] = 1
        scope["limits"] = {"max_provider_calls": 0, "max_cost_usd_micros": 0}
        now = datetime.now(UTC).replace(microsecond=0)
        value = {
            "schema_version": "millefeuille-approved-live-receipt/v0.1",
            "receipt_id": "synthetic-" + operation,
            "run_id": request["run_id"],
            "approval": {
                "approver_id": "fr-meyer",
                "approved_at": (now - timedelta(seconds=1)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            },
            "expires_at": (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scope": scope,
            "integrity": {
                "algorithm": "sha256",
                "content_digest": "sha256:" + "0" * 64,
            },
        }
        value["integrity"]["content_digest"] = compute_approved_live_receipt_digest(
            value
        )
        return value

    def test_cli_plan_preflight_and_invalid_no_network(self):
        root = Path(self.fixture.temp.name)
        inp, receipt = root / "inputs.json", root / "receipt.json"
        inp.write_bytes(_wire(self.inputs))
        receipt.write_bytes(_wire(self.receipt()))
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch.object(broker, "request") as network,
        ):
            self.assertEqual(
                run_writeback_operator_cli(["plan", "--inputs", str(inp)]), 0
            )
            self.assertEqual(
                run_writeback_operator_cli(
                    ["preflight", "--inputs", str(inp), "--receipt", str(receipt)]
                ),
                0,
            )
            receipt.write_text("{}")
            self.assertEqual(
                run_writeback_operator_cli(
                    [
                        "execute",
                        "--mode",
                        "approved-live",
                        "--inputs",
                        str(inp),
                        "--receipt",
                        str(receipt),
                    ]
                ),
                3,
            )
            network.assert_not_called()

    def test_exact_authenticated_receipt_and_operation(self):
        receipt = self.receipt()
        grant = live.authority(self.plan, "execute", receipt, receipt)
        grant.check(self.plan, "execute")
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            live.authority(self.plan, "execute", receipt, {})
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            live.authority(
                self.plan, "rollback", receipt, receipt, "sha256:" + "1" * 64
            )
        stale = copy.deepcopy(receipt)
        stale["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            live.authority(self.plan, "execute", stale, stale)

    def test_actual_dispatch_consumption_restart_and_rollback(self):
        # Replace obsolete mocked-root success with the actual guarded socket path.
        from tests import test_millefeuille_writeback_dispatch as dispatch

        test = dispatch.DispatchTests()
        test.setUp()
        try:
            test.test_actual_socket_execute_and_restart_replay()
        finally:
            test.doCleanups()

    def test_socket_denial_frame_on_main_thread(self):
        a, b = socket.socketpair()
        with a, b:
            wire = _wire({"forged": True})
            a.sendall(struct.pack("!I", len(wire)) + wire)
            broker.serve_connection(b, {"caller_uid": os.getuid()})
            size = struct.unpack("!I", a.recv(4))[0]
            self.assertEqual(
                json.loads(a.recv(size))["status"], "denied-or-unconfirmed"
            )

    def test_root_thread_gate(self):
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            broker.process({}, {})
