"""Read-only retrieval reuses verified publications and preserves their lineage."""

from copy import deepcopy
from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.artifact_writer import (
    build_dry_run_artifact_index,
    build_dry_run_stage_manifest,
    write_stage_manifest,
)
from millefeuille.domain.artifacts import write_artifact_index
from millefeuille.domain.index_fixtures import write_indexes_from_evidence
from millefeuille.domain.millefeuille import (
    MillefeuilleContractError,
)
from millefeuille.domain.published_summary_run_link import (
    load_published_summary_run_view,
)
from millefeuille.domain.retrieve import (
    _load_retrieval_summary,
    _validate_published_summary_entry_refs,
    retrieve_artifact_refs,
)
from millefeuille.domain.route_fixtures import (
    ROUTE_EVIDENCE_REF,
    ROUTE_MARKDOWN_REF,
    ROUTE_SELECTION_EVIDENCE_SCHEMA_VERSION,
)
from millefeuille.domain.secure_io import RootArtifactReader
from millefeuille.domain.stage_runtime import relative_ref, resolve_run_artifacts
from millefeuille.domain.summary_published_handoff import (
    load_verified_published_gpt_summary_package,
)
from tests import test_millefeuille_published_summary_run_link as link_tests
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_source_pack_writer import (
    _make_handoff_row,
    _make_item,
    _write_index_fixture_json,
)
from tests.test_millefeuille_stage_cli import _prepare_fixture_run


