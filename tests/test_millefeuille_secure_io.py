"""Focused cross-platform tests for fail-closed artifact byte reads."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from millefeuille.domain import secure_io
from millefeuille.domain.millefeuille import MillefeuilleContractError
from tests.platform_capabilities import requires_pinned_root_reads


def _stat_like(
    source: os.stat_result,
    *,
    inode: int | None = None,
    size: int | None = None,
    mtime_ns: int | None = None,
    ctime_ns: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        st_dev=source.st_dev,
        st_ino=source.st_ino if inode is None else inode,
        st_mode=source.st_mode,
        st_nlink=source.st_nlink,
        st_size=source.st_size if size is None else size,
        st_mtime_ns=source.st_mtime_ns if mtime_ns is None else mtime_ns,
        st_ctime_ns=source.st_ctime_ns if ctime_ns is None else ctime_ns,
        st_file_attributes=getattr(source, "st_file_attributes", 0),
        st_reparse_tag=getattr(source, "st_reparse_tag", 0),
    )


class TestMillefeuilleSecureIo(unittest.TestCase):
    def test_portable_byte_read_is_an_exact_binary_round_trip(self):
        payload = b"line-one\r\nline-two\ncontrol-z:\x1a\x00\xff" + bytes(range(256))
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(payload)

            with mock.patch.object(
                secure_io,
                "_supports_no_follow",
                return_value=False,
            ):
                actual = secure_io.read_bytes_no_follow(path, "binary artifact")

        self.assertEqual(actual, payload)

    def test_portable_bounded_read_rejects_opened_size_before_payload_read(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "oversized.bin"
            path.write_bytes(b"oversized")

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(secure_io.os, "read") as read,
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "exceeds 8 bytes",
                ),
            ):
                secure_io.read_bytes_no_follow(
                    path,
                    "bounded artifact",
                    max_bytes=8,
                )

        read.assert_not_called()

    def test_portable_bounded_read_accepts_the_exact_boundary(self):
        payload = b"boundary"
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "boundary.bin"
            path.write_bytes(payload)

            with mock.patch.object(
                secure_io,
                "_supports_no_follow",
                return_value=False,
            ):
                actual = secure_io.read_bytes_no_follow(
                    path,
                    "bounded artifact",
                    max_bytes=len(payload),
                )

        self.assertEqual(actual, payload)

    def test_portable_bounded_read_rejects_growth_during_read(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "growing.bin"
            path.write_bytes(b"ok")
            reads = iter((b"ok", b"x"))

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(secure_io.os, "read", side_effect=reads) as read,
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "exceeds 2 bytes",
                ),
            ):
                secure_io.read_bytes_no_follow(
                    path,
                    "growing artifact",
                    max_bytes=2,
                )

        self.assertEqual([call.args[1] for call in read.call_args_list], [3, 1])

    def test_regular_file_read_flags_include_binary_mode_when_available(self):
        binary_flag = 1 << 27
        with mock.patch.object(
            secure_io.os,
            "O_BINARY",
            binary_flag,
            create=True,
        ):
            flags = secure_io._regular_file_read_flags(no_follow=False)

        self.assertEqual(flags & binary_flag, binary_flag)

    def test_windows_portable_read_uses_write_excluding_handle(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            payload = b"stable windows bytes"
            path.write_bytes(payload)
            expected_stat = path.stat()
            fd = os.open(path, os.O_RDONLY)

            with (
                mock.patch.object(secure_io, "_WINDOWS", True),
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    secure_io,
                    "_open_windows_locked_regular_file_fd",
                    return_value=(fd, expected_stat),
                ) as locked_open,
            ):
                actual = secure_io.read_bytes_no_follow(path, "windows artifact")

        self.assertEqual(actual, payload)
        locked_open.assert_called_once_with(
            path,
            label="windows artifact",
            expected_stat=mock.ANY,
        )

    def test_windows_ctime_drift_does_not_change_stability_snapshot(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(b"stable")
            before = path.stat()
        after = _stat_like(before, ctime_ns=before.st_ctime_ns + 1)

        self.assertEqual(
            secure_io._stat_object_identity(before),
            secure_io._stat_object_identity(after),
        )
        with mock.patch.object(secure_io, "_WINDOWS", True):
            self.assertEqual(
                secure_io._stat_stability_snapshot(before),
                secure_io._stat_stability_snapshot(after),
            )

    def test_posix_ctime_drift_remains_a_mutation_signal(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(b"stable")
            before = path.stat()
        after = _stat_like(before, ctime_ns=before.st_ctime_ns + 1)

        with mock.patch.object(secure_io, "_WINDOWS", False):
            self.assertNotEqual(
                secure_io._stat_stability_snapshot(before),
                secure_io._stat_stability_snapshot(after),
            )

    def test_portable_read_rejects_post_open_path_replacement(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            path = root / "artifact.bin"
            replacement = root / "replacement.bin"
            payload = b"same-size"
            path.write_bytes(payload)
            replacement.write_bytes(payload)
            expected = path.stat()
            replaced = _stat_like(expected, inode=replacement.stat().st_ino)
            real_inspect = secure_io._portable_lstat_regular_file
            inspections = 0

            def inspect(candidate: Path, *, label: str) -> os.stat_result:
                nonlocal inspections
                inspections += 1
                if inspections == 3:
                    return replaced
                return real_inspect(candidate, label=label)

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    secure_io,
                    "_portable_lstat_regular_file",
                    side_effect=inspect,
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "changed while read",
                ),
            ):
                secure_io.read_bytes_no_follow(path, "replaceable artifact")

        self.assertEqual(inspections, 3)

    def test_portable_read_rejects_in_place_mutation_snapshot(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(b"stable")
            expected = path.stat()
            mutated = _stat_like(
                expected,
                size=expected.st_size + 1,
                mtime_ns=expected.st_mtime_ns + 1,
            )
            real_inspect = secure_io._portable_lstat_regular_file
            inspections = 0

            def inspect(candidate: Path, *, label: str) -> os.stat_result:
                nonlocal inspections
                inspections += 1
                if inspections == 3:
                    return mutated
                return real_inspect(candidate, label=label)

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    secure_io,
                    "_portable_lstat_regular_file",
                    side_effect=inspect,
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "changed while read",
                ),
            ):
                secure_io.read_bytes_no_follow(path, "mutable artifact")

    def test_portable_read_keeps_the_exact_size_gate(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(b"complete")
            reads = iter((b"short", b""))

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(secure_io.os, "read", side_effect=reads),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "changed while read",
                ),
            ):
                secure_io.read_bytes_no_follow(path, "truncated artifact")

    def test_portable_read_fails_closed_without_stable_inode_identity(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(b"stable")
            unavailable = _stat_like(path.stat(), inode=0)

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    secure_io,
                    "_portable_lstat_regular_file",
                    return_value=unavailable,
                ),
                mock.patch.object(secure_io.os, "open") as open_file,
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "does not expose stable file identity",
                ),
            ):
                secure_io.read_bytes_no_follow(path, "identity-less artifact")

        open_file.assert_not_called()

    def test_windows_reparse_points_are_rejected_as_read_targets(self):
        anchor_stat = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=0,
            st_reparse_tag=0,
        )
        target_stat = SimpleNamespace(
            st_mode=stat.S_IFREG,
            st_file_attributes=secure_io._WINDOWS_REPARSE_POINT,
            st_reparse_tag=0,
        )
        with (
            mock.patch.object(secure_io, "_WINDOWS", True),
            mock.patch.object(
                secure_io.os,
                "lstat",
                side_effect=(anchor_stat, target_stat),
            ),
            self.assertRaisesRegex(
                MillefeuilleContractError,
                "symbolic link or reparse point",
            ),
        ):
            secure_io._portable_lstat_regular_file(
                Path("artifact.bin"),
                label="reparse artifact",
            )

    def test_windows_reparse_points_are_rejected_in_nested_parent_paths(self):
        anchor_stat = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=0,
            st_reparse_tag=0,
        )
        reparse_parent_stat = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=secure_io._WINDOWS_REPARSE_POINT,
            st_reparse_tag=0,
        )
        with (
            mock.patch.object(secure_io, "_WINDOWS", True),
            mock.patch.object(
                secure_io.os,
                "lstat",
                side_effect=(anchor_stat, reparse_parent_stat),
            ) as inspect,
            self.assertRaisesRegex(
                MillefeuilleContractError,
                "path must not contain symbolic links, reparse points",
            ),
        ):
            secure_io._portable_lstat_regular_file(
                Path("parent") / "artifact.bin",
                label="nested reparse artifact",
            )

        self.assertEqual(
            inspect.call_args_list,
            [mock.call(Path(".")), mock.call(Path("parent"))],
        )

    def test_relative_portable_read_rejects_a_reparse_point_anchor(self):
        reparse_anchor_stat = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=secure_io._WINDOWS_REPARSE_POINT,
            st_reparse_tag=0,
        )
        with (
            mock.patch.object(secure_io, "_WINDOWS", True),
            mock.patch.object(
                secure_io.os,
                "lstat",
                return_value=reparse_anchor_stat,
            ) as inspect,
            self.assertRaisesRegex(
                MillefeuilleContractError,
                "path must not contain symbolic links, reparse points",
            ),
        ):
            secure_io._portable_lstat_regular_file(
                Path("artifact.bin"),
                label="relative reparse artifact",
            )

        inspect.assert_called_once_with(Path("."))

    @unittest.skipUnless(
        os.name == "nt",
        "requires Windows kernel share-mode enforcement",
    )
    def test_windows_read_blocks_same_size_mtime_preserving_mutation(self):
        payload = b"A" * 131072
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(payload)
            before = path.stat()
            real_read = os.read
            mutation_attempts: list[str] = []

            def read_with_mutation_attempt(fd: int, size: int) -> bytes:
                chunk = real_read(fd, size)
                if not mutation_attempts:
                    try:
                        with path.open("r+b", buffering=0) as stream:
                            stream.seek(0)
                            stream.write(b"B" * len(payload))
                        os.utime(
                            path,
                            ns=(before.st_atime_ns, before.st_mtime_ns),
                        )
                    except OSError as exc:
                        if (
                            getattr(exc, "winerror", None) not in {5, 32, 33}
                            and exc.errno != errno.EACCES
                        ):
                            raise
                        mutation_attempts.append("blocked")
                    else:
                        mutation_attempts.append("mutated")
                return chunk

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    secure_io.os,
                    "read",
                    side_effect=read_with_mutation_attempt,
                ),
            ):
                actual = secure_io.read_bytes_no_follow(path, "locked artifact")

        self.assertEqual(mutation_attempts, ["blocked"])
        self.assertEqual(actual, payload)


if __name__ == "__main__":
    unittest.main()


class BudgetedDirectoryTests(unittest.TestCase):
    """Exercise public readers, real directory iterators and the actual OS seam."""

    @requires_pinned_root_reads
    def test_limits_snapshot_revalidation_and_bounded_entry_stats(self):
        import os
        from unittest.mock import patch

        from millefeuille.domain.evidence_source_adapter import ReadBudget
        from millefeuille.domain.millefeuille import MillefeuilleContractError
        from millefeuille.domain.secure_io import RootArtifactReader
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(512):
                (root / str(n)).touch()
            counts = {"next": 0, "stat": 0}
            real_scan = os.scandir

            class Entry:
                def __init__(self, entry):
                    self.entry = entry
                    self.name = entry.name

                def stat(self, **kwargs):
                    counts["stat"] += 1
                    return self.entry.stat(**kwargs)

            class Scan:
                def __init__(self, fd):
                    self.iterator = real_scan(fd)

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.iterator.close()

                def __next__(self):
                    counts["next"] += 1
                    return Entry(next(self.iterator))

            budget = ReadBudget(max_opens=20000, max_scans=20000)
            with semantic_read_scope(budget), RootArtifactReader(root) as reader:  # noqa: SIM117 (explicit budget lifetime)
                with patch("os.scandir", side_effect=Scan):
                    self.assertEqual(
                        len(reader.list_directory_names(root, "fixture")), 512
                    )
                    self.assertEqual(counts, {"next": 513, "stat": 512})
                    reader.revalidate_snapshot()
                    self.assertEqual(counts, {"next": 1026, "stat": 1024})
                    self.assertEqual(budget.scans, 1028)  # scan-open + 512 + EOF, twice
                    with patch("os.dup") as dup:  # noqa: SIM117 (explicit budget lifetime)
                        with self.assertRaisesRegex(
                            MillefeuilleContractError, "entry_limit changed"
                        ):
                            reader.list_directory_names(
                                root, "fixture", entry_limit=513
                            )
                        dup.assert_not_called()
                    (root / "added").touch()
                    counts.update(next=0, stat=0)
                    with self.assertRaisesRegex(
                        MillefeuilleContractError, "512 entries"
                    ):
                        reader.revalidate_snapshot()
                    self.assertEqual(counts, {"next": 513, "stat": 512})

    @requires_pinned_root_reads
    def test_reviewed_root_513_does_not_expand_generic_or_nested_limit(self):
        from millefeuille.domain.evidence_source_adapter import ReadBudget
        from millefeuille.domain.millefeuille import MillefeuilleContractError
        from millefeuille.domain.reviewed_summary_handoff import BudgetedPackageReader
        from millefeuille.domain.secure_io import RootArtifactReader
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(512):
                (root / str(n)).touch()
            (root / "manifest.json").write_text("{}")
            budget = ReadBudget(max_opens=20000, max_scans=20000)
            with semantic_read_scope(budget), BudgetedPackageReader(root) as reader:
                self.assertEqual(
                    len(reader.list_directory_names(root, "reviewed", entry_limit=513)),
                    513,
                )
                reader.revalidate_snapshot()
                self.assertEqual(reader._directory_snapshots[Path(".")][3], 513)
                (root / "extra-payload").touch()
                with self.assertRaisesRegex(MillefeuilleContractError, "513 entries"):
                    reader.revalidate_snapshot()
            with RootArtifactReader(root) as reader:  # noqa: SIM117 (explicit budget lifetime)
                with self.assertRaisesRegex(MillefeuilleContractError, "512 entries"):
                    reader.list_directory_names(root, "generic")
            # A different nested directory has no inherited reviewed-root capability.
            (root / "extra-payload").unlink()
            (root / "manifest.json").unlink()
            nested = root / "nested"
            nested.mkdir()
            for n in range(513):
                (nested / str(n)).touch()
            with RootArtifactReader(root) as reader:  # noqa: SIM117 (explicit budget lifetime)
                with self.assertRaisesRegex(MillefeuilleContractError, "512 entries"):
                    reader.list_directory_names(nested, "nested")

    def test_entry_limit_validation_before_any_io(self):
        from unittest.mock import patch

        from millefeuille.domain.millefeuille import MillefeuilleContractError
        from millefeuille.domain.secure_io import RootArtifactReader

        reader = RootArtifactReader("/not-opened")
        with patch("os.dup") as dup, patch("os.scandir") as scan:
            for invalid in [True, False, 0, -1, 514, 512.0, None, float("inf")]:
                with self.subTest(invalid=invalid):  # noqa: SIM117 (explicit budget lifetime)
                    with self.assertRaises(MillefeuilleContractError):
                        reader.list_directory_names(
                            "/not-opened", "invalid", entry_limit=invalid
                        )
            dup.assert_not_called()
            scan.assert_not_called()

    @requires_pinned_root_reads
    def test_default_scan_budget_cannot_finish_two_512_entry_scans(self):
        from millefeuille.domain.evidence_source_adapter import (
            BudgetExhaustedError,
            ReadBudget,
        )
        from millefeuille.domain.secure_io import RootArtifactReader
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        with tempfile.TemporaryDirectory() as tmp:
            for n in range(512):
                Path(tmp, str(n)).touch()
            budget = ReadBudget(max_opens=20000)  # retain default 1024 scans
            with semantic_read_scope(budget), RootArtifactReader(tmp) as reader:
                reader.list_directory_names(tmp, "fixture")
                self.assertEqual(budget.scans, 514)
                with self.assertRaises(BudgetExhaustedError):
                    reader.revalidate_snapshot()
                self.assertTrue(budget.exhausted)

    @requires_pinned_root_reads
    def test_exhausted_snapshot_has_zero_noncleanup_io(self):
        from unittest.mock import patch

        from millefeuille.domain.evidence_source_adapter import (
            BudgetExhaustedError,
            ReadBudget,
        )
        from millefeuille.domain.secure_io import RootArtifactReader
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "one").write_bytes(b"one")
            budget = ReadBudget()
            with semantic_read_scope(budget), RootArtifactReader(tmp) as reader:
                reader.list_directory_names(tmp, "fixture")
                with self.assertRaises(BudgetExhaustedError):
                    budget.debit(read_bytes=budget.max_read_bytes + 1)
                with (
                    patch("os.open") as opened,
                    patch("os.fstat") as fst,
                    patch("os.scandir") as scan,
                    patch("os.dup") as dup,
                ):
                    with self.assertRaises(BudgetExhaustedError):
                        reader.revalidate_snapshot()
                    for spy in [opened, fst, scan, dup]:
                        spy.assert_not_called()

    @requires_pinned_root_reads
    def test_directory_iterator_and_entry_stat_are_predebited_on_every_axis(self):
        import os
        from unittest.mock import patch

        from millefeuille.domain.evidence_source_adapter import (
            BudgetExhaustedError,
            ReadBudget,
        )
        from millefeuille.domain.secure_io import RootArtifactReader
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "one").touch()
            fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for limits, expected_next in [
                    ({"max_scans": 1}, 0),
                    ({"max_read_bytes": 1}, 1),
                    ({"max_opens": 1}, 1),
                ]:
                    counts = {"next": 0, "stat": 0}
                    budget = ReadBudget(**limits)
                    real_scan = os.scandir

                    class Entry:
                        name = "one"

                        def stat(self, *, counts=counts, **kwargs):
                            counts["stat"] += 1
                            self.fail_if_called = True
                            raise AssertionError("stat after exhausted budget")

                    class Scan:
                        def __init__(self, fd, real_scan=real_scan):
                            self.scan = real_scan(fd)

                        def __enter__(self):
                            return self

                        def __exit__(self, *args):
                            self.scan.close()

                        def __next__(self, counts=counts):
                            counts["next"] += 1
                            next(self.scan)
                            return Entry()

                    with (
                        self.subTest(limits=limits),
                        semantic_read_scope(budget),
                        patch("os.scandir", side_effect=Scan),
                    ):
                        with self.assertRaises(BudgetExhaustedError):
                            RootArtifactReader._directory_entries(
                                fd, path=Path(tmp), label="fixture"
                            )
                        self.assertEqual(counts, {"next": expected_next, "stat": 0})
                        self.assertTrue(budget.exhausted)
            finally:
                os.close(fd)


@requires_pinned_root_reads
class OutputPlannerBudgetTests(unittest.TestCase):
    def test_real_before_images_share_ledger_and_protect_missing_outputs(self):
        from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
        from millefeuille.domain.evidence_source_adapter import ReadBudget
        from millefeuille.domain.secure_io import RootArtifactReader
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "reports").mkdir()
            (root / "stage-manifest.json").write_bytes(b"manifest before")
            (root / "artifact-index.json").write_bytes(b"index before")
            outputs = {
                "stage-manifest.json": b"manifest after",
                "artifact-index.json": b"index after",
                **{"reports/" + str(n) + ".json": b"{}" for n in range(4)},
            }
            ledger = ReadBudget()
            with semantic_read_scope(ledger), RootArtifactReader(root) as reader:
                plan = plan_exact_outputs(
                    root=root, outputs=outputs, artifact_reader=reader
                )
                self.assertIs(reader.budget_identity, ledger)
                self.assertGreater(
                    ledger.read_bytes, len(b"manifest beforeindex before")
                )
                self.assertGreater(ledger.opens, 10)
                self.assertEqual(len(plan), 6)
                self.assertEqual(sum(p.before_bytes is not None for p in plan), 2)
                self.assertEqual(len(reader._missing_file_snapshots), 4)
            self.assertEqual(
                (root / "stage-manifest.json").read_bytes(), b"manifest before"
            )

    def test_exhausted_or_foreign_capability_zero_actual_planner_io(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextvars import copy_context
        from unittest.mock import patch

        from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
        from millefeuille.domain.evidence_source_adapter import (
            BudgetExhaustedError,
            ReadBudget,
        )
        from millefeuille.domain.millefeuille import MillefeuilleContractError
        from millefeuille.domain.secure_io import RootArtifactReader
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ledger = ReadBudget()
            with semantic_read_scope(ledger), RootArtifactReader(root) as reader:
                context = copy_context()
                with ThreadPoolExecutor(max_workers=1) as pool:  # noqa: SIM117 (explicit I/O boundary)
                    with patch("os.open") as opened, patch("os.stat") as metadata:
                        with self.assertRaises(MillefeuilleContractError):
                            pool.submit(
                                context.run,
                                plan_exact_outputs,
                                root=root,
                                outputs={"new": b"x"},
                                artifact_reader=reader,
                            ).result()
                        opened.assert_not_called()
                        metadata.assert_not_called()
                with self.assertRaises(BudgetExhaustedError):
                    ledger.debit(opens=ledger.max_opens + 1)
                with (
                    patch("os.open") as opened,
                    patch("os.fstat") as fst,
                    patch("pathlib.Path.exists") as exists,
                    patch("pathlib.Path.lstat") as lst,
                ):
                    with self.assertRaises(BudgetExhaustedError):
                        plan_exact_outputs(
                            root=root, outputs={"new": b"x"}, artifact_reader=reader
                        )
                    for spy in [opened, fst, exists, lst]:
                        spy.assert_not_called()
            with patch("os.open") as opened:
                with self.assertRaisesRegex(MillefeuilleContractError, "capability"):
                    plan_exact_outputs(
                        root=root, outputs={"new": b"x"}, artifact_reader=reader
                    )
                opened.assert_not_called()

    def test_before_image_growth_refuses_with_only_reserved_sentinel_read(self):
        import os
        from unittest.mock import patch

        from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
        from millefeuille.domain.evidence_source_adapter import ReadBudget
        from millefeuille.domain.millefeuille import MillefeuilleContractError
        from millefeuille.domain.secure_io import RootArtifactReader
        from millefeuille.domain.semantic_acceptance import semantic_read_scope

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "a"
            target.write_bytes(b"old")
            ledger = ReadBudget()
            actual_read = os.read
            sizes = []

            def grow(fd, size):
                sizes.append(size)
                with target.open("ab") as output:
                    output.write(b"unreserved growth")
                return actual_read(fd, size)

            with semantic_read_scope(ledger), RootArtifactReader(root) as reader:
                with patch("os.read", side_effect=grow):  # noqa: SIM117 (explicit I/O boundary)
                    with self.assertRaises(MillefeuilleContractError):
                        plan_exact_outputs(
                            root=root,
                            outputs={"a": b"new", "z": b"later"},
                            artifact_reader=reader,
                        )
                self.assertEqual(sizes, [4])  # advertised 3 bytes + one reserved probe
                self.assertFalse((root / "z").exists())
