"""Factory/mock-worker tests, not live network/SDK/OCR qualification."""

from dataclasses import replace
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from millefeuille.clients.approved_mistral import (
    ApprovedMistralLifecycle,
    OwnedHTTPWorker,
    supports_approved_ocr,
)
from millefeuille.domain.ocr_lifecycle import (
    ApprovedGraph,
    D03Capability,
    DocumentRecipe,
    OperatorHMACVerifier,
    canonical,
    digest,
)
from millefeuille.domain.ocr_request_budget import OCRBudgetError

requires_ocr_platform = unittest.skipUnless(
    supports_approved_ocr(),
    "requires OCR no-follow ledger, POSIX flock and platform primitives",
)


TOKEN = "synthetic-token-not-real"
KEY = b"synthetic-trust-key-only-0000000000"
DATA = b"%PDF-synthetic"
OCR = {
    "model": "mistral-ocr-2601",
    "pages": [{"index": 0, "markdown": "gold 42", "images": [], "dimensions": None}],
    "usage_info": {"pages_processed": 1},
}


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger"
        self.graph = ApprovedGraph(
            (DocumentRecipe("a", digest(DATA), len(DATA)),),
            "account",
            digest(TOKEN.encode()),
            "a" * 64,
            "b" * 64,
            100,
            4096,
            4096,
            20480,
            20480,
            30,
            10,
            2,
        )
        now = int(time.time())
        cap = D03Capability(
            self.graph.binding,
            "account",
            self.graph.credential_fingerprint,
            now + 60,
            now - 1,
            "c" * 32,
            "",
        )
        self.cap = replace(
            cap, signature=hmac.new(KEY, cap.message(), hashlib.sha256).hexdigest()
        )
        self.verifier = OperatorHMACVerifier(KEY)

    def factory(self, cap=None, graph=None, resolver=None):
        client = ApprovedMistralLifecycle.approved(
            graph or self.graph,
            cap or self.cap,
            verifier=self.verifier,
            token_resolver=resolver or (lambda *_: TOKEN),
            ledger=self.path,
        )
        self.addCleanup(client.close)
        return client

    def fake(self, client, failure=None):
        calls = []

        def send(method, url, body, content_type, token, cap, seconds):
            calls.append((method, url, body))
            rows = [json.loads(x) for x in self.path.read_text().splitlines()]
            self.assertEqual(rows[0]["reserved_calls"], 5)
            self.assertEqual(rows[-1]["state"], "unknown")
            self.assertEqual(token, TOKEN)
            if failure and failure(method, url):
                raise TimeoutError("secret must not escape")
            if url.endswith("/url"):
                return canonical(
                    {"url": "https://storage.example/file?signature=sentinel"}
                )
            if url.endswith("/ocr"):
                return canonical(OCR)
            if method == "DELETE":
                return canonical({"id": "file_a", "deleted": True})
            return canonical(
                {"id": "file_a", "object": "file", "purpose": "ocr", "bytes": len(DATA)}
            )

        client._worker.send = send
        return calls

    def test_invalid_capabilities_before_resolver_and_ledger(self):
        for cap in (
            replace(self.cap, signature="bad"),
            replace(self.cap, account="wrong"),
            replace(self.cap, expires=0),
            replace(self.cap, graph_sha256="0" * 64),
            replace(self.cap, credential_fingerprint="0" * 64),
        ):
            with self.subTest(cap=cap.signature):
                with self.assertRaises(OCRBudgetError):
                    self.factory(
                        cap=cap, resolver=lambda *_: self.fail("resolver called")
                    )
                self.assertFalse(self.path.exists())

    def test_short_lived_capability_refuses_before_resolver_and_ledger(self):
        now = int(time.time())
        cap = replace(self.cap, issued=now - 1, expires=now + 2, signature="")
        cap = replace(
            cap, signature=hmac.new(KEY, cap.message(), hashlib.sha256).hexdigest()
        )
        with self.assertRaises(OCRBudgetError):
            self.factory(cap=cap, resolver=lambda *_: self.fail("resolver called"))
        self.assertFalse(self.path.exists())

    def test_multipart_has_real_crlf_and_independent_parser(self):
        from email import policy
        from email.parser import BytesParser

        from millefeuille.domain.ocr_lifecycle import multipart

        body, kind = multipart(self.graph.documents[0], DATA)
        crlf = bytes((13, 10))
        raw = (
            ("Content-Type: " + kind).encode()
            + crlf
            + b"MIME-Version: 1.0"
            + crlf * 2
            + body
        )
        message = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertTrue(message.is_multipart())
        parts = list(message.iter_parts())
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].get_payload(decode=True), b"ocr")
        self.assertEqual(parts[1].get_payload(decode=True), DATA)
        self.assertEqual(parts[1].get_filename(), "document.pdf")
        self.assertIn(crlf, body)

    def test_wrong_token_and_injection_before_ledger(self):
        for token in ("wrong", "secret\r\ninjected", ""):
            with self.assertRaises(OCRBudgetError):
                self.factory(resolver=lambda *_, token=token: token)
            self.assertFalse(self.path.exists())

    @requires_ocr_platform
    def test_complete_concrete_lifecycle_and_secret_redaction(self):
        client = self.factory()
        calls = self.fake(client)
        result = client.run_document("a", DATA)
        self.assertEqual(result["pages"][0]["markdown"], "gold 42")
        self.assertEqual(
            [(m, u.removeprefix("https://api.mistral.ai")) for m, u, _ in calls],
            [
                ("POST", "/v1/files"),
                ("GET", "/v1/files/file_a/url"),
                ("POST", "/v1/ocr"),
                ("GET", "/v1/files/file_a"),
                ("DELETE", "/v1/files/file_a"),
            ],
        )
        self.assertIn(b'name="purpose"', calls[0][2])
        self.assertIn(b"ocr", calls[0][2])
        self.assertIn(b"signature=sentinel", calls[2][2])
        ledger = self.path.read_text()
        for secret in (TOKEN, "signature=sentinel", self.cap.signature):
            self.assertNotIn(secret, ledger)
        with self.assertRaises(OCRBudgetError):
            client.run_document("a", DATA)
        self.assertEqual(len(calls), 5)

    @requires_ocr_platform
    def test_unknown_upload_no_guessed_cleanup_and_no_replay(self):
        client = self.factory()
        calls = self.fake(client, lambda *_: True)
        with self.assertRaises(OCRBudgetError):
            client.run_document("a", DATA)
        with self.assertRaises(OCRBudgetError):
            client.run_document("a", DATA)
        self.assertEqual(len(calls), 1)
        self.assertEqual(client._states["a:upload"], "unknown")
        self.assertEqual(client._states["a:delete"], "reserved")

    @requires_ocr_platform
    def test_unknown_ocr_runs_only_reserved_finalizers(self):
        client = self.factory()
        calls = self.fake(client, lambda m, u: u.endswith("/ocr"))
        with self.assertRaises(OCRBudgetError) as error:
            client.run_document("a", DATA)
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(len(calls), 5)
        self.assertEqual(client._states["a:ocr"], "unknown")
        self.assertEqual(client._states["a:delete"], "validated")

    @requires_ocr_platform
    def test_parse_failed_is_not_semantic_success(self):
        client = self.factory()
        self.fake(client)
        original = client._worker.send
        client._worker.send = lambda m, u, *a: (
            b'{"model":"x","pages":[]}' if u.endswith("/ocr") else original(m, u, *a)
        )
        with self.assertRaises(OCRBudgetError):
            client.run_document("a", DATA)
        self.assertTrue(client.receipts["a:ocr"]["wire_observed"])
        self.assertEqual(client.receipts["a:ocr"]["semantic_status"], "parse-failed")

    @requires_ocr_platform
    def test_cross_document_and_arbitrary_route_zero_wire(self):
        client = self.factory()
        calls = self.fake(client)
        doc = self.graph.documents[0]
        for kind, path in (
            ("signed_url", "/v1/files/foreign/url"),
            ("ocr", "/v1/ocr"),
            ("upload", "/evil"),
        ):
            with self.assertRaises(OCRBudgetError):
                client._send(doc, kind, path)
        self.assertEqual(calls, [])

    @requires_ocr_platform
    def test_ordinary_deadline_preserves_finalization_window(self):
        client = self.factory()
        calls = self.fake(client)
        original = client._worker.send

        def send(m, u, *args):
            raw = original(m, u, *args)
            if u.endswith("/url"):
                client._ordinary_deadline = time.monotonic() - 1
            return raw

        client._worker.send = send
        with self.assertRaises(OCRBudgetError):
            client.run_document("a", DATA)
        self.assertEqual([m for m, _, _ in calls], ["POST", "GET", "GET", "DELETE"])

    @requires_ocr_platform
    def test_existing_ledger_never_reset(self):
        self.factory()
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.factory()
        self.assertEqual(before, self.path.read_bytes())

    @requires_ocr_platform
    def test_input_drift_before_wire(self):
        client = self.factory()
        calls = self.fake(client)
        with self.assertRaises(OCRBudgetError):
            client.run_document("a", DATA + b"changed")
        self.assertEqual(calls, [])


