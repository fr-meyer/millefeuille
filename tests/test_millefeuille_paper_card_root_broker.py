"""Linux root-to-node card reservation rehearsal with synthetic source only."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from millefeuille.domain import paper_card_reservation_broker as broker
from tests import test_millefeuille_paper_card_trusted_reservation as trusted_tests
from tests import test_millefeuille_summary_reservation_broker as summary_broker_tests

_CLIENT = """
import json, os, sys
from pathlib import Path
from millefeuille.domain import paper_card_reservation_broker as broker
from millefeuille.domain.live_receipts import ApprovedLiveReceipt
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import OperatorPreflightPacket
from millefeuille.domain.summary_published_handoff import GptSummaryPublicationIdentity
value = json.load(sys.stdin)
broker._BROKER_SOCKET_PATH = Path(value['socket'])
try:
    result = broker.request_gpt_card_reservation(
        **value['inputs'], packet=OperatorPreflightPacket.from_dict(value['packet']),
        receipt=ApprovedLiveReceipt.from_dict(value['receipt']),
        publication=GptSummaryPublicationIdentity(**value['publication']),
    )
    print(json.dumps({'status':'reserved','uid':os.geteuid(),'calls':result.provider_calls_performed}))
except MillefeuilleContractError:
    print(json.dumps({'status':'denied','uid':os.geteuid(),'calls':0}))
"""


@unittest.skipUnless(
    sys.platform == "linux" and os.geteuid() == 0,
    "requires Linux root with a separate node account",
)
class TestRootGptCardBroker(unittest.TestCase):
    def test_real_root_and_node_reserve_once_then_reject_replay(self):
        import pwd

        node = pwd.getpwnam("node")
        self.assertNotEqual(node.pw_uid, 0)
        fixture = trusted_tests.TestTrustedGptCardReservation(
            "test_reservation_is_durable_single_use_and_contains_only_control_metadata"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture._write()
        control = fixture.control
        source = fixture.values["source_pack_root"]
        # Only these newly created synthetic fixture directories are exposed
        # for the node client. Existing paper roots and controls are untouched.
        for target in (control, source):
            self.assertTrue(target.is_relative_to(Path("/tmp")))
            for parent in (target, *target.parents):
                if parent == Path("/tmp"):
                    break
                parent.chmod(0o755)
        for target in source.rglob("*"):
            target.chmod(0o755 if target.is_dir() else 0o644)
        path = control / "card.sock"
        payload = {
            "socket": str(path),
            "inputs": {
                key: str(value)
                for key, value in fixture.values.items()
                if key not in {"packet", "receipt", "publication"}
            },
            "packet": fixture.packet.to_dict(),
            "receipt": fixture.receipt.to_dict(),
            "publication": vars(fixture.values["publication"]),
        }
        replies = []
        with (
            patch.object(broker, "_CONTROL_DIR", control),
            patch.object(broker, "_BROKER_SOCKET_PATH", path),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            for _attempt in range(2):
                server = pool.submit(broker.serve_one_reservation, timeout_seconds=15)
                summary_broker_tests._wait_for_socket(path)
                detail = path.stat()
                self.assertEqual((detail.st_uid, detail.st_gid), (0, node.pw_gid))
                child = subprocess.run(
                    [sys.executable, "-c", _CLIENT],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    timeout=15,
                    user=node.pw_uid,
                    group=node.pw_gid,
                    extra_groups=[],
                )
                self.assertEqual(child.returncode, 0, child.stderr)
                replies.append(json.loads(child.stdout))
                server.result(timeout=15)
                self.assertFalse(path.exists())
        self.assertEqual(
            replies,
            [
                {"status": "reserved", "uid": node.pw_uid, "calls": 0},
                {"status": "denied", "uid": node.pw_uid, "calls": 0},
            ],
        )
        with sqlite3.connect(control / "gpt-card.sqlite3") as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM approvals").fetchone()[0], 1
            )
