"""Receipt authenticity separation and raw-proof/version tests, all synthetic."""

import copy
from datetime import UTC, datetime, timedelta
import unittest

from millefeuille.domain.live_receipts import compute_approved_live_receipt_digest
from millefeuille.domain.writeback_core import _wire
from millefeuille.domain.writeback_http import _project
from millefeuille.domain.writeback_live_science import (
    acceptance_request,
    validate_acceptance,
    validate_source_observations,
)
from tests import test_millefeuille_writeback_core as core


def issue(request):
    req = request.to_dict()
    scope = {
        k: v
        for k, v in req.items()
        if k
        not in (
            "run_id",
            "item_cap",
            "selected_item_count",
            "provider_call_limit",
            "cost_limit_usd_micros",
        )
    }
    scope["max_items"] = 1
    scope["limits"] = {"max_provider_calls": 0, "max_cost_usd_micros": 0}
    now = datetime.now(UTC)
    value = {
        "schema_version": "millefeuille-approved-live-receipt/v0.1",
        "receipt_id": "synthetic-science",
        "run_id": req["run_id"],
        "approval": {
            "approver_id": "fr-meyer",
            "approved_at": (now - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "expires_at": (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": scope,
        "integrity": {"algorithm": "sha256", "content_digest": "sha256:" + "0" * 64},
    }
    value["integrity"]["content_digest"] = compute_approved_live_receipt_digest(value)
    return value


def observation(snapshot):
    identity = {k: snapshot[k] for k in ("library_type", "library_id", "key")}
    raw = _wire(
        {
            "key": snapshot["key"],
            "version": snapshot["version"],
            "library": {
                "type": snapshot["library_type"],
                "id": int(snapshot["library_id"]),
            },
            "data": {
                **snapshot["data"],
                "key": snapshot["key"],
                "version": snapshot["version"],
                "dateModified": "2026-11-16T00:00:00Z",
            },
        }
    )
    state, proof = _project(identity, raw, snapshot["version"])
    return {"snapshot": state, "proof": proof}


class ScienceTests(unittest.TestCase):
    def setUp(self):
        self.f = core.WritebackCoreTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_separate_scientific_acceptance_exact_materialization(self):
        files = {"materialized": "sha256:" + "1" * 64}
        receipt = issue(acceptance_request(self.f.plan, files))
        self.assertEqual(
            validate_acceptance(self.f.plan, files, receipt, receipt),
            receipt["integrity"]["content_digest"],
        )
        with self.assertRaises(ValueError):
            validate_acceptance(self.f.plan, files, receipt, {})
        with self.assertRaises(ValueError):
            validate_acceptance(self.f.plan, {}, receipt, receipt)
        forged = copy.deepcopy(receipt)
        forged["scope"]["operations"] = ["classification.publish-proposal"]
        forged["integrity"]["content_digest"] = compute_approved_live_receipt_digest(
            forged
        )
        with self.assertRaises(ValueError):
            validate_acceptance(self.f.plan, files, forged, forged)

    def test_original_version_separate_freshness_and_raw_proof(self):
        original = observation({**self.f.before, "version": 7})
        fresh = observation(self.f.before)
        now = datetime.now(UTC).replace(microsecond=0)
        captured = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        validate_source_observations(
            self.f.plan, original, fresh, captured_at=captured, now=now
        )
        with self.assertRaises(ValueError):
            validate_source_observations(
                self.f.plan, fresh, fresh, captured_at=captured, now=now
            )
        with self.assertRaises(ValueError):
            validate_source_observations(
                self.f.plan,
                original,
                fresh,
                captured_at=captured,
                now=now + timedelta(seconds=61),
            )
        fresh["proof"]["raw_utf8"] += " "
        with self.assertRaises(ValueError):
            validate_source_observations(
                self.f.plan, original, fresh, captured_at=captured, now=now
            )

    def test_preparation_consumes_separate_receipt_before_two_actual_reads(self):
        import sqlite3

        import httpx

        from millefeuille.domain.writeback_live_prepare import (
            preparation_request,
            prepare_remote,
        )
        from tests.test_millefeuille_writeback_http import Stream

        expected = {k: self.f.mapping[k] for k in ("library_type", "library_id", "key")}
        expected.update(version=7, name="Accepted topic", parentCollection=False)
        original = observation({**self.f.before, "version": 7})
        receipt = issue(preparation_request(self.f.plan, expected))
        calls = []

        async def handler(request):
            calls.append(str(request.url))
            if "/collections/" in str(request.url):
                raw = _wire(
                    {
                        "key": expected["key"],
                        "version": 7,
                        "library": {"type": "user", "id": 12345},
                        "data": {
                            "key": expected["key"],
                            "version": 7,
                            "name": "Accepted topic",
                            "parentCollection": False,
                        },
                    }
                )
                version = 7
            else:
                raw = observation(self.f.before)["proof"]["raw_utf8"].encode()
                version = self.f.before["version"]
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "last-modified-version": str(version),
                },
                stream=Stream(raw),
            )

        kwargs = {
            "plan": self.f.plan,
            "expected_collection": expected,
            "original": original,
            "receipt_payload": receipt,
            "authenticated_receipt": receipt,
            "journal": self.f.journal,
            "secret": "SYNTHETICSECRET",
            "transport": httpx.MockTransport(handler),
        }
        result = prepare_remote(**kwargs)
        self.assertEqual(result["fresh"]["snapshot"], self.f.plan.value()["before"])
        self.assertEqual(len(calls), 2)
        self.assertIn("/collections/", calls[0])
        self.assertIn("/items/", calls[1])
        with self.assertRaises(sqlite3.IntegrityError):
            prepare_remote(**kwargs)
        self.assertEqual(len(calls), 2)
        # Independent execution authority was not consumed by preparation.
        self.assertEqual(self.f.execute()["result"]["status"], "written")
