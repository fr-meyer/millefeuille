"""Synthetic disposition and registry lock regression checks."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.domain.acceptance_output_plan import json_bytes
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.release_preflight import validate_no_writeback_disposition
from millefeuille.domain.semantic_acceptance import digest
from millefeuille.domain.taxonomy import (
    create_taxonomy_lock,
    validate_external_classification_taxonomy,
)
from tests.test_millefeuille_classification_model import ClassificationModelTests


class GuardTests(unittest.TestCase):
    def test_generic_registry_lock_and_drift(self):
        lock = create_taxonomy_lock(
            ClassificationModelTests.make_registry(),
            lock_id="synthetic",
            scope_type="single-run",
            scope_id="run",
            locked_by="synthetic",
            locked_at="2026-11-12T00:00:00Z",
        )
        evidence = {
            "taxonomy_lock": lock,
            "taxonomy_version": lock["taxonomy_version"],
            "taxonomy_lock_identity": lock["content_identity"],
            "primary_entry_id": "L2-TRAINING",
            "primary_path": "Methods > Training",
        }
        validate_external_classification_taxonomy(evidence, run_id="run")
        for field, value in [
            ("taxonomy_version", "drift"),
            ("taxonomy_lock_identity", "drift"),
            ("primary_path", "Title/Guess"),
            ("taxonomy_lock", None),
        ]:
            changed = deepcopy(evidence)
            changed[field] = value
            with self.assertRaises(MillefeuilleContractError):
                validate_external_classification_taxonomy(changed, run_id="run")

    def test_disposition_requires_separate_hash_approval_and_state_binding(self):
        disposition = {
            "schema": "millefeuille-writeback-disposition/v1",
            "enabled": False,
            "disposition": "explicit_approved_no_writeback",
            "remote_operations": 0,
            "release_preflight_consumer_result": "pass",
            "approval_identity": "synthetic-owner",
            "binding": {"state": "old"},
        }
        from pathlib import Path

        from millefeuille.domain.semantic_publication_orchestrator import (
            capture_bound_manifest,
        )

        manifest = capture_bound_manifest([Path(__file__).absolute()])
        disposition.update(code_manifest=manifest, input_manifest=manifest)
        kwargs = {
            "resolved": SimpleNamespace(),
            "current_version": "1",
            "candidate_version": "2",
            "approved_identity": digest(json_bytes(disposition)),
        }
        with patch(
            "millefeuille.domain.release_preflight.no_writeback_binding",
            return_value={"state": "old"},
        ):
            validate_no_writeback_disposition(disposition, **kwargs)
            with self.assertRaises(MillefeuilleContractError):
                validate_no_writeback_disposition(
                    disposition, **{**kwargs, "approved_identity": None}
                )
        with (
            patch(
                "millefeuille.domain.release_preflight.no_writeback_binding",
                return_value={"state": "drift"},
            ),
            self.assertRaises(MillefeuilleContractError),
        ):
            validate_no_writeback_disposition(disposition, **kwargs)


def locked_evidence(run_id):
    lock = create_taxonomy_lock(
        ClassificationModelTests.make_registry(),
        lock_id="synthetic",
        scope_type="single-run",
        scope_id=run_id,
        locked_by="synthetic",
        locked_at="2026-11-12T00:00:00Z",
    )
    return {
        "taxonomy_lock": lock,
        "taxonomy_version": lock["taxonomy_version"],
        "taxonomy_lock_identity": lock["content_identity"],
        "primary_entry_id": "L2-TRAINING",
        "primary_path": "Methods > Training",
    }