@requires_secure_nofollow_writes
class PublishedSummaryRetrievalTests(unittest.TestCase):
    def setUp(self):
        fixture = link_tests.TestPublishedSummaryRunLink(
            "test_view_keeps_original_generation_and_uses_verified_original_files"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture._publish_fixture_link()
        self.fixture = fixture
        self.root, self.run = fixture.root, fixture.run
        self.pack = self.root / "zotero" / fixture.plan.paper_id
        manifest = json.loads((self.pack / "manifest.json").read_bytes())
        identity = manifest["identity"]
        selected = self.pack / ROUTE_MARKDOWN_REF
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_text("Synthetic selected full text.\n", encoding="utf-8")
        route = self.pack / ROUTE_EVIDENCE_REF
        route.parent.mkdir(parents=True, exist_ok=True)
        route.write_text(
            json.dumps(
                {
                    "schema_version": ROUTE_SELECTION_EVIDENCE_SCHEMA_VERSION,
                    "source_hash": manifest["source_hash"],
                }
            ),
            encoding="utf-8",
        )
        index_fixture = _write_index_fixture_json(str(self.root))
        evidence = self.root / "retrieval-index-evidence.json"
        evidence.write_text(
            json.dumps(
                {
                    "schema_version": "millefeuille-index-fixture-evidence/v0.1",
                    "item_key": identity["zotero_item_key"],
                    "attachment_key": identity["zotero_attachment_key"],
                    "canonical_filename": identity["canonical_filename"],
                    "expected_sha256": manifest["source_hash"].split(":", 1)[1],
                    "index_status_path": str(index_fixture),
                }
            ),
            encoding="utf-8",
        )
        write_indexes_from_evidence(
            evidence_path=evidence,
            source_pack_root=self.root,
            run_id=fixture.plan.run_id,
            artifact_run_dir=self.run,
        )
        item = _make_item()
        item = replace(
            item,
            attachments=[
                replace(
                    item.attachments[0],
                    filename=identity["canonical_filename"],
                    sha256=manifest["source_hash"].split(":", 1)[1],
                    zotero_version=None,
                )
            ],
        )
        rows = [
            replace(
                _make_handoff_row(),
                canonical_filename=identity["canonical_filename"],
                sha256=manifest["source_hash"].split(":", 1)[1],
                zotero_version=None,
            )
        ]
        stage = build_dry_run_stage_manifest(
            item=item,
            rows=rows,
            run_id=fixture.plan.run_id,
            handoff_enabled=True,
            source_pack_verified=True,
            route_ready=True,
            structure_ready=True,
            summarize_ready=True,
            card_ready=True,
            index_ready=True,
        )
        write_stage_manifest(stage, self.run / "stage-manifest.json")
        index = json.loads((self.run / "index/index-status.json").read_bytes())
        artifact = build_dry_run_artifact_index(
            item=item,
            rows=rows,
            run_id=fixture.plan.run_id,
            run_dir=self.run,
            stage_manifest=stage,
            source_pack_ref=relative_ref(self.pack, self.run),
            source_pack_manifest_ref=relative_ref(
                self.pack / "manifest.json", self.run
            ),
            source_pack_hash=manifest["source_hash"],
            route_markdown_ref=relative_ref(selected, self.run),
            summary_artifact_ref="summaries/hierarchical-summary.json",
            summary_text_dir_ref=relative_ref(
                fixture.origin.parent / "texts", self.run
            ),
            paper_card_json_ref="cards/paper-card.json",
            paper_card_markdown_ref="cards/paper-card.md",
            retrieval_index_status_ref="index/index-status.json",
            index_records=index["lanes"],
        )
        write_artifact_index(artifact, self.run / "artifact-index.json")
        self.kwargs = {
            "source_pack_root": self.root,
            "artifact_root": self.run,
            "run_id": fixture.plan.run_id,
            "paper_id": fixture.plan.paper_id,
        }

    def test_actual_api_and_cli_preserve_origin_refs_without_content_or_writes(self):
        before = self.fixture._census()
        original = json.loads(self.fixture.origin.read_bytes())
        payload = retrieve_artifact_refs(**self.kwargs)
        self.assertEqual(payload["summary_match_count"], len(original["summaries"]))
        self.assertEqual(
            payload["summary_origin_run_id"], self.fixture.plan.origin_run_id
        )
        self.assertEqual(payload["run_id"], self.fixture.plan.run_id)
        self.assertEqual(
            payload["summary_link_sha256"], self.fixture.plan.manifest_sha256
        )
        self.assertEqual(
            payload["summary_schema_version"],
            "millefeuille-published-summary-run-view/v0.1",
        )
        for old, new in zip(
            original["summaries"], payload["summary_entries"], strict=True
        ):
            self.assertEqual(old["summary_id"], new["summary_id"])
            self.assertEqual(old["source_locators"], new["source_locators"])
            self.assertEqual(
                Path(os.path.normpath(self.fixture.origin.parent / old["text_ref"])),
                Path(os.path.normpath(self.fixture.link.parent / new["text_ref"])),
            )
        stdout, stderr = StringIO(), StringIO()
        code = run_stage_cli(
            [
                "retrieve",
                "--source-pack-root",
                str(self.root),
                "--artifact-root",
                str(self.run),
                "--paper-id",
                self.fixture.plan.paper_id,
                "--run-id",
                self.fixture.plan.run_id,
                "--json",
            ],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue()), payload)
        self.assertEqual(before, self.fixture._census())
        self.assertFalse((self.fixture.link.parent / "texts").exists())
        self.assertNotIn("Private", json.dumps(payload))

    def test_page_grain_classification_and_index_filters_use_original_locators(self):
        original = load_published_summary_run_view(self.fixture.link)
        page = retrieve_artifact_refs(**self.kwargs, grain="page", page=1)
        self.assertGreater(page["summary_match_count"], 0)
        self.assertTrue(all(e["grain"] == "page" for e in page["summary_entries"]))
        classified = retrieve_artifact_refs(
            **self.kwargs, evidence_need="classification", index_lane="openkb"
        )
        expected = [e for e in original["summaries"] if e["scope"] == "classification"]
        self.assertEqual(classified["summary_entries"], expected)
        self.assertTrue(all(e["lane"] == "openkb" for e in classified["index_lanes"]))

    def test_source_text_provenance_and_link_tampering_reject_actual_retrieval(self):
        view = load_published_summary_run_view(self.fixture.link)
        entry = view["summaries"][0]
        package = load_verified_published_gpt_summary_package(
            **self.fixture.fixture.values
        )
        paths = [
            Path(os.path.normpath(self.fixture.link.parent / entry["text_ref"])),
            self.root / package.handoff.provenance_refs[0],
            self.pack / "manifest.json",
            self.fixture.link,
        ]
        for path in paths:
            raw = path.read_bytes()
            if path == self.fixture.link:
                changed = json.loads(raw)
                changed["source_hash"] = "sha256:" + "0" * 64
                altered = json.dumps(changed).encode()
            elif path.suffix == ".json":
                changed = json.loads(raw)
                changed["tampering"] = True
                altered = json.dumps(changed).encode()
            else:
                altered = raw + b"tampered"
            path.write_bytes(altered)
            before = self.fixture._census()
            with (
                self.subTest(path=path.name),
                self.assertRaises(MillefeuilleContractError),
            ):
                retrieve_artifact_refs(**self.kwargs)
            self.assertEqual(before, self.fixture._census())
            path.write_bytes(raw)

    def test_cross_run_refs_are_confined_to_verified_original_publication(self):
        view = load_published_summary_run_view(self.fixture.link)
        entry = view["summaries"][0]
        origin = self.fixture.origin.parent.parent
        _validate_published_summary_entry_refs(
            summary_dir=self.fixture.link.parent,
            origin_dir=origin,
            entry=entry,
            entry_index=1,
        )
        for ref in (
            "../../../zotero/ITEM1/manifest.json",
            "/tmp/outside.txt",
            "https://example.com/text",
            "..\\outside.txt",
        ):
            bad = deepcopy(entry)
            bad["text_ref"] = ref
            with self.subTest(ref=ref), self.assertRaises(MillefeuilleContractError):
                _validate_published_summary_entry_refs(
                    summary_dir=self.fixture.link.parent,
                    origin_dir=origin,
                    entry=bad,
                    entry_index=1,
                )

    def test_derived_view_cannot_replace_a_verified_link_artifact(self):
        view = load_published_summary_run_view(self.fixture.link)
        self.fixture.link.write_text(json.dumps(view), encoding="utf-8")
        with self.assertRaisesRegex(MillefeuilleContractError, "verified summary link"):
            retrieve_artifact_refs(**self.kwargs)

    def test_batch_reader_rejects_unsnapshotted_publication_dependencies(self):
        resolved = resolve_run_artifacts(**self.kwargs)
        before = self.fixture._census()
        with (
            RootArtifactReader(self.root) as reader,
            self.assertRaisesRegex(
                MillefeuilleContractError, "complete input snapshots"
            ),
        ):
            _load_retrieval_summary(resolved, artifact_reader=reader)
        self.assertEqual(before, self.fixture._census())


class InlineSummaryRetrievalCompatibilityTests(unittest.TestCase):
    def test_existing_inline_pipeline_remains_readable_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run = _prepare_fixture_run(tempdir)
            kwargs = {
                "source_pack_root": root,
                "run_id": "run-fixture",
                "paper_id": "zotero-ITEM1",
            }
            payload = retrieve_artifact_refs(**kwargs)
            self.assertEqual(payload["summary_match_count"], 2)
            self.assertNotIn("summary_origin_run_id", payload)
            path = run / "summaries/hierarchical-summary.json"
            summary = json.loads(path.read_bytes())
            summary["summaries"][0]["text_ref"] = "../cards/paper-card.json"
            path.write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaisesRegex(MillefeuilleContractError, "traversal-safe"):
                retrieve_artifact_refs(**kwargs)


if __name__ == "__main__":
    unittest.main()
