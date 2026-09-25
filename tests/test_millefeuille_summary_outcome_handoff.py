"""Strict no-write transport checks for transient GPT summary results."""

from __future__ import annotations

from dataclasses import replace
import json
import unittest

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_outcome_handoff import (
    decode_gpt_summary_outcome_handoff,
    encode_gpt_summary_outcome_handoff,
)
from tests import test_millefeuille_summary_output_write_scope as scope_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestGptSummaryOutcomeHandoff(unittest.TestCase):
    def setUp(self):
        fixture = scope_tests.TestGptSummaryOutputWriteScope(
            "test_exact_write_scope_is_previewed_without_effects"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture

    def _decode(self, encoded: bytes, **overrides):
        values = {
            "expected_approval": self.fixture.outcome.approval,
            "route_evidence_path": self.fixture.evidence["route_evidence_path"],
            "structure_evidence_path": self.fixture.evidence["structure_evidence_path"],
            "preparation_path": self.fixture.evidence["preparation_path"],
        }
        values.update(overrides)
        return decode_gpt_summary_outcome_handoff(encoded, **values)

    def test_roundtrip_revalidates_complete_current_run_without_writing(self):
        root = self.fixture.root
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        encoded = encode_gpt_summary_outcome_handoff(self.fixture.outcome)
        restored = self._decode(encoded)
        after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(restored, self.fixture.outcome)
        self.assertEqual(before, after)

    def test_rejects_noncanonical_and_wrong_approval(self):
        encoded = encode_gpt_summary_outcome_handoff(self.fixture.outcome)
        with self.assertRaises(MillefeuilleContractError):
            self._decode(encoded + b" ")
        with self.assertRaises(MillefeuilleContractError):
            self._decode(
                encoded,
                expected_approval=replace(
                    self.fixture.outcome.approval, receipt_id="other-receipt"
                ),
            )
        payload = json.loads(encoded)
        payload["executions"][0]["output_base64"] = "!"
        with self.assertRaises(MillefeuilleContractError):
            self._decode(self._canonical(payload))

    def test_rejects_result_tampering_and_source_drift(self):
        encoded = encode_gpt_summary_outcome_handoff(self.fixture.outcome)
        payload = json.loads(encoded)
        payload["executions"][0]["result"]["status"] = "failed"
        with self.assertRaises(MillefeuilleContractError):
            self._decode(self._canonical(payload))
        (self.fixture.root / "selected.md").write_text(
            "Changed source.\n", encoding="utf-8"
        )
        with self.assertRaises(MillefeuilleContractError):
            self._decode(encoded)

    def test_rejects_deeply_nested_json_without_recursion_escape(self):
        deeply_nested = b"[" * 1100 + b"0" + b"]" * 1100
        with self.assertRaises(MillefeuilleContractError):
            self._decode(deeply_nested)
        nested_result = 0
        for _ in range(1100):
            nested_result = [nested_result]
        original = self.fixture.outcome
        executions = list(original.executions)
        executions[0] = replace(executions[0], result={"deep": nested_result})
        with self.assertRaises(MillefeuilleContractError):
            encode_gpt_summary_outcome_handoff(
                replace(original, executions=tuple(executions))
            )

    @staticmethod
    def _canonical(payload):
        return (
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
