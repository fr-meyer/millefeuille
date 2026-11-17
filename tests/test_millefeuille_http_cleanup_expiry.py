"""Owned cleanup initiation under real absolute-deadline exhaustion."""

import asyncio
import time
import unittest

import httpx

from millefeuille.domain import writeback_http as h

IDENTITY = {"library_type": "user", "library_id": "1", "key": "ABCD1234"}


class Stream(httpx.AsyncByteStream):
    def __init__(self, *, starve=False, slow=False, error=False):
        self.starve, self.slow, self.error = starve, slow, error
        self.entered = self.started = self.released = False

    async def __aiter__(self):
        self.entered = True
        if self.starve:
            time.sleep(0.06)  # new negative scheduling fixture, never clock replacement
        await asyncio.sleep(0.3)
        yield b"{}"

    async def aclose(self):
        self.started = True
        try:
            if self.error:
                raise RuntimeError("DUMMY_PRIVATE_CLOSE_ERROR")
            if self.slow:
                await asyncio.sleep(0.3)
        finally:
            self.released = True


class Transport(httpx.MockTransport):
    def __init__(self, handler, *, slow=False):
        super().__init__(handler)
        self.slow = slow
        self.started = self.released = False

    async def aclose(self):
        self.started = True
        try:
            if self.slow:
                await asyncio.sleep(0.3)
        finally:
            self.released = True


class CleanupExpiry(unittest.IsolatedAsyncioTestCase):
    async def exercise(
        self,
        *,
        starve=False,
        slow=False,
        error=False,
        status=200,
        no_response=False,
        repeat=False,
    ):
        before = set(asyncio.all_tasks())
        stream = Stream(starve=starve, slow=slow, error=error)
        requests = []

        async def handler(request):
            requests.append(request)
            if no_response:
                await asyncio.sleep(0.3)
            return httpx.Response(
                status,
                headers={
                    "Content-Type": "application/json",
                    "Last-Modified-Version": "1",
                },
                stream=stream,
            )

        transport = Transport(handler, slow=slow)
        reader = h._HTTPReader("DUMMYSECRET", transport)
        current = asyncio.current_task()
        handles = []
        if repeat:
            loop = asyncio.get_running_loop()
            handles = [
                loop.call_later(0.01, current.cancel),
                loop.call_later(0.02, current.cancel),
            ]
        try:
            with self.assertRaises(ValueError) as caught:
                await reader._exchange(
                    "PATCH" if status == 412 else "GET",
                    IDENTITY,
                    time.monotonic() + 0.04,
                    body=b"{}" if status == 412 else None,
                    expected_version=1 if status == 412 else None,
                )
        finally:
            for handle in handles:
                handle.cancel()
        if status == 412:
            self.assertIsInstance(caught.exception, h._DefinitiveRefusalError)
            self.assertEqual(str(caught.exception), "remote mutation refused")
        self.assertNotIn("DUMMY", str(caught.exception))
        self.assertTrue(transport.started)
        self.assertTrue(transport.released)
        if not no_response:
            self.assertTrue(stream.started, "acquired response close never started")
            self.assertTrue(stream.released, "acquired response not released")
        else:
            self.assertFalse(stream.started)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("Zotero-API-Key", requests[0].headers)
        pending = [t for t in asyncio.all_tasks() - before if not t.done()]
        self.assertEqual(pending, [], "owned finalizer task escaped")
        return stream

    async def test_expired_outer_still_initiates_owned_closers(self):
        stream = await self.exercise(starve=True)
        self.assertTrue(stream.entered)

    async def test_immediate_close_after_operation_timeout(self):
        await self.exercise()

    async def test_yielding_closers_cancel_and_release(self):
        await self.exercise(slow=True)

    async def test_close_error_preserves_refusal_and_drains_sibling(self):
        await self.exercise(error=True, slow=True, status=412)

    async def test_repeated_cancellation_releases_owned_resources(self):
        await self.exercise(slow=True, repeat=True)

    async def test_no_response_still_closes_client(self):
        await self.exercise(no_response=True)
