"""Real root/node publication uses disposable synthetic source and controls."""

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import paper_card_output_replay_reservation as reservation
from millefeuille.domain import paper_card_output_trusted_approval as trusted
from millefeuille.domain import paper_card_publication_broker as broker
from millefeuille.domain import paper_card_publication_socket as publication_socket
from millefeuille.domain.paper_card_outcome_handoff import (
    encode_gpt_card_outcome_handoff,
)
from tests import test_millefeuille_paper_card_output_controls as controls_tests
from tests import test_millefeuille_summary_reservation_broker as broker_tests

_CLIENT = """
import base64, json, os, socket, struct, sys
value=json.load(sys.stdin)
metadata=base64.b64decode(value['metadata'])
handoff=base64.b64decode(value['handoff'])
def read_exact(channel,size):
    result=b''
    while len(result)<size:
        chunk=channel.recv(size-len(result))
        if not chunk: raise RuntimeError('closed')
        result+=chunk
    return result
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as channel:
    channel.settimeout(15)
    channel.connect(value['socket'])
    pid,uid,gid=struct.unpack('3i',channel.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    assert uid==0
    channel.sendall(struct.pack('!I',len(metadata))+metadata)
    channel.sendall(struct.pack('!I',len(handoff))+handoff)
    size=struct.unpack('!I',read_exact(channel,4))[0]
    assert size<=4096
    reply=json.loads(read_exact(channel,size))
print(json.dumps({'reply':reply,'uid':os.geteuid(),'server_uid':uid}))
"""


@unittest.skipUnless(
    sys.platform == "linux" and os.geteuid() == 0,
    "requires Linux root and separate node account",
)
class TestRootGptCardPublication(unittest.TestCase):
    def test_real_root_node_publication_is_exact_audited_single_use_and_clean(self):
        import pwd

        node = pwd.getpwnam("node")
        self.assertNotEqual(node.pw_uid, 0)
        fixture, bundle, record = controls_tests._case(self)
        with tempfile.TemporaryDirectory() as tempdir:
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o755)
            approval = control / "approval.json"
            controls_tests._write_record(approval, record)
            # Only synthetic fixture paths are exposed to the separate node UID.
            for target in (control, fixture.root):
                self.assertTrue(target.is_relative_to(Path("/tmp")))
                for parent in (target, *target.parents):
                    if parent == Path("/tmp"):
                        break
                    parent.chmod(0o755)
            path = control / "card-publication.sock"
            metadata = {
                "schema_version": publication_socket._SCHEMA,
                "execution_approval": asdict(fixture.values["outcome"].approval),
                "publication": asdict(fixture.values["publication"]),
                "source_pack_root": str(fixture.root),
                "evidence": {
                    key: str(fixture.values[key])
                    for key in (
                        "route_evidence_path",
                        "structure_evidence_path",
                        "preparation_path",
                    )
                },
                "packet": fixture.packet.to_dict(),
                "receipt": fixture.receipt.to_dict(),
            }
            payload = {
                "socket": str(path),
                "metadata": base64.b64encode(
                    publication_socket._canonical_json(metadata).encode("utf-8")
                ).decode("ascii"),
                "handoff": base64.b64encode(
                    encode_gpt_card_outcome_handoff(fixture.values["outcome"])
                ).decode("ascii"),
            }
            replies = []
            with (
                patch.object(reservation, "_CONTROL_DIR", control),
                patch.object(broker, "_CONTROL_DIR", control),
                patch.object(trusted, "_TRUSTED_APPROVAL_PATH", approval),
                patch.object(publication_socket, "_CONTROL_DIR", control),
                patch.object(publication_socket, "_SOCKET_PATH", path),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                for _attempt in range(2):
                    server = pool.submit(
                        publication_socket.serve_one_publication, timeout_seconds=15
                    )
                    broker_tests._wait_for_socket(path)
                    detail = path.stat()
                    self.assertEqual((detail.st_uid, detail.st_gid), (0, node.pw_gid))
                    self.assertEqual(stat.S_IMODE(detail.st_mode), 0o660)
                    child = subprocess.run(
                        [sys.executable, "-c", _CLIENT],
                        input=json.dumps(payload),
                        text=True,
                        capture_output=True,
                        timeout=20,
                        user=node.pw_uid,
                        group=node.pw_gid,
                        extra_groups=[],
                    )
                    self.assertEqual(child.returncode, 0, child.stderr)
                    replies.append(json.loads(child.stdout))
                    server.result(timeout=20)
                    self.assertFalse(path.exists())
            self.assertEqual(replies[0]["uid"], node.pw_uid)
            self.assertEqual(replies[0]["server_uid"], 0)
            self.assertEqual(
                replies[0]["reply"],
                {
                    "status": "published",
                    "run_id": bundle.preview.run_id,
                    "receipt_digest": fixture.receipt.content_digest,
                    "bundle_manifest_sha256": bundle.bundle_manifest_sha256,
                    "file_count": 5,
                },
            )
            self.assertEqual(replies[1]["reply"]["status"], "uncertain")
            for item in bundle.files:
                output = fixture.root / item.ref
                self.assertEqual(output.read_bytes(), item.data)
                self.assertEqual(
                    (output.stat().st_uid, stat.S_IMODE(output.stat().st_mode)),
                    (0, 0o644),
                )
            database = control / reservation._LEDGER_NAME
            self.assertEqual(
                (database.stat().st_uid, stat.S_IMODE(database.stat().st_mode)),
                (0, 0o600),
            )
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM approvals").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT status,bundle_manifest_sha256 FROM publication_attempts"
                    ).fetchall(),
                    [("published", bundle.bundle_manifest_sha256)],
                )
            self.assertFalse(list(fixture.root.rglob(".gpt-card-stage-*")))
