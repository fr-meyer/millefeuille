"""Unsupported OCR platforms refuse before custody, secrets or child effects."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from millefeuille.clients import approved_mistral as approved
from millefeuille.domain import ocr_request_budget as budget


class UnsupportedOCRPlatformTests(unittest.TestCase):
    def test_ledger_entry_points_refuse_without_files_or_graph_access(self):
        graph = Mock()
        ledger = Path("never-open-ocr-ledger")
        with (
            patch.object(budget, "fcntl", None),
            patch("os.open") as opening,
            patch.object(Path, "read_text") as reading,
        ):
            for operation, args in (
                (budget.initialize_ledger, (graph, ledger)),
                (budget.read_ledger, (graph, ledger)),
                (budget.reserve_operation, (graph, "upload", ledger)),
                (budget.reconcile_operation, (graph, "upload", ledger, "observed")),
            ):
                with (
                    self.subTest(operation=operation.__name__),
                    self.assertRaisesRegex(
                        budget.OCRBudgetError, "unsupported-ocr-ledger-platform"
                    ),
                ):
                    operation(*args)
            opening.assert_not_called()
            reading.assert_not_called()
            graph.assert_not_called()
            graph.validate.assert_not_called()

    def test_factory_refuses_before_verifier_resolver_ledger_or_worker(self):
        verifier, resolver = Mock(), Mock()
        with (
            patch.object(approved, "fcntl", None),
            patch("os.open") as opening,
            patch.object(approved, "OwnedHTTPWorker") as worker,
            patch.object(approved.subprocess, "Popen") as child,
        ):
            with self.assertRaisesRegex(
                budget.OCRBudgetError, "unsupported-approved-ocr-platform"
            ):
                approved.ApprovedMistralLifecycle.approved(
                    object(),
                    object(),
                    verifier=verifier,
                    token_resolver=resolver,
                    ledger="never-open-ocr-ledger",
                )
            verifier.verify.assert_not_called()
            resolver.assert_not_called()
            opening.assert_not_called()
            worker.assert_not_called()
            child.assert_not_called()

    def test_direct_worker_refuses_before_child_launch(self):
        with (
            patch.object(approved, "fcntl", None),
            patch.object(approved.subprocess, "Popen") as child,
            patch("os.open") as opening,
        ):
            with self.assertRaisesRegex(
                budget.OCRBudgetError, "unsupported-approved-ocr-platform"
            ):
                approved.OwnedHTTPWorker().send(
                    "GET",
                    "https://example.invalid",
                    b"",
                    "application/json",
                    "synthetic",
                    10,
                    1,
                )
            child.assert_not_called()
            opening.assert_not_called()

    @unittest.skipIf(
        budget.supports_ocr_ledger() and approved.supports_approved_ocr(),
        "requires a platform missing secure OCR capabilities",
    )
    def test_actual_unsupported_platform_refuses_without_effects(self):
        verifier, resolver = Mock(), Mock()
        with (
            patch("os.open") as opening,
            patch.object(Path, "read_text") as reading,
            patch.object(approved, "OwnedHTTPWorker") as worker,
            patch.object(approved.subprocess, "Popen") as child,
        ):
            if not budget.supports_ocr_ledger():
                with self.assertRaisesRegex(
                    budget.OCRBudgetError, "unsupported-ocr-ledger-platform"
                ):
                    budget.initialize_ledger(Mock(), Path("never-open-ocr-ledger"))
            if not approved.supports_approved_ocr():
                with self.assertRaisesRegex(
                    budget.OCRBudgetError, "unsupported-approved-ocr-platform"
                ):
                    approved.ApprovedMistralLifecycle.approved(
                        object(),
                        object(),
                        verifier=verifier,
                        token_resolver=resolver,
                        ledger="never-open-ocr-ledger",
                    )
            opening.assert_not_called()
            reading.assert_not_called()
            verifier.verify.assert_not_called()
            resolver.assert_not_called()
            worker.assert_not_called()
            child.assert_not_called()

    def test_non_posix_capabilities_refuse(self):
        with patch.object(budget.os, "name", "nt"):
            self.assertFalse(budget.supports_ocr_ledger())
            self.assertFalse(approved.supports_approved_ocr())

    def test_missing_nofollow_capabilities_refuse(self):
        import types

        proxy = types.SimpleNamespace(
            **{
                name: getattr(budget.os, name)
                for name in dir(budget.os)
                if name != "O_NOFOLLOW"
            }
        )
        with patch.object(budget, "os", proxy), patch.object(approved, "os", proxy):
            self.assertFalse(budget.supports_ocr_ledger())
            self.assertFalse(approved.supports_approved_ocr())
