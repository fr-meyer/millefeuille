"""Actual writer-death boundaries, not mocked exceptions or power-loss proof."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from millefeuille.domain.card_index_contract import (
    load_and_validate_canonical_card_index,
)
from millefeuille.domain.index_fixtures import (
    CARD_INDEX_TRANSACTION_ROOT_REF,
    write_indexes_from_evidence,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.route_fixtures import ROUTE_MARKDOWN_REF
from millefeuille.domain.summary_fixtures import SUMMARY_ARTIFACT_REF
from tests.fixtures.evidence_transport import runtime_identity
from tests.fixtures.f05_receipts import effects, emit, snapshot, source_binding
from tests.test_millefeuille_source_pack_writer import _prepare_card_index_fixture

CRASH_WORKER = """
import json
import os
import sys
from millefeuille.domain import index_fixtures
request = json.loads(sys.stdin.read())
seam = request.pop("seam")
original = getattr(index_fixtures, seam)
def crash_after_real_effect(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(71)
setattr(index_fixtures, seam, crash_after_real_effect)
index_fixtures.write_indexes_from_evidence(**request)
raise AssertionError("selected real boundary was not reached")
"""


@unittest.skipUnless(
    os.name == "nt" or sys.platform.startswith(("linux", "darwin")),
    "atomic card exchange requires a supported platform",
)
class TestIndexRefreshCrashRecovery(unittest.TestCase):
    def test_death_after_index_publish_refuses_join_then_recovers(self):
        self._check_crash("_ensure_expected_index", observed=False)

    def test_death_after_card_exchange_keeps_truthful_join_and_original(self):
        self._check_crash("_atomic_capture_replace", observed=True)

    def _check_crash(self, seam, *, observed):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root, run_dir, _, evidence = _prepare_card_index_fixture(temporary)
            card_path = run_dir / "cards/paper-card.json"
            original_card = card_path.read_bytes()
            self.assertEqual(
                json.loads(original_card)["index_state"]["phase"], "planned"
            )
            protected = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob("*")
                if path.is_file() and not path.is_relative_to(run_dir)
            }
            before = snapshot(root)
            process = subprocess.run(
                [sys.executable, "-B", "-c", CRASH_WORKER],
                input=json.dumps(
                    {
                        "seam": seam,
                        "evidence_path": str(evidence),
                        "source_pack_root": str(source_root),
                        "run_id": "run-fixture",
                    }
                ),
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(process.returncode, 71, process.stderr)
            after_death = snapshot(root)
            index_path = run_dir / "index/index-status.json"
            index_bytes = index_path.read_bytes()
            index = json.loads(index_bytes)
            readback = {
                "card_path": card_path,
                "index_path": index_path,
                "paper_id": index["paper_id"],
                "run_id": "run-fixture",
                "source_hash": index["source_hash"],
                "selected_fulltext_path": run_dir.parents[2] / ROUTE_MARKDOWN_REF,
                "summary_path": run_dir / SUMMARY_ARTIFACT_REF,
            }
            transactions = list((run_dir / CARD_INDEX_TRANSACTION_ROOT_REF).iterdir())
            self.assertEqual(len(transactions), 1)
            transaction = transactions[0]
            recovery_index = transaction / "index-status.json"
            self.assertEqual(recovery_index.read_bytes(), index_bytes)
            self.assertNotEqual(
                (recovery_index.stat().st_dev, recovery_index.stat().st_ino),
                (index_path.stat().st_dev, index_path.stat().st_ino),
            )
            retained = {
                path: path.read_bytes()
                for path in transaction.iterdir()
                if path.is_file()
            }
            if observed:
                load_and_validate_canonical_card_index(**readback)
                initial_readback = "accepted"
                displaced_name = (
                    "displaced-card.json" if os.name == "nt" else "card-exchange.json"
                )
                self.assertEqual(
                    (transaction / displaced_name).read_bytes(), original_card
                )
            else:
                self.assertEqual(card_path.read_bytes(), original_card)
                with self.assertRaises(MillefeuilleContractError):
                    load_and_validate_canonical_card_index(**readback)
                initial_readback = "refused:MillefeuilleContractError"
            # No patched seams or recovery helper in the surviving process.
            results = write_indexes_from_evidence(
                evidence_path=evidence,
                source_pack_root=source_root,
                run_id="run-fixture",
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "existing")
            card, _ = load_and_validate_canonical_card_index(**readback)
            self.assertEqual(card.payload["index_state"]["phase"], "observed")
            self.assertEqual(index_path.read_bytes(), index_bytes)
            for path, payload in retained.items():
                self.assertEqual(path.read_bytes(), payload)
            for path, expected in protected.items():
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(), expected
                )
            after_retry = snapshot(root)
            transaction_ref = transaction.relative_to(root).as_posix()
            transaction_parent = transaction.parent.relative_to(root).as_posix()
            emit(
                {
                    "schema": "millefeuille-f05-receipt/v1",
                    "test": self.id(),
                    "source_sha256": source_binding(
                        Path(__file__).resolve().parents[1]
                    ),
                    "runtime": runtime_identity(),
                    "fault": {
                        "seam": seam,
                        "returncode": process.returncode,
                        "worker_sha256": hashlib.sha256(
                            CRASH_WORKER.encode()
                        ).hexdigest(),
                        "position": "original seam returned, then os._exit(71)",
                    },
                    "before": before,
                    "after_death": after_death,
                    "after_retry": after_retry,
                    "paths": {
                        "card": card_path.relative_to(root).as_posix(),
                        "index": index_path.relative_to(root).as_posix(),
                        "recovery_index": recovery_index.relative_to(root).as_posix(),
                        "transaction": transaction_ref,
                    },
                    "protected_sha256": {
                        path.relative_to(root).as_posix(): digest
                        for path, digest in protected.items()
                    },
                    "custody": {
                        "transaction_files": sorted(
                            path.relative_to(root).as_posix() for path in retained
                        ),
                        "journal": {
                            "kind": "transaction file inventory, not a write journal",
                            "journal_named_paths": sorted(
                                p
                                for p in after_death["files"]
                                if p.startswith(transaction_ref + "/")
                                and "journal" in Path(p).name.lower()
                            ),
                        },
                        "transaction_counts": {
                            stage: sum(
                                p.startswith(transaction_parent + "/")
                                and p.count("/") == transaction_ref.count("/")
                                for p in snap["directories"]
                            )
                            for stage, snap in (
                                ("before", before),
                                ("after_death", after_death),
                                ("after_retry", after_retry),
                            )
                        },
                    },
                    "readback": {
                        "after_death": initial_readback,
                        "after_retry": card.payload["index_state"]["phase"],
                    },
                    "retry_statuses": [result.status for result in results],
                    "net_effects": {
                        "death": effects(before, after_death),
                        "retry": effects(after_death, after_retry),
                    },
                    "limits": "net snapshots, not syscall counts or power-loss "
                    "durability, arbitrary crash windows or guard qualification",
                }
            )