class OwnedWorkerLocalDeadlineTests(unittest.TestCase):
    """Actual owned process deadline, substituted inert program, no network."""

    @requires_ocr_platform
    def test_sleeping_child_killed_reaped_with_bound(self):
        import millefeuille.clients.approved_mistral as module

        real_popen = module.subprocess.Popen
        children = []

        def spawn(*a, **kw):
            child = real_popen(*a, **kw)
            children.append(child)
            return child

        start = time.monotonic()
        with (
            patch.object(module, "_WORKER", "import time; time.sleep(30)"),
            patch.object(module.subprocess, "Popen", spawn),
            self.assertRaises(OCRBudgetError),
        ):
            OwnedHTTPWorker().send(
                "GET",
                "https://unused.invalid",
                b"",
                "application/json",
                TOKEN,
                4096,
                0.2,
            )
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)

    @requires_ocr_platform
    def test_excess_stdout_is_bounded_and_reaped(self):
        import millefeuille.clients.approved_mistral as module

        with (
            patch.object(
                module,
                "_WORKER",
                "import sys; sys.stdin.buffer.read(); "
                "sys.stdout.buffer.write(b'x'*8192)",
            ),
            self.assertRaises(OCRBudgetError),
        ):
            OwnedHTTPWorker().send(
                "GET", "https://unused.invalid", b"", "application/json", TOKEN, 128, 1
            )


