"""Synthetic actual HTTP exchange only; no DNS/provider calls."""

import copy
import json
import time
import unittest

import httpx

from millefeuille.domain.writeback_live_collection import (
    CollectionReader,
    checked_collection,
)
from tests.test_millefeuille_writeback_http import Stream


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.identity = {
            "library_type": "user",
            "library_id": "12345",
            "key": "COLL0002",
        }
        self.expected = {
            **self.identity,
            "version": 7,
            "name": "Accepted topic",
            "parentCollection": False,
        }
        self.calls = []
        self.envelope = {
            "key": "COLL0002",
            "version": 7,
            "library": {"type": "user", "id": 12345},
            "data": {
                "key": "COLL0002",
                "version": 7,
                "name": "Accepted topic",
                "parentCollection": False,
            },
        }

    async def handler(self, request):
        self.calls.append(request)
        raw = json.dumps(self.envelope).encode()
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "last-modified-version": "7"},
            stream=Stream(raw),
        )

    def reader(self):
        return CollectionReader("SYNTHETICSECRET", httpx.MockTransport(self.handler))

    def test_actual_fixed_get_raw_proof_budget_and_mapping(self):
        reader = self.reader()
        value = reader.read_collection(self.identity, deadline=time.monotonic() + 2)
        self.assertEqual(checked_collection(value, self.expected), value)
        self.assertEqual(
            str(self.calls[0].url),
            "https://api.zotero.org/users/12345/collections/COLL0002",
        )
        self.assertEqual(self.calls[0].method, "GET")
        self.assertEqual(self.calls[0].headers["cache-control"], "no-cache, no-store")
        with self.assertRaises(ValueError):
            reader.read_collection(self.identity, deadline=time.monotonic() + 2)
        changed = copy.deepcopy(value)
        changed["proof"]["raw_utf8"] += " "
        with self.assertRaises(ValueError):
            checked_collection(changed, self.expected)
        with self.assertRaises(ValueError):
            checked_collection(value, {**self.expected, "name": "Other"})

    def test_wrong_library_version_key_denied(self):
        for field in ("library", "version", "key"):
            with self.subTest(field=field):
                old = self.envelope[field]
                self.envelope[field] = (
                    {"type": "user", "id": 999} if field == "library" else 99
                )
                with self.assertRaises(ValueError):
                    self.reader().read_collection(
                        self.identity, deadline=time.monotonic() + 2
                    )
                self.envelope[field] = old
