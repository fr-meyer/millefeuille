"""Published-summary reuse verifies lineage without regenerating or copying text."""

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.domain.acceptance import _load_run_scoped_summary_check
from millefeuille.domain.artifact_writer import _resolve_summary_refs
from millefeuille.domain.card_index_contract import canonical_json_bytes
from millefeuille.domain.index_fixtures import _validate_summary_dependency
from millefeuille.domain.millefeuille import (
    HierarchicalSummaryRecord,
    MillefeuilleContractError,
)
from millefeuille.domain.published_summary_run_link import (
    VIEW_SCHEMA_VERSION,
    load_published_summary_run_view,
    plan_published_gpt_summary_run_link,
)
from millefeuille.domain.summary_fixtures import (
    _materialize_summary_bundle,
    load_hierarchical_summary,
)
from millefeuille.domain.summary_published_handoff import (
    load_verified_published_gpt_summary_package,
)
from tests import test_millefeuille_paper_card_output_plan as card_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestPublishedSummaryRunLink(unittest.TestCase):
    def setUp(self):
        fixture = card_tests.TestGptCardOutputPlan(
            "test_plans_canonical_files_without_writes_or_false_downstream_status"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.card_plan = fixture._plan()
        self.root = fixture.values["source_pack_root"]
        for item in self.card_plan.files:
            path = self.root / item.ref
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.data)
        self.values = dict(
            fixture.values,
            run_id=self.card_plan.run_id,
            card_write_manifest_sha256=self.card_plan.write_manifest_sha256,
        )
        self.run = self.root / f"analyses/millefeuille/{self.card_plan.run_id}"
        self.plan = plan_published_gpt_summary_run_link(**self.values)
        self.link = self.root / self.plan.ref
        self.origin = (
            self.root
            / f"analyses/millefeuille/{fixture.values['publication'].run_id}"
            / "summaries/hierarchical-summary.json"
        )

    def _publish_fixture_link(self):
        self.link.parent.mkdir(parents=True, exist_ok=True)
        self.link.write_bytes(self.plan.link_json)

    def _census(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_link_planning_is_metadata_only_and_has_no_effects(self):
        before = self._census()
        plan = plan_published_gpt_summary_run_link(**self.values)
        self.assertEqual(plan, self.plan)
        self.assertEqual(before, self._census())
        self.assertEqual((plan.provider_calls_performed, plan.writes_performed), (0, 0))
        self.assertNotIn("Private", repr(plan))
        self.assertNotIn(b"Private", plan.link_json)
        self.assertNotEqual(plan.run_id, plan.origin_run_id)
        self.assertEqual(plan.total_bytes, len(plan.link_json))

    def test_view_keeps_original_generation_and_uses_verified_original_files(self):
        original = self.origin.read_bytes()
        self._publish_fixture_link()
        before = self._census()
        view = load_hierarchical_summary(self.link)
        self.assertEqual(view["schema_version"], VIEW_SCHEMA_VERSION)
        self.assertEqual(view["run_id"], self.card_plan.run_id)
        self.assertEqual(view["origin_run_id"], self.values["publication"].run_id)
        self.assertEqual(view["summary_link_sha256"], self.plan.manifest_sha256)
        self.assertEqual(original, self.origin.read_bytes())
        self.assertEqual(before, self._census())
        raw_record = HierarchicalSummaryRecord.from_dict(json.loads(original)).to_dict()
        for old, reused in zip(raw_record["summaries"], view["summaries"], strict=True):
            self.assertEqual(old["summary_id"], reused["summary_id"])
            self.assertEqual(old["source_locators"], reused["source_locators"])
            for key in ("text_ref", "model_provenance_ref"):
                if key not in old:
                    self.assertNotIn(key, reused)
                    continue
                self.assertEqual(
                    (self.origin.parent / old[key]).resolve(),
                    (self.link.parent / reused[key]).resolve(),
                )
                self.assertTrue((self.link.parent / reused[key]).is_file())
        self.assertFalse((self.link.parent / "texts").exists())

    def test_index_artifact_and_acceptance_consumers_use_the_view_without_copies(self):
        self._publish_fixture_link()
        _validate_summary_dependency(
            run_dir=self.run, paper_id=self.plan.paper_id, run_id=self.plan.run_id
        )
        refs = _resolve_summary_refs(
            run_dir=self.run, paper_id=self.plan.paper_id, run_id=self.plan.run_id
        )
        self.assertEqual(
            (self.run / refs["summary_text_dir_ref"]).resolve(),
            (self.origin.parent / "texts").resolve(),
        )
        reasons = []
        resolved = SimpleNamespace(
            run_dir=self.run, paper_id=self.plan.paper_id, run_id=self.plan.run_id
        )
        check = _load_run_scoped_summary_check(
            resolved=resolved, review_reasons=reasons
        )
        self.assertEqual(check.status.value, "passed")
        self.assertEqual(check.details["origin_run_id"], self.plan.origin_run_id)
        self.assertEqual(reasons, [])

    def test_view_cannot_be_rematerialized_as_a_generated_summary(self):
        self._publish_fixture_link()
        view = load_hierarchical_summary(self.link)
        with self.assertRaisesRegex(
            MillefeuilleContractError, "cannot be rematerialized"
        ):
            _materialize_summary_bundle(
                fixture_payload=view,
                fixture_base_dir=self.link.parent,
                paper_id=self.plan.paper_id,
                run_id=self.plan.run_id,
                summary_output_path=self.run / "other-summary.json",
            )

    def test_original_text_and_provenance_tampering_fail_before_reuse(self):
        self._publish_fixture_link()
        old = json.loads(self.origin.read_bytes())["summaries"][0]
        package = load_verified_published_gpt_summary_package(**self.fixture.values)
        paths = {
            "text": self.origin.parent / old["text_ref"],
            "provenance": self.root / package.handoff.provenance_refs[0],
        }
        for key, path in paths.items():
            original = path.read_bytes()
            path.write_bytes(original + b"tampered")
            with self.subTest(key=key), self.assertRaises(MillefeuilleContractError):
                load_hierarchical_summary(self.link)
            path.write_bytes(original)

    def test_link_identity_and_path_changes_are_rejected(self):
        self._publish_fixture_link()
        raw = json.loads(self.plan.link_json)
        variants = []
        for key in (
            "paper_id",
            "run_id",
            "source_hash",
            "summary_record_sha256",
            "card_write_manifest_sha256",
        ):
            changed = deepcopy(raw)
            changed[key] = "changed"
            variants.append(changed)
        for bad_ref in (
            "../escape.json",
            "/absolute.json",
            "C:/drive.json",
            "a\\b.json",
        ):
            changed = deepcopy(raw)
            changed["evidence_refs"]["route_evidence_ref"] = bad_ref
            variants.append(changed)
        for changed in variants:
            self.link.write_bytes(canonical_json_bytes(changed))
            with (
                self.subTest(changed=changed),
                self.assertRaises(MillefeuilleContractError),
            ):
                load_hierarchical_summary(self.link)
        self.link.write_bytes(self.plan.link_json)

    def test_card_content_tampering_is_rejected(self):
        self._publish_fixture_link()
        card_path = self.run / "cards/paper-card.json"
        card = json.loads(card_path.read_bytes())
        card["one_line_thesis"] = "altered after publication"
        card_path.write_bytes(canonical_json_bytes(card))
        with self.assertRaisesRegex(MillefeuilleContractError, "card artifact drift"):
            load_hierarchical_summary(self.link)

    def test_controlled_index_refresh_preserves_the_summary_link(self):
        self._publish_fixture_link()
        path = self.run / "cards/paper-card.json"
        card = json.loads(path.read_bytes())
        card["index_state"] = {
            "phase": "observed",
            "status_ref": "../index/index-status.json",
            "lanes": [
                {"lane": "openkb", "status": "written"},
                {"lane": "pageindex", "status": "skipped"},
            ],
        }
        path.write_bytes(canonical_json_bytes(card))
        view = load_hierarchical_summary(self.link)
        self.assertEqual(view["origin_run_id"], self.plan.origin_run_id)

    def test_duplicate_keys_and_oversized_link_are_rejected(self):
        self._publish_fixture_link()
        raw = self.plan.link_json.decode()
        self.link.write_text(
            raw.replace('"run_id":', '"run_id":"duplicate","run_id":', 1)
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "duplicate JSON key"):
            load_published_summary_run_view(self.link)
        self.link.write_bytes(self.plan.link_json + b" " * 32769)
        with patch(
            "millefeuille.domain.published_summary_run_link.load_verified_published_gpt_summary_package"
        ) as loader:
            with self.assertRaisesRegex(MillefeuilleContractError, "exceeds"):
                load_published_summary_run_view(self.link)
            loader.assert_not_called()

    def test_link_symlinks_and_wrong_locations_are_rejected(self):
        self._publish_fixture_link()
        elsewhere = self.run / "copied-link.json"
        elsewhere.write_bytes(self.plan.link_json)
        with self.assertRaisesRegex(MillefeuilleContractError, "location"):
            load_published_summary_run_view(elsewhere)
        self.link.unlink()
        self.link.symlink_to(elsewhere)
        with self.assertRaises(MillefeuilleContractError):
            load_published_summary_run_view(self.link)
