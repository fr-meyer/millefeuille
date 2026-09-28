"""Real multi-PDF intake with synthetic upstream evidence and downstream joins."""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import jsonschema

from millefeuille.domain import card_fixtures, index_fixtures
from millefeuille.domain.card_fixtures import (
    CardFixtureEvidence,
    write_cards_from_evidence,
)
from millefeuille.domain.index_fixtures import (
    IndexFixtureEvidence,
    write_indexes_from_evidence,
)
from millefeuille.domain.local_structure import build_local_markdown_structure
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.route_fixtures import (
    ROUTE_EVIDENCE_REF,
    ROUTE_MARKDOWN_REF,
    ROUTE_SELECTION_EVIDENCE_SCHEMA_VERSION,
)
from millefeuille.domain.source_packs import (
    aggregate_source_hash,
    write_source_pack_from_recovered_pdfs,
)
from millefeuille.domain.source_scope import MultiSourceScope
from millefeuille.domain.structure_fixtures import (
    STRUCTURE_EVIDENCE_REF,
    STRUCTURE_EVIDENCE_SCHEMA_VERSION,
)
from tests.test_millefeuille_source_pack_writer import (
    _evidence,
    _write_card_fixture_json,
    _write_index_fixture_json,
    _write_summary_fixture_json,
)

RUN_ID = "run-multi-fixture"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _prepare(tempdir, item_key="ITEM1", root=None):
    base = Path(tempdir) / item_key
    base.mkdir()
    root = root or Path(tempdir) / "source-packs"
    records = []
    for key, filename, content in (
        ("ATT1", "Main.pdf", b"synthetic main PDF bytes\n"),
        ("ATT2", "Supplement.pdf", b"synthetic supplementary PDF bytes\n"),
    ):
        pdf = base / filename
        pdf.write_bytes(content)
        records.append(
            replace(
                _evidence(pdf),
                item_key=item_key,
                attachment_key=key,
                canonical_filename=filename,
                expected_sha256=hashlib.sha256(content).hexdigest(),
                file_size_bytes=len(content),
                zotero_version=7,
            )
        )
    result = write_source_pack_from_recovered_pdfs(
        evidence_records=records,
        source_pack_root=root,
        created_at="2026-09-28T00:00:00+00:00",
    )
    pack = result.source_pack_dir
    manifest = json.loads((pack / "manifest.json").read_bytes())
    scope = {
        "schema_version": "millefeuille-source-scope/v0.1",
        "source_hash": manifest["source_hash"],
        "sources": [
            {
                "attachment_key": source["identity"]["zotero_attachment_key"],
                "canonical_filename": source["identity"]["canonical_filename"],
                "zotero_version": source["identity"]["zotero_version"],
                "sha256": source["sha256"],
                "source_ref": source["ref"],
            }
            for source in manifest["sources"]
        ],
    }
    # Upstream fixtures contain both attachments; no live extraction/model calls.
    selected = pack / ROUTE_MARKDOWN_REF
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(
        "# ATT1 main\nSynthetic main text.\n"
        "# ATT2 supplement\nSynthetic supplementary text.\n",
        encoding="utf-8",
    )
    _write_json(
        pack / ROUTE_EVIDENCE_REF,
        {
            "schema_version": ROUTE_SELECTION_EVIDENCE_SCHEMA_VERSION,
            "source_hash": scope["source_hash"],
            "selected_route": "fixture-multi-source",
        },
    )
    _write_json(
        pack / STRUCTURE_EVIDENCE_REF,
        {
            "schema_version": STRUCTURE_EVIDENCE_SCHEMA_VERSION,
            "source_hash": scope["source_hash"],
            "page_count": 2,
            "provenance": {
                "method": "synthetic-two-attachment-fixture",
                "provider_calls": 0,
            },
        },
    )
    run = pack / "analyses/millefeuille" / RUN_ID
    summary = json.loads(_write_summary_fixture_json(str(base)).read_bytes())
    summary.update(paper_id=pack.name, run_id=RUN_ID)
    summary["summaries"][0]["source_locators"] = ["attachment:ATT1/p.1"]
    supplement = copy.deepcopy(summary["summaries"][0])
    supplement.update(
        summary_id="supplement-page",
        text_ref="supplement.md",
        source_locators=["attachment:ATT2/p.1"],
    )
    summary["summaries"].insert(1, supplement)
    summary["summaries"][-1]["depends_on"].append("supplement-page")
    _write_json(run / "summaries/hierarchical-summary.json", summary)
    for ref in ("page-1.md", "supplement.md", "full-paper.md"):
        (run / "summaries" / ref).write_text(
            "Synthetic multi-source summary.\n", encoding="utf-8"
        )
    card = _write_card_fixture_json(str(base))
    markdown = base / "card.md"
    markdown.write_text("# Synthetic multi-source card\n", encoding="utf-8")
    index = _write_index_fixture_json(str(base))
    common = {"source_type": "zotero", "item_key": item_key, "source_scope": scope}
    card_e = base / "card-evidence.json"
    index_e = base / "index-evidence.json"
    _write_json(
        card_e,
        {
            **common,
            "schema_version": "millefeuille-card-fixture-evidence/v0.2",
            "card_json_path": str(card),
            "card_markdown_path": str(markdown),
        },
    )
    _write_json(
        index_e,
        {
            **common,
            "schema_version": "millefeuille-index-fixture-evidence/v0.2",
            "index_status_path": str(index),
        },
    )
    return root, pack, run, card_e, index_e


