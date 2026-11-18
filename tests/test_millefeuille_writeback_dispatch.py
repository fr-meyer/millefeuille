"""Full fixed-root dispatch in TEMP fixtures. UID metadata is synthetic only.

Actual descriptor checks, Unix peer credentials, receipt validation, SQLite,
main-thread server and HTTP MockTransport. Never DNS/API or real root files.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.domain import writeback_http as http
from millefeuille.domain import writeback_http_process as contained
from millefeuille.domain import writeback_live as live
from millefeuille.domain import writeback_live_broker as broker
from millefeuille.domain.writeback_core import _hash, _wire
from millefeuille.domain.writeback_live_prepare import preparation_request
from millefeuille.domain.writeback_live_science import acceptance_request
from tests import test_millefeuille_writeback_core as core
from tests.test_millefeuille_writeback_science import issue, observation


def assert_dispatch_unavailable(test, payload):
    from unittest.mock import Mock
    channel = Mock()
    # Retain scenario input as unread data: unauthenticated peers cannot dispatch it.
    pending = _wire({"scenario": payload})
    channel.recv.return_value = struct.pack("!I", len(pending)) + pending
    with patch.object(broker, "process") as process, patch.object(
        broker, "protected_read"
    ) as custody, patch.object(contained.subprocess, "Popen") as worker:
        with test.assertRaisesRegex(broker.Error, "GPT card broker needs peer credentials"):
            broker.serve_connection(channel, {"caller_uid": 0})
        process.assert_not_called()
        custody.assert_not_called()
        worker.assert_not_called()
    channel.recv.assert_not_called()
    channel.send.assert_not_called()
    channel.getsockopt.assert_not_called()


class DispatchTests(unittest.TestCase):
    def setUp(self):
        if os.name == "nt":
            return  # Test bodies assert refusal before custody/HTTP setup.
        self.f = core.WritebackCoreTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = Path(self.f.temp.name)
        self.control = self.root / "control"
        self.control.mkdir(mode=0o700)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir(mode=0o700)
        evidence_patch = patch.object(live, "EVIDENCE_ROOT", self.evidence)
        evidence_patch.start()
        self.addCleanup(evidence_patch.stop)
        source = b"synthetic original source"
        sha = "sha256:" + hashlib.sha256(source).hexdigest()
        # Replace synthetic source identity consistently before independently planning.
        old = self.f.bundle["source"]["source_hash"]
        self.f.bundle = json.loads(json.dumps(self.f.bundle).replace(old, sha))
        self.f.plan = self.f.makeplan()
        self.plan = self.f.plan
        original = observation({**self.f.before, "version": 7})
        docs = {
            **self.f.bundle,
            "before": self.f.before,
            "original_parent": {**self.f.before, "version": 7},
            "original_proof": original,
            "collection": self.f.mapping,
        }
        ledger, chain, published = self.classification_chain()
        docs.update(
            classification_ledger=ledger,
            classification_chain=chain,
            classification_output=published,
        )
        files = {}
        for name, value in {**docs, "source_bytes": source}.items():
            raw = value if isinstance(value, bytes) else _wire(value)
            p = self.evidence / name
            p.write_bytes(raw)
            p.chmod(0o600)
            files[name] = {
                "name": name,
                "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
        self.collection = {
            k: self.f.mapping[k] for k in ("library_type", "library_id", "key")
        }
        self.collection.update(version=7, name="Accepted topic", parentCollection=False)
        self.f.commitment["uid"] = 0
        commitment = _wire(self.f.commitment)
        self.config = {
            "schema": "millefeuille-writeback-root/v1",
            "caller_uid": os.getuid(),
            "caller_gid": os.getgid(),
            "files": files,
            "allowlist": {
                **{k: self.f.before[k] for k in ("library_type", "library_id", "key")},
                "collection_key": self.f.mapping["key"],
            },
            "add_tags": ["new"],
            "remove_tags": ["remove"],
            "accepted_plan_digest": self.plan.digest,
            "commitment_sha256": "sha256:" + hashlib.sha256(commitment).hexdigest(),
            "transport_accepted": True,
            "collection": self.collection,
        }
        self.save("config.json", self.config)
        self.save("journal-commitment.json", self.f.commitment)
        self.save("credential", b"SYNTHETICSECRET")
        self.receipt = issue(live.request_scope(self.plan, "execute"))
        self.save("execute-receipt.json", self.receipt)
        prep = issue(preparation_request(self.plan, self.collection))
        prep["receipt_id"] = "synthetic-prepare"
        from millefeuille.domain.live_receipts import (
            compute_approved_live_receipt_digest,
        )

        prep["integrity"]["content_digest"] = compute_approved_live_receipt_digest(prep)
        self.save("execute-preparation-receipt.json", prep)
        science = issue(acceptance_request(self.plan, files))
        science["receipt_id"] = "synthetic-science"
        science["integrity"]["content_digest"] = compute_approved_live_receipt_digest(
            science
        )
        self.save("science-acceptance-receipt.json", science)
        self.save(
            "science-attestation.json",
            {
                "schema": "millefeuille-writeback-science-custody/v1",
                "plan_digest": self.plan.digest,
                "files_digest": _hash(files),
                "science_receipt_digest": science["integrity"]["content_digest"],
            },
        )
        self.calls = []
        self.state = copy.deepcopy(self.f.before)
        self.real_fstat = os.fstat
        self.real_stat = os.stat

    def save(self, name, value):
        p = self.control / name
        p.write_bytes(value if isinstance(value, bytes) else _wire(value))
        p.chmod(0o600)

    def meta(self, s):
        values = {
            name: getattr(s, name)
            for name in (
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime",
                "st_mtime_ns",
                "st_ctime_ns",
                "st_nlink",
                "st_mode",
                "st_uid",
            )
        }
        values["st_uid"] = 0
        if stat.S_ISDIR(s.st_mode):
            values["st_mode"] = s.st_mode & ~0o022
        return SimpleNamespace(**values)

    def exchange(self, payload):
        self.state_path.write_text(
            json.dumps(
                {
                    "state": self.state,
                    "collection": self.collection,
                    "calls": self.calls,
                    "collection_drift": getattr(self, "collection_drift", False),
                }
            )
        )
        self.state_path.chmod(0o600)
        response = []
        path = self.control / "operator.sock"

        # Client thread only. Service stays on current MAIN thread.
        def client():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(10)
                import time

                until = time.monotonic() + 5
                while True:
                    try:
                        s.connect(str(path))
                        break
                    except (FileNotFoundError, PermissionError, ConnectionRefusedError):
                        if time.monotonic() >= until:
                            raise
                        time.sleep(0.01)
                wire = _wire(payload)
                s.sendall(struct.pack("!I", len(wire)) + wire)
                size = struct.unpack("!I", s.recv(4))[0]
                data = b""
                while len(data) < size:
                    data += s.recv(size - len(data))
                response.append(json.loads(data))

        thread = threading.Thread(target=client)
        thread.start()
        try:
            with patch.object(broker, "SOCKET", path), patch.object(broker.os, "chown"):
                broker.serve_one()
        finally:
            thread.join(12)
        self.assertFalse(thread.is_alive())
        remote = json.loads(self.state_path.read_text())
        self.state, self.calls = remote["state"], remote["calls"]
        return response[0]

    def test_actual_socket_execute_and_restart_replay(self):
        if os.name == "nt":
            assert_dispatch_unavailable(self, "test_actual_socket_execute_and_restart_replay")
            return
        payload = {
            "operation": "execute",
            "plan_digest": self.plan.digest,
            "receipt": self.receipt,
            "execution_audit": None,
        }
        state_path = self.root / "remote-fixture.json"
        self.state_path = state_path
        worker = self.root / "mock-worker.py"
        worker.write_text(
            "import sys\nsys.path.insert(0,"
            + repr(str(Path(http.__file__).resolve().parents[2]))
            + ")\nfrom tests.writeback_mock_worker import run\nrun("
            + repr(str(state_path))
            + ")\n"
        )
        original_popen = subprocess.Popen
        children = []

        def spawn(*args, **kwargs):
            self.assertNotIn("SYNTHETICSECRET", repr(args) + repr(kwargs))
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child

        def reaped():
            for child in children:
                self.assertIsNotNone(child.poll())
                self.assertFalse(contained._alive(child.pid))

        self.addCleanup(reaped)
        with (
            patch.object(broker, "CONTROL", self.control),
            patch.object(broker, "CONFIG", self.control / "config.json"),
            patch.object(
                broker, "COMMITMENT", self.control / "journal-commitment.json"
            ),
            patch.object(broker, "JOURNAL", self.f.journal.path),
            patch.object(live, "EVIDENCE_ROOT", self.evidence),
            patch.object(broker.os, "geteuid", return_value=0),
            patch("os.fstat", side_effect=lambda fd: self.meta(self.real_fstat(fd))),
            patch(
                "os.stat",
                side_effect=lambda *a, **kw: self.meta(self.real_stat(*a, **kw)),
            ),
            patch.object(
                contained,
                "_worker_command",
                return_value=[sys.executable, "-I", "-B", str(worker)],
            ),
            patch.object(contained.subprocess, "Popen", side_effect=spawn),
        ):
            # Invalid static preflight never consumes preparation or opens HTTP.
            forged = copy.deepcopy(payload)
            forged["receipt"] = {}
            self.assertEqual(self.exchange(forged)["status"], "denied-or-unconfirmed")
            self.assertEqual(self.calls, [])
            original = (self.evidence / "source_bytes").read_bytes()
            (self.evidence / "source_bytes").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                broker._controls()
            (self.evidence / "source_bytes").write_bytes(original)
            (self.evidence / "source_bytes").chmod(0o644)
            with self.assertRaises(ValueError):
                broker._controls()
            (self.evidence / "source_bytes").chmod(0o600)
            # Actual custody; injected import write fault leaves quarantine.
            from millefeuille.domain import writeback_live_import as importer

            with (
                patch.object(importer, "CONTROL", self.control),
                patch.object(
                    importer, "ATTESTATION", self.control / "science-attestation.json"
                ),
            ):
                (self.control / "science-attestation.json").unlink()
                with (
                    patch.object(
                        importer.os, "write", side_effect=OSError("synthetic fault")
                    ),
                    self.assertRaises(OSError),
                ):
                    importer.import_attended(confirmed=True)
                with self.assertRaises(FileExistsError):
                    importer.import_attended(confirmed=True)
                (
                    self.control / "science-attestation.json"
                ).unlink()  # explicit TEST fixture recovery only
                importer.import_attended(confirmed=True)
            from millefeuille.domain import writeback_live_provision as provision

            self.save("provision-approved", b"new-writeback-journal-v1")
            newdir = self.root / "newjournal"
            newdir.mkdir(mode=0o700)
            newcommit = self.control / "newcommitment.json"
            with (
                patch.object(provision, "COMMITMENT", newcommit),
                patch.object(provision, "JOURNAL", newdir / "new.sqlite3"),
            ):
                with (
                    patch.object(
                        provision.os,
                        "write",
                        side_effect=OSError("synthetic interrupted commitment"),
                    ),
                    self.assertRaises(OSError),
                ):
                    provision.provision_attended(confirmed_new_namespace=True)
                self.assertTrue(newcommit.exists())
                with self.assertRaises(FileExistsError):
                    provision.provision_attended(confirmed_new_namespace=True)
            result = self.exchange(payload)
            if getattr(self, "collection_drift", False):
                self.assertEqual(result["status"], "denied-or-unconfirmed")
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.state["version"], 9)
                return
            self.assertEqual(result["result"]["status"], "written")
            self.assertEqual(len(self.calls), 5)
            saved = self.exchange(
                {
                    "operation": "readback",
                    "plan_digest": self.plan.digest,
                    "receipt": None,
                    "execution_audit": result["audit_digest"],
                }
            )
            self.assertEqual(saved["status"], "journal-readback-not-remote")
            self.assertEqual(saved["audit"], result["audit"])
            self.assertEqual(len(self.calls), 5)
            count = len(self.calls)
            self.assertEqual(self.exchange(payload)["status"], "denied-or-unconfirmed")
            self.assertEqual(len(self.calls), count)
            from millefeuille.domain.live_receipts import (
                compute_approved_live_receipt_digest,
            )

            audit = result["audit"]
            digest = result["audit_digest"]
            rollback = issue(live.request_scope(self.plan, "rollback", digest))
            rollback["receipt_id"] = "synthetic-rollback"
            rollback["integrity"]["content_digest"] = (
                compute_approved_live_receipt_digest(rollback)
            )
            prep = issue(preparation_request(self.plan, self.collection, audit))
            prep["receipt_id"] = "synthetic-rollback-prep"
            prep["integrity"]["content_digest"] = compute_approved_live_receipt_digest(
                prep
            )
            self.save("rollback-receipt.json", rollback)
            self.save("rollback-preparation-receipt.json", prep)
            if getattr(self, "rollback_drift", False):
                self.state["version"] += 1
            out = self.exchange(
                {
                    "operation": "rollback",
                    "plan_digest": self.plan.digest,
                    "receipt": rollback,
                    "execution_audit": digest,
                }
            )
            if getattr(self, "rollback_drift", False):
                self.assertEqual(out["status"], "denied-or-unconfirmed")
                self.assertEqual(len(self.calls), 7)
                self.assertEqual(
                    sum(method == "PATCH" for method, url in self.calls), 1
                )
                return
            self.assertEqual(out["audit"]["status"], "rolled-back")
            self.assertEqual(len(self.calls), 10)

    def classification_chain(self):
        import sqlite3

        from millefeuille.domain.classification_live_scope import identity
        from millefeuille.domain.live_receipts import (
            ReceiptReplayState,
            build_approved_live_audit_record,
        )
        from tests.test_millefeuille_classification_live import (
            TestClassificationLive,
            pair,
        )

        f = TestClassificationLive()
        f.setUp()
        self.addCleanup(f.doCleanups)
        execution = (f.packet, f.receipt)
        from millefeuille.domain.classification_live import output_identity

        published = {"proposal.json": "synthetic accepted published output"}
        publication = pair(
            f.inputs,
            f.plan,
            output_identity({k: v.encode() for k, v in published.items()}),
        )
        db = sqlite3.connect(":memory:")
        db.execute(
            "CREATE TABLE approvals (receipt_digest TEXT,plan_sha256 TEXT,"
            "operation TEXT,audit_json TEXT)"
        )
        for packet, receipt in (execution, publication):
            audit = build_approved_live_audit_record(
                receipt,
                packet.to_approved_live_request(),
                status="consumed",
                replay_state=ReceiptReplayState(),
            )
            db.execute(
                "INSERT INTO approvals VALUES (?,?,?,?)",
                (
                    receipt.content_digest,
                    identity(f.plan),
                    packet.operations[0],
                    _wire(audit).decode(),
                ),
            )
        db.commit()
        raw = db.serialize()
        db.close()
        return (
            raw,
            {
                "execution_receipt_digest": execution[1].content_digest,
                "publication_receipt_digest": publication[1].content_digest,
                "classification_plan_sha256": identity(f.plan),
            },
            published,
        )

    def test_collection_drift_denies_before_patch(self):
        if os.name == "nt":
            assert_dispatch_unavailable(self, "test_collection_drift_denies_before_patch")
            return
        self.collection_drift = True
        self.test_actual_socket_execute_and_restart_replay()

    def test_rollback_changed_version_denies(self):
        if os.name == "nt":
            assert_dispatch_unavailable(self, "test_rollback_changed_version_denies")
            return
        self.rollback_drift = True
        self.test_actual_socket_execute_and_restart_replay()
