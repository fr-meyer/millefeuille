"""Published retrieval batches pin every original dependency before commit."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import retrieve
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.published_summary_run_link import (
    load_published_summary_run_view,
)
from millefeuille.domain.secure_io import RootArtifactReader
from millefeuille.domain.summary_published_handoff import (
    load_verified_published_gpt_summary_package,
)
from tests import test_millefeuille_published_summary_retrieval as retrieval_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class PublishedSummaryBatchTests(unittest.TestCase):
    def setUp(self):
        fixture = retrieval_tests.PublishedSummaryRetrievalTests(
            "test_actual_api_and_cli_preserve_origin_refs_without_content_or_writes"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root, self.run = fixture.root, fixture.run
        self.link_fixture = fixture.fixture
        self.manifest = self.root.parent / "published-retrieval-batch.json"
        self.payload = {
            "schema_version": "millefeuille-retrieval-batch-manifest/v0.2",
            "batch_id": "batch-published",
            "runs": [
                {
                    "paper_id": self.link_fixture.plan.paper_id,
                    "run_id": self.link_fixture.plan.run_id,
                    "artifact_run_ref": self.run.relative_to(self.root).as_posix(),
                }
            ],
        }
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.payload), encoding="utf-8")

    def run_batch(self, **filters):
        return retrieve.write_retrieval_batch_result(
            source_pack_root=self.root, batch_manifest_path=self.manifest, **filters
        )

    def assert_no_result(self):
        path = (
            self.root / "batches/millefeuille" / self.payload["batch_id"] / "retrieval"
        )
        self.assertFalse(path.exists())

    def test_verified_batch_preserves_original_refs_and_repeats_exactly(self):
        original = json.loads(self.link_fixture.origin.read_bytes())
        before = self.link_fixture._census()
        result = self.run_batch()
        self.assertEqual(
            result["schema_version"], "millefeuille-retrieval-batch-result/v0.2"
        )
        run = result["runs"][0]
        self.assertEqual(
            run["summary_origin_run_id"], self.link_fixture.plan.origin_run_id
        )
        self.assertEqual(
            run["summary_link_sha256"], self.link_fixture.plan.manifest_sha256
        )
        self.assertEqual(
            run["source_summary_ref"],
            self.link_fixture.origin.relative_to(self.root).as_posix(),
        )
        self.assertEqual(
            result["counts"]["summary_matches"], len(original["summaries"])
        )
        originals = {entry["summary_id"]: entry for entry in original["summaries"]}
        self.assertEqual(
            set(originals), {entry["summary_id"] for entry in run["summary_entries"]}
        )
        for new in run["summary_entries"]:
            old = originals[new["summary_id"]]
            self.assertEqual(new["source_locators"], old["source_locators"])
            expected = (
                (self.link_fixture.origin.parent / old["text_ref"])
                .relative_to(self.root)
                .as_posix()
            )
            self.assertEqual(new["text_ref"], expected)
        after = self.link_fixture._census()
        self.assertTrue(all(after[key] == value for key, value in before.items()))
        self.assertNotIn("Private", json.dumps(result))
        self.assertEqual(self.run_batch(), result)
        self.assertEqual(self.link_fixture._census(), after)

    def test_filters_preserve_lineage_when_no_summaries_match(self):
        result = self.run_batch(page=99, grain="page", index_lane="openkb")
        self.assertEqual(result["counts"]["summary_matches"], 0)
        self.assertEqual(
            result["runs"][0]["summary_origin_run_id"],
            self.link_fixture.plan.origin_run_id,
        )
        self.assertEqual(len(result["runs"][0]["index_lanes"]), 1)

    def test_duplicate_aliases_and_late_missing_run_fail_before_output(self):
        duplicate = deepcopy(self.payload["runs"][0])
        duplicate.pop("paper_id")
        duplicate["slug"] = self.link_fixture.plan.paper_id
        self.payload["runs"].append(duplicate)
        self.write_manifest()
        with self.assertRaisesRegex(
            MillefeuilleContractError, "unique paper_id/run_id"
        ):
            self.run_batch()
        self.assert_no_result()
        duplicate["run_id"] = "run-missing"
        duplicate["artifact_run_ref"] = "analyses/millefeuille/run-missing"
        self.write_manifest()
        with self.assertRaises(MillefeuilleContractError):
            self.run_batch()
        self.assert_no_result()

    def test_invalid_artifact_run_refs_reject_before_publication(self):
        for ref in (
            None,
            "/tmp/outside",
            "../outside",
            "analyses/millefeuille/run-other",
        ):
            self.payload["runs"][0]["artifact_run_ref"] = ref
            self.write_manifest()
            with (
                self.subTest(ref=ref),
                self.assertRaisesRegex(MillefeuilleContractError, "artifact_run_ref"),
            ):
                self.run_batch()
            self.assert_no_result()

    def test_every_verifier_layer_uses_the_pinned_reader(self):
        modules = (
            "published_summary_run_link",
            "summary_published_handoff",
            "summary_preparation",
            "summary_dispatch",
            "summary_results",
        )
        from contextlib import ExitStack

        with ExitStack() as stack:
            for module in modules:
                stack.enter_context(
                    patch(
                        "millefeuille.domain." + module + ".read_bytes_no_follow",
                        side_effect=AssertionError("unbound artifact read"),
                    )
                )
            for module in ("route_fixtures", "structure_fixtures"):
                stack.enter_context(
                    patch(
                        "millefeuille.domain." + module + ".read_text_no_follow",
                        side_effect=AssertionError("unbound evidence read"),
                    )
                )
            self.assertGreater(self.run_batch()["counts"]["summary_matches"], 0)

    def test_post_preflight_mutations_reject_each_dependency_without_output(self):
        link = json.loads(self.link_fixture.link.read_bytes())
        package = load_verified_published_gpt_summary_package(
            **self.link_fixture.fixture.values
        )
        paths = [
            self.link_fixture.link,
            *[self.root / ref for ref in link["evidence_refs"].values()],
            *[self.root / f.ref for f in package.files],
            self.fixture.pack / "manifest.json",
            self.run / "cards/paper-card.json",
            self.run / "cards/write-manifest.json",
        ]
        card_manifest = json.loads(
            (self.run / "cards/write-manifest.json").read_bytes()
        )
        paths.extend(self.root / entry["ref"] for entry in card_manifest["entries"])
        preparation = json.loads(
            (self.root / link["evidence_refs"]["preparation_ref"]).read_bytes()
        )
        paths.extend(
            Path(preparation["inputs"][key]["path"])
            for key in ("selected_markdown", "structure")
        )
        original_publish = retrieve._publish_retrieval_batch_outputs
        for index, path in enumerate(dict.fromkeys(paths)):
            original = path.read_bytes()
            self.payload["batch_id"] = "batch-mutation-" + str(index)
            self.write_manifest()

            def mutate_then_publish(path=path, original=original, **kwargs):
                path.write_bytes(original + b" ")
                return original_publish(**kwargs)

            try:
                with (
                    self.subTest(ref=path.relative_to(self.root).as_posix()),
                    patch.object(
                        retrieve,
                        "_publish_retrieval_batch_outputs",
                        side_effect=mutate_then_publish,
                    ),
                    self.assertRaisesRegex(
                        MillefeuilleContractError, "changed after batch preflight"
                    ),
                ):
                    self.run_batch()
                self.assert_no_result()
            finally:
                path.write_bytes(original)

    def test_census_addition_after_preflight_is_rejected(self):
        new_file = self.link_fixture.origin.parent / "unexpected.txt"
        original_publish = retrieve._publish_retrieval_batch_outputs

        def mutate_then_publish(**kwargs):
            new_file.write_text("added", encoding="utf-8")
            return original_publish(**kwargs)

        with (
            patch.object(
                retrieve,
                "_publish_retrieval_batch_outputs",
                side_effect=mutate_then_publish,
            ),
            self.assertRaises(MillefeuilleContractError),
        ):
            self.run_batch()
        self.assert_no_result()

    def test_wrong_pinned_root_and_original_text_symlink_fail(self):
        with (
            tempfile.TemporaryDirectory() as tempdir,
            RootArtifactReader(tempdir) as reader,
            self.assertRaisesRegex(MillefeuilleContractError, "reader root drift"),
        ):
            load_published_summary_run_view(
                self.link_fixture.link, artifact_reader=reader
            )
        original = json.loads(self.link_fixture.origin.read_bytes())
        path = self.link_fixture.origin.parent / original["summaries"][0]["text_ref"]
        raw = path.read_bytes()
        target = self.root / "outside-original.md"
        target.write_bytes(raw)
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(MillefeuilleContractError):
            self.run_batch()
        self.assert_no_result()


if __name__ == "__main__":
    unittest.main()