def _cards(case):
    return write_cards_from_evidence(
        evidence_path=case[3], source_pack_root=case[0], run_id=RUN_ID
    )


def _indexes(case):
    return write_indexes_from_evidence(
        evidence_path=case[4], source_pack_root=case[0], run_id=RUN_ID
    )


class MultiSourceMaterializationTests(unittest.TestCase):
    def test_create_refresh_and_exact_rerun_preserve_aggregate_and_members(self):
        with tempfile.TemporaryDirectory() as tempdir:
            case = _prepare(tempdir)
            root, pack, run, card_e, index_e = case
            manifest_before = (pack / "manifest.json").read_bytes()
            self.assertEqual(_cards(case)[0].status, "created")
            card_path = run / "cards/paper-card.json"
            card = json.loads(card_path.read_bytes())
            self.assertEqual(
                card["identity"]["source_hash"],
                json.loads(manifest_before)["source_hash"],
            )
            self.assertIsNone(card["identity"]["canonical_filename"])
            self.assertEqual(card["index_state"]["phase"], "planned")
            self.assertEqual(_indexes(case)[0].status, "created")
            card_bytes = card_path.read_bytes()
            index_path = run / "index/index-status.json"
            index_bytes = index_path.read_bytes()
            self.assertEqual(json.loads(card_bytes)["index_state"]["phase"], "observed")
            self.assertEqual(
                json.loads(index_bytes)["source_hash"], card["identity"]["source_hash"]
            )
            for evidence in (card_e, index_e):
                payload = json.loads(evidence.read_bytes())
                payload["source_scope"]["sources"].reverse()
                _write_json(evidence, payload)
            self.assertEqual(_cards(case)[0].status, "existing")
            self.assertEqual(_indexes(case)[0].status, "existing")
            self.assertEqual(card_path.read_bytes(), card_bytes)
            self.assertEqual(index_path.read_bytes(), index_bytes)
            self.assertEqual((pack / "manifest.json").read_bytes(), manifest_before)
            self.assertEqual(len(list((pack / "sources").glob("*.pdf"))), 2)
            self.assertFalse((pack / "source.pdf").exists())

    def test_live_v02_route_and_structure_sidecars_index_without_weakening_identity(
        self,
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            case = _prepare(tempdir)
            root, pack, run, _card_e, index_e = case
            self.assertEqual(_cards(case)[0].status, "created")
            scope = json.loads(index_e.read_bytes())["source_scope"]
            selected_text = (
                "# Synthetic two-source text\n\n"
                "## Page 1\n\n"
                "# Source: ATT1 (ATT1)\n\n"
                "Synthetic main text.\n\n"
                "## Page 2\n\n"
                "# Source: ATT2 (ATT2)\n\n"
                "Synthetic supplementary text.\n"
            )
            (pack / ROUTE_MARKDOWN_REF).write_text(selected_text, encoding="utf-8")
            selected_hash = hashlib.sha256(
                (pack / ROUTE_MARKDOWN_REF).read_bytes()
            ).hexdigest()
            rebuilt, _outline, counts, warnings = build_local_markdown_structure(
                selected_text, expected_page_count=2
            )
            common = {
                "paper_id": pack.name,
                "item_key": "ITEM1",
                "source_hash": scope["source_hash"],
                "source_scope": scope,
                "page_count": 2,
                "provider_calls": 0,
            }
            route = {
                **common,
                "schema_version": "millefeuille-route-selection-evidence/v0.2",
                "selected_route": "native",
                "output_markdown_ref": ROUTE_MARKDOWN_REF.as_posix(),
                "output_markdown_sha256": selected_hash,
                "native_evidence_ref": "extractions/native/evidence.json",
                "page_map": [
                    {
                        "attachment_key": "ATT1",
                        "source_ref": scope["sources"][0]["source_ref"],
                        "global_locator": "p.1",
                        "attachment_locator": "attachment:ATT1/p.1",
                        "character_count": len("Synthetic main text."),
                        "text_sha256": hashlib.sha256(
                            b"Synthetic main text."
                        ).hexdigest(),
                    },
                    {
                        "attachment_key": "ATT2",
                        "source_ref": scope["sources"][1]["source_ref"],
                        "global_locator": "p.2",
                        "attachment_locator": "attachment:ATT2/p.1",
                        "character_count": len("Synthetic supplementary text."),
                        "text_sha256": hashlib.sha256(
                            b"Synthetic supplementary text."
                        ).hexdigest(),
                    },
                ],
            }
            structure = {
                **common,
                "schema_version": "millefeuille-structure-evidence/v0.2",
                "selected_route": "native",
                "source_markdown_ref": ROUTE_MARKDOWN_REF.as_posix(),
                "route_evidence_ref": ROUTE_EVIDENCE_REF.as_posix(),
                "outline_markdown_ref": "structure/outline.md",
                "counts": counts,
                "warnings": warnings,
                "structure": rebuilt,
            }
            _write_json(pack / ROUTE_EVIDENCE_REF, route)
            _write_json(pack / STRUCTURE_EVIDENCE_REF, structure)
            self.assertEqual(_indexes(case)[0].status, "created")
            index_bytes = (run / "index/index-status.json").read_bytes()
            card_bytes = (run / "cards/paper-card.json").read_bytes()

            route["output_markdown_sha256"] = "0" * 64
            _write_json(pack / ROUTE_EVIDENCE_REF, route)
            with self.assertRaisesRegex(
                MillefeuilleContractError, "selected text hash drift"
            ):
                _indexes(case)
            self.assertEqual(
                (run / "index/index-status.json").read_bytes(), index_bytes
            )
            self.assertEqual((run / "cards/paper-card.json").read_bytes(), card_bytes)

            bad_cases = (
                ("missing page map", "route", "page_map", None),
                ("short page map", "route", "page_map", route["page_map"][:1]),
                ("wrong attachment", "route", "attachment_key", "UNKNOWN"),
                ("wrong source ref", "route", "source_ref", "sources/other.pdf"),
                ("wrong locator", "route", "attachment_locator", "attachment:ATT2/p.2"),
                ("swapped valid sources", "route", "swap_sources", None),
                ("fabricated page hash", "route", "text_sha256", "f" * 64),
                ("fabricated character count", "route", "character_count", 999),
                ("empty structure", "structure", "structure", {}),
                ("missing page", "structure", "pages", rebuilt["pages"][:1]),
                ("wrong count", "structure", "counts", {**counts, "pages": 1}),
            )
            route["output_markdown_sha256"] = selected_hash
            for label, target, field, value in bad_cases:
                with self.subTest(label=label):
                    bad_route = copy.deepcopy(route)
                    bad_structure = copy.deepcopy(structure)
                    if target == "route" and field in (
                        "attachment_key", "source_ref", "attachment_locator",
                        "text_sha256", "character_count",
                    ):
                        bad_route["page_map"][1][field] = value
                    elif target == "route" and field == "swap_sources":
                        for page, key, member in (
                            (0, "ATT2", scope["sources"][1]),
                            (1, "ATT1", scope["sources"][0]),
                        ):
                            row = bad_route["page_map"][page]
                            row["attachment_key"] = key
                            row["source_ref"] = member["source_ref"]
                            row["attachment_locator"] = f"attachment:{key}/p.1"
                    elif target == "structure" and field == "pages":
                        bad_structure["structure"]["pages"] = value
                    elif target == "route":
                        bad_route[field] = value
                    else:
                        bad_structure[field] = value
                    _write_json(pack / ROUTE_EVIDENCE_REF, bad_route)
                    _write_json(pack / STRUCTURE_EVIDENCE_REF, bad_structure)
                    with self.assertRaises(MillefeuilleContractError):
                        _indexes(case)
                    self.assertEqual(
                        (run / "index/index-status.json").read_bytes(), index_bytes
                    )
                    self.assertEqual(
                        (run / "cards/paper-card.json").read_bytes(), card_bytes
                    )

    def test_json_schema_and_runtime_accept_both_whole_pack_formats(self):
        schema = json.loads(
            (
                REPO_ROOT
                / "specs/millefeuille-pipeline/whole-pack-fixture-evidence.schema.json"
            ).read_bytes()
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        with tempfile.TemporaryDirectory() as tempdir:
            case = _prepare(tempdir)
            for cls, path in (
                (CardFixtureEvidence, case[3]),
                (IndexFixtureEvidence, case[4]),
            ):
                payload = json.loads(path.read_bytes())
                jsonschema.validate(payload, schema)
                evidence = cls.from_dict(payload)
                self.assertIsNone(evidence.attachment_key)
                self.assertIsNone(evidence.canonical_filename)
                self.assertIsNone(evidence.expected_sha256)
                self.assertEqual(len(evidence.source_scope.sources), 2)
                payload["attachment_key"] = "ATT1"
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(payload, schema)
                with self.assertRaises(MillefeuilleContractError):
                    cls.from_dict(payload)

    def test_incomplete_duplicate_unsafe_or_uncanonical_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            case = _prepare(tempdir)
            scope = json.loads(case[3].read_bytes())["source_scope"]
            bad_scopes = []
            one = copy.deepcopy(scope)
            one["sources"].pop()
            bad_scopes.append(one)
            duplicate = copy.deepcopy(scope)
            duplicate["sources"][1] = duplicate["sources"][0]
            bad_scopes.append(duplicate)
            for field, value in (
                ("source_ref", "../secret.pdf"),
                ("source_ref", "sources/sub/file.pdf"),
                ("canonical_filename", "../Main.pdf"),
                ("sha256", "A" * 64),
                ("zotero_version", True),
                ("zotero_version", -1),
                ("attachment_key", " ATT1 "),
            ):
                bad = copy.deepcopy(scope)
                bad["sources"][0][field] = value
                bad_scopes.append(bad)
            unknown = copy.deepcopy(scope)
            unknown["sources"][0]["extra"] = "ignored"
            bad_scopes.append(unknown)
            wrong = copy.deepcopy(scope)
            wrong["source_hash"] = "sha256-aggregate:" + "0" * 64
            bad_scopes.append(wrong)
            for bad in bad_scopes:
                with (
                    self.subTest(scope=bad),
                    self.assertRaises(MillefeuilleContractError),
                ):
                    MultiSourceScope.from_dict(bad)

    def test_full_member_identity_set_must_match_manifest(self):
        for field, value in (
            ("attachment_key", "ATT3"),
            ("canonical_filename", "Changed.pdf"),
            ("source_ref", "sources/other.pdf"),
            ("zotero_version", 8),
            ("zotero_version", None),
        ):
            with (
                self.subTest(field=field, value=value),
                tempfile.TemporaryDirectory() as tempdir,
            ):
                case = _prepare(tempdir)
                payload = json.loads(case[3].read_bytes())
                payload["source_scope"]["sources"][0][field] = value
                _write_json(case[3], payload)
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "member set drift"
                ):
                    _cards(case)
                self.assertFalse((case[2] / "cards").exists())

    def test_extra_member_with_valid_new_aggregate_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            case = _prepare(tempdir)
            payload = json.loads(case[3].read_bytes())
            extra = copy.deepcopy(payload["source_scope"]["sources"][0])
            extra.update(attachment_key="ATT3", source_ref="sources/extra.pdf")
            payload["source_scope"]["sources"].append(extra)
            payload["source_scope"]["source_hash"] = aggregate_source_hash(
                [s["sha256"] for s in payload["source_scope"]["sources"]]
            )
            _write_json(case[3], payload)
            with self.assertRaisesRegex(
                MillefeuilleContractError, "aggregate source_hash drift"
            ):
                _cards(case)
            self.assertFalse((case[2] / "cards").exists())

    def test_missing_or_changed_pdf_fails_before_card_or_index_output(self):
        for stage in ("card", "index"):
            for mutation in ("missing", "same-size-drift", "size-drift"):
                with (
                    self.subTest(stage=stage, mutation=mutation),
                    tempfile.TemporaryDirectory() as tempdir,
                ):
                    case = _prepare(tempdir)
                    card_path = case[2] / "cards/paper-card.json"
                    if stage == "index":
                        _cards(case)
                        before = card_path.read_bytes()
                    source = sorted((case[1] / "sources").glob("*.pdf"))[1]
                    if mutation == "missing":
                        source.unlink()
                    else:
                        source.write_bytes(
                            b"x" * len(source.read_bytes())
                            if mutation == "same-size-drift"
                            else b"changed"
                        )
                    with self.assertRaises(MillefeuilleContractError):
                        (_cards if stage == "card" else _indexes)(case)
                    self.assertFalse((case[2] / "index/index-status.json").exists())
                    if stage == "index":
                        self.assertEqual(card_path.read_bytes(), before)
                    else:
                        self.assertFalse(card_path.exists())

    @unittest.skipIf(
        sys.platform == "win32", "symlink creation requires Windows privileges"
    )
    def test_pdf_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            case = _prepare(tempdir)
            source = sorted((case[1] / "sources").glob("*.pdf"))[0]
            outside = Path(tempdir) / "outside.pdf"
            outside.write_bytes(source.read_bytes())
            source.unlink()
            source.symlink_to(outside)
            with self.assertRaises(MillefeuilleContractError):
                _cards(case)
            self.assertFalse((case[2] / "cards").exists())

    def test_upstream_hash_drift_and_unsafe_paper_id_are_rejected(self):
        for stage, ref in (
            ("card", STRUCTURE_EVIDENCE_REF),
            ("index", ROUTE_EVIDENCE_REF),
            ("index", STRUCTURE_EVIDENCE_REF),
        ):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tempdir:
                case = _prepare(tempdir)
                if stage == "index":
                    _cards(case)
                payload = json.loads((case[1] / ref).read_bytes())
                payload["source_hash"] = "sha256-aggregate:" + "0" * 64
                _write_json(case[1] / ref, payload)
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "source_hash drift"
                ):
                    (_cards if stage == "card" else _indexes)(case)
        with tempfile.TemporaryDirectory() as tempdir:
            case = _prepare(tempdir)
            payload = json.loads(case[3].read_bytes())
            payload["paper_id"] = "../outside"
            _write_json(case[3], payload)
            with self.assertRaisesRegex(
                MillefeuilleContractError, "safe path component"
            ):
                _cards(case)

    def test_both_batch_preflights_reject_later_drift_without_earlier_writes(self):
        for stage in ("card", "index"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tempdir:
                first = _prepare(tempdir)
                second = _prepare(tempdir, "ITEM2", root=first[0])
                if stage == "index":
                    _cards(first)
                    _cards(second)
                    before = (first[2] / "cards/paper-card.json").read_bytes()
                pos = 3 if stage == "card" else 4
                good = json.loads(first[pos].read_bytes())
                bad = json.loads(second[pos].read_bytes())
                bad["source_scope"]["sources"][0]["canonical_filename"] = "Changed.pdf"
                _write_json(first[pos], [good, bad])
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "member set drift"
                ):
                    (_cards if stage == "card" else _indexes)(first)
                if stage == "card":
                    self.assertFalse((first[2] / "cards").exists())
                else:
                    self.assertFalse((first[2] / "index/index-status.json").exists())
                    self.assertEqual(
                        (first[2] / "cards/paper-card.json").read_bytes(), before
                    )

    def test_duplicate_whole_pack_and_mixed_legacy_batch_are_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            case = _prepare(tempdir)
            payload = json.loads(case[3].read_bytes())
            legacy = copy.deepcopy(payload)
            legacy.update(
                schema_version="millefeuille-card-fixture-evidence/v0.1",
                attachment_key="ATT1",
                canonical_filename="Main.pdf",
                expected_sha256=payload["source_scope"]["sources"][0]["sha256"],
            )
            legacy.pop("source_scope")
            for batch in ([payload, payload], [payload, legacy]):
                _write_json(case[3], batch)
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "conflicts with another item record"
                ):
                    _cards(case)
            self.assertFalse((case[2] / "cards").exists())

    def test_member_mutation_between_planning_and_apply_is_rejected(self):
        for stage, module, function in (
            ("card", card_fixtures, "_apply_planned_card_write"),
            ("index", index_fixtures, "_apply_planned_index_write"),
        ):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tempdir:
                case = _prepare(tempdir)
                if stage == "index":
                    _cards(case)
                original = getattr(module, function)

                def mutate_then_apply(plan, case=case, original=original):
                    source = sorted((case[1] / "sources").glob("*.pdf"))[1]
                    source.write_bytes(b"changed after batch planning")
                    return original(plan)

                with (
                    patch.object(module, function, side_effect=mutate_then_apply),
                    self.assertRaises(MillefeuilleContractError),
                ):
                    (_cards if stage == "card" else _indexes)(case)
                self.assertFalse((case[2] / "index/index-status.json").exists())
                if stage == "card":
                    self.assertFalse((case[2] / "cards").exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux atomic exchange seam")
    def test_multi_source_card_refresh_rolls_back_concurrent_card_mutation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            case = _prepare(tempdir)
            _cards(case)
            card_path = case[2] / "cards/paper-card.json"
            concurrent = json.loads(card_path.read_bytes())
            concurrent["one_line_thesis"] = "Concurrent value retained."
            concurrent_bytes = (json.dumps(concurrent, sort_keys=True) + "\n").encode()

            def mutate(_plan):
                card_path.write_bytes(concurrent_bytes)

            with (
                patch.object(
                    index_fixtures, "_before_card_exchange", side_effect=mutate
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError, "atomic index-state commit boundary"
                ),
            ):
                _indexes(case)
            self.assertEqual(card_path.read_bytes(), concurrent_bytes)
            self.assertTrue((case[2] / "index/index-status.json").is_file())
            self.assertEqual(
                json.loads(card_path.read_bytes())["index_state"]["phase"], "planned"
            )


if __name__ == "__main__":
    unittest.main()
