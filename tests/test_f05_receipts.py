"""Receipt integrity tests and real fixture inventory, without crash execution."""

import base64
import copy
import hashlib
from pathlib import Path, PurePosixPath
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

    def _accepted_receipt(self, os_name="Linux", seam="_ensure_expected_index"):
        from tests.fixtures.f05_receipts import METHOD_SEAMS, SOURCE_PATHS

        def image(payload, inode):
            return {
                "bytes_b64": base64.b64encode(payload).decode(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "identity": [1, inode],
            }

        # Producer path graph only. Bytes and identities are new synthetic data.
        run = "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/run-fixture"
        card = run + "/cards/paper-card.json"
        index = run + "/index/index-status.json"
        txn = run + "/recovery/card-index/txn"
        lock = run + "/.card-index.lock"
        recovery = txn + "/index-status.json"
        original = txn + (
            "/card-exchange.json" if os_name == "Linux" else "/displaced-card.json"
        )
        initial_paths = [
            "card-evidence.json",
            "full-paper.md",
            "hierarchical-summary-fixture.json",
            "index-evidence.json",
            "native-extraction-evidence.json",
            "native-fulltext.md",
            "ocr-extraction-evidence.json",
            "ocr-fulltext.md",
            "page-1.md",
            "paper-card-fixture.json",
            "paper-card.md",
            "recovered.pdf",
            "retrieval-index-fixture.json",
            "route-selection-evidence.json",
            "selected-fulltext.md",
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/run-fixture/cards/paper-card.json",
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/run-fixture/cards/paper-card.md",
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/run-fixture/summaries/hierarchical-summary.json",
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/run-fixture/summaries/texts/full-paper.md",
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/run-fixture/summaries/texts/page-1.md",
            "source-packs/zotero/zotero-ITEM1/extractions/mistral-ocr/evidence.json",
            "source-packs/zotero/zotero-ITEM1/extractions/mistral-ocr/fulltext.md",
            "source-packs/zotero/zotero-ITEM1/extractions/native/evidence.json",
            "source-packs/zotero/zotero-ITEM1/extractions/native/fulltext.md",
            "source-packs/zotero/zotero-ITEM1/manifest.json",
            "source-packs/zotero/zotero-ITEM1/selected/fulltext.md",
            "source-packs/zotero/zotero-ITEM1/selected/route.json",
            "source-packs/zotero/zotero-ITEM1/source.pdf",
            "source-packs/zotero/zotero-ITEM1/structure/structure.json",
            "structure-evidence.json",
            "structure-fixture.json",
            "summary-evidence.json",
        ]
        initial = {
            path: image(b"synthetic retained fixture", 100 + n)
            for n, path in enumerate(initial_paths)
        }
        initial[card] = image(b"planned", 1)
        dirs = [
            "source-packs",
            "source-packs/zotero",
            "source-packs/zotero/zotero-ITEM1",
            "source-packs/zotero/zotero-ITEM1/analyses",
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille",
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/run-fixture",
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/run-fixture/cards",
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/run-fixture/summaries",
            "source-packs/zotero/zotero-ITEM1/analyses/millefeuille/run-fixture/summaries/texts",
            "source-packs/zotero/zotero-ITEM1/extractions",
            "source-packs/zotero/zotero-ITEM1/extractions/mistral-ocr",
            "source-packs/zotero/zotero-ITEM1/extractions/native",
            "source-packs/zotero/zotero-ITEM1/pages",
            "source-packs/zotero/zotero-ITEM1/selected",
            "source-packs/zotero/zotero-ITEM1/structure",
        ]
        before = {"files": initial, "directories": dirs}
        death = copy.deepcopy(before)
        death["directories"] = sorted(
            dirs
            + [run + "/index", run + "/recovery", run + "/recovery/card-index", txn]
        )
        death["files"].update(
            {
                index: image(b"index", 3),
                recovery: image(b"index", 4),
                lock: image(b"\0", 6),
            }
        )
        if seam == "_atomic_capture_replace":
            death["files"][original] = copy.deepcopy(initial[card])
            death["files"][card] = image(b"observed", 5)
        retry = copy.deepcopy(death)
        retry["files"][original] = copy.deepcopy(initial[card])
        retry["files"][card] = image(b"observed", 5)
        runtime = {
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "RUNNER_OS": os_name,
            "python": "3.12",
        }
        method = next(m for m, value in METHOD_SEAMS.items() if value == seam)
        receipt = {
            "schema": "millefeuille-f05-receipt/v1",
            "source_sha256": dict.fromkeys(SOURCE_PATHS, "b" * 64),
            "runtime": runtime,
            "test": method,
            "fault": {
                "returncode": 71,
                "seam": seam,
                "worker_sha256": "c" * 64,
                "position": "original seam returned, then os._exit(71)",
            },
            "before": before,
            "after_death": death,
            "after_retry": retry,
            "paths": {
                "card": card,
                "index": index,
                "transaction": txn,
                "recovery_index": recovery,
            },
            "protected_sha256": {
                p: value["sha256"]
                for p, value in initial.items()
                if not p.startswith(run + "/")
            },
            "custody": {
                "transaction_files": sorted(
                    p for p in death["files"] if p.startswith(txn + "/")
                ),
                "transaction_counts": {"before": 0, "after_death": 1, "after_retry": 1},
                "journal": {
                    "kind": "transaction file inventory, not a write journal",
                    "journal_named_paths": [],
                },
            },
            "readback": {
                "after_death": "refused:MillefeuilleContractError"
                if seam == "_ensure_expected_index"
                else "accepted",
                "after_retry": "observed",
            },
            "retry_statuses": ["existing"],
        }
        self._refresh(receipt)
        return receipt

    def _refresh(self, receipt):
        for stage in ("before", "after_death", "after_retry"):
            snap = receipt[stage]
            snap["file_count"] = len(snap["files"])
            snap["total_bytes"] = sum(i["size"] for i in snap["files"].values())
            snap["directories"] = sorted(snap["directories"])
        receipt["net_effects"] = {
            "death": effects(receipt["before"], receipt["after_death"]),
            "retry": effects(receipt["after_death"], receipt["after_retry"]),
        }

    def _accept(self, receipt):
        return accept(
            canonical(receipt),
            source_sha256=receipt["source_sha256"],
            runtime=receipt["runtime"],
            method=receipt["test"],
            worker_sha256="c" * 64,
        )

    def test_acceptance_nonoptional_custody_and_bindings(self):
        receipt = self._accepted_receipt()
        hashes, runtime, method = (
            receipt[k] for k in ("source_sha256", "runtime", "test")
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

    def test_exact_stage_matrix_and_adversaries(self):
        for os_name in ("Linux", "Windows"):
            for seam in ("_ensure_expected_index", "_atomic_capture_replace"):
                receipt = self._accepted_receipt(os_name, seam)
                self._accept(receipt)  # synthetic only, not CI evidence
                txn = receipt["paths"]["transaction"]
                original = txn + (
                    "/card-exchange.json"
                    if os_name == "Linux"
                    else "/displaced-card.json"
                )
                recovery = receipt["paths"]["recovery_index"]
                card, index = (receipt["paths"][k] for k in ("card", "index"))
                for mode in (
                    "missing_old",
                    "missing_addition",
                    "extra_file",
                    "extra_dir",
                    "sibling",
                    "wrong_os",
                    "both_names",
                    "nested",
                    "death_dir",
                    "before_file",
                    "death_card",
                    "original_identity",
                    "original_bytes",
                    "original_hash",
                    "original_size",
                    "observed_original",
                    "old_identity",
                    "old_bytes",
                    "old_hash",
                    "old_size",
                    "index_alias",
                    "card_alias",
                    "retained_index_card_alias",
                    "death_extra",
                    "count",
                    "effect",
                    "journal",
                    "worker",
                    "position",
                    "source",
                    "runtime",
                    "seam",
                    "protected",
                    "traversal",
                ):
                    bad = copy.deepcopy(receipt)
                    death, retry = (
                        bad[k]["files"] for k in ("after_death", "after_retry")
                    )
                    if mode == "missing_old":
                        del retry[recovery]
                    elif mode == "missing_addition":
                        del retry[original]
                    elif mode in ("extra_file", "nested", "before_file", "death_extra"):
                        stage = (
                            "before"
                            if mode == "before_file"
                            else (
                                "after_death"
                                if mode == "death_extra"
                                else "after_retry"
                            )
                        )
                        name = txn + (
                            "/nested/extra.json" if mode == "nested" else "/extra.json"
                        )
                        bad[stage]["files"][name] = copy.deepcopy(retry[original])
                    elif mode in ("extra_dir", "sibling"):
                        bad["after_retry"]["directories"].append(
                            txn + "/extra" if mode == "extra_dir" else txn + "-sibling"
                        )
                    elif mode == "death_dir":
                        bad["after_death"]["directories"].remove(txn)
                    elif mode in ("wrong_os", "both_names"):
                        wrong = txn + (
                            "/displaced-card.json"
                            if os_name == "Linux"
                            else "/card-exchange.json"
                        )
                        retry[wrong] = copy.deepcopy(retry[original])
                        if mode == "wrong_os":
                            del retry[original]
                    elif mode == "death_card":
                        death[card]["identity"] = [1, 99]
                        if seam == "_atomic_capture_replace":
                            death[original]["identity"] = [1, 99]
                    elif mode.startswith("original_") or mode.startswith("old_"):
                        target = retry[
                            original if mode.startswith("original_") else recovery
                        ]
                        field = mode.rsplit("_", 1)[1]
                        if field == "identity":
                            target[field] = [1, 99]
                        elif field == "bytes":
                            payload = b"changed"
                            target.update(
                                bytes_b64=base64.b64encode(payload).decode(),
                                size=len(payload),
                                sha256=hashlib.sha256(payload).hexdigest(),
                            )
                        elif field == "hash":
                            target["sha256"] = "0" * 64
                        else:
                            target["size"] += 1
                    elif mode == "observed_original":
                        retry[original] = copy.deepcopy(retry[card])
                    elif mode in (
                        "index_alias",
                        "card_alias",
                        "retained_index_card_alias",
                    ):
                        target = original if mode == "card_alias" else recovery
                        canonical_path = index if mode == "index_alias" else card
                        retry[target]["identity"] = retry[canonical_path]["identity"][:]
                    elif mode == "count":
                        bad["custody"]["transaction_counts"]["after_retry"] = 2
                    elif mode == "effect":
                        pass
                    elif mode == "journal":
                        bad["custody"]["journal"]["journal_named_paths"] = ["missing"]
                    elif mode == "worker":
                        bad["fault"]["worker_sha256"] = "d" * 64
                    elif mode == "position":
                        bad["fault"]["position"] = "before seam"
                    elif mode == "source":
                        bad["source_sha256"] = {}
                    elif mode == "runtime":
                        bad["runtime"]["RUNNER_OS"] = "Darwin"
                    elif mode == "seam":
                        bad["fault"]["seam"] = "unknown"
                    elif mode == "protected":
                        bad["protected_sha256"] = {}
                    else:
                        retry[txn + "/../escape.json"] = copy.deepcopy(retry[original])
                    self._refresh(bad)
                    if mode == "effect":
                        bad["net_effects"]["retry"]["created_files"] += 1
                    with (
                        self.subTest(os=os_name, seam=seam, mode=mode),
                        self.assertRaises((ValueError, KeyError)),
                    ):
                        self._accept(bad)

    def test_producer_lock_and_index_directory_adversaries(self):
        for os_name in ("Linux", "Windows"):
            for seam in ("_ensure_expected_index", "_atomic_capture_replace"):
                receipt = self._accepted_receipt(os_name, seam)
                run = PurePosixPath(receipt["paths"]["card"]).parent.parent.as_posix()
                lock = run + "/.card-index.lock"
                txn = receipt["paths"]["transaction"]
                recovery = receipt["paths"]["recovery_index"]
                original = txn + (
                    "/card-exchange.json"
                    if os_name == "Linux"
                    else "/displaced-card.json"
                )
                self.assertNotIn(lock, receipt["before"]["files"])
                self.assertNotIn(run + "/index", receipt["before"]["directories"])
                self.assertEqual(len(receipt["before"]["files"]), 32)
                self.assertEqual(len(receipt["before"]["directories"]), 15)
                self.assertEqual(len(receipt["after_retry"]["files"]), 36)
                self.assertEqual(
                    len(receipt["after_death"]["files"]),
                    35 if seam == "_ensure_expected_index" else 36,
                )
                self.assertEqual(len(receipt["after_retry"]["directories"]), 19)
                for mode in (
                    "lock_missing_death",
                    "lock_missing_retry",
                    "lock_extra",
                    "lock_wrong_context",
                    "lock_initial",
                    "lock_bytes",
                    "lock_hash",
                    "lock_size",
                    "lock_identity",
                    "lock_card_alias",
                    "lock_index_alias",
                    "lock_retained_alias",
                    "index_missing",
                    "index_extra",
                    "index_wrong_parent",
                    "index_sibling",
                    "index_nested",
                    "index_initial",
                    "lock_nonzero_both",
                ):
                    bad = copy.deepcopy(receipt)
                    death, retry = (
                        bad[k]["files"] for k in ("after_death", "after_retry")
                    )
                    dirs = bad["after_retry"]["directories"]
                    if mode.startswith("lock_missing"):
                        del (death if mode.endswith("death") else retry)[lock]
                    elif mode in ("lock_extra", "lock_wrong_context"):
                        retry[run + "/cards/.card-index.lock"] = copy.deepcopy(
                            retry[lock]
                        )
                        if mode == "lock_wrong_context":
                            del retry[lock]
                    elif mode == "lock_initial":
                        bad["before"]["files"][lock] = copy.deepcopy(death[lock])
                    elif mode in ("lock_bytes", "lock_nonzero_both"):
                        payload = b"x"
                        retry[lock].update(
                            bytes_b64=base64.b64encode(payload).decode(),
                            sha256=hashlib.sha256(payload).hexdigest(),
                            size=1,
                        )
                        if mode == "lock_nonzero_both":
                            death[lock] = copy.deepcopy(retry[lock])
                    elif mode in ("lock_hash", "lock_size", "lock_identity"):
                        field = mode.split("_")[1]
                        retry[lock][{"hash": "sha256"}.get(field, field)] = (
                            "0" * 64
                            if field == "hash"
                            else (2 if field == "size" else [1, 999])
                        )
                    elif mode.endswith("alias"):
                        target = (
                            bad["paths"]["card"]
                            if mode == "lock_card_alias"
                            else bad["paths"]["index"]
                            if mode == "lock_index_alias"
                            else recovery
                        )
                        for images in (death, retry):
                            images[lock]["identity"] = images[target]["identity"][:]
                    elif mode == "index_missing":
                        dirs.remove(run + "/index")
                    elif mode == "index_initial":
                        bad["before"]["directories"].append(run + "/index")
                    else:
                        suffix = {
                            "index_extra": "/extra",
                            "index_wrong_parent": "-wrong",
                            "index_sibling": "-sibling",
                            "index_nested": "/nested",
                        }[mode]
                        dirs.append(run + "/index" + suffix)
                        if mode == "index_wrong_parent":
                            dirs.remove(run + "/index")
                    self._refresh(bad)
                    with (
                        self.subTest(os=os_name, seam=seam, mode=mode),
                        self.assertRaises(ValueError),
                    ):
                        self._accept(bad)
                # Lock may not alias the newly retained original at retry either.
                bad = copy.deepcopy(receipt)
                bad["after_retry"]["files"][lock]["identity"] = bad["after_retry"][
                    "files"
                ][original]["identity"][:]
                self._refresh(bad)
                with self.assertRaises(ValueError):
                    self._accept(bad)

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
