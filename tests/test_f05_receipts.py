"""Pure receipt tests: no product imports or fault/recovery execution."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.fixtures.evidence_transport import canonical
from tests.fixtures.f05_receipts import accept, effects, snapshot, validate


class TestF05Receipts(unittest.TestCase):
    def _receipt(self, root):
        (root / "card.json").write_bytes(b"{}")
        before = snapshot(root)
        (root / "txn").mkdir()
        (root / "txn/index.json").write_bytes(b"{}")
        (root / "index.json").write_bytes(b"{}")
        death = snapshot(root)
        return {
            "schema": "millefeuille-f05-receipt/v1",
            "source_sha256": {},
            "fault": {"returncode": 71, "seam": "_ensure_expected_index"},
            "before": before,
            "after_death": death,
            "after_retry": death,
            "paths": {
                "card": "card.json",
                "index": "index.json",
                "recovery_index": "txn/index.json",
                "transaction": "txn",
            },
            "protected_sha256": {},
            "custody": {
                "transaction_files": ["txn/index.json"],
                "transaction_counts": {"before": 0, "after_death": 1, "after_retry": 1},
            },
            "readback": {
                "after_death": "refused:MillefeuilleContractError",
                "after_retry": "observed",
            },
            "retry_statuses": ["existing"],
            "net_effects": {
                "death": effects(before, death),
                "retry": effects(death, death),
            },
        }

    def test_schema_integrity_and_count_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt = self._receipt(Path(temporary))
            validate(receipt)
            for field in ("schema", "bytes", "count", "escape", "alias"):
                with self.subTest(field=field):
                    bad = copy.deepcopy(receipt)
                    if field == "schema":
                        bad["schema"] = "wrong"
                    elif field == "bytes":
                        bad["after_death"]["files"]["card.json"]["sha256"] = "0" * 64
                    elif field == "count":
                        bad["net_effects"]["retry"]["created_files"] = 1
                    elif field == "escape":
                        bad["before"]["files"]["../escape"] = bad["before"][
                            "files"
                        ].pop("card.json")
                    else:
                        files = bad["after_death"]["files"]
                        files["txn/index.json"]["identity"] = files["index.json"][
                            "identity"
                        ]
                    with self.assertRaises(ValueError):
                        validate(bad)

    def test_blank_synthetic_binding_is_not_archive_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt = self._receipt(Path(temporary))
            validate(receipt)  # low-level only, deliberately blank synthetic bindings
            with self.assertRaises(ValueError):
                accept(
                    canonical(receipt),
                    source_sha256={},
                    runtime={},
                    method="",
                    worker_sha256="",
                )

    def test_acceptance_nonoptional_custody_and_bindings(self):
        from tests.fixtures.f05_receipts import METHOD_SEAMS, SOURCE_PATHS

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run/cards").mkdir(parents=True)
            (root / "run/cards/card.json").write_bytes(b"{}")
            (root / "input.pdf").write_bytes(b"fixture")
            before = snapshot(root)
            (root / "run/txn").mkdir()
            (root / "run/txn/index.json").write_bytes(b"{}")
            (root / "run/index.json").write_bytes(b"{}")
            death = snapshot(root)
            # Reuse synthetic shape, then substitute the full captured fixture.
            other = root / "shape"
            other.mkdir()
            receipt = self._receipt(other)
            receipt.update(before=before, after_death=death, after_retry=death)
            receipt["paths"] = {
                "card": "run/cards/card.json",
                "index": "run/index.json",
                "transaction": "run/txn",
                "recovery_index": "run/txn/index.json",
            }
            receipt["protected_sha256"] = {
                "input.pdf": before["files"]["input.pdf"]["sha256"]
            }
            receipt["custody"]["transaction_files"] = ["run/txn/index.json"]
            receipt["custody"]["journal"] = {
                "kind": "transaction file inventory, not a write journal",
                "journal_named_paths": [],
            }
            receipt["net_effects"] = {
                "death": effects(before, death),
                "retry": effects(death, death),
            }
            runtime = {
                "GITHUB_SHA": "a" * 40,
                "GITHUB_RUN_ID": "1",
                "GITHUB_RUN_ATTEMPT": "1",
                "RUNNER_OS": "Linux",
                "python": "3.12",
            }
            method = next(iter(METHOD_SEAMS))
            hashes = dict.fromkeys(SOURCE_PATHS, "b" * 64)
            receipt.update(runtime=runtime, test=method, source_sha256=hashes)
            receipt["fault"].update(
                worker_sha256="c" * 64,
                position="original seam returned, then os._exit(71)",
            )

            def check(value):
                return accept(
                    canonical(value),
                    source_sha256=hashes,
                    runtime=runtime,
                    method=method,
                    worker_sha256="c" * 64,
                )

            check(receipt)  # synthetic helper coverage only, not CI evidence
            for field in ("protected", "transaction", "source", "runtime", "journal"):
                bad = copy.deepcopy(receipt)
                if field == "protected":
                    bad["protected_sha256"] = {}
                elif field == "transaction":
                    bad["custody"]["transaction_files"] = []
                elif field == "source":
                    bad["source_sha256"] = {}
                elif field == "runtime":
                    bad["runtime"] = {}
                else:
                    bad["custody"]["journal"]["journal_named_paths"] = ["missing"]
                with self.subTest(field=field), self.assertRaises(ValueError):
                    check(bad)

    def test_bounds_and_unknown_data_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "card.json").write_bytes(b"12345")
            with (
                patch("tests.fixtures.f05_receipts.MAX_TOTAL_BYTES", 4),
                self.assertRaises(ValueError),
            ):
                snapshot(root)
            with (
                patch("tests.fixtures.f05_receipts.MAX_FILES", 0),
                self.assertRaises(ValueError),
            ):
                snapshot(root)
            (root / "unknown.bin").write_bytes(b"data")
            with self.assertRaises(ValueError):
                snapshot(root)

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "card.json"
            target.write_bytes(b"{}")
            try:
                (root / "link.json").symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaises(ValueError):
                snapshot(root)
