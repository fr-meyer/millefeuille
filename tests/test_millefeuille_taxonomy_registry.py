from __future__ import annotations

from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from millefeuille.cli.taxonomy import run_taxonomy_cli
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.taxonomy import (
    MAX_TAXONOMY_AFFECTED_ENTRY_IDS,
    MAX_TAXONOMY_ENTRIES,
    MAX_TAXONOMY_ENTRY_RULES,
    MAX_TAXONOMY_EVIDENCE_REFS,
    MAX_TAXONOMY_REVIEWS,
    REQUIRED_REVIEW_ROLES,
    TAXONOMY_REGISTRY_SCHEMA_VERSION,
    apply_taxonomy_change,
    canonical_content_identity,
    create_taxonomy_change_proposal,
    create_taxonomy_change_review,
    create_taxonomy_lock,
    load_taxonomy_registry,
    rollback_taxonomy_change,
    seal_taxonomy_registry,
    validate_taxonomy_change_proposal,
    validate_taxonomy_lock,
    validate_taxonomy_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_ROOT = REPO_ROOT / "specs" / "millefeuille-pipeline"


def _entry(
    entry_id: str,
    *,
    level: int,
    parent_id: str | None,
    label: str,
    definition: str | None = None,
    status: str = "active",
    replacement_id: str | None = None,
) -> dict[str, object]:
    return {
        "entry_id": entry_id,
        "level": level,
        "parent_id": parent_id,
        "label": label,
        "definition": definition or f"Definition for {label}.",
        "include_when": [f"Include evidence supports {label}."],
        "exclude_when": [f"Exclude evidence supports another node, not {label}."],
        "boundary_notes": [f"Compare {label} with its nearest sibling."],
        "status": status,
        "replacement_id": replacement_id,
    }


def _registry(
    version: str,
    *,
    previous_version: str | None = None,
    previous_registry: dict[str, object] | None = None,
    training_label: str = "Training",
    status: str = "released",
    entries: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if previous_registry is not None:
        if previous_version is not None:
            raise AssertionError("pass previous_registry or previous_version, not both")
        previous_version = str(previous_registry["taxonomy_version"])
        previous_content_identity = str(previous_registry["content_identity"])
    else:
        if previous_version is not None:
            raise AssertionError(
                "non-root test registries must supply their exact previous_registry"
            )
        previous_content_identity = None
    return seal_taxonomy_registry(
        {
            "schema_version": TAXONOMY_REGISTRY_SCHEMA_VERSION,
            "registry_id": "research-papers",
            "taxonomy_version": version,
            "previous_version": previous_version,
            "previous_content_identity": previous_content_identity,
            "status": status,
            "governing_basis": "primary intellectual contribution",
            "owner_id": "taxonomy-owner",
            "entries": entries
            or [
                _entry(
                    "L1-METHODS",
                    level=1,
                    parent_id=None,
                    label="Methods",
                ),
                _entry(
                    "L2-TRAINING",
                    level=2,
                    parent_id="L1-METHODS",
                    label=training_label,
                ),
            ],
        }
    )


def _proposal(
    base: dict[str, object],
    candidate: dict[str, object],
    *,
    operation: str = "rename",
    rollback_source: dict[str, object] | None = None,
    affected_entry_ids: list[str] | None = None,
) -> dict[str, object]:
    return create_taxonomy_change_proposal(
        base_registry=base,
        candidate_registry=candidate,
        change_id=("TCR-ROLLBACK" if operation == "rollback" else "TCR-CHANGE"),
        operation=operation,
        affected_entry_ids=(
            ["L2-TRAINING"] if affected_entry_ids is None else affected_entry_ids
        ),
        reason="Resolve a repeated, evidenced boundary issue.",
        evidence_refs=["evidence/TCR-CHANGE.md"],
        impact_risk="medium",
        impact_summary="Existing decisions require a bounded review.",
        estimated_affected_records=12,
        historical_reclassification="review",
        migration_instructions="Queue affected records; preserve active locks.",
        requested_by="requester-a",
        requested_at="2026-08-11T12:00:00Z",
        rollback_source_registry=rollback_source,
    )


def _reviews(
    proposal: dict[str, object],
    base_registry: dict[str, object],
    *,
    decision: str = "approve",
) -> list[dict[str, object]]:
    return [
        create_taxonomy_change_review(
            proposal,
            base_registry=base_registry,
            review_id=f"REVIEW-{index}",
            role=role,
            decision=decision,
            reviewer_id=f"reviewer-{index}",
            reviewed_at=f"2026-08-11T12:0{index}:00Z",
            notes="Reviewed the exact candidate, impact, and migration plan.",
        )
        for index, role in enumerate(sorted(REQUIRED_REVIEW_ROLES), start=1)
    ]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    payload["content_identity"] = canonical_content_identity(payload)
    return payload


class TaxonomyRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _registry("v19", previous_version=None)
        renamed_training = deepcopy(self.base["entries"][1])
        renamed_training["label"] = "Training methods"
        self.candidate = _registry(
            "v20",
            previous_registry=self.base,
            entries=[
                deepcopy(self.base["entries"][0]),
                renamed_training,
            ],
        )
        self.proposal = _proposal(self.base, self.candidate)
        self.reviews = _reviews(self.proposal, self.base)

    def test_checked_in_example_is_nonproduction_and_content_addressed(self):
        example = load_taxonomy_registry(SPEC_ROOT / "taxonomy-registry.example.json")

        self.assertEqual(example["status"], "draft")
        self.assertEqual(
            example["content_identity"], canonical_content_identity(example)
        )
        with self.assertRaisesRegex(
            MillefeuilleContractError, "only a released taxonomy registry"
        ):
            create_taxonomy_lock(
                example,
                lock_id="LOCK-EXAMPLE",
                scope_type="pilot",
                scope_id="PILOT-1",
                locked_by="owner-a",
                locked_at="2026-08-11T12:00:00Z",
            )

    def test_sealing_is_deterministic_and_loading_preserves_canonical_order(self):
        draft = deepcopy(self.base)
        draft.pop("content_identity")
        draft["entries"].reverse()
        for entry in draft["entries"]:
            entry["boundary_notes"] = sorted(
                [*entry["boundary_notes"], "An earlier boundary note."]
            )[::-1]

        first = seal_taxonomy_registry(draft)
        second = seal_taxonomy_registry(deepcopy(draft))

        self.assertEqual(first, second)
        self.assertEqual(
            [entry["entry_id"] for entry in first["entries"]],
            ["L1-METHODS", "L2-TRAINING"],
        )
        self.assertEqual(first["content_identity"], canonical_content_identity(first))

    def test_registry_fails_closed_on_hash_duplicates_and_malformed_entries(self):
        tampered = deepcopy(self.base)
        tampered["entries"][1]["label"] = "Tampered"
        with self.assertRaisesRegex(MillefeuilleContractError, "identity drift"):
            validate_taxonomy_registry(tampered)

        duplicate_id = deepcopy(self.base)
        duplicate_id["entries"].append(deepcopy(duplicate_id["entries"][1]))
        duplicate_id["entries"].sort(key=lambda entry: entry["entry_id"])
        _rehash(duplicate_id)
        with self.assertRaisesRegex(MillefeuilleContractError, "unique entry_id"):
            validate_taxonomy_registry(duplicate_id)

        duplicate_label = deepcopy(self.base)
        duplicate_label["entries"].append(
            _entry(
                "L2-TRAINING-ALT",
                level=2,
                parent_id="L1-METHODS",
                label="training",
            )
        )
        duplicate_label["entries"].sort(key=lambda entry: entry["entry_id"])
        _rehash(duplicate_label)
        with self.assertRaisesRegex(MillefeuilleContractError, "sibling labels"):
            validate_taxonomy_registry(duplicate_label)

        bad_parent = deepcopy(self.base)
        bad_parent["entries"][1]["parent_id"] = "L1-MISSING"
        _rehash(bad_parent)
        with self.assertRaisesRegex(MillefeuilleContractError, "existing level 1"):
            validate_taxonomy_registry(bad_parent)

        unsorted = deepcopy(self.base)
        unsorted["entries"].reverse()
        _rehash(unsorted)
        with self.assertRaisesRegex(MillefeuilleContractError, "sorted by entry_id"):
            validate_taxonomy_registry(unsorted)

    def test_taxonomy_collection_limits_accept_boundary_and_reject_overflow(self):
        bounded_entries = [
            _entry(
                "L1-ROOT",
                level=1,
                parent_id=None,
                label="Root category",
            ),
            *[
                _entry(
                    f"L2-{index:04d}",
                    level=2,
                    parent_id="L1-ROOT",
                    label=f"Category {index:04d}",
                )
                for index in range(MAX_TAXONOMY_ENTRIES - 1)
            ],
        ]
        bounded_base = _registry("bounded-v1", entries=bounded_entries)
        self.assertEqual(len(bounded_base["entries"]), MAX_TAXONOMY_ENTRIES)

        with self.assertRaisesRegex(MillefeuilleContractError, "at most 512"):
            _registry(
                "overflow-v1",
                status="draft",
                entries=[
                    *bounded_entries,
                    _entry(
                        "L2-OVERFLOW",
                        level=2,
                        parent_id="L1-ROOT",
                        label="Overflow category",
                    ),
                ],
            )

        for field_name in ("include_when", "exclude_when", "boundary_notes"):
            with self.subTest(bounded_entry_collection=field_name):
                bounded_registry = deepcopy(self.base)
                bounded_registry.pop("content_identity")
                bounded_registry["entries"][0][field_name] = [
                    f"Rule {index:02d}." for index in range(MAX_TAXONOMY_ENTRY_RULES)
                ]
                sealed = seal_taxonomy_registry(bounded_registry)
                self.assertEqual(
                    len(sealed["entries"][0][field_name]),
                    MAX_TAXONOMY_ENTRY_RULES,
                )

                overflow_registry = deepcopy(bounded_registry)
                overflow_registry["entries"][0][field_name].append("Rule overflow.")
                with self.assertRaisesRegex(MillefeuilleContractError, "at most 32"):
                    seal_taxonomy_registry(overflow_registry)

        changed_entries = deepcopy(bounded_base["entries"])
        for entry in changed_entries:
            entry["definition"] = f"{entry['definition']} Revised."
        bounded_candidate = _registry(
            "bounded-v2",
            previous_registry=bounded_base,
            entries=changed_entries,
        )
        affected_ids = [entry["entry_id"] for entry in bounded_candidate["entries"]]
        bounded_proposal = _proposal(
            bounded_base,
            bounded_candidate,
            operation="mixed",
            affected_entry_ids=affected_ids,
        )
        self.assertEqual(
            len(bounded_proposal["affected_entry_ids"]),
            MAX_TAXONOMY_AFFECTED_ENTRY_IDS,
        )

        overflow_affected = deepcopy(self.proposal)
        overflow_affected["affected_entry_ids"] = [
            f"L2-{index:04d}" for index in range(MAX_TAXONOMY_AFFECTED_ENTRY_IDS + 1)
        ]
        _rehash(overflow_affected)
        with self.assertRaisesRegex(MillefeuilleContractError, "at most 512"):
            validate_taxonomy_change_proposal(
                overflow_affected, base_registry=self.base
            )

        for count in (
            MAX_TAXONOMY_EVIDENCE_REFS,
            MAX_TAXONOMY_EVIDENCE_REFS + 1,
        ):
            evidence_proposal = deepcopy(self.proposal)
            evidence_proposal["evidence_refs"] = [
                f"evidence/{index:03d}.md" for index in range(count)
            ]
            _rehash(evidence_proposal)
            if count == MAX_TAXONOMY_EVIDENCE_REFS:
                self.assertEqual(
                    len(
                        validate_taxonomy_change_proposal(
                            evidence_proposal, base_registry=self.base
                        )["evidence_refs"]
                    ),
                    MAX_TAXONOMY_EVIDENCE_REFS,
                )
            else:
                with self.assertRaisesRegex(MillefeuilleContractError, "at most 64"):
                    validate_taxonomy_change_proposal(
                        evidence_proposal, base_registry=self.base
                    )

        optional_review = create_taxonomy_change_review(
            self.proposal,
            base_registry=self.base,
            review_id="REVIEW-OPTIONAL-BOUNDS",
            role="subject-matter-reviewer",
            decision="approve",
            reviewer_id="reviewer-optional-bounds",
            reviewed_at="2026-08-11T12:10:00Z",
            notes="Optional bounded review.",
        )
        _, bounded_application = apply_taxonomy_change(
            base_registry=self.base,
            proposal=self.proposal,
            reviews=[*self.reviews, optional_review],
            application_id="APPLY-BOUNDED-REVIEWS",
            applied_by="release-owner",
            applied_at="2026-08-11T13:00:00Z",
        )
        self.assertEqual(
            len(bounded_application["review_content_identities"]),
            MAX_TAXONOMY_REVIEWS,
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "at most 4"):
            apply_taxonomy_change(
                base_registry=self.base,
                proposal=self.proposal,
                reviews=[*self.reviews, optional_review, deepcopy(optional_review)],
                application_id="APPLY-OVERFLOW-REVIEWS",
                applied_by="release-owner",
                applied_at="2026-08-11T13:00:00Z",
            )

    def test_lock_embeds_exact_snapshot_and_rejects_registry_or_snapshot_drift(self):
        lock = create_taxonomy_lock(
            self.base,
            lock_id="LOCK-19",
            scope_type="batch",
            scope_id="BATCH-19",
            locked_by="owner-a",
            locked_at="2026-08-11T12:00:00Z",
        )
        original = deepcopy(lock)

        self.assertEqual(validate_taxonomy_lock(lock, registry=self.base), original)
        with self.assertRaisesRegex(
            MillefeuilleContractError, "does not match the supplied registry"
        ):
            validate_taxonomy_lock(lock, registry=self.candidate)

        mutated_snapshot = deepcopy(lock)
        mutated_snapshot["registry_snapshot"]["entries"][1]["label"] = "Changed"
        _rehash(mutated_snapshot)
        with self.assertRaisesRegex(MillefeuilleContractError, "identity drift"):
            validate_taxonomy_lock(mutated_snapshot)

        draft_snapshot = deepcopy(lock)
        draft_snapshot["registry_snapshot"]["status"] = "draft"
        _rehash(draft_snapshot["registry_snapshot"])
        draft_snapshot["registry_content_identity"] = draft_snapshot[
            "registry_snapshot"
        ]["content_identity"]
        _rehash(draft_snapshot)
        with self.assertRaisesRegex(MillefeuilleContractError, "must be released"):
            validate_taxonomy_lock(draft_snapshot)

    def test_unreleased_bases_malformed_drafts_and_time_travel_fail_closed(self):
        malformed_draft = deepcopy(self.base)
        malformed_draft.pop("content_identity")
        malformed_draft["entries"][0]["include_when"] = ["valid", 7]
        with self.assertRaisesRegex(MillefeuilleContractError, "unpadded string"):
            seal_taxonomy_registry(malformed_draft)

        draft_base = deepcopy(self.base)
        draft_base["status"] = "draft"
        _rehash(draft_base)
        with self.assertRaisesRegex(MillefeuilleContractError, "base registry"):
            create_taxonomy_change_proposal(
                base_registry=draft_base,
                candidate_registry=self.candidate,
                change_id="TCR-DRAFT",
                operation="rename",
                affected_entry_ids=["L2-TRAINING"],
                reason="Drafts cannot authorize a transition.",
                evidence_refs=["evidence/draft.md"],
                impact_risk="low",
                impact_summary="No valid released base exists.",
                estimated_affected_records=0,
                historical_reclassification="none",
                migration_instructions="Release the base first.",
                requested_by="requester-a",
                requested_at="2026-08-11T12:00:00Z",
            )

        with self.assertRaisesRegex(MillefeuilleContractError, "predate"):
            create_taxonomy_change_review(
                self.proposal,
                base_registry=self.base,
                review_id="REVIEW-TIME-TRAVEL",
                role="taxonomy-owner",
                decision="approve",
                reviewer_id="owner-a",
                reviewed_at="2026-08-11T11:59:59Z",
                notes="This review predates the request.",
            )

        with self.assertRaisesRegex(MillefeuilleContractError, "predate"):
            apply_taxonomy_change(
                base_registry=self.base,
                proposal=self.proposal,
                reviews=self.reviews,
                application_id="APPLY-TIME-TRAVEL",
                applied_by="release-owner",
                applied_at="2026-08-11T12:00:01Z",
            )

    def test_proposal_requires_exact_diff_and_preserves_stable_node_identity(self):
        wrong_diff = deepcopy(self.proposal)
        wrong_diff["affected_entry_ids"] = ["L1-METHODS"]
        _rehash(wrong_diff)
        with self.assertRaisesRegex(MillefeuilleContractError, "exact registry diff"):
            validate_taxonomy_change_proposal(wrong_diff, base_registry=self.base)

        deleted_entries = [
            deepcopy(self.base["entries"][0]),
            _entry(
                "L2-REPLACEMENT",
                level=2,
                parent_id="L1-METHODS",
                label="Replacement training",
            ),
        ]
        deleted_candidate = _registry(
            "v20",
            previous_registry=self.base,
            entries=deleted_entries,
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "cannot delete"):
            create_taxonomy_change_proposal(
                base_registry=self.base,
                candidate_registry=deleted_candidate,
                change_id="TCR-DELETE",
                operation="deprecate",
                affected_entry_ids=["L2-REPLACEMENT", "L2-TRAINING"],
                reason="Attempted deletion.",
                evidence_refs=["evidence/delete.md"],
                impact_risk="high",
                impact_summary="Would erase a stable ID.",
                estimated_affected_records=1,
                historical_reclassification="required",
                migration_instructions="This must fail.",
                requested_by="requester-a",
                requested_at="2026-08-11T12:00:00Z",
            )

        moved_training = deepcopy(self.candidate["entries"][1])
        moved_training["parent_id"] = "L1-OTHER"
        moved = _registry(
            "v20",
            previous_registry=self.base,
            entries=[
                deepcopy(self.base["entries"][0]),
                _entry(
                    "L1-OTHER",
                    level=1,
                    parent_id=None,
                    label="Other methods",
                ),
                _entry(
                    "L2-METHODS-REPLACEMENT",
                    level=2,
                    parent_id="L1-METHODS",
                    label="Replacement training",
                ),
                moved_training,
            ],
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "reassign"):
            create_taxonomy_change_proposal(
                base_registry=self.base,
                candidate_registry=moved,
                change_id="TCR-MOVE",
                operation="mixed",
                affected_entry_ids=[
                    "L1-OTHER",
                    "L2-METHODS-REPLACEMENT",
                    "L2-TRAINING",
                ],
                reason="Attempted ID reuse.",
                evidence_refs=["evidence/move.md"],
                impact_risk="high",
                impact_summary="Would reuse an ID for another level.",
                estimated_affected_records=1,
                historical_reclassification="required",
                migration_instructions="This must fail.",
                requested_by="requester-a",
                requested_at="2026-08-11T12:00:00Z",
            )

    def test_specific_proposal_operations_match_their_exact_diff_shapes(self):
        clarified_training = deepcopy(self.base["entries"][1])
        clarified_training["definition"] = "A clarified training definition."
        clarify_candidate = _registry(
            "v20",
            previous_registry=self.base,
            entries=[deepcopy(self.base["entries"][0]), clarified_training],
        )
        added_entry = _entry(
            "L2-EVALUATION",
            level=2,
            parent_id="L1-METHODS",
            label="Evaluation methods",
        )
        add_candidate = _registry(
            "v20",
            previous_registry=self.base,
            entries=[
                deepcopy(self.base["entries"][0]),
                added_entry,
                deepcopy(self.base["entries"][1]),
            ],
        )
        deprecate_base = _registry(
            "deprecate-v1",
            entries=[
                deepcopy(self.base["entries"][0]),
                deepcopy(added_entry),
                deepcopy(self.base["entries"][1]),
            ],
        )
        deprecated_training = deepcopy(deprecate_base["entries"][2])
        deprecated_training["status"] = "deprecated"
        deprecate_candidate = _registry(
            "deprecate-v2",
            previous_registry=deprecate_base,
            entries=[
                deepcopy(deprecate_base["entries"][0]),
                deepcopy(deprecate_base["entries"][1]),
                deprecated_training,
            ],
        )
        split_candidate = _registry(
            "v20",
            previous_registry=self.base,
            entries=[
                deepcopy(self.base["entries"][0]),
                deprecated_training,
                _entry(
                    "L2-TRAINING-SUPERVISED",
                    level=2,
                    parent_id="L1-METHODS",
                    label="Supervised training",
                ),
                _entry(
                    "L2-TRAINING-UNSUPERVISED",
                    level=2,
                    parent_id="L1-METHODS",
                    label="Unsupervised training",
                ),
            ],
        )
        merge_base = _registry(
            "merge-v1",
            entries=[
                deepcopy(self.base["entries"][0]),
                _entry(
                    "L2-TRAINING-A", level=2, parent_id="L1-METHODS", label="Training A"
                ),
                _entry(
                    "L2-TRAINING-B", level=2, parent_id="L1-METHODS", label="Training B"
                ),
            ],
        )
        merged_entries = deepcopy(merge_base["entries"])
        for entry in merged_entries[1:]:
            entry["status"] = "deprecated"
            entry["replacement_id"] = "L2-TRAINING-COMBINED"
        merged_entries.append(
            _entry(
                "L2-TRAINING-COMBINED",
                level=2,
                parent_id="L1-METHODS",
                label="Combined training",
            )
        )
        merge_candidate = _registry(
            "merge-v2", previous_registry=merge_base, entries=merged_entries
        )

        valid_cases = [
            ("add", self.base, add_candidate, ["L2-EVALUATION"]),
            ("clarify", self.base, clarify_candidate, ["L2-TRAINING"]),
            ("rename", self.base, self.candidate, ["L2-TRAINING"]),
            ("deprecate", deprecate_base, deprecate_candidate, ["L2-TRAINING"]),
            (
                "split",
                self.base,
                split_candidate,
                ["L2-TRAINING", "L2-TRAINING-SUPERVISED", "L2-TRAINING-UNSUPERVISED"],
            ),
            (
                "merge",
                merge_base,
                merge_candidate,
                ["L2-TRAINING-A", "L2-TRAINING-B", "L2-TRAINING-COMBINED"],
            ),
            ("mixed", self.base, add_candidate, ["L2-EVALUATION"]),
        ]
        for operation, base, candidate, affected in valid_cases:
            with self.subTest(valid_operation=operation):
                _proposal(
                    base, candidate, operation=operation, affected_entry_ids=affected
                )

        base_entries = {item["entry_id"]: item for item in self.base["entries"]}
        mismatched_cases = [
            ("add", self.candidate),
            ("clarify", add_candidate),
            ("rename", clarify_candidate),
            ("deprecate", self.candidate),
            ("split", add_candidate),
            ("merge", split_candidate),
        ]
        for operation, candidate in mismatched_cases:
            affected = sorted(
                entry["entry_id"]
                for entry in candidate["entries"]
                if entry["entry_id"] not in base_entries
                or entry != base_entries.get(entry["entry_id"])
            )
            with (
                self.subTest(mismatched_operation=operation),
                self.assertRaisesRegex(MillefeuilleContractError, "does not match"),
            ):
                _proposal(
                    self.base,
                    candidate,
                    operation=operation,
                    affected_entry_ids=affected,
                )

    def test_application_requires_exact_three_role_approval_and_separation(self):
        for role in sorted(REQUIRED_REVIEW_ROLES):
            with (
                self.subTest(role=role),
                self.assertRaisesRegex(
                    MillefeuilleContractError, "independent from the requester"
                ),
            ):
                create_taxonomy_change_review(
                    self.proposal,
                    base_registry=self.base,
                    review_id=f"REVIEW-REQUESTER-{role}",
                    role=role,
                    decision="approve",
                    reviewer_id=self.proposal["requested_by"],
                    reviewed_at="2026-08-11T12:10:00Z",
                    notes="A requester cannot provide a required approval.",
                )

        with self.assertRaisesRegex(MillefeuilleContractError, "missing required"):
            apply_taxonomy_change(
                base_registry=self.base,
                proposal=self.proposal,
                reviews=self.reviews[:2],
                application_id="APPLY-1",
                applied_by="release-owner",
                applied_at="2026-08-11T13:00:00Z",
            )

        rejected = deepcopy(self.reviews)
        rejected[0] = create_taxonomy_change_review(
            self.proposal,
            base_registry=self.base,
            review_id="REVIEW-REJECT",
            role=rejected[0]["role"],
            decision="reject",
            reviewer_id="rejecter-a",
            reviewed_at="2026-08-11T12:10:00Z",
            notes="Impact analysis is incomplete.",
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "non-approval"):
            apply_taxonomy_change(
                base_registry=self.base,
                proposal=self.proposal,
                reviews=rejected,
                application_id="APPLY-2",
                applied_by="release-owner",
                applied_at="2026-08-11T13:00:00Z",
            )

        reused_reviewer = deepcopy(self.reviews)
        reused_reviewer[1]["reviewer_id"] = reused_reviewer[0]["reviewer_id"]
        _rehash(reused_reviewer[1])
        with self.assertRaisesRegex(MillefeuilleContractError, "distinct reviewers"):
            apply_taxonomy_change(
                base_registry=self.base,
                proposal=self.proposal,
                reviews=reused_reviewer,
                application_id="APPLY-3",
                applied_by="release-owner",
                applied_at="2026-08-11T13:00:00Z",
            )

        stale = deepcopy(self.reviews)
        stale[0]["proposal_content_identity"] = "sha256:" + "0" * 64
        _rehash(stale[0])
        with self.assertRaisesRegex(MillefeuilleContractError, "identity drift"):
            apply_taxonomy_change(
                base_registry=self.base,
                proposal=self.proposal,
                reviews=stale,
                application_id="APPLY-4",
                applied_by="release-owner",
                applied_at="2026-08-11T13:00:00Z",
            )

        optional_review = create_taxonomy_change_review(
            self.proposal,
            base_registry=self.base,
            review_id="REVIEW-OPTIONAL",
            role="subject-matter-reviewer",
            decision="approve",
            reviewer_id="reviewer-optional",
            reviewed_at="2026-08-11T12:10:00Z",
            notes="Optional review of the same immutable candidate.",
        )
        for actor_id, reviews in (
            (self.proposal["requested_by"], self.reviews),
            (self.reviews[0]["reviewer_id"], self.reviews),
            (optional_review["reviewer_id"], [*self.reviews, optional_review]),
        ):
            with (
                self.subTest(applied_by=actor_id),
                self.assertRaisesRegex(
                    MillefeuilleContractError, "independent from the requester"
                ),
            ):
                apply_taxonomy_change(
                    base_registry=self.base,
                    proposal=self.proposal,
                    reviews=reviews,
                    application_id="APPLY-NOT-INDEPENDENT",
                    applied_by=actor_id,
                    applied_at="2026-08-11T13:00:00Z",
                )

    def test_apply_returns_reviewed_candidate_without_mutating_base_or_lock(self):
        lock = create_taxonomy_lock(
            self.base,
            lock_id="LOCK-19",
            scope_type="batch",
            scope_id="BATCH-19",
            locked_by="owner-a",
            locked_at="2026-08-11T12:00:00Z",
        )
        base_before = deepcopy(self.base)
        lock_before = deepcopy(lock)

        result, application = apply_taxonomy_change(
            base_registry=self.base,
            proposal=self.proposal,
            reviews=self.reviews,
            application_id="APPLY-20",
            applied_by="release-owner",
            applied_at="2026-08-11T13:00:00Z",
        )

        self.assertEqual(result, self.candidate)
        self.assertEqual(application["status"], "applied")
        self.assertEqual(application["active_batch_policy"], "preserve-locked-version")
        self.assertEqual(self.base, base_before)
        self.assertEqual(lock, lock_before)
        self.assertEqual(validate_taxonomy_lock(lock, registry=self.base), lock_before)

    def test_rollback_restores_exact_source_as_new_forward_version(self):
        rollback_candidate = _registry(
            "v21",
            previous_registry=self.candidate,
            training_label="Training",
        )
        proposal = _proposal(
            self.candidate,
            rollback_candidate,
            operation="rollback",
            rollback_source=self.base,
        )
        reviews = _reviews(proposal, self.candidate)

        result, application = rollback_taxonomy_change(
            base_registry=self.candidate,
            rollback_source_registry=self.base,
            proposal=proposal,
            reviews=reviews,
            application_id="ROLLBACK-21",
            applied_by="release-owner",
            applied_at="2026-08-11T14:00:00Z",
        )

        self.assertEqual(result["taxonomy_version"], "v21")
        self.assertEqual(result["previous_version"], "v20")
        self.assertEqual(result["entries"], self.base["entries"])
        self.assertEqual(application["status"], "rolled-back")

        wrong_source = _registry(
            "v18", previous_version=None, training_label="Older training"
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "source registry"):
            rollback_taxonomy_change(
                base_registry=self.candidate,
                rollback_source_registry=wrong_source,
                proposal=proposal,
                reviews=reviews,
                application_id="ROLLBACK-STALE",
                applied_by="release-owner",
                applied_at="2026-08-11T14:00:00Z",
            )

        bad_candidate = _registry(
            "v21",
            previous_registry=self.candidate,
            training_label="Not the historical definition",
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "source entry exactly"):
            bad_proposal = _proposal(
                self.candidate,
                bad_candidate,
                operation="rollback",
                rollback_source=self.base,
            )
            rollback_taxonomy_change(
                base_registry=self.candidate,
                rollback_source_registry=self.base,
                proposal=bad_proposal,
                reviews=_reviews(bad_proposal, self.candidate),
                application_id="ROLLBACK-BAD",
                applied_by="release-owner",
                applied_at="2026-08-11T14:00:00Z",
            )

    def test_forward_rollback_deprecates_nodes_added_after_historical_source(self):
        changed_training = deepcopy(self.base["entries"][1])
        changed_training["label"] = "Training methods"
        added_entry = _entry(
            "L2-EVALUATION",
            level=2,
            parent_id="L1-METHODS",
            label="Evaluation methods",
        )
        current = _registry(
            "v20",
            previous_registry=self.base,
            entries=[
                deepcopy(self.base["entries"][0]),
                added_entry,
                changed_training,
            ],
        )
        deprecated_added_entry = deepcopy(added_entry)
        deprecated_added_entry["status"] = "deprecated"
        rollback_candidate = _registry(
            "v21",
            previous_registry=current,
            entries=[
                deepcopy(self.base["entries"][0]),
                deprecated_added_entry,
                deepcopy(self.base["entries"][1]),
            ],
        )
        proposal = _proposal(
            current,
            rollback_candidate,
            operation="rollback",
            rollback_source=self.base,
            affected_entry_ids=["L2-EVALUATION", "L2-TRAINING"],
        )
        result, application = rollback_taxonomy_change(
            base_registry=current,
            rollback_source_registry=self.base,
            proposal=proposal,
            reviews=_reviews(proposal, current),
            application_id="ROLLBACK-ADDED-NODE",
            applied_by="release-owner",
            applied_at="2026-08-11T14:00:00Z",
        )

        result_entries = {entry["entry_id"]: entry for entry in result["entries"]}
        source_entries = {entry["entry_id"]: entry for entry in self.base["entries"]}
        self.assertEqual(result_entries["L1-METHODS"], source_entries["L1-METHODS"])
        self.assertEqual(result_entries["L2-TRAINING"], source_entries["L2-TRAINING"])
        self.assertEqual(result_entries["L2-EVALUATION"]["status"], "deprecated")
        self.assertEqual(application["status"], "rolled-back")

        already_deprecated_current = _registry(
            "v20",
            previous_registry=self.base,
            entries=[
                deepcopy(self.base["entries"][0]),
                deepcopy(deprecated_added_entry),
                deepcopy(changed_training),
            ],
        )
        already_deprecated_rollback_candidate = _registry(
            "v21",
            previous_registry=already_deprecated_current,
            entries=[
                deepcopy(self.base["entries"][0]),
                deepcopy(deprecated_added_entry),
                deepcopy(self.base["entries"][1]),
            ],
        )
        already_deprecated_proposal = _proposal(
            already_deprecated_current,
            already_deprecated_rollback_candidate,
            operation="rollback",
            rollback_source=self.base,
            affected_entry_ids=["L2-TRAINING"],
        )
        already_deprecated_result, _ = rollback_taxonomy_change(
            base_registry=already_deprecated_current,
            rollback_source_registry=self.base,
            proposal=already_deprecated_proposal,
            reviews=_reviews(already_deprecated_proposal, already_deprecated_current),
            application_id="ROLLBACK-DEPRECATED-NODE",
            applied_by="release-owner",
            applied_at="2026-08-11T14:00:00Z",
        )
        self.assertEqual(
            {
                entry["entry_id"]: entry
                for entry in already_deprecated_result["entries"]
            }["L2-EVALUATION"],
            deprecated_added_entry,
        )

        active_later_candidate = _registry(
            "v21",
            previous_registry=current,
            entries=[
                deepcopy(self.base["entries"][0]),
                deepcopy(added_entry),
                deepcopy(self.base["entries"][1]),
            ],
        )
        with self.assertRaisesRegex(
            MillefeuilleContractError, "sole permitted active-to-deprecated"
        ):
            _proposal(
                current,
                active_later_candidate,
                operation="rollback",
                rollback_source=self.base,
                affected_entry_ids=["L2-TRAINING"],
            )

        edited_later_entry = deepcopy(deprecated_added_entry)
        edited_later_entry["definition"] = "Unrelated prose smuggled into rollback."
        edited_later_candidate = _registry(
            "v21",
            previous_registry=current,
            entries=[
                deepcopy(self.base["entries"][0]),
                edited_later_entry,
                deepcopy(self.base["entries"][1]),
            ],
        )
        with self.assertRaisesRegex(
            MillefeuilleContractError, "sole permitted active-to-deprecated"
        ):
            _proposal(
                current,
                edited_later_candidate,
                operation="rollback",
                rollback_source=self.base,
                affected_entry_ids=["L2-EVALUATION", "L2-TRAINING"],
            )

        unrelated_source = _registry(
            "v18",
            previous_version=None,
            entries=[
                deepcopy(self.base["entries"][0]),
                _entry(
                    "L2-HISTORICAL",
                    level=2,
                    parent_id="L1-METHODS",
                    label="Historical-only method",
                ),
                deepcopy(self.base["entries"][1]),
            ],
        )
        unrelated_candidate = _registry(
            "v21",
            previous_registry=current,
            entries=[
                deepcopy(unrelated_source["entries"][0]),
                deepcopy(deprecated_added_entry),
                deepcopy(unrelated_source["entries"][1]),
                deepcopy(unrelated_source["entries"][2]),
            ],
        )
        with self.assertRaisesRegex(
            MillefeuilleContractError, "content-addressed predecessor"
        ):
            _proposal(
                current,
                unrelated_candidate,
                operation="rollback",
                rollback_source=unrelated_source,
                affected_entry_ids=[
                    "L2-EVALUATION",
                    "L2-HISTORICAL",
                    "L2-TRAINING",
                ],
            )

        extra_candidate = _registry(
            "v21",
            previous_registry=current,
            entries=[
                deepcopy(self.base["entries"][0]),
                deepcopy(deprecated_added_entry),
                _entry(
                    "L2-INTRUDER",
                    level=2,
                    parent_id="L1-METHODS",
                    label="Unrelated candidate entry",
                ),
                deepcopy(self.base["entries"][1]),
            ],
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "base/source union"):
            _proposal(
                current,
                extra_candidate,
                operation="rollback",
                rollback_source=self.base,
                affected_entry_ids=[
                    "L2-EVALUATION",
                    "L2-INTRUDER",
                    "L2-TRAINING",
                ],
            )

    def test_rollback_rejects_fabricated_structurally_compatible_source(self):
        fabricated_source = deepcopy(self.base)
        fabricated_source["entries"][1]["label"] = "Fabricated historical label"
        fabricated_source["entries"][1]["definition"] = "Fabricated history."
        _rehash(fabricated_source)
        fabricated_candidate = _registry(
            "v21",
            previous_registry=self.candidate,
            entries=deepcopy(fabricated_source["entries"]),
        )
        with self.assertRaisesRegex(
            MillefeuilleContractError, "exact content-addressed predecessor"
        ):
            _proposal(
                self.candidate,
                fabricated_candidate,
                operation="rollback",
                rollback_source=fabricated_source,
                affected_entry_ids=["L2-TRAINING"],
            )

    def test_generated_artifacts_match_all_json_schemas(self):
        lock = create_taxonomy_lock(
            self.base,
            lock_id="LOCK-19",
            scope_type="batch",
            scope_id="BATCH-19",
            locked_by="owner-a",
            locked_at="2026-08-11T12:00:00Z",
        )
        _, application = apply_taxonomy_change(
            base_registry=self.base,
            proposal=self.proposal,
            reviews=self.reviews,
            application_id="APPLY-20",
            applied_by="release-owner",
            applied_at="2026-08-11T13:00:00Z",
        )
        schema_paths = sorted(SPEC_ROOT.glob("taxonomy-*.schema.json"))
        schemas = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in schema_paths
        }
        registry = Registry().with_resources(
            [
                (schema["$id"], Resource.from_contents(schema))
                for schema in schemas.values()
            ]
        )
        instances = {
            "taxonomy-registry.schema.json": self.base,
            "taxonomy-lock.schema.json": lock,
            "taxonomy-change-proposal.schema.json": self.proposal,
            "taxonomy-change-review.schema.json": self.reviews[0],
            "taxonomy-application.schema.json": application,
        }
        for name, instance in instances.items():
            with self.subTest(schema=name):
                self.assertTrue(schemas[name]["x-millefeuille-semantic-validation"])
                Draft202012Validator(schemas[name], registry=registry).validate(
                    instance
                )
        proposal_semantics = " ".join(
            schemas["taxonomy-change-proposal.schema.json"][
                "x-millefeuille-semantic-validation"
            ]
        )
        review_semantics = " ".join(
            schemas["taxonomy-change-review.schema.json"][
                "x-millefeuille-semantic-validation"
            ]
        )
        application_semantics = " ".join(
            schemas["taxonomy-application.schema.json"][
                "x-millefeuille-semantic-validation"
            ]
        )
        self.assertIn("base/source union", proposal_semantics)
        self.assertIn("content-addressed immediate predecessor", proposal_semantics)
        self.assertIn("operation", proposal_semantics)
        self.assertIn("distinct from proposal requested_by", review_semantics)
        self.assertIn("distinct from requested_by", application_semantics)
        self.assertEqual(
            schemas["taxonomy-registry.schema.json"]["properties"]["entries"][
                "maxItems"
            ],
            MAX_TAXONOMY_ENTRIES,
        )
        self.assertEqual(
            schemas["taxonomy-registry.schema.json"]["$defs"]["sortedTextArray"][
                "maxItems"
            ],
            MAX_TAXONOMY_ENTRY_RULES,
        )
        self.assertEqual(
            schemas["taxonomy-change-proposal.schema.json"]["properties"][
                "affected_entry_ids"
            ]["maxItems"],
            MAX_TAXONOMY_AFFECTED_ENTRY_IDS,
        )
        self.assertEqual(
            schemas["taxonomy-change-proposal.schema.json"]["properties"][
                "evidence_refs"
            ]["maxItems"],
            MAX_TAXONOMY_EVIDENCE_REFS,
        )
        self.assertEqual(
            schemas["taxonomy-application.schema.json"]["properties"][
                "review_content_identities"
            ]["maxItems"],
            MAX_TAXONOMY_REVIEWS,
        )

    def test_cli_derives_lock_proposal_reviews_apply_and_rollback_json(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            base_path = root / "v19.json"
            candidate_path = root / "v20.json"
            _write_json(base_path, self.base)
            _write_json(candidate_path, self.candidate)

            lock_out = StringIO()
            self.assertEqual(
                run_taxonomy_cli(
                    [
                        "lock",
                        "--registry",
                        str(base_path),
                        "--lock-id",
                        "LOCK-19",
                        "--scope-type",
                        "batch",
                        "--scope-id",
                        "BATCH-19",
                        "--locked-by",
                        "owner-a",
                        "--locked-at",
                        "2026-08-11T12:00:00Z",
                    ],
                    stdout=lock_out,
                ),
                0,
            )
            lock_path = root / "lock.json"
            _write_json(lock_path, json.loads(lock_out.getvalue()))

            validate_out = StringIO()
            self.assertEqual(
                run_taxonomy_cli(
                    [
                        "validate",
                        "--registry",
                        str(base_path),
                        "--lock",
                        str(lock_path),
                    ],
                    stdout=validate_out,
                ),
                0,
            )
            self.assertEqual(json.loads(validate_out.getvalue())["status"], "valid")

            proposal_out = StringIO()
            propose_args = [
                "propose",
                "--base-registry",
                str(base_path),
                "--candidate-registry",
                str(candidate_path),
                "--change-id",
                "TCR-CHANGE",
                "--operation",
                "rename",
                "--affected-entry-id",
                "L2-TRAINING",
                "--reason",
                "Resolve a repeated, evidenced boundary issue.",
                "--evidence-ref",
                "evidence/TCR-CHANGE.md",
                "--impact-risk",
                "medium",
                "--impact-summary",
                "Existing decisions require a bounded review.",
                "--estimated-affected-records",
                "12",
                "--historical-reclassification",
                "review",
                "--migration-instructions",
                "Queue affected records; preserve active locks.",
                "--requested-by",
                "requester-a",
                "--requested-at",
                "2026-08-11T12:00:00Z",
            ]
            self.assertEqual(run_taxonomy_cli(propose_args, stdout=proposal_out), 0)
            proposal_path = root / "proposal.json"
            _write_json(proposal_path, json.loads(proposal_out.getvalue()))

            review_paths: list[Path] = []
            for index, role in enumerate(sorted(REQUIRED_REVIEW_ROLES), start=1):
                review_out = StringIO()
                self.assertEqual(
                    run_taxonomy_cli(
                        [
                            "review",
                            "--base-registry",
                            str(base_path),
                            "--proposal",
                            str(proposal_path),
                            "--review-id",
                            f"REVIEW-{index}",
                            "--role",
                            role,
                            "--decision",
                            "approve",
                            "--reviewer-id",
                            f"reviewer-{index}",
                            "--reviewed-at",
                            f"2026-08-11T12:0{index}:00Z",
                            "--notes",
                            "Reviewed the exact candidate and migration.",
                        ],
                        stdout=review_out,
                    ),
                    0,
                )
                review_path = root / f"review-{index}.json"
                _write_json(review_path, json.loads(review_out.getvalue()))
                review_paths.append(review_path)

            apply_out = StringIO()
            apply_args = [
                "apply",
                "--base-registry",
                str(base_path),
                "--proposal",
                str(proposal_path),
            ]
            for path in review_paths:
                apply_args.extend(["--review", str(path)])
            apply_args.extend(
                [
                    "--application-id",
                    "APPLY-20",
                    "--applied-by",
                    "release-owner",
                    "--applied-at",
                    "2026-08-11T13:00:00Z",
                ]
            )
            self.assertEqual(run_taxonomy_cli(apply_args, stdout=apply_out), 0)
            self.assertEqual(
                json.loads(apply_out.getvalue())["registry"]["taxonomy_version"],
                "v20",
            )

            rollback_candidate = _registry(
                "v21", previous_registry=self.candidate, training_label="Training"
            )
            rollback_candidate_path = root / "v21.json"
            _write_json(rollback_candidate_path, rollback_candidate)
            rollback_proposal = _proposal(
                self.candidate,
                rollback_candidate,
                operation="rollback",
                rollback_source=self.base,
            )
            rollback_proposal_path = root / "rollback-proposal.json"
            _write_json(rollback_proposal_path, rollback_proposal)
            rollback_reviews = _reviews(rollback_proposal, self.candidate)
            rollback_review_paths: list[Path] = []
            for index, review in enumerate(rollback_reviews, start=1):
                path = root / f"rollback-review-{index}.json"
                _write_json(path, review)
                rollback_review_paths.append(path)
            rollback_out = StringIO()
            rollback_args = [
                "rollback",
                "--base-registry",
                str(candidate_path),
                "--rollback-source-registry",
                str(base_path),
                "--proposal",
                str(rollback_proposal_path),
            ]
            for path in rollback_review_paths:
                rollback_args.extend(["--review", str(path)])
            rollback_args.extend(
                [
                    "--application-id",
                    "ROLLBACK-21",
                    "--applied-by",
                    "release-owner",
                    "--applied-at",
                    "2026-08-11T14:00:00Z",
                ]
            )
            self.assertEqual(run_taxonomy_cli(rollback_args, stdout=rollback_out), 0)
            self.assertEqual(
                json.loads(rollback_out.getvalue())["application"]["status"],
                "rolled-back",
            )

    def test_cli_seal_is_offline_and_rejects_hash_or_approval_drift(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            draft = deepcopy(self.base)
            draft.pop("content_identity")
            draft_path = root / "draft.json"
            _write_json(draft_path, draft)

            stdout = StringIO()
            self.assertEqual(
                run_taxonomy_cli(
                    ["seal", "--registry-draft", str(draft_path)], stdout=stdout
                ),
                0,
            )
            self.assertEqual(json.loads(stdout.getvalue()), self.base)

            tampered_path = root / "tampered.json"
            tampered = deepcopy(self.base)
            tampered["taxonomy_version"] = "v999"
            _write_json(tampered_path, tampered)
            stderr = StringIO()
            self.assertEqual(
                run_taxonomy_cli(
                    ["validate", "--registry", str(tampered_path)],
                    stderr=stderr,
                ),
                2,
            )
            self.assertIn("identity drift", stderr.getvalue())

    def test_contract_docs_keep_immutability_compatibility_and_gates_explicit(self):
        contract = (SPEC_ROOT / "taxonomy-registry.md").read_text(encoding="utf-8")
        classification = (SPEC_ROOT / "classification-orchestration.md").read_text(
            encoding="utf-8"
        )

        for phrase in (
            "never overwritten",
            "preserve-locked-version",
            "taxonomy-owner",
            "qa-lead",
            "operations-lead",
            "forward-only rollback",
            "base/source union",
            "base-only",
            "accountable actor identities",
            "no automatic taxonomy generation",
            "v0.1/v0.2 source-pack",
        ):
            self.assertIn(phrase, contract)
        self.assertIn("content-addressed lock snapshot", classification)
        self.assertIn("active batch stays on its original", classification)


if __name__ == "__main__":
    unittest.main()
