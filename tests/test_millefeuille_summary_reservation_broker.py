"""Offline one-shot Unix broker checks for GPT summary reservations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.domain import summary_replay_reservation as reservation
from millefeuille.domain import summary_reservation_broker as broker
from millefeuille.domain import summary_trusted_approval as trusted
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_execution_scope import (
    validate_gpt_summary_approval_preview,
)
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_summary_trusted_approval import _case, _write_record


@contextmanager
def _broker_test_controls(control: Path):
    model_user = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
    with (
        patch.dict(
            sys.modules,
            {"pwd": SimpleNamespace(getpwnam=lambda _name: model_user)},
        ),
        patch.object(broker, "_CONTROL_DIR", control),
        patch.object(broker, "_BROKER_SOCKET_PATH", control / "summary.sock"),
        patch.object(broker, "_BROKER_OWNER_UID", os.getuid()),
        patch.object(broker, "_require_control_dir", return_value=None),
        patch.object(broker.os, "geteuid", return_value=0),
        patch.object(broker.os, "chown", return_value=None),
    ):
        yield


def _wait_for_socket(path: Path) -> None:
    deadline = time.monotonic() + 5
    while True:
        try:
            detail = path.stat()
            if stat.S_ISSOCK(detail.st_mode) and stat.S_IMODE(detail.st_mode) == 0o660:
                return
        except FileNotFoundError:
            pass
        if time.monotonic() >= deadline:
            raise AssertionError("broker socket did not become ready")
        time.sleep(0.01)


@requires_secure_nofollow_writes
class TestGptSummaryReservationBroker(unittest.TestCase):
    def test_real_ledger_reservation_rejects_replay_over_new_socket(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "paper"
            root.mkdir()
            evidence, packet, receipt, record = _case(root)
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            approval = control / "approval.json"
            _write_record(approval, record)
            with (
                _broker_test_controls(control),
                patch.object(reservation, "_CONTROL_DIR", control),
                patch.object(reservation, "_CONTROL_OWNER_UID", os.getuid()),
                patch.object(trusted, "_TRUSTED_APPROVAL_PATH", approval),
                patch.object(trusted, "_TRUSTED_APPROVAL_OWNER_UID", os.getuid()),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                for attempt in (1, 2):
                    future = pool.submit(
                        broker.serve_one_reservation, timeout_seconds=5
                    )
                    _wait_for_socket(control / "summary.sock")
                    if attempt == 1:
                        result = broker.request_gpt_summary_reservation(
                            **evidence, packet=packet, receipt=receipt
                        )
                        self.assertEqual(result.provider_calls_performed, 0)
                    else:
                        with self.assertRaises(MillefeuilleContractError):
                            broker.request_gpt_summary_reservation(
                                **evidence, packet=packet, receipt=receipt
                            )
                    future.result(timeout=5)
            self.assertTrue((control / "gpt-summary.sqlite3").is_file())

    def test_one_node_request_receives_exact_reservation_ack(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "paper"
            root.mkdir()
            evidence, packet, receipt, _record = _case(root)
            preview = validate_gpt_summary_approval_preview(
                **evidence, packet=packet, receipt=receipt
            )
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with (
                _broker_test_controls(control),
                patch.object(
                    broker, "reserve_trusted_gpt_summary_receipt", return_value=preview
                ) as reserve,
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(broker.serve_one_reservation, timeout_seconds=5)
                _wait_for_socket(control / "summary.sock")
                result = broker.request_gpt_summary_reservation(
                    **evidence, packet=packet, receipt=receipt
                )
                future.result(timeout=5)
                self.assertEqual(reserve.call_count, 1)
                self.assertEqual(result.manifest_sha256, preview.manifest_sha256)
                self.assertEqual(result.provider_calls_performed, 0)
                self.assertFalse((control / "summary.sock").exists())
                with self.assertRaises(MillefeuilleContractError):
                    broker.request_gpt_summary_reservation(
                        **evidence, packet=packet, receipt=receipt
                    )
            after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_broker_denies_malformed_request_without_reservation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            with (
                _broker_test_controls(control),
                patch.object(broker, "reserve_trusted_gpt_summary_receipt") as reserve,
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(broker.serve_one_reservation, timeout_seconds=5)
                path = control / "summary.sock"
                _wait_for_socket(path)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                    channel.settimeout(5)
                    channel.connect(str(path))
                    request = b'{"packet":{}}'
                    channel.sendall(struct.pack("!I", len(request)) + request)
                    size = struct.unpack("!I", broker._read_exact(channel, 4))[0]
                    reply = json.loads(broker._read_exact(channel, size))
                future.result(timeout=5)
                self.assertEqual(reply["status"], "denied")
                reserve.assert_not_called()

    def test_evidence_cannot_escape_source_root_or_follow_symlink(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "source"
            root.mkdir()
            evidence = root / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            outside = Path(tempdir) / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            with self.assertRaises(MillefeuilleContractError):
                broker._require_evidence_paths({"evidence": str(outside)}, str(root))
            link = root / "linked.json"
            link.symlink_to(evidence)
            with self.assertRaises(MillefeuilleContractError):
                broker._require_evidence_paths({"evidence": str(link)}, str(root))

    def test_evidence_swap_after_path_check_fails_closed_during_replan(self):
        for field in ("route_evidence_path", "structure_evidence_path"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir) / "paper"
                root.mkdir()
                evidence, packet, receipt, _record = _case(root)
                target = Path(evidence[field])
                outside = Path(tempdir) / "outside.json"
                outside.write_bytes(target.read_bytes())
                payload = {
                    "packet": packet.to_dict(),
                    "receipt": receipt.to_dict(),
                    "evidence": {
                        key: str(evidence[key]) for key in broker._EVIDENCE_FIELDS
                    },
                }
                original_check = broker._require_evidence_paths

                def swap_after_check(
                    paths,
                    source_root,
                    checked=original_check,
                    checked_target=target,
                    outside_target=outside,
                ):
                    checked(paths, source_root)
                    checked_target.rename(
                        checked_target.with_name(checked_target.name + ".saved")
                    )
                    checked_target.symlink_to(outside_target)

                with (
                    patch.object(
                        broker, "_require_evidence_paths", side_effect=swap_after_check
                    ),
                    patch.object(
                        broker,
                        "reserve_trusted_gpt_summary_receipt",
                        side_effect=lambda **kwargs: (
                            validate_gpt_summary_approval_preview(**kwargs)
                        ),
                    ),
                    self.assertRaises(MillefeuilleContractError),
                ):
                    broker._process_request(payload)
