import asyncio
import json
import time
import unittest
from unittest.mock import patch

import httpx

from millefeuille.domain import writeback_http as h
from tests import test_millefeuille_writeback_core as core


class Stream(httpx.AsyncByteStream):
    def __init__(self, body, delay=0):
        self.body = body
        self.delay = delay
        self.closed = False

    async def __aiter__(self):
        for chunk in (self.body[:1], self.body[1:]):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.f = core.WritebackCoreTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.identity = {
            k: self.f.state[k] for k in ("library_type", "library_id", "key")
        }
        self.requests = []
        self.streams = []
        self.behavior = "ok"

    def envelope(self):
        state = self.f.state
        return {
            "key": state["key"],
            "version": state["version"],
            "library": {"type": state["library_type"], "id": int(state["library_id"])},
            "data": {
                **state["data"],
                "key": state["key"],
                "version": state["version"],
                "dateModified": "2026-11-16T00:00:00Z",
            },
        }

    def response(self, status, body=b"", version=None, delay=0):
        stream = Stream(body, delay)
        self.streams.append(stream)
        headers = {
            "content-type": "application/json",
            "last-modified-version": str(version or self.f.state["version"]),
            "content-length": str(len(body)),
        }
        return httpx.Response(status, headers=headers, stream=stream)

    async def handler(self, request):
        self.requests.append(request)
        if request.method == "GET":
            return self.response(200, json.dumps(self.envelope()).encode())
        delta = json.loads(request.content)
        if self.behavior in ("412", "429"):
            # Another actor may have happened to make the same target state.
            self.f.state["data"].update(delta)
            self.f.state["version"] += 1
            return self.response(int(self.behavior))
        self.f.state["data"].update(delta)
        self.f.state["version"] += 1
        if self.behavior == "lost":
            raise httpx.ReadTimeout("DUMMYSECRET should not escape")
        ack = self.f.state["version"]
        if self.behavior == "concurrent":
            self.f.state["version"] += 1
        return self.response(204, version=ack)

    def transports(self):
        return h._HTTPReader(
            "DUMMYSECRET", httpx.MockTransport(self.handler)
        ), h._HTTPWriter("DUMMYSECRET", httpx.MockTransport(self.handler))

    def test_exact_actual_requests_success_raw_proof_and_rollback(self):
        reader, writer = self.transports()
        result = self.f.execute(reader=reader, writer=writer)
        self.assertEqual(result["result"]["status"], "written")
        self.assertEqual([r.method for r in self.requests], ["GET", "PATCH", "GET"])
        patch_request = self.requests[1]
        self.assertEqual(
            str(patch_request.url), "https://api.zotero.org/users/12345/items/ABCD1234"
        )
        self.assertEqual(patch_request.headers["If-Unmodified-Since-Version"], "9")
        self.assertEqual(
            set(json.loads(patch_request.content)), {"tags", "collections"}
        )
        self.assertTrue(all(s.closed for s in self.streams))
        self.assertNotIn("DUMMYSECRET", repr(reader))
        reader, writer = self.transports()
        rolled = self.f.execute(
            reader=reader,
            writer=writer,
            authority=self.f.authority("rollback", result["audit_digest"], "rollback"),
            rollback=True,
        )
        self.assertEqual(rolled["audit"]["status"], "rolled-back")
        import sqlite3

        with sqlite3.connect(self.f.journal.path) as db:
            records = db.execute(
                "SELECT payload FROM events WHERE kind='after-read'"
            ).fetchall()
        proof = json.loads(records[0][0])
        h._checked_observation(proof)
        self.assertNotIn("DUMMYSECRET", str(records))

    def test_refused_ambiguous_and_concurrent_never_owned(self):
        for behavior in ("412", "429", "lost", "concurrent"):
            with self.subTest(behavior=behavior):
                self.setUp()
                self.behavior = behavior
                reader, writer = self.transports()
                out = self.f.execute(reader=reader, writer=writer)
                self.assertEqual(out["result"]["status"], "needs-review")
                self.assertEqual(sum(r.method == "PATCH" for r in self.requests), 1)
                with self.assertRaises(ValueError):
                    self.f.journal.execution(self.f.plan, out["audit_digest"])

    def test_projection_preserves_semantics_and_raw_commitment(self):
        self.f.state["data"].update(
            {
                "dateAdded": "2020-01-01T00:00:00Z",
                "creators": [{"name": "B"}, {"name": "A"}],
                "unknown": [3, 1],
            }
        )
        self.f.state["data"]["tags"] = [{"tag": "z"}, {"tag": "a", "type": 0}]
        reader, _ = self.transports()
        out = reader.read(self.identity, deadline=time.monotonic() + 1)
        snapshot, proof = h._checked_observation(out)
        self.assertEqual(snapshot["data"]["creators"], [{"name": "B"}, {"name": "A"}])
        self.assertEqual(snapshot["data"]["unknown"], [3, 1])
        self.assertEqual(
            snapshot["data"]["tags"], [{"tag": "a", "type": 0}, {"tag": "z"}]
        )
        self.assertIn("dateAdded", snapshot["data"])
        self.assertNotIn("dateModified", snapshot["data"])
        out["proof"]["raw_utf8"] += " "
        with self.assertRaises(ValueError):
            h._checked_observation(out)

    def test_wrong_identity_json_status_size_and_redirect(self):
        good = json.dumps(self.envelope()).encode()
        wrong = self.envelope()
        wrong["data"]["key"] = "WRONG123"
        cases = [
            (200, json.dumps(wrong).encode()),
            (200, b'{"x":1,"x":2}'),
            (200, b'{"x":NaN}'),
            (200, b"{"),
            (200, b"x" * 65537),
            (302, good),
        ]
        for status, body in cases:

            async def handler(request, status=status, body=body):
                return self.response(status, body)

            reader = h._HTTPReader("DUMMYSECRET", httpx.MockTransport(handler))
            with self.assertRaises(ValueError) as caught:
                reader.read(self.identity, deadline=time.monotonic() + 1)
            self.assertNotIn("DUMMYSECRET", str(caught.exception))

    def test_slow_headers_body_total_deadline_cleanup(self):
        for kind in ("headers", "body"):

            async def handler(request, kind=kind):
                if kind == "headers":
                    await asyncio.sleep(0.2)
                return self.response(
                    200,
                    json.dumps(self.envelope()).encode(),
                    delay=0.06 if kind == "body" else 0,
                )

            reader = h._HTTPReader("DUMMYSECRET", httpx.MockTransport(handler))
            start = time.monotonic()
            with self.assertRaises(ValueError):
                reader.read(self.identity, deadline=start + 0.08)
            self.assertLess(time.monotonic() - start, 0.2)
            self.assertTrue(all(s.closed for s in self.streams))

    def test_group_path_and_client_security_options_budgets(self):
        self.f.state["library_type"] = "group"
        self.identity["library_type"] = "group"
        reader, _ = self.transports()
        factory = httpx.AsyncClient
        options = []

        def client(**kwargs):
            options.append(kwargs)
            return factory(**kwargs)

        with patch.object(h.httpx, "AsyncClient", side_effect=client):
            reader.read(self.identity, deadline=time.monotonic() + 1)
        self.assertIn("/groups/", str(self.requests[0].url))
        self.assertFalse(options[0]["trust_env"])
        self.assertFalse(options[0]["follow_redirects"])
        reader.read(self.identity, deadline=time.monotonic() + 1)
        with self.assertRaises(ValueError):
            reader.read(self.identity, deadline=time.monotonic() + 1)

    def test_slow_close_is_cancelled_without_orphan(self):
        states = []

        class SlowClose(Stream):
            async def aclose(self):
                states.append("started")
                try:
                    await asyncio.sleep(0.2)
                finally:
                    states.append("closed-or-cancelled")
                    self.closed = True

        async def handler(request):
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "last-modified-version": "9",
                },
                stream=SlowClose(json.dumps(self.envelope()).encode()),
            )

        start = time.monotonic()
        with self.assertRaises(ValueError):
            h._HTTPReader("DUMMYSECRET", httpx.MockTransport(handler)).read(
                self.identity, deadline=start + 0.08
            )
        self.assertLess(time.monotonic() - start, 0.2)
        self.assertEqual(states, ["started", "closed-or-cancelled"])

    def test_refusal_survives_close_error_and_secret_echo_is_rejected(self):
        class BadClose(Stream):
            async def aclose(self):
                raise RuntimeError("DUMMYSECRET")

        async def refuse(request):
            return httpx.Response(412, stream=BadClose(b""))

        writer = h._HTTPWriter("DUMMYSECRET", httpx.MockTransport(refuse))
        with self.assertRaises(h._DefinitiveRefusalError) as caught:
            writer.patch(
                self.identity,
                expected_version=9,
                delta={"tags": [], "collections": []},
                deadline=time.monotonic() + 1,
            )
        self.assertNotIn("DUMMYSECRET", str(caught.exception))
        self.f.state["data"]["extra"] = "DUMMYSECRET"
        reader, _ = self.transports()
        with self.assertRaises(ValueError):
            reader.read(self.identity, deadline=time.monotonic() + 1)

    def test_header_content_and_version_refusals(self):
        for change in (
            {"content-type": "text/html"},
            {"last-modified-version": "10"},
            {"content-length": "999"},
            {"content-encoding": "gzip"},
        ):

            async def handler(request, change=change):
                response = self.response(200, json.dumps(self.envelope()).encode())
                response.headers.update(change)
                return response

            reader = h._HTTPReader("DUMMYSECRET", httpx.MockTransport(handler))
            with self.assertRaises(ValueError):
                reader.read(self.identity, deadline=time.monotonic() + 1)
        for deadline in (float("nan"), float("inf"), time.monotonic() + 100):
            reader, _ = self.transports()
            with self.assertRaises(ValueError):
                reader.read(self.identity, deadline=deadline)
