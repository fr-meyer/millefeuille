"""Atomic GPT publication commit checks using a disposable source pack."""

from __future__ import annotations

from dataclasses import replace
import os
import stat
import sys
import unittest
from unittest.mock import patch

from millefeuille.domain import paper_card_publication_fs as fs
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.paper_card_publication_bundle import (
    plan_gpt_card_publication_bundle,
)
from tests import test_millefeuille_paper_card_output_write_scope as scope_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@unittest.skipUnless(sys.platform == "linux", "requires Linux renameat2")
@requires_secure_nofollow_writes
class TestGptCardPublicationFilesystem(unittest.TestCase):
    def setUp(self):
        fixture = scope_tests.TestGptCardOutputWriteScope(
            "test_exact_bundle_and_preview_have_no_effects"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.bundle = plan_gpt_card_publication_bundle(**fixture.values)
        self.owner = patch.object(fs, "_ROOT_UID", os.geteuid())
        self.owner.start()
        self.addCleanup(self.owner.stop)

    def _commit(self, bundle=None):
        return fs.commit_prevalidated_gpt_card_bundle(
            bundle=bundle or self.bundle, source_pack_root=self.fixture.root
        )

    def test_atomic_commit_is_byte_exact_and_cannot_replace_run(self):
        result = self._commit()
        self.assertEqual(result.file_count, len(self.bundle.files))
        self.assertEqual(result.total_bytes, self.bundle.total_bytes)
        self.assertEqual(
            result.bundle_manifest_sha256, self.bundle.bundle_manifest_sha256
        )
        for item in self.bundle.files:
            path = self.fixture.root / item.ref
            self.assertEqual(path.read_bytes(), item.data)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        with self.assertRaises(fs.GptCardPublicationError) as caught:
            self._commit()
        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.cleanup_complete)
        parent = self.fixture.root / "analyses" / "millefeuille"
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
        self.assertEqual(
            stat.S_IMODE((parent / self.bundle.preview.run_id).stat().st_mode),
            0o755,
        )
        self.assertEqual(
            {entry.name for entry in parent.iterdir()},
            {self.bundle.preview.run_id, self.fixture.values["publication"].run_id},
        )

    def test_tampered_file_is_rejected_before_any_write(self):
        item = self.bundle.files[0]
        altered = replace(item, data=item.data + b"drift")
        with self.assertRaises(MillefeuilleContractError):
            self._commit(replace(self.bundle, files=(altered, *self.bundle.files[1:])))
        self.assertFalse(
            (
                self.fixture.root
                / "analyses"
                / "millefeuille"
                / self.bundle.preview.run_id
            ).exists()
        )

    def test_rename_failure_cleans_private_stage(self):
        with (
            patch.object(fs, "_rename_noreplace", side_effect=OSError("injected")),
            self.assertRaises(fs.GptCardPublicationError) as caught,
        ):
            self._commit()
        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.cleanup_complete)
        self.assertEqual(
            [
                p.name
                for p in (self.fixture.root / "analyses" / "millefeuille").iterdir()
            ],
            [self.fixture.values["publication"].run_id],
        )

    def test_post_rename_check_is_reported_uncertain(self):
        original = fs._require_same_path

        def fail_after_rename(path, held_fd):
            if path.name == self.bundle.preview.run_id:
                raise MillefeuilleContractError("injected post-rename failure")
            return original(path, held_fd)

        with (
            patch.object(fs, "_require_same_path", side_effect=fail_after_rename),
            self.assertRaises(fs.GptCardPublicationError) as caught,
        ):
            self._commit()
        self.assertTrue(caught.exception.committed)
        self.assertTrue(
            (
                self.fixture.root
                / "analyses"
                / "millefeuille"
                / self.bundle.preview.run_id
            ).is_dir()
        )

    def test_untrusted_publication_parent_is_rejected(self):
        parent = self.fixture.root / "analyses" / "millefeuille"
        parent.mkdir(parents=True, exist_ok=True)
        parent.chmod(0o777)
        with self.assertRaises(fs.GptCardPublicationError) as caught:
            self._commit()
        self.assertFalse(caught.exception.committed)
        self.assertEqual(
            [p.name for p in parent.iterdir()],
            [self.fixture.values["publication"].run_id],
        )

    def test_symlinked_destination_is_rejected(self):
        target = self.fixture.root / "elsewhere"
        target.mkdir()
        destination = (
            self.fixture.root / "analyses" / "millefeuille" / self.bundle.preview.run_id
        )
        destination.symlink_to(target, target_is_directory=True)
        with self.assertRaises(fs.GptCardPublicationError) as caught:
            self._commit()
        self.assertFalse(caught.exception.committed)
        self.assertEqual(list(target.iterdir()), [])

    def test_cleanup_uncertainty_is_reported(self):
        with (
            patch.object(fs, "_rename_noreplace", side_effect=OSError("injected")),
            patch.object(fs, "_clean_owned_stage", return_value=False),
            self.assertRaises(fs.GptCardPublicationError) as caught,
        ):
            self._commit()
        self.assertFalse(caught.exception.committed)
        self.assertFalse(caught.exception.cleanup_complete)

    def test_non_root_cannot_call_primitive(self):
        if os.geteuid() == 0:
            self.skipTest("test process is root")
        with (
            patch.object(fs, "_ROOT_UID", 0),
            self.assertRaises(MillefeuilleContractError),
        ):
            self._commit()
        self.assertFalse(
            (
                self.fixture.root
                / "analyses"
                / "millefeuille"
                / self.bundle.preview.run_id
            ).exists()
        )
