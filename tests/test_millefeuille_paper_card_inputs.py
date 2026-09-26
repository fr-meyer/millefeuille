"""Verified publication bytes feed the GPT card plan without reread or effects."""

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.paper_card_inputs import plan_published_gpt_paper_card_request
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.summary_published_handoff import (
    load_published_gpt_summary_card_inputs,
)
from tests import test_millefeuille_summary_published_handoff as handoff_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestPublishedPaperCardInputs(unittest.TestCase):
    def setUp(self):
        fixture = handoff_tests.TestPublishedSummaryHandoff(
            "test_handoff_matches_source_pack_and_has_no_effects_or_private_text"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.values = dict(
            source_pack_root=fixture.fixture.root,
            publication=fixture.publication,
            **fixture.evidence,
        )

    def test_plans_card_from_all_held_verified_bytes_without_effects(self):
        root = self.fixture.fixture.root
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        inputs = load_published_gpt_summary_card_inputs(**self.values)
        plan = plan_published_gpt_paper_card_request(
            **self.values, run_id="run-card-02"
        )
        after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(plan.paper_id, self.fixture.publication.paper_id)
        self.assertEqual(plan.run_id, "run-card-02")
        self.assertEqual(
            plan.publication_manifest_sha256,
            self.fixture.publication.bundle_manifest_sha256,
        )
        self.assertEqual(len(inputs.summaries), len(inputs.handoff.summary_text_refs))
        self.assertNotIn("Private", repr(inputs))
        self.assertNotIn("Private", repr(plan))
        self.assertEqual((plan.provider_calls_performed, plan.writes_performed), (0, 0))

    def test_source_snapshots_and_summary_text_are_read_once(self):
        refs = {
            self.fixture.fixture.root / ref
            for ref in (
                *self.fixture._plan().source_input_refs,
                *self.fixture._plan().summary_text_refs,
            )
        }
        counts = {}

        def observed_read(path, context):
            selected = Path(path)
            counts[selected] = counts.get(selected, 0) + 1
            if selected in refs and counts[selected] > 1:
                raise AssertionError("unchecked second source read")
            return read_bytes_no_follow(path, context)

        with patch(
            "millefeuille.domain.summary_published_handoff.read_bytes_no_follow",
            side_effect=observed_read,
        ):
            inputs = load_published_gpt_summary_card_inputs(**self.values)
        self.assertEqual({ref: counts[ref] for ref in refs}, dict.fromkeys(refs, 1))
        self.assertTrue(inputs.markdown)
        self.assertTrue(inputs.structure)

    def test_corrupt_publication_is_rejected_before_prompt_planning(self):
        refs = (
            *self.fixture._plan().source_input_refs,
            *self.fixture._plan().summary_text_refs,
        )
        for ref in refs:
            path = self.fixture.fixture.root / ref
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            with (
                self.subTest(ref=ref),
                patch(
                    "millefeuille.domain.paper_card_inputs.plan_gpt_paper_card_request"
                ) as builder,
                self.assertRaises(MillefeuilleContractError),
            ):
                plan_published_gpt_paper_card_request(
                    **self.values, run_id="run-card-02"
                )
            builder.assert_not_called()
            path.write_bytes(original)

    def test_wrong_trusted_bundle_is_rejected_before_prompt_planning(self):
        wrong = replace(
            self.fixture.publication, bundle_manifest_sha256="sha256:" + "0" * 64
        )
        with (
            patch(
                "millefeuille.domain.paper_card_inputs.plan_gpt_paper_card_request"
            ) as builder,
            self.assertRaises(MillefeuilleContractError),
        ):
            plan_published_gpt_paper_card_request(
                **{**self.values, "publication": wrong}, run_id="run-card-02"
            )
        builder.assert_not_called()
