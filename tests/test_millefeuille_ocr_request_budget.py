# ruff: noqa: SIM117
"""Synthetic wire tests, no accounts, credentials, network or private input."""

from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from millefeuille.clients.mistral_client import BoundedMistralAdapter
from millefeuille.domain.ocr_request_budget import (
    OCRBudgetError,
    Operation,
    RequestGraph,
    initialize_ledger,
    read_ledger,
    reconcile_operation,
    reserve_operation,
    supports_ocr_ledger,
)
from millefeuille.domain.source_qualification import qualify_untrusted_native_source

requires_ocr_platform = unittest.skipUnless(
    supports_ocr_ledger(),
    "requires OCR no-follow ledger, POSIX flock and platform primitives",
)


class BudgetTests(unittest.TestCase):
    payload = b'{"model":"mistral-ocr-latest","document":{"type":"document_url","document_url":"https://example.invalid/synthetic"}}'

    def graph(self):
        ops = tuple(
            Operation(
                k,
                k,
                "POST"
                if k in ("upload", "ocr")
                else "DELETE"
                if k == "delete"
                else "GET",
                "https://api.mistral.ai/v1/" + k,
                hashlib.sha256(self.payload).hexdigest(),
                1024,
                256,
                2,
                10,
            )
            for k in ("upload", "signed_url", "ocr", "status", "poll", "delete")
        )
        return RequestGraph(
            ops, 6, 60, 30, ("delete",), ("status",), "synthetic-offline-only", True
        )

    @requires_ocr_platform
    def test_all_operations_reserved_before_actual_wire_and_no_replay(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = Path(root) / "ledger"
            seen = []

            def wire(request):
                state = read_ledger(self.graph(), ledger)
                kind = request.url.path.rsplit("/", 1)[1]
                self.assertEqual(state["operations"][kind], "unknown")
                self.assertEqual(state["reserved_calls"], 6)
                seen.append(kind)
                return httpx.Response(200, content=b"{}")

            adapter = BoundedMistralAdapter.isolated_mock(
                self.graph(), ledger, transport=httpx.MockTransport(wire)
            )
            try:
                for op in self.graph().operations:
                    self.assertEqual(
                        adapter.execute(op.id, self.payload, authorization="synthetic"),
                        b"{}",
                    )
                    with self.assertRaises(OCRBudgetError):
                        adapter.execute(op.id, self.payload, authorization="synthetic")
                self.assertEqual(len(seen), 6)
            finally:
                adapter.close()

    @requires_ocr_platform
    def test_every_operation_failure_retains_liability_without_retry(self):
        for operation in self.graph().operations:
            for status in (302, 429, 500):
                with self.subTest(operation=operation.id, status=status):
                    with tempfile.TemporaryDirectory() as root:
                        ledger = Path(root) / "ledger"
                        calls = []

                        def wire(request, calls=calls, status=status):
                            calls.append(request)
                            return httpx.Response(
                                status, headers={"Location": "https://evil.invalid"}
                            )

                        adapter = BoundedMistralAdapter.isolated_mock(
                            self.graph(), ledger, transport=httpx.MockTransport(wire)
                        )
                        try:
                            for prior in self.graph().operations:
                                if prior.id == operation.id:
                                    break
                                reserve_operation(self.graph(), prior.id, ledger)
                                reconcile_operation(
                                    self.graph(), prior.id, ledger, "observed"
                                )
                            with self.assertRaises(OCRBudgetError):
                                adapter.execute(
                                    operation.id,
                                    self.payload,
                                    authorization="synthetic",
                                )
                            self.assertEqual(len(calls), 1)
                            state = read_ledger(self.graph(), ledger)
                            self.assertEqual(
                                state["operations"][operation.id], "unknown"
                            )
                            self.assertEqual(state["reserved_cost_microusd"], 60)
                        finally:
                            adapter.close()

    @requires_ocr_platform
    def test_timeout_cancel_and_response_cap_no_retry(self):
        for failure in ("timeout", "cancel", "large"):
            with tempfile.TemporaryDirectory() as root:
                ledger = Path(root) / "ledger"

                def wire(request, failure=failure):
                    if failure == "timeout":
                        raise httpx.ReadTimeout("synthetic")
                    if failure == "cancel":
                        raise KeyboardInterrupt
                    return httpx.Response(200, content=b"x" * 257)

                adapter = BoundedMistralAdapter.isolated_mock(
                    self.graph(), ledger, transport=httpx.MockTransport(wire)
                )
                try:
                    for prior in ("upload", "signed_url"):
                        reserve_operation(self.graph(), prior, ledger)
                        reconcile_operation(self.graph(), prior, ledger, "observed")
                    with self.assertRaises(OCRBudgetError):
                        adapter.execute("ocr", self.payload, authorization="synthetic")
                    self.assertEqual(
                        read_ledger(self.graph(), ledger)["operations"]["ocr"],
                        "unknown",
                    )
                finally:
                    adapter.close()

    @requires_ocr_platform
    def test_rolling_and_resolved_model(self):
        with tempfile.TemporaryDirectory() as root:
            adapter = BoundedMistralAdapter.isolated_mock(
                self.graph(),
                Path(root) / "ledger",
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(
                        200,
                        content=b'{"model":"mistral-ocr-2505","pages":[{"index":0,"markdown":"test"}]}',
                    )
                ),
            )
            try:
                for prior in ("upload", "signed_url"):
                    reserve_operation(self.graph(), prior, adapter.ledger)
                    reconcile_operation(self.graph(), prior, adapter.ledger, "observed")
                result = adapter.process_pdf_with_attribution(
                    "ocr", self.payload, authorization="synthetic"
                )
                self.assertEqual(result.requested_model, "mistral-ocr-latest")
                self.assertEqual(result.returned_model, "mistral-ocr-2505")
            finally:
                adapter.close()

    @requires_ocr_platform
    def test_invalid_graph_and_durability_failure_send_nothing(self):
        for graph in (
            replace(self.graph(), account_price_verified=False),
            replace(self.graph(), total_transport_limit=2),
            replace(self.graph(), cost_limit_microusd=0),
            replace(self.graph(), cleanup_ids=()),
        ):
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaises(OCRBudgetError):
                    BoundedMistralAdapter.isolated_mock(
                        graph,
                        Path(root) / "ledger",
                        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                    )
        with tempfile.TemporaryDirectory() as root:
            wire = unittest.mock.Mock()
            adapter = BoundedMistralAdapter.isolated_mock(
                self.graph(), Path(root) / "ledger", transport=httpx.MockTransport(wire)
            )
            try:
                with patch("os.fsync", side_effect=OSError("synthetic")):
                    with self.assertRaises(OSError):
                        adapter.execute(
                            "upload", self.payload, authorization="synthetic"
                        )
                wire.assert_not_called()
            finally:
                adapter.close()

    def test_native_refuses_before_input_import_or_child(self):
        class Poison:
            def __getattribute__(self, name):
                raise AssertionError("input accessed")

        with patch("subprocess.Popen", side_effect=AssertionError("child created")):
            result = qualify_untrusted_native_source(Poison())
        self.assertEqual(result["outcome"], "refused")
        self.assertFalse(result["limits_enforced"])
        self.assertEqual(result["charged_read_bytes"], 0)
        self.assertEqual(result["evidence"]["termination_receipt"], "no-child-created")