class FactoryHTTPMockTests(FactoryTests):
    # Explicit load_tests below avoids counting inherited methods twice.
    @requires_ocr_platform
    def test_httpx_mock_status_429_and_redirect_never_retried(self):
        import httpx

        for status in (429, 302):
            with self.subTest(status=status):
                path = self.path.with_name("ledger-" + str(status))
                self.path = path
                client = self.factory()
                requests = []

                def handler(request, requests=requests, status=status):
                    requests.append(request)
                    return httpx.Response(
                        status,
                        headers={
                            "Retry-After": "0",
                            "Location": "https://evil.invalid",
                        },
                    )

                transport = httpx.MockTransport(handler)

                def send(
                    method,
                    url,
                    body,
                    content_type,
                    token,
                    cap,
                    seconds,
                    transport=transport,
                ):
                    with httpx.Client(
                        transport=transport, trust_env=False, follow_redirects=False
                    ) as http:
                        response = http.request(method, url, content=body)
                        if not 200 <= response.status_code < 300:
                            raise OCRBudgetError("unknown")
                        return response.content

                client._worker.send = send
                with self.assertRaises(OCRBudgetError):
                    client.run_document("a", DATA)
                self.assertEqual(len(requests), 1)
                self.assertEqual(client._states["a:upload"], "unknown")

    def test_strict_response_duplicate_indices_and_unknown_fields(self):
        from millefeuille.clients.approved_mistral import _json, validate_ocr

        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'"\xff"'):
            with self.assertRaises((OCRBudgetError, ValueError, UnicodeError)):
                _json(raw)
        for value in (
            dict(OCR, surprise=True),
            dict(OCR, pages=[{"index": 1, "markdown": "bad"}]),
            dict(
                OCR,
                pages=[
                    {"index": 0, "markdown": "ok"},
                    {"index": 0, "markdown": "duplicate"},
                ],
            ),
        ):
            with self.assertRaises(OCRBudgetError):
                validate_ocr(value)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(FactoryTests))
    suite.addTests(loader.loadTestsFromTestCase(OwnedWorkerLocalDeadlineTests))
    for name in (
        "test_httpx_mock_status_429_and_redirect_never_retried",
        "test_strict_response_duplicate_indices_and_unknown_fields",
    ):
        suite.addTest(FactoryHTTPMockTests(name))
    return suite
