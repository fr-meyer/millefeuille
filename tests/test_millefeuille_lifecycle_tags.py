"""Offline contract tests for lifecycle-tag registry and migration previews."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from millefeuille.cli.lifecycle_tags import run_lifecycle_tags_cli
from millefeuille.domain.lifecycle_tags import (
    LIFECYCLE_TAG_JSON_MAX_BYTES,
    build_lifecycle_tag_migration_plan,
    canonical_lifecycle_tag_registry,
    load_lifecycle_tag_json,
    load_lifecycle_tag_migration_plan,
    load_lifecycle_tag_registry,
    validate_lifecycle_tag_migration_plan,
    validate_lifecycle_tag_registry,
)
from millefeuille.domain.millefeuille import (
    ALLOWED_TAG_TRANSITIONS,
    AcceptanceCheckRecord,
    AcceptanceStatus,
    AcceptanceSummaryRecord,
    ClassificationDecisionRecord,
    ClassificationPlanRecord,
    MillefeuilleContractError,
    StageManifest,
    StageRecord,
    TagState,
)
from millefeuille.domain.taxonomy import (
    create_taxonomy_lock,
    seal_taxonomy_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_ROOT = REPO_ROOT / "specs" / "millefeuille-pipeline"
REGISTRY_PATH = SPEC_ROOT / "lifecycle-tag-registry.v0.1.json"
REGISTRY_SCHEMA_PATH = SPEC_ROOT / "lifecycle-tag-registry.schema.json"
PLAN_SCHEMA_PATH = SPEC_ROOT / "lifecycle-tag-migration-plan.schema.json"

PAPER_ID = "zotero-ITEM1"
ITEM_KEY = "ITEM1"
RUN_ID = "run-mf161"
SOURCE_HASH = "sha256:" + ("a" * 64)
TAXONOMY_VERSION = "taxonomy-2026-08"


def _canonical_identity(payload: dict[str, object]) -> str:
    body = deepcopy(payload)
    body.pop("content_identity", None)
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _rehash(payload: dict[str, object]) -> None:
    payload["content_identity"] = _canonical_identity(payload)


def _taxonomy_registry() -> dict[str, object]:
    return seal_taxonomy_registry(
        {
            "schema_version": "millefeuille-taxonomy-registry/v0.1",
            "registry_id": "research-papers",
            "taxonomy_version": TAXONOMY_VERSION,
            "previous_version": None,
            "previous_content_identity": None,
            "status": "released",
            "governing_basis": "primary intellectual contribution",
            "owner_id": "taxonomy-owner",
            "entries": [
                {
                    "entry_id": "L1-METHODS",
                    "level": 1,
                    "parent_id": None,
                    "label": "Methods",
                    "definition": "Research methods and systems.",
                    "include_when": ["The primary contribution is methodological."],
                    "exclude_when": ["The method is only incidental."],
                    "boundary_notes": ["Prefer the primary contribution."],
                    "status": "active",
                    "replacement_id": None,
                },
                {
                    "entry_id": "L2-OPTIMIZATION",
                    "level": 2,
                    "parent_id": "L1-METHODS",
                    "label": "Optimization",
                    "definition": "Optimization methods.",
                    "include_when": ["Optimization is the main contribution."],
                    "exclude_when": ["Optimization is only an implementation detail."],
                    "boundary_notes": ["Separate methods from applications."],
                    "status": "active",
                    "replacement_id": None,
                },
            ],
        }
    )


def _stage_and_index(
    statuses: dict[str, str],
    *,
    indexes: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    acceptance_ref = "reports/acceptance-summary.json"
    classification_plan_ref = "classification/classification-plan.json"
    classification_decision_ref = f"classification/decision-records/{PAPER_ID}.json"
    outputs: dict[str, list[str]] = {}
    artifacts: dict[str, dict[str, object]] = {
        "stage_manifest": {
            "kind": "stage-manifest",
            "ref": "stage-manifest.json",
            "format": "json",
            "stage": "discover",
            "private_content": False,
        }
    }
    if statuses.get("acceptance") in {"passed", "needs-review"}:
        outputs["acceptance"] = [
            acceptance_ref,
            "reports/acceptance-summary.md",
        ]
        artifacts["acceptance_summary"] = {
            "kind": "acceptance-summary",
            "ref": acceptance_ref,
            "format": "json",
            "stage": "acceptance",
            "private_content": False,
        }
    if statuses.get("classify") in {"passed", "needs-review"}:
        outputs["classify"] = [
            classification_plan_ref,
            classification_decision_ref,
            "classification/zotero-writeback-preview.json",
        ]
        artifacts["classification_plan"] = {
            "kind": "classification-plan",
            "ref": classification_plan_ref,
            "format": "json",
            "stage": "classify",
            "private_content": False,
        }
        artifacts["classification_decision"] = {
            "kind": "classification-decision",
            "ref": classification_decision_ref,
            "format": "json",
            "stage": "classify",
            "private_content": False,
        }
    stage_manifest = StageManifest(
        run_id=RUN_ID,
        mode="preview",
        stages=[
            StageRecord(name=name, status=status, outputs=outputs.get(name, []))
            for name, status in statuses.items()
        ],
    ).to_dict()
    artifact_index: dict[str, object] = {
        "schema_version": "millefeuille-artifact-index/v0.1",
        "paper_id": PAPER_ID,
        "run_id": RUN_ID,
        "artifact_root": "artifacts",
        "source_pack": {
            "ref": "source-pack/manifest.json",
            "source_type": "zotero",
            "source_hash": SOURCE_HASH,
        },
        "source_identity": {
            "title": "Fixture paper",
            "zotero_item_key": ITEM_KEY,
        },
        "stages": {
            name: {
                "status": status,
                "manifest_ref": "stage-manifest.json",
            }
            for name, status in statuses.items()
        },
        "artifacts": artifacts,
        "indexes": indexes or [],
        "zotero_writeback": {"mode": "preview", "status": "previewed"},
    }
    return stage_manifest, artifact_index


class LifecycleTagFixture(unittest.TestCase):
    def setUp(self) -> None:
        statuses = {
            "discover": "passed",
            "handoff": "passed",
            "recover": "passed",
            "source-pack": "passed",
            "extract-native": "passed",
            "structure": "passed",
            "summarize": "passed",
            "card": "passed",
            "openkb-add": "passed",
            "index": "passed",
            "acceptance": "passed",
            "classify": "passed",
        }
        self.stage_manifest, self.artifact_index = _stage_and_index(
            statuses,
            indexes=[
                {
                    "lane": "openkb",
                    "status": "written",
                    "result_ref": "indexes/openkb-result.json",
                },
                {
                    "lane": "pageindex",
                    "status": "skipped",
                    "skip_reason": "not enabled for this offline fixture",
                },
            ],
        )
        self.acceptance = AcceptanceSummaryRecord(
            paper_id=PAPER_ID,
            run_id=RUN_ID,
            source_hash=SOURCE_HASH,
            source_pack_ref="source-pack/manifest.json",
            status=AcceptanceStatus.PASS,
            counts={"handoff_rows": 1, "review_rows": 0},
            checks=[
                AcceptanceCheckRecord(
                    name="identity-join",
                    status="passed",
                    refs=["artifact-index.json"],
                )
            ],
        ).to_dict()
        self.classification_plan = ClassificationPlanRecord(
            run_id=RUN_ID,
            taxonomy_version=TAXONOMY_VERSION,
            mode="single",
            papers=[
                {
                    "paper_id": PAPER_ID,
                    "decision_ref": (
                        f"classification/decision-records/{PAPER_ID}.json"
                    ),
                    "decision_markdown_ref": (
                        f"classification/decision-records/{PAPER_ID}.md"
                    ),
                    "status": "classified",
                    "writeback_preview_ref": (
                        "classification/zotero-writeback-preview.json"
                    ),
                }
            ],
            default_profile="research-default",
        ).to_dict()
        self.classification_decision = ClassificationDecisionRecord(
            paper_id=PAPER_ID,
            run_id=RUN_ID,
            source_hash=SOURCE_HASH,
            taxonomy_version=TAXONOMY_VERSION,
            mode="single",
            status="classified",
            primary_path="Methods > Optimization",
            confidence="high",
            evidence_refs=["summaries/full-paper.json"],
        ).to_dict()
        self.taxonomy_registry = _taxonomy_registry()
        self.taxonomy_lock = create_taxonomy_lock(
            self.taxonomy_registry,
            lock_id="LOCK-MF161",
            scope_type="single-run",
            scope_id=RUN_ID,
            locked_by="taxonomy-owner",
            locked_at="2026-08-11T12:00:00Z",
        )

    def build(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "registry": canonical_lifecycle_tag_registry(),
            "plan_id": "PLAN-MF161",
            "item_key": ITEM_KEY,
            "zotero_version": 42,
            "current_tags": [
                "millefeuille",
                "millefeuille-processed",
                "docai-pageindex",
                "研究 notes",
            ],
            "stage_manifest_payload": deepcopy(self.stage_manifest),
            "artifact_index_payload": deepcopy(self.artifact_index),
            "acceptance_payload": deepcopy(self.acceptance),
            "classification_plan_payload": deepcopy(self.classification_plan),
            "classification_decision_payload": deepcopy(self.classification_decision),
            "taxonomy_lock": deepcopy(self.taxonomy_lock),
            "remove_selection_after_terminal_success": False,
        }
        arguments.update(overrides)
        return build_lifecycle_tag_migration_plan(**arguments)  # type: ignore[arg-type]

    def validation_evidence(self) -> dict[str, object]:
        return {
            "stage_manifest_payload": deepcopy(self.stage_manifest),
            "artifact_index_payload": deepcopy(self.artifact_index),
            "acceptance_payload": deepcopy(self.acceptance),
            "classification_plan_payload": deepcopy(self.classification_plan),
            "classification_decision_payload": deepcopy(self.classification_decision),
            "taxonomy_lock": deepcopy(self.taxonomy_lock),
        }


class TestLifecycleTagRegistry(LifecycleTagFixture):
    def test_checked_in_registry_is_exact_content_addressed_tagstate_policy(self):
        checked_in = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        runtime = canonical_lifecycle_tag_registry()

        self.assertEqual(checked_in, runtime)
        self.assertEqual(
            runtime["content_identity"],
            "sha256:f5eb1f82d936108f930e9dc84e9c0c515c416220d940bfa03501790928133844",
        )
        self.assertEqual(
            [entry["tag"] for entry in runtime["tags"]],
            [state.value for state in TagState],
        )
        self.assertEqual(
            {(entry["from"], entry["to"]) for entry in runtime["transitions"]},
            {
                (source.value, destination.value)
                for source, destination in ALLOWED_TAG_TRANSITIONS
            },
        )
        validate_lifecycle_tag_registry(runtime)

    def test_registry_and_rich_plan_validate_against_draft_2020_schemas(self):
        registry_schema = json.loads(REGISTRY_SCHEMA_PATH.read_text(encoding="utf-8"))
        plan_schema = json.loads(PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(registry_schema)
        Draft202012Validator.check_schema(plan_schema)

        Draft202012Validator(registry_schema).validate(
            canonical_lifecycle_tag_registry()
        )
        Draft202012Validator(plan_schema).validate(self.build())
        tag_values = [state.value for state in TagState]
        self.assertEqual(registry_schema["$defs"]["tag"]["enum"], tag_values)
        self.assertEqual(
            plan_schema["$defs"]["lifecycleTag"]["enum"],
            tag_values,
        )
        self.assertEqual(
            registry_schema["properties"]["transitions"]["minItems"],
            len(ALLOWED_TAG_TRANSITIONS),
        )

    def test_registry_rejects_unknown_fields_policy_drift_and_digest_tamper(self):
        for mutate in (
            lambda value: value.update({"unexpected": True}),
            lambda value: value["legacy_tags"][1].update(
                {"canonical_mapping": "millefeuille-indexed"}
            ),
            lambda value: value.update({"content_identity": "sha256:" + ("0" * 64)}),
        ):
            with self.subTest(mutate=mutate):
                payload = canonical_lifecycle_tag_registry()
                mutate(payload)
                with self.assertRaises(MillefeuilleContractError):
                    validate_lifecycle_tag_registry(payload)

        cyclic = canonical_lifecycle_tag_registry()
        cyclic["unexpected"] = cyclic
        with self.assertRaisesRegex(MillefeuilleContractError, "cyclic"):
            validate_lifecycle_tag_registry(cyclic)

    def test_strict_no_follow_loader_rejects_duplicate_nonfinite_and_nonobject(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = {
                "duplicate.json": '{"a": 1, "a": 2}',
                "nonfinite.json": '{"a": NaN}',
                "array.json": "[]",
                "deep.json": '{"a":' + ("[" * 2_000) + "0" + ("]" * 2_000) + "}",
            }
            for name, text in cases.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(MillefeuilleContractError):
                        load_lifecycle_tag_json(path, "fixture")

            target = root / "registry.json"
            target.write_text(
                json.dumps(canonical_lifecycle_tag_registry()),
                encoding="utf-8",
            )
            link = root / "registry-link.json"
            try:
                link.symlink_to(target)
            except OSError:
                pass
            else:
                with self.assertRaises(MillefeuilleContractError):
                    load_lifecycle_tag_registry(link)

    def test_loader_enforces_size_before_reader_and_at_exact_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (LIFECYCLE_TAG_JSON_MAX_BYTES + 1))
            with patch(
                "millefeuille.domain.lifecycle_tags.read_bytes_no_follow"
            ) as reader:
                with self.assertRaisesRegex(MillefeuilleContractError, "exceeds"):
                    load_lifecycle_tag_json(oversized, "oversized fixture")
                reader.assert_not_called()

            with (
                patch.object(
                    Path,
                    "stat",
                    return_value=SimpleNamespace(st_size=2),
                ),
                patch("millefeuille.domain.secure_io.os.read") as os_read,
                self.assertRaisesRegex(MillefeuilleContractError, "exceeds"),
            ):
                load_lifecycle_tag_json(oversized, "opened-size fixture")
            os_read.assert_not_called()

            raw = json.dumps(
                canonical_lifecycle_tag_registry(),
                ensure_ascii=False,
            ).encode("utf-8")
            boundary = root / "boundary.json"
            boundary.write_bytes(
                raw + (b" " * (LIFECYCLE_TAG_JSON_MAX_BYTES - len(raw)))
            )
            self.assertEqual(
                load_lifecycle_tag_registry(boundary),
                canonical_lifecycle_tag_registry(),
            )

            small = root / "small.json"
            small.write_text("{}", encoding="utf-8")
            with (
                patch(
                    "millefeuille.domain.lifecycle_tags.read_bytes_no_follow",
                    return_value=b" " * (LIFECYCLE_TAG_JSON_MAX_BYTES + 1),
                ),
                self.assertRaisesRegex(MillefeuilleContractError, "exceeds"),
            ):
                load_lifecycle_tag_json(small, "size-drift fixture")


class TestLifecycleTagMigrationSemantics(LifecycleTagFixture):
    def test_plan_binds_item_version_tags_run_source_and_every_evidence_identity(self):
        plan = self.build()
        validate_lifecycle_tag_migration_plan(
            plan,
            **self.validation_evidence(),  # type: ignore[arg-type]
        )

        self.assertEqual(plan["item"]["zotero_version"], 42)
        self.assertEqual(
            plan["run"],
            {
                "paper_id": PAPER_ID,
                "run_id": RUN_ID,
                "source_hash": SOURCE_HASH,
            },
        )
        self.assertTrue(
            all(
                binding is not None
                and str(binding["content_identity"]).startswith("sha256:")
                for binding in plan["evidence"].values()
            )
        )
        self.assertFalse(plan["writeback"]["external_effects_performed"])
        self.assertEqual(plan["writeback"]["status"], "not-executed")

        different_version = self.build(zotero_version=43)
        different_tags = self.build(current_tags=["millefeuille", "an unrelated tag"])
        self.assertNotEqual(
            plan["content_identity"], different_version["content_identity"]
        )
        self.assertNotEqual(
            plan["content_identity"], different_tags["content_identity"]
        )

    def test_unicode_and_docai_prefix_tags_are_preserved_observations(self):
        stages, index = _stage_and_index({"discover": "passed", "index": "not-started"})
        current = [
            "docai",
            "docai-pageindex",
            "docaiAnything",
            "millefeuille-processed",
            "Team review",
            "研究 notes",
        ]
        plan = self.build(
            current_tags=current,
            stage_manifest_payload=stages,
            artifact_index_payload=index,
            acceptance_payload=None,
            classification_plan_payload=None,
            classification_decision_payload=None,
            taxonomy_lock=None,
        )

        derived = {entry["tag"] for entry in plan["derived_lifecycle_tags"]}
        observed = {entry["tag"] for entry in plan["observed_legacy_tags"]}
        self.assertNotIn("millefeuille-indexed", derived)
        self.assertEqual(
            observed,
            {"docai", "docai-pageindex", "docaiAnything", "millefeuille-processed"},
        )
        self.assertEqual(plan["preserved_tags"], sorted(current))
        self.assertEqual(plan["proposed_removes"], [])

    def test_current_tags_are_exact_nfc_control_free_unique_observations(self):
        with self.assertRaisesRegex(MillefeuilleContractError, "unique"):
            self.build(current_tags=["same", "same"])
        with self.assertRaisesRegex(MillefeuilleContractError, "NFC"):
            self.build(current_tags=["e\u0301"])
        with self.assertRaisesRegex(MillefeuilleContractError, "control"):
            self.build(current_tags=["unsafe\nmarkdown"])
        with self.assertRaisesRegex(MillefeuilleContractError, "unpadded"):
            self.build(current_tags=[" padded"])

    def test_stage_index_item_and_source_scope_drift_fail_closed(self):
        wrong_run = deepcopy(self.stage_manifest)
        wrong_run["run_id"] = "run-other"
        wrong_stage = deepcopy(self.artifact_index)
        wrong_stage["stages"]["index"]["status"] = "failed"
        wrong_source = deepcopy(self.artifact_index)
        wrong_source["source_pack"]["source_hash"] = "not-a-digest"
        wrong_item = deepcopy(self.artifact_index)
        wrong_item["source_identity"]["zotero_item_key"] = "ITEM2"

        for label, overrides in (
            ("run", {"stage_manifest_payload": wrong_run}),
            ("stage", {"artifact_index_payload": wrong_stage}),
            ("source", {"artifact_index_payload": wrong_source}),
            ("item", {"artifact_index_payload": wrong_item}),
        ):
            with (
                self.subTest(label=label),
                self.assertRaises(MillefeuilleContractError),
            ):
                self.build(**overrides)

    def test_manifest_index_artifact_and_stage_output_refs_join_exactly(self):
        mutations: list[tuple[str, dict[str, object]]] = []

        for field, value in (
            ("ref", "reports/unrelated.json"),
            ("kind", "unrelated-kind"),
            ("stage", "classify"),
            ("private_content", True),
        ):
            index = deepcopy(self.artifact_index)
            index["artifacts"]["acceptance_summary"][field] = value
            mutations.append((f"acceptance-{field}", index))

        for name, field, value in (
            ("classification_plan", "ref", "classification/unrelated.json"),
            ("classification_plan", "kind", "unrelated-kind"),
            ("classification_decision", "stage", "acceptance"),
            ("classification_decision", "private_content", True),
        ):
            index = deepcopy(self.artifact_index)
            index["artifacts"][name][field] = value
            mutations.append((f"{name}-{field}", index))

        manifest_ref = deepcopy(self.artifact_index)
        manifest_ref["stages"]["acceptance"]["manifest_ref"] = "other.json"
        mutations.append(("stage-manifest-ref", manifest_ref))

        for label, index in mutations:
            with (
                self.subTest(label=label),
                self.assertRaises(MillefeuilleContractError),
            ):
                self.build(artifact_index_payload=index)

        missing_acceptance_output = deepcopy(self.stage_manifest)
        acceptance_stage = next(
            stage
            for stage in missing_acceptance_output["stages"]
            if stage["name"] == "acceptance"
        )
        acceptance_stage["outputs"].remove("reports/acceptance-summary.json")
        with self.assertRaisesRegex(MillefeuilleContractError, "outputs"):
            self.build(stage_manifest_payload=missing_acceptance_output)

        missing_decision_output = deepcopy(self.stage_manifest)
        classify_stage = next(
            stage
            for stage in missing_decision_output["stages"]
            if stage["name"] == "classify"
        )
        classify_stage["outputs"].remove(
            f"classification/decision-records/{PAPER_ID}.json"
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "outputs"):
            self.build(stage_manifest_payload=missing_decision_output)

        decision_ref_drift = deepcopy(self.classification_plan)
        decision_ref_drift["papers"][0]["decision_ref"] = (
            "classification/decision-records/zotero-ITEM2.json"
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "artifact index"):
            self.build(classification_plan_payload=decision_ref_drift)

    def test_indexed_requires_passed_stage_and_only_final_explicit_lane_evidence(self):
        for lane in (
            {"lane": "openkb", "status": "previewed"},
            {"lane": "openkb", "status": "written"},
            {"lane": "pageindex", "status": "skipped"},
        ):
            stages, index = _stage_and_index(
                {"index": "passed"},
                indexes=[lane],
            )
            with (
                self.subTest(lane=lane),
                self.assertRaises(MillefeuilleContractError),
            ):
                self.build(
                    stage_manifest_payload=stages,
                    artifact_index_payload=index,
                    acceptance_payload=None,
                    classification_plan_payload=None,
                    classification_decision_payload=None,
                    taxonomy_lock=None,
                )

        stages, index = _stage_and_index(
            {"index": "passed"},
            indexes=[
                {
                    "lane": "openkb",
                    "status": "written",
                    "result_ref": "indexes/result.json",
                }
            ],
        )
        plan = self.build(
            stage_manifest_payload=stages,
            artifact_index_payload=index,
            acceptance_payload=None,
            classification_plan_payload=None,
            classification_decision_payload=None,
            taxonomy_lock=None,
        )
        self.assertIn(
            "millefeuille-indexed",
            {entry["tag"] for entry in plan["derived_lifecycle_tags"]},
        )

    def test_acceptance_pass_requires_scope_stage_and_no_review_check(self):
        review_check = deepcopy(self.acceptance)
        review_check["checks"][0]["status"] = "needs-review"
        wrong_paper = deepcopy(self.acceptance)
        wrong_paper["paper_id"] = "zotero-ITEM2"

        for label, acceptance in (
            ("review-check", review_check),
            ("wrong-paper", wrong_paper),
        ):
            with (
                self.subTest(label=label),
                self.assertRaises(MillefeuilleContractError),
            ):
                self.build(acceptance_payload=acceptance)

        with self.assertRaisesRegex(MillefeuilleContractError, "requires acceptance"):
            self.build(acceptance_payload=None)

    def test_classified_requires_acceptance_classified_decision_and_exact_lock(self):
        review_decision = deepcopy(self.classification_decision)
        review_decision["status"] = "needs-review"
        wrong_mode = deepcopy(self.classification_decision)
        wrong_mode["mode"] = "review"
        wrong_version = deepcopy(self.classification_plan)
        wrong_version["taxonomy_version"] = "taxonomy-other"
        wrong_paper = deepcopy(self.classification_plan)
        wrong_paper["papers"][0]["paper_id"] = "zotero-ITEM2"
        empty_plan = deepcopy(self.classification_plan)
        empty_plan["papers"] = []
        batch_lock = create_taxonomy_lock(
            self.taxonomy_registry,
            lock_id="LOCK-BATCH",
            scope_type="batch",
            scope_id="batch-1",
            locked_by="taxonomy-owner",
            locked_at="2026-08-11T12:00:00Z",
        )

        cases = (
            {"classification_decision_payload": review_decision},
            {"classification_decision_payload": wrong_mode},
            {"classification_plan_payload": wrong_version},
            {"classification_plan_payload": wrong_paper},
            {"classification_plan_payload": empty_plan},
            {"taxonomy_lock": batch_lock},
            {"taxonomy_lock": None},
        )
        for overrides in cases:
            with (
                self.subTest(overrides=overrides),
                self.assertRaises(MillefeuilleContractError),
            ):
                self.build(**overrides)

        stages, index = _stage_and_index(
            {"acceptance": "not-started", "classify": "passed"}
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "passing acceptance"):
            self.build(
                stage_manifest_payload=stages,
                artifact_index_payload=index,
                acceptance_payload=None,
            )

    def test_selection_is_the_only_removal_and_requires_terminal_success(self):
        plan = self.build(remove_selection_after_terminal_success=True)
        self.assertEqual(
            [entry["tag"] for entry in plan["proposed_removes"]],
            ["millefeuille"],
        )
        self.assertIn("millefeuille-processed", plan["preserved_tags"])
        self.assertIn("docai-pageindex", plan["preserved_tags"])
        self.assertEqual(
            plan["proposed_removes"][0]["preconditions"],
            [
                "acceptance-pass-revalidated",
                "classification-decision-revalidated",
                "separate-approved-live-writeback",
                "taxonomy-lock-revalidated",
                "zotero-version-rechecked",
            ],
        )
        self.assertEqual(plan["writeback"]["expected_zotero_version"], 42)
        self.assertTrue(plan["writeback"]["requires_mf160_executor"])

        stages, index = _stage_and_index({"discover": "passed"})
        with self.assertRaisesRegex(MillefeuilleContractError, "terminal classified"):
            self.build(
                stage_manifest_payload=stages,
                artifact_index_payload=index,
                acceptance_payload=None,
                classification_plan_payload=None,
                classification_decision_payload=None,
                taxonomy_lock=None,
                remove_selection_after_terminal_success=True,
            )

    def test_unsubstantiated_current_lifecycle_tag_is_reported_not_removed(self):
        stages, index = _stage_and_index({"discover": "passed"})
        plan = self.build(
            current_tags=["millefeuille-classified", "millefeuille-processed"],
            stage_manifest_payload=stages,
            artifact_index_payload=index,
            acceptance_payload=None,
            classification_plan_payload=None,
            classification_decision_payload=None,
            taxonomy_lock=None,
        )
        self.assertEqual(
            plan["unsubstantiated_lifecycle_tags"],
            ["millefeuille-classified"],
        )
        self.assertEqual(
            plan["preserved_tags"],
            ["millefeuille-classified", "millefeuille-processed"],
        )

    def test_unsafe_decision_refs_and_noncanonical_evidence_records_fail_closed(self):
        for ref in ("../decision.json", "C:/decision.json", "https://x/y", "%2e%2e/x"):
            plan = deepcopy(self.classification_plan)
            plan["papers"][0]["decision_ref"] = ref
            with (
                self.subTest(ref=ref),
                self.assertRaises(MillefeuilleContractError),
            ):
                self.build(classification_plan_payload=plan)

        stage = deepcopy(self.stage_manifest)
        stage["unknown"] = True
        with self.assertRaisesRegex(MillefeuilleContractError, "fields are not exact"):
            self.build(stage_manifest_payload=stage)

    def test_plan_validator_rejects_rehashed_semantic_drift_and_raw_tamper(self):
        original = self.build(remove_selection_after_terminal_success=True)

        semantic_mutations = []
        changed_add = deepcopy(original)
        changed_add["proposed_adds"][0]["evidence"] = ["taxonomy_lock"]
        semantic_mutations.append(changed_add)
        changed_legacy = deepcopy(original)
        changed_legacy["observed_legacy_tags"] = []
        semantic_mutations.append(changed_legacy)
        changed_remove = deepcopy(original)
        changed_remove["proposed_removes"][0]["tag"] = "docai-pageindex"
        semantic_mutations.append(changed_remove)
        changed_write = deepcopy(original)
        changed_write["writeback"]["external_effects_performed"] = True
        semantic_mutations.append(changed_write)
        changed_scope = deepcopy(original)
        changed_scope["evidence"]["taxonomy_lock"]["scope_id"] = "run-other"
        semantic_mutations.append(changed_scope)

        for payload in semantic_mutations:
            with self.subTest(payload=payload):
                _rehash(payload)
                with self.assertRaises(MillefeuilleContractError):
                    validate_lifecycle_tag_migration_plan(
                        payload,
                        **self.validation_evidence(),  # type: ignore[arg-type]
                    )

        fabricated = deepcopy(original)
        fabricated["evidence"]["classification_decision"]["content_identity"] = (
            "sha256:" + ("f" * 64)
        )
        _rehash(fabricated)
        with self.assertRaisesRegex(MillefeuilleContractError, "rederived evidence"):
            validate_lifecycle_tag_migration_plan(
                fabricated,
                **self.validation_evidence(),  # type: ignore[arg-type]
            )

        tampered = deepcopy(original)
        tampered["plan_id"] = "PLAN-TAMPERED"
        with self.assertRaisesRegex(MillefeuilleContractError, "identity mismatch"):
            validate_lifecycle_tag_migration_plan(
                tampered,
                **self.validation_evidence(),  # type: ignore[arg-type]
            )

    def test_plan_loader_rejects_unknown_duplicate_and_tampered_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = self.build()
            valid = root / "plan.json"
            valid.write_text(json.dumps(plan), encoding="utf-8")
            self.assertEqual(
                load_lifecycle_tag_migration_plan(
                    valid,
                    **self.validation_evidence(),  # type: ignore[arg-type]
                ),
                plan,
            )

            unknown = deepcopy(plan)
            unknown["unknown"] = True
            unknown_path = root / "unknown.json"
            unknown_path.write_text(json.dumps(unknown), encoding="utf-8")
            with self.assertRaises(MillefeuilleContractError):
                load_lifecycle_tag_migration_plan(
                    unknown_path,
                    **self.validation_evidence(),  # type: ignore[arg-type]
                )

            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"schema_version":"a","schema_version":"b"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MillefeuilleContractError, "duplicate"):
                load_lifecycle_tag_migration_plan(
                    duplicate,
                    **self.validation_evidence(),  # type: ignore[arg-type]
                )


class TestLifecycleTagsCli(LifecycleTagFixture):
    def _write_bundle(self, root: Path) -> dict[str, Path]:
        payloads = {
            "registry": canonical_lifecycle_tag_registry(),
            "stage": self.stage_manifest,
            "index": self.artifact_index,
            "acceptance": self.acceptance,
            "classification_plan": self.classification_plan,
            "classification_decision": self.classification_decision,
            "lock": self.taxonomy_lock,
        }
        paths: dict[str, Path] = {}
        for name, payload in payloads.items():
            path = root / f"{name}.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            paths[name] = path
        return paths

    def test_registry_plan_and_validate_cli_are_deterministic_no_effect_commands(self):
        registry_out = StringIO()
        self.assertEqual(
            run_lifecycle_tags_cli(["registry"], stdout=registry_out),
            0,
        )
        self.assertEqual(
            json.loads(registry_out.getvalue()),
            canonical_lifecycle_tag_registry(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self._write_bundle(root)
            files_before = sorted(path.name for path in root.iterdir())
            output = StringIO()
            error = StringIO()
            exit_code = run_lifecycle_tags_cli(
                [
                    "plan",
                    "--registry",
                    str(paths["registry"]),
                    "--plan-id",
                    "PLAN-CLI",
                    "--item-key",
                    ITEM_KEY,
                    "--zotero-version",
                    "42",
                    "--current-tag",
                    "millefeuille",
                    "--current-tag",
                    "docai-pageindex",
                    "--stage-manifest",
                    str(paths["stage"]),
                    "--artifact-index",
                    str(paths["index"]),
                    "--acceptance-summary",
                    str(paths["acceptance"]),
                    "--classification-plan",
                    str(paths["classification_plan"]),
                    "--classification-decision",
                    str(paths["classification_decision"]),
                    "--taxonomy-lock",
                    str(paths["lock"]),
                    "--remove-selection-after-terminal-success",
                ],
                stdout=output,
                stderr=error,
            )
            self.assertEqual(exit_code, 0, error.getvalue())
            plan = json.loads(output.getvalue())
            validate_lifecycle_tag_migration_plan(
                plan,
                **self.validation_evidence(),  # type: ignore[arg-type]
            )
            self.assertFalse(plan["writeback"]["external_effects_performed"])
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                files_before,
            )

            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            missing_evidence_out = StringIO()
            missing_evidence_error = StringIO()
            self.assertEqual(
                run_lifecycle_tags_cli(
                    [
                        "validate",
                        "--registry",
                        str(paths["registry"]),
                        "--plan",
                        str(plan_path),
                    ],
                    stdout=missing_evidence_out,
                    stderr=missing_evidence_error,
                ),
                2,
            )
            self.assertEqual(missing_evidence_out.getvalue(), "")
            self.assertIn(
                "requires --stage-manifest", missing_evidence_error.getvalue()
            )

            validation_out = StringIO()
            self.assertEqual(
                run_lifecycle_tags_cli(
                    [
                        "validate",
                        "--registry",
                        str(paths["registry"]),
                        "--plan",
                        str(plan_path),
                        "--stage-manifest",
                        str(paths["stage"]),
                        "--artifact-index",
                        str(paths["index"]),
                        "--acceptance-summary",
                        str(paths["acceptance"]),
                        "--classification-plan",
                        str(paths["classification_plan"]),
                        "--classification-decision",
                        str(paths["classification_decision"]),
                        "--taxonomy-lock",
                        str(paths["lock"]),
                    ],
                    stdout=validation_out,
                    stderr=error,
                ),
                0,
            )
            validation = json.loads(validation_out.getvalue())
            self.assertEqual(validation["status"], "valid")
            self.assertEqual(validation["plan_id"], "PLAN-CLI")
            self.assertFalse(validation["external_effects_performed"])

    def test_cli_rejects_empty_classification_plan_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self._write_bundle(root)
            empty_plan = deepcopy(self.classification_plan)
            empty_plan["papers"] = []
            paths["classification_plan"].write_text(
                json.dumps(empty_plan),
                encoding="utf-8",
            )
            output = StringIO()
            error = StringIO()
            self.assertEqual(
                run_lifecycle_tags_cli(
                    [
                        "plan",
                        "--registry",
                        str(paths["registry"]),
                        "--plan-id",
                        "PLAN-EMPTY-CLASSIFICATION",
                        "--item-key",
                        ITEM_KEY,
                        "--zotero-version",
                        "42",
                        "--stage-manifest",
                        str(paths["stage"]),
                        "--artifact-index",
                        str(paths["index"]),
                        "--acceptance-summary",
                        str(paths["acceptance"]),
                        "--classification-plan",
                        str(paths["classification_plan"]),
                        "--classification-decision",
                        str(paths["classification_decision"]),
                        "--taxonomy-lock",
                        str(paths["lock"]),
                    ],
                    stdout=output,
                    stderr=error,
                ),
                2,
            )
            self.assertEqual(output.getvalue(), "")
            self.assertIn("papers must be a non-empty array", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

    def test_cli_fails_closed_without_echoing_malformed_payload(self):
        synthetic_marker = "SYNTHETIC-MF161-SENTINEL-7X9Q"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "registry.json"
            path.write_text(
                '{"registry_id":"' + synthetic_marker + '","registry_id":"duplicate"}',
                encoding="utf-8",
            )
            output = StringIO()
            error = StringIO()
            self.assertEqual(
                run_lifecycle_tags_cli(
                    ["validate", "--registry", str(path)],
                    stdout=output,
                    stderr=error,
                ),
                2,
            )
            combined = output.getvalue() + error.getvalue()
            self.assertNotIn(synthetic_marker, combined)
            self.assertEqual(output.getvalue(), "")

    def test_packaged_entrypoint_dispatches_lifecycle_tags_before_hydra(self):
        from millefeuille.cli import main as cli_main

        with (
            patch.object(sys, "argv", ["millefeuille", "lifecycle-tags", "registry"]),
            patch.object(cli_main, "run_lifecycle_tags_cli", return_value=0) as runner,
            self.assertRaises(SystemExit) as raised,
        ):
            cli_main.entrypoint()
        self.assertEqual(raised.exception.code, 0)
        runner.assert_called_once_with(["registry"])


if __name__ == "__main__":
    unittest.main()