class SafetyRegressions(BudgetTests):
    def test_selected_legacy_refuses_before_config_or_sdk(self):
        from millefeuille.clients.exceptions import MistralAPIError
        from millefeuille.clients.mistral_client import MistralClient

        class Poison:
            def __getattribute__(self, name):
                raise AssertionError("credential/config read")

        with patch("mistralai.Mistral") as sdk:
            with self.assertRaises(MistralAPIError):
                MistralClient(Poison())
            sdk.assert_not_called()

    def test_default_bounded_refuses_before_mock_wire(self):
        wire = unittest.mock.Mock()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(OCRBudgetError):
                BoundedMistralAdapter(
                    self.graph(),
                    Path(root) / "ledger",
                    transport=httpx.MockTransport(wire),
                )
            wire.assert_not_called()
            self.assertFalse((Path(root) / "ledger").exists())

    @requires_ocr_platform
    def test_graph_order_and_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = Path(root) / "ledger"
            initialize_ledger(self.graph(), ledger)
            with self.assertRaises(OCRBudgetError):
                reserve_operation(self.graph(), "ocr", ledger)
            self.assertEqual(
                read_ledger(self.graph(), ledger)["operations"]["ocr"], "reserved"
            )
            with self.assertRaises(OCRBudgetError):
                replace(self.graph(), cleanup_ids=("ocr",)).validate()
            with self.assertRaises(OCRBudgetError):
                replace(
                    self.graph(),
                    operations=(replace(self.graph().operations[0], method="GET"),),
                ).validate()

    @requires_ocr_platform
    def test_damaged_tail_never_reinitialized(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = Path(root) / "ledger"
            initialize_ledger(self.graph(), ledger)
            reserve_operation(self.graph(), "upload", ledger)
            self.assertEqual(
                read_ledger(self.graph(), ledger)["operations"]["upload"], "unknown"
            )
            with self.assertRaises(OCRBudgetError):
                reserve_operation(self.graph(), "upload", ledger)
            with ledger.open("ab") as stream:
                stream.write(b'{"torn":')
            with self.assertRaises(OCRBudgetError):
                read_ledger(self.graph(), ledger)
            with self.assertRaises(FileExistsError):
                initialize_ledger(self.graph(), ledger)

    @requires_ocr_platform
    def test_compressed_stream_refused_without_iteration(self):
        class PoisonStream(httpx.SyncByteStream):
            def __iter__(self):
                raise AssertionError("compressed body touched")

        def wire(request):
            self.assertEqual(request.headers["Accept-Encoding"], "identity")
            return httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, stream=PoisonStream()
            )

        with tempfile.TemporaryDirectory() as root:
            adapter = BoundedMistralAdapter.isolated_mock(
                self.graph(), Path(root) / "ledger", transport=httpx.MockTransport(wire)
            )
            try:
                with self.assertRaises(OCRBudgetError):
                    adapter.execute("upload", self.payload, authorization="synthetic")
            finally:
                adapter.close()

    @requires_ocr_platform
    def test_slow_header_mock_retains_unknown(self):
        import time

        def wire(request):
            time.sleep(0.03)
            return httpx.Response(200, content=b"{}")

        with tempfile.TemporaryDirectory() as root:
            graph = replace(self.graph(), wall_seconds=0.02)
            adapter = BoundedMistralAdapter.isolated_mock(
                graph, Path(root) / "ledger", transport=httpx.MockTransport(wire)
            )
            try:
                with self.assertRaises(OCRBudgetError):
                    adapter.execute("upload", self.payload, authorization="synthetic")
                self.assertEqual(
                    read_ledger(graph, adapter.ledger)["operations"]["upload"],
                    "unknown",
                )
            finally:
                adapter.close()

    @requires_ocr_platform
    def test_wrong_family_and_malformed_shapes(self):
        for body in (
            b'{"model":"other-model","pages":[{"index":0,"markdown":"x"}]}',
            b'{"model":"mistral-ocr-2505","pages":[{"index":0,"markdown":42}]}',
            b'{"model":"mistral-ocr-2505","pages":{}}',
            b"[" * 20 + b"0" + b"]" * 20,
        ):
            with tempfile.TemporaryDirectory() as root:
                adapter = BoundedMistralAdapter.isolated_mock(
                    self.graph(),
                    Path(root) / "ledger",
                    transport=httpx.MockTransport(
                        lambda r, body=body: httpx.Response(200, content=body)
                    ),
                )
                try:
                    for prior in ("upload", "signed_url"):
                        reserve_operation(self.graph(), prior, adapter.ledger)
                        reconcile_operation(
                            self.graph(), prior, adapter.ledger, "observed"
                        )
                    with self.assertRaises(OCRBudgetError):
                        adapter.process_pdf_with_attribution(
                            "ocr", self.payload, authorization="synthetic"
                        )
                finally:
                    adapter.close()

    @requires_ocr_platform
    def test_crash_reopen_unknown_and_terminal_no_duplicate(self):
        import os

        for stage in ("before-fsync", "after-fsync", "after-send", "terminal"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as root:
                ledger = Path(root) / "ledger"
                initialize_ledger(self.graph(), ledger)
                pid = os.fork()
                if pid == 0:
                    if stage == "before-fsync":
                        with patch("os.fsync", side_effect=lambda fd: os._exit(71)):
                            reserve_operation(self.graph(), "upload", ledger)
                    reserve_operation(self.graph(), "upload", ledger)
                    if stage in {"after-send", "terminal"}:
                        with (Path(root) / "synthetic-send").open("wb") as stream:
                            stream.write(b"sent")
                    if stage == "terminal":
                        reconcile_operation(self.graph(), "upload", ledger, "observed")
                    os._exit(72)
                _, status = os.waitpid(pid, 0)
                self.assertIn(os.waitstatus_to_exitcode(status), (71, 72))
                state = read_ledger(self.graph(), ledger)
                self.assertEqual(
                    state["operations"]["upload"],
                    "observed" if stage == "terminal" else "unknown",
                )
                with self.assertRaises(OCRBudgetError):
                    reserve_operation(self.graph(), "upload", ledger)

    @requires_ocr_platform
    def test_busy_ledger_nonblocking(self):
        import fcntl

        with tempfile.TemporaryDirectory() as root:
            ledger = Path(root) / "ledger"
            initialize_ledger(self.graph(), ledger)
            with ledger.open("r+") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(OCRBudgetError, "ledger-busy"):
                    reserve_operation(self.graph(), "upload", ledger)


class ClosedJSONTests(unittest.TestCase):
    def test_duplicate_keys_utf8_and_nonfinite_refused(self):
        from millefeuille.clients.mistral_client import bounded_json

        for raw in (b'{"index":0,"index":1}', b'{"x":NaN}', b'{"x":"\xff"}'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, UnicodeError)):
                bounded_json(raw)
