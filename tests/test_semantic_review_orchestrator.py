"""Pure synthetic review -> actual decision validator -> six exact outputs."""

import copy
import tempfile
import unittest

from millefeuille.domain.acceptance import _build_acceptance_summary
from millefeuille.domain.evidence_answer import canonical
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import digest
from millefeuille.domain.semantic_materials import build_material_binding
from millefeuille.domain.semantic_review_orchestrator import plan_reviewed_acceptance
from millefeuille.domain.stage_runtime import resolve_run_artifacts
from tests.test_evidence_answer import generated_scenarios
from tests.test_millefeuille_semantic_acceptance import SemanticAcceptanceTests
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_handoff_jsonl,
)


class OrchestratorTests(unittest.TestCase):
    def test_roundtrip_and_review_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, run = _prepare_fixture_run(tmp)
            resolved = resolve_run_artifacts(
                source_pack_root=root, paper_id=PAPER_ID, run_id=RUN_ID
            )
            summary = _build_acceptance_summary(
                resolved=resolved,
                handoff_path=_write_handoff_jsonl(tmp),
                duplicate_scan_path=None,
            )
            _, evidence = SemanticAcceptanceTests().fixture(run)
            binding = build_material_binding(resolved)
            from unittest.mock import patch

            from millefeuille.domain.evidence_source_adapter import (
                BudgetExhaustedError,
                ReadBudget,
            )
            from millefeuille.domain.semantic_acceptance import semantic_read_scope

            first = ReadBudget()
            build_material_binding(resolved, budget=first)
            cumulative = ReadBudget(max_read_bytes=first.read_bytes)
            with semantic_read_scope(cumulative):
                self.assertEqual(build_material_binding(resolved), binding)
                with patch(
                    "millefeuille.domain.semantic_acceptance._secure_read"
                ) as io:
                    with self.assertRaises(BudgetExhaustedError):
                        build_material_binding(resolved)
                    io.assert_not_called()
            bindings = [
                {
                    "attachment_key": f["name"][7:],
                    "sha256": f["sha256"],
                    "page": 1,
                    "section": "method",
                }
                for f in binding["files"]
                if f["name"].startswith("source:")
            ]
            with tempfile.TemporaryDirectory() as sources:
                gold, responses, source_review, scenarios = generated_scenarios(sources)
            review = {
                "schema_version": "millefeuille-semantic-review-result/v1",
                "paper_id": PAPER_ID,
                "run_id": RUN_ID,
                "source_hash": resolved.source_hash,
                "material_binding": binding,
                "source_bindings": bindings,
                "versions": "resolved",
                "coverage": {
                    "observed_pages": [1],
                    "covered_pages": [1],
                    "observed_sections": ["method"],
                    "covered_sections": ["method"],
                    "uncovered_material_claims": [],
                },
                "claims": [
                    {
                        "claim_id": "c",
                        "claim_text": "Synthetic method claim",
                        "claim_text_sha256": digest(b"Synthetic method claim"),
                        "source_locators": bindings,
                        "verdict": "supported",
                        "qualification": "synthetic only",
                    }
                ],
                "scenarios": scenarios["scenarios"],
            }
            independent = {
                "source_review_sha256": scenarios["source_review_sha256"],
                "review_content_sha256": digest(canonical(review)),
            }
            review["review_method"] = {
                "method": "independent synthetic source comparison",
                "reviewer_assertion": "test-reviewer, not authenticated",
                "independent_review_record_sha256": digest(canonical(independent)),
                "limitations": ["synthetic fixtures only"],
            }
            args = {
                "review_result": review,
                "resolved_materials": resolved,
                "summary": summary,
                "required_evidence": evidence,
                "candidate_responses": responses,
                "candidate_response_sha256s": [digest(r) for r in responses],
                "independent_gold": gold,
                "source_review": source_review,
                "independent_review_record": independent,
            }
            damaged = list(responses)
            damaged[0] += b" "
            with self.assertRaises(MillefeuilleContractError):
                plan_reviewed_acceptance(**dict(args, candidate_responses=damaged))
            changed_source = copy.deepcopy(source_review)
            changed_source["source_spans"]["0"]["text_utf8"] = "source changed"
            with self.assertRaises(MillefeuilleContractError):
                plan_reviewed_acceptance(**dict(args, source_review=changed_source))
            # Preserve old unrelated-source fixture as an explicit rejection.
            with self.assertRaisesRegex(MillefeuilleContractError, "identity missing"):
                plan_reviewed_acceptance(**args)
            bad = copy.deepcopy(review)
            bad["claims"][0]["verdict"] = "uncovered"
            with self.assertRaises(MillefeuilleContractError):
                plan_reviewed_acceptance(**dict(args, review_result=bad))
            with self.assertRaises(MillefeuilleContractError):
                plan_reviewed_acceptance(**dict(args, required_evidence=evidence[:-1]))
