"""Strict card transport revalidates every byte against published source."""

from dataclasses import replace
import json
import unittest

from millefeuille.domain import paper_card_outcome_handoff as handoff
from millefeuille.domain.millefeuille import MillefeuilleContractError
from tests import test_millefeuille_paper_card_output_plan as output_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestGptCardOutcomeHandoff(unittest.TestCase):
    def setUp(self):
        fixture = output_tests.TestGptCardOutputPlan(
            "test_plans_canonical_files_without_writes_or_false_downstream_status"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.outcome = fixture.outcome
        self.encoded = handoff.encode_gpt_card_outcome_handoff(self.outcome)

    def _decode(self, encoded=None, **changes):
        return handoff.decode_gpt_card_outcome_handoff(
            self.encoded if encoded is None else encoded,
            **{
                **self.fixture.values,
                "expected_approval": self.outcome.approval,
                **changes,
            },
        )

    def test_roundtrip_is_exact_and_has_no_filesystem_effect(self):
        root = self.fixture.values["source_pack_root"]
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        decoded = self._decode()
        self.assertEqual(decoded, self.outcome)
        self.assertNotIn("Private", repr(decoded))
        self.assertEqual(
            before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        )

    def test_noncanonical_duplicate_oversize_and_wrong_approval_fail_closed(self):
        for encoded in (
            b"{}",
            b'{"x":1,"x":2}',
            b"\xff",
            self.encoded + b" ",
            b" " * (handoff._MAX_BYTES + 1),
        ):
            with (
                self.subTest(size=len(encoded)),
                self.assertRaises(MillefeuilleContractError),
            ):
                self._decode(encoded)
        for change in (
            {"manifest_sha256": "sha256:" + "0" * 64},
            {"request_count": True},
            {"run_id": "wrong-run"},
        ):
            with self.assertRaises(MillefeuilleContractError):
                self._decode(expected_approval=replace(self.outcome.approval, **change))

    def test_output_result_encoding_and_published_input_drift_are_rejected(self):
        payload = json.loads(self.encoded)
        for key, wrong in (
            ("output_base64", "%%%"),
            ("output_base64", "eA=="),
            ("result", {}),
        ):
            with self.subTest(key=key), self.assertRaises(MillefeuilleContractError):
                self._decode(handoff._canonical_json({**payload, key: wrong}))
        path = (
            self.fixture.values["source_pack_root"]
            / f"analyses/millefeuille/{self.fixture.values['publication'].run_id}"
            / "structure/inputs/selected.md"
        )
        path.write_bytes(path.read_bytes() + b"drift")
        with self.assertRaises(MillefeuilleContractError):
            self._decode()
