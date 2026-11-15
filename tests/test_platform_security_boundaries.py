"""Unsupported capabilities must refuse, not silently downgrade secure I/O."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.cli.native_source import run_native_source_cli
from millefeuille.domain import secure_io
from millefeuille.domain.evidence_answer import answer_evidence
from millefeuille.domain.evidence_source_adapter import ReadBudget
from millefeuille.domain.millefeuille import MillefeuilleContractError
from tests.platform_capabilities import LINUX_NATIVE_ISOLATION, PINNED_ROOT_READS
from tests.test_evidence_answer import eight, fixture


class UnsupportedSecurityBoundaryTests(unittest.TestCase):
    def test_missing_nofollow_refuses_before_root_open_or_scan(self):
        with tempfile.TemporaryDirectory() as root:
            reader = secure_io.RootArtifactReader(root)
            with (
                patch.object(secure_io, "_supports_no_follow", return_value=False),
                patch.object(secure_io.os, "open") as opened,
                patch.object(secure_io.os, "scandir") as scanned,
                self.assertRaisesRegex(MillefeuilleContractError, "no-follow"),
            ):
                reader.__enter__()
            opened.assert_not_called()
            scanned.assert_not_called()
            self.assertIsNone(reader._root_fd)

    @unittest.skipIf(PINNED_ROOT_READS, "requires missing pinned-root primitives")
    def test_actual_unsupported_root_refuses_without_portable_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            with (
                patch.object(secure_io.os, "open") as opened,
                patch.object(secure_io, "_open_portable_regular_file_fd") as portable,
                self.assertRaisesRegex(MillefeuilleContractError, "no-follow"),
                secure_io.RootArtifactReader(root),
            ):
                self.fail("unsupported pinned-root capability was accepted")
            opened.assert_not_called()
            portable.assert_not_called()

    def test_missing_nofollow_yields_integrity_refusal_not_supported_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            corpus, reader = fixture(Path(root))
            with (
                patch.object(secure_io, "_supports_no_follow", return_value=False),
                patch.object(secure_io.os, "open") as opened,
            ):
                result = answer_evidence(
                    request=eight(corpus)[0],
                    corpus=corpus,
                    reader=reader,
                    budget=ReadBudget(),
                )
            opened.assert_not_called()
            self.assertEqual(result["outcome"], "integrity_refused")

    def assert_native_platform_refusal(self):
        output = StringIO()
        with (
            patch(
                "millefeuille.domain.native_decode_boundary.subprocess.Popen"
            ) as spawn,
            redirect_stdout(output),
        ):
            code = run_native_source_cli(
                ["--pdf", "never-open-this-input.pdf", "--sha256", "0" * 64]
            )
        spawn.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["disposition"], "refused")
        self.assertEqual(result["reason"], "full-native-isolation-adapter-unavailable")
        self.assertEqual(result["boundary"]["charged_read_bytes"], 0)
        self.assertFalse(result["boundary"]["limits_enforced"])

    def test_native_missing_adapter_refuses_before_input_and_spawn(self):
        with patch("millefeuille.domain.native_decode_boundary.sys.platform", "win32"):
            self.assert_native_platform_refusal()

    @unittest.skipIf(LINUX_NATIVE_ISOLATION, "requires unsupported native platform")
    def test_actual_unsupported_native_cli_has_no_trust_bypass(self):
        self.assert_native_platform_refusal()
