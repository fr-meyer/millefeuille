"""Receipt integrity tests and real fixture inventory, without crash execution."""

import base64
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
            (root / "run/index-transactions/txn").mkdir(parents=True)
            (root / "run/index-transactions/txn/index.json").write_bytes(b"{}")
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
                "transaction": "run/index-transactions/txn",
                "recovery_index": "run/index-transactions/txn/index.json",
            }
            receipt["protected_sha256"] = {
                "input.pdf": before["files"]["input.pdf"]["sha256"]
            }
            receipt["custody"]["transaction_files"] = [
                "run/index-transactions/txn/index.json"
            ]
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
            for field in (
                "protected",
                "transaction",
                "source",
                "runtime",
                "journal",
                "transaction_count",
            ):
                bad = copy.deepcopy(receipt)
                if field == "protected":
                    bad["protected_sha256"] = {}
                elif field == "transaction":
                    bad["custody"]["transaction_files"] = []
                elif field == "source":
                    bad["source_sha256"] = {}
                elif field == "runtime":
                    bad["runtime"] = {}
                elif field == "transaction_count":
                    bad["custody"]["transaction_counts"]["after_death"] = 2
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

    def test_exact_synthetic_run_lock_capture_and_guards(self):
        lock_ref = (
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/"
            "run-fixture/.card-index.lock"
        )
        for payload in (b"", b"\0", b"retained-lock-bytes"):
            with (
                self.subTest(payload=payload),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                lock = root / lock_ref
                lock.parent.mkdir(parents=True)
                lock.write_bytes(payload)
                image = snapshot(root)["files"][lock_ref]
                self.assertEqual(image["size"], len(payload))
                self.assertEqual(base64.b64decode(image["bytes_b64"]), payload)
                self.assertEqual(
                    image["identity"], [lock.stat().st_dev, lock.stat().st_ino]
                )
                with (
                    patch("tests.fixtures.f05_receipts.MAX_TOTAL_BYTES", -1),
                    self.assertRaises(ValueError),
                ):
                    snapshot(root)
        for ref in (
            ".card-index.lock",
            "run/.card-index.lock",
            lock_ref + ".extra",
            lock_ref.replace("source-packs/zotero/", "source-packs/"),
            lock_ref.replace("source-packs/zotero/", "source-packs/other/"),
            lock_ref.replace("zotero-ITEM1/", "extra/zotero-ITEM1/"),
            lock_ref.replace("run-fixture", "other"),
            lock_ref.replace(".card-index.lock", "other.lock"),
        ):
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / ref
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"\0")
                with self.assertRaises(ValueError):
                    snapshot(root)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "card.json"
            target.write_bytes(b"{}")
            lock = root / lock_ref
            lock.parent.mkdir(parents=True)
            try:
                lock.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaises(ValueError):
                snapshot(root)

    def test_snapshot_lock_path_from_real_fixture_producer(self):
        # Only future approved CI runs execute this. The same fixture producer
        # feeds both original process-death tests; no toy path reimplementation.
        from tests.test_millefeuille_source_pack_writer import (
            _prepare_card_index_fixture,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, run_dir, _, _ = _prepare_card_index_fixture(temporary)
            before = snapshot(root)
            lock = run_dir / ".card-index.lock"
            lock.write_bytes(b"\0")
            relative = lock.relative_to(root).as_posix()
            after = snapshot(root)
            self.assertNotIn(relative, before["files"])
            self.assertIn(relative, after["files"])
            self.assertEqual(after["file_count"], before["file_count"] + 1)
            image = after["files"][relative]
            self.assertEqual(base64.b64decode(image["bytes_b64"]), b"\0")
            self.assertEqual(image["size"], 1)
            self.assertEqual(
                image["identity"], [lock.stat().st_dev, lock.stat().st_ino]
            )
            for name, original in before["files"].items():
                self.assertEqual(after["files"][name], original)
